import { describe, expect, it, vi } from 'vitest';
import { ApiClientError, createApiClient } from '../src/api/client';

describe('account-scoped HTTP client', () => {
  it('uses one same-origin request entry point and returns the typed course payload', async () => {
    const payload = { items: [], next_cursor: null };
    const transport = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }));
    const client = createApiClient({ epoch: 3, isCurrent: epoch => epoch === 3 }, transport);
    expect(await client.getCourses()).toEqual(payload);
    expect(transport).toHaveBeenCalledWith('/api/web/v1/courses', expect.objectContaining({ method: 'GET', credentials: 'same-origin', cache: 'no-store' }));
  });

  it('rejects a late response even if transport cannot be cancelled', async () => {
    let current = 1;
    let resolve!: (response: Response) => void;
    const transport = vi.fn(() => new Promise<Response>(done => { resolve = done; }));
    const client = createApiClient({ epoch: 1, isCurrent: epoch => epoch === current }, transport);
    const pending = client.getCourses();
    current = 2;
    resolve(new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 }));
    await expect(pending).rejects.toMatchObject({ kind: 'stale' });
    await expect(client.getCourses()).rejects.toMatchObject({ kind: 'stale' });
    expect(transport).toHaveBeenCalledTimes(1);
  });

  it('maps HTTP and network errors without exposing server messages in the error message', async () => {
    const scope = { epoch: 1, isCurrent: () => true };
    const client = createApiClient(scope, async () => new Response(JSON.stringify({ code: 'UNAUTHENTICATED' }), { status: 401 }));
    await expect(client.getCourses()).rejects.toMatchObject({ kind: 'http', status: 401 });
    const offline = createApiClient(scope, async () => { throw new TypeError('network details'); });
    await expect(offline.getCourses()).rejects.toEqual(new ApiClientError('network'));
  });
});
