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
    };
    window.ancestry = { semesterId: ids.semester, courseId: ids.course, classSessionId: ids.classroom };
    window.operation = (text = 'SYNTHETIC PRIVATE LOCAL TEXT', revision = 1) => {
      const draft = crypto.randomUUID();
      return { op_id: crypto.randomUUID(), object_id: ids.document, object_type: 'personal_revision',
        draft_id: draft, base_revision: revision,
        payload: { base_revision: revision, base_content_revision_id: null, draft_id: draft,
          body_markdown: text, personal_note: 'Synthetic personal note' } };
    };
    window.expectCode = async (promise, code) => {
      try { await promise; throw new Error('Expected ' + code); }
      catch (error) { if (error.code !== code) throw error; }
    };
    class NativeCache extends lib.TrustedCache {
      unlockScope(scope) {
        // Public synthetic fixture. Each unlock receives independent key material.
        return this.unlock(scope, null, async () => ({ key_id: 'synthetic-' + scope, key_epoch: 1,
          key_material_b64: btoa(String.fromCharCode(...new Uint8Array(32).fill(17))), scope,
          expires_at: new Date(Date.now() + 120000).toISOString(), cache_policy_revision: 'synthetic-only' }));
      }
    }
    class Transport {
      constructor() {
        this.receipts = new Map(); this.pushes = []; this.resolutions = []; this.mode = 'apply'; this.pages = [];
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
      async receipt(id) { if (!this.receipts.has(id)) throw Object.assign(new Error('Missing'), { code: 'NOT_FOUND' }); return structuredClone(this.receipts.get(id)); }
      async makeReceipt(operation, kind = 'syncOperation', value = operation) {
        return { op_id: operation.op_id, operation: kind, request_sha256: await lib.syncFingerprint(value),
          status: this.mode === 'conflict' ? 'conflict' : 'applied', object_id: operation.object_id,
          revision: this.serverRevision, conflict_id: this.mode === 'conflict' ? ids.conflict : null, task_id: null };
      }
      async push(operation, batchId) {
        this.pushes.push({ operation: structuredClone(operation), batchId });
        const receipt = await this.makeReceipt(operation);
        this.receipts.set(operation.op_id, receipt);
        if (this.mode === 'conflict') this.conflictValue = { id: ids.conflict, object_id: ids.document,
          base_revision: operation.base_revision, server_revision: this.serverRevision, local_operation: structuredClone(operation),
          server_content: { body_markdown: 'SYNTHETIC CLOUD TEXT' }, resolved: false };
        if (this.mode === 'unknown') throw Object.assign(new Error('Synthetic response loss'), { code: 'RESULT_UNKNOWN' });
        if (this.mode === 'delay') await new Promise(resolve => { this.release = resolve; });
        if (this.mode === 'invalid') receipt.request_sha256 = '0'.repeat(64);
        return { receipts: [structuredClone(receipt)], cursor: 'unconsumed-server-hint' };
      }
      async conflict() { return structuredClone(this.conflictValue); }
      async resolve(id, body, opId) {
        this.resolutions.push({ id, body: structuredClone(body), opId });
        if (body.base_revision !== this.serverRevision) throw Object.assign(new Error('Synthetic stale CAS'), { code: 'REVISION_CONFLICT' });
        const receipt = { op_id: opId, operation: 'resolveConflict', request_sha256: await lib.syncFingerprint({ conflict_id: id, body }),
          status: 'applied', object_id: body.resolution === 'copy_to_new_note' ? crypto.randomUUID() : ids.document,
          revision: 3, conflict_id: null, task_id: null };
        this.receipts.set(opId, receipt);
        return receipt;
      }
    }
    window.create = async (name, limits = {}, runtimeOptions = {}, scope = 'drafts') => {
      window.cache = new NativeCache({ enabled: true, recordingEnabled: true, databaseName: name,
        limits: { draftBytes: 2000000, draftOperations: 100, recordingBytes: 2000000, ...limits } });
      cache.bind({ ownerId: ids.owner, deviceId: ids.device, sessionId: ids.session });
      window.transport = new Transport();
      window.runtime = new lib.OfflineSyncRuntime(cache, transport, { enabled: true, ...runtimeOptions });
      if (scope === 'learning') await runtime.unlockLearning('synthetic-password');
      else await runtime.unlock('synthetic-password');
    };
    window.learningReference = (revision = 1) => ({ scope: 'learning', objectType: 'review_document',
      objectId: ids.document, revision, generation: 1, ancestry });
    window.hydrate = (reference, text) => async () => ({ objectId: reference.objectId,
      revision: reference.revision, bytes: new TextEncoder().encode(text) });
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
  });
}

async function test(name, run) {
  const started = Date.now();
  try { await run(); results.push({ name, passed: true, elapsed_ms: Date.now() - started }); }
  catch (error) { results.push({ name, passed: false, elapsed_ms: Date.now() - started, error: error.message }); }
  finally { await page.evaluate(() => { window.uiRoot?.unmount(); window.uiRoot = null; window.runtime?.close(); }).catch(() => {}); }
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
    await page.evaluate(() => cache.lock());
    await page.getByRole('status').filter({ hasText: '同步已锁定，请重新验证身份。' }).waitFor();
    assert.equal(await page.locator('textarea').count(), 0);
    assert.equal((await page.locator('body').innerText()).includes('UI PRIVATE LOCK SENTINEL'), false);
    assert.equal((await page.locator('body').innerText()).includes('UNSENT PRIVATE EDIT'), false);
    assert.equal(await page.evaluate(async () => (await records('pr08b-ui-lock')).length), 1);
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
    { unlocked: false, records: 1, pushes: 1 });
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
      return { oldCode, empty, mismatchCode, text, afterSameRevisionChange: await runtime.readLearning(current) };
    });
    assert.deepEqual(result, { oldCode: 'AUTHORITY_UNAVAILABLE', empty: null, mismatchCode: 'INVALID_HYDRATION',
      text: 'CURRENT CONTENT', afterSameRevisionChange: null });
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
  await test('legacy-untyped-learning-is-invalidated-with-unique-pools-preserved', async () => {
    const result = await page.evaluate(async () => {
      await create('pr08b-legacy-learning');
      const draft = await runtime.enqueue(operation('UNIQUE LEGACY DRAFT'), ancestry, 1);
      await cache.unlockScope('learning');
      const legacy = { ...learningReference(), verifiedAt: Date.now() }; delete legacy.objectType;
      await cache.put(legacy, new TextEncoder().encode('OLD UNTYPED LEARNING'));
      const unlockRecording = () => cache.unlock('recording_recovery', ids.classroom, async () => ({
        key_id: 'synthetic-recording_recovery', key_epoch: 1,
        key_material_b64: btoa(String.fromCharCode(...new Uint8Array(32).fill(17))), scope: 'recording_recovery',
        expires_at: new Date(Date.now() + 120000).toISOString(), cache_policy_revision: 'synthetic-only' }));
      await unlockRecording();
      const pcm = { scope: 'recording_recovery', objectId: 'synthetic-pcm', revision: 1, generation: 1, ancestry };
      await cache.put(pcm, new Uint8Array([1, 2, 3, 4]));
      await runtime.unlock('synthetic-password');
      const stored = await records('pr08b-legacy-learning');
      await unlockRecording();
      return { scopes: stored.map(value => value.header.scope).sort(),
        draft: (await runtime.readDraft(draft)).payload.body_markdown,
        pcm: Array.from(await cache.read(pcm, ids.classroom)) };
    });
    assert.deepEqual(result, { scopes: ['drafts', 'recording_recovery'], draft: 'UNIQUE LEGACY DRAFT', pcm: [1, 2, 3, 4] });
  });
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
      return { writeCode, readCode, text: new TextDecoder().decode(await runtime.readLearning(reference)),
        count: (await records('pr08b-learning-kind')).length };
    });
    assert.deepEqual(result, { writeCode: 'REVISION_CONFLICT', readCode: 'REVISION_CONFLICT', text: 'TYPED LEARNING CONTENT', count: 1 });
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
    native_crypto_indexeddb: true, results, passed: results.length === 25 && results.every(result => result.passed) }, null, 2));
}
console.log(JSON.stringify({ tests: results.length, passed: results.filter(result => result.passed).length, results }));
if (results.length !== 25 || results.some(result => !result.passed)) process.exitCode = 1;
