import { expect, it, vi } from 'vitest';
import { handleSearchBackend, recordMockNotification, resolveMockNotification, revokeSearchFixture,
  searchFixture, seedSearchBackend } from '../src/mock/search-backend';
import type { SearchPageData } from '../src/domain/search';
import type { NotificationPage } from '../src/domain/notifications';

const { a, b } = searchFixture;

it('isolates accounts and cursor scope, then excludes revoked review snippets', () => {
  const store = seedSearchBackend();
  function search(ownerId: string, values: Record<string, string>) {
    return handleSearchBackend(store, { ownerId, method: 'GET', path: '/search', query: new URLSearchParams(values) })!;
  }
  expect((search(a.owner, { q: '乙账号' }).body as SearchPageData).items).toEqual([]);
  expect(search(b.owner, { q: '合成', course_id: a.course }).status).toBe(404);
  expect(search(b.owner, { q: '合成', class_session_id: a.classroom }).status).toBe(404);
  const first = search(a.owner, { q: '合成', limit: '1' });
  const cursor = (first.body as SearchPageData).next_cursor!;
  expect(cursor).toBeTruthy();
  expect(search(b.owner, { q: '合成', limit: '1', cursor }).status).toBe(422);
  expect(search(a.owner, { q: '其他', limit: '1', cursor }).status).toBe(422);
  expect(search(a.owner, { q: '甲账号' }).body).toMatchObject({ items: [{ object_id: a.document }] });
  revokeSearchFixture(store, a.owner, a.document);
  expect(search(a.owner, { q: '甲账号' }).body).toEqual({ items: [], next_cursor: null });
});

it('deduplicates events and keeps a read fault until the resolution event', () => {
  const store = seedSearchBackend();
  const values = { kind: 'conflict', severity: 'warning', object_id: a.document, message_code: 'SYNC_CONFLICT' } as const;
  const notice = recordMockNotification(store, a.owner, 'same-event', values)!;
  expect(recordMockNotification(store, a.owner, 'same-event', values)?.id).toBe(notice.id);
  const list = (ownerId: string) => handleSearchBackend(store, { ownerId, method: 'GET', path: '/notifications' })!.body as NotificationPage;
  const mark = (ownerId: string, body: unknown) => handleSearchBackend(store, { ownerId, method: 'POST', key: crypto.randomUUID(), csrfVerified: true,
    path: `/notifications/${notice.id}/read`, body })!;
  expect(list(b.owner).items).toEqual([]);
  expect(mark(b.owner, { read: true }).status).toBe(404);
  for (const body of [undefined, { read: false }, { read: true, resolved: true }]) {
    expect(mark(a.owner, body).body).toMatchObject({ error: { code: 'VALIDATION_FAILED' } });
    expect(list(a.owner).items[0].read_at).toBeNull();
  }
  expect(mark(a.owner, { read: true }).body).toMatchObject({ read_at: expect.any(String), resolved_at: null });
  expect(list(a.owner).items).toHaveLength(1);
  resolveMockNotification(store, a.owner, 'same-event');
  expect(list(a.owner).items[0].resolved_at).toBeTruthy();
});

it('revokes notifications for a classroom and its tasks when only the parent course is revoked', () => {
  const store = seedSearchBackend();
  const first = recordMockNotification(store, a.owner, 'classroom', {
    kind: 'capture_failure', severity: 'warning', object_id: a.classroom, message_code: 'CAPTURE_LEASE_LOST',
  })!;
  const second = recordMockNotification(store, a.owner, 'task', {
    kind: 'task_completed', severity: 'info', object_id: crypto.randomUUID(), message_code: 'TASK_COMPLETED',
  }, { class_session_id: a.classroom })!;
  const list = () => handleSearchBackend(store, { ownerId: a.owner, method: 'GET', path: '/notifications' })!.body as NotificationPage;
  expect(list().items).toHaveLength(2);
  revokeSearchFixture(store, a.owner, a.course);
  expect(list().items).toEqual([]);
  for (const item of [first, second]) {
    const reply = handleSearchBackend(store, { ownerId: a.owner, method: 'POST', path: `/notifications/${item.id}/read`,
      csrfVerified: true, key: crypto.randomUUID(), body: { read: true } })!;
    expect(reply).toMatchObject({ status: 404, body: { error: { code: 'NOT_FOUND' } } });
    expect(item.read_at).toBeNull();
  }
});

it('rejects a revoked owner at admission, including existing feedback receipts', () => {
  const store = seedSearchBackend();
  const notice = recordMockNotification(store, a.owner, 'before-revocation', {
    kind: 'task_completed', severity: 'info', object_id: a.document, message_code: 'TASK_COMPLETED',
  })!;
  const feedback = {
    ownerId: a.owner, method: 'POST', path: '/feedback', csrfVerified: true, key: crypto.randomUUID(),
    body: { category: 'bug', build_id: 'test', request_id: null, description: 'synthetic',
      confirmed_excerpt: null, confirm_send_excerpt: false },
  };
  expect(handleSearchBackend(store, feedback)?.status).toBe(202);
  revokeSearchFixture(store, a.owner, a.owner);
  for (const request of [
    { method: 'GET', path: '/search', query: new URLSearchParams({ q: '合成' }) },
    { method: 'GET', path: '/notifications' },
    { method: 'POST', path: `/notifications/${notice.id}/read`, csrfVerified: true, key: crypto.randomUUID(), body: { read: true } },
    feedback, { ...feedback, key: crypto.randomUUID() },
  ]) {
    expect(handleSearchBackend(store, { ...request, ownerId: a.owner }))
      .toMatchObject({ status: 401, body: { error: { code: 'UNAUTHENTICATED' } } });
  }
  expect(notice.read_at).toBeNull();
  expect(store.accounts.get(a.owner)?.feedback.size).toBe(1);
});

it('rejects unconfirmed excerpts and scopes the same feedback key by owner', () => {
  const store = seedSearchBackend();
  const key = crypto.randomUUID();
  const send = (ownerId: string, confirm: boolean, csrfVerified = true) => handleSearchBackend(store, {
    ownerId, method: 'POST', key, csrfVerified, path: '/feedback', body: {
      category: 'bug', build_id: 'test', request_id: a.document, description: 'description',
      confirmed_excerpt: 'private excerpt', confirm_send_excerpt: confirm,
    },
  })!;
  expect(send(a.owner, true, false).status).toBe(403);
  expect(send(a.owner, false).body).toMatchObject({ error: { code: 'CONSENT_REQUIRED' } });
  const accepted = send(a.owner, true);
  expect(accepted.status).toBe(202);
  expect(send(a.owner, true)).toEqual(accepted);
  expect(send(b.owner, true).body).not.toEqual(accepted.body);
  expect(store.accounts.get(b.owner)?.feedback.has((accepted.body as { request_id: string }).request_id)).toBe(false);
  expect(accepted.body).not.toHaveProperty('confirmed_excerpt');
});

it.each(['description', 'build_id', 'confirmed_excerpt'] as const)(
  'rejects NUL in feedback %s without recording a report, as the backend does', field => {
    const store = seedSearchBackend();
    const body = { category: 'bug', build_id: 'test', request_id: null, description: 'description',
      confirmed_excerpt: 'excerpt', confirm_send_excerpt: true };
    const reply = handleSearchBackend(store, { ownerId: a.owner, method: 'POST', key: crypto.randomUUID(),
      csrfVerified: true, path: '/feedback', body: { ...body, [field]: `before\u0000after` } })!;
    expect(reply.status).toBe(422);
    expect(reply.body).toMatchObject({ error: { code: 'VALIDATION_FAILED' } });
    expect(store.accounts.get(a.owner)?.feedback.size).toBe(0);
  },
);

it('rate limits distinct successful feedback submissions per account within the rolling window', () => {
  const store = seedSearchBackend();
  const clock = vi.spyOn(Date, 'now').mockReturnValue(1_000_000);
  function send(ownerId: string, key: string) {
    return handleSearchBackend(store, { ownerId, key, method: 'POST', path: '/feedback', csrfVerified: true, body: {
      category: 'bug', build_id: 'test', request_id: null, description: 'synthetic',
      confirmed_excerpt: null, confirm_send_excerpt: false,
    } })!;
  }
  try {
    const firstKey = crypto.randomUUID();
    const first = send(a.owner, firstKey);
    expect(first.status).toBe(202);
    for (let i = 1; i < 10; i++) expect(send(a.owner, crypto.randomUUID()).status).toBe(202);
    const blockedKey = crypto.randomUUID();
    expect(send(a.owner, blockedKey)).toMatchObject({ status: 429, body: { error: { code: 'RATE_LIMITED' } } });
    expect(send(a.owner, firstKey)).toEqual(first);
    expect(send(b.owner, blockedKey).status).toBe(202);
    clock.mockReturnValue(1_599_999);
    expect(send(a.owner, blockedKey).status).toBe(429);
    clock.mockReturnValue(1_600_000);
    expect(send(a.owner, blockedKey).status).toBe(202);
  } finally {
    clock.mockRestore();
  }
});
