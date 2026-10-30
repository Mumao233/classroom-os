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
