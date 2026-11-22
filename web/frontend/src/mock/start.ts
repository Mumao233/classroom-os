import { delay, http, HttpResponse, passthrough } from 'msw';
import { setupWorker } from 'msw/browser';
import { meta, unauthenticated } from './fixtures';
import { createMockBackend, type MockDataset } from './backend';

const headers = { 'Cache-Control': 'no-store' };
// Development-only in-memory session identity; never a new HTTP field or real credential.
let mockAccount: 'A' | 'B' | null = 'A';
let mockOffline = false;
const backend = createMockBackend();
export function setMockAccount(account: 'A' | 'B' | null) { mockAccount = account; }
/** Simulated loss of connectivity: every business request fails like a network error. */
export function setMockOffline(offline: boolean) { mockOffline = offline; }
export function isMockOffline() { return mockOffline; }
/** MOCK-01 uses an empty account; other scenarios use the seeded synthetic records. */
export function setMockDataset(account: 'A' | 'B', dataset: MockDataset) { backend.reset(account, dataset); }
/** Dev panel helper: another device updates a record so CAS conflicts can be reproduced. */
export function bumpMockRecord(kind: 'semester' | 'course' | 'class_session' | 'preferences', id: string, change: Record<string, unknown> = {}) {
  if (mockAccount) backend.bumpElsewhere(mockAccount, kind, id, change);
}
/** PR-10a development panel: a worker reports a later task state (no model is called). */
export function advanceMockTask(taskId: string, outcome: 'running' | 'succeeded' | 'failed' | 'unknown') { return mockAccount ? backend.advanceTask(mockAccount, taskId, outcome) : null; }
/** PR-10a development panel: the stopped class finishes (or fails) its transcription backlog. */
export function finishMockAsr(classId: string, state: 'succeeded' | 'failed' | 'unknown') { return mockAccount ? backend.finishAsr(mockAccount, classId, state) : false; }
/** Final transcript events observed by the page runtime are also stored by the Mock server. */
export function persistMockFinal(input: Parameters<typeof backend.persistFinal>[1]) { if (mockAccount) backend.persistFinal(mockAccount, input); }
/** Registers the PR-09a demo capture (lease generated in the page) with the Mock server. */
export function ensureMockDemoClassroom(input: Parameters<typeof backend.ensureDemoClassroom>[1]) { return mockAccount ? backend.ensureDemoClassroom(mockAccount, input) : false; }
/** Durable chunk receipts seen by the page runtime, so the Mock stop can seal a matching manifest. */
export function setMockDurable(classId: string, captureEpochId: string, seqs: number[]) { if (mockAccount) backend.setDurable(mockAccount, classId, captureEpochId, seqs); }

export function releaseMockLease(classId: string) { return mockAccount ? backend.releaseLease(mockAccount, classId) : false; }

/** Account teardown: the outgoing account's leases are retired explicitly (the transport may already point elsewhere). */
export function releaseMockAccountLeases(account: 'A' | 'B') { return backend.releaseAccountLeases(account); }

export function projectMockTask(input: Parameters<typeof backend.projectTask>[1]) { return mockAccount ? backend.projectTask(mockAccount, input) : false; }

export function revokeMockAudio(classId: string, captureEpochId: string) { return mockAccount ? backend.revokeAudio(mockAccount, classId, captureEpochId) : false; }

export function revokeMockClassroom(classId: string) { return mockAccount ? backend.revokeClassroom(mockAccount, classId) : false; }
// Development/test hook only (this module never ships in production builds).
export function setMockManifestDev(value: { pageSize?: number | null; failContinuations?: boolean }) { return mockAccount ? backend.setManifestDev(mockAccount, value) : null; }

/** PR-10b development panel: retranscription refusals (original audio expired/deleted, session or month quota). */
export function setMockRetranscribe(value: { classId?: string; audio?: 'ok' | 'expired' | 'deleted'; quota?: 'ok' | 'session' | 'month'; approve?: boolean }) { return mockAccount ? backend.setRetranscribeDev(mockAccount, value) : null; }

(globalThis as { __classroomMock?: unknown }).__classroomMock = { bump: bumpMockRecord, advanceTask: advanceMockTask, finishAsr: finishMockAsr, manifestDev: setMockManifestDev, retranscribe: setMockRetranscribe };
function viteAsset(request: Request) {
  const url = new URL(request.url);
  return url.origin === location.origin && request.method === 'GET' &&
    (url.pathname.startsWith('/@') || url.pathname.startsWith('/src/') ||
     url.pathname.startsWith('/node_modules/') || url.pathname === '/favicon.ico');
}
const worker = setupWorker(
  http.get('/api/web/v1/meta', () => HttpResponse.json(meta, { headers })),
  http.all('/api/web/v1/*', async ({ request }) => {
    if (mockOffline) return HttpResponse.error();
    const requestAccount = mockAccount;
    if (!requestAccount) return HttpResponse.json(unauthenticated, { status: 401, headers });
    const url = new URL(request.url);
    const path = url.pathname.slice('/api/web/v1'.length);
    // Browser-test-only timing control; it never changes the HTTP contract.
    if (path === '/courses' && localStorage.getItem('mock-test-delay') === '1') await delay(1200);
    // Same rule as the media service: raw upload bytes must be sent as application/octet-stream.
    if (request.method === 'PUT' && request.headers.get('Content-Type') !== 'application/octet-stream') {
      return HttpResponse.json({ error: { code: 'VALIDATION_FAILED', message: '上传内容类型必须为 application/octet-stream', request_id: crypto.randomUUID(), retryable: false, retry_after_seconds: null, current_revision: null, conflict_id: null, task_id: null } }, { status: 422, headers });
    }
    let body: unknown = undefined;
    if (request.method === 'PUT') {
      // Raw upload bytes: the Mock receives their size, SHA-256 and leading bytes, never a JSON parse.
      const bytes = new Uint8Array(await request.arrayBuffer());
      const toHex = (data: Uint8Array) => [...data].map(value => value.toString(16).padStart(2, '0')).join('');
      body = { byte_length: bytes.byteLength, sha256: toHex(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))), signature: toHex(bytes.slice(0, 16)) };
    } else if (request.method !== 'GET') {
      try { body = await request.json(); } catch { body = undefined; }
    }
    // Computed for the account that sent the request; a switch meanwhile is rejected by the client epoch.
    const reply = backend.handle(requestAccount, request.method, path, url.searchParams, body, request.headers.get('Idempotency-Key'));
    if (!reply) return HttpResponse.error();
    return HttpResponse.json(reply.body as Record<string, unknown>, { status: reply.status, headers });
  }),
  // Explicit catch-all is essential: throwing in onUnhandledRequest alone can
  // fall back to the network in the browser worker.
  http.all('*', ({ request }) => viteAsset(request) ? passthrough() : HttpResponse.error()),
);

/** Must be awaited before app render. Unmatched requests fail, never bypass. */
export async function startMock(): Promise<void> {
  await worker.start({
    serviceWorker: { url: '/mockServiceWorker.js' },
    onUnhandledRequest(request, print) {
      // Only the Vite development server's own public assets may pass through.
      if (viteAsset(request)) return;
      print.error();
    },
  });
}
