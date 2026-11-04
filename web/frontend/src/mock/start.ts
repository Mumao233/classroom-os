import { delay, http, HttpResponse, passthrough } from 'msw';
import { setupWorker } from 'msw/browser';
import { coursePages, meta, semesterPage, unauthenticated } from './fixtures';

const headers = { 'Cache-Control': 'no-store' };
// Development-only in-memory session identity; never a new HTTP field or real credential.
let mockAccount: 'A' | 'B' | null = 'A';
export function setMockAccount(account: 'A' | 'B' | null) { mockAccount = account; }
function viteAsset(request: Request) {
  const url = new URL(request.url);
  return url.origin === location.origin && request.method === 'GET' &&
    (url.pathname.startsWith('/@') || url.pathname.startsWith('/src/') ||
     url.pathname.startsWith('/node_modules/') || url.pathname === '/favicon.ico');
}
const worker = setupWorker(
  http.get('/api/web/v1/meta', () => HttpResponse.json(meta, { headers })),
  http.get('/api/web/v1/courses', async () => {
    const requestAccount = mockAccount;
    if (!requestAccount) return HttpResponse.json(unauthenticated, { status: 401, headers });
    // Browser-test-only timing control; it never changes the HTTP contract.
    if (localStorage.getItem('mock-test-delay') === '1') await delay(1200);
    return HttpResponse.json(coursePages[requestAccount], { headers });
  }),
  http.get('/api/web/v1/semesters', () => mockAccount ? HttpResponse.json(semesterPage, { headers }) : HttpResponse.json(unauthenticated, { status: 401, headers })),
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
