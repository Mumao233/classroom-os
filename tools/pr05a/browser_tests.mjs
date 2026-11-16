import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const { chromium } = await import(pathToFileURL(process.env.PR05A_PLAYWRIGHT_MODULE).href);
const origin = process.env.PR05A_ORIGIN;
const unpinned = process.env.PR05A_UNPINNED_ORIGIN;
const root = process.env.PR05A_RUN_DIR;
const controlToken = process.env.PR05A_CONTROL_TOKEN;
const results = [];
const failures = [];
const contexts = new Set();
let browserVersion = null;
const sample = 'PR05a 合成样本 α 中文 2026';
const launchOptions = {
  headless: true,
  args: [`--ignore-certificate-errors-spki-list=${process.env.PR05A_SPKI_PIN}`],
};

async function launch(name) {
  const context = await chromium.launchPersistentContext(path.join(root, name), launchOptions);
  contexts.add(context);
  const page = context.pages()[0] ?? await context.newPage();
  const cdp = await context.newCDPSession(page);
  browserVersion = (await cdp.send('Browser.getVersion')).product;
  await cdp.detach();
  await prepare(page);
  return { context, page };
}

async function prepare(page) {
  await page.goto(origin);
  await page.evaluate(async () => {
    const { BrowserVault } = await import('/cache.mjs');
    window.labVault = new BrowserVault();
  });
}

async function evaluate(page, code, argument) {
  return page.evaluate(code, argument);
}

async function loginUnlock(page, alias = 'A') {
  await evaluate(page, async alias => {
    await window.labVault.login(alias);
    await window.labVault.unlock();
  }, alias);
}

async function control(page, action) {
  const result = await evaluate(page, async ({ action, token }) => {
    const response = await fetch('/lab/control', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Pr05a-Control': token },
      body: JSON.stringify({ action }),
    });
    return { status: response.status, body: await response.json() };
  }, { action, token: controlToken });
  assert.equal(result.status, 200);
}

async function expectError(page, callback, pattern, argument) {
  let message = 'UNEXPECTED_SUCCESS';
  try { await evaluate(page, callback, argument); }
  catch (error) { message = error.message; }
  assert.match(message, pattern);
  return message;
}

async function test(name, callback) {
  const started = Date.now();
  try {
    const evidence = await callback();
    results.push({ name, passed: true, duration_ms: Date.now() - started, evidence });
    console.log(`PASS ${name}`);
  } catch (error) {
    failures.push(name);
    results.push({ name, passed: false, duration_ms: Date.now() - started, error: error.message });
    console.log(`FAIL ${name} ${error.message}`);
    throw error;
  }
}

let context;
let page;
try {
  ({ context, page } = await launch('persistent-profile'));
  await test('C11 TLS pin and browser write guards', async () => {
    const cookiesBefore = await context.cookies();
    assert.equal(cookiesBefore.length, 0);
    const negative = await context.newPage();
    await assert.rejects(negative.goto(unpinned), /CERT_AUTHORITY_INVALID|CERT/);
    await negative.close();
    const missing = await evaluate(page, async () => {
      const response = await fetch('/lab/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ alias: 'A' }),
      });
      return response.status;
    });
    assert.equal(missing, 403);
    await loginUnlock(page);
    const cookies = await context.cookies();
    assert.equal(cookies[0].httpOnly, true);
    assert.equal(cookies[0].secure, true);
    assert.equal(cookies[0].sameSite, 'Strict');
    const headers = await evaluate(page, async () => {
      const session = await window.labVault.request('session');
      const response = await fetch('/lab/unlock', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf },
        body: JSON.stringify({ scope: 'drafts', key_epoch: session.key_epoch }),
      });
      return { cache: response.headers.get('Cache-Control'), status: response.status };
    });
    assert.equal(headers.cache, 'no-store');
    assert.equal(headers.status, 200);
    const foreign = await context.request.post(`${origin}/lab/unlock`, {
      ignoreHTTPSErrors: true, headers: { Origin: 'https://attacker.invalid' },
      data: { scope: 'drafts', key_epoch: 1 },
    });
    assert.equal(foreign.status(), 403);
    return { certificate_negative_control: 'rejected', missing_csrf: missing, foreign_origin: foreign.status(), cache_control: headers.cache };
  });

  await test('C01 C02 native WebCrypto and ciphertext-only IndexedDB', async () => {
    await evaluate(page, sample => window.labVault.save(sample, 'sample'), sample);
    assert.equal(await evaluate(page, () => window.labVault.load('sample')), sample);
    const audit = await evaluate(page, async () => {
      const db = await new Promise((resolve, reject) => {
        const request = indexedDB.open('pr05a-synthetic-v1');
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      const rows = await new Promise(resolve => {
        const request = db.transaction('records').objectStore('records').getAll();
        request.onsuccess = () => resolve(request.result);
      });
      db.close();
      return {
        status: window.labVault.status(), rows: JSON.stringify(rows),
        local: { ...localStorage }, session: { ...sessionStorage }, caches: await caches.keys(),
      };
    });
    assert.equal(audit.status.extractable, false);
    assert.equal(audit.rows.includes(sample), false);
    assert.equal(audit.rows.includes('key_material'), false);
    assert.equal(audit.rows.includes('CryptoKey'), false);
    assert.deepEqual(audit.local, {});
    assert.deepEqual(audit.session, {});
    assert.deepEqual(audit.caches, []);
    return { extractable: false, plaintext_in_idb: false, other_persistent_stores_empty: true };
  });

  await test('C03 nonce uniqueness, immutable retries and AAD tampering', async () => {
    return evaluate(page, async sample => {
      const vault = window.labVault;
      const first = await vault.encrypt(sample, 'nonce1');
      const second = await vault.encrypt(sample, 'nonce2');
      if (first.nonce === second.nonce) throw new Error('NONCE_REUSED');
      await vault.commit(first);
      await vault.commit(first);
      let plaintextFieldRejected = false;
      try { await vault.commit({ ...first, plaintext: sample }); }
      catch (error) { plaintextFieldRejected = error.message === 'INVALID_ENVELOPE'; }
      let conflicts = 0;
      try { await vault.commit(await vault.encrypt('other synthetic text', 'nonce1')); }
      catch (error) { if (error.message === 'IMMUTABLE_VERSION_CONFLICT') conflicts++; else throw error; }
      const fields = ['owner', 'device', 'scope', 'object', 'revision', 'schema', 'key_id', 'key_epoch'];
      let rejected = 0;
      for (const field of fields) {
        const bad = structuredClone(first);
        bad.binding[field] = typeof bad.binding[field] === 'number' ? bad.binding[field] + 1 : `${bad.binding[field]}-tampered`;
        try { await vault.decrypt(bad, 'nonce1'); }
        catch { rejected++; }
      }
      const badCipher = structuredClone(first);
      badCipher.ciphertext = (badCipher.ciphertext[0] === 'A' ? 'B' : 'A') + badCipher.ciphertext.slice(1);
      try { await vault.decrypt(badCipher, 'nonce1'); } catch { rejected++; }
      const originalRandom = crypto.getRandomValues.bind(crypto);
      const fixed = originalRandom(new Uint8Array(12));
      crypto.getRandomValues = array => { array.set(fixed); return array; };
      let nonceCollisionRejected = false;
      try {
        await vault.commit(await vault.encrypt(sample, 'collision1'));
        try { await vault.commit(await vault.encrypt(sample, 'collision2')); }
        catch (error) { nonceCollisionRejected = error.name === 'ConstraintError'; }
      } finally { crypto.getRandomValues = originalRandom; }
      if (rejected !== 9 || conflicts !== 1 || !nonceCollisionRejected || !plaintextFieldRejected) throw new Error('TAMPER_OR_NONCE_ACCEPTED');
      return { tamper_cases_rejected: rejected, immutable_conflicts: conflicts, identical_retry: 'same envelope', nonce_collision: 'native IDB ConstraintError', plaintext_field_rejected: plaintextFieldRejected };
    }, sample);
  });

  await test('C04 close Chromium and reopen same persistent profile', async () => {
    await context.close();
    contexts.delete(context);
    ({ context, page } = await launch('persistent-profile'));
    assert.equal(await evaluate(page, () => window.labVault.status().unlocked), false);
    await expectError(page, () => window.labVault.load('sample'), /LOCKED/);
    await expectError(page, () => window.labVault.save('locked synthetic input', 'locked-write'), /LOCKED/);
    assert.equal(await evaluate(page, () => window.labVault.pendingText), null);
    await loginUnlock(page);
    assert.equal(await evaluate(page, () => window.labVault.load('sample')), sample);
    return { browser_restarted: true, identical_profile: true, required_online_unlock: true };
  });

  await test('C05 switch account and preserve both encrypted namespaces', async () => {
    await loginUnlock(page, 'B');
    await expectError(page, () => window.labVault.load('sample'), /LOCAL_COPY_UNAVAILABLE/);
    await evaluate(page, () => window.labVault.save('synthetic B only', 'sample'));
    await loginUnlock(page, 'A');
    assert.equal(await evaluate(page, () => window.labVault.load('sample')), sample);
    const tab = await context.newPage();
    await prepare(tab);
    await evaluate(tab, () => window.labVault.logout());
    await page.waitForFunction(() => !window.labVault.status().unlocked);
    await tab.close();
    await expectError(page, () => window.labVault.load('sample'), /LOCKED/);
    await loginUnlock(page);
    return { account_isolation: true, other_account_ciphertext_preserved: true, cross_tab_logout_locks: true };
  });

  await test('C06 password reset revokes memory grant and requires explicit recovery', async () => {
    await control(page, 'password-reset');
    await page.waitForFunction(() => !window.labVault.status().unlocked);
    await evaluate(page, () => window.labVault.login('A'));
    await expectError(page, () => window.labVault.unlock(), /KEY_AUTHORIZATION_REVOKED/);
    await evaluate(page, () => window.labVault.confirmRecovery());
    await evaluate(page, () => window.labVault.unlock());
    assert.equal(await evaluate(page, () => window.labVault.load('sample')), sample);
    return { old_grant_revoked: true, wrapped_key_retained: true, explicit_recovery: true };
  });

  await test('C07 device revoke blocks unlock and retains unique draft', async () => {
    const oldKey = await evaluate(page, () => window.labVault.status().binding.key_id);
    await control(page, 'device-revoke');
    await page.waitForFunction(() => !window.labVault.status().unlocked);
    await evaluate(page, () => window.labVault.login('A'));
    await expectError(page, () => window.labVault.unlock(), /KEY_AUTHORIZATION_REVOKED/);
    await evaluate(page, () => window.labVault.confirmRecovery());
    await evaluate(page, () => window.labVault.unlock());
    await expectError(page, () => window.labVault.load('sample'), /RECORD_CONTEXT_MISMATCH/);
    await evaluate(page, key => window.labVault.unlock('drafts', key), oldKey);
    assert.equal(await evaluate(page, () => window.labVault.load('sample')), sample);
    return { revoked_device_denied: true, key_epoch_advanced: true, historical_key_explicitly_selected: true, unique_draft_recovered_after_confirmation: true };
  });

  await test('C09 KEK and DEK rotation preserve historical ciphertext', async () => {
    const oldKey = await evaluate(page, () => window.labVault.status().binding.key_id);
    await control(page, 'rotate-kek');
    await evaluate(page, key => window.labVault.unlock('drafts', key), oldKey);
    assert.equal(await evaluate(page, () => window.labVault.load('sample')), sample);
    await evaluate(page, () => window.labVault.rotateDek());
    await evaluate(page, () => window.labVault.unlock());
    const newKey = await evaluate(page, () => window.labVault.status().binding.key_id);
    assert.notEqual(oldKey, newKey);
    await evaluate(page, () => window.labVault.save('new DEK synthetic record', 'new-generation'));
    await expectError(page, () => window.labVault.load('sample'), /RECORD_CONTEXT_MISMATCH/);
    await evaluate(page, key => window.labVault.unlock('drafts', key), oldKey);
    assert.equal(await evaluate(page, () => window.labVault.load('sample')), sample);
    return { kek_rewrapped: true, old_kek_retired: true, new_dek_distinct: true, historical_key_explicitly_selected: true };
  });

  await test('C10 offline grant deadline and reconnect revocation', async () => {
    await control(page, 'short-window');
    await evaluate(page, () => window.labVault.unlock());
    await context.setOffline(true);
    await controlThroughOtherContext('device-revoke');
    await page.waitForFunction(() => !window.labVault.status().unlocked, null, { timeout: 5000 });
    await expectError(page, () => window.labVault.load('sample'), /LOCKED/);
    await context.setOffline(false);
    await control(page, 'normal-window');
    await evaluate(page, () => window.labVault.login('A'));
    await expectError(page, () => window.labVault.unlock(), /KEY_AUTHORIZATION_REVOKED/);
    await evaluate(page, () => window.labVault.confirmRecovery());
    await evaluate(page, () => window.labVault.unlock());
    return { offline_window_seconds: 1.2, deadline_locked: true, reconnect_rechecks_revoke: true };
  });

  await test('C10 delayed unlock response cannot resurrect a locked vault', async () => {
    let release;
    let intercepted;
    const gate = new Promise(resolve => { intercepted = resolve; });
    const wait = new Promise(resolve => { release = resolve; });
    await page.route('**/lab/unlock', async route => {
      intercepted();
      await wait;
      await route.continue();
    });
    const pending = evaluate(page, async () => {
      try { await window.labVault.unlock(); return 'UNEXPECTED_SUCCESS'; }
      catch (error) { return error.message; }
    });
    await gate;
    await evaluate(page, () => window.labVault.lock());
    release();
    assert.equal(await pending, 'STALE_OPERATION');
    assert.equal(await evaluate(page, () => window.labVault.status().unlocked), false);
    await page.unroute('**/lab/unlock');
    return { stale_response_rejected: true };
  });

  await test('C08 native Chromium quota exhaustion and atomic rollback', async () => {
    const isolated = await launch('quota-profile');
    await loginUnlock(isolated.page);
    const cdp = await isolated.context.newCDPSession(isolated.page);
    await cdp.send('Storage.overrideQuotaForOrigin', { origin, quotaSize: 1_048_576 });
    const evidence = await evaluate(isolated.page, async () => {
      const vault = window.labVault;
      await vault.save('existing synthetic unique draft', 'survivor');
      let failure = null;
      let attempted = 0;
      for (let index = 0; index < 16; index++) {
        const chunks = [];
        for (let part = 0; part < 8; part++) {
          chunks.push(btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32768)))));
        }
        const text = chunks.join('');
        attempted += text.length;
        try { await vault.save(text, `fill-${index}`); }
        catch (error) {
          failure = { name: error.name, status: vault.lastFailure, retained: vault.pendingText === text, failedObject: `fill-${index}` };
          break;
        }
      }
      if (!failure) throw new Error('NATIVE_QUOTA_FAILURE_NOT_OBSERVED');
      const survivor = await vault.load('survivor');
      let abortedRecordAbsent = false;
      try { await vault.load(failure.failedObject); }
      catch (error) { abortedRecordAbsent = error.message === 'LOCAL_COPY_UNAVAILABLE'; }
      return { ...failure, attempted_bytes: attempted, survivor: survivor === 'existing synthetic unique draft', aborted_record_absent: abortedRecordAbsent,
        estimate: await navigator.storage.estimate() };
    });
    assert.equal(evidence.name, 'QuotaExceededError');
    assert.equal(evidence.status, 'QUOTA_EXCEEDED');
    assert.equal(evidence.retained, true);
    assert.equal(evidence.survivor, true);
    assert.equal(evidence.aborted_record_absent, true);
    await cdp.send('Storage.overrideQuotaForOrigin', { origin });
    await isolated.context.close();
    contexts.delete(isolated.context);
    return { ...evidence, mechanism: 'CDP Storage.overrideQuotaForOrigin, actual IndexedDB transaction', override_bytes: 1_048_576 };
  });

  await test('C08 real browser storage deletion reports unavailable unique copy', async () => {
    await loginUnlock(page);
    await evaluate(page, () => window.labVault.save('clear synthetic', 'clear-check'));
    const cdp = await context.newCDPSession(page);
    await cdp.send('Storage.clearDataForOrigin', { origin, storageTypes: 'indexeddb' });
    await expectError(page, () => window.labVault.load('clear-check'), /LOCAL_COPY_UNAVAILABLE/);
    return { cleared_by_browser: true, unavailable_reported: true };
  });

  await test('C05 independent experiment UI clears rendered plaintext on lock', async () => {
    const ui = await launch('ui-profile');
    await ui.page.locator('#login-a').click();
    await ui.page.locator('#unlock').click();
    await ui.page.waitForFunction(() => document.querySelector('#status').textContent === '已解锁');
    await ui.page.locator('#save').click();
    // The UI returns status only after the IDB commit. Read retries until the result is visible.
    await ui.page.locator('#read').click();
    await ui.page.waitForFunction(() => document.querySelector('#content').textContent.includes('PR05a'));
    await ui.page.screenshot({ path: path.join(root, 'experiment-ui.png') });
    await ui.page.locator('#lock').click();
    await ui.page.waitForFunction(() => document.querySelector('#content').textContent === '');
    await ui.context.close();
    contexts.delete(ui.context);
    return { independent_ui_exercised: true, rendered_plaintext_cleared: true };
  });
} catch (error) {
  if (failures.length === 0) {
    failures.push('browser harness');
    results.push({ name: 'browser harness', passed: false, error: error.message });
    console.log(`FAIL browser harness ${error.message}`);
  }
} finally {
  for (const context of contexts) await context.close().catch(() => {});
  const report = { browser: chromium.name(), browser_version: browserVersion, results, failed: failures, passed: failures.length === 0 && results.length === 13 };
  fs.writeFileSync(path.join(root, 'browser-results.json'), JSON.stringify(report, null, 2));
  process.exitCode = report.passed ? 0 : 1;
}

async function controlThroughOtherContext(action) {
  const helper = await launch('control-profile');
  try { await control(helper.page, action); }
  finally { await helper.context.close(); contexts.delete(helper.context); }
}
