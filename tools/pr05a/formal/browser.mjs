import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { createInterface } from 'node:readline';

const { chromium } = await import(pathToFileURL(process.env.PR05A_PLAYWRIGHT_MODULE).href);
const context = await chromium.launchPersistentContext(process.env.PR05A_BROWSER_PROFILE, {
  headless: true, args: [`--ignore-certificate-errors-spki-list=${process.env.PR05A_PIN}`],
});
const input = createInterface({ input: process.stdin });
try {
  const page = context.pages()[0];
  await page.goto(process.env.PR05A_ORIGIN);
  async function call(path, body, csrf, extra = {}) {
    return page.evaluate(async ({ path, body, csrf, extra }) => {
      const response = await fetch('/api/web/v1' + path, {
        method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}), ...extra },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      return { status: response.status, body: await response.json(), noStore: response.headers.get('Cache-Control') };
    }, { path, body, csrf, extra });
  }
  const csrf = (await call('/auth/csrf')).body.csrf_token;
  const login = await call('/auth/login', {
    email: process.env.PR05A_EMAIL, password: process.env.PR05A_PASSWORD,
    remember_me: false, device_name: 'PR05a native browser candidate',
  }, csrf, { 'Idempotency-Key': randomUUID() });
  assert.equal(login.status, 200);
  assert.equal(login.body.state, 'authenticated');
  console.log(JSON.stringify({ phase: 'device-ready', device_id: login.body.device_id }));
  await input[Symbol.asyncIterator]().next();
  const body = { device_id: login.body.device_id, key_epoch: 1, scope: 'drafts', class_session_id: null };
  async function reauth(purpose, parameters) {
    const key = randomUUID();
    const result = await call('/auth/reauth', {
      password: process.env.PR05A_PASSWORD,
      operation: { purpose, target: { kind: 'device', id: body.device_id }, candidate_parameters: { idempotency_key: key, body: parameters } },
    }, login.body.csrf_token, { 'Idempotency-Key': randomUUID() });
    assert.equal(result.status, 200);
    return { 'Idempotency-Key': key, 'X-Reauth-Token': result.body.reauth_token };
  }
  const proof = await reauth('unlockCache', body);
  const unlocked = await call('/me/cache-unlock', body, login.body.csrf_token, proof);
  assert.equal(unlocked.status, 200);
  assert.equal(unlocked.noStore, 'no-store');
  const storage = await page.evaluate(async grant => {
    const bytes = Uint8Array.from(atob(grant.key_material_b64), character => character.charCodeAt(0));
    const key = await crypto.subtle.importKey('raw', bytes, 'AES-GCM', false, ['encrypt', 'decrypt']);
    bytes.fill(0);
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const aad = new TextEncoder().encode(JSON.stringify([grant.key_id, grant.key_epoch, 'synthetic-formal-object', 1]));
    const plain = new TextEncoder().encode('PR05a formal synthetic 中文');
    const ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce, additionalData: aad }, key, plain);
    const db = await new Promise(resolve => {
      const request = indexedDB.open('pr05a-formal', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('ciphertexts');
      request.onsuccess = () => resolve(request.result);
    });
    await new Promise((resolve, reject) => {
      const transaction = db.transaction('ciphertexts', 'readwrite');
      transaction.objectStore('ciphertexts').put({ ciphertext, nonce, aad }, 'sample');
      transaction.oncomplete = resolve;
      transaction.onabort = () => reject(transaction.error);
    });
    const restored = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: nonce, additionalData: aad }, key, ciphertext);
    db.close();
    return { extractable: key.extractable, restored: new TextDecoder().decode(restored) === 'PR05a formal synthetic 中文' };
  }, unlocked.body);
  assert.deepEqual(storage, { extractable: false, restored: true });
  const oldCookie = (await context.cookies()).find(cookie => cookie.name === '__Host-classroom_session');
  const revocation = await call(`/me/devices/${body.device_id}/revoke`, {}, login.body.csrf_token, await reauth('revokeDevice', {}));
  assert.equal(revocation.status, 200);
  await context.addCookies([oldCookie]);
  assert.equal((await call('/me/cache-unlock', body, login.body.csrf_token, proof)).status, 401);
  console.log(JSON.stringify({ phase: 'complete', browser: context.browser()?.version(),
    real_cookie_login: true, real_reauthentication: true, native_webcrypto_idb: storage,
    real_device_revoke_blocks_old_unlock: true, no_store: true }));
} finally {
  input.close();
  process.stdin.pause();
  await context.close();
}
