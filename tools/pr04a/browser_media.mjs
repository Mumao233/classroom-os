import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const config = JSON.parse(readFileSync(0, 'utf8'));
const { chromium } = await import(pathToFileURL(config.playwrightModule).href);
const browser = await chromium.launch({
  args: [`--ignore-certificate-errors-spki-list=${config.spki}`],
});
try {
  const context = await browser.newContext();
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    return url.origin === config.origin ? route.continue() : route.abort();
  });
  const page = await context.newPage();
  await page.goto(`${config.origin}/api/web/v1/auth/csrf`);
  const steps = await page.evaluate(async ({ email, password, classroomId }) => {
    const prefix = '/api/web/v1';
    function expect(value, message) {
      if (!value) throw new Error(message);
    }
    const preauth = await (await fetch(`${prefix}/auth/csrf`)).json();
    async function command(path, body, csrf) {
      const response = await fetch(prefix + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify(body),
      });
      expect(response.ok, `command failed with ${response.status}`);
      return response.json();
    }
    const session = await command('/auth/login', { email, password, remember_me: false, device_name: 'Synthetic media browser' }, preauth.csrf_token);
    expect(session.state === 'authenticated', 'authentication required');
    const steps = ['HTTPS_login'];
    const bytes = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10, ...new TextEncoder().encode('SYNTHETIC-BROWSER-MEDIA')]);
    const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), value => value.toString(16).padStart(2, '0')).join('');
    const upload = await command('/media/uploads', { purpose: 'slide', class_session_id: classroomId, mime_type: 'image/png', byte_length: bytes.length, sha256, filename: 'synthetic.png' }, session.csrf_token);
    const put = await fetch(upload.upload_path, { method: 'PUT', headers: { 'Content-Type': 'application/octet-stream', 'X-CSRF-Token': session.csrf_token, 'Idempotency-Key': crypto.randomUUID() }, body: bytes });
    expect(put.ok, `upload failed with ${put.status}`);
    const asset = await command(`/media/uploads/${upload.id}/complete`, { byte_length: bytes.length, sha256 }, session.csrf_token);
    expect(asset.state === 'available', 'asset unavailable');
    steps.push('media_upload_verify');
    const ticket = await command(`/media/${asset.id}/access`, {}, session.csrf_token);
    const range = await fetch(ticket.download_path, { headers: { Range: 'bytes=0-7' } });
    const content = new Uint8Array(await range.arrayBuffer());
    expect(range.status === 206 && content.length === 8 && content.every((value, index) => value === bytes[index]), 'range mismatch');
    steps.push('authorized_range');
    await command('/auth/logout', {}, session.csrf_token);
    expect((await fetch(ticket.download_path)).status === 401, 'revoked ticket accepted');
    steps.push('logout_revokes_ticket');
    expect(localStorage.length === 0 && sessionStorage.length === 0 && (await indexedDB.databases()).length === 0, 'unexpected persistent browser data');
    steps.push('browser_storage_empty');
    return steps;
  }, { email: config.email, password: config.password, classroomId: config.classroomId });
  process.stdout.write(JSON.stringify({ passed: true, steps }));
} finally {
  await browser.close();
}
