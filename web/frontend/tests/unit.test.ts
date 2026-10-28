import { describe, expect, it } from 'vitest';
import { createMockRuntime } from '../src/mock/runtime';
import { MemoryVault, MockCapturePort, MockEventClient, MockUploadPort } from '../src/mock/ports';
import type { ClassroomOSWebV1EventEnvelope } from '../src/generated/events';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('runtime used by the preview shell', () => {
  it('keeps upload failure independent of other flows', () => {
    const runtime = createMockRuntime();
    runtime.chooseScenario('04');
    expect(runtime.getState().flows).toMatchObject({ capture: 'recording', upload: 'failed', asr: 'streaming' });
    runtime.setFlow('upload', 'durable');
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
