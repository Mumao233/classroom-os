import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { createInterface } from 'node:readline';

const { chromium } = await import(pathToFileURL(process.env.PR08B_PLAYWRIGHT_MODULE).href);
const browser = await chromium.launch({
  headless: true,
  args: [`--ignore-certificate-errors-spki-list=${process.env.PR08B_PIN}`],
});
const first = await browser.newContext();
const second = await browser.newContext();
const input = createInterface({ input: process.stdin });
const scenario = JSON.parse(process.env.PR08B_SCENARIO);
const credentials = { email: process.env.PR08B_EMAIL, password: process.env.PR08B_PASSWORD };

async function initialize(page, login) {
  return page.evaluate(async ({ credentials, login }) => {
    const { AuthClient } = await import('/sync.js');
    window.dropNextSyncResponse = false;
    window.lostResponseReceipt = null;
    window.auth = new AuthClient(async (url, options) => {
      const response = await fetch(url, options);
      if (window.dropNextSyncResponse && String(url).endsWith('/sync/operations')) {
        window.dropNextSyncResponse = false;
        const committed = await response.clone().json();
        window.lostResponseReceipt = committed.receipts?.[0] ?? null;
        throw new TypeError('Synthetic lost response after actual server commit');
      }
      return response;
    });
    await window.auth.bootstrap();
    if (login) {
      await window.auth.login({ ...credentials, remember_me: false, device_name: 'PR08b actual browser device' });
    }
    window.session = await window.auth.bootstrap();
    window.sync = window.auth.createOfflineSync({ enabled: true, databaseName: 'pr08b-formal',
      limits: { draftBytes: 2_000_000, draftOperations: 30, recordingBytes: 1_000_000 } });
    return window.session.device_id;
  }, { credentials, login });
}

function operation(document, text) {
  const draft = crypto.randomUUID();
  return { op_id: crypto.randomUUID(), object_type: 'personal_revision', object_id: document.id,
    draft_id: draft, base_revision: document.revision,
    payload: { base_revision: document.revision, base_content_revision_id: document.latest_personal_revision_id,
      draft_id: draft, body_markdown: text, personal_note: 'Synthetic personal explanation' } };
}

const ancestry = { semesterId: scenario.semester_id, courseId: scenario.course_id,
  classSessionId: scenario.class_session_id };
try {
  const page = await first.newPage();
  await page.goto(process.env.PR08B_ORIGIN);
  const device = await initialize(page, true);
  console.log(JSON.stringify({ phase: 'device-ready', device_id: device }));
  await input[Symbol.asyncIterator]().next();
  await page.evaluate(password => window.sync.unlock(password), credentials.password);
  const openedRevision = await page.evaluate(note => window.sync.readReviewRevision(note.id,
    note.latest_personal_revision_id), scenario.note);
  assert.equal(openedRevision.body_markdown, 'Initial synthetic content');

  const draft = operation(scenario.note, 'Offline draft survives a browser refresh.');
  await first.setOffline(true);
  const reference = await page.evaluate(async ({ draft, ancestry }) => {
    const reference = await window.sync.enqueue(draft, ancestry, 1);
    return reference;
  }, { draft, ancestry });
  assert.equal(await page.evaluate(() => window.sync.snapshot().pending[0].state), 'local_only');
  await page.evaluate(() => window.sync.close());
  await first.setOffline(false);
  await page.reload();
  await initialize(page, false);
  await page.evaluate(password => window.sync.unlock(password), credentials.password);
  const restored = await page.evaluate(reference => window.sync.readDraft(reference), reference);
  assert.deepEqual(restored, draft);

  await page.evaluate(async () => {
    window.dropNextSyncResponse = true;
    await window.sync.flush();
  });
  const committedBeforeLoss = await page.evaluate(() => window.lostResponseReceipt);
  assert.equal(committedBeforeLoss?.status, 'applied');
  assert.equal(committedBeforeLoss.op_id, draft.op_id);
  assert.equal(committedBeforeLoss.revision, scenario.note.revision + 1);
  assert.equal(await page.evaluate(() => window.sync.snapshot().pending.length), 1);
  await page.evaluate(password => window.sync.unlock(password), credentials.password);
  await page.evaluate(() => window.sync.flush());
  assert.equal(await page.evaluate(() => window.sync.snapshot().pending.length), 0);
  const current = await page.evaluate(id => window.auth.request(`/review-documents/${id}`), scenario.note.id);
  assert.equal(current.revision, scenario.note.revision + 1);
  const revisions = await page.evaluate(id => window.auth.request(`/review-documents/${id}/revisions`), scenario.note.id);
  assert.equal(revisions.items.filter(item => item.body_markdown === draft.payload.body_markdown).length, 1);

  const local = operation(current, 'Device A preserves its conflicting draft.');
  const localReference = await page.evaluate(({ local, ancestry }) => window.sync.enqueue(local, ancestry, 2), { local, ancestry });
  const other = await second.newPage();
  await other.goto(process.env.PR08B_ORIGIN);
  const otherDevice = await initialize(other, true);
  assert.notEqual(otherDevice, device);
  const remote = operation(current, 'Device B has independently saved this content.');
  const remoteResult = await other.evaluate(remote => window.auth.request('/sync/operations',
    { operations: [remote] }, { key: crypto.randomUUID() }), remote);
  assert.equal(remoteResult.receipts[0].status, 'applied');
  await page.evaluate(password => window.sync.unlock(password), credentials.password);
  await page.evaluate(() => window.sync.flush());
  await page.evaluate(password => window.sync.unlock(password), credentials.password);
  await page.evaluate(() => window.sync.flush());
  assert.equal(await page.evaluate(() => window.sync.snapshot().pending[0].state), 'conflict');
  const conflict = await page.evaluate(reference => window.sync.inspectConflict(reference), localReference);
  assert.equal(conflict.local_operation.payload.body_markdown, local.payload.body_markdown);
  assert.equal(conflict.server_content.body_markdown, remote.payload.body_markdown);
  await page.evaluate(async reference => {
    const { React, createRoot, ConflictPanel } = await import('/sync.js');
    const mount = document.createElement('main');
    document.body.append(mount);
    window.conflictRoot = createRoot(mount);
    window.conflictRoot.render(React.createElement(ConflictPanel, {
      runtime: window.sync, reference, generation: 3,
    }));
  }, localReference);
  await page.getByRole('radio', { name: '采用云端版本' }).check();
  await page.getByRole('button', { name: '确认处理', exact: true }).click();
  await page.getByRole('status').filter({ hasText: '冲突处理已由服务器确认。' }).waitFor();
  assert.equal(await page.evaluate(() => window.sync.snapshot().pending.length), 0);
  const resolved = await page.evaluate(id => window.auth.request(`/review-documents/${id}`), scenario.note.id);
  const effective = await page.evaluate(id => window.auth.request(`/review-revisions/${id}`), resolved.latest_personal_revision_id);
  assert.equal(effective.body_markdown, remote.payload.body_markdown);
  const retained = await page.evaluate(id => window.auth.request(`/review-documents/${id}/revisions`), scenario.note.id);
  assert(retained.items.some(item => item.body_markdown === local.payload.body_markdown));
  await page.evaluate(() => window.conflictRoot.unmount());

  const partialReference = await page.evaluate(async ({ partial, ancestry, password }) => {
    const { React, createRoot, ConflictPanel } = await import('/sync.js');
    window.partialSync = window.auth.createOfflineSync({ enabled: true, databaseName: 'pr08b-partial-revocation',
      limits: { draftBytes: 2_000_000, draftOperations: 30, recordingBytes: 1_000_000 } });
    await window.partialSync.unlock(password);
    const reference = await window.partialSync.enqueue(partial.operation, ancestry, 1);
    await window.partialSync.flush();
    const mount = document.createElement('main');
    document.body.append(mount);
    window.partialRoot = createRoot(mount);
    window.partialRoot.render(React.createElement(ConflictPanel, {
      runtime: window.partialSync, reference, generation: 2,
    }));
    return reference;
  }, { partial: scenario.partial, ancestry, password: credentials.password });
  await page.getByRole('textbox', { name: '合并正文', exact: true }).waitFor();
  const partialText = scenario.partial.operation.payload.body_markdown;
  assert((await page.locator('body').innerText()).includes(partialText));
  const revoked = await page.evaluate(async () => {
    const response = await fetch('/test/revoke-partial-source', { method: 'POST' });
    return response.ok && (await response.json()).revoked;
  });
  assert.equal(revoked, true);
  await page.waitForFunction(() => !window.partialSync.snapshot().unlocked, null, { timeout: 15_000 });
  await page.getByRole('status').filter({ hasText: '同步已锁定，请重新验证身份。' }).waitFor();
  assert.equal((await page.locator('body').innerText()).includes(partialText), false);
  assert.equal(await page.getByRole('textbox').count(), 0);
  const authority = await page.evaluate(async partial => {
    const paths = [`/review-documents/${partial.document_id}`,
      `/review-revisions/${partial.unaffected_revision_id}`,
      `/review-revisions/${partial.base_content_revision_id}`, `/sync/conflicts/${partial.conflict_id}`];
    const statuses = [];
    for (const path of paths) statuses.push((await fetch(`/api/web/v1${path}`)).status);
    return statuses;
  }, scenario.partial);
  assert.deepEqual(authority, [200, 200, 404, 404]);
  await page.evaluate(async password => {
    window.partialRoot.unmount();
    window.partialSync.close();
    window.partialSync = window.auth.createOfflineSync({ enabled: true, databaseName: 'pr08b-partial-revocation',
      limits: { draftBytes: 2_000_000, draftOperations: 30, recordingBytes: 1_000_000 } });
    await window.partialSync.unlock(password);
  }, credentials.password);
  const partialState = await page.evaluate(() => window.partialSync.snapshot());
  assert.equal(partialState.pending.length, 1);
  assert.equal(partialState.pending[0].state, 'restricted');
  const restoredReference = partialState.pending[0].reference;
  for (const [field, value] of Object.entries(partialReference)) {
    assert.deepEqual(restoredReference[field], value);
  }
  const restoredNamespace = await page.evaluate(() => ({ ownerId: window.session.user.id,
    deviceId: window.session.device_id }));
  assert.equal(restoredReference.ownerId, restoredNamespace.ownerId);
  assert.equal(restoredReference.deviceId, restoredNamespace.deviceId);
  assert.equal(restoredReference.schema, 2);
  assert.match(restoredReference.keyId, /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i);
  assert(Number.isSafeInteger(restoredReference.keyVersion) && restoredReference.keyVersion > 0);
  assert.equal(await page.evaluate(async reference => {
    try { await window.partialSync.readDraft(reference); return false; } catch { return true; }
  }, partialReference), true);
  await page.evaluate(() => window.partialSync.close());
  await page.evaluate(password => window.sync.unlock(password), credentials.password);

  const deletedDraft = operation(resolved, 'The unique local draft remains encrypted after deletion.');
  const deletedReference = await page.evaluate(({ draft, ancestry }) => window.sync.enqueue(draft, ancestry, 4),
    { draft: deletedDraft, ancestry });
  await other.evaluate(async id => {
    const preview = await window.auth.request(`/class-sessions/${id}/deletion-preview`);
    await window.auth.request(`/class-sessions/${id}/delete`, {
      base_revision: preview.revision, confirmation_token: preview.confirmation_token,
    });
  }, scenario.class_session_id);
  await page.waitForFunction(() => !window.sync.snapshot().unlocked, null, { timeout: 15_000 });
  await page.evaluate(password => window.sync.unlock(password), credentials.password);
  const restricted = await page.evaluate(() => window.sync.snapshot());
  assert.equal(restricted.pending.length, 1);
  assert.equal(restricted.pending[0].state, 'restricted');
  const denied = await page.evaluate(async reference => {
    try { await window.sync.readDraft(reference); return false; } catch { return true; }
  }, deletedReference);
  assert(denied);
  const late = await other.evaluate(draft => window.auth.request('/sync/operations',
    { operations: [draft] }, { key: crypto.randomUUID() }), deletedDraft);
  assert(['deleted', 'rejected'].includes(late.receipts[0].status));

  await page.evaluate(async password => {
    await window.auth.change('revokeDevice', { kind: 'device', id: window.session.device_id }, {},
      password, undefined, `/me/devices/${window.session.device_id}/revoke`);
  }, credentials.password);
  assert.equal(await page.evaluate(() => window.sync.snapshot().unlocked), false);
  await page.evaluate(() => window.sync.close());
  await other.evaluate(() => window.sync.close());
  console.log(JSON.stringify({ phase: 'complete', browser: browser.version(), public_contract: '0.2.1',
    review_contract_candidate: 'pr07b-1',
    actual_http_postgres: true, native_crypto_indexeddb: true, two_authenticated_devices: true,
    offline_refresh_recovery: true, lost_ack_idempotency: true, cas_conflict_preserved: true,
    explicit_resolution: true, actual_conflict_panel: true,
    partial_source_revocation_clears_view: true, partial_source_revocation_survives_reopen: true,
    deletion_blocks_replay: true, device_revocation_locks: true }));
} finally {
  input.close();
  process.stdin.pause();
  await browser.close();
}
