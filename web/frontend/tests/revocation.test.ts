import { describe, expect, it } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { ApiClientError } from '../src/api/client';
import { createRevocationLedger, isAuthoritativeDenial, submitScopeDeletion } from '../src/features/revocation';

/**
 * Account-shell revocation ledger (REVOCATION-DESIGN.md): deletion receipts, deletion events and
 * authoritative denials revoke cached content at once, with no refetch (which fails offline).
 */
const A = 'A:1';
const key = (...parts: unknown[]) => ['account', A, ...parts];
const page = <T,>(items: T[]) => ({ items, next_cursor: null });
const ids = (client: QueryClient, ...parts: unknown[]) => (client.getQueryData(key(...parts)) as { items: Array<{ id: string }> } | undefined)?.items.map(item => item.id);
const httpError = (status: number, code: string) => new ApiClientError('http', status, { error: { code, message: 'x', request_id: 'r', retryable: false, retry_after_seconds: null, current_revision: null, conflict_id: null, task_id: null } } as never);
const networkError = () => new ApiClientError('network');
const deferred = <T,>() => { let resolve!: (value: T) => void; let reject!: (error: unknown) => void; const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail; }); return { promise, resolve, reject }; };

function setup(bound: string | null = null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const revoked: string[] = [];
  const ledger = createRevocationLedger({ client, accountKey: A, hooks: { boundClassId: () => bound, onClassRevoked: id => revoked.push(id) } });
  const stop = ledger.install();
  return { client, ledger, revoked, stop };
}

/** A fetch through the cache, so the ledger's guard sees it like a page refetch. */
const fetchInto = (client: QueryClient, parts: unknown[], fn: () => Promise<unknown>) => client.fetchQuery({ queryKey: key(...parts), queryFn: fn, staleTime: 0 }).catch(() => undefined);

describe('scenario 1 · the receipt uses the submitted scope and mode', () => {
  it('a cascade submitted and then switched to "move" in the dialog still revokes the children', async () => {
    const { client, ledger, revoked } = setup();
    client.setQueryData(key('class-sessions', null), page([{ id: 'c1', course_id: 'k1' }]));
    const reply = deferred<unknown>();
    const dialog = { mode: 'cascade' as 'cascade' | 'move_children' };
    const sent: unknown[] = [];
    const api = { deleteCourse: (_id: string, body: unknown) => { sent.push(body); return reply.promise as never; }, deleteSemester: () => Promise.reject(new Error('unused')) };
    const pending = submitScopeDeletion(api, ledger, { kind: 'course', id: 'k1', mode: dialog.mode, destination: '', revision: 3, token: 't' });
    dialog.mode = 'move_children'; // the user flips the choice while the request is in flight
    await Promise.resolve();
    reply.resolve({ scope_type: 'course', scope_id: 'k1', state: 'access_revoked' });
    await pending;
    expect(sent).toEqual([{ base_revision: 3, mode: 'cascade', confirmation_token: 't' }]);
    expect(ids(client, 'class-sessions', null)).toEqual([]);
    expect(revoked).toEqual(['c1']);
  });
  it('a submitted "move children" deletes only the course object; moved classrooms stay', async () => {
    const { client, ledger, revoked } = setup('c1');
    client.setQueryData(key('courses', {}), page([{ id: 'k1', semester_id: 's1' }, { id: 'k2', semester_id: 's1' }]));
    client.setQueryData(key('course', 'k1'), { id: 'k1', semester_id: 's1' });
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'k1' });
    client.setQueryData(key('marks', 'c1'), page([{ id: 'm1' }]));
    await submitScopeDeletion({ deleteCourse: () => Promise.resolve({ scope_type: 'course', scope_id: 'k1', state: 'access_revoked' } as never), deleteSemester: () => Promise.reject(new Error('unused')) }, ledger, { kind: 'course', id: 'k1', mode: 'move_children', destination: 'k2', revision: 1, token: 't' });
    expect(ids(client, 'courses', {})).toEqual(['k2']);
    expect(client.getQueryState(key('course', 'k1'))?.status).toBe('error');
    expect(client.getQueryData(key('class-session', 'c1'))).toBeDefined();
    expect(ids(client, 'marks', 'c1')).toEqual(['m1']);
    expect(revoked).toEqual([]);
  });
  it('a lost deletion reply hides what it may have removed, but keeps nothing revoked once the server answers', async () => {
    const { client, ledger, revoked } = setup('c1');
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'k1' });
    client.setQueryData(key('class-sessions', null), page([{ id: 'c1', course_id: 'k1' }]));
    await expect(submitScopeDeletion({ deleteCourse: () => Promise.reject(networkError()), deleteSemester: () => Promise.reject(new Error('unused')) }, ledger, { kind: 'course', id: 'k1', mode: 'cascade', destination: '', revision: 1, token: 't' })).rejects.toBeInstanceOf(ApiClientError);
    expect(client.getQueryData(key('class-session', 'c1'))).toBeUndefined();
    expect(client.getQueryData(key('class-sessions', null))).toBeUndefined();
    expect(revoked).toEqual([]);
    // A fresh server read then shows the course was not deleted: its data is accepted again.
    await fetchInto(client, ['class-session', 'c1'], () => Promise.resolve({ id: 'c1', course_id: 'k1' }));
    expect(client.getQueryData(key('class-session', 'c1'))).toBeDefined();
  });
});

describe('scenario 2 · a confirmed cascade revokes the bound capture at once', () => {
  it('the bound classroom of the deleted course is revoked without any classroom read', () => {
    const { client, ledger, revoked } = setup('c1');
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'k1' });
    ledger.applyScopeReceipt({ kind: 'course', id: 'k1', mode: 'cascade' });
    expect(revoked).toEqual(['c1']);
    expect(client.getQueryState(key('class-session', 'c1'))?.status).toBe('error');
    expect(ledger.isClassRevoked('c1')).toBe(true); // remembered by ID once its membership leaves the cache
  });
  it('a semester cascade revokes a bound classroom whose course is known to be in it', () => {
    const { client, ledger, revoked } = setup('c1');
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'k1' });
    client.setQueryData(key('course', 'k1'), { id: 'k1', semester_id: 's1' });
    ledger.applyScopeReceipt({ kind: 'semester', id: 's1', mode: 'cascade' });
    expect(revoked).toEqual(['c1']);
  });
  it('a bound classroom known to be elsewhere keeps capturing', () => {
    const { client, ledger, revoked } = setup('c2');
    client.setQueryData(key('class-session', 'c2'), { id: 'c2', course_id: 'k2' });
    ledger.applyScopeReceipt({ kind: 'course', id: 'k1', mode: 'cascade' });
    expect(revoked).toEqual([]);
  });
  it('prepare resolves the bound membership first; an unresolvable one only suspends the capture', async () => {
    const known = setup('c1');
    const api = { classSession: () => Promise.resolve({ id: 'c1', course_id: 'k9' }), course: () => Promise.resolve({ id: 'k9', semester_id: 's9' }) };
    const ledger = createRevocationLedger({ client: known.client, accountKey: A, api: api as never, hooks: { boundClassId: () => 'c1', onClassRevoked: id => known.revoked.push(id) } });
    await ledger.prepareScopeDeletion({ kind: 'semester', id: 's1' });
    ledger.applyScopeReceipt({ kind: 'semester', id: 's1', mode: 'cascade' });
    expect(known.revoked).toEqual([]);

    const unknown = setup();
    const suspended: string[] = [];
    const failing = createRevocationLedger({ client: unknown.client, accountKey: A, api: { classSession: () => Promise.reject(networkError()) } as never, hooks: { boundClassId: () => 'c1', onClassRevoked: id => unknown.revoked.push(id), onCaptureSuspended: id => suspended.push(id) } });
    const prepared = await failing.prepareScopeDeletion({ kind: 'course', id: 'k1' });
    failing.applyScopeReceipt({ kind: 'course', id: 'k1', mode: 'cascade' }, prepared);
    // Not a confirmed deletion of c1: no tombstone or deletion event; the capture stops locally only.
    expect(unknown.revoked).toEqual([]);
    expect(suspended).toEqual(['c1']);
  });
  it('an earlier cascade does not make a later, unrelated revocation stop a capture of unknown membership', () => {
    const client = new QueryClient();
    const revoked: string[] = [];
    let bound: string | null = null;
    const ledger = createRevocationLedger({ client, accountKey: A, hooks: { boundClassId: () => bound, onClassRevoked: id => revoked.push(id) } });
    ledger.applyScopeReceipt({ kind: 'course', id: 'k1', mode: 'cascade' });
    bound = 'c-new'; // bound after the cascade; its course is not cached
    ledger.revokeClass('c-y');
    expect(revoked).toEqual(['c-y']);
  });
  it('a bound classroom the server does not know (unregistered demo) is not inside any deleted scope', async () => {
    const { client, revoked } = setup();
    const suspended: string[] = [];
    const ledger = createRevocationLedger({ client, accountKey: A, api: { classSession: () => Promise.reject(httpError(404, 'NOT_FOUND')) } as never, hooks: { boundClassId: () => 'demo', onClassRevoked: id => revoked.push(id), onCaptureSuspended: id => suspended.push(id) } });
    const prepared = await ledger.prepareScopeDeletion({ kind: 'course', id: 'k1' });
    ledger.applyScopeReceipt({ kind: 'course', id: 'k1', mode: 'cascade' }, prepared);
    expect(revoked).toEqual([]);
    expect(suspended).toEqual([]);
    // The exemption belongs to that deletion only: a later cascade without one suspends the unknown capture.
    ledger.applyScopeReceipt({ kind: 'course', id: 'k2', mode: 'cascade' });
    expect(suspended).toEqual(['demo']);
  });
});

describe('scenario 3 · unknown membership is never treated as unaffected', () => {
  it('a review list with a classroom of unknown course is dropped, not shown', () => {
    const { client, ledger } = setup();
    client.setQueryData(key('class-sessions', null), page([{ id: 'c2', course_id: 'k2' }]));
    client.setQueryData(key('review-library', {}), page([{ id: 'd1', class_session_id: 'c-unknown' }, { id: 'd2', class_session_id: 'c2' }]));
    client.setQueryData(key('review-library', { class_session_id: 'c2' }), page([{ id: 'd2', class_session_id: 'c2' }]));
    ledger.applyScopeReceipt({ kind: 'semester', id: 's1', mode: 'cascade' });
    // c2's course k2 has no known semester either: under a semester cascade both lists are untrustworthy.
    expect(client.getQueryData(key('review-library', {}))).toBeUndefined();
    expect(client.getQueryData(key('review-library', { class_session_id: 'c2' }))).toBeUndefined();
  });
  it('revisions, segments and classroom-keyed data of unknown membership are dropped; proven-unaffected data stays', () => {
    const { client, ledger } = setup();
    client.setQueryData(key('class-session', 'c2'), { id: 'c2', course_id: 'k2' });
    client.setQueryData(key('tasks', 'c2'), page([{ id: 't2' }]));
    client.setQueryData(key('tasks', 'c-unknown'), page([{ id: 't9' }]));
    client.setQueryData(key('review-revisions', 'd-unknown'), page([{ id: 'r9' }]));
    client.setQueryData(key('transcript-segments', 'v-unknown'), page([{ id: 'g9' }]));
    ledger.applyScopeReceipt({ kind: 'course', id: 'k1', mode: 'cascade' });
    expect(ids(client, 'tasks', 'c2')).toEqual(['t2']);
    expect(client.getQueryData(key('tasks', 'c-unknown'))).toBeUndefined();
    expect(client.getQueryData(key('review-revisions', 'd-unknown'))).toBeUndefined();
    expect(client.getQueryData(key('transcript-segments', 'v-unknown'))).toBeUndefined();
  });
  it('after the sweep, newly read data of a classroom outside the scope is accepted again', () => {
    const { client, ledger } = setup();
    ledger.applyScopeReceipt({ kind: 'course', id: 'k1', mode: 'cascade' });
    client.setQueryData(key('tasks', 'c-later'), page([{ id: 't1' }]));
    expect(ids(client, 'tasks', 'c-later')).toEqual(['t1']);
  });
});

describe('scenario 4 · authoritative denials remove cached content; transport failures keep it', () => {
  it('a FORBIDDEN revisions refetch removes the cached revision bodies', async () => {
    const { client } = setup();
    client.setQueryData(key('review-revisions', 'd1'), page([{ id: 'r1', body_markdown: '旧内容' }]));
    await fetchInto(client, ['review-revisions', 'd1'], () => Promise.reject(httpError(403, 'FORBIDDEN')));
    expect(client.getQueryState(key('review-revisions', 'd1'))?.status).toBe('error');
    expect(client.getQueryData(key('review-revisions', 'd1'))).toBeUndefined();
  });
  it('a network failure keeps the cached content for offline reading', async () => {
    const { client } = setup();
    client.setQueryData(key('review-document', 'd1'), { id: 'd1', class_session_id: 'c1', title: '旧' });
    await fetchInto(client, ['review-document', 'd1'], () => Promise.reject(networkError()));
    expect(client.getQueryState(key('review-document', 'd1'))?.status).toBe('error');
    expect(client.getQueryData(key('review-document', 'd1'))).toMatchObject({ title: '旧' });
  });
  it('a classroom the server served before and now refuses is revoked everywhere (capture included)', async () => {
    const { client, revoked } = setup('c1');
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'k1' });
    client.setQueryData(key('review-library', {}), page([{ id: 'd1', class_session_id: 'c1' }, { id: 'd2', class_session_id: 'c2' }]));
    client.setQueryData(key('class-sessions', null), page([{ id: 'c1', course_id: 'k1' }, { id: 'c2', course_id: 'k1' }]));
    await fetchInto(client, ['class-session', 'c1'], () => Promise.reject(httpError(404, 'NOT_FOUND')));
    expect(revoked).toEqual(['c1']);
    expect(client.getQueryData(key('class-session', 'c1'))).toBeUndefined();
    expect(ids(client, 'review-library', {})).toEqual(['d2']);
    expect(ids(client, 'class-sessions', null)).toEqual(['c2']);
  });
  it('a classroom never served before (unregistered demo) is not revoked by NOT_FOUND', async () => {
    const { client, revoked } = setup('demo');
    await fetchInto(client, ['class-session', 'demo'], () => Promise.reject(httpError(404, 'NOT_FOUND')));
    expect(revoked).toEqual([]);
  });
  it('classifies only the server refusing the resource as authoritative', () => {
    expect(isAuthoritativeDenial(httpError(404, 'NOT_FOUND'))).toBe(true);
    expect(isAuthoritativeDenial(httpError(410, 'GONE'))).toBe(true);
    expect(isAuthoritativeDenial(httpError(403, 'FORBIDDEN'))).toBe(true);
    expect(isAuthoritativeDenial(httpError(409, 'REVISION_CONFLICT'))).toBe(false);
    expect(isAuthoritativeDenial(networkError())).toBe(false);
    expect(isAuthoritativeDenial(new ApiClientError('stale'))).toBe(false);
  });
});

describe('scenario 5 · one mechanism covers 09b, 10a and 10b projections', () => {
  function seeded() {
    const ctx = setup('c1');
    const { client } = ctx;
    // 09b: semesters, courses, course detail, materials, dashboard review documents
    client.setQueryData(key('semesters'), page([{ id: 's1' }, { id: 's2' }]));
    client.setQueryData(key('courses', {}), page([{ id: 'k1', semester_id: 's1' }, { id: 'k2', semester_id: 's2' }]));
    client.setQueryData(['account', 'A', 'courses', 1], page([{ id: 'k1', semester_id: 's1' }, { id: 'k2', semester_id: 's2' }]));
    client.setQueryData(key('course', 'k1'), { id: 'k1', semester_id: 's1' });
    client.setQueryData(key('materials', 'k1'), page([{ id: 'mat1' }]));
    client.setQueryData(key('review-documents', {}), page([{ id: 'd1', class_session_id: 'c1' }, { id: 'd2', class_session_id: 'c2' }]));
    client.setQueryData(key('review-documents', { course_id: 'k1' }), page([{ id: 'd1', class_session_id: 'c1' }]));
    // 10a: classrooms and everything keyed by them
    client.setQueryData(key('class-sessions', null), page([{ id: 'c1', course_id: 'k1' }, { id: 'c2', course_id: 'k2' }]));
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'k1' });
    for (const kind of ['marks', 'tasks', 'questions', 'slides', 'audio-manifests', 'transcript-versions']) {
      client.setQueryData(key(kind, 'c1'), page([{ id: `${kind}-1`, class_session_id: 'c1' }]));
      client.setQueryData(key(kind, 'c2'), page([{ id: `${kind}-2`, class_session_id: 'c2' }]));
    }
    client.setQueryData(key('transcript', 'c1'), { segments: [] });
    client.setQueryData(key('class-segments', 'c1'), []);
    // 10b: review library, documents, revisions, snapshots, segments
    client.setQueryData(key('review-library', {}), page([{ id: 'd1', class_session_id: 'c1' }, { id: 'd2', class_session_id: 'c2' }]));
    client.setQueryData(key('review-document', 'd1'), { id: 'd1', class_session_id: 'c1' });
    client.setQueryData(key('review-revisions', 'd1'), page([{ id: 'r1' }]));
    client.setQueryData(key('review-revisions', 'd2'), page([{ id: 'r2' }]));
    client.setQueryData(key('source-snapshot', 'p1'), { id: 'p1', class_session_id: 'c1' });
    client.setQueryData(key('transcript-segments', 'transcript-versions-1'), page([{ id: 'g1' }]));
    client.setQueryData(key('transcript-segments', 'transcript-versions-2'), page([{ id: 'g2' }]));
    return ctx;
  }
  it('a semester cascade revokes every page projection of its courses and classrooms, and nothing else', () => {
    const { client, ledger, revoked } = seeded();
    ledger.applyScopeReceipt({ kind: 'semester', id: 's1', mode: 'cascade' });
    expect(ids(client, 'semesters')).toEqual(['s2']);
    expect(ids(client, 'courses', {})).toEqual(['k2']);
    expect((client.getQueryData(['account', 'A', 'courses', 1]) as { items: Array<{ id: string }> }).items.map(item => item.id)).toEqual(['k2']);
    for (const parts of [['course', 'k1'], ['materials', 'k1'], ['class-session', 'c1'], ['transcript', 'c1'], ['class-segments', 'c1'], ['review-document', 'd1'], ['review-revisions', 'd1'], ['source-snapshot', 'p1'], ['transcript-segments', 'transcript-versions-1']]) {
      expect(client.getQueryData(key(...parts)), parts.join('/')).toBeUndefined();
    }
    for (const kind of ['marks', 'tasks', 'questions', 'slides', 'audio-manifests', 'transcript-versions']) {
      expect(client.getQueryData(key(kind, 'c1')), kind).toBeUndefined();
      expect(ids(client, kind, 'c2'), kind).toEqual([`${kind}-2`]);
    }
    expect(ids(client, 'review-documents', {})).toEqual(['d2']);
    expect(ids(client, 'review-documents', { course_id: 'k1' })).toEqual([]);
    expect(ids(client, 'class-sessions', null)).toEqual(['c2']);
    expect(ids(client, 'review-library', {})).toEqual(['d2']);
    expect(ids(client, 'review-revisions', 'd2')).toEqual(['r2']);
    expect(ids(client, 'transcript-segments', 'transcript-versions-2')).toEqual(['g2']);
    expect(revoked).toEqual(['c1']);
  });
  it('a classroom deletion event revokes that classroom across pages', () => {
    const { client, ledger, revoked } = seeded();
    ledger.revokeClass('c1');
    expect(revoked).toEqual(['c1']);
    expect(ids(client, 'class-sessions', null)).toEqual(['c2']);
    expect(ids(client, 'review-library', {})).toEqual(['d2']);
    expect(client.getQueryData(key('review-revisions', 'd1'))).toBeUndefined();
    expect(ids(client, 'courses', {})).toEqual(['k1', 'k2']);
  });
  it('an audio revocation makes the classroom manifest traversal unavailable, never a filtered "complete" result', () => {
    const { client, ledger } = setup();
    client.setQueryData(key('audio-manifests', 'c1'), page([{ capture_epoch_id: 'e1' }, { capture_epoch_id: 'e2' }]));
    client.setQueryData(key('audio-manifests', 'c2'), page([{ capture_epoch_id: 'e9' }]));
    ledger.revokeAudio('c1', 'e1');
    expect(client.getQueryData(key('audio-manifests', 'c1'))).toBeUndefined();
    expect(client.getQueryState(key('audio-manifests', 'c1'))?.status).toBe('error');
    expect(client.getQueryData(key('audio-manifests', 'c2'))).toBeDefined();
    // A later traversal that still contains the revoked epoch is unavailable too.
    client.setQueryData(key('audio-manifests', 'c1'), page([{ capture_epoch_id: 'e1' }, { capture_epoch_id: 'e2' }]));
    expect(client.getQueryData(key('audio-manifests', 'c1'))).toBeUndefined();
  });
  it('a late reply of a request started before the receipt cannot bring deleted content back', async () => {
    const { client, ledger } = setup();
    client.setQueryData(key('class-sessions', null), page([{ id: 'c1', course_id: 'k1' }, { id: 'c2', course_id: 'k2' }]));
    const late = deferred<unknown>();
    const inFlight = fetchInto(client, ['review-library', {}], () => late.promise);
    ledger.applyScopeReceipt({ kind: 'course', id: 'k1', mode: 'cascade' });
    late.resolve(page([{ id: 'd1', class_session_id: 'c1' }, { id: 'd2', class_session_id: 'c2' }]));
    await inFlight;
    expect(ids(client, 'review-library', {})).toEqual(['d2']);
    // A fresh read after the receipt is judged too.
    await fetchInto(client, ['review-library', {}], () => Promise.resolve(page([{ id: 'd1', class_session_id: 'c1' }, { id: 'd2', class_session_id: 'c2' }])));
    expect(ids(client, 'review-library', {})).toEqual(['d2']);
  });
  it('a request in flight during the receipt is judged strictly when it lands (unknown membership is dropped)', async () => {
    const { client, ledger } = setup();
    const late = deferred<unknown>();
    const inFlight = fetchInto(client, ['review-library', {}], () => late.promise);
    ledger.applyScopeReceipt({ kind: 'course', id: 'k1', mode: 'cascade' });
    late.resolve(page([{ id: 'd9', class_session_id: 'c-unknown' }]));
    await inFlight;
    expect(client.getQueryData(key('review-library', {}))).toBeUndefined();
  });
  it('the ledger guard ends with its account epoch (unsubscribe)', () => {
    const { client, ledger, stop } = setup();
    ledger.revokeClass('c1');
    stop();
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'k1' });
    expect(client.getQueryData(key('class-session', 'c1'))).toBeDefined();
  });
});

describe('a revoked projection stays revoked when an offline refetch fails', () => {
  it('a transport failure after revocation keeps the "deleted" state instead of a connection error', async () => {
    const { client, ledger } = setup();
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'k1' });
    ledger.applyScopeReceipt({ kind: 'course', id: 'k1', mode: 'cascade' });
    await fetchInto(client, ['class-session', 'c1'], () => Promise.reject(networkError()));
    expect((client.getQueryState(key('class-session', 'c1'))?.error as ApiClientError).code).toBe('GONE');
    expect(client.getQueryData(key('class-session', 'c1'))).toBeUndefined();
  });
});

describe('round-0 review regressions', () => {
  it('a classroom denial after an ambiguous deletion reset its cached detail still revokes it', async () => {
    const { client, ledger, revoked } = setup('c1');
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'k1' });
    ledger.applyAmbiguousScopeDeletion({ kind: 'course', id: 'k1', mode: 'cascade' });
    expect(client.getQueryData(key('class-session', 'c1'))).toBeUndefined();
    await fetchInto(client, ['class-session', 'c1'], () => Promise.reject(httpError(404, 'NOT_FOUND')));
    expect(revoked).toEqual(['c1']);
  });
  it('a later cascade of the move destination revokes the classrooms moved into it', () => {
    const { client, ledger, revoked } = setup();
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'kA', revision: 1 });
    client.setQueryData(key('review-document', 'd1'), { id: 'd1', class_session_id: 'c1' });
    ledger.applyScopeReceipt({ kind: 'course', id: 'kA', mode: 'move_children', destination: 'kB' });
    expect(client.getQueryData(key('class-session', 'c1'))).toBeDefined();
    ledger.applyScopeReceipt({ kind: 'course', id: 'kB', mode: 'cascade' });
    expect(client.getQueryData(key('class-session', 'c1'))).toBeUndefined();
    expect(client.getQueryData(key('review-document', 'd1'))).toBeUndefined();
    expect(revoked).toEqual(['c1']);
  });
  it('a later semester cascade follows courses moved into it', () => {
    const { client, ledger } = setup();
    client.setQueryData(key('course', 'k1'), { id: 'k1', semester_id: 'sA' });
    ledger.applyScopeReceipt({ kind: 'semester', id: 'sA', mode: 'move_children', destination: 'sB' });
    expect(client.getQueryData(key('course', 'k1'))).toBeDefined();
    ledger.applyScopeReceipt({ kind: 'semester', id: 'sB', mode: 'cascade' });
    expect(client.getQueryData(key('course', 'k1'))).toBeUndefined();
  });
  it('membership comes from the newest revision; a stale list entry cannot revoke a moved classroom', () => {
    const { client, ledger, revoked } = setup();
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'kB', revision: 2 });
    client.setQueryData(key('class-sessions', 'kA'), page([{ id: 'c1', course_id: 'kA', revision: 1 }]));
    ledger.applyScopeReceipt({ kind: 'course', id: 'kA', mode: 'cascade' });
    expect(client.getQueryData(key('class-session', 'c1'))).toBeDefined();
    expect(revoked).toEqual([]);
  });
  it('equal revisions that disagree are unknown: dropped, never tombstoned', () => {
    const { client, ledger, revoked } = setup();
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'kB', revision: 1 });
    client.setQueryData(key('class-sessions', 'kA'), page([{ id: 'c1', course_id: 'kA', revision: 1 }]));
    ledger.applyScopeReceipt({ kind: 'course', id: 'kA', mode: 'cascade' });
    expect(client.getQueryData(key('class-session', 'c1'))).toBeUndefined();
    expect(ledger.isClassRevoked('c1')).toBe(false);
    expect(revoked).toEqual([]);
  });
  it('an ambiguous deletion cancels reads already in flight, so their pre-deletion reply never lands', async () => {
    const { client, ledger } = setup();
    const late = deferred<unknown>();
    const inFlight = fetchInto(client, ['review-library', {}], () => late.promise);
    ledger.applyAmbiguousScopeDeletion({ kind: 'course', id: 'k1', mode: 'cascade' });
    late.resolve(page([{ id: 'd1', class_session_id: 'c1' }]));
    await inFlight;
    expect(client.getQueryData(key('review-library', {}))).toBeUndefined();
  });
  it('a 401 refetch removes cached private content (not an offline read)', async () => {
    const { client } = setup();
    client.setQueryData(key('review-document', 'd1'), { id: 'd1', class_session_id: 'c1', title: '旧' });
    await fetchInto(client, ['review-document', 'd1'], () => Promise.reject(httpError(401, 'UNAUTHENTICATED')));
    expect(client.getQueryData(key('review-document', 'd1'))).toBeUndefined();
  });
  it('a request spanning two revocations is judged by both when it lands', async () => {
    const { client, ledger } = setup();
    const late = deferred<unknown>();
    const inFlight = fetchInto(client, ['review-library', {}], () => late.promise);
    ledger.applyScopeReceipt({ kind: 'course', id: 'k1', mode: 'cascade' });
    ledger.revokeClass('c-other');
    // c-known's course is unknown: the cascade alone drops the reply; the class event must not weaken that.
    late.resolve(page([{ id: 'd1', class_session_id: 'c-known' }]));
    await inFlight;
    expect(client.getQueryData(key('review-library', {}))).toBeUndefined();
  });
  it('an unreadable 2xx deletion reply is treated as ambiguous', async () => {
    const { client, ledger } = setup();
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'k1' });
    await expect(submitScopeDeletion({ deleteCourse: () => Promise.reject(new ApiClientError('invalid_response')), deleteSemester: () => Promise.reject(new Error('unused')) }, ledger, { kind: 'course', id: 'k1', mode: 'cascade', destination: '', revision: 1, token: 't' })).rejects.toBeInstanceOf(ApiClientError);
    expect(client.getQueryData(key('class-session', 'c1'))).toBeUndefined();
  });
  it('a definitive refusal of the deletion changes nothing', async () => {
    const { client, ledger } = setup();
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'k1' });
    await expect(submitScopeDeletion({ deleteCourse: () => Promise.reject(httpError(409, 'PLAN_EXPIRED')), deleteSemester: () => Promise.reject(new Error('unused')) }, ledger, { kind: 'course', id: 'k1', mode: 'cascade', destination: '', revision: 1, token: 't' })).rejects.toBeInstanceOf(ApiClientError);
    expect(client.getQueryData(key('class-session', 'c1'))).toBeDefined();
  });
  it('replacing the data set forgets earlier revocations', async () => {
    const { client, ledger } = setup();
    client.setQueryData(key('course', 'k1'), { id: 'k1', semester_id: 's1' });
    await fetchInto(client, ['course', 'k1'], () => Promise.reject(httpError(404, 'NOT_FOUND')));
    client.setQueryData(key('course', 'k1'), { id: 'k1', semester_id: 's1' });
    expect(client.getQueryData(key('course', 'k1'))).toBeUndefined();
    ledger.reset();
    client.setQueryData(key('course', 'k1'), { id: 'k1', semester_id: 's1' });
    expect(client.getQueryData(key('course', 'k1'))).toBeDefined();
  });
});

describe('round-1 review regressions', () => {
  it('a parent link seen only in invalidated queries never becomes a tombstone', async () => {
    const { client, ledger, revoked } = setup();
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'kA', revision: 1 });
    await client.invalidateQueries({ queryKey: key('class-session', 'c1') }, { cancelRefetch: false, throwOnError: false });
    ledger.applyScopeReceipt({ kind: 'course', id: 'kA', mode: 'cascade' });
    expect(client.getQueryData(key('class-session', 'c1'))).toBeUndefined(); // dropped as unknown
    expect(ledger.isClassRevoked('c1')).toBe(false);
    expect(revoked).toEqual([]);
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'kB', revision: 2 });
    await fetchInto(client, ['class-session', 'c1'], () => Promise.resolve({ id: 'c1', course_id: 'kB', revision: 2 }));
    expect(client.getQueryData(key('class-session', 'c1'))).toBeDefined();
  });
  it('membership first seen in a late reply confirms and propagates the revocation', async () => {
    const { client, ledger, revoked } = setup();
    client.setQueryData(key('tasks', 'c1'), page([{ id: 't1' }]));
    const late = deferred<unknown>();
    const inFlight = fetchInto(client, ['class-sessions', null], () => late.promise);
    ledger.applyScopeReceipt({ kind: 'course', id: 'k1', mode: 'cascade' });
    expect(client.getQueryData(key('tasks', 'c1'))).toBeUndefined(); // unknown, dropped
    late.resolve(page([{ id: 'c1', course_id: 'k1' }, { id: 'c2', course_id: 'k2' }]));
    await inFlight;
    expect(ledger.isClassRevoked('c1')).toBe(true);
    expect(revoked).toEqual(['c1']);
    expect(ids(client, 'class-sessions', null)).toEqual(['c2']);
  });
  it('a late mutation reply cannot refill a projection a revocation dropped; a fresh read can', async () => {
    const { client, ledger } = setup();
    client.setQueryData(key('review-document', 'd1'), { id: 'd1', class_session_id: 'c-unknown', title: '旧' });
    ledger.applyScopeReceipt({ kind: 'course', id: 'k1', mode: 'cascade' });
    expect(client.getQueryData(key('review-document', 'd1'))).toBeUndefined();
    client.setQueryData(key('review-document', 'd1'), { id: 'd1', class_session_id: 'c-unknown', title: '旧', understood: true });
    expect(client.getQueryData(key('review-document', 'd1'))).toBeUndefined();
    await fetchInto(client, ['review-document', 'd1'], () => Promise.resolve({ id: 'd1', class_session_id: 'c-unknown', title: '新' }));
    expect(client.getQueryData(key('review-document', 'd1'))).toMatchObject({ title: '新' });
  });
  it('a detail denial of a resource already listed revokes it', async () => {
    const { client, revoked } = setup('c1');
    client.setQueryData(key('class-sessions', null), page([{ id: 'c1', course_id: 'k1' }]));
    client.setQueryData(key('review-library', {}), page([{ id: 'd1', class_session_id: 'c1' }]));
    await fetchInto(client, ['class-session', 'c1'], () => Promise.reject(httpError(410, 'GONE')));
    expect(revoked).toEqual(['c1']);
    expect(ids(client, 'review-library', {})).toEqual([]);
  });
  it('a denied query without data keeps its denial when an offline refetch fails', async () => {
    const { client } = setup();
    client.setQueryData(key('class-sessions', null), page([{ id: 'c1', course_id: 'k1' }]));
    await fetchInto(client, ['class-session', 'c1'], () => Promise.reject(httpError(410, 'GONE')));
    await fetchInto(client, ['class-session', 'c1'], () => Promise.reject(networkError()));
    expect((client.getQueryState(key('class-session', 'c1'))?.error as ApiClientError).code).toBe('GONE');
  });
  it('a 401 retires the whole account epoch', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let retired = 0;
    const ledger = createRevocationLedger({ client, accountKey: A, hooks: { onUnauthenticated: () => { retired++; } } });
    ledger.install();
    client.setQueryData(key('review-document', 'd1'), { id: 'd1', class_session_id: 'c1' });
    await fetchInto(client, ['review-document', 'd1'], () => Promise.reject(httpError(401, 'UNAUTHENTICATED')));
    await fetchInto(client, ['course', 'k1'], () => Promise.reject(httpError(401, 'UNAUTHENTICATED')));
    await Promise.resolve();
    expect(retired).toBe(1);
  });
  it('indeterminate HTTP failures and mismatched receipts are ambiguous; definitive refusals are not', async () => {
    for (const failure of [httpError(504, 'SERVICE_DISABLED'), httpError(409, 'RESULT_UNKNOWN')]) {
      const { client, ledger } = setup();
      client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'k1' });
      await expect(submitScopeDeletion({ deleteCourse: () => Promise.reject(failure), deleteSemester: () => Promise.reject(new Error('unused')) }, ledger, { kind: 'course', id: 'k1', mode: 'cascade', destination: '', revision: 1, token: 't' })).rejects.toBe(failure);
      expect(client.getQueryData(key('class-session', 'c1'))).toBeUndefined();
    }
    for (const reply of [{}, { scope_type: 'course', scope_id: 'other', state: 'access_revoked' }]) {
      const { client, ledger, revoked } = setup('c1');
      client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'k1' });
      await expect(submitScopeDeletion({ deleteCourse: () => Promise.resolve(reply as never), deleteSemester: () => Promise.reject(new Error('unused')) }, ledger, { kind: 'course', id: 'k1', mode: 'cascade', destination: '', revision: 1, token: 't' })).rejects.toBeInstanceOf(ApiClientError);
      expect(client.getQueryData(key('class-session', 'c1'))).toBeUndefined();
      expect(revoked).toEqual([]);
    }
  });
  it('an ambiguous deletion keeps a snapshot proven unaffected while cancelling its in-flight refresh', async () => {
    const { client, ledger } = setup();
    client.setQueryData(key('class-session', 'c2'), { id: 'c2', course_id: 'k2' });
    client.setQueryData(key('tasks', 'c2'), page([{ id: 't2' }]));
    const late = deferred<unknown>();
    const inFlight = fetchInto(client, ['tasks', 'c2'], () => late.promise);
    ledger.applyAmbiguousScopeDeletion({ kind: 'course', id: 'k1', mode: 'cascade' });
    late.resolve(page([{ id: 't-late' }]));
    await inFlight;
    expect(ids(client, 'tasks', 'c2')).toEqual(['t2']);
  });
});

describe('round-2 review regressions', () => {
  it('prepare reads the bound membership fresh: a classroom moved elsewhere on another device is not revoked', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    const revoked: string[] = [];
    let reads = 0;
    const api = { classSession: () => { reads++; return Promise.resolve({ id: 'c1', course_id: 'kB', revision: 2 }); } };
    const ledger = createRevocationLedger({ client, accountKey: A, api: api as never, hooks: { boundClassId: () => 'c1', onClassRevoked: id => revoked.push(id) } });
    ledger.install();
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'kA', revision: 1 });
    const prepared = await ledger.prepareScopeDeletion({ kind: 'course', id: 'kA' });
    ledger.applyScopeReceipt({ kind: 'course', id: 'kA', mode: 'cascade' }, prepared);
    expect(reads).toBe(1);
    expect(revoked).toEqual([]);
    expect(client.getQueryData(key('class-session', 'c1'))).toMatchObject({ course_id: 'kB' });
  });
  it('a failed fresh read leaves the bound classroom unknown: suspended, never tombstoned from the cached link', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    const revoked: string[] = [];
    const suspended: string[] = [];
    const ledger = createRevocationLedger({ client, accountKey: A, api: { classSession: () => Promise.reject(networkError()) } as never, hooks: { boundClassId: () => 'c1', onClassRevoked: id => revoked.push(id), onCaptureSuspended: id => suspended.push(id) } });
    ledger.install();
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'kA', revision: 1 });
    const prepared = await ledger.prepareScopeDeletion({ kind: 'course', id: 'kA' });
    ledger.applyScopeReceipt({ kind: 'course', id: 'kA', mode: 'cascade' }, prepared);
    client.setQueryData(key('tasks', 'c9'), page([{ id: 't9' }])); // any later arrival must not learn from the old link
    expect(ledger.isClassRevoked('c1')).toBe(false);
    expect(revoked).toEqual([]);
    expect(suspended).toEqual(['c1']);
    // A fresh detail read showing it in the deleted course confirms it.
    await fetchInto(client, ['class-session', 'c1'], () => Promise.resolve({ id: 'c1', course_id: 'kA', revision: 1 }));
    expect(ledger.isClassRevoked('c1')).toBe(true);
    expect(revoked).toEqual(['c1']);
  });
  it('an ambiguous deletion that cancels an unaffected refresh learns nothing and revokes nothing', async () => {
    const { client, ledger, revoked } = setup('c1');
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'k1' });
    client.setQueryData(key('class-session', 'c2'), { id: 'c2', course_id: 'k2' });
    client.setQueryData(key('tasks', 'c2'), page([{ id: 't2' }]));
    const late = deferred<unknown>();
    const inFlight = fetchInto(client, ['tasks', 'c2'], () => late.promise);
    ledger.applyAmbiguousScopeDeletion({ kind: 'course', id: 'k1', mode: 'cascade' });
    late.resolve(page([]));
    await inFlight;
    expect(ledger.isClassRevoked('c1')).toBe(false);
    expect(revoked).toEqual([]);
    expect(ids(client, 'tasks', 'c2')).toEqual(['t2']);
  });
  it('after an ambiguous deletion a late mutation reply cannot refill the dropped document', async () => {
    const { client, ledger } = setup();
    client.setQueryData(key('review-document', 'd1'), { id: 'd1', class_session_id: 'c1', title: '旧' });
    client.setQueryData(key('class-session', 'c1'), { id: 'c1', course_id: 'k1' });
    ledger.applyAmbiguousScopeDeletion({ kind: 'course', id: 'k1', mode: 'cascade' });
    expect(client.getQueryData(key('review-document', 'd1'))).toBeUndefined();
    client.setQueryData(key('review-document', 'd1'), { id: 'd1', class_session_id: 'c1', title: '旧', understood: true });
    expect(client.getQueryData(key('review-document', 'd1'))).toBeUndefined();
    await fetchInto(client, ['review-document', 'd1'], () => Promise.reject(networkError()));
    expect(client.getQueryData(key('review-document', 'd1'))).toBeUndefined();
  });
  it('courses learned late under a semester cascade confirm the classrooms that arrive after them', async () => {
    const { client, ledger, revoked } = setup('c1');
    const courseReply = deferred<unknown>();
    const classReply = deferred<unknown>();
    const coursesInFlight = fetchInto(client, ['courses', {}], () => courseReply.promise);
    const classesInFlight = fetchInto(client, ['class-sessions', null], () => classReply.promise);
    ledger.applyScopeReceipt({ kind: 'semester', id: 's1', mode: 'cascade' });
    courseReply.resolve(page([{ id: 'k1', semester_id: 's1' }, { id: 'k2', semester_id: 's2' }]));
    await coursesInFlight;
    expect(ids(client, 'courses', {})).toEqual(['k2']);
    classReply.resolve(page([{ id: 'c1', course_id: 'k1' }, { id: 'c2', course_id: 'k2' }]));
    await classesInFlight;
    expect(ledger.isClassRevoked('c1')).toBe(true);
    expect(revoked).toEqual(['c1']);
    expect(ids(client, 'class-sessions', null)).toEqual(['c2']);
  });
  it('a write refused with 401 retires the account epoch too', async () => {
    const client = new QueryClient();
    let retired = 0;
    const ledger = createRevocationLedger({ client, accountKey: A, hooks: { onUnauthenticated: () => { retired++; } } });
    ledger.install();
    await client.getMutationCache().build(client, { mutationFn: () => Promise.reject(httpError(401, 'UNAUTHENTICATED')) }).execute(undefined).catch(() => undefined);
    await Promise.resolve();
    expect(retired).toBe(1);
  });
  it('a successful read clears an earlier denial, so a later offline failure keeps the content readable', async () => {
    const { client } = setup();
    await fetchInto(client, ['class-session', 'demo'], () => Promise.reject(httpError(404, 'NOT_FOUND')));
    await fetchInto(client, ['class-session', 'demo'], () => Promise.resolve({ id: 'demo', course_id: 'k1' }));
    await fetchInto(client, ['class-session', 'demo'], () => Promise.reject(networkError()));
    expect((client.getQueryState(key('class-session', 'demo'))?.error as ApiClientError).kind).toBe('network');
    expect(client.getQueryData(key('class-session', 'demo'))).toBeDefined();
  });
  it('a first offline visit to a confirmed-revoked classroom reports it deleted, not unreachable', async () => {
    const { client, ledger } = setup();
    client.setQueryData(key('class-sessions', null), page([{ id: 'c1', course_id: 'k1' }]));
    ledger.applyScopeReceipt({ kind: 'course', id: 'k1', mode: 'cascade' });
    expect(ledger.isClassRevoked('c1')).toBe(true);
    await fetchInto(client, ['class-session', 'c1'], () => Promise.reject(networkError()));
    await fetchInto(client, ['marks', 'c1'], () => Promise.reject(networkError()));
    expect((client.getQueryState(key('class-session', 'c1'))?.error as ApiClientError).code).toBe('GONE');
    expect((client.getQueryState(key('marks', 'c1'))?.error as ApiClientError).code).toBe('GONE');
  });
});
