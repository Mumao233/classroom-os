// @vitest-environment jsdom
import { createElement } from 'react';
import { MemoryRouter } from 'react-router';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { ApiClientError, type ApiClient } from '../src/api/client';
import { authorizeSearchResult, createOperations } from '../src/features/operations';
import { createRevocationLedger } from '../src/features/revocation';
import { discardFeedbackContext, feedbackContextStale, forgetFeedbackContexts, readFeedbackContext, stashFeedbackContext } from '../src/features/feedback/context';
import { seedClassroom, taskSourcesUnavailable } from '../src/mock/classroom-backend';
import { CommandPalette } from '../src/features/search/CommandPalette';
import { SearchPage } from '../src/pages/search/SearchPage';
import { TranscriptTimeline } from '../src/pages/classroom/TranscriptTimeline';
import { createMockBackend } from '../src/mock/backend';
import { createSearchStore, ensureSearchAccount, handleSearchBackend, setSearchHits } from '../src/mock/search-backend';
import type { components } from '../src/generated/openapi';
import type { SearchResult } from '../src/domain/search';
import type { TimelineEntry } from '../src/domain/classroom';

type S = components['schemas'];
afterEach(cleanup);
const ENDED = 'c0000000-0000-4000-8000-000000000001';
const UNKNOWN_AI = 'c0000000-0000-4000-8000-000000000006';

function harness() {
  const backend = createMockBackend();
  let key = 0;
  const call = (method: string, path: string, query: Record<string, string> = {}, body?: unknown) =>
    backend.handle('A', method, path, new URLSearchParams(query), body,
      method === 'GET' ? null : `11c00000-0000-4000-8000-${String(++key).padStart(12, '0')}`)!;
  const page = <T>(reply: { body: unknown }) => (reply.body as { items: T[] }).items;
  return { backend, call, page };
}

describe('PR-11c Mock wiring over the workbench account data', () => {
  it('searches live classrooms and active transcript finals, and drops them once the classroom is revoked', () => {
    const { backend, call, page } = harness();
    const room = page<S['ClassSession']>(call('GET', '/class-sessions')).find(item => item.id === ENDED)!;
    const byTitle = page<SearchResult>(call('GET', '/search', { q: room.title }));
    expect(byTitle).toContainEqual(expect.objectContaining({ object_type: 'class_session', object_id: ENDED, locator: `classroom/${ENDED}` }));
    const versions = page<S['TranscriptVersion']>(call('GET', `/class-sessions/${ENDED}/transcript-versions`));
    const segments = versions.flatMap(version => page<S['TranscriptSegment']>(call('GET', `/transcript-versions/${version.id}/segments`)))
      .filter(segment => segment.status === 'final');
    expect(segments.length).toBeGreaterThan(0);
    const phrase = [...segments[0].text].slice(0, 6).join('');
    const transcript = page<SearchResult>(call('GET', '/search', { q: phrase })).filter(hit => hit.object_type === 'transcript_segment');
    expect(transcript).toContainEqual(expect.objectContaining({ class_session_id: ENDED,
      locator: expect.stringMatching(new RegExp(`^transcript/${ENDED}/[0-9a-f-]{36}/[0-9a-f-]{36}$`)) }));
    expect(backend.revokeClassroom('A', ENDED)).toBe(true);
    const after = page<SearchResult>(call('GET', '/search', { q: room.title })).concat(page<SearchResult>(call('GET', '/search', { q: phrase })));
    expect(after.some(hit => hit.class_session_id === ENDED || hit.object_id === ENDED)).toBe(false);
  });

  it('serves the seeded notifications, and one deduplicated task_completed notice hidden with its classroom', () => {
    const { backend, call, page } = harness();
    const seeded = page<S['Notification']>(call('GET', '/notifications'));
    expect(seeded.map(item => item.id)).toEqual(expect.arrayContaining(['90000000-0000-4000-8000-000000000001']));
    const task = page<S['Task']>(call('GET', '/tasks', { class_session_id: UNKNOWN_AI }))
      .find(item => !['succeeded', 'failed', 'cancelled'].includes(item.state))!;
    expect(backend.advanceTask('A', task.id, 'succeeded')).toMatchObject({ state: 'succeeded' });
    expect(backend.advanceTask('A', task.id, 'succeeded')).toBeNull();
    const created = backend.drainCreatedNotifications('A');
    expect(created).toHaveLength(1);
    expect(backend.drainCreatedNotifications('A')).toHaveLength(0);
    const listed = page<S['Notification']>(call('GET', '/notifications')).filter(item => item.object_id === task.id);
    expect(listed).toEqual([expect.objectContaining({ id: created[0].id, kind: 'task_completed', message_code: 'TASK_COMPLETED' })]);
    expect(call('POST', `/notifications/${created[0].id}/read`, {}, { read: true }).status).toBe(200);
    expect(call('POST', `/notifications/${created[0].id}/read`, {}, {}).status).toBe(422);
    expect(backend.revokeClassroom('A', UNKNOWN_AI)).toBe(true);
    expect(page<S['Notification']>(call('GET', '/notifications')).some(item => item.id === created[0].id)).toBe(false);
  });

  it('matches full fields (card faces, personal revisions) and excerpts around the match', () => {
    const { call, page } = harness();
    const card = page<SearchResult>(call('GET', '/search', { q: 'characteristic polynomial' }));
    expect(card).toContainEqual(expect.objectContaining({ object_type: 'card', snippet: 'characteristic polynomial' }));
    const doc = page<S['ReviewDocument']>(call('GET', '/review-documents')).find(item => item.adopted_revision_id !== null)!;
    const saved = call('POST', `/review-documents/${doc.id}/personal-revisions`, {}, { base_revision: doc.revision,
      base_content_revision_id: doc.adopted_revision_id, draft_id: '11c00000-0000-4000-8000-0000000000aa',
      body_markdown: `${'合成填充。'.repeat(150)}独有关键词鸢尾花`, personal_note: '' });
    expect(saved.status).toBe(201);
    const personal = page<SearchResult>(call('GET', '/search', { q: '独有关键词鸢尾花' }));
    expect(personal).toEqual([expect.objectContaining({ object_id: doc.id, revision_id: (saved.body as S['ReviewRevision']).id })]);
    expect(personal[0].snippet).toContain('独有关键词鸢尾花');
    expect([...personal[0].snippet].length).toBeLessThanOrEqual(300);
    // The adopted revision stays searchable next to the personal one.
    expect(page<SearchResult>(call('GET', '/search', { q: doc.title })).filter(hit => hit.object_id === doc.id).map(hit => hit.revision_id))
      .toEqual(expect.arrayContaining([doc.adopted_revision_id]));
  });

  it('refuses an exact revision read once its source is denied, like the revision list', () => {
    const { backend, call, page } = harness();
    const doc = page<S['ReviewDocument']>(call('GET', '/review-documents')).find(item => item.adopted_revision_id !== null)!;
    expect(call('GET', `/review-revisions/${doc.adopted_revision_id}`).status).toBe(200);
    backend.denyReviewRevisions('A', doc.id);
    expect(call('GET', `/review-revisions/${doc.adopted_revision_id}`).status).toBe(403);
  });

  it('records one capture-failure notice per lost lease, dated by the workbench clock', () => {
    const backend = createMockBackend({ now: () => '2026-10-28T08:00:00Z' });
    expect(backend.recordLeaseLost('A', ENDED, 'epoch-1')).toMatchObject({ kind: 'capture_failure', message_code: 'CAPTURE_LEASE_LOST',
      object_id: ENDED, created_at: '2026-10-28T08:00:00Z' });
    backend.recordLeaseLost('A', ENDED, 'epoch-1');
    expect(backend.drainCreatedNotifications('A')).toHaveLength(1);
    const listed = (backend.handle('A', 'GET', '/notifications', new URLSearchParams(), undefined, null)!.body as { items: S['Notification'][] }).items;
    expect(listed.filter(item => item.message_code === 'CAPTURE_LEASE_LOST')).toHaveLength(1);
    expect(backend.revokeClassroom('A', ENDED)).toBe(true);
    const after = (backend.handle('A', 'GET', '/notifications', new URLSearchParams(), undefined, null)!.body as { items: S['Notification'][] }).items;
    expect(after.some(item => item.message_code === 'CAPTURE_LEASE_LOST')).toBe(false);
  });

  it('cuts the snippet at the original position of a match even when case folding changes lengths', () => {
    const store = createSearchStore();
    const owner = 'c1100000-111c-4000-8000-0000000000f1';
    ensureSearchAccount(store, owner);
    const room = 'c1100000-111c-4000-8000-0000000000f2';
    setSearchHits(store, owner, [{ courseId: 'c1100000-111c-4000-8000-0000000000f3', fields: [`${'İ'.repeat(100)}target tail`],
      result: { object_type: 'class_session', object_id: room, class_session_id: room, title: '合成', snippet: '', revision_id: null, locator: `classroom/${room}` } }]);
    const reply = handleSearchBackend(store, { ownerId: owner, method: 'GET', path: '/search', query: new URLSearchParams({ q: 'target' }) })!;
    expect((reply.body as { items: SearchResult[] }).items[0].snippet).toContain('target');
  });

  it('locates a match in the same folded text the search matched, even when folding depends on context', () => {
    const store = createSearchStore();
    const owner = 'c1100000-111c-4000-8000-0000000000f1';
    ensureSearchAccount(store, owner);
    const room = 'c1100000-111c-4000-8000-0000000000f2';
    setSearchHits(store, owner, [{ courseId: 'c1100000-111c-4000-8000-0000000000f3', fields: [`${'x'.repeat(400)}AΣ`],
      result: { object_type: 'class_session', object_id: room, class_session_id: room, title: '合成', snippet: '', revision_id: null, locator: `classroom/${room}` } }]);
    const reply = handleSearchBackend(store, { ownerId: owner, method: 'GET', path: '/search', query: new URLSearchParams({ q: 'ς' }) })!;
    expect((reply.body as { items: SearchResult[] }).items[0].snippet).toContain('AΣ');
  });

  it('records a lease-loss notice only for a live classroom, and announces every new visible notice', () => {
    const backend = createMockBackend();
    expect(backend.recordLeaseLost('A', 'c0000000-0000-4000-8000-0000000000ff', 'epoch-x')).toBeNull();
    // Same workbench timestamp for all of them: announcing never depends on which page a notice sorts into.
    for (let n = 0; n < 150; n++) backend.recordLeaseLost('A', ENDED, `epoch-${n}`);
    expect(backend.drainCreatedNotifications('A')).toHaveLength(150);
    backend.reset('A', 'empty');
    expect(backend.recordLeaseLost('A', ENDED, 'epoch-z')).toBeNull();
    expect(backend.drainCreatedNotifications('A')).toHaveLength(0);
  });

  it('treats a task whose declared snapshot cannot be resolved as having unavailable sources', () => {
    const store = seedClassroom({ ended: ENDED, uploadFailed: 'c0000000-0000-4000-8000-000000000002', unknownAi: UNKNOWN_AI });
    const task = store.tasks.find(item => item.source_snapshot_id !== null)!;
    expect(taskSourcesUnavailable(store, task.id, '2026-10-28T08:00:00Z')).toBe(false);
    store.plans.clear();
    expect(taskSourcesUnavailable(store, task.id, '2026-10-28T08:00:00Z')).toBe(true);
  });

  it('reseeding the empty data set leaves no notifications and no search hits', () => {
    const { backend, call, page } = harness();
    backend.reset('A', 'empty');
    expect(page(call('GET', '/notifications'))).toEqual([]);
    expect(page(call('GET', '/search', { q: '课' }))).toEqual([]);
  });
});

describe('PR-11c search result authorization before navigation', () => {
  const ACCOUNT = 'A:1';
  const segment = { id: 'aaaaaaaa-0000-4000-8000-000000000001', segment_revision_id: 'aaaaaaaa-0000-4000-8000-000000000002',
    class_session_id: 'aaaaaaaa-0000-4000-8000-000000000003' };
  const version = 'aaaaaaaa-0000-4000-8000-000000000004';
  const DOC = 'aaaaaaaa-0000-4000-8000-000000000006';
  const REVISION = 'aaaaaaaa-0000-4000-8000-000000000007';
  const transcriptHit: SearchResult = { object_type: 'transcript_segment', object_id: segment.id, class_session_id: segment.class_session_id,
    title: '合成课堂', snippet: '合成', revision_id: segment.segment_revision_id,
    locator: `transcript/${segment.class_session_id}/${version}/${segment.id}` };
  const review: SearchResult = { object_type: 'review_document', object_id: DOC, class_session_id: segment.class_session_id,
    title: '合成', snippet: '', revision_id: REVISION, locator: `review/${DOC}/${REVISION}` };
  const api = (overrides: Partial<Record<keyof ApiClient, unknown>>) => ({
    course: vi.fn(async () => ({})), classSession: vi.fn(async () => ({})),
    transcriptVersions: vi.fn(async () => ({ items: [{ id: version, adopted: true, kind: 'realtime' }], next_cursor: null })),
    transcriptSegments: vi.fn(async () => ({ items: [segment], next_cursor: null })),
    reviewDocument: vi.fn(async () => ({ id: DOC })),
    reviewRevisions: vi.fn(async () => ({ items: [{ id: REVISION, document_id: DOC }], next_cursor: null })), ...overrides,
  }) as unknown as ApiClient;
  const signal = () => new AbortController().signal;

  it('accepts the exact revision of the version the record shows, and refuses a changed or superseded one as gone', async () => {
    const client = new QueryClient();
    const reader = api({});
    await expect(authorizeSearchResult(client, ACCOUNT, reader, transcriptHit, signal())).resolves.toBeUndefined();
    expect(reader.transcriptSegments).toHaveBeenCalledWith(version, expect.any(AbortSignal));
    // The record page reads the same cache entry.
    expect(client.getQueryData(['account', ACCOUNT, 'transcript', segment.class_session_id])).toMatchObject({ segments: [segment] });
    const stale = { ...transcriptHit, revision_id: 'aaaaaaaa-0000-4000-8000-0000000000ff' };
    await expect(authorizeSearchResult(client, ACCOUNT, reader, stale, signal())).rejects.toMatchObject({ status: 404 });
    const superseded = api({ transcriptVersions: vi.fn(async () => ({ items: [{ id: version, adopted: false, kind: 'realtime' },
      { id: 'aaaaaaaa-0000-4000-8000-0000000000ee', adopted: true, kind: 'retranscription' }], next_cursor: null })) });
    await expect(authorizeSearchResult(new QueryClient(), ACCOUNT, superseded, transcriptHit, signal())).rejects.toMatchObject({ status: 404 });
  });

  it('opens a review hit only through the reader\'s own revision list, and passes refusals through its cache', async () => {
    await expect(authorizeSearchResult(new QueryClient(), ACCOUNT, api({}), review, signal())).resolves.toBeUndefined();
    const other = api({ reviewRevisions: vi.fn(async () => ({ items: [{ id: 'aaaaaaaa-0000-4000-8000-0000000000dd', document_id: DOC }], next_cursor: null })) });
    await expect(authorizeSearchResult(new QueryClient(), ACCOUNT, other, review, signal())).rejects.toMatchObject({ status: 404 });
    const client = new QueryClient();
    const denied = api({ reviewRevisions: vi.fn(async () => { throw new ApiClientError('http', 403); }) });
    await expect(authorizeSearchResult(client, ACCOUNT, denied, review, signal())).rejects.toMatchObject({ status: 403 });
    expect(client.getQueryState(['account', ACCOUNT, 'review-revisions', DOC])?.status).toBe('error');
    const expired = api({ course: vi.fn(async () => { throw new ApiClientError('http', 401); }) });
    const course: SearchResult = { object_type: 'course', object_id: segment.id, class_session_id: null, title: '合成', snippet: '',
      revision_id: null, locator: `course/${segment.id}` };
    await expect(authorizeSearchResult(new QueryClient(), ACCOUNT, expired, course, signal())).rejects.toMatchObject({ status: 401 });
  });

  it('a 401 outside the cache retires the epoch; a revoked classroom hides its hits and notifies subscribers', async () => {
    const client = new QueryClient();
    const onUnauthenticated = vi.fn();
    const ledger = createRevocationLedger({ client, accountKey: ACCOUNT, hooks: { onUnauthenticated } });
    ledger.observeError(new ApiClientError('http', 404));
    ledger.observeError(new ApiClientError('http', 401));
    await Promise.resolve();
    expect(onUnauthenticated).toHaveBeenCalledTimes(1);
    const listener = vi.fn();
    ledger.subscribe(listener);
    const before = ledger.version();
    expect(ledger.isSearchHitRevoked(transcriptHit)).toBe(false);
    ledger.revokeClass(segment.class_session_id);
    expect(listener).toHaveBeenCalled();
    expect(ledger.version()).not.toBe(before);
    expect(ledger.isSearchHitRevoked(transcriptHit)).toBe(true);
    expect(ledger.isSearchHitRevoked(review)).toBe(true);
  });

  it('a classroom named only by search hits is revoked by its denial on opening, hiding every sibling hit', async () => {
    const client = new QueryClient();
    const ledger = createRevocationLedger({ client, accountKey: ACCOUNT });
    const stop = ledger.install();
    // An empty ledger filters without reading the cache.
    const spy = vi.spyOn(client.getQueryCache(), 'getAll');
    expect(ledger.searchHitFilter()(transcriptHit)).toBe(false);
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
    ledger.recordSearchServed([transcriptHit, review]);
    const gone = api({ classSession: vi.fn(async () => { throw new ApiClientError('http', 404); }) });
    await expect(authorizeSearchResult(client, ACCOUNT, gone, transcriptHit, signal())).rejects.toMatchObject({ status: 404 });
    const filter = ledger.searchHitFilter();
    expect(filter(transcriptHit)).toBe(true);
    expect(filter(review)).toBe(true);
    stop();
  });

  it('authorization never joins a destination read already in flight', async () => {
    const client = new QueryClient();
    let release: (value: unknown) => void = () => undefined;
    void client.fetchQuery({ queryKey: ['account', ACCOUNT, 'course', segment.id], queryFn: () => new Promise(resolve => { release = resolve; }) }).catch(() => undefined);
    const course: SearchResult = { object_type: 'course', object_id: segment.id, class_session_id: null, title: '合成', snippet: '',
      revision_id: null, locator: `course/${segment.id}` };
    const denied = api({ course: vi.fn(async () => { throw new ApiClientError('http', 404); }) });
    const opening = authorizeSearchResult(client, ACCOUNT, denied, course, signal());
    release({ id: segment.id });
    await expect(opening).rejects.toMatchObject({ status: 404 });
    expect(denied.course).toHaveBeenCalledTimes(1);
  });

  it('keeps a feedback excerpt only for the account epoch that captured it, bound to its source', () => {
    const source = { object_type: 'class_session', object_id: segment.class_session_id, class_session_id: segment.class_session_id,
      locator: `classroom/${segment.class_session_id}` };
    const reference = stashFeedbackContext('A:1', { requestId: 'req-1', excerpt: '合成片段', source });
    expect(readFeedbackContext('A:1', reference)).toEqual({ requestId: 'req-1', excerpt: '合成片段', source });
    expect(readFeedbackContext('B:2', reference)).toBeNull();
    expect(readFeedbackContext('A:1', { excerpt: '合成片段' })).toBeNull();
    // The source is judged like a search hit: revoking its classroom revokes the excerpt.
    const ledger = createRevocationLedger({ client: new QueryClient(), accountKey: ACCOUNT });
    expect(ledger.isSearchHitRevoked(source)).toBe(false);
    ledger.revokeClass(segment.class_session_id);
    expect(ledger.isSearchHitRevoked(source)).toBe(true);
    discardFeedbackContext(reference);
    expect(readFeedbackContext('A:1', reference)).toBeNull();
    const other = stashFeedbackContext('A:1', { requestId: null, excerpt: '合成', source: null });
    forgetFeedbackContexts();
    expect(readFeedbackContext('A:1', other)).toBeNull();
  });

  it('a refused revision list hides that document\'s hits until a list read succeeds again', async () => {
    const client = new QueryClient();
    const ledger = createRevocationLedger({ client, accountKey: ACCOUNT });
    const stop = ledger.install();
    const listener = vi.fn();
    ledger.subscribe(listener);
    const denied = api({ reviewRevisions: vi.fn(async () => { throw new ApiClientError('http', 403); }) });
    await expect(authorizeSearchResult(client, ACCOUNT, denied, review, signal())).rejects.toMatchObject({ status: 403 });
    expect(listener).toHaveBeenCalled();
    expect(ledger.searchHitFilter()(review)).toBe(true);
    expect(ledger.searchHitFilter()(transcriptHit)).toBe(false);
    await expect(authorizeSearchResult(client, ACCOUNT, api({}), review, signal())).resolves.toBeUndefined();
    expect(ledger.searchHitFilter()(review)).toBe(false);
    stop();
  });

  it('a cascade deletion or a replaced data set resets held search results', () => {
    const ledger = createRevocationLedger({ client: new QueryClient(), accountKey: ACCOUNT });
    const start = ledger.searchEpoch();
    ledger.applyScopeReceipt({ kind: 'course', id: segment.id, mode: 'move_children', destination: version });
    expect(ledger.searchEpoch()).toBe(start);
    ledger.applyScopeReceipt({ kind: 'course', id: segment.id, mode: 'cascade' });
    expect(ledger.searchEpoch()).toBe(start + 1);
    ledger.applyAmbiguousScopeDeletion({ kind: 'semester', id: version, mode: 'move_children', destination: segment.id });
    expect(ledger.searchEpoch()).toBe(start + 2);
    ledger.reset();
    expect(ledger.searchEpoch()).toBe(start + 3);
  });

  it('an abandoned opening starts no further destination read', async () => {
    const controller = new AbortController();
    const reader = api({ transcriptVersions: vi.fn(async () => { controller.abort(); return { items: [], next_cursor: null }; }) });
    await expect(authorizeSearchResult(new QueryClient(), ACCOUNT, reader, transcriptHit, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(reader.transcriptSegments).not.toHaveBeenCalled();
    controller.abort();
    const later = api({});
    await expect(authorizeSearchResult(new QueryClient(), ACCOUNT, later, review, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(later.reviewDocument).not.toHaveBeenCalled();
  });

  it('an abandoned opening never leaves a page reading the same entry without a request', async () => {
    const queryKey = ['account', ACCOUNT, 'review-revisions', DOC];
    const list = { items: [{ id: REVISION, document_id: DOC }], next_cursor: null };
    const slow = () => new Promise<typeof list>(resolve => setTimeout(() => resolve(list), 10));
    // Abandoned while its own read runs: the read is the page's only request now, so it completes.
    const client = new QueryClient();
    const observer = new QueryObserver(client, { queryKey, queryFn: slow, retry: false });
    const unsubscribe = observer.subscribe(() => undefined);
    const controller = new AbortController();
    const reader = api({ reviewRevisions: vi.fn(async () => { controller.abort(); return slow(); }) });
    await expect(authorizeSearchResult(client, ACCOUNT, reader, review, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    await waitFor(() => expect(observer.getCurrentResult().status).toBe('success'));
    unsubscribe();
    // Abandoned right after cancelling the page's own read: the page reads again.
    const other = new QueryClient();
    const watching = new QueryObserver(other, { queryKey, queryFn: slow, retry: false });
    const stopWatching = watching.subscribe(() => undefined);
    const second = new AbortController();
    const cancel = other.cancelQueries.bind(other);
    vi.spyOn(other, 'cancelQueries').mockImplementation(async (filters, options) => {
      await cancel(filters, options);
      if (filters?.queryKey?.[2] === 'review-revisions') second.abort();
    });
    const idle = api({});
    await expect(authorizeSearchResult(other, ACCOUNT, idle, review, second.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(idle.reviewRevisions).not.toHaveBeenCalled();
    await waitFor(() => expect(watching.getCurrentResult().status).toBe('success'));
    stopWatching();
  });

  it('a fresh revision list that omits a served revision hides exactly that revision in every view', async () => {
    const client = new QueryClient();
    const ledger = createRevocationLedger({ client, accountKey: ACCOUNT });
    const stop = ledger.install();
    const SIBLING = 'aaaaaaaa-0000-4000-8000-0000000000dd';
    const sibling: SearchResult = { ...review, revision_id: SIBLING, locator: `review/${DOC}/${SIBLING}` };
    ledger.recordSearchServed([review, sibling]);
    const listener = vi.fn();
    ledger.subscribe(listener);
    const without = api({ reviewRevisions: vi.fn(async () => ({ items: [{ id: SIBLING, document_id: DOC }], next_cursor: null })) });
    await expect(authorizeSearchResult(client, ACCOUNT, without, review, signal())).rejects.toMatchObject({ status: 404 });
    expect(listener).toHaveBeenCalled();
    const filter = ledger.searchHitFilter();
    expect(filter(review)).toBe(true);
    expect(filter(sibling)).toBe(false);
    // An excerpt bound to that revision goes with it; the document itself stays readable.
    expect(filter({ object_type: 'review_document', object_id: DOC, class_session_id: null, locator: `review/${DOC}`, revision_id: REVISION })).toBe(true);
    expect(filter({ object_type: 'review_document', object_id: DOC, class_session_id: null, locator: `review/${DOC}` })).toBe(false);
    const both = api({ reviewRevisions: vi.fn(async () => ({ items: [{ id: REVISION, document_id: DOC }, { id: SIBLING, document_id: DOC }], next_cursor: null })) });
    await expect(authorizeSearchResult(client, ACCOUNT, both, review, signal())).resolves.toBeUndefined();
    expect(ledger.searchHitFilter()(review)).toBe(false);
    stop();
  });

  it('discards a feedback excerpt taken before the search epoch changed, even with the page closed', () => {
    const ledger = createRevocationLedger({ client: new QueryClient(), accountKey: ACCOUNT });
    const reference = stashFeedbackContext('A:1', { requestId: null, excerpt: '合成片段', source: null }, ledger.searchEpoch());
    expect(feedbackContextStale(reference, ledger.searchEpoch())).toBe(false);
    ledger.applyScopeReceipt({ kind: 'course', id: segment.id, mode: 'cascade' }, { exempt: null, unverifiedLinks: true });
    expect(feedbackContextStale(reference, ledger.searchEpoch())).toBe(true);
    // A request ID alone carries no classroom text.
    const plain = stashFeedbackContext('A:1', { requestId: 'req-2', excerpt: null, source: null }, 0);
    expect(feedbackContextStale(plain, ledger.searchEpoch())).toBe(false);
    forgetFeedbackContexts();
  });

  it('a refused mark-read drops the notice from the dashboard snapshot at once', async () => {
    const client = new QueryClient();
    const key = ['account', ACCOUNT, 'notifications'];
    const first = 'aaaaaaaa-0000-4000-8000-0000000000a1';
    const second = 'aaaaaaaa-0000-4000-8000-0000000000a2';
    client.setQueryData(key, { items: [{ id: first }, { id: second }], next_cursor: null });
    const reader = api({ readNotification: vi.fn(async () => { throw new ApiClientError('http', 404); }) });
    await expect(createOperations(client, ACCOUNT, reader).readNotification(first, 'k', signal())).rejects.toMatchObject({ status: 404 });
    expect(client.getQueryData(key)).toMatchObject({ items: [{ id: second }] });
    const offline = api({ readNotification: vi.fn(async () => { throw new ApiClientError('network'); }) });
    await expect(createOperations(client, ACCOUNT, offline).readNotification(second, 'k', signal())).rejects.toMatchObject({ kind: 'network' });
    expect(client.getQueryData(key)).toMatchObject({ items: [{ id: second }] });
  });
});

describe('PR-11c shell components', () => {
  it('searches a keyword carried in the address once and reports submitted keywords', async () => {
    const search = vi.fn<(query: { q: string; cursor?: string }, signal: AbortSignal) => Promise<{ items: never[]; next_cursor: null }>>(
      async () => ({ items: [], next_cursor: null }));
    const onSubmitQuery = vi.fn();
    render(createElement(MemoryRouter, null, createElement(SearchPage, { accountKey: 'A:1', online: true, search,
      openResult: async () => {}, initialQuery: '线性代数', onSubmitQuery })));
    await waitFor(() => expect(search).toHaveBeenCalledTimes(1));
    expect(search.mock.calls[0][0]).toEqual({ q: '线性代数', cursor: undefined });
    expect(onSubmitQuery).toHaveBeenCalledWith('线性代数');
    expect(await screen.findByText('没有匹配的结果。')).toBeTruthy();
  });

  it('hides hits that point into revoked content as soon as the predicate changes', async () => {
    const hit: SearchResult = { object_type: 'class_session', object_id: 'aaaaaaaa-0000-4000-8000-000000000003',
      class_session_id: 'aaaaaaaa-0000-4000-8000-000000000003', title: '合成课堂', snippet: '', revision_id: null,
      locator: 'classroom/aaaaaaaa-0000-4000-8000-000000000003' };
    const props = { accountKey: 'A:1', online: true, search: async () => ({ items: [hit], next_cursor: null }),
      openResult: async () => {}, initialQuery: '合成' };
    const view = render(createElement(MemoryRouter, null, createElement(SearchPage, { ...props, isRevoked: () => false })));
    expect(await screen.findByRole('button', { name: '合成课堂' })).toBeTruthy();
    view.rerender(createElement(MemoryRouter, null, createElement(SearchPage, { ...props, isRevoked: () => true })));
    expect(screen.queryByRole('button', { name: '合成课堂' })).toBeNull();
    expect(screen.getByText('没有匹配的结果。')).toBeTruthy();
  });

  it('offers the capture stop inside the modal palette while a capture is running', async () => {
    const onStopCapture = vi.fn();
    const onClose = vi.fn();
    const props = { open: true, onClose, accountKey: 'A:1', online: true,
      search: async () => ({ items: [], next_cursor: null }), openResult: async () => {} };
    const view = render(createElement(CommandPalette, { ...props, onStopCapture }));
    await userEvent.click(screen.getByRole('button', { name: '停止采集' }));
    expect(onStopCapture).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalled();
    view.rerender(createElement(CommandPalette, props));
    expect(screen.queryByRole('button', { name: '停止采集' })).toBeNull();
  });

  it('marks the opened transcript segment only while its exact revision is shown', () => {
    const entry = (n: number, revision: string): TimelineEntry => ({ key: `k${n}`, segmentId: `s${n}`, status: 'final', text: `合成字幕 ${n}`,
      startMs: n * 1000, endMs: n * 1000 + 900, revision: 1, sequence: n, segmentRevisionId: revision });
    const entries = [entry(1, 'r1'), entry(2, 'r2'), entry(3, 'r3')];
    const view = render(createElement(TranscriptTimeline, { entries, label: '课堂正式字幕', emptyText: '无', focus: { segmentId: 's2', segmentRevisionId: 'r2' } }));
    const current = screen.getAllByRole('listitem').filter(item => item.getAttribute('aria-current') === 'location');
    expect(current.map(item => item.getAttribute('data-segment-id'))).toEqual(['s2']);
    expect(screen.getByTestId('transcript-timeline').getAttribute('data-following')).toBe('false');
    view.rerender(createElement(TranscriptTimeline, { entries, label: '课堂正式字幕', emptyText: '无', focus: { segmentId: 's2', segmentRevisionId: 'stale' } }));
    expect(screen.getAllByRole('listitem').some(item => item.getAttribute('aria-current') === 'location')).toBe(false);
  });
});
