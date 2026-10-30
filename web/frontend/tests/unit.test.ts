import { describe, expect, it } from 'vitest';
import { createMockRuntime } from '../src/mock/runtime';
import { MemoryVault, MockCapturePort, MockEventClient, MockUploadPort } from '../src/mock/ports';
import type { ClassroomOSWebV1EventEnvelope } from '../src/generated/events';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { eventScripts } from '../src/mock/event-scripts';
import { coursePages } from '../src/mock/fixtures';

describe('runtime used by the preview shell', () => {
  it('keeps upload failure independent of other flows', () => {
    const runtime = createMockRuntime();
    runtime.chooseScenario('04');
    expect(runtime.getState().flows).toMatchObject({ capture: 'recording', upload: 'failed', asr: 'streaming' });
    runtime.runEventScript('upload');
    expect(runtime.getState().flows.capture).toBe('recording');
    expect(runtime.getState().flows.asr).toBe('streaming');
  });
  it('rejects ordinary retry on unknown outcome', () => {
    const runtime = createMockRuntime();
    runtime.chooseScenario('06');
    expect(runtime.getState().flows.ai).toBe('unknown');
    expect(runtime.requestAiRetry()).toBe(false);
    expect(runtime.getState().flows.ai).toBe('unknown');
  });
  it('resets independent scenario state instead of carrying a prior recording into an empty scenario', () => {
    const runtime = createMockRuntime();
    runtime.chooseScenario('04');
    runtime.chooseScenario('01');
    expect(runtime.getState()).toMatchObject({ active: false, scenario: '01', flows: { capture: 'idle', upload: 'pending', asr: 'idle', ai: 'idle', sync: 'synced' } });
  });
  it('invalidates and clears account data, rejecting an A response after switch to B', () => {
    const runtime = createMockRuntime();
    const aEpoch = runtime.getEpoch();
    expect(runtime.acceptAccountResponse(aEpoch, 'A data')).toBe(true);
    runtime.start();
    runtime.switchAccount();
    expect(runtime.getState()).toMatchObject({ account: 'B', active: false, accountResponse: null });
    expect(runtime.acceptAccountResponse(aEpoch, 'late A data')).toBe(false);
    expect(runtime.acceptAccountResponse(runtime.getEpoch(), 'B data')).toBe(true);
    expect(runtime.getState().accountResponse).toBe('B data');
  });
  it('disconnects old event scope and rejects delayed account and event data after revocation and login B', async () => {
    const runtime = createMockRuntime();
    const oldEpoch = runtime.getEpoch();
    const oldEvents = runtime.getEventClient();
    expect(oldEvents.listenerCount()).toBe(1);
    runtime.start();
    runtime.runEventScript('transcript');
    expect(runtime.getState().eventProjection.finalText).toBe('合成的正式片段');
    let resolveA!: (value: typeof coursePages.A) => void;
    const pendingA = new Promise<typeof coursePages.A>(resolve => { resolveA = resolve; });
    const lateCommit = pendingA.then(data => runtime.acceptAccountResponse(oldEpoch, data));
    runtime.revokeAccount();
    expect(oldEvents.listenerCount()).toBe(0);
    expect(runtime.getState()).toMatchObject({ account: null, active: false, accountResponse: null, eventProjection: { finalText: null } });
    runtime.loginAccount('B');
    expect(runtime.acceptAccountResponse(runtime.getEpoch(), coursePages.B)).toBe(true);
    resolveA(coursePages.A);
    expect(await lateCommit).toBe(false);
    oldEvents.emit(eventScripts.transcript[1]);
    expect(runtime.getState()).toMatchObject({ account: 'B', accountResponse: coursePages.B, eventProjection: { finalText: null } });
  });
  it('projects schema-shaped events, deduplicates final delivery and keeps late revisions from overwriting newer data', () => {
    const runtime = createMockRuntime();
    const snapshots: Array<ReturnType<typeof runtime.getState>['eventProjection']> = [];
    const stop = runtime.subscribe(() => snapshots.push(runtime.getState().eventProjection));
    runtime.runEventScript('transcript');
    expect(snapshots.some(item => item.partial === '合成的临时片段')).toBe(true);
    expect(runtime.getState().eventProjection).toMatchObject({ partial: null, finalText: '合成的正式片段', finalRevision: 2, delivered: 3 });
    stop();
    runtime.runEventScript('deletion');
    expect(runtime.getState().eventProjection).toMatchObject({ audioDeleted: true, finalText: '合成的正式片段' });
  });
  it('treats frame ACK separately from durable receipt and unknown charge as non-retryable', () => {
    const runtime = createMockRuntime();
    runtime.getEventClient().emit({ ...eventScripts.upload[0], data: { ...eventScripts.upload[0].data, frame_seq: 99 } });
    expect(runtime.getState().eventProjection.frameReceived).toBe(false);
    runtime.getEventClient().emit(eventScripts.upload[0]);
    expect(runtime.getState()).toMatchObject({ flows: { upload: 'pending' }, eventProjection: { frameReceived: true, chunkDurable: false } });
    runtime.getEventClient().emit(eventScripts.upload[1]);
    expect(runtime.getState()).toMatchObject({ flows: { upload: 'durable' }, eventProjection: { chunkDurable: true } });
    runtime.runEventScript('taskUnknown');
    expect(runtime.getState()).toMatchObject({ flows: { ai: 'unknown' }, eventProjection: { mayHaveCharged: true } });
    expect(runtime.requestAiRetry()).toBe(false);
  });
  it('delivers scripted steps through the connected event port without skipping intermediate states', () => {
    const runtime = createMockRuntime();
    expect(runtime.runEventStep('transcript', 0)).toBe(true);
    expect(runtime.getState().eventProjection).toMatchObject({ partial: '合成的临时片段', finalText: null });
    expect(runtime.runEventStep('transcript', 1)).toBe(true);
    expect(runtime.getState().eventProjection).toMatchObject({ partial: null, finalText: '合成的正式片段' });
    expect(runtime.runEventStep('upload', 0)).toBe(true);
    expect(runtime.getState()).toMatchObject({ flows: { upload: 'pending' }, eventProjection: { frameReceived: true, chunkDurable: false } });
    expect(runtime.runEventStep('upload', 1)).toBe(true);
    expect(runtime.getState()).toMatchObject({ flows: { upload: 'durable' }, eventProjection: { chunkDurable: true } });
    expect(runtime.runEventStep('upload', 99)).toBe(false);
  });
  it('lease loss ends simulated capture; AUTH_REVOKED event clears and tears down the account', () => {
    const runtime = createMockRuntime();
    runtime.start();
    runtime.runEventScript('leaseLoss');
    expect(runtime.getState()).toMatchObject({ active: false, flows: { capture: 'failed' }, eventProjection: { leaseLost: true } });
    const old = runtime.getEventClient();
    runtime.runEventScript('revocation');
    expect(old.listenerCount()).toBe(0);
    expect(runtime.getState()).toMatchObject({ account: null, active: false, accountResponse: null });
    expect(runtime.runEventScript('transcript')).toBe(false);
  });
  it('does not stop the current capture for another classroom, capture epoch, or fence', () => {
    const runtime = createMockRuntime();
    runtime.start();
    const event = eventScripts.leaseLoss[0];
    const otherId = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';
    for (const data of [
      { ...event.data, class_session_id: otherId },
      { ...event.data, capture_epoch_id: otherId },
      { ...event.data, fence_token: event.data.fence_token + 1 },
    ]) runtime.getEventClient().emit({ ...event, data });
    expect(runtime.getState()).toMatchObject({ active: true, flows: { capture: 'recording' }, eventProjection: { leaseLost: false, delivered: 0 } });
    // A rejected out-of-scope ID must not poison deduplication for the matching event.
    runtime.getEventClient().emit(event);
    expect(runtime.getState()).toMatchObject({ active: false, flows: { capture: 'failed' }, eventProjection: { leaseLost: true } });
  });
  it('ignores frame and durable receipts for a different bound classroom, epoch or chunk', () => {
    const runtime = createMockRuntime();
    const [frame, receipt] = eventScripts.upload;
    const otherId = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';
    runtime.getEventClient().emit({ ...frame, data: { ...frame.data, chunk_id: otherId } });
    for (const data of [
      { ...receipt.data, class_session_id: otherId },
      { ...receipt.data, capture_epoch_id: otherId },
      { ...receipt.data, chunk_id: otherId },
    ]) runtime.getEventClient().emit({ ...receipt, data });
    expect(runtime.getState()).toMatchObject({ flows: { upload: 'pending' }, eventProjection: { frameReceived: false, chunkDurable: false, delivered: 0 } });
    runtime.getEventClient().emit(frame);
    runtime.getEventClient().emit(receipt);
    expect(runtime.getState()).toMatchObject({ flows: { upload: 'durable' }, eventProjection: { frameReceived: true, chunkDurable: true } });
  });
  it('does not resurrect a deleted classroom from a newly arriving final event', () => {
    const runtime = createMockRuntime();
    runtime.runEventScript('transcript');
    runtime.runEventScript('classroomDeletion');
    const oldFinal = eventScripts.transcript[1];
    runtime.getEventClient().emit({ ...oldFinal, event_id: 'cccccccc-cccc-4ccc-8ccc-ccccccccccc2', data: { ...oldFinal.data, revision: 3, text: '迟到后不可复活' } });
    expect(runtime.getState().eventProjection).toMatchObject({ finalText: null, partial: null });
  });
  it('blocks newly identified late frame ACK and durable receipt after audio access is revoked', () => {
    const runtime = createMockRuntime();
    runtime.runEventScript('deletion');
    const [frame, receipt] = eventScripts.upload;
    runtime.getEventClient().emit({ ...frame, connection_seq: frame.connection_seq + 10 });
    runtime.getEventClient().emit({ ...receipt, event_id: 'cccccccc-cccc-4ccc-8ccc-ccccccccccc4' });
    expect(runtime.getState()).toMatchObject({ flows: { upload: 'pending' }, eventProjection: { audioDeleted: true, frameReceived: false, chunkDurable: false } });
    // Tombstones belong to the old account scope, not the next account's simulation.
    runtime.switchAccount();
    runtime.getEventClient().emit(receipt);
    expect(runtime.getState()).toMatchObject({ account: 'B', flows: { upload: 'pending' }, eventProjection: { audioDeleted: false, chunkDurable: false } });
    const scope = runtime.getState().captureScope!;
    runtime.getEventClient().emit({ ...receipt, data: { ...receipt.data, class_session_id: scope.classSessionId, capture_epoch_id: scope.captureEpochId, chunk_id: scope.chunkId } });
    expect(runtime.getState()).toMatchObject({ account: 'B', flows: { upload: 'durable' }, eventProjection: { audioDeleted: false, chunkDurable: true } });
  });
  it('does not replace a newer unknown task result with an older succeeded event', () => {
    const runtime = createMockRuntime();
    const task = eventScripts.taskUnknown[0];
    runtime.getEventClient().emit({ ...task, data: { ...task.data, revision: 2 } });
    runtime.getEventClient().emit({ ...task, event_id: 'cccccccc-cccc-4ccc-8ccc-ccccccccccc3', data: { ...task.data, revision: 1, state: 'succeeded', may_have_charged: false } });
    expect(runtime.getState()).toMatchObject({ flows: { ai: 'unknown' }, eventProjection: { mayHaveCharged: true } });
    expect(runtime.requestAiRetry()).toBe(false);
  });
});

describe('revocation, isolated tasks and segment projection', () => {
  it('stops immediately on classroom revocation and rejects restart in that classroom', () => {
    const runtime = createMockRuntime();
    expect(runtime.start()).toBe(true);
    runtime.runEventScript('classroomDeletion');
    expect(runtime.getState()).toMatchObject({ active: false, leaseValid: false, flows: { capture: 'failed' }, eventProjection: { portCaptureState: 'failed' } });
    expect(runtime.start()).toBe(false);
    expect(runtime.acquireMockLease()).toBe(false);
    runtime.getEventClient().emit(eventScripts.upload[1]);
    expect(runtime.getState()).toMatchObject({ active: false, flows: { capture: 'failed', upload: 'pending' } });
  });
  it('requires a new lease after loss; old lease events cannot stop the new capture', () => {
    const runtime = createMockRuntime();
    expect(runtime.start()).toBe(true);
    const oldScope = runtime.getState().captureScope!;
    runtime.runEventScript('upload');
    expect(runtime.getState().eventProjection).toMatchObject({ frameReceived: true, chunkDurable: true, uploadPending: false });
    runtime.runEventScript('leaseLoss');
    expect(runtime.start()).toBe(false);
    expect(runtime.getState()).toMatchObject({ active: false, leaseValid: false, flows: { capture: 'failed' } });
    expect(runtime.acquireMockLease()).toBe(true);
    const newScope = runtime.getState().captureScope!;
    expect(runtime.getState()).toMatchObject({ flows: { upload: 'pending' }, eventProjection: { frameReceived: false, chunkDurable: false, uploadPending: true } });
    expect(newScope.captureEpochId).not.toBe(oldScope.captureEpochId);
    expect(newScope.fenceToken).toBeGreaterThan(oldScope.fenceToken);
    expect(runtime.start()).toBe(true);
    const loss = eventScripts.leaseLoss[0];
    runtime.getEventClient().emit({ ...loss, event_id: crypto.randomUUID(), data: { ...loss.data, capture_epoch_id: oldScope.captureEpochId, fence_token: oldScope.fenceToken } });
    expect(runtime.getState()).toMatchObject({ active: true, leaseValid: true, flows: { capture: 'recording' } });
  });
  it('never starts a lease bound to a different classroom and projects scripted upload consistently', () => {
    const runtime = createMockRuntime();
    runtime.chooseScenario('03');
    expect(runtime.getState()).toMatchObject({ flows: { upload: 'durable' }, eventProjection: { chunkDurable: true, uploadPending: false } });
    runtime.stop();
    runtime.switchClassroom(crypto.randomUUID());
    expect(runtime.start()).toBe(false);
    expect(runtime.getState().active).toBe(false);
    expect(runtime.acquireMockLease()).toBe(true);
    expect(runtime.start()).toBe(true);
  });
  it('scene reset disconnects old listeners and resets tombstones, event deduplication and ports', () => {
    const runtime = createMockRuntime();
    const old = runtime.getEventClient();
    runtime.start(); runtime.runEventScript('classroomDeletion');
    runtime.chooseScenario('02');
    expect(old.listenerCount()).toBe(0);
    old.emit(eventScripts.classroomDeletion[0]);
    expect(runtime.start()).toBe(true);
    runtime.runEventScript('classroomDeletion');
    expect(runtime.getState()).toMatchObject({ active: false, leaseValid: false, eventProjection: { classroomDeleted: true } });
  });
  it('isolates tasks by classroom and retains unknown cost risk until that task is reconciled', () => {
    const runtime = createMockRuntime();
    const original = eventScripts.taskUnknown[0];
    const otherClass = crypto.randomUUID();
    const t1 = original.data.task_id;
    const t2 = crypto.randomUUID();
    const emit = (taskId: string, classroomId: string, revision: number, result: 'unknown' | 'succeeded' | 'failed' | 'running', risk: boolean) => runtime.getEventClient().emit({ ...original, event_id: crypto.randomUUID(), data: { ...original.data, task_id: taskId, class_session_id: classroomId, revision, state: result, may_have_charged: risk } });
    emit(t1, original.data.class_session_id, 1, 'unknown', true);
    emit(t2, otherClass, 1, 'succeeded', false);
    expect(runtime.getState()).toMatchObject({ flows: { ai: 'unknown' }, eventProjection: { mayHaveCharged: true } });
    runtime.switchClassroom(otherClass);
    expect(runtime.getState()).toMatchObject({ flows: { ai: 'succeeded' }, eventProjection: { mayHaveCharged: true } });
    emit(t1, original.data.class_session_id, 2, 'running', false);
    expect(runtime.getState()).toMatchObject({ flows: { ai: 'succeeded' }, eventProjection: { mayHaveCharged: true } });
    expect(runtime.getState().tasks[t1].mayHaveCharged).toBe(true);
    emit(t1, original.data.class_session_id, 3, 'failed', false);
    expect(runtime.getState().eventProjection.mayHaveCharged).toBe(false);
    runtime.switchClassroom(original.data.class_session_id);
    emit(t2, otherClass, 2, 'running', false);
    expect(runtime.getState()).toMatchObject({ flows: { ai: 'failed' }, tasks: { [t1]: { state: 'failed' }, [t2]: { state: 'running' } } });
    runtime.switchClassroom(otherClass);
    expect(runtime.getState().flows.ai).toBe('running');
  });
  it('binds scripted audio revocation to the newly acquired chunk', () => {
    const runtime = createMockRuntime();
    runtime.runEventScript('leaseLoss');
    expect(runtime.acquireMockLease()).toBe(true);
    runtime.runEventScript('deletion');
    runtime.runEventScript('upload');
    expect(runtime.getState().eventProjection).toMatchObject({ audioDeleted: true, frameReceived: false, chunkDurable: false });
  });
  it('other classroom and audio tombstones do not change current classroom projection', () => {
    const runtime = createMockRuntime();
    runtime.runEventScript('transcript');
    const other = crypto.randomUUID();
    const deleted = eventScripts.classroomDeletion[0];
    runtime.getEventClient().emit({ ...deleted, event_id: crypto.randomUUID(), data: { ...deleted.data, object_id: other } });
    const audio = eventScripts.deletion[0];
    runtime.getEventClient().emit({ ...audio, event_id: crypto.randomUUID(), data: { ...audio.data, object_id: crypto.randomUUID() } });
    expect(runtime.getState().eventProjection).toMatchObject({ finalText: '合成的正式片段', finalRevision: 2, classroomDeleted: false, audioDeleted: false });
    runtime.switchClassroom(other);
    expect(runtime.getState().eventProjection).toMatchObject({ finalText: null, finalRevision: 0, classroomDeleted: true });
    expect(runtime.acquireMockLease()).toBe(false);
  });
  it('preserves unknown charge risk across scene changes until that task is reconciled', () => {
    const runtime = createMockRuntime();
    runtime.chooseScenario('06');
    const [taskId] = Object.keys(runtime.getState().tasks);
    expect(runtime.getState()).toMatchObject({ flows: { ai: 'unknown' }, eventProjection: { mayHaveCharged: true } });
    runtime.chooseScenario('04');
    expect(runtime.getState()).toMatchObject({ flows: { ai: 'unknown' }, eventProjection: { mayHaveCharged: true } });
    runtime.chooseScenario('06');
    expect(Object.keys(runtime.getState().tasks)).toEqual([taskId]);
    const event = eventScripts.taskUnknown[0];
    runtime.getEventClient().emit({ ...event, event_id: crypto.randomUUID(), data: { ...event.data, task_id: taskId, revision: 2, state: 'running', may_have_charged: false } });
    expect(runtime.getState().eventProjection.mayHaveCharged).toBe(true);
    runtime.getEventClient().emit({ ...event, event_id: crypto.randomUUID(), data: { ...event.data, task_id: taskId, revision: 3, state: 'succeeded', may_have_charged: false } });
    expect(runtime.getState().eventProjection.mayHaveCharged).toBe(false);
    runtime.chooseScenario('06');
    const tasks = runtime.getState().tasks;
    expect(Object.keys(tasks)).toHaveLength(2);
    expect(tasks[taskId].state).toBe('succeeded');
    expect(runtime.getState()).toMatchObject({ flows: { ai: 'unknown' }, eventProjection: { mayHaveCharged: true } });
  });
  it('a final only clears the partial of the same stream and segment', () => {
    const runtime = createMockRuntime();
    const partial = eventScripts.transcript[0];
    const final = eventScripts.transcript[1];
    const segmentB = crypto.randomUUID();
    runtime.getEventClient().emit({ ...partial, data: { ...partial.data, segment_id: segmentB, sequence: 6, text: 'B still partial' } });
    runtime.getEventClient().emit(final);
    expect(runtime.getState().eventProjection).toMatchObject({ partial: 'B still partial', finalText: '合成的正式片段' });
    runtime.getEventClient().emit({ ...final, event_id: crypto.randomUUID(), data: { ...final.data, segment_id: segmentB, sequence: 6, text: 'B final' } });
    expect(runtime.getState().eventProjection).toMatchObject({ partial: null, finalText: '合成的正式片段 / B final' });
  });
});

describe('independent capture and vault lifecycles', () => {
  it('replays stable logical ids across two lease lifecycles without accepting old receipts or losses', () => {
    const runtime = createMockRuntime();
    const observed: ClassroomOSWebV1EventEnvelope[] = [];
    runtime.getEventClient().subscribe(event => observed.push(event));
    expect(runtime.start()).toBe(true);
    const firstScope = runtime.getState().captureScope!;
    runtime.runEventScript('upload');
    const firstReceipt = observed.find(event => event.type === 'audio.chunk_durable');
    expect(firstReceipt?.type).toBe('audio.chunk_durable');
    expect(runtime.getState()).toMatchObject({ flows: { upload: 'durable' }, eventProjection: { chunkDurable: true, uploadPending: false } });
    const delivered = runtime.getState().eventProjection.delivered;
    runtime.getEventClient().emit(firstReceipt!);
    expect(runtime.getState().eventProjection.delivered).toBe(delivered);
    expect(runtime.getState().durableUploads).toHaveLength(1);
    runtime.runEventScript('leaseLoss');
    const firstLoss = observed.find(event => event.type === 'capture.lease_lost')!;
    expect(runtime.getState()).toMatchObject({ active: false, leaseValid: false, flows: { capture: 'failed' } });
    expect(runtime.acquireMockLease()).toBe(true);
    const secondScope = runtime.getState().captureScope!;
    expect(secondScope.captureEpochId).not.toBe(firstScope.captureEpochId);
    expect(secondScope.chunkId).not.toBe(firstScope.chunkId);
    expect(secondScope.fenceToken).toBeGreaterThan(firstScope.fenceToken);
    expect(runtime.start()).toBe(true);
    runtime.getEventClient().emit(firstReceipt!);
    runtime.getEventClient().emit(firstLoss);
    expect(runtime.getState()).toMatchObject({ active: true, leaseValid: true, flows: { capture: 'recording', upload: 'pending' }, eventProjection: { chunkDurable: false, uploadPending: true } });
    runtime.runEventScript('upload');
    const receipts = observed.filter(event => event.type === 'audio.chunk_durable');
    const secondReceipt = receipts.at(-1)!;
    expect(secondReceipt.event_id).not.toBe(firstReceipt!.event_id);
    expect(secondReceipt.data.receipt_id).not.toBe(firstReceipt!.data.receipt_id);
    expect(secondReceipt.data.chunk_id).toBe(secondScope.chunkId);
    expect(secondReceipt.data.capture_epoch_id).toBe(secondScope.captureEpochId);
    expect(runtime.getState()).toMatchObject({ flows: { upload: 'durable' }, eventProjection: { chunkDurable: true, uploadPending: false } });
    expect(runtime.getState().durableUploads).toHaveLength(2);
    const afterReceipt = runtime.getState().eventProjection.delivered;
    runtime.runEventScript('upload');
    const replay = observed.filter(event => event.type === 'audio.chunk_durable').at(-1)!;
    expect(replay.event_id).toBe(secondReceipt.event_id);
    expect(replay.data.receipt_id).toBe(secondReceipt.data.receipt_id);
    // The frame is ephemeral; the repeated durable event itself is discarded.
    expect(runtime.getState().eventProjection.delivered).toBe(afterReceipt + 1);
    expect(runtime.getState().durableUploads).toHaveLength(2);
    runtime.runEventScript('leaseLoss');
    const losses = observed.filter(event => event.type === 'capture.lease_lost');
    expect(losses.at(-1)!.event_id).not.toBe(firstLoss.event_id);
    expect(runtime.getState()).toMatchObject({ active: false, leaseValid: false, flows: { capture: 'failed' } });
  });
  it.each([false, true])('starts a fresh upload lifecycle after stop (first durable=%s)', durable => {
    const runtime = createMockRuntime();
    const observed: ClassroomOSWebV1EventEnvelope[] = [];
    runtime.getEventClient().subscribe(event => observed.push(event));
    expect(runtime.start()).toBe(true);
    const firstScope = runtime.getState().captureScope!;
    expect(runtime.getState()).toMatchObject({ flows: { capture: 'recording', upload: 'pending' }, eventProjection: { chunkDurable: false, uploadPending: true } });
    if (durable) runtime.runEventScript('upload');
    const firstReceipt = observed.find(event => event.type === 'audio.chunk_durable');
    const firstFrame = observed.find(event => event.type === 'capture.frame_received') ?? { ...eventScripts.upload[0], data: { ...eventScripts.upload[0].data, chunk_id: firstScope.chunkId } };
    runtime.stop();
    expect(runtime.start()).toBe(true);
    const secondScope = runtime.getState().captureScope!;
    expect(secondScope.captureEpochId).not.toBe(firstScope.captureEpochId);
    expect(secondScope.chunkId).not.toBe(firstScope.chunkId);
    expect(secondScope.fenceToken).toBeGreaterThan(firstScope.fenceToken);
    runtime.getEventClient().emit(firstFrame);
    if (firstReceipt) runtime.getEventClient().emit(firstReceipt);
    expect(runtime.getState()).toMatchObject({ active: true, flows: { capture: 'recording', upload: 'pending' }, eventProjection: { frameReceived: false, chunkDurable: false, uploadPending: true } });
    expect(runtime.getState().durableUploads).toHaveLength(durable ? 1 : 0);
    runtime.runEventScript('upload');
    expect(runtime.getState()).toMatchObject({ flows: { capture: 'recording', upload: 'durable' }, eventProjection: { frameReceived: true, chunkDurable: true, uploadPending: false } });
    expect(runtime.getState().durableUploads).toHaveLength(durable ? 2 : 1);
    if (durable) expect(runtime.getState().durableUploads[0].chunkId).toBe(firstScope.chunkId);
    runtime.stop();
    expect(runtime.getState().flows.capture).toBe('ended');
  });
  it('uses the real MemoryVault result and locks and clears old account data on switch and revocation', () => {
    const vaults: MemoryVault[] = [];
    const runtime = createMockRuntime({ createVault: () => { const vault = new MemoryVault(); vaults.push(vault); return vault; } });
    const key = 'synthetic-local-draft';
    const oldA = vaults[0];
    expect(runtime.simulateLocalWrite(true)).toBe(true);
    expect(oldA.read(key)).toBe(runtime.getState().mockLocalRecord);
    expect(JSON.parse(oldA.read(key)!).account).toBe('A');
    expect(runtime.getState()).toMatchObject({ localWriteResult: 'stored', flows: { sync: 'local_only' } });
    expect(runtime.simulateLocalWrite(false)).toBe(false);
    expect(runtime.getState()).toMatchObject({ localWriteResult: 'write_failed', flows: { sync: 'failed' } });
    expect(JSON.parse(oldA.read(key)!).account).toBe('A');
    runtime.lockMockVault();
    expect(runtime.simulateLocalWrite(true)).toBe(false);
    expect(runtime.getState()).toMatchObject({ localWriteResult: 'locked', mockLocalRecord: null, flows: { sync: 'failed' } });
    runtime.unlockMockVault();
    runtime.switchAccount();
    const vaultB = vaults.at(-1)!;
    expect(runtime.getState()).toMatchObject({ account: 'B', mockLocalRecord: null, localWriteResult: 'none' });
    expect(vaultB.read(key)).toBeUndefined();
    expect(oldA.read(key)).toBeUndefined(); // locked
    oldA.unlock();
    expect(oldA.read(key)).toBeUndefined(); // actually cleared, not merely hidden by locking
    expect(runtime.simulateLocalWrite(true)).toBe(true);
    expect(JSON.parse(vaultB.read(key)!).account).toBe('B');
    runtime.revokeAccount();
    expect(vaultB.read(key)).toBeUndefined(); // locked
    vaultB.unlock();
    expect(vaultB.read(key)).toBeUndefined(); // cleared on revocation
    expect(runtime.getState()).toMatchObject({ account: null, mockLocalRecord: null, localWriteResult: 'none' });
  });
  it('does not mark a saved local record synced by restarting capture or locking and unlocking', () => {
    const runtime = createMockRuntime();
    expect(runtime.start()).toBe(true);
    expect(runtime.simulateLocalWrite(true)).toBe(true);
    const stored = runtime.getState().mockLocalRecord;
    runtime.stop();
    expect(runtime.start()).toBe(true);
    expect(runtime.getState()).toMatchObject({ localWriteResult: 'stored', mockLocalRecord: stored, flows: { capture: 'recording', sync: 'local_only' } });
    runtime.lockMockVault();
    expect(runtime.getState()).toMatchObject({ localWriteResult: 'locked', mockLocalRecord: null, flows: { sync: 'local_only' } });
    runtime.unlockMockVault();
    expect(runtime.getState()).toMatchObject({ localWriteResult: 'stored', mockLocalRecord: stored, flows: { sync: 'local_only' } });
    expect(runtime.reconcileLocalWrite()).toBe(true);
    runtime.lockMockVault(); runtime.unlockMockVault();
    expect(runtime.getState()).toMatchObject({ localWriteResult: 'reconciled', mockLocalRecord: stored, flows: { sync: 'synced' } });
  });
  it('records scenario durable receipts through the same upload event path', () => {
    const runtime = createMockRuntime();
    runtime.chooseScenario('03');
    const scope = runtime.getState().captureScope!;
    expect(runtime.getState()).toMatchObject({ flows: { upload: 'durable' }, eventProjection: { chunkDurable: true, uploadPending: false } });
    expect(runtime.getState().durableUploads).toHaveLength(1);
    expect(runtime.getState().durableUploads[0]).toMatchObject({ chunkId: scope.chunkId, captureEpochId: scope.captureEpochId });
    runtime.stop(); expect(runtime.start()).toBe(true);
    expect(runtime.getState()).toMatchObject({ flows: { upload: 'pending' }, eventProjection: { chunkDurable: false, uploadPending: true } });
    expect(runtime.getState().durableUploads[0].chunkId).toBe(scope.chunkId);
  });
});

describe('pending history and vault unlock idempotency', () => {
  it('keeps an unverified actual vault record and sync projection through repeated unlocks, scenarios and capture', () => {
    const vaults: MemoryVault[] = [];
    const runtime = createMockRuntime({ createVault: () => { const vault = new MemoryVault(); vaults.push(vault); return vault; } });
    const key = 'synthetic-local-draft';
    const emptyState = runtime.getState();
    runtime.unlockMockVault();
    expect(runtime.getState()).toBe(emptyState);
    runtime.chooseScenario('05');
    expect(runtime.simulateLocalWrite(true)).toBe(true);
    const saved = vaults[0].read(key)!;
    runtime.unlockMockVault(); runtime.unlockMockVault();
    expect(vaults[0].read(key)).toBe(saved);
    expect(runtime.getState()).toMatchObject({ mockLocalRecord: saved, localWriteResult: 'stored', localRecordSync: 'pending', flows: { sync: 'local_only' } });
    runtime.chooseScenario('02');
    expect(runtime.start()).toBe(true);
    expect(vaults[0].read(key)).toBe(saved);
    expect(runtime.getState()).toMatchObject({ mockLocalRecord: saved, localRecordSync: 'pending', flows: { sync: 'local_only' } });
    runtime.lockMockVault(); runtime.lockMockVault(); runtime.unlockMockVault(); runtime.unlockMockVault();
    expect(vaults[0].read(key)).toBe(saved);
    expect(runtime.getState()).toMatchObject({ mockLocalRecord: saved, localRecordSync: 'pending', flows: { sync: 'local_only' } });
    expect(runtime.reconcileLocalWrite()).toBe(true);
    runtime.unlockMockVault(); runtime.chooseScenario('02');
    expect(runtime.getState()).toMatchObject({ localRecordSync: 'reconciled', flows: { sync: 'synced' } });
    runtime.switchAccount();
    expect(runtime.getState()).toMatchObject({ account: 'B', mockLocalRecord: null, localRecordSync: 'none' });
    expect(vaults[0].read(key)).toBeUndefined();
    vaults[0].unlock(); expect(vaults[0].read(key)).toBeUndefined();
  });
  it('never marks the old record synced when an attempted write is rejected by a locked vault', () => {
    const runtime = createMockRuntime();
    runtime.chooseScenario('05'); expect(runtime.simulateLocalWrite(true)).toBe(true);
    const saved = runtime.getState().mockLocalRecord;
    runtime.lockMockVault(); expect(runtime.simulateLocalWrite(true)).toBe(false);
    runtime.unlockMockVault(); runtime.unlockMockVault();
    expect(runtime.getState()).toMatchObject({ mockLocalRecord: saved, localRecordSync: 'pending' });
    runtime.chooseScenario('02'); runtime.start();
    expect(runtime.getState().flows.sync).not.toBe('synced');
    expect(runtime.getState().mockLocalRecord).toBe(saved);
  });
  it('accepts interleaved old durable receipts in three independent epochs without altering the active chunk', () => {
    const runtime = createMockRuntime();
    expect(runtime.getState().uploadHistory).toEqual([expect.objectContaining({ chunkId: runtime.getState().captureScope!.chunkId, status: 'pending', seq: 1, canAccept: true })]);
    const scopes = [];
    for (let round = 0; round < 3; round++) {
      expect(runtime.start()).toBe(true);
      scopes.push(runtime.getState().captureScope!);
      runtime.runEventStep('upload', 0);
      expect(runtime.getState().uploadHistory.find(item => item.chunkId === scopes[round].chunkId)).toMatchObject({ seq: 1, frameReceived: true, status: 'pending' });
      if (round < 2) runtime.stop();
    }
    expect(new Set(scopes.map(scope => scope.captureEpochId)).size).toBe(3);
    expect(new Set(scopes.map(scope => scope.chunkId)).size).toBe(3);
    const receipt = (index: number) => ({ ...eventScripts.upload[1], event_id: crypto.randomUUID(), data: { ...eventScripts.upload[1].data, class_session_id: scopes[index].classSessionId, capture_epoch_id: scopes[index].captureEpochId, chunk_id: scopes[index].chunkId, receipt_id: crypto.randomUUID() } });
    const first = receipt(0), second = receipt(1), third = receipt(2);
    const client = runtime.getEventClient();
    client.emit({ ...first, event_id: crypto.randomUUID(), data: { ...first.data, capture_epoch_id: crypto.randomUUID() } });
    client.emit({ ...first, event_id: crypto.randomUUID(), data: { ...first.data, seq: 2 } });
    expect(runtime.getState().durableUploads).toHaveLength(0);
    client.emit(first); client.emit(first);
    expect(runtime.getState().uploadHistory.find(item => item.chunkId === scopes[0].chunkId)).toMatchObject({ status: 'durable', receiptId: first.data.receipt_id, eventId: first.event_id });
    expect(runtime.getState()).toMatchObject({ flows: { upload: 'pending' }, eventProjection: { chunkDurable: false, uploadPending: true } });
    expect(runtime.getState().durableUploads).toHaveLength(1);
    client.emit(second); client.emit(second);
    expect(runtime.getState().uploadHistory.find(item => item.chunkId === scopes[1].chunkId)?.status).toBe('durable');
    expect(runtime.getState().uploadHistory.find(item => item.chunkId === scopes[2].chunkId)?.status).toBe('pending');
    expect(runtime.getState().durableUploads).toHaveLength(2);
    client.emit(third); client.emit(third);
    expect(runtime.getState()).toMatchObject({ flows: { upload: 'durable' }, eventProjection: { chunkDurable: true, uploadPending: false } });
    expect(runtime.getState().durableUploads).toHaveLength(3);
    expect(runtime.getState().uploadHistory.filter(item => item.status === 'durable')).toHaveLength(3);
  });
  it('retains pending history across scenario changes but rejects old receipts after revocation or account change', () => {
    const runtime = createMockRuntime();
    runtime.start();
    const first = runtime.getState().captureScope!;
    runtime.runEventStep('upload', 0);
    runtime.stop(); runtime.chooseScenario('02');
    expect(runtime.getState().uploadHistory.find(item => item.chunkId === first.chunkId)).toMatchObject({ status: 'pending', frameReceived: true });
    let received: ClassroomOSWebV1EventEnvelope | undefined;
    runtime.getEventClient().subscribe(event => { if (event.type === 'audio.chunk_durable') received = event; });
    expect(runtime.runHistoricalDurable(first.chunkId)).toBe(true);
    expect(runtime.getState().uploadHistory.find(item => item.chunkId === first.chunkId)?.status).toBe('durable');
    expect(runtime.getState().flows.upload).toBe('pending');
    expect(runtime.runHistoricalDurable(first.chunkId)).toBe(false);
    expect(received?.type).toBe('audio.chunk_durable');
    runtime.chooseScenario('02');
    const beforeReplay = runtime.getState().eventProjection.delivered;
    runtime.getEventClient().emit(received!);
    expect(runtime.getState().eventProjection.delivered).toBe(beforeReplay);
    expect(runtime.getState().durableUploads).toHaveLength(1);
    runtime.start(); const second = runtime.getState().captureScope!; runtime.stop(); runtime.start();
    const deletion = eventScripts.deletion[0];
    runtime.getEventClient().emit({ ...deletion, event_id: crypto.randomUUID(), data: { ...deletion.data, object_id: second.chunkId } });
    expect(runtime.runHistoricalDurable(second.chunkId)).toBe(false);
    expect(runtime.getState().uploadHistory.find(item => item.chunkId === second.chunkId)).toMatchObject({ status: 'pending', canAccept: false });
    runtime.chooseScenario('02');
    expect(runtime.runHistoricalDurable(second.chunkId)).toBe(false);
    runtime.switchAccount();
    expect(runtime.getState().uploadHistory.some(item => item.chunkId === first.chunkId || item.chunkId === second.chunkId)).toBe(false);
    expect(runtime.runHistoricalDurable(second.chunkId)).toBe(false);
    const receipt = eventScripts.upload[1];
    runtime.getEventClient().emit({ ...receipt, event_id: crypto.randomUUID(), data: { ...receipt.data, class_session_id: second.classSessionId, capture_epoch_id: second.captureEpochId, chunk_id: second.chunkId } });
    expect(runtime.getState().durableUploads).toHaveLength(0);
    runtime.revokeAccount();
    expect(runtime.getState().uploadHistory).toEqual([]);
  });
  it('refuses late receipts for lease-lost or classroom-revoked scopes, including after scene reset', () => {
    const runtime = createMockRuntime();
    runtime.start(); const lost = runtime.getState().captureScope!;
    runtime.runEventScript('leaseLoss');
    expect(runtime.getState().uploadHistory.find(item => item.chunkId === lost.chunkId)?.canAccept).toBe(false);
    expect(runtime.acquireMockLease()).toBe(true); runtime.start();
    expect(runtime.runHistoricalDurable(lost.chunkId)).toBe(false);
    const revoked = runtime.getState().captureScope!;
    runtime.runEventScript('classroomDeletion');
    expect(runtime.getState().uploadHistory.find(item => item.chunkId === revoked.chunkId)?.canAccept).toBe(false);
    runtime.chooseScenario('02');
    expect(runtime.runHistoricalDurable(revoked.chunkId)).toBe(false);
    const event = eventScripts.upload[1];
    runtime.getEventClient().emit({ ...event, event_id: crypto.randomUUID(), data: { ...event.data, class_session_id: revoked.classSessionId, capture_epoch_id: revoked.captureEpochId, chunk_id: revoked.chunkId } });
    expect(runtime.getState().durableUploads).toHaveLength(0);
  });
});

describe('fifth review: attempt and saved-record identity matrix', () => {
  function setup(oldSynced: boolean) {
    const vaults: MemoryVault[] = [];
    const runtime = createMockRuntime({ createVault: () => { const vault = new MemoryVault(4096); vaults.push(vault); return vault; } });
    runtime.chooseScenario('05');
    expect(runtime.simulateLocalWrite(true)).toBe(true);
    const old = runtime.getState().localRecordHistory[0];
    const oldData = vaults[0].read(old.vaultKey)!;
    expect(JSON.parse(oldData)).toMatchObject({ recordId: old.recordId, captureEpochId: old.captureEpochId, account: 'A' });
    if (oldSynced) expect(runtime.reconcileLocalWrite()).toBe(true);
    runtime.stop(); expect(runtime.start()).toBe(true);
    expect(runtime.getState().captureScope!.captureEpochId).not.toBe(old.captureEpochId);
    runtime.lockMockVault(); expect(runtime.simulateLocalWrite(true)).toBe(false);
    const attempt = runtime.getState().localWriteAttempt!;
    expect(attempt).toMatchObject({ result: 'locked', recordId: null, captureEpochId: runtime.getState().captureScope!.captureEpochId });
    expect(runtime.getState().flows.sync).toBe('failed');
    runtime.unlockMockVault();
    return { runtime, vaults, old, oldData, attempt };
  }
  it.each([['A', false], ['B', true]] as const)('%s: old pending or reconciled record survives new failure, unlock and scenario change', (_case, oldSynced) => {
    const { runtime, vaults, old, oldData, attempt } = setup(oldSynced);
    runtime.chooseScenario('02');
    expect(vaults[0].read(old.vaultKey)).toBe(oldData);
    expect(vaults[0].read('synthetic-local-draft')).toBe(oldData);
    expect(runtime.getState()).toMatchObject({ mockLocalRecord: oldData, localWriteAttempt: attempt, localRecordSync: oldSynced ? 'reconciled' : 'pending', localRecordHistory: [{ ...old, sync: oldSynced ? 'reconciled' : 'pending' }], flows: { sync: 'failed' } });
  });
  it('C: repeated unlock cannot clear the latest failed attempt over a reconciled record', () => {
    const { runtime, vaults, old, oldData, attempt } = setup(true);
    const snapshot = runtime.getState();
    runtime.unlockMockVault(); runtime.unlockMockVault();
    expect(runtime.getState()).toBe(snapshot);
    expect(runtime.getState()).toMatchObject({ vaultLocked: false, localWriteResult: 'locked', localWriteAttempt: attempt, flows: { sync: 'failed' } });
    expect(vaults[0].read(old.vaultKey)).toBe(oldData);
  });
  it('D: starting another capture does not replace failed-attempt identity or mark old record synced as current', () => {
    const { runtime, vaults, old, oldData, attempt } = setup(true);
    runtime.chooseScenario('02'); expect(runtime.start()).toBe(true);
    expect(runtime.getState().captureScope!.captureEpochId).not.toBe(attempt.captureEpochId);
    expect(runtime.getState()).toMatchObject({ localWriteAttempt: attempt, localRecordSync: 'reconciled', flows: { capture: 'recording', sync: 'failed' } });
    expect(vaults[0].read(old.vaultKey)).toBe(oldData);
  });
  it('E and F: a successful new write becomes pending until that exact record is reconciled', () => {
    const { runtime, vaults, old, oldData, attempt } = setup(true);
    expect(runtime.simulateLocalWrite(true)).toBe(true);
    const current = runtime.getState().localRecordHistory.at(-1)!;
    const currentAttempt = runtime.getState().localWriteAttempt!;
    expect(current.recordId).not.toBe(old.recordId);
    expect(currentAttempt.attemptId).not.toBe(attempt.attemptId);
    expect(currentAttempt).toMatchObject({ result: 'stored', recordId: current.recordId, captureEpochId: attempt.captureEpochId });
    expect(JSON.parse(vaults[0].read(current.vaultKey)!)).toMatchObject({ recordId: current.recordId, captureEpochId: attempt.captureEpochId });
    expect(vaults[0].read(old.vaultKey)).toBe(oldData);
    expect(runtime.getState()).toMatchObject({ localRecordSync: 'pending', flows: { sync: 'local_only' } });
    expect(runtime.reconcileLocalWrite(current.recordId)).toBe(true);
    expect(runtime.getState()).toMatchObject({ localRecordSync: 'reconciled', localRecordHistory: [{ ...old, sync: 'reconciled' }, { ...current, sync: 'reconciled' }], flows: { sync: 'synced' } });
    expect(vaults[0].read(old.vaultKey)).toBe(oldData);
  });
  it('G: a duplicate or late receipt for the old record cannot reconcile a newer pending record', () => {
    const { runtime, vaults, old, oldData } = setup(true);
    expect(runtime.simulateLocalWrite(true)).toBe(true);
    const current = runtime.getState().localRecordHistory.at(-1)!;
    const before = runtime.getState();
    expect(runtime.reconcileLocalWrite(old.recordId)).toBe(true);
    expect(runtime.reconcileLocalWrite(old.recordId)).toBe(true);
    expect(runtime.getState()).toBe(before);
    expect(runtime.getState()).toMatchObject({ localWriteAttempt: { recordId: current.recordId }, localRecordSync: 'pending', flows: { sync: 'local_only' } });
    expect(vaults[0].read(old.vaultKey)).toBe(oldData);
    expect(JSON.parse(vaults[0].read(current.vaultKey)!).recordId).toBe(current.recordId);
  });
  it('a genuinely late receipt for an older pending record cannot mask the latest failed attempt', () => {
    const { runtime, vaults, old, oldData, attempt } = setup(false);
    expect(runtime.reconcileLocalWrite(old.recordId)).toBe(true);
    expect(runtime.getState()).toMatchObject({ localWriteAttempt: attempt, localRecordSync: 'reconciled', localRecordHistory: [{ ...old, sync: 'reconciled' }], flows: { sync: 'failed' } });
    const afterReceipt = runtime.getState();
    expect(runtime.reconcileLocalWrite(old.recordId)).toBe(true);
    expect(runtime.getState()).toBe(afterReceipt);
    expect(vaults[0].read(old.vaultKey)).toBe(oldData);
  });
  it.each(['switch', 'revoke'] as const)('H: %s locks and clears all old-account versions and failed attempts', action => {
    const { runtime, vaults, old, attempt } = setup(true);
    expect(runtime.getState().localWriteAttempt).toEqual(attempt);
    expect(runtime.simulateLocalWrite(true)).toBe(true);
    const second = runtime.getState().localRecordHistory.at(-1)!;
    expect(second.recordId).not.toBe(old.recordId);
    expect(vaults[0].read(old.vaultKey)).toBeTruthy();
    expect(vaults[0].read(second.vaultKey)).toBeTruthy();
    runtime.lockMockVault(); expect(runtime.simulateLocalWrite(true)).toBe(false);
    expect(runtime.getState().localRecordHistory).toHaveLength(2);
    if (action === 'switch') runtime.switchAccount(); else runtime.revokeAccount();
    expect(runtime.getState()).toMatchObject({ account: action === 'switch' ? 'B' : null, localWriteAttempt: null, localRecordHistory: [], localRecordSync: 'none', mockLocalRecord: null });
    expect(vaults[0].read(old.vaultKey)).toBeUndefined();
    vaults[0].unlock();
    expect(vaults[0].read(old.vaultKey)).toBeUndefined();
    expect(vaults[0].read(second.vaultKey)).toBeUndefined();
    expect(vaults[0].read('synthetic-local-draft')).toBeUndefined();
    expect(runtime.reconcileLocalWrite(old.recordId)).toBe(false);
  });
  it('keeps version and current alias atomic when quota or write failure rejects a new version', () => {
    const vault = new MemoryVault(48); vault.unlock();
    expect(vault.write('v1', 'old', false, ['current'])).toBe('stored');
    expect(vault.write('v2', 'x'.repeat(40), false, ['current'])).toBe('quota');
    expect(vault.read('v1')).toBe('old'); expect(vault.read('current')).toBe('old'); expect(vault.read('v2')).toBeUndefined();
    expect(vault.write('v2', 'new', true, ['current'])).toBe('write_failed');
    expect(vault.read('current')).toBe('old'); expect(vault.read('v2')).toBeUndefined();
    vault.lock(); expect(vault.write('v2', 'new', false, ['current'])).toBe('locked');
    vault.unlock(); expect(vault.read('v1')).toBe('old'); expect(vault.read('current')).toBe('old'); expect(vault.read('v2')).toBeUndefined();
  });
});

describe('deterministic in-memory ports', () => {
  it('only advances simulated time during recording and never invokes a microphone', () => {
    const capture = new MockCapturePort();
    expect(capture.command('recording')).toBe(true);
    capture.advance(1000);
    expect(capture.command('paused')).toBe(true);
    capture.advance(2000);
    expect(capture.durationMs).toBe(1000);
    expect(capture.command('ended')).toBe(true);
    expect(capture.command('recording')).toBe(false);
  });
  it('keeps a chunk after frame acknowledgement until durable confirmation', () => {
    const upload = new MockUploadPort(); upload.stage(1); upload.frameReceived(1);
    expect(upload.pending(1)).toBe(true);
    upload.durable(1); expect(upload.pending(1)).toBe(false);
  });
  it('does not replace saved data after quota or write failure and respects locking', () => {
    const vault = new MemoryVault(20);
    expect(vault.write('a', 'old')).toBe('locked'); vault.unlock();
    expect(vault.write('a', 'old')).toBe('stored');
    expect(vault.write('a', 'new', true)).toBe('write_failed');
    expect(vault.write('a', 'a value that exceeds the limit')).toBe('quota');
    expect(vault.read('a')).toBe('old'); vault.lock(); expect(vault.read('a')).toBeUndefined();
  });
  it('delivers duplicate events in scripted order and unsubscribes cleanly', () => {
    const client = new MockEventClient(); const seen: string[] = [];
    const stop = client.subscribe(event => seen.push(event.type));
    const examples = JSON.parse(readFileSync(resolve(process.cwd(), '../../docs/web-alpha/architecture/contracts/examples.json'), 'utf8'));
    const event = examples.cases.find((item: {name: string}) => item.name === 'durable_final_event').value as ClassroomOSWebV1EventEnvelope;
    client.emit(event); client.emit(event); stop(); client.emit(event);
    expect(seen).toEqual(['transcript.final', 'transcript.final']);
  });
});
