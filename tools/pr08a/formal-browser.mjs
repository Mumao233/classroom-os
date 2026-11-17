import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { createInterface } from 'node:readline';

const { chromium } = await import(pathToFileURL(process.env.PR08A_PLAYWRIGHT_MODULE).href);
const context = await chromium.launchPersistentContext(process.env.PR08A_PROFILE, {
  headless: true, args: [`--ignore-certificate-errors-spki-list=${process.env.PR08A_PIN}`],
});
const input = createInterface({ input: process.stdin });
try {
  const page = context.pages()[0];
  await page.goto(process.env.PR08A_ORIGIN);
  const device = await page.evaluate(async ({ email, password }) => {
    const { AuthClient } = await import('/cache.js');
    window.auth = new AuthClient();
    await auth.bootstrap();
    await auth.login({ email, password, remember_me: false, device_name: 'PR08a actual AuthClient' });
    window.session = await auth.bootstrap();
    window.cache = auth.createTrustedCache({ enabled: true, databaseName: 'pr08a-formal', limits: { draftBytes: 1_000_000, draftOperations: 10, recordingBytes: 1_000_000 } });
    return session.device_id;
  }, { email: process.env.PR08A_EMAIL, password: process.env.PR08A_PASSWORD });
  console.log(JSON.stringify({ phase: 'device-ready', device_id: device }));
  await input[Symbol.asyncIterator]().next();
  const result = await page.evaluate(async password => {
    await cache.unlockScope('drafts', password);
    const reference = { scope: 'drafts', objectId: 'formal-draft', revision: 1, generation: 1 };
    const write = { ...reference, operationId: 'formal-operation', ancestry: { semesterId: null, courseId: null, classSessionId: null } };
    await cache.put(write, new TextEncoder().encode('正式合成草稿'));
    const restored = new TextDecoder().decode(await cache.read(reference));
    await cache.revalidate();
    await auth.change('revokeDevice', { kind: 'device', id: session.device_id }, {}, password, undefined, `/me/devices/${session.device_id}/revoke`);
    let revoked;
    try { await cache.read(reference); } catch (error) { revoked = error.code; }
    cache.close();
    return { restored, revoked };
  }, process.env.PR08A_PASSWORD);
  assert.deepEqual(result, { restored: '正式合成草稿', revoked: 'LOCKED' });
  console.log(JSON.stringify({ phase: 'complete', browser: context.browser()?.version(), public_contract: '0.2.1',
    actual_authclient_login_reauth_unlock: true, actual_postgres_key: true, native_crypto_indexeddb: true, actual_device_revocation: true }));
} finally { input.close(); process.stdin.pause(); await context.close(); }
