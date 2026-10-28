import { useState, useSyncExternalStore } from 'react';

type Capture = 'idle' | 'recording' | 'paused' | 'local_only' | 'failed' | 'ended';
type Upload = 'pending' | 'uploading' | 'durable' | 'failed';
type Asr = 'idle' | 'streaming' | 'catching_up' | 'draining' | 'succeeded' | 'failed' | 'unknown';
type Ai = 'idle' | 'queued' | 'running' | 'succeeded' | 'failed' | 'unknown' | 'cancel_requested' | 'cancelled';
type Sync = 'synced' | 'local_only' | 'syncing' | 'failed' | 'conflict';
export type Flows = { capture: Capture; upload: Upload; asr: Asr; ai: Ai; sync: Sync };
type State = { active: boolean; account: 'A' | 'B'; epoch: number; scenario: string; flows: Flows; accountResponse: unknown | null };

export function createAuthEpoch() {
  let epoch = 0;
  return { getEpoch: () => epoch, invalidate: () => ++epoch, isCurrent: (value: number) => value === epoch };
}

export function createMockRuntime() {
  const auth = createAuthEpoch();
  let state: State = { active: false, account: 'A', epoch: 0, scenario: '02', flows: { capture: 'idle', upload: 'pending', asr: 'idle', ai: 'idle', sync: 'synced' }, accountResponse: null };
  const listeners = new Set<() => void>();
  const update = (next: State) => { state = next; listeners.forEach(listener => listener()); };
  const setFlow = <K extends keyof Flows>(flow: K, value: Flows[K]) => update({ ...state, flows: { ...state.flows, [flow]: value } });
  return {
    getState: () => state,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => listeners.delete(listener); },
    start: () => update({ ...state, active: true, flows: { capture: 'recording', upload: 'pending', asr: 'idle', ai: 'idle', sync: 'synced' } }),
    stop: () => update({ ...state, active: false, flows: { ...state.flows, capture: 'ended' } }),
    chooseScenario: (scenario: string) => {
      const recording = ['03', '04', '05'].includes(scenario);
      update({ ...state, scenario, active: recording, flows: {
        capture: recording ? 'recording' : 'idle',
        upload: scenario === '04' ? 'failed' : scenario === '03' ? 'durable' : 'pending',
        asr: recording ? 'streaming' : 'idle',
        ai: scenario === '06' ? 'unknown' : 'idle',
        sync: scenario === '05' ? 'failed' : 'synced',
      } });
    },
    switchAccount: () => update({ ...state, account: state.account === 'A' ? 'B' : 'A', epoch: auth.invalidate(), active: false, accountResponse: null, flows: { capture: 'idle', upload: 'pending', asr: 'idle', ai: 'idle', sync: 'synced' } }),
    setFlow,
    getEpoch: auth.getEpoch,
    isCurrent: auth.isCurrent,
    acceptAccountResponse: (epoch: number, value: unknown) => {
      if (!auth.isCurrent(epoch)) return false;
      update({ ...state, accountResponse: value });
      return true;
    },
    // An unknown result may have cost money; this mock never submits another task.
    requestAiRetry: () => state.flows.ai !== 'unknown' && state.flows.ai === 'failed',
  };
}

export function useMockRuntime() {
  const [runtime] = useState(createMockRuntime);
  const state = useSyncExternalStore(runtime.subscribe, runtime.getState, runtime.getState);
  return { ...runtime, state };
}
