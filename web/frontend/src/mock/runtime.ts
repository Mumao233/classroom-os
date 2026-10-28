import { useState, useSyncExternalStore } from 'react';
import type { ClassroomOSWebV1EventEnvelope } from '../generated/events';
import { eventScripts, type EventScriptName } from './event-scripts';
import { MockEventClient } from './ports';

type Capture = 'idle' | 'recording' | 'paused' | 'local_only' | 'failed' | 'ended';
type Upload = 'pending' | 'uploading' | 'durable' | 'failed';
type Asr = 'idle' | 'streaming' | 'catching_up' | 'draining' | 'succeeded' | 'failed' | 'unknown';
type Ai = 'idle' | 'queued' | 'running' | 'succeeded' | 'failed' | 'unknown' | 'cancel_requested' | 'cancelled';
type Sync = 'synced' | 'local_only' | 'syncing' | 'failed' | 'conflict';
export type Flows = { capture: Capture; upload: Upload; asr: Asr; ai: Ai; sync: Sync };
export type MockAccount = 'A' | 'B';
type Projection = { partial: string | null; finalText: string | null; finalRevision: number; frameReceived: boolean; chunkDurable: boolean; audioDeleted: boolean; classroomDeleted: boolean; leaseLost: boolean; mayHaveCharged: boolean; delivered: number };
type CaptureScope = { classSessionId: string; captureEpochId: string; fenceToken: number; chunkId: string };
type State = { active: boolean; account: MockAccount | null; epoch: number; scenario: string; flows: Flows; accountResponse: unknown | null; captureScope: CaptureScope | null; eventProjection: Projection };
const emptyFlows = (): Flows => ({ capture: 'idle', upload: 'pending', asr: 'idle', ai: 'idle', sync: 'synced' });
const emptyProjection = (): Projection => ({ partial: null, finalText: null, finalRevision: 0, frameReceived: false, chunkDurable: false, audioDeleted: false, classroomDeleted: false, leaseLost: false, mayHaveCharged: false, delivered: 0 });
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
  let state: State = { active: false, account: 'A', epoch: 0, scenario: '02', flows: emptyFlows(), accountResponse: null, captureScope: mockCaptureScope(), eventProjection: emptyProjection() };
  const listeners = new Set<() => void>();
  let events = new MockEventClient();
  let stopEvents = () => {};
  let durableIds = new Set<string>();
  let finalSegments = new Map<string, { classroomId: string; sequence: number; revision: number; text: string }>();
  let taskRevisions = new Map<string, number>();
  let deletedClassrooms = new Set<string>();
  let deletedAudioIds = new Set<string>();
  const update = (next: State) => { state = next; listeners.forEach(listener => listener()); };
  const setFlow = <K extends keyof Flows>(flow: K, value: Flows[K]) => update({ ...state, flows: { ...state.flows, [flow]: value } });
  const clearScope = (account: MockAccount | null) => {
    // Invalidate first: synchronous teardown must win over any already queued callbacks.
    const nextEpoch = auth.invalidate();
    stopEvents(); events.clear(); durableIds = new Set(); finalSegments = new Map(); taskRevisions = new Map(); deletedClassrooms = new Set(); deletedAudioIds = new Set();
    update({ ...state, account, epoch: nextEpoch, active: false, scenario: '02', accountResponse: null, captureScope: account ? mockCaptureScope() : null, flows: emptyFlows(), eventProjection: emptyProjection() });
    if (account) connectEvents();
  };
  const handleEvent = (event: ClassroomOSWebV1EventEnvelope, eventEpoch: number) => {
    if (!auth.isCurrent(eventEpoch) || !state.account) return;
    if (event.type === 'error' && event.data.code === 'AUTH_REVOKED') { clearScope(null); return; }
    const scope = state.captureScope;
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
      case 'transcript.partial':
        if (!deletedClassrooms.has(event.data.class_session_id) && !finalSegments.has(event.data.segment_id)) projection.partial = event.data.text;
        break;
      case 'transcript.final': {
        const previous = finalSegments.get(event.data.segment_id);
        if (!deletedClassrooms.has(event.data.class_session_id) && event.data.revision > (previous?.revision ?? 0)) {
          finalSegments.set(event.data.segment_id, { classroomId: event.data.class_session_id, sequence: event.data.sequence, revision: event.data.revision, text: event.data.text });
          projection.finalRevision = event.data.revision;
          projection.finalText = [...finalSegments.values()].sort((a, b) => a.sequence - b.sequence).map(segment => segment.text).join(' / ');
          projection.partial = null;
        }
        break;
      }
      case 'capture.frame_received':
        if (!deletedAudioIds.has(event.data.chunk_id)) projection.frameReceived = true;
        break;
      case 'audio.chunk_durable':
        if (!deletedAudioIds.has(event.data.chunk_id) && !deletedClassrooms.has(event.data.class_session_id)) { projection.chunkDurable = true; flows = { ...flows, upload: 'durable' }; }
        break;
      case 'task.changed':
        if (event.data.revision > (taskRevisions.get(event.data.task_id) ?? 0)) {
          taskRevisions.set(event.data.task_id, event.data.revision);
          projection.mayHaveCharged = event.data.may_have_charged;
          flows = { ...flows, ai: event.data.state };
        }
        break;
      case 'object.deleted':
        if (event.data.object_type === 'audio') { deletedAudioIds.add(event.data.object_id); projection.audioDeleted = true; }
        if (event.data.object_type === 'class_session') {
          projection.classroomDeleted = true;
          deletedClassrooms.add(event.data.object_id);
          for (const [key, item] of finalSegments) if (item.classroomId === event.data.object_id) finalSegments.delete(key);
          projection.finalText = [...finalSegments.values()].sort((a, b) => a.sequence - b.sequence).map(segment => segment.text).join(' / ') || null;
          projection.finalRevision = 0; projection.partial = null;
        }
        break;
      case 'capture.lease_lost': projection.leaseLost = true; active = false; flows = { ...flows, capture: 'failed' }; break;
    }
    update({ ...state, active, flows, eventProjection: projection });
  };
  function connectEvents() {
    events = new MockEventClient();
    const scopeEpoch = state.epoch;
    stopEvents = events.subscribe(event => handleEvent(event, scopeEpoch));
  }
  connectEvents();
  return {
    getState: () => state,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => listeners.delete(listener); },
    start: () => { if (state.account && !state.active) update({ ...state, active: true, flows: { capture: 'recording', upload: 'pending', asr: 'idle', ai: 'idle', sync: 'synced' } }); },
    stop: () => update({ ...state, active: false, flows: { ...state.flows, capture: 'ended' } }),
    chooseScenario: (scenario: string) => {
      if (!state.account) return;
      const recording = ['03', '04', '05'].includes(scenario);
      update({ ...state, scenario, active: recording, flows: {
        capture: recording ? 'recording' : 'idle',
        upload: scenario === '04' ? 'failed' : scenario === '03' ? 'durable' : 'pending',
        asr: recording ? 'streaming' : 'idle',
        ai: scenario === '06' ? 'unknown' : 'idle',
        sync: scenario === '05' ? 'failed' : 'synced',
      } });
    },
    switchAccount: () => clearScope(state.account === 'A' ? 'B' : 'A'),
    revokeAccount: () => clearScope(null),
    loginAccount: (account: MockAccount) => clearScope(account),
    setFlow,
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
      eventScripts[name].forEach(event => connection.emit(event));
      return true;
    },
    runEventStep: (name: EventScriptName, index: number) => {
      const event = eventScripts[name][index];
      if (!state.account || !event) return false;
      events.emit(event);
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
