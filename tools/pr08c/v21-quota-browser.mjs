import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { realpathSync } from 'node:fs';
import { createServer } from 'node:http';
import { readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const output = process.env.PR08A_OUTPUT;
const modules = process.env.PR08A_FRONTEND_MODULES;
const require = createRequire(realpathSync(join(modules, 'vite', 'package.json')));
const { build } = require('esbuild');
const { chromium } = await import(pathToFileURL(process.env.PR08A_PLAYWRIGHT_MODULE).href);

const bundle = await build({
  stdin: {
    contents: `export { TrustedCache } from './src/cache/trusted-cache';
      export { CaptureRuntime } from './src/capture/runtime';
      export { attachPcmWorklet } from './src/capture/worklet';`,
    resolveDir: join(root, 'web/frontend'), loader: 'ts',
  },
  bundle: true, write: false, format: 'esm', platform: 'browser', target: 'es2022',
  nodePaths: [modules],
  plugins: [{
    name: 'serve-unchanged-pcm-worklet',
    setup(builder) {
      builder.onResolve({filter: /pcm-worklet\.js\?url$/}, () => ({path: 'pcm', namespace: 'worklet'}));
      builder.onLoad({filter: /.*/, namespace: 'worklet'}, () => ({contents: 'export default "/pcm-worklet.js";'}));
    },
  }],
});
const worklet = await readFile(join(root, 'static/pcm-worklet.js'));
const server = createServer((request, response) => {
  response.setHeader('Cache-Control', 'no-store');
  if (request.url === '/bundle.js' || request.url === '/pcm-worklet.js') {
    response.setHeader('Content-Type', 'text/javascript');
    response.end(request.url === '/bundle.js' ? bundle.outputFiles[0].contents : worklet);
  } else {
    response.setHeader('Content-Type', 'text/html');
    response.end('<!doctype html><title>PR-08c quota probe</title>');
  }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = 'http://127.0.0.1:' + server.address().port;
let context;
try {
  context = await chromium.launchPersistentContext(process.env.PR08C_BROWSER_PROFILE, {headless: true});
  const page = context.pages()[0];
  const cdp = await context.newCDPSession(page);
  await page.goto(origin);
  await cdp.send('Storage.overrideQuotaForOrigin', {origin, quotaSize: 2_000_000});
  const before = await page.evaluate(async () => {
    const { TrustedCache, CaptureRuntime, attachPcmWorklet } = await import('/bundle.js');
    const identity = {ownerId: 'synthetic-owner', deviceId: 'synthetic-device', sessionId: 'synthetic-session'};
    const ancestry = {semesterId: 'synthetic-semester', courseId: 'synthetic-course', classSessionId: 'synthetic-class'};
    const options = {enabled: true, recordingEnabled: true, databaseName: 'pr08c-quota-probe',
      limits: {draftBytes: 20_000_000, draftOperations: 2000, recordingBytes: 1_000_000_000}};
    const expiry = new Date(Date.now() + 120_000).toISOString();
    const grant = {key_id: 'synthetic-key', key_epoch: 1, scope: 'recording_recovery', expires_at: expiry,
      key_material_b64: btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32)))),
      cache_policy_revision: 'synthetic-only'};
    const lease = {class_session_id: ancestry.classSessionId, capture_epoch_id: 'synthetic-epoch',
      fence_token: 1, expires_at: expiry};
    const cache = new TrustedCache(options);
    cache.bind(identity);
    await cache.unlock('recording_recovery', ancestry.classSessionId, async () => structuredClone(grant));
    const runtime = new CaptureRuntime({}, cache, ancestry, 1_000_000_000);
    await runtime.prepare(lease);
    await runtime.start();
    const prior = new Uint8Array(32_000); prior.fill(7);
    await runtime.append(prior);
    const descriptor = runtime.snapshot().pending[0].descriptor;
    window.probe = {attachPcmWorklet, cache, runtime, identity, options, grant, ancestry, descriptor};
    return (await navigator.storage.estimate()).usage;
  });
  assert.ok(Number.isFinite(before));
  const quota = before + 96_000;
  await cdp.send('Storage.overrideQuotaForOrigin', {origin, quotaSize: quota});
  const seed = await page.evaluate(async () => {
    const {runtime, cache, identity, options, grant, ancestry, descriptor, attachPcmWorklet} = window.probe;
    const audio = new AudioContext();
    const source = audio.createConstantSource();
    const adapter = await attachPcmWorklet(audio, source, bytes => runtime.append(bytes));
    source.start();
    await audio.resume();
    const deadline = performance.now() + 20_000;
    while (!adapter.status().failed && performance.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    const status = adapter.status();
    let error;
    try { await adapter.stop(); } catch (failure) { error = failure.code; }
    source.stop();
    await audio.close();
    cache.close();
    return {status, error, identity, options, grant, ancestry, descriptor};
  });
  assert.equal(seed.error, 'QUOTA_EXCEEDED');
  assert.equal(seed.status.disconnected, true);
  await page.reload();
  const restored = await page.evaluate(async ({identity, options, grant, ancestry, descriptor}) => {
    const {TrustedCache} = await import('/bundle.js');
    const cache = new TrustedCache(options);
    cache.bind(identity);
    await cache.unlock('recording_recovery', ancestry.classSessionId, async () => structuredClone(grant));
    const bytes = await cache.readRecording(descriptor);
    cache.close();
    return bytes?.length === 32_000 && bytes.every(byte => byte === 7);
  }, seed);
  assert.equal(restored, true);
  const evidence = {passed: true, method: 'native Chromium origin quota override and IndexedDB',
    prior_bytes: 32_000, origin_quota_bytes: quota, initial_usage_bytes: before,
    error: seed.error, disconnected: seed.status.disconnected, recovered_after_reload: restored};
  await writeFile(join(output, 'pr08c-quota.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify(evidence));
} finally {
  await context?.close();
  server.close();
}
