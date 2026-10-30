import { useState, useSyncExternalStore } from 'react';
import type { ClassroomOSWebV1EventEnvelope } from '../generated/events';
import { eventScripts, type EventScriptName } from './event-scripts';
import { MemoryVault, MockCapturePort, MockEventClient, MockUploadPort } from './ports';

type Capture = 'idle' | 'recording' | 'paused' | 'local_only' | 'failed' | 'ended';
type Upload = 'pending' | 'uploading' | 'durable' | 'failed';
type Asr = 'idle' | 'streaming' | 'catching_up' | 'draining' | 'succeeded' | 'failed' | 'unknown';
type Ai = 'idle' | 'queued' | 'running' | 'succeeded' | 'failed' | 'unknown' | 'cancel_requested' | 'cancelled';
type Sync = 'synced' | 'local_only' | 'syncing' | 'failed' | 'conflict';
export type Flows = { capture: Capture; upload: Upload; asr: Asr; ai: Ai; sync: Sync };
export type MockAccount = 'A' | 'B';
type Projection = { portCaptureState: Capture; uploadPending: boolean; partial: string | null; finalText: string | null; finalRevision: number; frameReceived: boolean; chunkDurable: boolean; audioDeleted: boolean; classroomDeleted: boolean; leaseLost: boolean; mayHaveCharged: boolean; delivered: number };
type CaptureScope = { classSessionId: string; captureEpochId: string; fenceToken: number; chunkId: string };
type Task = { classroomId: string | null; revision: number; state: Ai; mayHaveCharged: boolean; order: number };
type LocalWriteResult = 'none' | 'stored' | 'write_failed' | 'locked' | 'quota' | 'reconciled';
type DurableUpload = { classSessionId: string; captureEpochId: string; chunkId: string; seq: number; receiptId: string };
type UploadHistoryEntry = { classSessionId: string; captureEpochId: string; chunkId: string; seq: number; frameReceived: boolean; status: 'pending' | 'durable'; receiptId: string | null; eventId: string | null; canAccept: boolean };
type HistoricalUpload = { entry: UploadHistoryEntry; port: MockUploadPort; canAccept: boolean };
type LocalRecordSync = 'none' | 'pending' | 'reconciled';
type State = { currentClassroomId: string; tasks: Record<string, Task>; leaseValid: boolean; active: boolean; account: MockAccount | null; epoch: number; scenario: string; flows: Flows; accountResponse: unknown | null; captureScope: CaptureScope | null; eventProjection: Projection; localWriteResult: LocalWriteResult; mockLocalRecord: string | null; localRecordSync: LocalRecordSync; durableUploads: DurableUpload[]; uploadHistory: UploadHistoryEntry[] };
const emptyFlows = (): Flows => ({ capture: 'idle', upload: 'pending', asr: 'idle', ai: 'idle', sync: 'synced' });
const emptyProjection = (): Projection => ({ portCaptureState: 'idle', uploadPending: true, partial: null, finalText: null, finalRevision: 0, frameReceived: false, chunkDurable: false, audioDeleted: false, classroomDeleted: false, leaseLost: false, mayHaveCharged: false, delivered: 0 });
// Fixed synthetic lease: resource identifiers are never inferred from the current route.
const mockCaptureScope = (): CaptureScope => ({
  classSessionId: eventScripts.leaseLoss[0].data.class_session_id,
  captureEpochId: eventScripts.leaseLoss[0].data.capture_epoch_id,
  fenceToken: eventScripts.leaseLoss[0].data.fence_token,
  chunkId: eventScripts.upload[0].data.chunk_id,
});

export function createAuthEpoch() {
  let epoch = 0;
  return { getEpoch: () => epoch, invalidate: () => ++epoch, isCurrent: (value: number) => value === epoch };
}

export function createMockRuntime(options: { createVault?: () => MemoryVault } = {}) {
  const auth = createAuthEpoch();
  const createVault = options.createVault ?? (() => new MemoryVault());
  let vault = createVault();
  vault.unlock();
  let vaultLocked = false;
  let beforeVaultLock: { result: LocalWriteResult; sync: Sync } | null = null;
  const localRecordKey = 'synthetic-local-draft';
  let state: State = { currentClassroomId: mockCaptureScope().classSessionId, tasks: {}, leaseValid: true, active: false, account: 'A', epoch: 0, scenario: '02', flows: emptyFlows(), accountResponse: null, captureScope: mockCaptureScope(), eventProjection: emptyProjection(), localWriteResult: 'none', mockLocalRecord: null, localRecordSync: 'none', durableUploads: [], uploadHistory: [] };
  const listeners = new Set<() => void>();
  let events = new MockEventClient();
  let stopEvents = () => {};
  let durableIds = new Set<string>();
  // Persist receipt deduplication across scenario resets within an account.
  let processedUploadEventIds = new Set<string>();
  // One identity per logical scripted event and resource lifecycle. Replays reuse it;
  // a new epoch generates a different identity without weakening event deduplication.
  let scriptEventIds = new Map<string, string>();
  let finalSegments = new Map<string, { classroomId: string; sequence: number; revision: number; text: string }>();
  let tasks = new Map<string, Task>();
  let taskOrder = 0;
  let partialSegments = new Map<string, { classroomId: string; streamId: string; sequence: number; revision: number; text: string }>();
  let capture = new MockCapturePort();
  let upload = new MockUploadPort();
  upload.stage(1); // The deterministic fixture begins with one staged synthetic chunk.
  let uploadRecords = new Map<string, HistoricalUpload>();
  const registerUpload = (scope: CaptureScope) => {
    uploadRecords.set(scope.chunkId, { entry: { classSessionId: scope.classSessionId, captureEpochId: scope.captureEpochId, chunkId: scope.chunkId, seq: 1, frameReceived: false, status: 'pending', receiptId: null, eventId: null, canAccept: true }, port: upload, canAccept: true });
  };
  registerUpload(state.captureScope!);
  state = { ...state, uploadHistory: [...uploadRecords.values()].map(record => ({ ...record.entry })) };
  let leaseGeneration = 1;
  let deletedClassrooms = new Set<string>();
  let deletedAudioIds = new Set<string>();
  const update = (next: State) => { state = { ...next, uploadHistory: [...uploadRecords.values()].map(record => ({ ...record.entry, canAccept: record.canAccept })) }; listeners.forEach(listener => listener()); };
  const taskProjection = (classroomId: string, source = tasks) => {
    const matching = [...source.values()].filter(task => task.classroomId === classroomId);
    // Preserve an unresolved unknown as the primary state, even if a later task succeeds.
    const current = matching.sort((a, b) => Number(b.state === 'unknown' && b.mayHaveCharged) - Number(a.state === 'unknown' && a.mayHaveCharged) || b.order - a.order)[0];
    return { ai: current?.state ?? 'idle' as Ai, risk: [...source.values()].some(task => task.mayHaveCharged) };
  };
  const segmentKey = (stream: string, segment: string) => `${stream}:${segment}`;
  const partialText = (classroomId: string) => [...partialSegments.values()].filter(item => item.classroomId === classroomId).sort((a, b) => a.sequence - b.sequence).map(item => item.text).join(' / ') || null;
  const freshScope = (classSessionId: string): CaptureScope => ({ classSessionId, captureEpochId: crypto.randomUUID(), chunkId: crypto.randomUUID(), fenceToken: ++leaseGeneration });
  const resetPorts = () => { capture = new MockCapturePort(); upload = new MockUploadPort(); upload.stage(1); };
  const stopCapture = (reason: 'failed' | 'ended') => {
    if (capture.state === 'recording' || capture.state === 'paused' || capture.state === 'local_only') capture.command(reason);
  };
  const clearScope = (account: MockAccount | null) => {
    // Invalidate first: synchronous teardown must win over any already queued callbacks.
    const nextEpoch = auth.invalidate();
    stopCapture('ended'); stopEvents(); events.clear(); durableIds = new Set(); processedUploadEventIds = new Set(); scriptEventIds = new Map(); finalSegments = new Map(); partialSegments = new Map(); tasks = new Map(); taskOrder = 0; deletedClassrooms = new Set(); deletedAudioIds = new Set(); resetPorts();
    vault.lock(); vault.clear(); vault = createVault(); beforeVaultLock = null; vaultLocked = true; uploadRecords = new Map();
    if (account) { vault.unlock(); vaultLocked = false; }
    const scope = account ? freshScope(mockCaptureScope().classSessionId) : null;
    if (scope) registerUpload(scope);
    update({ ...state, account, epoch: nextEpoch, currentClassroomId: mockCaptureScope().classSessionId, tasks: {}, leaseValid: Boolean(scope), active: false, scenario: '02', accountResponse: null, captureScope: scope, flows: emptyFlows(), localWriteResult: 'none', mockLocalRecord: null, localRecordSync: 'none', durableUploads: [], uploadHistory: [], eventProjection: { ...emptyProjection(), uploadPending: Boolean(account) } });
    if (account) connectEvents();
  };
  const handleEvent = (event: ClassroomOSWebV1EventEnvelope, eventEpoch: number) => {
    if (!auth.isCurrent(eventEpoch) || !state.account) return;
    if (event.type === 'error' && event.data.code === 'AUTH_REVOKED') { clearScope(null); return; }
    const scope = state.captureScope;
    if (event.type === 'capture.lease_lost' && (!state.leaseValid || !scope || event.data.class_session_id !== scope.classSessionId || event.data.capture_epoch_id !== scope.captureEpochId || event.data.fence_token !== scope.fenceToken)) return;
    // Frame ACK identifies the chunk, while durable also binds classroom, epoch and sequence.
    // Keep each earlier port for legitimate late receipts, but never accept revoked resources.
    const record = event.type === 'audio.chunk_durable' || event.type === 'capture.frame_received' ? uploadRecords.get(event.data.chunk_id) : undefined;
    if (event.type === 'capture.frame_received' && (!record || !record.canAccept || event.data.frame_seq !== record.entry.seq || deletedAudioIds.has(record.entry.chunkId) || deletedClassrooms.has(record.entry.classSessionId))) return;
    if (event.type === 'audio.chunk_durable' && (!record || !record.canAccept || event.data.class_session_id !== record.entry.classSessionId || event.data.capture_epoch_id !== record.entry.captureEpochId || event.data.seq !== record.entry.seq || deletedAudioIds.has(record.entry.chunkId) || deletedClassrooms.has(record.entry.classSessionId))) return;
    if (event.type === 'audio.chunk_durable') {
      if (processedUploadEventIds.has(event.event_id) || record?.entry.status === 'durable') return;
      processedUploadEventIds.add(event.event_id);
    }
    if ('event_id' in event) {
      if (durableIds.has(event.event_id)) return;
      durableIds.add(event.event_id);
    }
    const projection = { ...state.eventProjection, delivered: state.eventProjection.delivered + 1 };
    let flows = state.flows;
    let active = state.active;
    let durableUploads = state.durableUploads;
    switch (event.type) {
      case 'transcript.partial': {
        const key = segmentKey(event.data.stream_id, event.data.segment_id);
        const old = partialSegments.get(key);
        if (!deletedClassrooms.has(event.data.class_session_id) && !finalSegments.has(key) && event.data.revision > (old?.revision ?? 0)) {
          partialSegments.set(key, { classroomId: event.data.class_session_id, streamId: event.data.stream_id, sequence: event.data.sequence, revision: event.data.revision, text: event.data.text });
          projection.partial = partialText(state.currentClassroomId);
        }
        break;
      }
      case 'transcript.final': {
        const key = segmentKey(event.data.stream_id, event.data.segment_id);
        const previous = finalSegments.get(key);
        if (!deletedClassrooms.has(event.data.class_session_id) && event.data.revision > (previous?.revision ?? 0)) {
          finalSegments.set(key, { classroomId: event.data.class_session_id, sequence: event.data.sequence, revision: event.data.revision, text: event.data.text });
          partialSegments.delete(key);
          if (event.data.class_session_id === state.currentClassroomId) projection.finalRevision = event.data.revision;
          projection.finalText = [...finalSegments.values()].filter(segment => segment.classroomId === state.currentClassroomId).sort((a, b) => a.sequence - b.sequence).map(segment => segment.text).join(' / ') || null;
          projection.partial = partialText(state.currentClassroomId);
        }
        break;
      }
      case 'capture.frame_received':
        if (record && record.port.frameReceived(event.data.frame_seq)) {
          record.entry = { ...record.entry, frameReceived: true };
          if (scope?.chunkId === event.data.chunk_id) { projection.frameReceived = true; projection.uploadPending = record.port.pending(event.data.frame_seq); }
        }
        break;
      case 'audio.chunk_durable':
        if (record && record.port.pending(event.data.seq)) {
          record.port.durable(event.data.seq);
          record.entry = { ...record.entry, status: 'durable', receiptId: event.data.receipt_id, eventId: event.event_id };
          durableUploads = [...durableUploads, { classSessionId: event.data.class_session_id, captureEpochId: event.data.capture_epoch_id, chunkId: event.data.chunk_id, seq: event.data.seq, receiptId: event.data.receipt_id }];
          if (scope?.chunkId === event.data.chunk_id) { projection.uploadPending = false; projection.chunkDurable = true; flows = { ...flows, upload: 'durable' }; }
        }
        break;
      case 'task.changed': {
        const old = tasks.get(event.data.task_id);
        if ((!old || old.classroomId === event.data.class_session_id) && event.data.revision > (old?.revision ?? 0)) {
          const reconciled = ['succeeded', 'failed', 'cancelled'].includes(event.data.state) && !event.data.may_have_charged;
          tasks.set(event.data.task_id, { classroomId: event.data.class_session_id, revision: event.data.revision, state: event.data.state, mayHaveCharged: event.data.may_have_charged || Boolean(old?.mayHaveCharged && !reconciled), order: ++taskOrder });
          const projected = taskProjection(state.currentClassroomId);
          projection.mayHaveCharged = projected.risk;
          flows = { ...flows, ai: projected.ai };
        }
        break;
      }
      case 'object.deleted':
        if (event.data.object_type === 'audio') { deletedAudioIds.add(event.data.object_id); const revoked = uploadRecords.get(event.data.object_id); if (revoked) revoked.canAccept = false; if (scope?.chunkId === event.data.object_id) projection.audioDeleted = true; }
        if (event.data.object_type === 'class_session') {
          if (state.currentClassroomId === event.data.object_id) projection.classroomDeleted = true;
          deletedClassrooms.add(event.data.object_id);
          for (const item of uploadRecords.values()) if (item.entry.classSessionId === event.data.object_id) item.canAccept = false;
          if (scope?.classSessionId === event.data.object_id) { stopCapture('failed'); active = false; flows = { ...flows, capture: 'failed' }; }
          for (const [key, item] of partialSegments) if (item.classroomId === event.data.object_id) partialSegments.delete(key);
          for (const [key, item] of finalSegments) if (item.classroomId === event.data.object_id) finalSegments.delete(key);
          projection.finalText = [...finalSegments.values()].filter(segment => segment.classroomId === state.currentClassroomId).sort((a, b) => a.sequence - b.sequence).map(segment => segment.text).join(' / ') || null;
          if (state.currentClassroomId === event.data.object_id) projection.finalRevision = 0;
          projection.partial = partialText(state.currentClassroomId);
        }
        break;
      case 'capture.lease_lost': { const revoked = scope && uploadRecords.get(scope.chunkId); if (revoked) revoked.canAccept = false; projection.leaseLost = true; stopCapture('failed'); active = false; flows = { ...flows, capture: 'failed' }; break; }
    }
    const invalidated = event.type === 'capture.lease_lost' || (event.type === 'object.deleted' && event.data.object_type === 'class_session' && scope?.classSessionId === event.data.object_id);
    projection.portCaptureState = capture.state;
    update({ ...state, active, flows, durableUploads, leaseValid: invalidated ? false : state.leaseValid, tasks: Object.fromEntries(tasks), eventProjection: projection });
  };
  function connectEvents() {
    events = new MockEventClient();
    const scopeEpoch = state.epoch;
    stopEvents = events.subscribe(event => handleEvent(event, scopeEpoch));
  }
  const scriptedId = (key: string) => {
    let id = scriptEventIds.get(key);
    if (!id) { id = crypto.randomUUID(); scriptEventIds.set(key, id); }
    return id;
  };
  const bindScriptEvent = (event: ClassroomOSWebV1EventEnvelope, scope = state.captureScope): ClassroomOSWebV1EventEnvelope => {
    if (!scope) return event;
    if (event.type === 'capture.lease_lost') return { ...event, event_id: scriptedId(`${event.type}:${event.event_id}:${scope.captureEpochId}:${scope.fenceToken}`), data: { ...event.data, class_session_id: scope.classSessionId, capture_epoch_id: scope.captureEpochId, fence_token: scope.fenceToken } };
    if (event.type === 'audio.chunk_durable') return { ...event, event_id: scriptedId(`${event.type}:${event.event_id}:${scope.captureEpochId}:${scope.chunkId}`), data: { ...event.data, class_session_id: scope.classSessionId, capture_epoch_id: scope.captureEpochId, chunk_id: scope.chunkId, receipt_id: scriptedId(`receipt:${event.data.receipt_id}:${scope.captureEpochId}:${scope.chunkId}`) } };
    if (event.type === 'capture.frame_received') return { ...event, data: { ...event.data, chunk_id: scope.chunkId } };
    if (event.type === 'object.deleted') {
      const objectId = event.data.object_type === 'audio' ? scope.chunkId : event.data.object_type === 'class_session' ? scope.classSessionId : event.data.object_id;
      return { ...event, event_id: scriptedId(`${event.type}:${event.event_id}:${event.data.object_type}:${objectId}`), data: { ...event.data, object_id: objectId, deletion_id: scriptedId(`deletion:${event.data.deletion_id}:${event.data.object_type}:${objectId}`) } };
    }
    if (event.type === 'transcript.partial') return { ...event, data: { ...event.data, class_session_id: state.currentClassroomId } };
    if (event.type === 'transcript.final') return { ...event, event_id: scriptedId(`${event.type}:${event.event_id}:${state.currentClassroomId}:${event.data.stream_id}:${event.data.segment_id}:${event.data.revision}`), data: { ...event.data, class_session_id: state.currentClassroomId } };
    if (event.type === 'task.changed') return { ...event, event_id: scriptedId(`${event.type}:${event.event_id}:${state.currentClassroomId}:${event.data.task_id}:${event.data.revision}`), data: { ...event.data, class_session_id: state.currentClassroomId } };
    return event;
  };
  connectEvents();
  return {
    getState: () => state,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => listeners.delete(listener); },
    start: () => {
      if (!state.account || state.active || !state.leaseValid || !state.captureScope || state.captureScope.classSessionId !== state.currentClassroomId || deletedClassrooms.has(state.captureScope.classSessionId)) return false;
      if (capture.state === 'ended') {
        const scope = freshScope(state.currentClassroomId);
        resetPorts(); registerUpload(scope);
        update({ ...state, captureScope: scope, flows: { ...state.flows, upload: 'pending' }, eventProjection: { ...state.eventProjection, frameReceived: false, chunkDurable: false, uploadPending: true, audioDeleted: false, leaseLost: false, portCaptureState: 'idle' } });
      }
      if (!capture.command('recording')) return false;
      update({ ...state, active: true, flows: { ...state.flows, capture: 'recording', upload: 'pending', asr: 'idle' }, eventProjection: { ...state.eventProjection, portCaptureState: capture.state } });
      return true;
    },
    stop: () => { stopCapture('ended'); update({ ...state, active: false, flows: { ...state.flows, capture: 'ended' }, eventProjection: { ...state.eventProjection, portCaptureState: capture.state } }); },
    acquireMockLease: (classroomId = state.currentClassroomId) => {
      if (!state.account || state.active || deletedClassrooms.has(classroomId)) return false;
      const scope = freshScope(classroomId); resetPorts(); registerUpload(scope);
      update({ ...state, captureScope: scope, currentClassroomId: classroomId, leaseValid: true, flows: { ...state.flows, capture: 'idle', upload: 'pending', ai: taskProjection(classroomId).ai }, eventProjection: { ...state.eventProjection, leaseLost: false, portCaptureState: 'idle', finalRevision: Math.max(0, ...[...finalSegments.values()].filter(item => item.classroomId === classroomId).map(item => item.revision)), frameReceived: false, chunkDurable: false, uploadPending: true, audioDeleted: false, classroomDeleted: false, partial: partialText(classroomId), finalText: [...finalSegments.values()].filter(item => item.classroomId === classroomId).sort((a, b) => a.sequence - b.sequence).map(item => item.text).join(' / ') || null } });
      return true;
    },
    switchClassroom: (classroomId: string) => {
      if (!state.account) return false;
      const projected = taskProjection(classroomId);
      update({ ...state, currentClassroomId: classroomId, flows: { ...state.flows, ai: projected.ai }, eventProjection: { ...state.eventProjection, mayHaveCharged: projected.risk, finalRevision: Math.max(0, ...[...finalSegments.values()].filter(item => item.classroomId === classroomId).map(item => item.revision)), partial: partialText(classroomId), finalText: [...finalSegments.values()].filter(item => item.classroomId === classroomId).sort((a, b) => a.sequence - b.sequence).map(item => item.text).join(' / ') || null, classroomDeleted: deletedClassrooms.has(classroomId) } });
      return true;
    },
    chooseScenario: (scenario: string) => {
      if (!state.account) return;
      stopCapture('ended'); stopEvents(); events.clear(); durableIds.clear(); finalSegments.clear(); partialSegments.clear(); deletedClassrooms.clear(); deletedAudioIds.clear(); resetPorts();
      const scope = freshScope(mockCaptureScope().classSessionId); registerUpload(scope);
      const recording = ['03', '04', '05'].includes(scenario);
      if (recording) capture.command('recording');
      const projected = taskProjection(scope.classSessionId);
      update({ ...state, scenario, currentClassroomId: scope.classSessionId, captureScope: scope, leaseValid: true, tasks: Object.fromEntries(tasks), eventProjection: { ...emptyProjection(), mayHaveCharged: projected.risk, portCaptureState: capture.state, uploadPending: upload.pending(1) }, active: recording, flows: {
        capture: recording ? 'recording' : 'idle',
        upload: scenario === '04' ? 'failed' : 'pending',
        asr: recording ? 'streaming' : 'idle',
        ai: projected.ai,
        sync: ['locked', 'quota', 'write_failed'].includes(state.localWriteResult) ? 'failed' : state.localRecordSync === 'pending' ? 'local_only' : state.localRecordSync === 'reconciled' ? 'synced' : scenario === '05' ? 'failed' : 'synced',
      } });
      connectEvents();
      // This synthetic durable fixture uses the same receipt path as user-triggered events.
      if (scenario === '03') events.emit(bindScriptEvent(eventScripts.upload[1]));
      if (scenario === '06' && ![...tasks.values()].some(task => task.classroomId === scope.classSessionId && task.state === 'unknown' && task.mayHaveCharged)) {
        const event = eventScripts.taskUnknown[0];
        events.emit({ ...event, event_id: crypto.randomUUID(), data: { ...event.data, task_id: crypto.randomUUID(), class_session_id: scope.classSessionId } });
      }
    },
    switchAccount: () => clearScope(state.account === 'A' ? 'B' : 'A'),
    revokeAccount: () => clearScope(null),
    loginAccount: (account: MockAccount) => clearScope(account),
    simulateLocalWrite: (success: boolean) => {
      if (!state.account) return false;
      const value = JSON.stringify({ account: state.account, classroomId: state.currentClassroomId, captureEpochId: state.captureScope?.captureEpochId ?? null, kind: 'synthetic-only' });
      const result = vault.write(localRecordKey, value, !success);
      if (result === 'locked') beforeVaultLock = null;
      update({ ...state, localWriteResult: result, localRecordSync: result === 'stored' ? 'pending' : state.localRecordSync, mockLocalRecord: vault.read(localRecordKey) ?? null, flows: { ...state.flows, sync: result === 'stored' ? 'local_only' : 'failed' } });
      return result === 'stored';
    },
    reconcileLocalWrite: () => { if (!state.account || state.flows.sync !== 'local_only' || !vault.read(localRecordKey)) return false; update({ ...state, localWriteResult: 'reconciled', localRecordSync: 'reconciled', flows: { ...state.flows, sync: 'synced' } }); return true; },
    lockMockVault: () => { if (!state.account || vaultLocked) return; beforeVaultLock = { result: state.localWriteResult, sync: state.flows.sync }; vault.lock(); vaultLocked = true; update({ ...state, localWriteResult: 'locked', mockLocalRecord: null }); },
    unlockMockVault: () => { if (!state.account || !vaultLocked) return; vault.unlock(); vaultLocked = false; const previous = beforeVaultLock; beforeVaultLock = null; update({ ...state, localWriteResult: previous?.result ?? 'none', mockLocalRecord: vault.read(localRecordKey) ?? null, flows: { ...state.flows, sync: previous?.sync ?? state.flows.sync } }); },
    getEpoch: auth.getEpoch,
    isCurrent: auth.isCurrent,
    acceptAccountResponse: (epoch: number, value: unknown) => {
      if (!auth.isCurrent(epoch) || !state.account) return false;
      update({ ...state, accountResponse: value });
      return true;
    },
    runEventScript: (name: EventScriptName) => {
      if (!state.account) return false;
      // Keep the original connection reference so revocation destroys the rest of a script.
      const connection = events;
      eventScripts[name].forEach(event => connection.emit(bindScriptEvent(event)));
      return true;
    },
    runEventStep: (name: EventScriptName, index: number) => {
      const event = eventScripts[name][index];
      if (!state.account || !event) return false;
      events.emit(bindScriptEvent(event));
      return true;
    },
    runHistoricalDurable: (chunkId: string) => {
      const record = uploadRecords.get(chunkId);
      if (!state.account || !record || !record.canAccept || record.entry.status !== 'pending' || deletedAudioIds.has(chunkId) || deletedClassrooms.has(record.entry.classSessionId)) return false;
      const event = eventScripts.upload[1];
      const scope: CaptureScope = { classSessionId: record.entry.classSessionId, captureEpochId: record.entry.captureEpochId, chunkId, fenceToken: 0 };
      events.emit(bindScriptEvent(event, scope));
      return true;
    },
    getEventClient: () => events,
    // An unknown result may have cost money; this mock never submits another task.
    requestAiRetry: () => state.flows.ai === 'failed',
  };
}

export function useMockRuntime() {
  const [runtime] = useState(createMockRuntime);
  const state = useSyncExternalStore(runtime.subscribe, runtime.getState, runtime.getState);
  return { ...runtime, state };
}
