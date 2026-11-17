import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const output = process.env.PR08A_OUTPUT;
const { chromium } = await import(pathToFileURL(process.env.PR08A_PLAYWRIGHT_MODULE).href);
const bundle = await readFile(join(output, 'cache.js'));
const server = createServer((request, response) => {
  response.setHeader('Cache-Control', 'no-store');
  response.setHeader('Content-Type', request.url === '/cache.js' ? 'text/javascript' : 'text/html');
  response.end(request.url === '/cache.js' ? bundle : '<!doctype html><title>PR05b PR08a synthetic integration</title>');
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const context = await chromium.launchPersistentContext(join(output, 'combined-profile'), { headless: true });
const results = [];
const page = context.pages()[0];

async function initialize(seed) {
  await page.evaluate(async supplied => {
    window.lib = await import('/cache.js');
    window.require = (condition, message) => { if (!condition) throw new Error(message); };
    window.expectCode = async (operation, code) => {
      try { await operation; throw new Error('Expected ' + code); }
      catch (error) { if (error.code !== code) throw error; }
    };
    window.setup = async (name, prior) => {
      const identity = { ownerId: 'combined-owner', deviceId: 'combined-device', sessionId: 'combined-session' };
      const ancestry = { semesterId: 'semester-' + name, courseId: 'course-' + name, classSessionId: 'class-' + name };
      const options = { enabled: true, recordingEnabled: true, databaseName: 'combined-' + name,
        limits: { draftBytes: 1_000_000, draftOperations: 100, recordingBytes: 2_000_000 } };
      const grant = prior?.grant ?? { key_id: 'key-' + name, key_epoch: 1, scope: 'recording_recovery',
        key_material_b64: btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32)))),
        expires_at: new Date(Date.now() + 120_000).toISOString(), cache_policy_revision: 'synthetic-only' };
      const lease = { class_session_id: ancestry.classSessionId, capture_epoch_id: 'epoch-' + name,
        fence_token: 1, expires_at: new Date(Date.now() + 120_000).toISOString() };
      const cache = new lib.TrustedCache(options);
      cache.bind(identity);
      await cache.unlock('recording_recovery', ancestry.classSessionId, async () => structuredClone(grant));
      const control = { uploadCount: 0, commandCount: 0, stopKeys: [], lastRecoveryToken: null,
        pendingAck: false, offline: false };
      const transport = {
        acquire: async () => structuredClone(lease),
        command: async () => { control.commandCount++; if (control.offline) throw new Error('synthetic offline'); return structuredClone(lease); },
        stop: async (classroom, body, key) => {
          control.stopKeys.push(key);
          if (control.offline) throw new Error('synthetic offline');
          return { class_session_id: classroom, capture_epoch_id: body.capture_epoch_id, state: 'stopped' };
        },
        recover: async (classroom, body) => ({ recovery_upload_token: 'synthetic-recovery-capability', asr_authorized: false,
          manifest: { class_session_id: classroom, capture_epoch_id: body.capture_epoch_id } }),
        upload: async (chunk, bytes, currentLease, recoveryToken) => {
          control.uploadCount++;
          if (control.offline) throw new Error('synthetic offline');
          const persisted = await cache.readRecording(chunk.descriptor);
          require(String(persisted) === String(bytes), 'Upload preceded encrypted chunk persistence');
          const state = JSON.parse(new TextDecoder().decode(await cache.readCaptureState(ancestry.classSessionId, lease.capture_epoch_id)));
          require(state.pending.some(item => item.chunkId === chunk.chunkId), 'Upload preceded opaque pending state');
          require(currentLease.capture_epoch_id === chunk.descriptor.capture_epoch_id, 'Lease scope changed');
          control.lastRecoveryToken = recoveryToken ?? null;
          return { id: chunk.chunkId, descriptor: structuredClone(chunk.descriptor),
            state: control.pendingAck ? 'pending' : 'durable', durable_at: control.pendingAck ? null : new Date().toISOString(),
            receipt_id: 'receipt-' + chunk.chunkId, asr_submission: 'not_submitted' };
        },
      };
      const runtime = new lib.CaptureRuntime(transport, cache, ancestry, 64000);
      window.fixture = { name, grant, ancestry, options, lease, cache, control, transport, runtime };
      return fixture;
    };
    if (supplied) await setup(supplied.name, supplied);
  }, seed);
}

async function test(name, action) {
  const started = Date.now();
  try { await action(); results.push({ name, passed: true, elapsed_ms: Date.now() - started }); }
  catch (error) { results.push({ name, passed: false, error: error.message }); }
  finally { await page.evaluate(() => window.fixture?.cache.close()); }
}

try {
  await page.goto(origin);
  await initialize();
  await test('persist-before-upload-and-durable-only-removal', async () => {
    await page.evaluate(async () => {
      const f = await setup('durable');
      await f.runtime.prepare(f.lease); await f.runtime.start();
      await f.runtime.append(new Uint8Array([1, 0, 2, 0]));
      const chunk = f.runtime.snapshot().pending[0];
      require(f.control.uploadCount === 0, 'append uploaded without explicit request');
      f.control.pendingAck = true;
      await expectCode(f.runtime.uploadPending(), 'INVALID_DURABLE_ACK');
      require(await f.cache.readRecording(chunk.descriptor), 'Non-durable receipt removed unique bytes');
      f.control.pendingAck = false;
      await f.runtime.uploadPending();
      require(await f.cache.readRecording(chunk.descriptor) === null, 'Durable bytes retained');
      require(f.runtime.snapshot().pending.length === 0 && f.runtime.snapshot().cleanup.length === 0, 'Durable cursor not persisted');
    });
  });
  await test('opaque-state-reload-needs-explicit-recovery-authorization', async () => {
    const seed = await page.evaluate(async () => {
      const f = await setup('reload'); await f.runtime.prepare(f.lease); await f.runtime.start();
      await f.runtime.append(new Uint8Array([3, 0, 4, 0]));
      f.cache.close(); return { name: f.name, grant: f.grant };
    });
    await page.reload(); await initialize(seed);
    await page.evaluate(async () => {
      const f = fixture;
      require(await f.runtime.restore(f.ancestry.classSessionId, f.lease.capture_epoch_id), 'Opaque state was not restored');
      require(f.runtime.snapshot().phase === 'recovery', 'Restore auto-started capture');
      require(f.control.uploadCount === 0, 'Restore auto-uploaded');
      await expectCode(f.runtime.uploadPending(), 'RECOVERY_AUTHORIZATION_REQUIRED');
      await f.runtime.authorizeRecovery(); await f.runtime.uploadPending();
      require(f.control.lastRecoveryToken === 'synthetic-recovery-capability', 'Recovery upload omitted authorization');
      require(f.runtime.snapshot().pending.length === 0, 'Restored chunk did not drain');
    });
  });
  await test('offline-pause-keeps-encrypted-pending-and-local-boundary', async () => {
    await page.evaluate(async () => {
      const f = await setup('pause'); await f.runtime.prepare(f.lease); await f.runtime.start();
      await f.runtime.append(new Uint8Array([5, 0, 6, 0])); f.control.offline = true;
      await f.runtime.pause();
      const state = f.runtime.snapshot();
      require(state.phase === 'paused' && state.command.action === 'pause' && state.pending.length === 1, 'Offline pause lost intent or pending');
      require(f.control.commandCount === 0, 'Pause contacted server before pending drain');
      require(await f.cache.readRecording(state.pending[0].descriptor), 'Pause removed pending bytes');
      await expectCode(f.runtime.append(new Uint8Array([7, 0])), 'CAPTURE_NOT_RECORDING');
      f.control.offline = false; await f.runtime.uploadPending(); await f.runtime.pause();
      require(f.control.commandCount === 1 && !f.runtime.snapshot().command, 'Pause intent did not drain');
    });
  });
  await test('offline-stop-refresh-replays-intent-before-authorized-upload', async () => {
    const seed = await page.evaluate(async () => {
      const f = await setup('stop'); await f.runtime.prepare(f.lease); await f.runtime.start();
      await f.runtime.append(new Uint8Array([7, 0, 8, 0])); f.control.offline = true;
      let failure;
      try { await f.runtime.stop(); } catch (error) { failure = error; }
      require(failure?.message === 'synthetic offline', 'Stop did not reach the synthetic offline boundary');
      const state = f.runtime.snapshot();
      require(state.phase === 'stopped' && state.command.action === 'stop' && state.pending.length === 1, 'Offline stop did not persist boundary');
      require(await f.cache.readRecording(state.pending[0].descriptor), 'Stop removed unique pending bytes');
      await expectCode(f.runtime.append(new Uint8Array([9, 0])), 'CAPTURE_NOT_RECORDING');
      f.cache.close(); return { name: f.name, grant: f.grant, stopKey: state.command.key };
    });
    await page.reload(); await initialize(seed);
    await page.evaluate(async expected => {
      const f = fixture; require(await f.runtime.restore(f.ancestry.classSessionId, f.lease.capture_epoch_id), 'Stopped state missing');
      await expectCode(f.runtime.uploadPending(), 'RECOVERY_AUTHORIZATION_REQUIRED');
      await f.runtime.authorizeRecovery();
      require(f.control.stopKeys[0] === expected.stopKey, 'Lost stop response retried with different idempotency key');
      await f.runtime.uploadPending(); require(f.runtime.snapshot().pending.length === 0, 'Stopped pending did not recover');
    }, seed);
  });
  await test('persisted-durable-cleanup-survives-cache-removal-failure', async () => {
    await page.evaluate(async () => {
      const f = await setup('cleanup'); await f.runtime.prepare(f.lease); await f.runtime.start();
      await f.runtime.append(new Uint8Array([9, 0, 10, 0])); const chunk = f.runtime.snapshot().pending[0];
      const acknowledge = f.cache.acknowledgeRecording.bind(f.cache);
      f.cache.acknowledgeRecording = async () => { throw new Error('synthetic cleanup interruption'); };
      try { await f.runtime.uploadPending(); throw new Error('Expected interruption'); }
      catch (error) { require(error.message === 'synthetic cleanup interruption', error.message); }
      require(f.runtime.snapshot().pending.length === 0 && f.runtime.snapshot().cleanup.length === 1, 'Durable cleanup cursor lost');
      require(await f.cache.readRecording(chunk.descriptor), 'Interrupted cleanup already discarded bytes');
      f.cache.acknowledgeRecording = acknowledge;
      const restored = new lib.CaptureRuntime(f.transport, f.cache, f.ancestry, 64000);
      await restored.restore(f.ancestry.classSessionId, f.lease.capture_epoch_id);
      await restored.authorizeRecovery(); await restored.uploadPending();
      require(f.control.uploadCount === 1, 'Durable chunk was uploaded again');
      require(await f.cache.readRecording(chunk.descriptor) === null && restored.snapshot().cleanup.length === 0, 'Persisted cleanup did not complete');
    });
  });
  await test('expired-lease-after-offline-stop-can-request-frozen-range-recovery', async () => {
    const seed = await page.evaluate(async () => {
      const f = await setup('expired'); f.lease.expires_at = new Date(Date.now() + 250).toISOString();
      await f.runtime.prepare(f.lease); await f.runtime.start(); await f.runtime.append(new Uint8Array([15, 0]));
      f.control.offline = true;
      try { await f.runtime.stop(); } catch (error) { require(error.message === 'synthetic offline', error.message); }
      const state = f.runtime.snapshot();
      await new Promise(resolve => setTimeout(resolve, 300));
      f.cache.close(); return { name: f.name, grant: f.grant, intent: state.command };
    });
    await page.reload(); await initialize(seed);
    await page.evaluate(async expected => {
      const f = fixture; await f.runtime.restore(f.ancestry.classSessionId, f.lease.capture_epoch_id);
      require(Date.parse(f.runtime.snapshot().lease.expires_at) < Date.now(), 'Restored lease has not expired');
      let recoveryBody;
      f.transport.stop = async () => { throw new lib.CaptureError('STALE_FENCE'); };
      const recover = f.transport.recover;
      f.transport.recover = async (classroom, body) => { recoveryBody = structuredClone(body); return recover(classroom, body); };
      await f.runtime.authorizeRecovery();
      require(f.runtime.snapshot().unresolvedCommands.some(command => command.key === expected.intent.key), 'Expired stop identity lost');
      require(recoveryBody.known_last_sample === expected.intent.body.last_sample
        && recoveryBody.manifest_sha256 === expected.intent.body.manifest_sha256, 'Expired recovery changed the frozen boundary');
      await f.runtime.uploadPending(); require(f.runtime.snapshot().pending.length === 0, 'Expired stopped chunk cannot recover');
    }, seed);
  });
  await test('unknown-stop-result-preserves-intent-and-blocks-recovery-transition', async () => {
    await page.evaluate(async () => {
      const f = await setup('unknown-stop'); await f.runtime.prepare(f.lease); await f.runtime.start();
      await f.runtime.append(new Uint8Array([16, 0]));
      f.transport.stop = async () => { throw new lib.CaptureError('RESULT_UNKNOWN'); };
      await expectCode(f.runtime.stop(), 'RESULT_UNKNOWN'); const key = f.runtime.snapshot().command.key;
      let recoveryCalls = 0; f.transport.recover = async () => { recoveryCalls++; throw new Error('Unexpected recovery'); };
      await expectCode(f.runtime.authorizeRecovery(), 'RESULT_UNKNOWN');
      require(f.runtime.snapshot().command.key === key && recoveryCalls === 0, 'Unknown stop silently transitioned to recovery');
    });
  });
  await test('unknown-renew-cannot-prevent-local-pause-or-stop', async () => {
    await page.evaluate(async () => {
      for (const action of ['pause', 'stop']) {
        const f = await setup('unknown-renew-' + action); await f.runtime.prepare(f.lease); await f.runtime.start();
        await f.runtime.append(new Uint8Array([17, 0]));
        f.transport.command = async () => { throw new lib.CaptureError('RESULT_UNKNOWN'); };
        await expectCode(f.runtime.renew(), 'RESULT_UNKNOWN'); const unknown = f.runtime.snapshot().command;
        f.control.offline = true;
        if (action === 'pause') await f.runtime.pause();
        else { try { await f.runtime.stop(); } catch (error) { require(error.message === 'synthetic offline', error.message); } }
        const state = f.runtime.snapshot();
        require(state.phase === (action === 'pause' ? 'paused' : 'stopped'), 'Unknown renewal prevented local halt');
        require(state.command.action === action && state.unresolvedCommands.some(command => command.key === unknown.key
          && JSON.stringify(command.body) === JSON.stringify(unknown.body)), 'Unknown renewal key or final intent lost');
        await expectCode(f.runtime.append(new Uint8Array([18, 0])), 'CAPTURE_NOT_RECORDING');
        require(await f.cache.readRecording(state.pending[0].descriptor), 'Safety action lost encrypted pending'); f.cache.close();
      }
    });
  });
  await test('old-fence-and-generation-cannot-overwrite-authoritative-local-cursor', async () => {
    await page.evaluate(async () => {
      const f = await setup('fence'); await f.runtime.prepare(f.lease); await f.runtime.start();
      const previous = f.runtime.snapshot();
      await expectCode(f.cache.saveCaptureState(f.ancestry.classSessionId, f.lease.capture_epoch_id,
        new Uint8Array([1]), f.ancestry, 1, previous.generation - 1), 'REVISION_CONFLICT');
      const future = { ...previous, lease: { ...previous.lease, fence_token: 2 }, generation: 1 };
      await f.cache.saveCaptureState(f.ancestry.classSessionId, f.lease.capture_epoch_id,
        new TextEncoder().encode(JSON.stringify(future)), f.ancestry, 2, 1);
      await expectCode(f.runtime.append(new Uint8Array([11, 0])), 'REVISION_CONFLICT');
      require(f.control.uploadCount === 0, 'Old fence reached upload');
      const state = JSON.parse(new TextDecoder().decode(await f.cache.readCaptureState(f.ancestry.classSessionId, f.lease.capture_epoch_id)));
      require(state.lease.fence_token === 2, 'Old runtime overwrote new fence');
    });
  });
  await test('caller-lease-and-ancestry-mutation-cannot-change-cache-binding', async () => {
    await page.evaluate(async () => {
      const f = await setup('inputs'); const original = structuredClone(f.lease);
      const preparing = f.runtime.prepare(f.lease);
      f.lease.capture_epoch_id = 'mutated-epoch'; f.lease.fence_token = 999; f.ancestry.classSessionId = 'mutated-class';
      await preparing; await f.runtime.start(); await f.runtime.append(new Uint8Array([12, 0]));
      const state = f.runtime.snapshot();
      require(state.lease.capture_epoch_id === original.capture_epoch_id && state.lease.fence_token === original.fence_token, 'Caller changed owned lease');
      require(await f.cache.readCaptureState(original.class_session_id, original.capture_epoch_id), 'Original cache ancestry lost');
    });
  });
  await test('deletion-tombstone-blocks-pending-upload-and-refresh-recovery', async () => {
    await page.evaluate(async () => {
      const f = await setup('delete'); await f.runtime.prepare(f.lease); await f.runtime.start();
      await f.runtime.append(new Uint8Array([13, 0]));
      await f.cache.applyDeletion({ object_type: 'course', object_id: f.ancestry.courseId, deletion_id: 'delete-combined', state: 'access_revoked' });
      await f.cache.unlock('recording_recovery', f.ancestry.classSessionId, async () => structuredClone(f.grant));
      await expectCode(f.runtime.uploadPending(), 'LOCAL_CHUNK_MISSING');
      require(f.control.uploadCount === 0, 'Deleted chunk reached transport');
      const restored = new lib.CaptureRuntime(f.transport, f.cache, f.ancestry, 64000);
      require(await restored.restore(f.ancestry.classSessionId, f.lease.capture_epoch_id) === false, 'Deleted opaque state restored');
      await expectCode(f.runtime.append(new Uint8Array([14, 0])), 'GONE');
    });
  });
} finally {
  await context.close(); server.close();
}
const report = { packages: ['PR-05b', 'PR-08a'], native_webcrypto_indexeddb: true,
  transport: 'synthetic CaptureTransport', actual_page_reload: true, synthetic_audio_only: true,
  passed: results.every(result => result.passed), cases: results };
await writeFile(join(output, 'combined-browser-results.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report));
assert.equal(report.passed, true);
