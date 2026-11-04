import type { components } from '../generated/openapi';

type Scope = { epoch: number; isCurrent: (epoch: number) => boolean };
type ErrorBody = components['schemas']['Error'];

/** Errors from transport, HTTP responses and invalidated account scopes share one boundary. */
export class ApiClientError extends Error {
  constructor(readonly kind: 'stale' | 'network' | 'http' | 'invalid_response', readonly status?: number, readonly detail?: ErrorBody) {
    super(`API request failed: ${kind}`);
    this.name = 'ApiClientError';
  }
}

/** A request belongs to the account epoch captured by its caller, including during body parsing. */
export function createApiClient(scope: Scope, transport: typeof fetch = fetch) {
  const assertCurrent = () => { if (!scope.isCurrent(scope.epoch)) throw new ApiClientError('stale'); };
  async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
    assertCurrent();
    let response: Response;
    try {
      response = await transport(path, { method: 'GET', credentials: 'same-origin', cache: 'no-store', signal });
    } catch (error) {
      assertCurrent();
      if (signal?.aborted) throw error;
      throw new ApiClientError('network');
    }
    assertCurrent();
    let body: unknown;
    try { body = await response.json(); } catch {
      assertCurrent();
      throw new ApiClientError(response.ok ? 'invalid_response' : 'http', response.status);
    }
    assertCurrent();
    if (!response.ok) throw new ApiClientError('http', response.status, body as ErrorBody);
    return body as T;
  }
  return { getCourses: (signal?: AbortSignal) => getJson<components['schemas']['CoursePage']>('/api/web/v1/courses', signal) };
}
