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
  response.end(request.url === '/cache.js' ? bundle : '<!doctype html><title>PR08a synthetic browser verification</title>');
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const context = await chromium.launchPersistentContext(join(output, 'browser-profile'), { headless: true });
const results = [];
try {
  const page = context.pages()[0];
  await page.goto(origin);
  await page.evaluate(async () => { window.lib = await import('/cache.js'); });
  async function test(name, run) {
    const started = Date.now();
    try { await run(); results.push({ name, passed: true, elapsed_ms: Date.now() - started }); }
    catch (error) { results.push({ name, passed: false, error: error.message }); }
  }
  await page.evaluate(() => {
    window.encoder = new TextEncoder();
    window.decoder = new TextDecoder();
    window.identity = { ownerId: 'owner-a', deviceId: 'device-a', sessionId: 'session-a' };
    window.ancestry = { semesterId: 'semester-a', courseId: 'course-a', classSessionId: 'class-a' };
    window.options = { enabled: true, recordingEnabled: true, databaseName: 'pr08a-native', limits: { draftBytes: 2_000_000, draftOperations: 100, recordingBytes: 2_000_000 } };
    window.keys = new Map();
    window.issue = (scope, expires = 60_000) => {
      if (!keys.has(scope)) keys.set(scope, btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32)))));
      return Promise.resolve({ key_id: 'key-' + scope, key_epoch: 1, key_material_b64: keys.get(scope), scope,
        expires_at: new Date(Date.now() + expires).toISOString(), cache_policy_revision: 'synthetic-only' });
    };
    window.reference = (objectId, generation = 1) => ({ scope: 'drafts', objectId, revision: 1, generation });
    window.write = (objectId, generation = 1) => ({ ...reference(objectId, generation), ancestry, operationId: 'op-' + objectId + '-' + generation });
    window.expectCode = async (promise, code) => { try { await promise; throw new Error('Expected ' + code); } catch (error) { if (error.code !== code) throw error; } };
    window.assertMetadata = async databaseName => {
      const db = await lib.openCacheDatabase(databaseName);
      const data = await new Promise((resolve, reject) => {
        const tx = db.transaction(['records', 'metadata']);
        const records = tx.objectStore('records').getAll(); const metadata = tx.objectStore('metadata').getAll();
        tx.oncomplete = () => resolve({ records: records.result, metadata: metadata.result }); tx.onabort = reject;
      }); db.close();
      if (data.records.length !== data.metadata.length) throw new Error('Metadata record count mismatch');
      for (const record of data.records) {
        const entry = data.metadata.find(value => value.id === record.id);
        if (!entry || JSON.stringify(entry.header) !== JSON.stringify(record.header) || entry.namespace !== record.namespace
          || entry.bytes !== record.bytes || entry.accessedAt !== record.accessedAt
          || Object.keys(entry).sort().join(',') !== 'accessedAt,bytes,header,id,namespace') throw new Error('Metadata projection mismatch');
      }
    };
    window.cache = new lib.TrustedCache(options); cache.bind(identity);
  });
  await test('default-disabled', async () => {
    assert.equal(await page.evaluate(async () => { const value = new lib.TrustedCache(); value.bind(identity); try { await value.unlock('drafts', null, () => issue('drafts')); } catch (error) { value.close(); return error.code; } }), 'DISABLED');
  });
  await test('native-encrypted-roundtrip-and-idempotence', async () => {
    const value = await page.evaluate(async () => {
      await cache.unlock('drafts', null, () => issue('drafts'));
      await cache.put(write('draft-1'), encoder.encode('PRIVATE SENTINEL 中文'));
      await cache.put(write('draft-1'), encoder.encode('PRIVATE SENTINEL 中文'));
      await expectCode(cache.put(write('draft-1'), encoder.encode('different')), 'REVISION_CONFLICT');
      return decoder.decode(await cache.read(reference('draft-1')));
    });
    assert.equal(value, 'PRIVATE SENTINEL 中文');
  });
  await test('ciphertext-only-browser-storage', async () => {
    const data = await page.evaluate(async () => {
      const db = await lib.openCacheDatabase(options.databaseName);
      const values = await new Promise(resolve => { const tx = db.transaction([...db.objectStoreNames]); const all = []; for (const name of db.objectStoreNames) { const request = tx.objectStore(name).getAll(); request.onsuccess = () => all.push(...request.result); } tx.oncomplete = () => resolve(all); }); db.close();
      return { text: JSON.stringify(values), local: { ...localStorage }, session: { ...sessionStorage }, caches: await caches.keys(), hasKey: values.some(value => value instanceof CryptoKey) };
    });
    assert.equal(data.text.includes('PRIVATE SENTINEL'), false); assert.equal(data.text.includes('key_material'), false);
    assert.deepEqual(data.local, {}); assert.deepEqual(data.session, {}); assert.deepEqual(data.caches, []); assert.equal(data.hasKey, false);
  });
  await test('draft-late-ack-preserves-new-generation', async () => {
    assert.equal(await page.evaluate(async () => {
      await cache.put(write('draft-1', 2), encoder.encode('new-generation'));
      await cache.acknowledgeDraft(reference('draft-1'), 'op-draft-1-1');
      if (await cache.read(reference('draft-1')) !== null) throw new Error('Old generation remains');
      return decoder.decode(await cache.read(reference('draft-1', 2)));
    }), 'new-generation');
  });
  await test('draft-quota-preserves-existing-content', async () => {
    await page.evaluate(async () => {
      const small = new lib.TrustedCache({ ...options, limits: { ...options.limits, draftOperations: 1 } }); small.bind(identity);
      await small.unlock('drafts', null, () => issue('drafts'));
      await expectCode(small.put(write('over-quota'), encoder.encode('overflow')), 'QUOTA_EXCEEDED');
      if (decoder.decode(await small.read(reference('draft-1', 2))) !== 'new-generation') throw new Error('Unique draft lost'); small.close();
    });
  });
  await test('lock-invalidates-late-unlock', async () => {
    await page.evaluate(async () => {
      let release; const pending = cache.unlock('learning', null, () => new Promise(resolve => { release = resolve; }));
      while (!release) await new Promise(resolve => setTimeout(resolve, 5));
      cache.lock(); release(await issue('learning')); await expectCode(pending, 'STALE_OPERATION');
      await expectCode(cache.read(reference('draft-1', 2)), 'LOCKED');
      await cache.unlock('drafts', null, () => issue('drafts'));
    });
  });
  await test('account-switch-and-purge-isolation', async () => {
    await page.evaluate(async () => {
      await cache.invalidate(); cache.bind({ ...identity, ownerId: 'owner-b' }); await cache.unlock('drafts', null, () => issue('drafts'));
      if (await cache.read(reference('draft-1', 2)) !== null) throw new Error('Cross-owner read');
      await cache.put(write('owner-b'), encoder.encode('B-only')); await cache.purge();
      cache.bind(identity); await cache.unlock('drafts', null, () => issue('drafts'));
      if (decoder.decode(await cache.read(reference('draft-1', 2))) !== 'new-generation') throw new Error('Other owner purged');
    });
  });
  await test('native-recording-durable-only-and-descriptor-binding', async () => {
    await page.evaluate(async () => {
      await cache.unlock('recording_recovery', 'class-a', () => issue('recording_recovery'));
      const bytes = new Uint8Array([1, 0, 2, 0]);
      const sha256 = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(x => x.toString(16).padStart(2, '0')).join('');
      window.descriptor = { class_session_id: 'class-a', capture_epoch_id: 'epoch-a', seq: 0, start_sample: 0, end_sample: 2, byte_length: 4, sha256, encoding: 'pcm_s16le', sample_rate: 16000, channels: 1 };
      await cache.putRecording(descriptor, bytes, ancestry, 1);
      await expectCode(cache.acknowledgeRecording({ id: 'chunk-a', descriptor, state: 'pending', durable_at: null, receipt_id: 'receipt-a', asr_submission: 'not_submitted' }), 'INVALID_INPUT');
      await expectCode(cache.readRecording({ ...descriptor, sha256: '0'.repeat(64) }), 'REVISION_CONFLICT');
      if ((await cache.readRecording(descriptor)).length !== 4) throw new Error('Chunk lost');
      await cache.acknowledgeRecording({ id: 'chunk-a', descriptor, state: 'durable', durable_at: new Date().toISOString(), receipt_id: 'receipt-a', asr_submission: 'not_submitted' });
      if (await cache.readRecording(descriptor) !== null) throw new Error('Durable chunk retained');
    });
  });
  await test('capture-state-cas-and-reload-recovery', async () => {
    assert.equal(await page.evaluate(async () => {
      await cache.saveCaptureState('class-a', 'epoch-a', encoder.encode('state-2'), ancestry, 1, 2);
      await expectCode(cache.saveCaptureState('class-a', 'epoch-a', encoder.encode('state-1'), ancestry, 1, 1), 'REVISION_CONFLICT');
      await expectCode(cache.saveCaptureState('class-a', 'epoch-a', encoder.encode('other'), ancestry, 1, 2), 'REVISION_CONFLICT');
      await cache.saveCaptureState('class-a', 'epoch-a', encoder.encode('new-fence'), ancestry, 2, 1);
      cache.close(); cache = new lib.TrustedCache(options); cache.bind(identity);
      await expectCode(cache.readCaptureState('class-a', 'epoch-a'), 'LOCKED');
      await cache.unlock('recording_recovery', 'class-a', () => issue('recording_recovery'));
      return decoder.decode(await cache.readCaptureState('class-a', 'epoch-a'));
    }), 'new-fence');
  });
  await test('recording-input-snapshot-survives-caller-buffer-reuse', async () => {
    await page.evaluate(async () => {
      const pcm = new Uint8Array([3, 0, 4, 0]);
      const originalDigest = crypto.subtle.digest.bind(crypto.subtle);
      const sha256 = [...new Uint8Array(await originalDigest('SHA-256', pcm))].map(x => x.toString(16).padStart(2, '0')).join('');
      const descriptor = { ...window.descriptor, seq: 5, sha256 };
      const frozen = structuredClone(descriptor);
      const parents = structuredClone(ancestry);
      let release;
      crypto.subtle.digest = async (...args) => { const hash = await originalDigest(...args); await new Promise(resolve => { release = resolve; }); return hash; };
      try {
        const pending = cache.putRecording(descriptor, pcm, parents, 2);
        while (!release) await new Promise(resolve => setTimeout(resolve, 5));
        pcm.fill(0); descriptor.seq = 999; parents.classSessionId = 'changed'; release(); await pending;
        const stored = await cache.readRecording(frozen);
        if (String(stored) !== '3,0,4,0') throw new Error('Reused buffer corrupted persisted PCM');
      } finally { crypto.subtle.digest = originalDigest; }
    });
  });
  await test('mutated-durable-receipt-cannot-delete-another-unique-chunk', async () => {
    await page.evaluate(async () => {
      const pcm = new Uint8Array([1, 0, 2, 0]);
      const a = { ...descriptor, seq: 6 }; const b = { ...descriptor, seq: 7 };
      await cache.putRecording(a, pcm, ancestry, 2); await cache.putRecording(b, pcm, ancestry, 2);
      const receipt = { id: 'chunk-a', descriptor: a, state: 'durable', durable_at: new Date().toISOString(), receipt_id: 'receipt-a', asr_submission: 'not_submitted' };
      const original = cache.readRecording.bind(cache); let release;
      cache.readRecording = async value => { await new Promise(resolve => { release = resolve; }); return original(value); };
      try { const pending = cache.acknowledgeRecording(receipt); receipt.descriptor = b; release(); await pending; }
      finally { cache.readRecording = original; }
      if (await cache.readRecording(a) !== null) throw new Error('Actual durable chunk retained');
      if (!(await cache.readRecording(b))) throw new Error('Unique unrelated chunk deleted');
    });
  });
  await test('same-generation-conflict-uses-frozen-plaintext', async () => {
    await page.evaluate(async () => {
      await cache.unlock('drafts', null, () => issue('drafts'));
      await cache.put(write('retry-race'), encoder.encode('AAAA'));
      const reused = encoder.encode('BBBB'); const pending = cache.put(write('retry-race'), reused);
      reused.set(encoder.encode('AAAA'));
      await expectCode(pending, 'REVISION_CONFLICT');
    });
  });
  await test('database-v1-upgrade-and-ciphertext-schema-migration', async () => {
    await page.evaluate(async () => {
      const databaseName = 'pr08a-v1-migration';
      const raw = crypto.getRandomValues(new Uint8Array(32));
      const material = btoa(String.fromCharCode(...raw));
      const key = await crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['encrypt']); raw.fill(0);
      const header = { ...write('legacy'), ownerId: identity.ownerId, deviceId: identity.deviceId, keyId: 'legacy-key', keyVersion: 1, schema: 1 };
      const canonical = value => value && typeof value === 'object' ? Array.isArray(value) ? value.map(canonical) : Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
      const namespace = JSON.stringify([identity.ownerId, identity.deviceId]);
      const id = JSON.stringify([namespace, 'drafts', 'legacy', 1, 1]); const nonce = crypto.getRandomValues(new Uint8Array(12));
      const ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce, additionalData: encoder.encode(JSON.stringify(canonical(header))) }, key, encoder.encode('legacy-private'));
      const db = await new Promise(resolve => { const request = indexedDB.open(databaseName, 1); request.onupgradeneeded = () => { request.result.createObjectStore('records', { keyPath: 'id' }); request.result.createObjectStore('nonces', { keyPath: 'id' }); }; request.onsuccess = () => resolve(request.result); });
      await new Promise(resolve => { const tx = db.transaction('records', 'readwrite'); tx.objectStore('records').add({ id, namespace, header, nonce, ciphertext, bytes: ciphertext.byteLength, accessedAt: Date.now() }); tx.oncomplete = resolve; }); db.close();
      const migrated = new lib.TrustedCache({ ...options, databaseName }); migrated.bind(identity);
      await migrated.unlock('drafts', null, async () => ({ key_id: 'legacy-key', key_epoch: 1, key_material_b64: material, scope: 'drafts', expires_at: new Date(Date.now() + 60_000).toISOString(), cache_policy_revision: 'synthetic-only' }));
      await migrated.migrate(reference('legacy'));
      if (decoder.decode(await migrated.read(reference('legacy'))) !== 'legacy-private') throw new Error('Migration lost ciphertext'); migrated.close();
      await assertMetadata(databaseName);
    });
  });
  await test('v2-metadata-backfill-abort-is-atomic-and-retry-preserves-ciphertext', async () => {
    await page.evaluate(async () => {
      const value = new lib.TrustedCache({ ...options, databaseName: 'pr08a-backfill-source' }); value.bind(identity);
      await value.unlock('drafts', null, () => issue('drafts')); await value.put(write('v2'), encoder.encode('v2-private'));
      await value.unlock('learning', null, () => issue('learning'));
      await value.put({ scope: 'learning', objectId: 'v2-learning', revision: 1, generation: 1, ancestry, verifiedAt: Date.now() }, encoder.encode('v2-learning'));
      await value.unlock('recording_recovery', 'class-a', () => issue('recording_recovery'));
      await value.putRecording({ ...descriptor, seq: 99 }, new Uint8Array([1, 0, 2, 0]), ancestry, 1);
      await value.saveCaptureState('class-a', 'v2-state', encoder.encode('v2-state'), ancestry, 1, 1);
      let db = await lib.openCacheDatabase('pr08a-backfill-source');
      const source = await new Promise(resolve => { const tx = db.transaction(['records', 'nonces']);
        const records = tx.objectStore('records').getAll(); const nonces = tx.objectStore('nonces').getAll();
        tx.oncomplete = () => resolve({ records: records.result, nonces: nonces.result }); });
      db.close(); value.close();
      const name = 'pr08a-v2-backfill';
      db = await new Promise(resolve => { const request = indexedDB.open(name, 2); request.onupgradeneeded = () => {
        for (const store of ['records', 'nonces', 'namespaces', 'tombstones']) request.result.createObjectStore(store, { keyPath: 'id' });
      }; request.onsuccess = () => resolve(request.result); });
      await new Promise(resolve => { const tx = db.transaction(['records', 'nonces'], 'readwrite');
        for (const record of source.records) tx.objectStore('records').put(record);
        for (const nonce of source.nonces) tx.objectStore('nonces').put(nonce);
        tx.oncomplete = resolve; }); db.close();
      const original = IDBObjectStore.prototype.put;
      let interrupted = false;
      IDBObjectStore.prototype.put = function (...args) {
        const request = original.apply(this, args);
        if (this.name === 'metadata' && !interrupted) { interrupted = true; this.transaction.abort(); }
        return request;
      };
      try { await expectCode(lib.openCacheDatabase(name), 'STORAGE_UNAVAILABLE'); }
      finally { IDBObjectStore.prototype.put = original; }
      db = await new Promise(resolve => { const request = indexedDB.open(name); request.onsuccess = () => resolve(request.result); });
      if (db.version !== 2 || db.objectStoreNames.contains('metadata')) throw new Error('Interrupted upgrade committed partial schema'); db.close();
      await assertMetadata(name);
      const upgraded = new lib.TrustedCache({ ...options, databaseName: name }); upgraded.bind(identity);
      await upgraded.unlock('drafts', null, () => issue('drafts'));
      if (decoder.decode(await upgraded.read(reference('v2'))) !== 'v2-private') throw new Error('v2 upgrade changed ciphertext');
      await upgraded.unlock('recording_recovery', 'class-a', () => issue('recording_recovery'));
      if (decoder.decode(await upgraded.readCaptureState('class-a', 'v2-state')) !== 'v2-state') throw new Error('v2 upgrade lost capture metadata');
      db = await lib.openCacheDatabase(name);
      const nonces = await new Promise(resolve => { const request = db.transaction('nonces').objectStore('nonces').getAll(); request.onsuccess = () => resolve(request.result); }); db.close();
      if (JSON.stringify(nonces) !== JSON.stringify(source.nonces)) throw new Error('Upgrade changed nonce history');
      await upgraded.acknowledgeDraft(reference('v2'), 'op-v2-1'); await assertMetadata(name); upgraded.close();
    });
  });
  await test('cache-writes-load-only-the-exact-retry-ciphertext', async () => {
    await page.evaluate(async () => {
      const name = 'pr08a-metadata-write'; const value = new lib.TrustedCache({ ...options, databaseName: name }); value.bind(identity);
      await value.unlock('drafts', null, () => issue('drafts'));
      for (let index = 0; index < 8; index++) await value.put(write('prior-' + index), new Uint8Array(64_000));
      const originalGetAll = IDBObjectStore.prototype.getAll; const originalCursor = IDBObjectStore.prototype.openCursor;
      const originalGet = IDBObjectStore.prototype.get;
      const expectedId = JSON.stringify([JSON.stringify([identity.ownerId, identity.deviceId]), 'drafts', 'target-only', 1, 1]);
      let reads = 0;
      IDBObjectStore.prototype.getAll = function (...args) { if (this.name === 'records') throw new Error('Write cloned all ciphertext'); return originalGetAll.apply(this, args); };
      IDBObjectStore.prototype.openCursor = function (...args) { if (this.name === 'records') throw new Error('Write scanned ciphertext'); return originalCursor.apply(this, args); };
      IDBObjectStore.prototype.get = function (...args) { if (this.name === 'records') { reads++; if (args[0] !== expectedId) throw new Error('Write read unrelated ciphertext'); } return originalGet.apply(this, args); };
      try { await value.put(write('target-only'), encoder.encode('target')); await value.put(write('target-only'), encoder.encode('target')); }
      finally { IDBObjectStore.prototype.getAll = originalGetAll; IDBObjectStore.prototype.openCursor = originalCursor; IDBObjectStore.prototype.get = originalGet; }
      if (reads !== 2) throw new Error('Expected one exact record lookup per write');
      await assertMetadata(name); value.close();
    });
  });
  await test('capture-state-read-decrypts-only-the-latest-target-with-unrelated-corrupt-pcm', async () => {
    await page.evaluate(async () => {
      const name = 'pr08a-target-state'; const value = new lib.TrustedCache({ ...options, databaseName: name }); value.bind(identity);
      await value.unlock('recording_recovery', 'class-a', () => issue('recording_recovery'));
      const chunk = { ...descriptor, capture_epoch_id: 'unrelated-pcm', seq: 0 };
      await value.putRecording(chunk, new Uint8Array([1, 0, 2, 0]), ancestry, 1);
      await value.saveCaptureState('class-a', 'target', encoder.encode('old'), ancestry, 1, 1);
      await value.saveCaptureState('class-a', 'target', encoder.encode('latest'), ancestry, 2, 1);
      const db = await lib.openCacheDatabase(name);
      await new Promise(resolve => { const tx = db.transaction('records', 'readwrite'); const records = tx.objectStore('records');
        const key = JSON.stringify([JSON.stringify([identity.ownerId, identity.deviceId]), 'recording_recovery', JSON.stringify(['class-a', 'unrelated-pcm', 0]), 1, 1]);
        const request = records.get(key); request.onsuccess = () => { const record = request.result; record.ciphertext = new Uint8Array([0]).buffer; records.put(record); }; tx.oncomplete = resolve;
      }); db.close();
      const decrypt = crypto.subtle.decrypt.bind(crypto.subtle); const getAll = IDBObjectStore.prototype.getAll;
      let decrypts = 0;
      crypto.subtle.decrypt = (...args) => { decrypts++; return decrypt(...args); };
      IDBObjectStore.prototype.getAll = function (...args) { if (this.name === 'records') throw new Error('State lookup cloned PCM pool'); return getAll.apply(this, args); };
      try {
        if (decoder.decode(await value.readCaptureState('class-a', 'target')) !== 'latest') throw new Error('Latest target state not selected');
        if (await value.readCaptureState('class-a', 'missing') !== null) throw new Error('Missing state returned another record');
      } finally { crypto.subtle.decrypt = decrypt; IDBObjectStore.prototype.getAll = getAll; }
      if (decrypts !== 1) throw new Error('State lookup decrypted unrelated content');
      await value.applyDeletion({ object_type: 'course', object_id: 'course-a', deletion_id: 'state-delete', state: 'access_revoked' });
      await value.unlock('recording_recovery', 'class-a', () => issue('recording_recovery'));
      if (await value.readCaptureState('class-a', 'target') !== null) throw new Error('Deleted capture state restored');
      await assertMetadata(name); value.close();
    });
  });
  await test('native-idb-blocked-upgrade-reports-failure', async () => {
    await page.evaluate(async () => {
      const db = await new Promise(resolve => { const request = indexedDB.open('pr08a-blocked', 1); request.onsuccess = () => resolve(request.result); });
      try { await expectCode(lib.openCacheDatabase('pr08a-blocked'), 'DATABASE_BLOCKED'); } finally { db.close(); }
    });
  });
  await test('learning-ttl-lru-and-independent-pools', async () => {
    await page.evaluate(async () => {
      await cache.unlock('learning', null, () => issue('learning'));
      for (let i = 0; i < 21; i++) await cache.put({ scope: 'learning', objectId: 'lesson-' + i, revision: 1, generation: 1,
        ancestry: { ...ancestry, classSessionId: 'lesson-' + i }, verifiedAt: Date.now() }, encoder.encode('learning'));
      if (await cache.read({ scope: 'learning', objectId: 'lesson-0', revision: 1, generation: 1 }) !== null) throw new Error('LRU cap missed');
      const stale = { scope: 'learning', objectId: 'stale', revision: 1, generation: 1, ancestry, verifiedAt: Date.now() - 14 * 24 * 60 * 60 * 1000 };
      await expectCode(cache.put(stale, encoder.encode('expired')), 'QUOTA_EXCEEDED');
      await cache.unlock('drafts', null, () => issue('drafts'));
      if (decoder.decode(await cache.read(reference('draft-1', 2))) !== 'new-generation') throw new Error('LRU affected drafts');
    });
  });
  await test('key-window-expires-without-deleting-unique-content', async () => {
    await page.evaluate(async () => {
      await cache.unlock('drafts', null, () => issue('drafts', 40));
      await new Promise(resolve => setTimeout(resolve, 70));
      await expectCode(cache.read(reference('draft-1', 2)), 'LOCKED');
      await cache.unlock('drafts', null, () => issue('drafts'));
      if (decoder.decode(await cache.read(reference('draft-1', 2))) !== 'new-generation') throw new Error('Expiry deleted draft');
    });
  });
  await test('cross-tab-invalidation-barrier', async () => {
    const other = await context.newPage(); await other.goto(origin);
    const seed = await page.evaluate(() => ({ identity, options, key: keys.get('drafts') }));
    await other.evaluate(async seed => { const { TrustedCache } = await import('/cache.js'); window.other = new TrustedCache(seed.options); other.bind(seed.identity); await other.unlock('drafts', null, async () => ({ key_id: 'key-drafts', key_epoch: 1, key_material_b64: seed.key, scope: 'drafts', expires_at: new Date(Date.now() + 60_000).toISOString(), cache_policy_revision: 'synthetic-only' })); }, seed);
    await page.evaluate(() => cache.invalidate());
    await other.waitForTimeout(30);
    assert.equal(await other.evaluate(async () => { try { await other.read({ scope: 'drafts', objectId: 'draft-1', revision: 1, generation: 2 }); } catch (error) { return error.code; } }), 'LOCKED');
    await other.close();
    await page.evaluate(async () => { cache.bind(identity); await cache.unlock('drafts', null, () => issue('drafts')); });
  });
  await test('ancestor-tombstone-and-no-resurrection', async () => {
    await page.evaluate(async () => {
      await cache.applyDeletion({ object_type: 'course', object_id: 'course-a', deletion_id: 'deletion-a', state: 'access_revoked' });
      await cache.unlock('drafts', null, () => issue('drafts'));
      if (await cache.read(reference('draft-1', 2)) !== null) throw new Error('Deleted descendant remains');
      await expectCode(cache.put(write('revive'), encoder.encode('do not revive')), 'GONE');
    });
  });
  await test('persistent-namespace-barrier-with-broadcast-reception-disabled', async () => {
    const other = await context.newPage(); await other.goto(origin);
    const seed = await page.evaluate(() => ({ identity, options, key: keys.get('drafts') }));
    await other.evaluate(async seed => {
      window.BroadcastChannel = class { postMessage() {} close() {} };
      const { TrustedCache } = await import('/cache.js');
      window.other = new TrustedCache({ ...seed.options, databaseName: 'pr08a-barrier-only' }); other.bind(seed.identity);
      await other.unlock('drafts', null, async () => ({ key_id: 'key-drafts', key_epoch: 1, key_material_b64: seed.key, scope: 'drafts', expires_at: new Date(Date.now() + 60_000).toISOString(), cache_policy_revision: 'synthetic-only' }));
      window.ref = { scope: 'drafts', objectId: 'barrier', revision: 1, generation: 1 };
      window.entry = { ...ref, operationId: 'barrier-op', ancestry: { semesterId: null, courseId: null, classSessionId: null } };
      await other.put(entry, new TextEncoder().encode('unique'));
    }, seed);
    await page.evaluate(async () => { const value = new lib.TrustedCache({ ...options, databaseName: 'pr08a-barrier-only' }); value.bind(identity); await value.invalidate(); value.close(); });
    const result = await other.evaluate(async () => {
      let read, write;
      try { await other.read(ref); } catch (error) { read = error.code; }
      try { await other.put({ ...entry, objectId: 'new' }, new TextEncoder().encode('stale')); } catch (error) { write = error.code; }
      return { read, write };
    });
    assert.deepEqual(result, { read: 'STALE_OPERATION', write: 'STALE_OPERATION' }); await other.close();
  });
  await test('native-browser-quota-aborts-atomically-and-retains-unique-draft', async () => {
    const isolated = await chromium.launchPersistentContext(join(output, 'quota-profile'), { headless: true });
    const quotaPage = isolated.pages()[0]; await quotaPage.goto(origin);
    const cdp = await isolated.newCDPSession(quotaPage);
    await cdp.send('Storage.overrideQuotaForOrigin', { origin, quotaSize: 1_048_576 });
    try {
      const result = await quotaPage.evaluate(async () => {
        const { TrustedCache, openCacheDatabase } = await import('/cache.js');
        const value = new TrustedCache({ enabled: true, databaseName: 'pr08a-native-quota', limits: { draftBytes: 32_000_000, draftOperations: 100, recordingBytes: 1_000_000 } });
        value.bind({ ownerId: 'quota-owner', deviceId: 'quota-device', sessionId: 'quota-session' });
        await value.unlock('drafts', null, async () => ({ key_id: 'quota-key', key_epoch: 1, key_material_b64: btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32)))), scope: 'drafts', expires_at: new Date(Date.now() + 60_000).toISOString(), cache_policy_revision: 'synthetic-only' }));
        const entry = id => ({ scope: 'drafts', objectId: id, revision: 1, generation: 1, operationId: 'op-' + id, ancestry: { semesterId: null, courseId: null, classSessionId: null } });
        await value.put(entry('survivor'), new TextEncoder().encode('surviving-unique'));
        await value.unlock('learning', null, async () => ({ key_id: 'quota-learning-key', key_epoch: 1, key_material_b64: btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32)))), scope: 'learning', expires_at: new Date(Date.now() + 60_000).toISOString(), cache_policy_revision: 'synthetic-only' }));
        const lesson = { scope: 'learning', objectId: 'quota-lesson', revision: 1, generation: 1, ancestry: { semesterId: null, courseId: null, classSessionId: null }, verifiedAt: Date.now() };
        await value.put(lesson, new TextEncoder().encode('surviving-learning'));
        let failure = null; let failedId = null;
        for (let i = 0; i < 16; i++) {
          const data = new Uint8Array(256_000);
          for (let offset = 0; offset < data.length; offset += 32_000) crypto.getRandomValues(data.subarray(offset, offset + 32_000));
          try { await value.put(entry('fill-' + i), data); } catch (error) { failure = error.code; failedId = 'fill-' + i; break; }
        }
        const survived = new TextDecoder().decode(await value.read(entry('survivor'))) === 'surviving-unique';
        const put = IDBObjectStore.prototype.put; let forcedTouch = false; let touchAbort;
        IDBObjectStore.prototype.put = function (...args) {
          const request = put.apply(this, args);
          if (this.name === 'metadata' && args[0].header?.objectId === 'quota-lesson' && !forcedTouch) {
            forcedTouch = true; const tx = this.transaction;
            tx.addEventListener('abort', () => { touchAbort = tx.error?.name; });
            const padding = new Uint8Array(2_000_000);
            for (let offset = 0; offset < padding.length; offset += 32_000) crypto.getRandomValues(padding.subarray(offset, offset + 32_000));
            put.call(this, { id: 'synthetic-over-quota-touch', padding });
          }
          return request;
        };
        let learningSurvived;
        try { learningSurvived = new TextDecoder().decode(await value.read(lesson)) === 'surviving-learning'; }
        finally { IDBObjectStore.prototype.put = put; }
        if (!forcedTouch || touchAbort !== 'QuotaExceededError') throw new Error('Learning quota fallback was not exercised');
        const absent = failedId !== null && await value.read(entry(failedId)) === null;
        const db = await openCacheDatabase('pr08a-native-quota');
        await new Promise((resolve, reject) => { const tx = db.transaction(['records', 'metadata']);
          const records = tx.objectStore('records').getAllKeys(); const metadata = tx.objectStore('metadata').getAllKeys();
          tx.oncomplete = () => JSON.stringify(records.result) === JSON.stringify(metadata.result) ? resolve() : reject(new Error('Quota rollback left orphan metadata'));
        }); db.close();
        value.close(); return { failure, survived, learningSurvived, absent, mechanism: 'CDP native IndexedDB quota' };
      });
      assert.deepEqual(result, { failure: 'QUOTA_EXCEEDED', survived: true, learningSurvived: true, absent: true, mechanism: 'CDP native IndexedDB quota' });
    } finally { await cdp.send('Storage.overrideQuotaForOrigin', { origin }); await isolated.close(); }
  });
  await test('browser-process-restart-retains-ciphertext-and-requires-unlock', async () => {
    const profile = join(output, 'restart-profile');
    let restarted = await chromium.launchPersistentContext(profile, { headless: true });
    let tab = restarted.pages()[0]; await tab.goto(origin);
    const grant = await tab.evaluate(async () => {
      const { TrustedCache } = await import('/cache.js');
      const raw = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))));
      const grant = { key_id: 'restart-key', key_epoch: 1, key_material_b64: raw, scope: 'drafts', expires_at: new Date(Date.now() + 60_000).toISOString(), cache_policy_revision: 'synthetic-only' };
      const value = new TrustedCache({ enabled: true, databaseName: 'restart', limits: { draftBytes: 1_000_000, draftOperations: 10, recordingBytes: 1_000_000 } });
      value.bind({ ownerId: 'restart-owner', deviceId: 'restart-device', sessionId: 'restart-session' }); await value.unlock('drafts', null, async () => ({ ...grant }));
      await value.put({ scope: 'drafts', objectId: 'restart', revision: 1, generation: 1, operationId: 'restart-op', ancestry: { semesterId: null, courseId: null, classSessionId: null } }, new TextEncoder().encode('restart-private'));
      return grant;
    });
    await restarted.close();
    restarted = await chromium.launchPersistentContext(profile, { headless: true });
    try {
      tab = restarted.pages()[0]; await tab.goto(origin);
      const result = await tab.evaluate(async grant => {
        const { TrustedCache } = await import('/cache.js');
        const value = new TrustedCache({ enabled: true, databaseName: 'restart', limits: { draftBytes: 1_000_000, draftOperations: 10, recordingBytes: 1_000_000 } });
        value.bind({ ownerId: 'restart-owner', deviceId: 'restart-device', sessionId: 'restart-session' });
        const ref = { scope: 'drafts', objectId: 'restart', revision: 1, generation: 1 }; let locked;
        try { await value.read(ref); } catch (error) { locked = error.code; }
        await value.unlock('drafts', null, async () => grant); const restored = new TextDecoder().decode(await value.read(ref)); value.close();
        return { locked, restored };
      }, grant);
      assert.deepEqual(result, { locked: 'LOCKED', restored: 'restart-private' });
    } finally { await restarted.close(); }
  });
  await test('learning-fifty-million-byte-soft-limit', async () => {
    await page.evaluate(async () => {
      const isolated = new lib.TrustedCache({ ...options, databaseName: 'pr08a-learning-limit' }); isolated.bind(identity);
      await isolated.unlock('learning', null, () => issue('learning'));
      await expectCode(isolated.put({ scope: 'learning', objectId: 'too-large', revision: 1, generation: 1, ancestry, verifiedAt: Date.now() }, new Uint8Array(50_000_000)), 'QUOTA_EXCEEDED');
      isolated.close();
    });
  });
  await test('learning-cumulative-soft-limit-evicts-oldest-and-preserves-unique-pools', async () => {
    const result = await page.evaluate(async () => {
      const databaseName = 'pr08a-cumulative';
      const value = new lib.TrustedCache({ ...options, databaseName }); value.bind(identity);
      await value.unlock('learning', null, () => issue('learning'));
      await value.unlock('drafts', null, () => issue('drafts'));
      await value.unlock('recording_recovery', 'class-a', () => issue('recording_recovery'));
      await value.put(write('unique-draft'), encoder.encode('retain-unique'));
      const chunk = { ...descriptor, seq: 33 };
      await value.putRecording(chunk, new Uint8Array([1, 0, 2, 0]), ancestry, 3);
      const entry = index => ({ scope: 'learning', objectId: 'cumulative-' + index, revision: 1, generation: 1, ancestry, verifiedAt: Date.now() });
      for (let index = 0; index < 11; index++) {
        const bytes = new Uint8Array(5_000_000); bytes[0] = index;
        await value.put(entry(index), bytes);
      }
      const oldestGone = await value.read(entry(0)) === null;
      const newest = await value.read(entry(10));
      const draftSurvives = decoder.decode(await value.read(reference('unique-draft'))) === 'retain-unique';
      const recordingSurvives = String(await value.readRecording(chunk)) === '1,0,2,0';
      const db = await lib.openCacheDatabase(databaseName);
      const records = await new Promise(resolve => { const request = db.transaction('records').objectStore('records').getAll(); request.onsuccess = () => resolve(request.result); }); db.close();
      const learning = records.filter(record => record.header.scope === 'learning');
      value.close();
      return { oldestGone, newestFirstByte: newest?.[0], draftSurvives, recordingSurvives,
        learningBytes: learning.reduce((sum, record) => sum + record.bytes, 0), learningRecords: learning.length };
    });
    assert.equal(result.oldestGone, true); assert.equal(result.newestFirstByte, 10);
    assert.equal(result.draftSurvives, true); assert.equal(result.recordingSurvives, true);
    assert.ok(result.learningBytes <= 50_000_000); assert.ok(result.learningRecords < 11);
  });
  await test('authclient-failed-logout-locks-before-network-completes', async () => {
    await page.evaluate(async () => {
      const user = { id: '11111111-1111-4111-8111-111111111111', revision: 1, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), email: 'synthetic@example.invalid', nickname: '', school: '', major: '', role: 'student', status: 'active', mfa_enabled: false };
      const session = { user, device_id: '22222222-2222-4222-8222-222222222222', session_id: '33333333-3333-4333-8333-333333333333', csrf_token: 'A'.repeat(43) };
      let release;
      const auth = new lib.AuthClient(async url => url.endsWith('/auth/session') ? Response.json(session) : new Promise((resolve, reject) => { release = () => reject(new TypeError('synthetic offline')); }));
      const storage = auth.createTrustedCache({ ...options, databaseName: 'pr08a-auth-native' });
      await auth.bootstrap(); await storage.unlock('drafts', null, () => issue('drafts'));
      await storage.put(write('auth'), encoder.encode('safe'));
      const logout = auth.logout().catch(error => error.code);
      await expectCode(storage.read(reference('auth')), 'LOCKED');
      release(); if (await logout !== 'RESULT_UNKNOWN') throw new Error('Unknown logout status lost'); storage.close();
    });
  });
  await test('metadata-projection-survives-migration-ack-lru-delete-and-purge', async () => {
    await page.evaluate(async () => {
      for (const database of await indexedDB.databases()) {
        if (database.version === 3) await assertMetadata(database.name);
      }
    });
  });
} finally {
  await context.close(); server.close();
}
const report = { package: 'PR-08a', browser: 'Chromium', native_webcrypto_indexeddb: true, synthetic_network: true, passed: results.every(result => result.passed), cases: results };
await writeFile(join(output, 'browser-results.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report));
assert.equal(report.passed, true);
