import { pathToFileURL } from 'node:url';
const config = JSON.parse(await new Promise(resolve => {
  let input = ''; process.stdin.on('data', value => { input += value; }); process.stdin.on('end', () => resolve(input));
}));
const { chromium } = await import(pathToFileURL(config.module));
const browser = await chromium.launch({ headless: true, args: [`--ignore-certificate-errors-spki-list=${config.pin}`] });
const context = await browser.newContext();
await context.route('**/*', route => new URL(route.request().url()).origin === config.origin ? route.continue() : route.abort());
const steps = [];
try {
  const page = await context.newPage();
  await page.goto(config.origin);
  await page.waitForFunction(() => Boolean(window.pr05b));
  const pcm = await page.evaluate(async () => {
    const results = [];
    for (const rate of [44100, 48000]) {
      // OfflineAudioContext renders whole 128-sample quanta. Use an exact quantum boundary so no padding is part of the source.
      const count = 128 * Math.floor(rate * 0.237 / 128);
      const context = new OfflineAudioContext(1, count, rate);
      const buffer = context.createBuffer(2, count, rate);
      for (let index = 0; index < count; index++) {
        buffer.getChannelData(0)[index] = Math.sin(index * 0.013) * 0.6;
        buffer.getChannelData(1)[index] = Math.sin(index * 0.013) * 0.2;
      }
      const source = context.createBufferSource(); source.buffer = buffer;
      const blocks = [];
      const stream = await window.pr05b.attachPcmWorklet(context, source, async bytes => { blocks.push([...bytes]); });
      source.start();
      await context.startRendering();
      await stream.stop();
      const bytes = new Uint8Array(blocks.flat());
      const expected = [];
      for (let position = 0; position <= count - 1; position += rate / 16000) {
        const index = Math.ceil(position);
        const previous = Math.max(0, index - 1);
        const fraction = index === 0 ? 1 : position - previous;
        const left = (buffer.getChannelData(0)[previous] + buffer.getChannelData(1)[previous]) / 2;
        const right = (buffer.getChannelData(0)[index] + buffer.getChannelData(1)[index]) / 2;
        expected.push(left + (right - left) * fraction);
      }
      const expectedBytes = window.pr05b.pcm16(new Float32Array(expected));
      if (bytes.length !== expectedBytes.length) throw new Error(`PCM length ${rate} actual=${bytes.length} expected=${expectedBytes.length}`);
      const actualView = new DataView(bytes.buffer); const expectedView = new DataView(expectedBytes.buffer);
      let maxError = 0;
      for (let index = 0; index < bytes.length; index += 2) maxError = Math.max(maxError,
        Math.abs(actualView.getInt16(index, true) - expectedView.getInt16(index, true)));
      if (maxError > 1 || bytes.length % 3200 === 0) throw new Error(`PCM mismatch ${rate}/${maxError}`);
      results.push({ inputRate: rate, outputRate: 16000, inputChannels: 2, outputChannels: 1, bytes: bytes.length, maxPcm16Error: maxError });
    }
    return results;
  });
  steps.push('native_worklet_44100_48000_stereo_mono_pcm16_continuous_tail');
  const bounded = await page.evaluate(async () => {
    const context = new OfflineAudioContext(1, 48000 * 5, 48000);
    const source = context.createBufferSource(); source.buffer = context.createBuffer(1, 48000 * 5, 48000);
    let release; let calls = 0;
    const blocked = new Promise(resolve => { release = resolve; });
    const stream = await window.pr05b.attachPcmWorklet(context, source, async () => { calls++; await blocked; }, 64000);
    source.start(); await context.startRendering();
    await new Promise(resolve => setTimeout(resolve, 20));
    const status = stream.status(); release();
    let error;
    try { await stream.stop(); } catch (caught) { error = caught.code; }
    if (!status.failed || !status.disconnected || status.bufferedBytes > 64000 || calls > 1 || error !== 'CAPACITY_EXCEEDED')
      throw new Error('worklet backpressure failed ' + JSON.stringify({ status, calls, error }));
    return { status, calls, error };
  });
  steps.push('native_worklet_hung_append_bounded_and_stops_input');
  const result = await page.evaluate(async config => {
    const base = '/api/web/v1';
    const csrf = (await (await fetch(base + '/auth/csrf')).json()).csrf_token;
    const login = await (await fetch(base + '/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json',
      'X-CSRF-Token': csrf, 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ email: config.email,
      password: config.password, remember_me: false, device_name: 'PR05b synthetic native WSS' }) })).json();
    if (login.state !== 'authenticated') throw new Error('login failed');
    const identity = { csrf: login.csrf_token };
    let offline = false;
    const transport = new window.pr05b.CaptureClient(identity, (url, options) => {
      if (offline) return Promise.reject(new TypeError('synthetic network outage'));
      return fetch(url, options);
    });
    const lease = await transport.acquire(config.classroom, { device_id: login.device_id, microphone_consent_version: 'synthetic-v1',
      recovery_consent_version: 'synthetic-v1', asr_consent_version: 'synthetic-v1', confirm_takeover: false }, crypto.randomUUID());
    // Transport harness uses bounded memory only. The separate A+B native harness covers real encrypted IDB.
    const memory = new Map(); const states = new Map(); const order = [];
    const cache = {
      async putRecording(descriptor, bytes) { memory.set(descriptor.seq, bytes.slice()); order.push('persist'); },
      async readRecording(descriptor) { return memory.get(descriptor.seq) ?? null; },
      async acknowledgeRecording(receipt) { if (receipt.state !== 'durable') throw new Error('premature cleanup'); memory.delete(receipt.descriptor.seq); order.push('durable-cleanup'); },
      async saveCaptureState(room, epoch, bytes) { states.set(epoch, bytes.slice()); },
      async readCaptureState(room, epoch) { return states.get(epoch) ?? null; },
    };
    const runtime = new window.pr05b.CaptureRuntime(transport, cache, { semesterId: config.semester,
      courseId: config.course, classSessionId: config.classroom }, 64000);
    await runtime.prepare(lease); await runtime.start();
    await runtime.append(new Uint8Array(32000).map((_, index) => index % 251));
    await runtime.append(new Uint8Array(514).fill(7));
    await runtime.uploadPending();
    if (memory.size || runtime.snapshot().pending.length || runtime.snapshot().cleanup.length) throw new Error('durable cleanup incomplete');
    await runtime.pause(); const resumed = await runtime.resume();
    if (resumed.capture_epoch_id === lease.capture_epoch_id || resumed.fence_token <= lease.fence_token) throw new Error('resume reused epoch');
    await runtime.prepare(resumed); await runtime.start(); await runtime.append(new Uint8Array(514).fill(9));
    offline = true;
    try { await runtime.renew(); throw new Error('offline renew unexpectedly reached server'); }
    catch (error) { if (error.code !== 'RESULT_UNKNOWN') throw error; }
    const renewKey = runtime.snapshot().command.key;
    const legacy = JSON.parse(new TextDecoder().decode(states.get(resumed.capture_epoch_id)));
    const invalid = { ...legacy, unresolvedCommands: {} };
    states.set(resumed.capture_epoch_id, new TextEncoder().encode(JSON.stringify(invalid)));
    const freshRuntime = () => new window.pr05b.CaptureRuntime(transport, cache, { semesterId: config.semester,
      courseId: config.course, classSessionId: config.classroom }, 64000);
    try { await freshRuntime().restore(config.classroom, resumed.capture_epoch_id); throw new Error('invalid v1 accepted'); }
    catch (error) { if (error.code !== 'INVALID_CAPTURE_STATE') throw error; }
    delete legacy.unresolvedCommands;
    states.set(resumed.capture_epoch_id, new TextEncoder().encode(JSON.stringify(legacy)));
    const legacyRestored = freshRuntime(); await legacyRestored.restore(config.classroom, resumed.capture_epoch_id);
    try { await legacyRestored.stop(); throw new Error('offline stop unexpectedly reached server'); }
    catch (error) { if (error.code !== 'RESULT_UNKNOWN') throw error; }
    if (legacyRestored.snapshot().phase !== 'stopped' || legacyRestored.snapshot().pending.length !== 1
      || legacyRestored.snapshot().unresolvedCommands[0]?.key !== renewKey) throw new Error('offline stop lost local tail or unknown renew key');
    await fetch('/synthetic/expire-lease', { method: 'POST' }); offline = false;
    const restored = new window.pr05b.CaptureRuntime(transport, cache, { semesterId: config.semester,
      courseId: config.course, classSessionId: config.classroom }, 64000);
    await restored.restore(config.classroom, resumed.capture_epoch_id);
    await restored.authorizeRecovery(); await restored.uploadPending();
    if (restored.snapshot().pending.length || memory.size) throw new Error('expired-fence recovery did not drain');
    window.captureOriginal = { csrf: identity.csrf, device: login.device_id };
    return { fullBytes: 32000, tailBytes: 514, durableChunks: 3, resumedNewEpoch: true,
      offlineStopExpiredLeaseRestoredAndRecovered: true, order };
  }, config);
  steps.push('real_https_cookie_capture_acquire', 'real_wss_heartbeat_full_and_tail_durable_ack', 'durable_ack_clears_memory_only_after_commit',
    'pause_resume_new_epoch', 'legacy_v1_missing_array_normalized_malformed_rejected', 'unknown_renew_key_preserved_through_local_stop',
    'offline_stop_expired_fence_restored_runtime_real_recovery');
  await page.evaluate(async config => {
    const client = new window.pr05b.CaptureClient({ csrf: window.captureOriginal.csrf });
    const body = { device_id: window.captureOriginal.device, microphone_consent_version: 'synthetic-v1',
      recovery_consent_version: 'synthetic-v1', asr_consent_version: 'synthetic-v1', confirm_takeover: false };
    let target;
    try { await client.acquire(config.classroom, body, crypto.randomUUID()); }
    catch (error) { if (error.code !== 'LEASE_CONFLICT' || !error.conflictId) throw error; target = error.conflictId; }
    if (!target) throw new Error('lease conflict target missing');
    window.captureOriginal.lease = await client.acquire(config.classroom, { ...body, confirm_takeover: true, expected_lease_id: target }, crypto.randomUUID());
  }, config);
  const otherContext = await browser.newContext();
  await otherContext.route('**/*', route => new URL(route.request().url()).origin === config.origin ? route.continue() : route.abort());
  try {
    const otherPage = await otherContext.newPage(); await otherPage.goto(config.origin);
    await otherPage.waitForFunction(() => Boolean(window.pr05b));
    await otherPage.evaluate(async config => {
      const base = '/api/web/v1'; const csrf = (await (await fetch(base + '/auth/csrf')).json()).csrf_token;
      const login = await (await fetch(base + '/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json',
        'X-CSRF-Token': csrf, 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ email: config.email,
        password: config.password, remember_me: false, device_name: 'Second independent capture device' }) })).json();
      const client = new window.pr05b.CaptureClient({ csrf: login.csrf_token });
      const body = { device_id: login.device_id, microphone_consent_version: 'synthetic-v1', recovery_consent_version: 'synthetic-v1',
        asr_consent_version: 'synthetic-v1', confirm_takeover: false };
      let target;
      try { await client.acquire(config.classroom, body, crypto.randomUUID()); }
      catch (error) { if (error.code !== 'LEASE_CONFLICT' || !error.conflictId) throw error; target = error.conflictId; }
      if (!target) throw new Error('new device could not discover CAS target');
      const taken = await client.acquire(config.classroom, { ...body, confirm_takeover: true, expected_lease_id: target }, crypto.randomUUID());
      try { await client.acquire(config.classroom, { ...body, confirm_takeover: true, expected_lease_id: target }, crypto.randomUUID()); throw new Error('stale CAS succeeded'); }
      catch (error) { if (error.code !== 'LEASE_CONFLICT' || error.conflictId !== taken.id) throw error; }
    }, config);
    await page.evaluate(async config => {
      const client = new window.pr05b.CaptureClient({ csrf: window.captureOriginal.csrf });
      const old = window.captureOriginal.lease;
      try {
        await client.command(config.classroom, 'renew', { capture_epoch_id: old.capture_epoch_id, fence_token: old.fence_token,
          last_local_seq: 0, last_sample: 0, client_monotonic_ms: 1 }, crypto.randomUUID());
        throw new Error('old device renewed after takeover');
      } catch (error) { if (error.code !== 'STALE_FENCE') throw error; }
    }, config);
  } finally { await otherContext.close(); }
  steps.push('two_independent_native_logins_discover_conflict_id_explicit_takeover_and_old_fence_rejected');
  const cookie = (await context.cookies()).find(item => item.name === '__Host-classroom_session');
  if (!cookie?.secure || !cookie.httpOnly) throw new Error('secure cookie missing');
  const eventsCloseCode = await page.evaluate(async () => {
    const base = '/api/web/v1';
    const session = await (await fetch(base + '/auth/session')).json();
    const headers = { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token, 'Idempotency-Key': crypto.randomUUID() };
    const issued = await (await fetch(base + '/ws-tickets', { method: 'POST', headers,
      body: JSON.stringify({ channel: 'events', class_session_id: null, capture_epoch_id: null, fence_token: null }) })).json();
    const socket = new WebSocket(location.origin.replace('https:', 'wss:') + base + '/ws/events', 'classroom.v1');
    const closed = new Promise(resolve => socket.addEventListener('close', event => resolve(event.code), { once: true }));
    await new Promise((resolve, reject) => {
      socket.addEventListener('error', () => reject(new Error('events WSS failed')), { once: true });
      socket.addEventListener('open', () => {
        socket.send(JSON.stringify({ type: 'authenticate', ticket: issued.ticket }));
        setTimeout(() => socket.readyState === WebSocket.OPEN ? resolve() : reject(new Error('events denied')), 500);
      }, { once: true });
    });
    await fetch(base + '/auth/logout', { method: 'POST', headers: { ...headers, 'Idempotency-Key': crypto.randomUUID() }, body: '{}' });
    return Promise.race([closed, new Promise((_, reject) => setTimeout(() => reject(new Error('events revocation timeout')), 3000))]);
  });
  if (eventsCloseCode !== 1008) throw new Error('events WSS authorization regression');
  steps.push('real_events_wss_ticket_and_logout_revocation_regression');
  console.log(JSON.stringify({ passed: true, steps, pcm, bounded, result, tlsMode: 'pinned_key', realMicrophone: false,
    paidApi: false, cacheHarness: 'memory-only; native encrypted IDB covered separately', externalRequestsAllowed: false }));
} finally { await browser.close(); }
