import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const output = process.env.PR08B_OUTPUT;
const { chromium } = await import(pathToFileURL(process.env.PR08B_PLAYWRIGHT_MODULE).href);
const bundle = await readFile(join(output, 'sync.js'));
const server = createServer((request, response) => {
  response.setHeader('Cache-Control', 'no-store');
  response.setHeader('Content-Type', request.url === '/sync.js' ? 'text/javascript' : 'text/html');
  response.end(request.url === '/sync.js' ? bundle : '<!doctype html><title>PR-08b native storage verification</title>');
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const context = await chromium.launchPersistentContext(join(output, 'sync-browser-profile'), { headless: true });
const browserVersion = context.browser()?.version();
const page = context.pages()[0];
const results = [];

async function install(target = page) {
  await target.goto(origin);
  await target.evaluate(async () => {
    window.lib = await import('/sync.js');
    window.ids = {
      owner: '10000000-0000-4000-8000-000000000001', device: '10000000-0000-4000-8000-000000000002',
      session: '10000000-0000-4000-8000-000000000003', semester: '10000000-0000-4000-8000-000000000004',
      course: '10000000-0000-4000-8000-000000000005', classroom: '10000000-0000-4000-8000-000000000006',
      document: '10000000-0000-4000-8000-000000000007', conflict: '10000000-0000-4000-8000-000000000008',
      deletion: '10000000-0000-4000-8000-000000000009',
      revisionA: '10000000-0000-4000-8000-000000000010', revisionB: '10000000-0000-4000-8000-000000000011',
      snapshotA: '10000000-0000-4000-8000-000000000012', snapshotB: '10000000-0000-4000-8000-000000000013',
    };
    window.ancestry = { semesterId: ids.semester, courseId: ids.course, classSessionId: ids.classroom };
    window.operation = (text = 'SYNTHETIC PRIVATE LOCAL TEXT', revision = 1, contentRevisionId = null) => {
      const draft = crypto.randomUUID();
      return { op_id: crypto.randomUUID(), object_id: ids.document, object_type: 'personal_revision',
        draft_id: draft, base_revision: revision,
        payload: { base_revision: revision, base_content_revision_id: contentRevisionId, draft_id: draft,
          body_markdown: text, personal_note: 'Synthetic personal note' } };
    };
    window.expectCode = async (promise, code) => {
      try { await promise; throw new Error('Expected ' + code); }
      catch (error) { if (error.code !== code) throw error; }
    };
    class NativeTrustedCache extends lib.TrustedCache {
      unlockScope(scope) {
        // Public synthetic fixture. Each unlock receives independent key material.
        return this.unlock(scope, null, async () => ({ key_id: 'synthetic-' + scope, key_epoch: 1,
          key_material_b64: btoa(String.fromCharCode(...new Uint8Array(32).fill(17))), scope,
          expires_at: new Date(Date.now() + 120000).toISOString(), cache_policy_revision: 'synthetic-only' }));
      }
    }
    class NativeCache extends NativeTrustedCache {
      get allowsSyncRecords() { return true; }
    }
    window.NativeTrustedCache = NativeTrustedCache;
    class Transport {
      constructor(options = {}) {
        this.receipts = new Map(options.receipts ?? []); this.conflicts = new Map(options.conflicts ?? []); this.conflictByOp = new Map(); this.pushes = []; this.resolutions = []; this.mode = 'apply'; this.pages = [];
        this.revokedRevisions = new Set(options.revokedRevisions ?? []);
        this.conflictsRevoked = options.conflictsRevoked ?? false;
        this.revisionReads = [];
        this.serverRevision = 2;
        this.projections = [
          ['semester', ids.semester, null], ['course', ids.course, ids.semester],
          ['class_session', ids.classroom, ids.course], ['review_document', ids.document, ids.classroom],
        ].map(([object_type, object_id, parent_id]) => ({ object_type, object_id, parent_id, revision: 1,
          adopted_revision_id: null, updated_at: new Date().toISOString() }));
      }
      async bootstrap() { return { projection_complete: true, maximum_projection_count: 10000, projections: structuredClone(this.projections), watermark: 'watermark-0' }; }
      async changes(after) { return this.pages.shift() ?? { items: [], next_cursor: after, has_more: false }; }
      async deletion(id) { return { id, scope_id: ids.classroom, scope_type: 'class_session', state: 'access_revoked', revision: 1 }; }
      async document(id) {
        const projection = this.projections.find(value => value.object_id === id && value.object_type === 'review_document');
        if (!projection) throw Object.assign(new Error('Document missing'), { code: 'NOT_FOUND' });
        return { id, revision: projection.revision, created_at: projection.updated_at, updated_at: projection.updated_at,
          class_session_id: projection.parent_id, kind: 'note', title: 'Synthetic personal note', adopted_revision_id: null,
          latest_personal_revision_id: null, candidate_revision_ids: [], understood: false, source_status: 'unchanged' };
      }
      async reviewRevision(id) {
        this.revisionReads.push(id);
        if (![ids.revisionA, ids.revisionB].includes(id) || this.revokedRevisions.has(id)) {
          throw Object.assign(new Error('Revision missing'), { code: 'NOT_FOUND' });
        }
        return { id, document_id: ids.document, revision: 1, kind: 'ai_candidate',
          created_at: new Date().toISOString(), updated_at: new Date().toISOString(), parent_revision_id: null,
          source_snapshot_id: id === ids.revisionA ? ids.snapshotA : ids.snapshotB, raw_output_ref: null,
          body_markdown: id === ids.revisionA ? 'AUTHORIZED SOURCE A BODY' : 'REVOKED SOURCE B BODY',
          structured_content: null, personal_note: '' };
      }
      async receipt(id) { if (!this.receipts.has(id)) throw Object.assign(new Error('Missing'), { code: 'NOT_FOUND' }); return structuredClone(this.receipts.get(id)); }
      async makeReceipt(operation, kind = 'syncOperation', value = operation) {
        if (this.mode === 'conflict' && !this.conflictByOp.has(operation.op_id)) this.conflictByOp.set(operation.op_id, crypto.randomUUID());
        return { op_id: operation.op_id, operation: kind, request_sha256: await lib.syncFingerprint(value),
          status: this.mode === 'conflict' ? 'conflict' : 'applied', object_id: operation.object_id,
          revision: this.serverRevision, conflict_id: this.mode === 'conflict' ? this.conflictByOp.get(operation.op_id) : null, task_id: null };
      }
      async push(operation, batchId) {
        this.pushes.push({ operation: structuredClone(operation), batchId });
        const receipt = await this.makeReceipt(operation);
        this.receipts.set(operation.op_id, receipt);
        if (this.mode === 'conflict') { this.conflictValue = { id: receipt.conflict_id, object_id: operation.object_id,
          base_revision: operation.base_revision, server_revision: this.serverRevision, local_operation: structuredClone(operation),
          server_content: { body_markdown: 'SYNTHETIC CLOUD TEXT' }, resolved: false }; this.conflicts.set(receipt.conflict_id, this.conflictValue); }
        if (this.mode === 'unknown') throw Object.assign(new Error('Synthetic response loss'), { code: 'RESULT_UNKNOWN' });
        if (this.mode === 'delay') await new Promise(resolve => { this.release = resolve; });
        if (this.mode === 'invalid') receipt.request_sha256 = '0'.repeat(64);
        return { receipts: [structuredClone(receipt)], cursor: 'unconsumed-server-hint' };
      }
      async conflict(id) {
        if (this.conflictsRevoked) throw Object.assign(new Error('Conflict missing'), { code: 'NOT_FOUND' });
        const conflict = this.conflicts.get(id);
        if (!conflict) throw Object.assign(new Error('Conflict missing'), { code: 'NOT_FOUND' });
        return structuredClone(conflict);
      }
      async resolve(id, body, opId) {
        this.resolutions.push({ id, body: structuredClone(body), opId });
        if (body.base_revision !== this.serverRevision) throw Object.assign(new Error('Synthetic stale CAS'), { code: 'REVISION_CONFLICT' });
        const conflict = this.conflicts.get(id);
        if (!conflict) throw Object.assign(new Error('Conflict missing'), { code: 'NOT_FOUND' });
        const receipt = { op_id: opId, operation: 'resolveConflict', request_sha256: await lib.syncFingerprint({ conflict_id: id, body }),
          status: 'applied', object_id: body.resolution === 'copy_to_new_note' ? crypto.randomUUID() : conflict.object_id,
          revision: 3, conflict_id: null, task_id: null };
        this.receipts.set(opId, receipt);
        conflict.resolved = true;
        return receipt;
      }
    }
    window.create = async (name, limits = {}, runtimeOptions = {}, scope = 'drafts', transportOptions = {}) => {
      window.cache = new NativeCache({ enabled: true, recordingEnabled: true, databaseName: name,
        limits: { draftBytes: 2000000, draftOperations: 100, recordingBytes: 2000000, ...limits } });
      cache.bind({ ownerId: ids.owner, deviceId: ids.device, sessionId: ids.session });
      window.transport = new Transport(transportOptions);
      window.runtime = new lib.OfflineSyncRuntime(cache, transport, { enabled: true, ...runtimeOptions });
      if (scope === 'learning') await runtime.unlockLearning('synthetic-password');
      else if (scope === 'drafts') await runtime.unlock('synthetic-password');
    };
    window.learningReference = (revision = 1) => ({ scope: 'learning', objectType: 'review_document',
      objectId: ids.document, revision, generation: 1, contentRevisionId: revision === 1 ? ids.revisionA : ids.revisionB, ancestry });
    window.hydrate = (reference, text) => async () => ({ objectId: reference.objectId,
      revision: reference.revision, contentRevisionId: reference.contentRevisionId, bytes: new TextEncoder().encode(text) });
    window.records = async name => {
      const database = await lib.openCacheDatabase(name);
      try {
        return await new Promise((resolve, reject) => {
          const tx = database.transaction(['records'], 'readonly');
          const request = tx.objectStore('records').getAll();
          tx.oncomplete = () => resolve(request.result);
          tx.onabort = () => reject(tx.error);
        });
      } finally { database.close(); }
    };
    window.editable = async (name, limits = {}, mountPanel = false) => {
      await create(name, limits); transport.mode = 'conflict';
      const reference = await runtime.enqueue(operation('ORIGINAL FROZEN INPUT'), ancestry, 1);
      await runtime.flush(); await runtime.inspectConflict(reference);
      if (mountPanel) { const mount = document.createElement('div'); document.body.append(mount);
        window.uiRoot = lib.createRoot(mount); uiRoot.render(lib.React.createElement(lib.ConflictPanel, { runtime, reference, generation: 2 })); }
      return reference;
    };
    window.delayEncryption = () => {
      window.nativeEncrypt = crypto.subtle.encrypt.bind(crypto.subtle); window.encryptedReleases = [];
      crypto.subtle.encrypt = (...args) => nativeEncrypt(...args).then(ciphertext => new Promise(resolve => encryptedReleases.push(() => resolve(ciphertext))));
    };
    window.restoreEncryption = () => { if (window.nativeEncrypt) { crypto.subtle.encrypt = nativeEncrypt; window.nativeEncrypt = null; } };
  });
}

async function test(name, run) {
  const started = Date.now();
  try { await run(); results.push({ name, passed: true, elapsed_ms: Date.now() - started }); }
  catch (error) { results.push({ name, passed: false, elapsed_ms: Date.now() - started, error: error.message }); }
  finally { await page.evaluate(() => { if (window.nativeTransaction) { IDBDatabase.prototype.transaction = nativeTransaction; window.nativeTransaction = null; } window.restoreEncryption?.(); window.encryptedReleases?.forEach(release => release()); window.uiRoot?.unmount(); window.uiRoot = null; window.runtime?.close(); }).catch(() => {}); }
}

try {
  await install();
  await test('default-disabled-runtime', async () => {
    assert.equal(await page.evaluate(async () => {
      await create('pr08b-disabled');
      const disabled = new lib.OfflineSyncRuntime(cache, transport);
      try { await disabled.unlock('synthetic'); } catch (error) { return error.code; }
    }), 'DISABLED');
  });
  await test('encrypted-offline-queue-and-frozen-input', async () => {
    const result = await page.evaluate(async () => {
      await create('pr08b-encrypted');
      const op = operation();
      const promise = runtime.enqueue(op, ancestry, 1);
      op.payload.body_markdown = 'MUTATED CALLER';
      const reference = await promise;
      const restored = await runtime.readDraft(reference);
      const stored = await records('pr08b-encrypted');
      return { text: restored.payload.body_markdown, count: stored.length,
        leaked: JSON.stringify(stored).includes('SYNTHETIC PRIVATE'), state: runtime.snapshot().pending[0].state };
    });
    assert.deepEqual(result, { text: 'SYNTHETIC PRIVATE LOCAL TEXT', count: 1, leaked: false, state: 'local_only' });
  });
  await test('applied-receipt-cleans-exact-generation-with-consumed-watermark', async () => {
    const result = await page.evaluate(async () => {
      await create('pr08b-applied');
      await runtime.enqueue(operation(), ancestry, 1);
      await runtime.flush();
      return { count: (await records('pr08b-applied')).length, cursor: runtime.snapshot().cursor, pushes: transport.pushes.length };
    });
    assert.deepEqual(result, { count: 0, cursor: 'watermark-0', pushes: 1 });
  });
  await test('late-ack-preserves-newer-editor-generation', async () => {
    await page.evaluate(async () => {
      await create('pr08b-late-ack'); transport.mode = 'delay';
      await runtime.enqueue(operation('OLDER'), ancestry, 1);
      window.flushing = runtime.flush();
    });
    await page.waitForFunction(() => typeof transport.release === 'function');
    const result = await page.evaluate(async () => {
      const newer = await runtime.enqueue(operation('NEWER'), ancestry, 2);
      transport.release(); await flushing;
      return { pending: runtime.snapshot().pending.map(row => row.reference.generation),
        text: (await runtime.readDraft(newer)).payload.body_markdown, count: (await records('pr08b-late-ack')).length };
    });
    assert.deepEqual(result, { pending: [2], text: 'NEWER', count: 1 });
  });
  await test('lost-ack-reload-queries-original-receipt-without-resend', async () => {
    const receipt = await page.evaluate(async () => {
      await create('pr08b-reload'); transport.mode = 'unknown';
      const op = operation(); await runtime.enqueue(op, ancestry, 1); await runtime.flush();
      const result = await transport.receipt(op.op_id); runtime.close(); return result;
    });
    await install();
    const result = await page.evaluate(async saved => {
      await create('pr08b-reload'); transport.receipts.set(saved.op_id, saved);
      const before = runtime.snapshot().pending.length; await runtime.flush();
      return { before, after: runtime.snapshot().pending.length, pushes: transport.pushes.length, records: (await records('pr08b-reload')).length };
    }, receipt);
    assert.deepEqual(result, { before: 1, after: 0, pushes: 0, records: 0 });
  });
  await test('invalid-receipt-retains-unique-local-content', async () => {
    const result = await page.evaluate(async () => {
      await create('pr08b-invalid'); transport.mode = 'invalid';
      const reference = await runtime.enqueue(operation(), ancestry, 1); await runtime.flush();
      return { error: runtime.snapshot().pending[0].error, count: (await records('pr08b-invalid')).length,
        text: (await runtime.readDraft(reference)).payload.body_markdown };
    });
    assert.deepEqual(result, { error: 'INVALID_RECEIPT', count: 1, text: 'SYNTHETIC PRIVATE LOCAL TEXT' });
  });
  await test('conflict-preserves-both-and-explicit-merge-cleans-receipted-branches', async () => {
    const result = await page.evaluate(async () => {
      await create('pr08b-conflict'); transport.mode = 'conflict';
      const reference = await runtime.enqueue(operation('LOCAL BRANCH'), ancestry, 1); await runtime.flush();
      const conflict = await runtime.inspectConflict(reference);
      const before = (await records('pr08b-conflict')).length;
      const merged = operation('EXPLICIT MERGE', 2);
      await runtime.resolve(reference, { resolution: 'edited_merge', base_revision: 2, merged_operation: merged }, 2);
      return { local: conflict.local_operation.payload.body_markdown, server: conflict.server_content.body_markdown,
        before, after: (await records('pr08b-conflict')).length, resolutions: transport.resolutions.length };
    });
    assert.deepEqual(result, { local: 'LOCAL BRANCH', server: 'SYNTHETIC CLOUD TEXT', before: 1, after: 0, resolutions: 1 });
  });
  await test('quota-rejection-never-reports-local-save', async () => {
    const result = await page.evaluate(async () => {
      await create('pr08b-quota', { draftBytes: 32 });
      let code = null;
      try { await runtime.enqueue(operation('TOO LARGE'), ancestry, 1); } catch (error) { code = error.code; }
      return { code, pending: runtime.snapshot().pending.length, count: (await records('pr08b-quota')).length };
    });
    assert.deepEqual(result, { code: 'QUOTA_EXCEEDED', pending: 0, count: 0 });
  });
  await test('full-draft-operation-quota-pairs-one-durable-resolution-and-recovers-lost-ack', async () => {
    const result = await page.evaluate(async () => {
      const name = 'pr08b-full-draft-quota';
      const reference = await editable(name, { draftOperations: 1 });
      await expectCode(runtime.enqueue(operation('UNRELATED NEW DRAFT'), ancestry, 2), 'QUOTA_EXCEEDED');
      const peerCache = new cache.constructor({ enabled: true, databaseName: name,
        limits: { draftBytes: 2000000, draftOperations: 1, recordingBytes: 2000000 } });
      peerCache.bind({ ownerId: ids.owner, deviceId: ids.device, sessionId: ids.session });
      const peer = new lib.OfflineSyncRuntime(peerCache, transport, { enabled: true });
      await peer.unlock('synthetic-password');
      await peer.flush();

      // Both runtimes see the same persistent original and race to put a different resolution.
      let arriving = 0; let release;
      const both = new Promise(resolve => { release = resolve; });
      for (const instance of [cache, peerCache]) {
        const put = instance.put.bind(instance);
        instance.put = async (write, bytes) => {
          if (write.resolutionOf) { if (++arriving === 2) release(); await both; }
          return put(write, bytes);
        };
      }
      const send = transport.resolve.bind(transport);
      let durableBeforeSend;
      transport.resolve = async (...args) => {
        durableBeforeSend = await records(name);
        await send(...args);
        throw Object.assign(new Error('Lost resolution response after commit'), { code: 'RESULT_UNKNOWN' });
      };
      const attempts = await Promise.allSettled([
        runtime.resolve(reference, { resolution: 'use_server', base_revision: 2 }, 2),
        peer.resolve(reference, { resolution: 'copy_to_new_note', base_revision: 2, destination_class_session_id: ids.classroom }, 3),
      ]);
      const persisted = await records(name);
      const sent = transport.resolutions[0];
      const exact = persisted.filter(value => value.header.resolutionOf?.operationId === reference.operationId);
      const receipts = [...transport.receipts]; const conflicts = [...transport.conflicts];
      const before = {
        failures: attempts.filter(value => value.status === 'rejected').map(value => value.reason.code),
        count: persisted.length, sent: transport.resolutions.length, original: persisted.some(value => value.header.operationId === reference.operationId),
        durable: durableBeforeSend?.some(value => value.header.operationId === sent?.opId),
        exact: exact.length === 1 && exact[0].header.operationId === sent?.opId && exact[0].header.resolutionOf.generation === 1
          && exact[0].header.resolutionOf.revision === reference.revision,
      };
      peer.close(); runtime.close();
      await create(name, { draftOperations: 1 }, {}, 'drafts', { receipts, conflicts });
      await runtime.resolve(reference, sent.body, 4);
      return { before, sentAfterReopen: transport.resolutions.length, pending: runtime.snapshot().pending.length, remaining: (await records(name)).length };
    });
    assert.deepEqual(result, { before: { failures: ['RESULT_UNKNOWN'], count: 2, sent: 1, original: true, durable: true, exact: true },
      sentAfterReopen: 0, pending: 0, remaining: 0 });
  });
  await test('resolution-still-respects-draft-byte-cap-and-retains-original-ciphertext', async () => {
    const result = await page.evaluate(async () => {
      const name = 'pr08b-resolution-byte-limits';
      const reference = await editable(name, { draftOperations: 1 });
      const [original] = await records(name);
      const receipts = [...transport.receipts]; const conflicts = [...transport.conflicts];
      runtime.close();
      await create(name, { draftOperations: 1, draftBytes: original.bytes }, {}, 'drafts', { receipts, conflicts });
      await runtime.flush();
      await expectCode(runtime.resolve(reference, { resolution: 'use_server', base_revision: 2 }, 2), 'QUOTA_EXCEEDED');
      const persisted = await records(name);
      return { sent: transport.resolutions.length, count: persisted.length, sameId: persisted[0]?.id === original.id,
        retained: (await runtime.readDraft(reference)).payload.body_markdown };
    });
    assert.deepEqual(result, { sent: 0, count: 1, sameId: true, retained: 'ORIGINAL FROZEN INPUT' });
  });
  await test('reopened-resolution-can-be-corrected-only-after-exact-definitive-rejection', async () => {
    const runScenario = preexisting => page.evaluate(async preexisting => {
      const name = `pr08b-rejected-resolution-${preexisting}`;
      const limits = { draftOperations: 2 };
      const reference = await editable(name, limits);
      const rejected = { resolution: 'edited_merge', base_revision: 2,
        merged_operation: operation('REJECTED MERGE UNIQUE INPUT', 2) };
      const storedRecovery = preexisting ? await runtime.saveConflictDraft(reference, rejected.merged_operation.payload.body_markdown,
        rejected.merged_operation.payload.personal_note, crypto.randomUUID(), 1) : null;
      const rejectedResponse = () => Object.assign(new Error('Rejected merge'), { code: 'VALIDATION_FAILED', status: 422 });
      transport.resolve = async () => { throw rejectedResponse(); };
      await runtime.resolve(reference, rejected, 2);
      const [frozen] = (await records(name)).filter(value => value.header.resolutionOf);
      const receipts = [...transport.receipts]; const conflicts = [...transport.conflicts];
      runtime.close();
      await create(name, limits, {}, 'drafts', { receipts, conflicts });

      const submit = transport.resolve.bind(transport);
      const attempted = [];
      let acknowledgeRejection = false;
      let atomicReplacement = false;
      transport.resolve = async (id, body, key) => {
        attempted.push(key);
        if (body.resolution === 'edited_merge') {
          if (acknowledgeRejection) throw rejectedResponse();
          throw Object.assign(new Error('No response to replay'), { code: 'RESULT_UNKNOWN' });
        }
        const current = await records(name);
        atomicReplacement = current.length === 3 && current.some(value => value.header.operationId === reference.operationId)
          && current.some(value => value.header.operationId === key && value.header.generation === 3)
          && current.some(value => value.header.objectType === 'sync_editor_recovery')
          && current.every(value => value.header.operationId !== frozen.header.operationId);
        return submit(id, body, key);
      };
      const replacement = { resolution: 'use_server', base_revision: 2 };
      await expectCode(runtime.resolve(reference, replacement, 2), 'RESULT_UNKNOWN');
      const stillFrozen = (await records(name)).some(value => value.id === frozen.id && value.header.operationId === frozen.header.operationId);
      acknowledgeRejection = true;
      await runtime.resolve(reference, replacement, 2);
      const recovery = await runtime.readConflictDrafts(reference);
      return {
        stillFrozen, atomicReplacement,
        originalKeyRetried: attempted.length === 3 && attempted[0] === frozen.header.operationId && attempted[1] === frozen.header.operationId,
        replacementHasNewKey: attempted[2] !== frozen.header.operationId,
        pending: runtime.snapshot().pending.length,
        recovery: recovery.map(value => [value.body, value.note]), remaining: (await records(name)).length,
        reusedRecovery: !storedRecovery || recovery[0]?.reference.operationId === storedRecovery.operationId,
      };
    }, preexisting);
    for (const preexisting of [false, true]) {
      assert.deepEqual(await runScenario(preexisting), { stillFrozen: true, atomicReplacement: true,
        originalKeyRetried: true, replacementHasNewKey: true, pending: 0,
        recovery: [['REJECTED MERGE UNIQUE INPUT', 'Synthetic personal note']], remaining: 1, reusedRecovery: true });
    }
  });
  await test('rejected-personal-merge-retains-its-exact-revision-authority', async () => {
    const result = await page.evaluate(async () => {
      const name = 'pr08b-rejected-merge-exact-authority';
      const reference = await editable(name);
      const merged = operation('Body based on a separately revocable revision', 2);
      merged.payload.base_content_revision_id = ids.revisionB;
      await runtime.saveConflictDraft(reference, 'Readable independent editor branch', '', crypto.randomUUID(), 1);
      const resolve = transport.resolve.bind(transport);
      transport.resolve = (id, body, key) => {
        if (body.resolution === 'edited_merge') {
          throw Object.assign(new Error('Rejected merge'), { code: 'VALIDATION_FAILED', status: 422 });
        }
        return resolve(id, body, key);
      };
      await runtime.resolve(reference, { resolution: 'edited_merge', base_revision: 2, merged_operation: merged }, 2);
      await runtime.resolve(reference, { resolution: 'use_server', base_revision: 2 }, 3);
      const draft = (await runtime.readConflictDrafts(reference)).find(value => value.reference.contentRevisionId === ids.revisionB);
      if (draft.body !== merged.payload.body_markdown || draft.reference.contentRevisionId !== ids.revisionB) {
        throw new Error('Recovered merge did not retain its exact revision');
      }
      const before = (await records(name)).find(value => value.header.contentRevisionId === ids.revisionB);
      transport.revokedRevisions.add(ids.revisionB);
      await expectCode(runtime.readConflictDrafts(reference), 'AUTHORITY_UNAVAILABLE');
      await runtime.unlock('synthetic-password');
      const states = (await runtime.listConflictDraftSources()).map(value => value.state).sort();
      const readable = (await runtime.readConflictDrafts(reference)).map(value => value.body);
      const after = (await records(name)).find(value => value.header.contentRevisionId === ids.revisionB);
      return { states, readable, sameId: before.id === after.id,
        sameCiphertext: JSON.stringify([...new Uint8Array(before.ciphertext)]) === JSON.stringify([...new Uint8Array(after.ciphertext)]) };
    });
    assert.deepEqual(result, { states: ['available', 'restricted'], readable: ['Readable independent editor branch'],
      sameId: true, sameCiphertext: true });
  });
  await test('legacy-pending-decisions-resolve-only-through-their-exact-receipts', async () => {
    const runScenario = scenario => page.evaluate(async scenario => {
      const name = `pr08b-legacy-decision-${scenario}`;
      const limits = { draftOperations: scenario === 'multiple' ? 3 : 2 };
      const reference = await editable(name, limits);
      const conflict = await runtime.inspectConflict(reference);
      const first = { resolution: 'copy_to_new_note', base_revision: 2, destination_class_session_id: ids.classroom };
      const replacement = { resolution: 'use_server', base_revision: 2 };
      const saveLegacy = async (body, generation) => {
        const opId = crypto.randomUUID();
        const operation = { ...conflict.local_operation, op_id: opId, base_revision: body.base_revision,
          payload: { ...conflict.local_operation.payload, base_revision: body.base_revision } };
        const requestHash = await lib.syncFingerprint({ conflict_id: conflict.id, body });
        const legacy = { ...reference, operationId: opId, revision: body.base_revision, generation };
        const envelope = { schema: 1, kind: 'resolve', operation, original: reference, conflictId: conflict.id, resolution: body, requestHash };
        await cache.put(legacy, new TextEncoder().encode(JSON.stringify(envelope)));
        return { ...legacy, requestHash };
      };
      const old = await saveLegacy(first, 2);
      if (scenario === 'multiple') await saveLegacy(replacement, 3);
      const receipts = [...transport.receipts]; const conflicts = [...transport.conflicts];
      runtime.close();
      await create(name, limits, {}, 'drafts', { receipts, conflicts });
      const send = transport.resolve.bind(transport);
      const fetchConflict = transport.conflict.bind(transport);
      const posts = [];
      let gone = false;
      let rejected = false;
      let reauthorized = false;
      let replacedBeforeSend = false;
      transport.conflict = async id => {
        if (rejected && id === conflict.id) reauthorized = true;
        return fetchConflict(id);
      };
      transport.resolve = async (id, body, key) => {
        posts.push({ resolution: body.resolution, key });
        if (key === old.operationId) {
          if (gone) { rejected = true; throw Object.assign(new Error('Destination gone'), { code: 'GONE', status: 410 }); }
          throw Object.assign(new Error('Lost old response'), { code: 'RESULT_UNKNOWN' });
        }
        const persisted = await records(name);
        replacedBeforeSend = persisted.length === 2 && persisted.every(value => value.header.operationId !== old.operationId)
          && persisted.some(value => value.header.operationId === reference.operationId)
          && persisted.some(value => value.header.operationId === key && value.header.generation === 3
            && value.header.resolutionOf?.operationId === reference.operationId);
        return send(id, body, key);
      };
      await expectCode(runtime.resolve(reference, replacement, 2), 'RESULT_UNKNOWN');
      if (scenario === 'multiple') {
        await runtime.flush();
        return { posted: posts.length, retained: (await records(name)).length };
      }
      const noReboundBeforeProof = posts.length === 1 && posts[0].key === old.operationId && (await records(name)).length === 2;
      if (scenario === 'applied') {
        transport.receipts.set(old.operationId, { op_id: old.operationId, operation: 'resolveConflict', request_sha256: old.requestHash,
          status: 'applied', object_id: crypto.randomUUID(), revision: 3, conflict_id: null, task_id: null });
        transport.conflicts.get(conflict.id).resolved = true;
      } else gone = true;
      await runtime.resolve(reference, replacement, 2);
      return { noReboundBeforeProof, reauthorized, replacedBeforeSend, postKinds: posts.map(value => value.resolution),
        pending: runtime.snapshot().pending.length, retained: (await records(name)).length };
    }, scenario);
    assert.deepEqual(await runScenario('multiple'), { posted: 0, retained: 3 });
    assert.deepEqual(await runScenario('applied'), { noReboundBeforeProof: true, reauthorized: false, replacedBeforeSend: false,
      postKinds: ['copy_to_new_note'], pending: 0, retained: 0 });
    assert.deepEqual(await runScenario('gone'), { noReboundBeforeProof: true, reauthorized: true, replacedBeforeSend: true,
      postKinds: ['copy_to_new_note', 'copy_to_new_note', 'use_server'], pending: 0, retained: 0 });
  });
  await test('authorized-legacy-decision-survives-revoked-newer-edit-of-same-document', async () => {
    const result = await page.evaluate(async () => {
      const name = 'pr08b-legacy-decision-revoked-sibling';
      await create(name);
      transport.mode = 'conflict';
      const original = await runtime.enqueue(operation('AUTHORIZED EARLIER EDIT', 1, ids.revisionA), ancestry, 1);
      await runtime.flush();
      const conflict = await runtime.inspectConflict(original);
      const body = { resolution: 'use_server', base_revision: 2 };
      const opId = crypto.randomUUID();
      const resolving = { ...conflict.local_operation, op_id: opId, base_revision: 2,
        payload: { ...conflict.local_operation.payload, base_revision: 2 } };
      const legacy = { ...original, operationId: opId, revision: 2, generation: 2 };
      await cache.put(legacy, new TextEncoder().encode(JSON.stringify({ schema: 1, kind: 'resolve', operation: resolving,
        original, conflictId: conflict.id, resolution: body, requestHash: await lib.syncFingerprint({ conflict_id: conflict.id, body }) })));
      const newer = await runtime.enqueue(operation('REVOKED NEWER EDIT', 1, ids.revisionB), ancestry, 3);
      const receipts = [...transport.receipts]; const conflicts = [...transport.conflicts];
      runtime.close();
      await create(name, {}, {}, 'drafts', { receipts, conflicts, revokedRevisions: [ids.revisionB] });
      await runtime.resolve(original, body, 4);
      const retained = await records(name);
      const pending = runtime.snapshot().pending;
      return { exactSend: transport.resolutions.length === 1 && transport.resolutions[0].opId === opId,
        retained: retained.length === 1 && retained[0].header.operationId === newer.operationId,
        restricted: pending.length === 1 && pending[0].reference.operationId === newer.operationId && pending[0].state === 'restricted' };
    });
    assert.deepEqual(result, { exactSend: true, retained: true, restricted: true });
  });
  await test('bootstrap-authority-loss-retains-ciphertext-and-blocks-replay', async () => {
    const result = await page.evaluate(async () => {
      await create('pr08b-authority');
      const reference = await runtime.enqueue(operation(), ancestry, 1);
      transport.projections = [];
      await expectCode(runtime.flush(), 'LOCKED');
      const locked = !runtime.snapshot().unlocked;
      await runtime.unlock('synthetic-password');
      let code = null;
      try { await runtime.readDraft(reference); } catch (error) { code = error.code; }
      return { state: runtime.snapshot().pending[0].state, count: (await records('pr08b-authority')).length,
        pushes: transport.pushes.length, code, locked };
    });
    assert.deepEqual(result, { state: 'restricted', count: 1, pushes: 0, code: 'AUTHORITY_UNAVAILABLE', locked: true });
  });
  await test('deletion-before-unlock-preserves-restricted-draft-ciphertext', async () => {
    const result = await page.evaluate(async () => {
      await create('pr08b-delete');
      const reference = await runtime.enqueue(operation(), ancestry, 1);
      transport.pages = [{ items: [{ event_id: crypto.randomUUID(), object_id: ids.deletion,
        object_type: 'deletion', revision: 1, deleted: true }], next_cursor: 'watermark-deleted', has_more: false }];
      await runtime.unlock('synthetic-password');
      let code = null;
      try { await runtime.readDraft(reference); } catch (error) { code = error.code; }
      return { state: runtime.snapshot().pending[0].state, count: (await records('pr08b-delete')).length,
        code, cursor: runtime.snapshot().cursor, pushes: transport.pushes.length };
    });
    assert.deepEqual(result, { state: 'restricted', count: 1, code: 'AUTHORITY_UNAVAILABLE', cursor: 'watermark-deleted', pushes: 0 });
  });
  await test('concurrent-flush-shares-one-send', async () => {
    await page.evaluate(async () => {
      await create('pr08b-concurrent'); transport.mode = 'delay';
      await runtime.enqueue(operation(), ancestry, 1);
      window.firstFlush = runtime.flush(); window.secondFlush = runtime.flush();
    });
    await page.waitForFunction(() => typeof transport.release === 'function');
    const result = await page.evaluate(async () => {
      transport.release(); await Promise.all([firstFlush, secondFlush]);
      return { pushes: transport.pushes.length, pending: runtime.snapshot().pending.length };
    });
    assert.deepEqual(result, { pushes: 1, pending: 0 });
  });
  for (const resolution of ['edited_merge', 'copy_to_new_note', 'use_server']) {
    await test('conflict-panel-explicit-' + resolution, async () => {
      await page.evaluate(async choice => {
        await create('pr08b-ui-' + choice); transport.mode = 'conflict';
        const reference = await runtime.enqueue(operation('UI LOCAL BRANCH'), ancestry, 1);
        await runtime.flush();
        const mount = document.createElement('div'); document.body.append(mount);
        window.uiRoot = lib.createRoot(mount);
        uiRoot.render(lib.React.createElement(lib.ConflictPanel, { runtime, reference, generation: 2,
          destinations: [{ id: ids.classroom, name: 'Synthetic target classroom' }] }));
      }, resolution);
      await page.getByRole('heading', { name: '云端版本 2' }).waitFor();
      await page.locator(`input[value="${resolution}"]`).check();
      if (resolution === 'edited_merge') {
        await page.getByRole('textbox', { name: '合并正文', exact: true }).fill('UI EXPLICIT MERGE');
        await page.getByRole('textbox', { name: '个人补充', exact: true }).fill('UI EXPLICIT NOTE');
      }
      if (resolution === 'copy_to_new_note') {
        await page.getByRole('combobox', { name: '目标课堂', exact: true }).selectOption({ label: 'Synthetic target classroom' });
      }
      await page.getByRole('button', { name: '确认处理', exact: true }).click();
      await page.getByRole('status').filter({ hasText: '冲突处理已由服务器确认。' }).waitFor();
      const result = await page.evaluate(() => ({ calls: transport.resolutions,
        original: transport.pushes[0].operation, pending: runtime.snapshot().pending.length }));
      assert.equal(result.calls.length, 1);
      assert.equal(result.calls[0].body.resolution, resolution);
      assert.equal(result.calls[0].body.base_revision, 2);
      assert.notEqual(result.calls[0].opId, result.original.op_id);
      assert.equal(result.pending, 0);
      if (resolution === 'edited_merge') {
        assert.equal(result.calls[0].body.merged_operation.payload.body_markdown, 'UI EXPLICIT MERGE');
        assert.equal(result.calls[0].body.merged_operation.payload.personal_note, 'UI EXPLICIT NOTE');
        assert.equal(result.calls[0].body.merged_operation.payload.base_revision, 2);
        assert.notEqual(result.calls[0].body.merged_operation.op_id, result.original.op_id);
      }
    });
  }
  await test('conflict-panel-lock-removes-visible-private-input', async () => {
    await page.evaluate(async () => {
      await create('pr08b-ui-lock'); transport.mode = 'conflict';
      const reference = await runtime.enqueue(operation('UI PRIVATE LOCK SENTINEL'), ancestry, 1);
      await runtime.flush();
      const mount = document.createElement('div'); document.body.append(mount);
      window.uiRoot = lib.createRoot(mount);
      uiRoot.render(lib.React.createElement(lib.ConflictPanel, { runtime, reference, generation: 2 }));
    });
    await page.getByRole('heading', { name: '云端版本 2' }).waitFor();
    await page.getByRole('textbox', { name: '合并正文', exact: true }).fill('UNSENT PRIVATE EDIT');
    await page.evaluate(async () => { cache.lock(); await runtime.awaitEditorDraftSaves(); });
    await page.getByRole('status').filter({ hasText: '同步已锁定，请重新验证身份。' }).waitFor();
    assert.equal(await page.locator('textarea').count(), 0);
    assert.equal((await page.locator('body').innerText()).includes('UI PRIVATE LOCK SENTINEL'), false);
    assert.equal((await page.locator('body').innerText()).includes('UNSENT PRIVATE EDIT'), false);
    assert.equal(await page.evaluate(async () => (await records('pr08b-ui-lock')).length), 2);
  });
  await test('conflict-panel-rechecks-new-cloud-cas-and-preserves-merge-input', async () => {
    await page.evaluate(async () => {
      await create('pr08b-ui-recheck'); transport.mode = 'conflict';
      const reference = await runtime.enqueue(operation('ORIGINAL UI DRAFT'), ancestry, 1);
      await runtime.flush();
      const mount = document.createElement('div'); document.body.append(mount);
      window.uiRoot = lib.createRoot(mount);
      uiRoot.render(lib.React.createElement(lib.ConflictPanel, { runtime, reference, generation: 2 }));
    });
    await page.getByRole('heading', { name: '云端版本 2' }).waitFor();
    await page.getByRole('textbox', { name: '合并正文', exact: true }).fill('MERGE INPUT SURVIVES RECHECK');
    await page.evaluate(() => { transport.serverRevision = 3; });
    await page.getByRole('button', { name: '确认处理', exact: true }).click();
    await page.getByRole('status').filter({ hasText: '操作结果仍待确认，草稿已保留。' }).waitFor();
    await page.getByRole('button', { name: '重新核对云端版本', exact: true }).click();
    await page.getByRole('heading', { name: '云端版本 3' }).waitFor();
    assert.equal(await page.getByRole('textbox', { name: '合并正文', exact: true }).inputValue(), 'MERGE INPUT SURVIVES RECHECK');
    await page.getByRole('button', { name: '确认处理', exact: true }).click();
    await page.getByRole('status').filter({ hasText: '冲突处理已由服务器确认。' }).waitFor();
    const result = await page.evaluate(() => ({ pending: runtime.snapshot().pending.length,
      last: transport.resolutions.at(-1).body, count: (transport.resolutions).length }));
    assert.equal(result.pending, 0);
    assert.equal(result.last.base_revision, 3);
    assert.equal(result.last.merged_operation.payload.body_markdown, 'MERGE INPUT SURVIVES RECHECK');
  });
  await test('background-deletion-poll-locks-private-conflict-view', async () => {
    await page.evaluate(async () => {
      await create('pr08b-background-deletion', {}, { pollIntervalMs: 30 }); transport.mode = 'conflict';
      const reference = await runtime.enqueue(operation('BACKGROUND DELETION PRIVATE TEXT'), ancestry, 1);
      await runtime.flush();
      const mount = document.createElement('div'); document.body.append(mount);
      window.uiRoot = lib.createRoot(mount);
      uiRoot.render(lib.React.createElement(lib.ConflictPanel, { runtime, reference, generation: 2 }));
    });
    await page.getByRole('heading', { name: '云端版本 2' }).waitFor();
    await page.evaluate(() => {
      transport.pages.push({ items: [{ event_id: crypto.randomUUID(), object_id: ids.deletion,
        object_type: 'deletion', revision: 1, deleted: true }], next_cursor: 'background-deleted', has_more: false });
    });
    await page.getByRole('status').filter({ hasText: '同步已锁定，请重新验证身份。' }).waitFor();
    assert.equal((await page.locator('body').innerText()).includes('BACKGROUND DELETION PRIVATE TEXT'), false);
    assert.deepEqual(await page.evaluate(async () => ({ unlocked: runtime.snapshot().unlocked,
      records: (await records('pr08b-background-deletion')).length, pushes: transport.pushes.length })),
    { unlocked: false, records: 1, pushes: 1 });
  });
  await test('bootstrap-advancement-revokes-visible-conflict-before-replay', async () => {
    await page.evaluate(async () => {
      await create('pr08b-bootstrap-revocation'); transport.mode = 'conflict';
      const reference = await runtime.enqueue(operation('BOOTSTRAP REVOKED PRIVATE TEXT'), ancestry, 1);
      await runtime.flush();
      const mount = document.createElement('div'); document.body.append(mount);
      window.uiRoot = lib.createRoot(mount);
      uiRoot.render(lib.React.createElement(lib.ConflictPanel, { runtime, reference, generation: 2 }));
    });
    await page.getByRole('heading', { name: '云端版本 2' }).waitFor();
    await page.getByRole('textbox', { name: '合并正文', exact: true }).fill('REVOKED UNSENT INPUT');
    await page.evaluate(() => runtime.awaitEditorDraftSaves());
    await page.evaluate(async () => {
      transport.projections = transport.projections.filter(value => value.object_id !== ids.document);
      transport.bootstrap = async () => ({ projection_complete: true, maximum_projection_count: 10000,
        projections: structuredClone(transport.projections), watermark: 'watermark-after-revocation' });
      transport.changes = async after => ({ items: after === 'watermark-0' ? [{ event_id: crypto.randomUUID(),
        object_id: ids.document, object_type: 'review_document', revision: 1, deleted: true }] : [],
        next_cursor: 'watermark-after-revocation', has_more: false });
      try { await runtime.flush(); } catch (error) { if (error.code !== 'LOCKED') throw error; }
    });
    await page.getByRole('status').filter({ hasText: '同步已锁定，请重新验证身份。' }).waitFor();
    assert.equal(await page.locator('textarea').count(), 0);
    const visible = await page.locator('body').innerText();
    assert.equal(visible.includes('BOOTSTRAP REVOKED PRIVATE TEXT'), false);
    assert.equal(visible.includes('REVOKED UNSENT INPUT'), false);
    assert.deepEqual(await page.evaluate(async () => ({ unlocked: runtime.snapshot().unlocked,
      records: (await records('pr08b-bootstrap-revocation')).length, pushes: transport.pushes.length })),
    { unlocked: false, records: 2, pushes: 1 });
  });
  await test('partial-source-revocation-clears-conflict-and-restricts-draft-after-unlock-and-page-reload', async () => {
    const reference = await page.evaluate(async () => {
      await create('pr08b-partial-source'); transport.mode = 'conflict';
      const reference = await runtime.enqueue(operation('SOURCE B UNIQUE DRAFT', 1, ids.revisionB), ancestry, 1);
      await runtime.flush();
      transport.conflictValue.server_content = await transport.reviewRevision(ids.revisionB);
      const mount = document.createElement('div'); document.body.append(mount);
      window.uiRoot = lib.createRoot(mount);
      uiRoot.render(lib.React.createElement(lib.ConflictPanel, { runtime, reference, generation: 2 }));
      return reference;
    });
    await page.getByRole('heading', { name: '云端版本 2' }).waitFor();
    assert.equal((await page.locator('body').innerText()).includes('REVOKED SOURCE B BODY'), true);
    await page.getByRole('textbox', { name: '合并正文', exact: true }).fill('SOURCE B UNSENT MERGE');
    await page.evaluate(() => runtime.awaitEditorDraftSaves());
    await page.evaluate(async () => {
      transport.revokedRevisions.add(ids.revisionB); transport.conflictsRevoked = true;
      transport.pages.push({ items: [{ event_id: crypto.randomUUID(), object_id: ids.document,
        object_type: 'review_document', revision: 1, deleted: false }], next_cursor: 'partial-source-revoked', has_more: false });
      await runtime.pollChanges();
    });
    await page.getByRole('status').filter({ hasText: '同步已锁定，请重新验证身份。' }).waitFor();
    const visible = await page.locator('body').innerText();
    for (const marker of ['REVOKED SOURCE B BODY', 'SOURCE B UNIQUE DRAFT', 'SOURCE B UNSENT MERGE']) {
      assert.equal(visible.includes(marker), false);
    }
    assert.equal(await page.locator('textarea').count(), 0);
    const reopened = await page.evaluate(async reference => {
      await runtime.unlock('synthetic-password');
      await expectCode(runtime.readDraft(reference), 'AUTHORITY_UNAVAILABLE');
      return { state: runtime.snapshot().pending[0].state, records: (await records('pr08b-partial-source')).length,
        documentVisible: (await transport.document(ids.document)).id === ids.document,
        allowedBody: (await runtime.readReviewRevision(ids.document, ids.revisionA)).body_markdown };
    }, reference);
    assert.deepEqual(reopened, { state: 'restricted', records: 2, documentVisible: true, allowedBody: 'AUTHORIZED SOURCE A BODY' });
    await page.reload();
    await install();
    const restarted = await page.evaluate(async reference => {
      await create('pr08b-partial-source', {}, {}, 'drafts', { revokedRevisions: [ids.revisionB], conflictsRevoked: true });
      await expectCode(runtime.readDraft(reference), 'AUTHORITY_UNAVAILABLE');
      const state = runtime.snapshot().pending[0].state;
      const allowed = await runtime.enqueue(operation('SOURCE A VALID DRAFT', 1, ids.revisionA), ancestry, 2);
      const stored = await records('pr08b-partial-source');
      return { state, records: stored.length, allowedText: (await runtime.readDraft(allowed)).payload.body_markdown,
        rejectedRevisionRead: transport.revisionReads.includes(ids.revisionB),
        leaked: JSON.stringify(stored).includes('SOURCE B UNIQUE DRAFT') };
    }, reference);
    assert.deepEqual(restarted, { state: 'restricted', records: 3, allowedText: 'SOURCE A VALID DRAFT', rejectedRevisionRead: true, leaked: false });
  });
  await test('partial-source-event-rejects-a-late-exact-revision-response', async () => {
    await page.evaluate(async () => {
      await create('pr08b-late-revision');
      await runtime.enqueue(operation('PRESERVED WHILE AUTHORIZATION IS PENDING'), ancestry, 1);
      const value = await transport.reviewRevision(ids.revisionB);
      transport.reviewRevision = async () => new Promise(resolve => { transport.release = () => resolve(value); });
      window.lateRevision = runtime.readReviewRevision(ids.document, ids.revisionB)
        .then(value => ({ body: value.body_markdown }), error => ({ code: error.code }));
    });
    await page.waitForFunction(() => typeof transport.release === 'function');
    const result = await page.evaluate(async () => {
      transport.pages.push({ items: [{ event_id: crypto.randomUUID(), object_id: ids.document,
        object_type: 'review_document', revision: 1, deleted: false }], next_cursor: 'late-partial-revocation', has_more: false });
      await runtime.pollChanges(); transport.release();
      return { result: await lateRevision, unlocked: runtime.snapshot().unlocked, records: (await records('pr08b-late-revision')).length };
    });
    assert.deepEqual(result, { result: { code: 'LOCKED' }, unlocked: false, records: 1 });
  });
  await test('learning-only-unlock-authorizes-exact-hydration-and-keeps-drafts-locked', async () => {
    const result = await page.evaluate(async () => {
      await create('pr08b-learning-only', {}, {}, 'learning');
      const reference = learningReference();
      await runtime.cacheLearning(reference, hydrate(reference, 'LEARNING PRIVATE TEXT'));
      const text = new TextDecoder().decode(await runtime.readLearning(reference));
      let code = null;
      try { await runtime.enqueue(operation(), ancestry, 1); } catch (error) { code = error.code; }
      return { scopes: runtime.snapshot().unlockedScopes, drafts: runtime.snapshot().unlocked, text, code,
        leaked: JSON.stringify(await records('pr08b-learning-only')).includes('LEARNING PRIVATE TEXT') };
    });
    assert.deepEqual(result, { scopes: ['learning'], drafts: false, text: 'LEARNING PRIVATE TEXT', code: 'LOCKED', leaked: false });
  });
  await test('adding-learning-scope-preserves-pending-drafts', async () => {
    const result = await page.evaluate(async () => {
      await create('pr08b-learning-drafts');
      const draft = await runtime.enqueue(operation('DRAFT REMAINS READABLE'), ancestry, 1);
      await runtime.unlockLearning('synthetic-password');
      const reference = learningReference();
      await runtime.cacheLearning(reference, hydrate(reference, 'LEARNING BODY'));
      return { scopes: runtime.snapshot().unlockedScopes.sort(), draft: (await runtime.readDraft(draft)).payload.body_markdown,
        text: new TextDecoder().decode(await runtime.readLearning(reference)), count: (await records('pr08b-learning-drafts')).length };
    });
    assert.deepEqual(result, { scopes: ['drafts', 'learning'], draft: 'DRAFT REMAINS READABLE', text: 'LEARNING BODY', count: 2 });
  });
  await test('learning-change-invalidates-old-content-and-requires-exact-hydration', async () => {
    const result = await page.evaluate(async () => {
      await create('pr08b-learning-change', {}, {}, 'learning');
      const old = learningReference();
      await runtime.cacheLearning(old, hydrate(old, 'OLD CONTENT'));
      transport.projections.find(value => value.object_id === ids.document).revision = 2;
      transport.pages.push({ items: [{ event_id: crypto.randomUUID(), object_id: ids.document,
        object_type: 'review_document', revision: 2, deleted: false }], next_cursor: 'changed-2', has_more: false });
      await runtime.pollChanges();
      const lockedOnChange = runtime.snapshot().unlockedScopes.length === 0;
      await runtime.unlockLearning('synthetic-password');
      let oldCode = null;
      try { await runtime.readLearning(old); } catch (error) { oldCode = error.code; }
      const current = learningReference(2);
      const empty = await runtime.readLearning(current);
      let mismatchCode = null;
      try { await runtime.cacheLearning(current, hydrate(old, 'WRONG REVISION')); } catch (error) { mismatchCode = error.code; }
      await runtime.cacheLearning(current, hydrate(current, 'CURRENT CONTENT'));
      const text = new TextDecoder().decode(await runtime.readLearning(current));
      transport.pages.push({ items: [{ event_id: crypto.randomUUID(), object_id: ids.document,
        object_type: 'review_document', revision: 2, deleted: false }], next_cursor: 'same-revision-change', has_more: false });
      await runtime.pollChanges();
      const lockedOnSameRevision = runtime.snapshot().unlockedScopes.length === 0;
      await runtime.unlockLearning('synthetic-password');
      return { oldCode, empty, mismatchCode, text, lockedOnChange, lockedOnSameRevision,
        afterSameRevisionChange: await runtime.readLearning(current) };
    });
    assert.deepEqual(result, { oldCode: 'AUTHORITY_UNAVAILABLE', empty: null, mismatchCode: 'INVALID_HYDRATION',
      text: 'CURRENT CONTENT', lockedOnChange: true, lockedOnSameRevision: true, afterSameRevisionChange: null });
  });
  await test('partial-source-revocation-denies-exact-learning-body-and-preserves-valid-source', async () => {
    const result = await page.evaluate(async () => {
      await create('pr08b-partial-learning', {}, {}, 'learning');
      const revoked = { ...learningReference(), contentRevisionId: ids.revisionB };
      await runtime.cacheLearning(revoked, hydrate(revoked, 'SOURCE B CACHED BODY'));
      transport.revokedRevisions.add(ids.revisionB);
      transport.pages.push({ items: [{ event_id: crypto.randomUUID(), object_id: ids.document,
        object_type: 'review_document', revision: 1, deleted: false }], next_cursor: 'learning-partial-revoked', has_more: false });
      await runtime.pollChanges();
      const locked = runtime.snapshot().unlockedScopes.length === 0;
      await runtime.unlockLearning('synthetic-password');
      await expectCode(runtime.readLearning(revoked), 'AUTHORITY_UNAVAILABLE');
      let hydrated = false;
      await expectCode(runtime.cacheLearning(revoked, async () => { hydrated = true; return hydrate(revoked, 'STALE SOURCE B')(); }), 'AUTHORITY_UNAVAILABLE');
      const allowed = learningReference();
      await runtime.cacheLearning(allowed, hydrate(allowed, 'SOURCE A CURRENT BODY'));
      return { locked, hydrated, allowed: new TextDecoder().decode(await runtime.readLearning(allowed)),
        storedRevision: (await records('pr08b-partial-learning'))[0].header.contentRevisionId };
    });
    assert.deepEqual(result, { locked: true, hydrated: false, allowed: 'SOURCE A CURRENT BODY',
      storedRevision: '10000000-0000-4000-8000-000000000010' });
  });
  await test('same-revision-source-revocation-clears-learning-and-retains-encrypted-draft', async () => {
    const result = await page.evaluate(async () => {
      await create('pr08b-source-revocation');
      await runtime.enqueue(operation('UNIQUE SOURCE DRAFT'), ancestry, 1);
      await runtime.unlockLearning('synthetic-password');
      const reference = learningReference();
      await runtime.cacheLearning(reference, hydrate(reference, 'DERIVED PRIVATE CONTENT'));
      transport.projections = transport.projections.filter(value => value.object_id !== ids.document);
      transport.pages.push({ items: [{ event_id: crypto.randomUUID(), object_id: ids.document,
        object_type: 'review_document', revision: 1, deleted: true }], next_cursor: 'source-revoked', has_more: false });
      await runtime.pollChanges();
      const locked = runtime.snapshot().unlockedScopes.length === 0;
      await runtime.unlockLearning('synthetic-password');
      let code = null;
      try { await runtime.readLearning(reference); } catch (error) { code = error.code; }
      const stored = await records('pr08b-source-revocation');
      return { locked, code, scopes: stored.map(value => value.header.scope),
        leaked: JSON.stringify(stored).includes('UNIQUE SOURCE DRAFT') };
    });
    assert.deepEqual(result, { locked: true, code: 'AUTHORITY_UNAVAILABLE', scopes: ['drafts'], leaked: false });
  });
  for (const legacyKind of ['untyped', 'missing-content-revision']) {
    await test('legacy-learning-' + legacyKind + '-is-invalidated-with-unique-pools-preserved', async () => {
      const result = await page.evaluate(async legacyKind => {
        const database = 'pr08b-legacy-learning-' + legacyKind;
        await create(database);
        const draft = await runtime.enqueue(operation('UNIQUE LEGACY DRAFT'), ancestry, 1);
        await cache.unlockScope('learning');
        const legacy = { ...learningReference(), verifiedAt: Date.now() };
        if (legacyKind === 'untyped') delete legacy.objectType;
        delete legacy.contentRevisionId;
        await cache.put(legacy, new TextEncoder().encode('OLD UNTYPED LEARNING'));
        const unlockRecording = () => cache.unlock('recording_recovery', ids.classroom, async () => ({
          key_id: 'synthetic-recording_recovery', key_epoch: 1,
          key_material_b64: btoa(String.fromCharCode(...new Uint8Array(32).fill(17))), scope: 'recording_recovery',
          expires_at: new Date(Date.now() + 120000).toISOString(), cache_policy_revision: 'synthetic-only' }));
        await unlockRecording();
        const pcm = { scope: 'recording_recovery', objectId: 'synthetic-pcm', revision: 1, generation: 1, ancestry };
        await cache.put(pcm, new Uint8Array([1, 2, 3, 4]));
        await runtime.unlock('synthetic-password');
        const stored = await records(database);
        await unlockRecording();
        return { scopes: stored.map(value => value.header.scope).sort(),
          draft: (await runtime.readDraft(draft)).payload.body_markdown,
          pcm: Array.from(await cache.read(pcm, ids.classroom)) };
      }, legacyKind);
      assert.deepEqual(result, { scopes: ['drafts', 'recording_recovery'], draft: 'UNIQUE LEGACY DRAFT', pcm: [1, 2, 3, 4] });
    });
  }
  await test('learning-kind-is-bound-to-same-uuid-revision-generation', async () => {
    const result = await page.evaluate(async () => {
      await create('pr08b-learning-kind', {}, {}, 'learning');
      const reference = learningReference();
      await runtime.cacheLearning(reference, hydrate(reference, 'TYPED LEARNING CONTENT'));
      const wrong = { ...reference, objectType: 'mark' };
      let writeCode = null; let readCode = null;
      try { await cache.put({ ...wrong, verifiedAt: Date.now() }, new TextEncoder().encode('TYPED LEARNING CONTENT')); }
      catch (error) { writeCode = error.code; }
      try { await cache.read(wrong); } catch (error) { readCode = error.code; }
      const wrongRevision = { ...reference, contentRevisionId: ids.revisionB };
      let revisionWriteCode = null; let revisionReadCode = null;
      try { await cache.put({ ...wrongRevision, verifiedAt: Date.now() }, new TextEncoder().encode('OTHER CONTENT VERSION')); }
      catch (error) { revisionWriteCode = error.code; }
      try { await cache.read(wrongRevision); } catch (error) { revisionReadCode = error.code; }
      const text = new TextDecoder().decode(await runtime.readLearning(reference));
      const database = await lib.openCacheDatabase('pr08b-learning-kind');
      try {
        await new Promise((resolve, reject) => {
          const tx = database.transaction(['records'], 'readwrite');
          const store = tx.objectStore('records');
          const request = store.getAll();
          request.onsuccess = () => {
            const record = request.result[0]; record.header.contentRevisionId = ids.revisionB; store.put(record);
          };
          tx.oncomplete = resolve; tx.onabort = () => reject(tx.error);
        });
      } finally { database.close(); }
      let tamperedCode = null;
      try { await cache.read(wrongRevision); } catch (error) { tamperedCode = error.code; }
      return { writeCode, readCode, revisionWriteCode, revisionReadCode, tamperedCode, text,
        count: (await records('pr08b-learning-kind')).length };
    });
    assert.deepEqual(result, { writeCode: 'REVISION_CONFLICT', readCode: 'REVISION_CONFLICT',
      revisionWriteCode: 'REVISION_CONFLICT', revisionReadCode: 'REVISION_CONFLICT', tamperedCode: 'CORRUPT_RECORD',
      text: 'TYPED LEARNING CONTENT', count: 1 });
  });
  await test('ordinary-cache-cannot-release-or-acknowledge-sync-records', async () => {
    const result = await page.evaluate(async () => {
      const name = 'pr08b-trusted-cache-sync-boundary';
      const original = await editable(name);
      const conflict = await runtime.inspectConflict(original);
      const editor = await runtime.saveConflictDraft(original, 'EDITOR RECOVERY', 'NOTE', crypto.randomUUID(), 1);
      await runtime.unlockLearning('synthetic-password');
      const learning = learningReference();
      await runtime.cacheLearning(learning, hydrate(learning, 'SOURCE-BOUND LEARNING'));

      const encode = value => new TextEncoder().encode(JSON.stringify(value));
      const body = { base_revision: 2, resolution: 'use_server' };
      const resolution = { ...conflict.local_operation, op_id: crypto.randomUUID(), base_revision: body.base_revision,
        payload: { ...conflict.local_operation.payload, base_revision: body.base_revision } };
      const legacy = { ...original, operationId: resolution.op_id, revision: body.base_revision, generation: 2 };
      const envelope = { schema: 1, kind: 'resolve', operation: resolution, original,
        conflictId: conflict.id, resolution: body, requestHash: await lib.syncFingerprint({ conflict_id: conflict.id, body }) };
      const whitespace = new TextEncoder().encode('\t\r\n ' + JSON.stringify(envelope));
      const bom = new TextEncoder().encode('\ufeff ' + JSON.stringify(envelope));
      await cache.put(legacy, whitespace);
      if ((await runtime.readDraft(legacy)).op_id !== resolution.op_id) throw new Error('Legacy Sync envelope lost its exact key');
      const stored = await records(name);
      for (const reference of [original, legacy]) {
        const header = stored.find(value => value.header.operationId === reference.operationId).header;
        if (header.objectType || header.resolutionOf) throw new Error('Expected a legacy unmarked Sync record');
      }
      const sealed = values => values.map(({ id, header, nonce, ciphertext }) =>
        [id, header, Array.from(nonce), Array.from(new Uint8Array(ciphertext))]);
      const before = JSON.stringify(sealed(stored));
      const ordinary = new NativeTrustedCache({ enabled: true, databaseName: name,
        limits: { draftBytes: 2000000, draftOperations: 100, recordingBytes: 2000000 } });
      ordinary.bind({ ownerId: ids.owner, deviceId: ids.device, sessionId: ids.session });
      try {
        await ordinary.unlockScope('drafts'); await ordinary.unlockScope('learning');
        const exact = ({ scope, objectId, revision, generation }) => ({ scope, objectId, revision, generation });
        for (const reference of [original, legacy, editor, learning]) {
          await expectCode(ordinary.read(exact(reference)), 'SYNC_AUTHORITY_REQUIRED');
        }
        for (const reference of [original, legacy, editor]) {
          await expectCode(ordinary.acknowledgeDraft(exact(reference), reference.operationId), 'SYNC_AUTHORITY_REQUIRED');
        }
        const forgedEditor = { ...editor, generation: editor.generation + 1 };
        await expectCode(Promise.resolve().then(() => ordinary.saveRecoveryDraft(forgedEditor, encode({ body: 'FOREIGN EDIT' }))),
          'SYNC_AUTHORITY_REQUIRED');
        await expectCode(ordinary.put(forgedEditor, encode({ body: 'FOREIGN EDIT' })), 'SYNC_AUTHORITY_REQUIRED');
        await expectCode(ordinary.put({ ...legacy, generation: legacy.generation + 1 }, encode(envelope)), 'SYNC_AUTHORITY_REQUIRED');
        await expectCode(ordinary.put({ ...learning, verifiedAt: Date.now() }, encode({ body: 'FOREIGN LEARNING' })),
          'SYNC_AUTHORITY_REQUIRED');
        const normal = { ...original, objectId: crypto.randomUUID(), operationId: crypto.randomUUID() };
        const bytes = encode({ schema: 1, kind: 'write', text: 'UNSTRUCTURED PR-08a DRAFT' });
        for (const reserved of [encode(envelope), whitespace, bom]) {
          await expectCode(ordinary.put(legacy, reserved), 'SYNC_AUTHORITY_REQUIRED');
        }
        const forged = { ...legacy, operationId: crypto.randomUUID(), generation: 3,
          resolutionOf: { operationId: original.operationId, revision: original.revision, generation: original.generation } };
        await expectCode(ordinary.put(forged, bytes), 'SYNC_AUTHORITY_REQUIRED');
        await expectCode(ordinary.put(normal, bytes, { rejected: legacy }), 'SYNC_AUTHORITY_REQUIRED');
        await ordinary.put(normal, bytes);
        const visible = await ordinary.listPending('drafts');
        const genericBody = JSON.parse(new TextDecoder().decode(await ordinary.read(normal))).text;
        await ordinary.acknowledgeDraft(normal, normal.operationId);
        return { genericBody, pendingGenericCount: visible.length, unchanged: before === JSON.stringify(sealed(await records(name))) };
      } finally { ordinary.close(); }
    });
    assert.deepEqual(result, { genericBody: 'UNSTRUCTURED PR-08a DRAFT', pendingGenericCount: 1, unchanged: true });
  });
  await test('ordinary-deletion-locks-peer-before-observer-cache-closes', async () => {
    const [observer, peer] = await Promise.all([context.newPage(), context.newPage()]);
    try {
      await Promise.all([install(observer), install(peer)]);
      await peer.evaluate(async () => {
        const cache = window.ordinaryPeer = new NativeTrustedCache({ enabled: true, databaseName: 'pr08b-notify-before-close',
          limits: { draftBytes: 2000000, draftOperations: 2, recordingBytes: 2000000 } });
        cache.bind({ ownerId: ids.owner, deviceId: ids.device, sessionId: ids.session });
        await cache.unlockScope('drafts');
        const reference = window.ordinaryReference = { scope: 'drafts', objectId: ids.document, revision: 1, generation: 1,
          operationId: crypto.randomUUID(), ancestry };
        await cache.put(reference, new TextEncoder().encode('VISIBLE ORDINARY DRAFT'));
        const content = document.createElement('pre');
        content.id = 'ordinary-deletion-content';
        content.textContent = new TextDecoder().decode(await cache.read(reference));
        document.body.append(content);
        cache.addEventListener('locked', () => { content.textContent = ''; });
      });
      assert.equal(await peer.locator('#ordinary-deletion-content').textContent(), 'VISIBLE ORDINARY DRAFT');
      await observer.evaluate(async () => {
        const cache = new NativeTrustedCache({ enabled: true, databaseName: 'pr08b-notify-before-close' });
        cache.bind({ ownerId: ids.owner, deviceId: ids.device, sessionId: ids.session });
        // Close this adapter and its channel while the IDB transaction is still queued.
        const committing = cache.applyDeletion({ object_type: 'class_session', object_id: ids.classroom,
          deletion_id: ids.deletion, state: 'access_revoked' }, { retainDrafts: true });
        cache.close();
        await committing;
      });
      await observer.close();
      await peer.waitForFunction(() => document.querySelector('#ordinary-deletion-content')?.textContent === '');
      await peer.evaluate(async () => {
        await expectCode(ordinaryPeer.read(ordinaryReference), 'LOCKED');
        await ordinaryPeer.unlockScope('drafts');
        await expectCode(ordinaryPeer.read(ordinaryReference), 'GONE');
        ordinaryPeer.close();
      });
    } finally {
      await Promise.all([observer, peer].filter(tab => !tab.isClosed()).map(tab => tab.close()));
    }
  });
  await test('deletion-fences-peer-unlock-until-the-same-request-commits-across-tabs', async () => {
    const other = await context.newPage();
    try {
      await install(other);
      const name = 'pr08b-deletion-peer-lock';
      await page.evaluate(name => create(name), name);
      await other.evaluate(async name => {
        await create(name);
        window.visibleDraft = await runtime.enqueue(operation('PEER PRIVATE TEXT'), ancestry, 1);
        await runtime.readDraft(visibleDraft);
        window.ordinaryOptions = { enabled: true, databaseName: name,
          limits: { draftBytes: 100000, draftOperations: 5, recordingBytes: 100000 } };
        window.oldOrdinary = new NativeTrustedCache(ordinaryOptions);
        oldOrdinary.bind({ ownerId: ids.owner, deviceId: ids.device, sessionId: ids.session });
        await oldOrdinary.unlockScope('drafts');
        window.ordinaryDraft = { ...visibleDraft, operationId: crypto.randomUUID(), generation: 2 };
        await oldOrdinary.put(ordinaryDraft, new TextEncoder().encode('OLD ORDINARY PR-08A DRAFT'));
        if (!(await oldOrdinary.read(ordinaryDraft))) throw new Error('Ordinary draft failed before revocation');
        window.otherNamespaceLocks = 0;
        window.otherNamespaceCache = new NativeTrustedCache(ordinaryOptions);
        otherNamespaceCache.bind({ ownerId: crypto.randomUUID(), deviceId: ids.device, sessionId: ids.session });
        otherNamespaceCache.addEventListener('locked', () => { otherNamespaceLocks++; });
        await otherNamespaceCache.unlockScope('drafts');
      }, name);

      await page.evaluate(() => {
        cache.lock();
        window.deletionPage = { items: [{ event_id: crypto.randomUUID(), object_type: 'deletion',
          object_id: ids.deletion, revision: 1, deleted: true }], next_cursor: 'after-committed-tombstone', has_more: false };
        transport.pages.push(deletionPage);
        transport.deletion = () => new Promise((_, reject) => {
          window.failDeletionDetail = () => reject(Object.assign(new Error('Synthetic offline deletion detail'), { code: 'STORAGE_UNAVAILABLE' }));
        });
        window.lockedObservation = runtime.unlock('synthetic-password').catch(error => error.code);
      });
      await page.waitForFunction(() => !!window.failDeletionDetail);
      await other.waitForFunction(() => !runtime.snapshot().unlocked);
      assert.deepEqual(await other.evaluate(async () => {
        await expectCode(runtime.readDraft(visibleDraft), 'LOCKED');
        await expectCode(oldOrdinary.applyDeletion({ object_type: 'media_asset', object_id: crypto.randomUUID(),
          deletion_id: ids.deletion, state: 'access_revoked' }), 'SYNC_AUTHORITY_REQUIRED');
        await expectCode(oldOrdinary.unlockScope('drafts'), 'LOCKED');
        await expectCode(oldOrdinary.read(ordinaryDraft), 'LOCKED');
        const fresh = new NativeTrustedCache(ordinaryOptions);
        fresh.bind({ ownerId: ids.owner, deviceId: ids.device, sessionId: crypto.randomUUID() });
        try {
          await expectCode(fresh.unlockScope('drafts'), 'LOCKED');
          await expectCode(fresh.read(ordinaryDraft), 'LOCKED');
        } finally { fresh.close(); }
        return { otherNamespaceLocks };
      }), { otherNamespaceLocks: 0 });

      const failure = await page.evaluate(async () => {
        const before = runtime.snapshot().cursor;
        failDeletionDetail();
        const code = await lockedObservation;
        const afterFailure = runtime.snapshot().cursor;
        runtime.close();
        return { before, afterFailure, code, deletionPage };
      });
      assert.deepEqual({ before: failure.before, afterFailure: failure.afterFailure, code: failure.code },
        { before: 'watermark-0', afterFailure: 'watermark-0', code: 'STORAGE_UNAVAILABLE' });
      await other.evaluate(() => expectCode(oldOrdinary.unlockScope('drafts'), 'LOCKED'));

      await page.evaluate(name => create(name, {}, {}, null), name);
      const reopen = async (target, replay) => target.evaluate(({ replay, deletionPage }) => {
        if (replay) transport.pages.push(deletionPage);
        else {
          const bootstrap = transport.bootstrap.bind(transport);
          transport.bootstrap = async () => ({ ...await bootstrap(), watermark: deletionPage.next_cursor });
        }
        const deletion = transport.deletion.bind(transport);
        transport.deletion = id => new Promise(resolve => { window.releaseRetriedDeletion = () => resolve(deletion(id)); });
        window.reopening = runtime.unlock('synthetic-password').then(() => ({ cursor: runtime.snapshot().cursor,
          pending: runtime.snapshot().pending.map(value => value.state) }));
      }, { replay, deletionPage: failure.deletionPage });
      await Promise.all([reopen(page, false), reopen(other, true)]);
      await Promise.all([page, other].map(target => target.waitForFunction(() => !!window.releaseRetriedDeletion)));
      const recovered = await Promise.all([page, other].map(target => target.evaluate(() => {
        releaseRetriedDeletion();
        return reopening;
      })));
      assert.deepEqual(recovered, Array.from({ length: 2 }, () => ({ cursor: 'after-committed-tombstone', pending: ['restricted', 'restricted'] })));
      await page.evaluate(() => cache.lockForDeletion([ids.deletion]));
      await other.evaluate(async () => {
        await oldOrdinary.unlockScope('drafts');
        await expectCode(oldOrdinary.read(ordinaryDraft), 'GONE');
      });

      await page.evaluate(async () => {
        const notice = () => ({ object_type: 'media_asset', object_id: crypto.randomUUID(),
          deletion_id: crypto.randomUUID(), state: 'access_revoked' });
        const first = notice();
        window.secondPendingDeletion = notice();
        await cache.lockForDeletion([first.deletion_id, secondPendingDeletion.deletion_id]);
        await cache.applyDeletion(first, { retainDrafts: true, completeFence: true });
      });
      await other.evaluate(() => expectCode(oldOrdinary.unlockScope('drafts'), 'LOCKED'));
      await page.evaluate(() => cache.applyDeletion(secondPendingDeletion, { retainDrafts: true, completeFence: true }));
      await other.evaluate(async () => {
        await oldOrdinary.unlockScope('drafts');
        await expectCode(oldOrdinary.read(ordinaryDraft), 'GONE');
      });
      assert.equal((await page.evaluate(name => records(name), name)).length, 2);
    } finally {
      await other.evaluate(() => { window.runtime?.close(); window.oldOrdinary?.close(); window.otherNamespaceCache?.close(); }).catch(() => {});
      await other.close();
    }
  });
  await test('v4-preserves-v3-ciphertext-and-excludes-stale-cache-openers', async () => {
    const oldPage = await context.newPage();
    const name = 'pr08b-v3-to-v4-deletion-boundary';
    try {
      await install(oldPage);
      const legacy = await oldPage.evaluate(async name => {
        // Use the existing adapter's real encrypted storage path with the frozen v3 open request.
        window.legacyNativeOpen = IDBFactory.prototype.open;
        IDBFactory.prototype.open = function(nameToOpen, version) {
          return legacyNativeOpen.call(this, nameToOpen, nameToOpen === name ? 3 : version);
        };
        const options = { enabled: true, databaseName: name,
          limits: { draftBytes: 100000, draftOperations: 5, recordingBytes: 100000 } };
        const identity = { ownerId: ids.owner, deviceId: ids.device, sessionId: ids.session };
        const reference = { scope: 'drafts', objectId: ids.document, revision: 1, generation: 1,
          operationId: crypto.randomUUID(), ancestry };
        window.v3Cache = new NativeTrustedCache(options);
        v3Cache.bind(identity);
        await v3Cache.unlockScope('drafts');
        await v3Cache.put(reference, new TextEncoder().encode('LEGACY ENCRYPTED PR-08A DRAFT'));
        const plaintext = new TextDecoder().decode(await v3Cache.read(reference));
        window.v3Connection = await lib.openCacheDatabase(name);
        const closeOnUpgrade = v3Connection.onversionchange;
        v3Connection.onversionchange = event => {
          window.v3Upgrade = [event.oldVersion, event.newVersion];
          closeOnUpgrade.call(v3Connection, event);
        };
        const stores = ['records', 'metadata', 'nonces', 'namespaces', 'tombstones'];
        const snapshot = db => new Promise((resolve, reject) => {
          const tx = db.transaction(stores, 'readonly');
          const requests = stores.map(store => tx.objectStore(store).getAll());
          tx.oncomplete = () => resolve(JSON.stringify(requests.map(request => request.result), (_, value) =>
            value instanceof ArrayBuffer ? Array.from(new Uint8Array(value)) : ArrayBuffer.isView(value) ? Array.from(value) : value));
          tx.onabort = () => reject(tx.error);
        });
        return { options, identity, reference, plaintext, version: v3Connection.version, sealed: await snapshot(v3Connection) };
      }, name);
      assert.equal(legacy.version, 3);
      assert.equal(legacy.plaintext, 'LEGACY ENCRYPTED PR-08A DRAFT');

      const upgraded = await page.evaluate(async ({ options, identity, reference }) => {
        const db = await lib.openCacheDatabase(options.databaseName);
        let sealed;
        try {
          const stores = ['records', 'metadata', 'nonces', 'namespaces', 'tombstones'];
          sealed = await new Promise((resolve, reject) => {
            const tx = db.transaction(stores, 'readonly');
            const requests = stores.map(store => tx.objectStore(store).getAll());
            tx.oncomplete = () => resolve(JSON.stringify(requests.map(request => request.result), (_, value) =>
              value instanceof ArrayBuffer ? Array.from(new Uint8Array(value)) : ArrayBuffer.isView(value) ? Array.from(value) : value));
            tx.onabort = () => reject(tx.error);
          });
        } finally { db.close(); }
        const ordinary = new NativeTrustedCache(options);
        ordinary.bind(identity);
        try {
          await ordinary.unlockScope('drafts');
          return { sealed, version: db.version, plaintext: new TextDecoder().decode(await ordinary.read(reference)) };
        } finally { ordinary.close(); }
      }, legacy);
      assert.deepEqual(upgraded, { sealed: legacy.sealed, version: 4, plaintext: legacy.plaintext });
      const closed = await oldPage.evaluate(async ({ options, reference }) => {
        let oldTransaction;
        try { v3Connection.transaction('records', 'readonly'); } catch (error) { oldTransaction = error.name; }
        await expectCode(v3Cache.read(reference), 'STORAGE_UNAVAILABLE');
        const reopenError = await new Promise(resolve => {
          const request = indexedDB.open(options.databaseName, 3);
          request.onerror = () => resolve(request.error.name);
          request.onsuccess = () => { request.result.close(); resolve(null); };
        });
        window.legacyFenceLocked = false;
        v3Cache.addEventListener('locked', () => { legacyFenceLocked = true; });
        return { oldTransaction, reopenError, versionchange: v3Upgrade };
      }, legacy);
      assert.deepEqual(closed, { oldTransaction: 'InvalidStateError', reopenError: 'VersionError', versionchange: [3, 4] });

      await page.evaluate(async name => {
        await create(name, {}, {}, null);
        await cache.lockForDeletion([ids.deletion]);
      }, name);
      await oldPage.waitForFunction(() => window.legacyFenceLocked);
      await oldPage.evaluate(async reference => {
        await expectCode(v3Cache.unlockScope('drafts'), 'STORAGE_UNAVAILABLE');
        await expectCode(v3Cache.read(reference), 'LOCKED');
      }, legacy.reference);
    } finally {
      await oldPage.evaluate(() => {
        window.v3Connection?.close(); window.v3Cache?.close();
        if (window.legacyNativeOpen) IDBFactory.prototype.open = legacyNativeOpen;
      }).catch(() => {});
      await oldPage.close();
    }
  });
  await test('ordinary-change-preserves-unsent-merge-body-and-note-after-reopen', async () => {
    await page.evaluate(() => editable('pr08b-editor-ordinary', {}, true));
    await page.getByRole('textbox', { name: '合并正文', exact: true }).fill('UNSENT ORDINARY MERGE');
    await page.getByRole('textbox', { name: '个人补充', exact: true }).fill('UNSENT ORDINARY NOTE');
    await page.evaluate(async () => {
      await runtime.awaitEditorDraftSaves();
      transport.pages.push({ items: [{ event_id: crypto.randomUUID(), object_id: ids.document, object_type: 'review_document', revision: 1, deleted: false }], next_cursor: 'ordinary-editor-change', has_more: false });
      await runtime.pollChanges();
    });
    await page.getByRole('status').filter({ hasText: '同步已锁定' }).waitFor();
    assert.equal(await page.locator('textarea').count(), 0);
    await page.evaluate(async () => {
      await runtime.unlock('synthetic-password'); await runtime.flush(); uiRoot.unmount();
      const mount = document.createElement('div'); document.body.append(mount); uiRoot = lib.createRoot(mount);
      uiRoot.render(lib.React.createElement(lib.ConflictPanel, { runtime, reference: runtime.snapshot().pending[0].reference, generation: 2 }));
    });
    await page.getByRole('textbox', { name: '合并正文', exact: true }).waitFor();
    assert.equal(await page.getByRole('textbox', { name: '合并正文', exact: true }).inputValue(), 'UNSENT ORDINARY MERGE');
    assert.equal(await page.getByRole('textbox', { name: '个人补充', exact: true }).inputValue(), 'UNSENT ORDINARY NOTE');
    assert.deepEqual(await page.evaluate(async () => ({ pending: runtime.snapshot().pending.length, records: (await records('pr08b-editor-ordinary')).length, resolutions: transport.resolutions.length })), { pending: 1, records: 2, resolutions: 0 });
  });
  await test('recovery-native-encryption-completes-after-lock-without-plaintext-reopening', async () => {
    await page.evaluate(async () => {
      window.editorSource = await editable('pr08b-editor-crypto-lock'); delayEncryption();
      window.editorSave = runtime.saveConflictDraft(editorSource, 'RACING CRYPTO BODY', 'RACING CRYPTO NOTE', crypto.randomUUID(), 1);
    });
    await page.waitForFunction(() => encryptedReleases.length === 1);
    const result = await page.evaluate(async () => {
      cache.lock(); restoreEncryption(); encryptedReleases.shift()(); await editorSave; await runtime.awaitEditorDraftSaves();
      const stored = await records('pr08b-editor-crypto-lock'); const locked = !runtime.snapshot().unlocked;
      await expectCode(runtime.readConflictDrafts(editorSource), 'LOCKED');
      await runtime.unlock('synthetic-password'); await runtime.flush();
      const [saved] = await runtime.readConflictDrafts(editorSource);
      return { locked, count: stored.length, leaked: JSON.stringify(stored).includes('RACING CRYPTO'), body: saved.body, note: saved.note };
    });
    assert.deepEqual(result, { locked: true, count: 2, leaked: false, body: 'RACING CRYPTO BODY', note: 'RACING CRYPTO NOTE' });
  });
  await test('explicit-purge-blocks-delayed-recovery-ciphertext-resurrection', async () => {
    await page.evaluate(async () => {
      const source = await editable('pr08b-editor-purge'); delayEncryption();
      window.editorSave = runtime.saveConflictDraft(source, 'PURGED UNIQUE INPUT', 'PURGED NOTE', crypto.randomUUID(), 1).then(() => 'saved', error => error.code);
    });
    await page.waitForFunction(() => encryptedReleases.length === 1);
    const result = await page.evaluate(async () => {
      await cache.purge(); restoreEncryption(); encryptedReleases.shift()(); const code = await editorSave;
      let retryCode = null; try { await runtime.retryEditorDraftSaves(); } catch (error) { retryCode = error.code; }
      return { code, retryCode, count: (await records('pr08b-editor-purge')).length };
    });
    assert.deepEqual(result, { code: 'STALE_OPERATION', retryCode: 'STALE_OPERATION', count: 0 });
  });
  for (const scope of ['class_session', 'review_document']) {
    for (const retainDrafts of [false, true]) {
      await test('deletion-' + scope + '-recovery-retention-' + retainDrafts, async () => {
        const database = 'pr08b-editor-delete-' + scope + '-' + retainDrafts;
        await page.evaluate(async database => {
          const source = await editable(database); const stream = crypto.randomUUID();
          await runtime.saveConflictDraft(source, 'EXISTING RECOVERY BODY', 'EXISTING NOTE', stream, 1);
          delayEncryption();
          window.editorSave = runtime.saveConflictDraft(source, 'DELAYED RECOVERY BODY', 'DELAYED NOTE', stream, 2)
            .then(reference => ({ code: 'saved', reference }), error => ({ code: error.code }));
        }, database);
        await page.waitForFunction(() => encryptedReleases.length === 1);
        const result = await page.evaluate(async ({ scope, retainDrafts, database }) => {
          const notice = { object_type: scope, object_id: scope === 'class_session' ? ids.classroom : ids.document, deletion_id: crypto.randomUUID(), state: 'access_revoked' };
          if (retainDrafts) await cache.applyDeletion(notice, { retainDrafts: true });
          else await cache.applyDeletion(notice);
          const afterDeletion = (await records(database)).length;
          restoreEncryption(); encryptedReleases.shift()(); const saved = await editorSave;
          const stored = await records(database);
          let readCode = null;
          if (saved.reference) {
            transport.projections = transport.projections.filter(value => value.object_id !== ids.document && (scope !== 'class_session' || value.object_id !== ids.classroom));
            await runtime.unlock('synthetic-password');
            try { await cache.read(saved.reference); } catch (error) { readCode = error.code; }
          }
          return { code: saved.code, afterDeletion, count: stored.length, readCode,
            sourceBound: stored.filter(value => value.header.objectType === 'sync_editor_recovery').every(value => value.header.recoverySourceObjectId === ids.document),
            leaked: JSON.stringify(stored).includes('RECOVERY BODY') };
        }, { scope, retainDrafts, database });
        assert.deepEqual(result, { code: retainDrafts ? 'saved' : 'STALE_OPERATION', afterDeletion: retainDrafts ? 2 : 0,
          count: retainDrafts ? 2 : 0, readCode: retainDrafts ? 'GONE' : null, sourceBound: true, leaked: false });
      });
    }
  }
  await test('targeted-deletion-keeps-unrelated-inflight-recovery-and-cannot-weaken-delete', async () => {
    await page.evaluate(async () => {
      window.firstSource = await editable('pr08b-editor-targeted-delete');
      const otherDocument = crypto.randomUUID();
      transport.projections.push({ object_type: 'review_document', object_id: otherDocument, parent_id: ids.classroom, revision: 1, adopted_revision_id: null, updated_at: new Date().toISOString() });
      await runtime.unlock('synthetic-password'); await runtime.flush();
      window.otherSource = await runtime.enqueue({ ...operation('UNRELATED ORIGINAL'), object_id: otherDocument }, ancestry, 1);
      await runtime.flush(); await runtime.inspectConflict(firstSource); await runtime.inspectConflict(otherSource);
      delayEncryption();
      window.firstSave = runtime.saveConflictDraft(firstSource, 'DELETED A INPUT', '', crypto.randomUUID(), 1).then(() => 'saved', error => error.code);
      window.otherSave = runtime.saveConflictDraft(otherSource, 'UNRELATED B INPUT', '', crypto.randomUUID(), 1).then(reference => ({ code: 'saved', reference }), error => ({ code: error.code }));
    });
    await page.waitForFunction(() => encryptedReleases.length === 2);
    const result = await page.evaluate(async () => {
      const notice = { object_type: 'review_document', object_id: ids.document, deletion_id: crypto.randomUUID(), state: 'access_revoked' };
      await cache.applyDeletion(notice);
      await cache.applyDeletion(notice, { retainDrafts: true });
      restoreEncryption(); encryptedReleases.splice(0).forEach(release => release());
      const first = await firstSave, other = await otherSave;
      transport.projections = transport.projections.filter(value => value.object_id !== ids.document);
      await runtime.unlock('synthetic-password'); await runtime.flush();
      const [draft] = await runtime.readConflictDrafts(otherSource);
      return { first, other: other.code, body: draft.body, count: (await records('pr08b-editor-targeted-delete')).length };
    });
    assert.deepEqual(result, { first: 'STALE_OPERATION', other: 'saved', body: 'UNRELATED B INPUT', count: 2 });
  });
  await test('locked-editor-reports-idb-failure-and-retries-only-retained-ciphertext', async () => {
    await page.evaluate(async () => { await editable('pr08b-editor-idb-failure', {}, true); delayEncryption(); });
    await page.getByRole('textbox', { name: '合并正文', exact: true }).fill('IDB FAILURE UNIQUE INPUT');
    await page.waitForFunction(() => encryptedReleases.length === 1);
    await page.evaluate(() => {
      window.nativeTransaction = IDBDatabase.prototype.transaction;
      IDBDatabase.prototype.transaction = function(...args) { if (args[1] === 'readwrite') throw new DOMException('Synthetic quota', 'QuotaExceededError'); return nativeTransaction.apply(this, args); };
      cache.lock(); restoreEncryption(); encryptedReleases.shift()();
    });
    await page.getByRole('status').filter({ hasText: '合并输入尚未保存' }).waitFor();
    assert.equal(await page.locator('textarea').count(), 0);
    await page.evaluate(() => { IDBDatabase.prototype.transaction = nativeTransaction; window.nativeTransaction = null; });
    await page.getByRole('button', { name: '重试保存合并输入', exact: true }).click();
    await page.getByRole('status').filter({ hasText: '合并输入已加密保存在本机' }).waitFor();
    assert.equal(await page.locator('textarea').count(), 0);
    assert.deepEqual(await page.evaluate(async () => ({ locked: !runtime.snapshot().unlocked, count: (await records('pr08b-editor-idb-failure')).length })), { locked: true, count: 2 });
  });
  await test('synchronous-save-failure-remains-visible-after-lock-and-empty-retry', async () => {
    await page.evaluate(async () => {
      await editable('pr08b-editor-sync-failure', {}, true);
      cache.saveRecoveryDraft = () => { throw Object.assign(new Error('Synthetic rejected persistence'), { code: 'QUOTA_EXCEEDED' }); };
    });
    await page.getByRole('textbox', { name: '合并正文', exact: true }).fill('NOT ENCRYPTED UNIQUE INPUT');
    await page.getByRole('status').filter({ hasText: '合并输入尚未保存' }).waitFor();
    await page.evaluate(() => cache.lock());
    await page.getByRole('button', { name: '重试保存合并输入', exact: true }).click();
    await page.getByRole('status').filter({ hasText: '合并输入尚未保存' }).waitFor();
    assert.equal(await page.locator('textarea').count(), 0);
    assert.equal(await page.getByRole('status').filter({ hasText: '合并输入已加密保存在本机' }).count(), 0);
    assert.equal(await page.evaluate(async () => (await records('pr08b-editor-sync-failure')).length), 1);
  });
  await test('editor-stream-replacement-bounds-quota-and-keeps-concurrent-branches', async () => {
    await page.evaluate(async () => {
      window.editorSource = await editable('pr08b-editor-streams', { draftOperations: 3 }, true);
      window.editorStream = crypto.randomUUID(); delayEncryption();
      window.editorSave = runtime.saveConflictDraft(editorSource, 'SLOW OLDER INPUT', '', editorStream, 1);
    });
    await page.waitForFunction(() => encryptedReleases.length === 1);
    const result = await page.evaluate(async () => {
      restoreEncryption();
      for (let generation = 2; generation <= 22; generation++) await runtime.saveConflictDraft(editorSource, 'LATEST INPUT ' + generation, '', editorStream, generation);
      encryptedReleases.shift()(); await editorSave;
      await runtime.saveConflictDraft(editorSource, 'OTHER TAB UNIQUE INPUT', '', crypto.randomUUID(), 1);
      let quota = null; try { await runtime.saveConflictDraft(editorSource, 'THIRD BRANCH', '', crypto.randomUUID(), 1); } catch (error) { quota = error.code; }
      const drafts = await runtime.readConflictDrafts(editorSource);
      uiRoot.unmount(); const mount = document.createElement('div'); document.body.append(mount); uiRoot = lib.createRoot(mount);
      uiRoot.render(lib.React.createElement(lib.ConflictPanel, { runtime, reference: editorSource, generation: 2 }));
      return { quota, count: (await records('pr08b-editor-streams')).length, generations: drafts.map(value => value.reference.generation).sort((a,b)=>a-b), bodies: drafts.map(value => value.body).sort() };
    });
    assert.deepEqual(result, { quota: 'QUOTA_EXCEEDED', count: 3, generations: [1,22], bodies: ['LATEST INPUT 22', 'OTHER TAB UNIQUE INPUT'] });
    await page.getByRole('combobox', { name: '本机合并草稿', exact: true }).waitFor();
    assert.equal(await page.getByRole('textbox', { name: '合并正文', exact: true }).inputValue(), 'ORIGINAL FROZEN INPUT');
  });
  await test('old-resolution-ack-preserves-and-discovers-a-later-editor-generation', async () => {
    await page.evaluate(async () => {
      window.editorSource = await editable('pr08b-editor-late-ack'); window.editorStream = crypto.randomUUID();
      const first = await runtime.saveConflictDraft(editorSource, 'SUBMITTED BODY', 'SUBMITTED NOTE', editorStream, 1);
      const local = await runtime.readDraft(editorSource); const resolve = transport.resolve.bind(transport);
      transport.resolve = async (...args) => { await new Promise(release => { window.releaseResolution = release; }); return resolve(...args); };
      window.resolvingEditor = runtime.resolve(editorSource, { base_revision: 2, resolution: 'edited_merge', merged_operation: { ...local, op_id: crypto.randomUUID(), base_revision: 2,
        payload: { ...local.payload, base_revision: 2, body_markdown: 'SUBMITTED BODY', personal_note: 'SUBMITTED NOTE' } } }, 2, [first]);
    });
    await page.waitForFunction(() => typeof window.releaseResolution === 'function');
    const result = await page.evaluate(async () => {
      const resolution = runtime.snapshot().pending.find(value => value.reference.revision === 2).reference;
      const envelope = JSON.parse(new TextDecoder().decode(await cache.read(resolution)));
      if (envelope.recoveryReferences.length !== 1 || envelope.recoveryReferences[0].generation !== 1) throw new Error('Test did not bind the older recovery ACK');
      await runtime.saveConflictDraft(editorSource, 'LATE UNIQUE BODY', 'LATE UNIQUE NOTE', editorStream, 2);
      releaseResolution(); await resolvingEditor;
      const [source] = await runtime.listConflictDraftSources(); const [draft] = await runtime.readConflictDrafts(source.source);
      return { pending: runtime.snapshot().pending.length, count: (await records('pr08b-editor-late-ack')).length, body: draft.body, note: draft.note, state: source.state };
    });
    assert.deepEqual(result, { pending: 0, count: 1, body: 'LATE UNIQUE BODY', note: 'LATE UNIQUE NOTE', state: 'available' });
  });
  for (const mode of ['use_server', 'copy_to_new_note']) {
    await test('nonmerge-' + mode + '-retains-discoverable-input-after-real-page-reload', async () => {
      const database = 'pr08b-editor-reopen-' + mode;
      await page.evaluate(name => editable(name, {}, true), database);
      await page.getByRole('textbox', { name: '合并正文', exact: true }).fill('UNSUBMITTED NONMERGE BODY');
      await page.getByRole('textbox', { name: '个人补充', exact: true }).fill('UNSUBMITTED NONMERGE NOTE');
      await page.evaluate(() => runtime.awaitEditorDraftSaves());
      await page.locator(`input[value="${mode}"]`).check();
      if (mode === 'copy_to_new_note') await page.getByRole('textbox', { name: '目标课堂', exact: true }).fill('10000000-0000-4000-8000-000000000006');
      await page.getByRole('button', { name: '确认处理', exact: true }).click();
      await page.getByRole('textbox', { name: '保留的合并正文', exact: true }).waitFor();
      assert.equal(await page.getByRole('textbox', { name: '保留的合并正文', exact: true }).inputValue(), 'UNSUBMITTED NONMERGE BODY');
      const serverState = await page.evaluate(() => ({ receipts: [...transport.receipts], conflicts: [...transport.conflicts] }));
      await page.reload(); await install();
      await page.evaluate(async ({ database, serverState }) => {
        await create(database, {}, {}, 'drafts', serverState);
        if (runtime.snapshot().pending.length !== 0) throw new Error('Editor recovery entered the sending queue');
        const mount = document.createElement('div'); document.body.append(mount); window.uiRoot = lib.createRoot(mount);
        uiRoot.render(lib.React.createElement(lib.ConflictRecoveryList, { runtime }));
      }, { database, serverState });
      await page.getByRole('button', { name: '查看本机合并草稿 1', exact: true }).click();
      await page.getByRole('textbox', { name: '保留的合并正文', exact: true }).waitFor();
      assert.equal(await page.getByRole('textbox', { name: '保留的合并正文', exact: true }).inputValue(), 'UNSUBMITTED NONMERGE BODY');
      assert.equal(await page.getByRole('textbox', { name: '保留的个人补充', exact: true }).inputValue(), 'UNSUBMITTED NONMERGE NOTE');
    });
  }
  await test('fresh-discovery-keeps-authorized-orphan-visible-beside-tombstoned-ancestry', async () => {
    const result = await page.evaluate(async () => {
      const first = await editable('pr08b-editor-deleted-sibling');
      await runtime.saveConflictDraft(first, 'DELETED CLASS INPUT', '', crypto.randomUUID(), 1);
      const secondClass = crypto.randomUUID(), secondDocument = crypto.randomUUID();
      const stamp = new Date().toISOString();
      transport.projections.push({ object_type: 'class_session', object_id: secondClass, parent_id: ids.course, revision: 1, adopted_revision_id: null, updated_at: stamp },
        { object_type: 'review_document', object_id: secondDocument, parent_id: secondClass, revision: 1, adopted_revision_id: null, updated_at: stamp });
      await runtime.unlock('synthetic-password'); await runtime.flush();
      const op = { ...operation('SECOND ORIGINAL'), object_id: secondDocument };
      const second = await runtime.enqueue(op, { ...ancestry, classSessionId: secondClass }, 1);
      await runtime.flush(); await runtime.inspectConflict(second);
      await runtime.saveConflictDraft(second, 'AUTHORIZED OTHER CLASS INPUT', '', crypto.randomUUID(), 1);
      await runtime.resolve(first, { base_revision: 2, resolution: 'use_server' }, 2);
      await runtime.resolve(second, { base_revision: 2, resolution: 'use_server' }, 2);
      if (runtime.snapshot().pending.length !== 0) throw new Error('Fixture failed to acknowledge both document identities: ' + JSON.stringify(runtime.snapshot().pending.map(value => ({ state: value.state, error: value.error }))));
      await cache.applyDeletion({ object_type: 'class_session', object_id: ids.classroom, deletion_id: crypto.randomUUID(), state: 'access_revoked' }, { retainDrafts: true });
      transport.projections = transport.projections.filter(value => ![ids.classroom, ids.document].includes(value.object_id));
      const server = transport; runtime.close(); await create('pr08b-editor-deleted-sibling'); runtime.close();
      runtime = new lib.OfflineSyncRuntime(cache, server, { enabled: true }); await runtime.unlock('synthetic-password');
      const sources = await runtime.listConflictDraftSources(); const available = sources.find(value => value.state === 'available');
      const [draft] = await runtime.readConflictDrafts(available.source);
      return { pending: runtime.snapshot().pending.length, states: sources.map(value => value.state).sort(), body: draft.body,
        restrictedSource: sources.find(value => value.state === 'restricted').source, count: (await records('pr08b-editor-deleted-sibling')).length };
    });
    assert.deepEqual(result, { pending: 0, states: ['available', 'restricted'], body: 'AUTHORIZED OTHER CLASS INPUT', restrictedSource: null, count: 2 });
  });
  await test('second-tab-reconciles-an-operation-already-acknowledged-in-first-tab', async () => {
    const other = await context.newPage();
    try {
      await page.evaluate(async () => {
        await create('pr08b-shared-tabs');
        await runtime.enqueue(operation('TWO TABS ONE OPERATION'), ancestry, 1);
      });
      await install(other);
      await other.evaluate(() => create('pr08b-shared-tabs'));
      assert.equal(await other.evaluate(() => runtime.snapshot().pending.length), 1);
      await page.evaluate(() => runtime.flush());
      assert.equal(await page.evaluate(() => runtime.snapshot().pending.length), 0);
      const receipts = await page.evaluate(() => [...transport.receipts.entries()]);
      await other.evaluate(values => { transport.receipts = new Map(values); }, receipts);
      await other.evaluate(() => runtime.flush());
      assert.deepEqual(await other.evaluate(() => ({ pending: runtime.snapshot().pending.length,
        pushes: transport.pushes.length })), { pending: 0, pushes: 0 });
    } finally {
      await other.evaluate(() => window.runtime?.close()).catch(() => {});
      await other.close();
    }
  });
} finally {
  await context.close();
  await new Promise(resolve => server.close(resolve));
  await writeFile(join(output, 'sync-browser.json'), JSON.stringify({ chromium: browserVersion, synthetic_transport: true,
    native_crypto_indexeddb: true, results, passed: results.length === 54 && results.every(result => result.passed) }, null, 2));
}
console.log(JSON.stringify({ tests: results.length, passed: results.filter(result => result.passed).length, results }));
if (results.length !== 54 || results.some(result => !result.passed)) process.exitCode = 1;
