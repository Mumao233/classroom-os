import { useState, useSyncExternalStore } from 'react';
import type { ClassroomOSWebV1EventEnvelope } from '../generated/events';
import { eventScripts, type EventScriptName } from './event-scripts';
import { MockCapturePort, MockEventClient, MockUploadPort } from './ports';

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
type State = { currentClassroomId: string; tasks: Record<string, Task>; leaseValid: boolean; active: boolean; account: MockAccount | null; epoch: number; scenario: string; flows: Flows; accountResponse: unknown | null; captureScope: CaptureScope | null; eventProjection: Projection };
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

export function createMockRuntime() {
  const auth = createAuthEpoch();
  let state: State = { currentClassroomId: mockCaptureScope().classSessionId, tasks: {}, leaseValid: true, active: false, account: 'A', epoch: 0, scenario: '02', flows: emptyFlows(), accountResponse: null, captureScope: mockCaptureScope(), eventProjection: emptyProjection() };
  const listeners = new Set<() => void>();
  let events = new MockEventClient();
  let stopEvents = () => {};
  let durableIds = new Set<string>();
  let finalSegments = new Map<string, { classroomId: string; sequence: number; revision: number; text: string }>();
  let tasks = new Map<string, Task>();
  let taskOrder = 0;
  let partialSegments = new Map<string, { classroomId: string; streamId: string; sequence: number; revision: number; text: string }>();
  let capture = new MockCapturePort();
  let upload = new MockUploadPort();
  upload.stage(1); // The deterministic fixture begins with one staged synthetic chunk.
  let leaseGeneration = 1;
  let deletedClassrooms = new Set<string>();
  let deletedAudioIds = new Set<string>();
  const update = (next: State) => { state = next; listeners.forEach(listener => listener()); };
  const setFlow = <K extends keyof Flows>(flow: K, value: Flows[K]) => update({ ...state, flows: { ...state.flows, [flow]: value } });
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
    stopCapture('ended'); stopEvents(); events.clear(); durableIds = new Set(); finalSegments = new Map(); partialSegments = new Map(); tasks = new Map(); taskOrder = 0; deletedClassrooms = new Set(); deletedAudioIds = new Set(); resetPorts();
    const scope = account ? freshScope(mockCaptureScope().classSessionId) : null;
    update({ ...state, account, epoch: nextEpoch, currentClassroomId: mockCaptureScope().classSessionId, tasks: {}, leaseValid: Boolean(scope), active: false, scenario: '02', accountResponse: null, captureScope: scope, flows: emptyFlows(), eventProjection: { ...emptyProjection(), uploadPending: Boolean(account) } });
    if (account) connectEvents();
  };
  const handleEvent = (event: ClassroomOSWebV1EventEnvelope, eventEpoch: number) => {
    if (!auth.isCurrent(eventEpoch) || !state.account) return;
    if (event.type === 'error' && event.data.code === 'AUTH_REVOKED') { clearScope(null); return; }
    const scope = state.captureScope;
    if ((event.type === 'capture.lease_lost' || event.type === 'audio.chunk_durable' || event.type === 'capture.frame_received') && !state.leaseValid) return;
    if (event.type === 'capture.lease_lost' && (!scope || event.data.class_session_id !== scope.classSessionId || event.data.capture_epoch_id !== scope.captureEpochId || event.data.fence_token !== scope.fenceToken)) return;
    // The durable receipt schema has no fence token; it carries classroom, epoch and chunk identity.
    if (event.type === 'audio.chunk_durable' && (!scope || event.data.class_session_id !== scope.classSessionId || event.data.capture_epoch_id !== scope.captureEpochId || event.data.chunk_id !== scope.chunkId)) return;
    // A frame ACK only includes chunk_id; it must match the bound synthetic chunk.
    if (event.type === 'capture.frame_received' && (!scope || event.data.chunk_id !== scope.chunkId)) return;
    if ('event_id' in event) {
      if (durableIds.has(event.event_id)) return;
      durableIds.add(event.event_id);
    }
    const projection = { ...state.eventProjection, delivered: state.eventProjection.delivered + 1 };
    let flows = state.flows;
    let active = state.active;
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
        if (!deletedAudioIds.has(event.data.chunk_id) && upload.frameReceived(event.data.frame_seq)) { projection.frameReceived = true; projection.uploadPending = upload.pending(event.data.frame_seq); }
        break;
      case 'audio.chunk_durable':
        if (!deletedAudioIds.has(event.data.chunk_id) && !deletedClassrooms.has(event.data.class_session_id) && upload.pending(event.data.seq)) { upload.durable(event.data.seq); projection.uploadPending = upload.pending(event.data.seq); projection.chunkDurable = true; flows = { ...flows, upload: 'durable' }; }
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
        if (event.data.object_type === 'audio') { deletedAudioIds.add(event.data.object_id); if (scope?.chunkId === event.data.object_id) projection.audioDeleted = true; }
        if (event.data.object_type === 'class_session') {
          if (state.currentClassroomId === event.data.object_id) projection.classroomDeleted = true;
          deletedClassrooms.add(event.data.object_id);
          if (scope?.classSessionId === event.data.object_id) { stopCapture('failed'); active = false; flows = { ...flows, capture: 'failed' }; }
          for (const [key, item] of partialSegments) if (item.classroomId === event.data.object_id) partialSegments.delete(key);
          for (const [key, item] of finalSegments) if (item.classroomId === event.data.object_id) finalSegments.delete(key);
          projection.finalText = [...finalSegments.values()].filter(segment => segment.classroomId === state.currentClassroomId).sort((a, b) => a.sequence - b.sequence).map(segment => segment.text).join(' / ') || null;
          if (state.currentClassroomId === event.data.object_id) projection.finalRevision = 0;
          projection.partial = partialText(state.currentClassroomId);
        }
        break;
      case 'capture.lease_lost': projection.leaseLost = true; stopCapture('failed'); active = false; flows = { ...flows, capture: 'failed' }; break;
    }
    const invalidated = event.type === 'capture.lease_lost' || (event.type === 'object.deleted' && event.data.object_type === 'class_session' && scope?.classSessionId === event.data.object_id);
    projection.portCaptureState = capture.state;
    update({ ...state, active, flows, leaseValid: invalidated ? false : state.leaseValid, tasks: Object.fromEntries(tasks), eventProjection: projection });
  };
  function connectEvents() {
    events = new MockEventClient();
    const scopeEpoch = state.epoch;
    stopEvents = events.subscribe(event => handleEvent(event, scopeEpoch));
  }
  const bindScriptEvent = (event: ClassroomOSWebV1EventEnvelope): ClassroomOSWebV1EventEnvelope => {
    const scope = state.captureScope;
    if (!scope) return event;
    if (event.type === 'capture.lease_lost') return { ...event, data: { ...event.data, class_session_id: scope.classSessionId, capture_epoch_id: scope.captureEpochId, fence_token: scope.fenceToken } };
    if (event.type === 'audio.chunk_durable') return { ...event, data: { ...event.data, class_session_id: scope.classSessionId, capture_epoch_id: scope.captureEpochId, chunk_id: scope.chunkId } };
    if (event.type === 'capture.frame_received') return { ...event, data: { ...event.data, chunk_id: scope.chunkId } };
    if (event.type === 'object.deleted') return { ...event, data: { ...event.data, object_id: event.data.object_type === 'audio' ? scope.chunkId : event.data.object_type === 'class_session' ? scope.classSessionId : event.data.object_id } };
    if (event.type === 'transcript.partial') return { ...event, data: { ...event.data, class_session_id: state.currentClassroomId } };
    if (event.type === 'transcript.final') return { ...event, data: { ...event.data, class_session_id: state.currentClassroomId } };
    if (event.type === 'task.changed') return { ...event, data: { ...event.data, class_session_id: state.currentClassroomId } };
    return event;
  };
  connectEvents();
  return {
    getState: () => state,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => listeners.delete(listener); },
    start: () => {
      if (!state.account || state.active || !state.leaseValid || !state.captureScope || state.captureScope.classSessionId !== state.currentClassroomId || deletedClassrooms.has(state.captureScope.classSessionId)) return false;
      if (capture.state === 'ended') capture = new MockCapturePort();
      if (!capture.command('recording')) return false;
      update({ ...state, active: true, flows: { ...state.flows, capture: 'recording', upload: 'pending', asr: 'idle', sync: 'synced' }, eventProjection: { ...state.eventProjection, portCaptureState: capture.state } });
      return true;
    },
    stop: () => { stopCapture('ended'); update({ ...state, active: false, flows: { ...state.flows, capture: 'ended' }, eventProjection: { ...state.eventProjection, portCaptureState: capture.state } }); },
    acquireMockLease: (classroomId = state.currentClassroomId) => {
      if (!state.account || state.active || deletedClassrooms.has(classroomId)) return false;
      const scope = freshScope(classroomId); resetPorts();
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
      const scope = freshScope(mockCaptureScope().classSessionId);
      const recording = ['03', '04', '05'].includes(scenario);
      if (recording) capture.command('recording');
      if (scenario === '03') upload.durable(1);
      const projected = taskProjection(scope.classSessionId);
      update({ ...state, scenario, currentClassroomId: scope.classSessionId, captureScope: scope, leaseValid: true, tasks: Object.fromEntries(tasks), eventProjection: { ...emptyProjection(), mayHaveCharged: projected.risk, portCaptureState: capture.state, chunkDurable: scenario === '03', uploadPending: upload.pending(1) }, active: recording, flows: {
        capture: recording ? 'recording' : 'idle',
        upload: scenario === '04' ? 'failed' : scenario === '03' ? 'durable' : 'pending',
        asr: recording ? 'streaming' : 'idle',
        ai: projected.ai,
        sync: scenario === '05' ? 'failed' : 'synced',
      } });
      connectEvents();
      if (scenario === '06' && ![...tasks.values()].some(task => task.classroomId === scope.classSessionId && task.state === 'unknown' && task.mayHaveCharged)) {
        const event = eventScripts.taskUnknown[0];
        events.emit({ ...event, event_id: crypto.randomUUID(), data: { ...event.data, task_id: crypto.randomUUID(), class_session_id: scope.classSessionId } });
      }
    },
    switchAccount: () => clearScope(state.account === 'A' ? 'B' : 'A'),
    revokeAccount: () => clearScope(null),
    loginAccount: (account: MockAccount) => clearScope(account),
    simulateLocalWrite: (success: boolean) => { if (!state.account) return false; setFlow('sync', success ? 'local_only' : 'failed'); return true; },
    reconcileLocalWrite: () => { if (!state.account || state.flows.sync !== 'local_only') return false; setFlow('sync', 'synced'); return true; },
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
