import type { components } from '../generated/openapi';
import type { Feedback } from '../domain/feedback';
import { orderNotifications, type Notification } from '../domain/notifications';
import { normalizeQuery, searchResultKey, type SearchResult } from '../domain/search';

type ErrorCode = components['schemas']['Error']['error']['code'];
type Reply = { status: number; body: unknown; headers: { 'Cache-Control': 'no-store' } };
type Hit = { courseId: string; result: SearchResult };
type MockNotice = { item: Notification; sourceIds: string[] };
type Account = {
  hits: Hit[];
  revoked: Set<string>;
  notices: Map<string, MockNotice>;
  feedback: Map<string, Feedback>;
  feedbackTimes: number[];
  receipts: Map<string, { fingerprint: string; identity: string }>;
};
type MockRequest = {
  ownerId: string | null; method: string; path: string; query?: URLSearchParams;
  body?: unknown; key?: string | null; csrfVerified?: boolean;
};
type MockCursor = { scope: string; anchor: string };

const synthetic = (id: number) => `c1100000-111c-4000-8000-${id.toString(16).padStart(12, '0')}`;
export const searchFixture = {
  a: { owner: synthetic(1), course: synthetic(11), classroom: synthetic(12), document: synthetic(13) },
  b: { owner: synthetic(2), course: synthetic(21), classroom: synthetic(22), document: synthetic(23) },
};
const stamp = '2026-11-23T00:00:00Z';
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const opId = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const categories = new Set(['bug', 'asr', 'ai_content', 'ui', 'sync', 'security', 'other']);
const response = (status: number, body: unknown): Reply => ({ status, body, headers: { 'Cache-Control': 'no-store' } });
const error = (status: number, code: ErrorCode) => response(status, { error: {
  code, message: 'Request could not be completed.', request_id: crypto.randomUUID(), retryable: status === 429,
  retry_after_seconds: null, current_revision: null, conflict_id: null, task_id: null,
} });

export function seedSearchBackend() {
  const accounts = new Map<string, Account>();
  for (const [side, fixture] of Object.entries(searchFixture)) {
    const revision = synthetic(side === 'a' ? 14 : 24);
    const hits: SearchResult[] = [
      { object_type: 'course', object_id: fixture.course, class_session_id: null, title: '合成课程',
        snippet: '', revision_id: null, locator: `course/${fixture.course}` },
      { object_type: 'class_session', object_id: fixture.classroom, class_session_id: fixture.classroom,
        title: '合成课堂', snippet: '', revision_id: null, locator: `classroom/${fixture.classroom}` },
      { object_type: 'review_document', object_id: fixture.document, class_session_id: fixture.classroom,
        title: '合成笔记', snippet: side === 'a' ? '合成甲账号专用片段' : '合成乙账号专用片段',
        revision_id: revision, locator: `review/${fixture.document}/${revision}` },
    ];
    accounts.set(fixture.owner, { hits: hits.map(result => ({ courseId: fixture.course, result })),
      revoked: new Set(), notices: new Map(), feedback: new Map(), feedbackTimes: [], receipts: new Map() });
  }
  return { accounts, cursors: new Map<string, MockCursor>() };
}

type Store = ReturnType<typeof seedSearchBackend>;

function liveHits(account: Account) {
  return account.hits.filter(({ courseId, result }) => !account.revoked.has(courseId)
    && !account.revoked.has(result.object_id)
    && !(result.class_session_id && account.revoked.has(result.class_session_id)));
}

export function revokeSearchFixture(store: Store, ownerId: string, objectId: string) {
  store.accounts.get(ownerId)?.revoked.add(objectId);
}

export function recordMockNotification(store: Store, ownerId: string, dedupeKey: string,
                                       values: Pick<Notification, 'kind' | 'severity' | 'object_id' | 'message_code'>,
                                       source?: { semester_id?: string; course_id?: string; class_session_id?: string }) {
  const account = store.accounts.get(ownerId);
  if (!account) return null;
  const prior = account.notices.get(dedupeKey);
  if (prior) return prior.item;
  const target = account.hits.find(hit => hit.result.object_id === values.object_id);
  const classroom = source?.class_session_id ?? target?.result.class_session_id;
  const classroomHit = account.hits.find(hit => hit.result.object_type === 'class_session' && hit.result.object_id === classroom);
  const sourceIds = [ownerId, values.object_id, classroom, target?.courseId, classroomHit?.courseId,
    source?.course_id, source?.semester_id]
    .filter((id): id is string => !!id);
  const item = { ...values, id: crypto.randomUUID(), created_at: stamp, read_at: null, resolved_at: null };
  account.notices.set(dedupeKey, { item, sourceIds });
  return item;
}

export function resolveMockNotification(store: Store, ownerId: string, dedupeKey: string) {
  const item = store.accounts.get(ownerId)?.notices.get(dedupeKey)?.item;
  if (item) item.resolved_at ??= stamp;
}

function parseFeedback(value: unknown): Feedback | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const body = value as Feedback;
  const fields = ['category', 'build_id', 'description', 'request_id', 'confirmed_excerpt', 'confirm_send_excerpt'];
  if (Object.keys(body).length !== fields.length || fields.some(field => !Object.hasOwn(body, field))) return null;
  if (!categories.has(body.category) || typeof body.build_id !== 'string' || [...body.build_id].length > 100
      || typeof body.description !== 'string' || !body.description.trim() || [...body.description].length > 5000
      || (body.request_id !== null && (typeof body.request_id !== 'string' || !uuid.test(body.request_id)))
      || (body.confirmed_excerpt !== null && (typeof body.confirmed_excerpt !== 'string' || [...body.confirmed_excerpt].length > 5000))
      || typeof body.confirm_send_excerpt !== 'boolean') return null;
  return body;
}

function containsNul(body: Feedback): boolean {
  return [body.description, body.build_id, body.confirmed_excerpt].some(text => text?.includes('\u0000'));
}

function page<T>(store: Store, query: URLSearchParams, binding: unknown[], items: T[],
                 anchorOf: (item: T) => string, after: (a: string, b: string) => boolean) {
  const size = query.get('limit') ?? '30';
  if (!/^[0-9]{1,3}$/.test(size) || Number(size) < 1 || Number(size) > 100) return error(422, 'VALIDATION_FAILED');
  const limit = Number(size);
  const scope = JSON.stringify([...binding, limit]);
  const cursor = query.get('cursor');
  if (cursor !== null) {
    const lookup = store.cursors.get(cursor);
    if (!lookup || lookup.scope !== scope) return error(422, 'VALIDATION_FAILED');
    items = items.filter(item => after(anchorOf(item), lookup.anchor));
  }
  let next_cursor = null;
  if (items.length > limit) {
    next_cursor = crypto.randomUUID();
    store.cursors.set(next_cursor, { scope, anchor: anchorOf(items[limit - 1]) });
    if (store.cursors.size > 1000) store.cursors.delete(store.cursors.keys().next().value!);
  }
  return response(200, { items: items.slice(0, limit), next_cursor });
}

export function handleSearchBackend(store: Store, input: MockRequest): Reply | null {
  const { method, path, ownerId, key } = input;
  const search = method === 'GET' && path === '/search';
  const list = method === 'GET' && path === '/notifications';
  const feedback = method === 'POST' && path === '/feedback';
  const mark = method === 'POST' ? /^\/notifications\/([^/]+)\/read$/.exec(path) : null;
  if (!search && !list && !feedback && !mark) return null;
  const account = ownerId ? store.accounts.get(ownerId) : undefined;
  if (!ownerId || !account || account.revoked.has(ownerId)) return error(401, 'UNAUTHENTICATED');
  const query = input.query ?? new URLSearchParams();
  const allowed = search ? ['cursor', 'limit', 'q', 'course_id', 'class_session_id'] : list ? ['cursor', 'limit'] : [];
  if ([...query.keys()].some(name => !allowed.includes(name) || query.getAll(name).length !== 1)) {
    return error(422, 'VALIDATION_FAILED');
  }
  if (search) {
    const keyword = normalizeQuery(query.get('q') ?? '');
    const course = query.get('course_id');
    const classroom = query.get('class_session_id');
    if (!keyword || [course, classroom].some(id => id !== null && !uuid.test(id))) return error(422, 'VALIDATION_FAILED');
    const hits = liveHits(account);
    if ((course && !hits.some(hit => hit.result.object_type === 'course' && hit.result.object_id === course))
        || (classroom && !hits.some(hit => hit.result.object_type === 'class_session' && hit.result.object_id === classroom))) {
      return error(404, 'NOT_FOUND');
    }
    const selected = hits.filter(({ courseId, result }) => (!course || courseId === course)
      && (!classroom || result.class_session_id === classroom || (result.object_type === 'course'
        && hits.some(hit => hit.courseId === courseId && hit.result.class_session_id === classroom)))
      && [result.title, result.snippet].some(value => value.toLowerCase().includes(keyword.toLowerCase())))
      .map(hit => hit.result).sort((a, b) => searchResultKey(a).localeCompare(searchResultKey(b)));
    return page(store, query, [ownerId, 'search', keyword, course, classroom], selected, searchResultKey, (a, b) => a.localeCompare(b) > 0);
  }
  const live = [...account.notices.values()].filter(notice => !notice.sourceIds.some(id => account.revoked.has(id)))
    .map(notice => notice.item);
  if (list) return page(store, query, [ownerId, 'notifications'], orderNotifications(live),
    item => `${item.created_at}|${item.id}`, (a, b) => a < b);
  if (!input.csrfVerified) return error(403, 'FORBIDDEN');
  if (!key || !opId.test(key)) return error(422, 'VALIDATION_FAILED');
  if (mark) {
    const body = input.body;
    if (!uuid.test(mark[1]) || !body || typeof body !== 'object' || Array.isArray(body)
        || Object.keys(body).length !== 1 || !Object.hasOwn(body, 'read') || (body as { read?: unknown }).read !== true) {
      return error(422, 'VALIDATION_FAILED');
    }
    const id = mark[1];
    const prior = account.receipts.get(`read:${key}`);
    if (prior && prior.identity !== id) return error(409, 'IDEMPOTENCY_MISMATCH');
    const item = live.find(notice => notice.id === id);
    if (!item) return error(404, 'NOT_FOUND');
    item.read_at ??= stamp;
    account.receipts.set(`read:${key}`, { fingerprint: id, identity: id });
    return response(200, { ...item });
  }
  const body = parseFeedback(input.body);
  if (body === null) return error(422, 'VALIDATION_FAILED');
  // Same precedence as OperationsService.dispatch: consent first, then NUL in stored text.
  if (body.confirmed_excerpt && !body.confirm_send_excerpt) return error(422, 'CONSENT_REQUIRED');
  if (containsNul(body)) return error(422, 'VALIDATION_FAILED');
  const fingerprint = JSON.stringify(Object.entries(body).sort(([a], [b]) => a.localeCompare(b)));
  const prior = account.receipts.get(`feedback:${key}`);
  if (prior) return prior.fingerprint === fingerprint ? response(202, { accepted: true, request_id: prior.identity })
    : error(409, 'IDEMPOTENCY_MISMATCH');
  const now = Date.now();
  account.feedbackTimes = account.feedbackTimes.filter(time => time > now - 10 * 60_000);
  if (account.feedbackTimes.length >= 10) return error(429, 'RATE_LIMITED');
  const id = crypto.randomUUID();
  account.feedback.set(id, { ...body, confirmed_excerpt: body.confirm_send_excerpt ? body.confirmed_excerpt || null : null });
  account.receipts.set(`feedback:${key}`, { fingerprint, identity: id });
  account.feedbackTimes.push(now);
  return response(202, { accepted: true, request_id: id });
}
