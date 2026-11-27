import { createDraftStore } from '../domain/classroom';
import type { BoundCapture, ClassroomShell, PendingCommand, PendingStop } from '../features/classroom';
import type { createMockRuntime } from './runtime';
import { ensureMockDemoClassroom, setMockDurable } from './start';

type MockRuntime = ReturnType<typeof createMockRuntime>;
/** The PR-09a runtime numbers its synthetic chunks from 1; the contract's chunk sequence starts at 0. */
const serverSeq = (runtimeSeq: number) => Math.max(0, runtimeSeq - 1);
const LAYOUT_KEY = 'classroom-layout-v1';
const DEVICE = { A: 'a2000000-0000-4000-8000-000000000001', B: 'b2000000-0000-4000-8000-000000000001', C: 'c2000000-0000-4000-8000-000000000001' } as const;
/** Stable synthetic device identity of an account's browser profile (Mock). */
export const mockDeviceId = (account: 'A' | 'B' | 'C') => DEVICE[account];

/**
 * Development adapter from the PR-09a in-page capture runtime to the PR-10a classroom shell.
 * One instance per account epoch: drafts and pending stops never cross an account change.
 */
export function createMockClassroomShell(runtime: MockRuntime, now: () => number): ClassroomShell {
  const drafts = createDraftStore();
  const pendingStops = new Map<string, PendingStop>();
  const pendingCommands = new Map<string, PendingCommand>();
  const offsets = new Map<string, number>();
  // Classrooms whose capture was stopped in this account epoch: their still-acceptable local audio is
  // at risk on unload even after another classroom is bound.
  const stopped = new Set<string>();
  const listeners = new Set<() => void>();
  let changes = 0;
  const changed = () => { changes++; listeners.forEach(listener => listener()); };
  const bound = (): BoundCapture | null => {
    const state = runtime.getState();
    const scope = state.captureScope;
    if (!state.account || !scope) return null;
    const port = state.eventProjection.portCaptureState;
    if (!state.active && port !== 'ended' && port !== 'failed') return null;
    const seqs = state.uploadHistory.filter(item => item.captureEpochId === scope.captureEpochId).map(item => item.seq);
    const lastSeq = Math.max(0, ...seqs.map(serverSeq));
    // Each synthetic chunk is one second at 16 kHz; last_sample is the exclusive end of the last chunk.
    const lastSample = seqs.length ? (lastSeq + 1) * 16000 : 0;
    return { classId: scope.classSessionId, captureEpochId: scope.captureEpochId, fenceToken: Math.max(1, scope.fenceToken), active: state.active, flows: state.flows,
      leaseLost: state.eventProjection.leaseLost, classroomRevoked: state.eventProjection.classroomDeleted, lastSeq, lastSample };
  };
  const durableSeqs = (classId: string, captureEpochId: string) => runtime.getState().durableUploads.filter(item => item.classSessionId === classId && item.captureEpochId === captureEpochId).map(item => serverSeq(item.seq));
  const epochsOf = (classId: string) => [...new Set(runtime.getState().durableUploads.filter(item => item.classSessionId === classId).map(item => item.captureEpochId))];
  return {
    deviceId: DEVICE[runtime.getState().account ?? 'A'],
    consent: { microphone: 'mock-microphone-2026-11', recovery: 'mock-recovery-2026-11', asr: 'mock-asr-2026-11' },
    drafts,
    now,
    capture: {
      bound,
      start: (classId, lease) => { offsets.set(lease.captureEpochId, lease.timelineOffsetMs); return runtime.acquireMockLease(classId, lease) && runtime.start(); },
      pause: () => runtime.pause(),
      rebind: lease => { offsets.set(lease.captureEpochId, lease.timelineOffsetMs); return runtime.rebindMockLease(lease); },
      resume: lease => { offsets.set(lease.captureEpochId, lease.timelineOffsetMs); return runtime.rebindMockLease(lease) && runtime.resume(); },
      // Capture clock: the epoch's timeline offset plus the samples captured in it (16 kHz).
      positionMs: classId => { const capture = bound(); return capture && capture.classId === classId ? (offsets.get(capture.captureEpochId) ?? 0) + Math.floor(capture.lastSample / 16) : null; },
      stopLocal: () => { const capture = bound(); if (capture) stopped.add(capture.classId); runtime.stop(); changed(); },
      durableSeqs,
      pendingChunks: classId => runtime.getState().uploadHistory.filter(item => item.classSessionId === classId && item.status === 'pending').length,
      liveSegments: classId => runtime.getState().liveSegments.filter(item => item.classroomId === classId),
    },
    pendingStops: { get: classId => pendingStops.get(classId) ?? null, set: (classId, value) => { if (value) pendingStops.set(classId, value); else pendingStops.delete(classId); changed(); } },
    pendingCommands: { get: classId => pendingCommands.get(classId) ?? null, set: (classId, value) => { if (value) pendingCommands.set(classId, value); else pendingCommands.delete(classId); changed(); } },
    unsafeToLeave: () => {
      const capture = bound();
      // Revoked classrooms make their chunks unacceptable (canAccept false), which clears the risk.
      return pendingStops.size > 0 || runtime.getState().uploadHistory.some(item => item.status === 'pending' && item.canAccept
        && (stopped.has(item.classSessionId) || (capture !== null && !capture.active && capture.classId === item.classSessionId)));
    },
    subscribe: listener => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    version: () => changes,
    layoutStorage: {
      read: () => { try { return localStorage.getItem(LAYOUT_KEY); } catch { return null; } },
      write: value => { try { localStorage.setItem(LAYOUT_KEY, value); } catch { /* layout memory is optional */ } },
    },
    ensureBoundClassroom: classId => {
      const capture = bound();
      if (!capture || capture.classId !== classId) return false;
      const account = runtime.getState().account;
      return ensureMockDemoClassroom({ classId, title: account === 'B' ? '模拟课堂 B' : '模拟课堂 · 线性代数', captureEpochId: capture.captureEpochId, fenceToken: capture.fenceToken });
    },
    beforeServerStop: classId => { for (const epoch of epochsOf(classId)) setMockDurable(classId, epoch, durableSeqs(classId, epoch)); },
  };
}
