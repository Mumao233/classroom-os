import type { MutationCacheNotifyEvent, Query, QueryCacheNotifyEvent, QueryClient } from '@tanstack/react-query';
import { ApiClientError, type ApiClient } from '../api/client';

/**
 * Account-shell revocation ledger (design: web/frontend/REVOCATION-DESIGN.md).
 *
 * A deletion receipt, a deletion event or an authoritative denial revokes content at once in every
 * cached projection of this account epoch, without waiting for a refetch that fails offline. One
 * ledger exists per account epoch and is dropped together with its QueryClient.
 */

export type ScopeKind = 'course' | 'semester';
export type DeleteMode = 'cascade' | 'move_children';
/** A submitted scope deletion; `destination` is where `move_children` moved the children. */
export type ScopeDeletion = { kind: ScopeKind; id: string; mode: DeleteMode; destination?: string };
/** What the pre-submit check established for this one deletion request. */
export type PreparedDeletion = { exempt: string | null };

export type RevocationHooks = {
  /** The classroom the account-level capture is bound to (active or stopped here), if any. */
  boundClassId?: () => string | null;
  /** A classroom is revoked (confirmed): stop a capture bound to it and drop its unconfirmed commands. */
  onClassRevoked?: (classId: string) => void;
  /**
   * A bound capture may belong to a deleted scope but that is not confirmed: stop it through the normal
   * (retryable) stop path, without a deletion event; it is revoked only once confirmed.
   */
  onCaptureSuspended?: (classId: string) => void;
  /** The server no longer authenticates this session: retire the whole account epoch. */
  onUnauthenticated?: () => void;
};

export type RevocationLedger = {
  /** Before submitting a scope deletion: resolve the bound classroom's course and semester. */
  prepareScopeDeletion(scope: { kind: ScopeKind; id: string }): Promise<PreparedDeletion>;
  /** The server confirmed a scope deletion with the submitted kind, id, mode and destination. */
  applyScopeReceipt(scope: ScopeDeletion, prepared?: PreparedDeletion): void;
  /** A scope deletion's outcome is unknown (its reply was lost or unreadable): drop what it may have removed, revoke nothing. */
  applyAmbiguousScopeDeletion(scope: ScopeDeletion): void;
  revokeClass(classId: string): void;
  revokeAudio(classId: string, captureEpochId: string): void;
  isClassRevoked(classId: string): boolean;
  /** The data set behind this account was replaced wholesale (Mock fixtures): forget every revocation. */
  reset(): void;
  /** Subscribe to the query cache (late responses, reverts, refetch denials). Returns the unsubscribe. */
  install(): () => void;
};

/** What the deletion dialog submitted; the receipt is applied with exactly these values. */
export type ScopeDeletionRequest = { kind: ScopeKind; id: string; mode: DeleteMode; destination: string; revision: number; token: string };

/**
 * Send a course or semester deletion and apply its receipt inside the request itself, with the
 * submitted scope, mode and destination (never the dialog's state at reply time, mounted or not).
 */
export async function submitScopeDeletion(api: Pick<ApiClient, 'deleteCourse' | 'deleteSemester'>, revocation: RevocationLedger | undefined, submitted: ScopeDeletionRequest) {
  const scope: ScopeDeletion = { kind: submitted.kind, id: submitted.id, mode: submitted.mode, ...(submitted.mode === 'move_children' ? { destination: submitted.destination } : {}) };
  const prepared = await revocation?.prepareScopeDeletion(scope);
  const body = { base_revision: submitted.revision, mode: submitted.mode, confirmation_token: submitted.token, ...(submitted.mode === 'move_children' ? { destination_id: submitted.destination } : {}) };
  let receipt: unknown;
  try {
    receipt = await (submitted.kind === 'course' ? api.deleteCourse(submitted.id, body) : api.deleteSemester(submitted.id, body));
  } catch (error) {
    // The server may have deleted it although its reply was lost, unreadable or indeterminate: hide what it
    // may have removed until a read says otherwise. A definitive refusal changes nothing.
    if (isIndeterminate(error)) revocation?.applyAmbiguousScopeDeletion(scope);
    throw error;
  }
  // Only a receipt for exactly the submitted scope confirms the deletion; anything else is an unknown outcome.
  const value = receipt as { scope_type?: unknown; scope_id?: unknown; state?: unknown } | null;
  const states = ['access_revoked', 'pending_delete', 'delete_failed', 'deleted'];
  if (!value || typeof value !== 'object' || value.scope_type !== submitted.kind || value.scope_id !== submitted.id || !states.includes(String(value.state))) {
    revocation?.applyAmbiguousScopeDeletion(scope);
    throw new ApiClientError('invalid_response');
  }
  revocation?.applyScopeReceipt(scope, prepared);
  return receipt;
}

/** A failed write whose effect on the server is unknown (not a definitive refusal, not a retired account epoch). */
export function isIndeterminate(error: unknown): boolean {
  if (!(error instanceof ApiClientError)) return false;
  if (error.kind === 'network' || error.kind === 'invalid_response') return true;
  if (error.kind !== 'http') return false;
  return error.code === 'RESULT_UNKNOWN' || (error.status !== undefined && (error.status >= 500 || error.status === 408));
}

type Tri = boolean | 'unknown';
/**
 * What the revocation being applied right now introduces. Only memberships it could affect count as
 * "unknown" (and are dropped); earlier revocations were already applied by their own sweeps.
 * `exempt`: a bound classroom the server reported as nonexistent just before this deletion.
 */
type Strict = { cascade: boolean; semesterCascade: boolean; classes: boolean; exempt: string | null } | null;
type Verdict = { type: 'keep' } | { type: 'gone' } | { type: 'unknown' } | { type: 'filter'; data: unknown };
const KEEP: Verdict = { type: 'keep' };
const GONE: Verdict = { type: 'gone' };
const UNKNOWN: Verdict = { type: 'unknown' };
const tri = (value: Tri): Verdict => value === true ? GONE : value === 'unknown' ? UNKNOWN : KEEP;
const mergeStrict = (a: NonNullable<Strict>, b: NonNullable<Strict>): NonNullable<Strict> => ({
  cascade: a.cascade || b.cascade, semesterCascade: a.semesterCascade || b.semesterCascade, classes: a.classes || b.classes, exempt: a.exempt === b.exempt ? a.exempt : null,
});

/** Queries whose fourth key part is a classroom ID and whose whole content belongs to it. */
const CLASS_KEYED = new Set(['audio-manifests', 'marks', 'slides', 'tasks', 'questions', 'transcript', 'transcript-versions', 'class-segments']);
/** Every query kind the ledger judges (the rest are account-level and never revoked by content deletions). */
const REVOCABLE = new Set([...CLASS_KEYED, 'semesters', 'courses', 'course', 'materials', 'class-sessions', 'class-session', 'review-library', 'review-documents', 'review-document', 'review-revisions', 'source-snapshot', 'transcript-segments']);
/** Single resources whose earlier successful read makes a later denial evidence of revocation. */
const SERVED_KINDS = new Set(['class-session', 'course', 'review-document']);
const DENIAL_CODES = new Set(['NOT_FOUND', 'GONE', 'FORBIDDEN']);

/** The server refused the resource itself (not a transport failure): cached content must not stay visible. */
export function isAuthoritativeDenial(error: unknown): boolean {
  if (!(error instanceof ApiClientError) || error.kind !== 'http') return false;
  return error.code ? DENIAL_CODES.has(error.code) : error.status === 403 || error.status === 404 || error.status === 410;
}
/** The session is no longer authenticated: nothing private may stay on screen from this account. */
export function isUnauthenticated(error: unknown): boolean {
  return error instanceof ApiClientError && error.kind === 'http' && (error.code === 'UNAUTHENTICATED' || error.status === 401);
}

/** The synthetic error a revoked projection shows (same code the server returns for deleted content). */
export function revokedError() {
  return new ApiClientError('http', 410, { error: { code: 'GONE', message: '内容已被删除', request_id: 'local-revocation', retryable: false, retry_after_seconds: null, current_revision: null, conflict_id: null, task_id: null } });
}

/**
 * One parent link per child; the newest entity revision wins, equal revisions that disagree are unknown,
 * and a link seen only in invalidated (not yet re-read) queries is unknown too: never proof for a tombstone.
 */
type Link = { parent: string; revision: number; conflict: boolean; stale: boolean };
type Links = Map<string, Link>;
type Membership = { classCourse: Links; courseSemester: Links; docClass: Links; versionClass: Links };
const record = (value: unknown): Record<string, unknown> | null => typeof value === 'object' && value !== null ? value as Record<string, unknown> : null;
const str = (value: unknown): string | undefined => typeof value === 'string' && value ? value : undefined;
const items = (data: unknown): unknown[] | null => { const items = record(data)?.items; return Array.isArray(items) ? items : null; };

function link(map: Links, id: string, parent: string, revision: number, stale: boolean) {
  const old = map.get(id);
  if (!old || revision > old.revision) map.set(id, { parent, revision, conflict: false, stale });
  else if (revision === old.revision) { if (parent !== old.parent) old.conflict = true; else old.stale = old.stale && stale; }
}
const parentOf = (map: Links, id: string): string | undefined => { const value = map.get(id); return value && !value.conflict && !value.stale ? value.parent : undefined; };

function readMembership(client: QueryClient): Membership {
  const m: Membership = { classCourse: new Map(), courseSemester: new Map(), docClass: new Map(), versionClass: new Map() };
  let stale = false;
  const add = (map: Links, field: string) => (value: unknown) => {
    const v = record(value); const id = str(v?.id); const parent = str(v?.[field]);
    if (id && parent) link(map, id, parent, typeof v?.revision === 'number' ? v.revision : 0, stale);
  };
  const course = add(m.courseSemester, 'semester_id');
  const session = add(m.classCourse, 'course_id');
  const doc = add(m.docClass, 'class_session_id');
  const version = add(m.versionClass, 'class_session_id');
  for (const query of client.getQueryCache().getAll()) {
    const data = query.state.data;
    if (data === undefined || query.queryKey[0] !== 'account') continue;
    stale = query.state.isInvalidated;
    switch (query.queryKey[2]) {
      case 'courses': items(data)?.forEach(course); break;
      case 'course': course(data); break;
      case 'class-sessions': items(data)?.forEach(session); break;
      case 'class-session': session(data); break;
      case 'review-library': case 'review-documents': items(data)?.forEach(doc); break;
      case 'review-document': doc(data); break;
      case 'transcript-versions': items(data)?.forEach(version); break;
    }
  }
  return m;
}

export function createRevocationLedger({ client, accountKey, api, hooks = {} }: { client: QueryClient; accountKey: string; api?: ApiClient; hooks?: RevocationHooks }): RevocationLedger {
  const semesters = new Map<string, 'cascade' | 'self'>();
  const courses = new Map<string, 'cascade' | 'self'>();
  const classes = new Set<string>();
  const documents = new Set<string>();
  const versions = new Set<string>();
  const audio = new Map<string, Set<string>>();
  const notified = new Set<string>();
  /** Children moved by a confirmed `move_children`: old parent → new parent (classroom courses, course semesters). */
  const movedCourse = new Map<string, string>();
  const movedSemester = new Map<string, string>();
  /** Resources the server served in this account epoch (a later denial of them is evidence, even without cached data). */
  const served = new Set<string>();
  /**
   * Bound classrooms whose fresh membership read failed before a deletion: their cached parent links may
   * predate a move, so they are never proof (unknown until a fresh detail read succeeds).
   */
  const unverified = new Set<string>();
  /** Requests in flight during a new revocation started before it: their replies are judged strictly once. */
  let strictOnArrival = new WeakMap<Query, NonNullable<Strict>>();
  /** Projections shown as revoked or denied: a later transport failure (offline refetch) must not relabel them as merely unreachable. */
  let revokedQueries = new WeakMap<Query, unknown>();
  /** Projections a revocation dropped as unknown: only a fresh read may fill them again, not a late mutation reply. */
  let droppedQueries = new WeakSet<Query>();
  let unauthenticated = false;
  /** True while a tentative (unknown-outcome) sweep runs: cache events it causes must not learn from its temporary scope. */
  let tentativeActive = false;

  const hasSemesterCascade = () => [...semesters.values()].includes('cascade');
  const hasCascade = () => hasSemesterCascade() || [...courses.values()].includes('cascade');
  const follow = (moves: Map<string, string>, id: string) => { let current = id; for (let hops = 0; hops < 32 && moves.has(current); hops++) current = moves.get(current)!; return current; };
  const isEmpty = () => !semesters.size && !courses.size && !classes.size && !documents.size && !versions.size && !audio.size;

  // `strict` (applying a new revocation): a membership it could affect that cannot be established counts as affected.
  const semesterOf = (courseId: string, m: Membership, strict: Strict, hint?: string): Tri => {
    if (!hasSemesterCascade()) return false;
    // The newest cached revision decides (an entry's own field may be a stale copy); conflicts are unknown.
    const cached = m.courseSemester.has(courseId) ? parentOf(m.courseSemester, courseId) : hint;
    if (cached === undefined) return strict?.semesterCascade ? 'unknown' : false;
    return semesters.get(follow(movedSemester, cached)) === 'cascade';
  };
  /** The course object itself is gone (deleted directly, either mode, or inside a cascaded semester). */
  const courseGone = (courseId: string, m: Membership, strict: Strict, semesterHint?: string): Tri => courses.has(courseId) ? true : semesterOf(courseId, m, strict, semesterHint);
  /** The course's classrooms are revoked (cascade only; moved children survive). */
  const courseCascaded = (courseId: string, m: Membership, strict: Strict): Tri => courses.get(courseId) === 'cascade' ? true : courses.has(courseId) ? false : semesterOf(courseId, m, strict);
  const classGone = (classId: string, m: Membership, strict: Strict, courseHint?: string): Tri => {
    if (classes.has(classId)) return true;
    if (!hasCascade()) return false;
    if (unverified.has(classId)) return strict?.cascade && strict.exempt !== classId ? 'unknown' : false;
    const cached = m.classCourse.has(classId) ? parentOf(m.classCourse, classId) : courseHint;
    if (cached === undefined) return strict?.cascade && strict.exempt !== classId ? 'unknown' : false;
    return courseCascaded(follow(movedCourse, cached), m, strict);
  };
  const viaClass = (classId: string | undefined, m: Membership, strict: Strict): Tri => {
    if (classId !== undefined) return classGone(classId, m, strict);
    return strict?.classes ? 'unknown' : false;
  };
  const docGone = (docId: string, m: Membership, strict: Strict, classHint?: string): Tri => documents.has(docId) ? true : viaClass(m.docClass.has(docId) ? parentOf(m.docClass, docId) : classHint, m, strict);

  /** Filter a paged list; an item whose membership is unknown makes the whole list untrustworthy. */
  const filterList = (data: unknown, gone: (item: Record<string, unknown>) => Tri): Verdict => {
    const list = items(data);
    if (!list) return KEEP;
    let unknown = false;
    const kept = list.filter(item => { const v = record(item); if (!v) return true; const result = gone(v); if (result === 'unknown') unknown = true; return result !== true; });
    if (unknown) return UNKNOWN;
    return kept.length === list.length ? KEEP : { type: 'filter', data: { ...record(data), items: kept } };
  };

  const judge = (query: Query, m: Membership, strict: Strict): Verdict => {
    const key = query.queryKey;
    if (key[0] !== 'account') return KEEP;
    const kind = key[2];
    const arg = key[3];
    const data = query.state.data;
    const value = record(data);
    if (typeof kind !== 'string') return KEEP;
    if (CLASS_KEYED.has(kind) && typeof arg === 'string') {
      const gone = classGone(arg, m, strict);
      if (gone !== false || kind !== 'audio-manifests') return tri(gone);
      // A manifest traversal may never silently skip an epoch: one revoked epoch makes the whole projection unavailable.
      const epochs = audio.get(arg);
      return epochs && items(data)?.some(item => epochs.has(String(record(item)?.capture_epoch_id))) ? GONE : KEEP;
    }
    switch (kind) {
      case 'semesters': return filterList(data, item => semesters.has(String(item.id)));
      case 'courses': return filterList(data, item => courseGone(String(item.id), m, strict, str(item.semester_id)));
      case 'course': return typeof arg === 'string' && arg ? tri(courseGone(arg, m, strict, str(value?.semester_id))) : KEEP;
      case 'materials': return typeof arg === 'string' ? tri(courseGone(arg, m, strict)) : KEEP;
      case 'class-sessions': return filterList(data, item => classGone(String(item.id), m, strict, str(item.course_id)));
      case 'class-session': return typeof arg === 'string' && arg ? tri(classGone(arg, m, strict, str(value?.course_id))) : KEEP;
      case 'review-library': case 'review-documents': {
        const filter = record(arg);
        const courseId = str(filter?.course_id);
        const classId = str(filter?.class_session_id);
        // A list filtered to a revoked course or classroom has nothing left to show.
        if ((courseId && courseCascaded(courseId, m, strict) === true) || (classId && classGone(classId, m, strict) === true)) return filterList(data, () => true);
        return filterList(data, item => docGone(String(item.id), m, strict, str(item.class_session_id)));
      }
      case 'review-document': return typeof arg === 'string' ? tri(docGone(arg, m, strict, str(value?.class_session_id))) : KEEP;
      case 'review-revisions': return typeof arg === 'string' ? tri(docGone(arg, m, strict)) : KEEP;
      case 'source-snapshot': return value ? tri(viaClass(str(value.class_session_id), m, strict)) : KEEP;
      case 'transcript-segments': return typeof arg === 'string' ? versions.has(arg) ? GONE : tri(viaClass(parentOf(m.versionClass, arg), m, strict)) : KEEP;
      default: return KEEP;
    }
  };

  const reset = (query: Query) => {
    // Resetting cancels any old request; what is read afterwards postdates the revocation.
    strictOnArrival.delete(query);
    droppedQueries.add(query);
    void client.resetQueries({ queryKey: query.queryKey, exact: true });
  };

  /**
   * `gone`: content known to be revoked becomes an error state (no data, no automatic refetch loop).
   * `unknown` (and everything in a tentative sweep): the query is reset; online it is read again,
   * offline it shows a connection error instead of the old content.
   */
  const apply = (query: Query, verdict: Verdict, tentative: boolean) => {
    if (verdict.type === 'keep') return;
    if (verdict.type === 'filter' && !tentative) {
      // A reply still in flight lands later and is judged again (strictly, if it predates this revocation).
      query.setState({ data: verdict.data });
      return;
    }
    if (verdict.type === 'gone' && !tentative) {
      if (query.state.data === undefined) return;
      if (query.state.fetchStatus === 'fetching') void query.cancel({ revert: true });
      query.setState({ data: undefined, dataUpdatedAt: 0, status: 'error', error: revokedError(), errorUpdatedAt: Date.now(), fetchStatus: 'idle', isInvalidated: false });
      revokedQueries.set(query, query.state.error);
      return;
    }
    if (query.state.data === undefined && query.state.fetchStatus !== 'fetching') return;
    reset(query);
  };

  const notifyClass = (classId: string) => {
    if (notified.has(classId)) return;
    notified.add(classId);
    hooks.onClassRevoked?.(classId);
  };

  /**
   * Remember every confirmed revoked ID by itself: once filtered out of the cache its membership is no
   * longer readable, and a late or repeated reply must still be recognised. Returns whether anything new
   * was learned.
   */
  const learn = (m: Membership) => {
    const before = courses.size + classes.size + documents.size + versions.size;
    // Courses inside a cascaded semester first: their classrooms are judged through them below.
    if (hasSemesterCascade()) for (const id of m.courseSemester.keys()) { const parent = parentOf(m.courseSemester, id); if (parent !== undefined && semesters.get(follow(movedSemester, parent)) === 'cascade' && courses.get(id) !== 'cascade') courses.set(id, 'cascade'); }
    for (const id of m.classCourse.keys()) if (classGone(id, m, null) === true) classes.add(id);
    for (const id of m.docClass.keys()) if (docGone(id, m, null) === true) documents.add(id);
    for (const id of m.versionClass.keys()) { const parent = parentOf(m.versionClass, id); if (parent !== undefined && classGone(parent, m, null) === true) versions.add(id); }
    return courses.size + classes.size + documents.size + versions.size !== before;
  };

  /** Sweep every cached projection of this account; then tell the shell which classrooms are revoked. */
  const sweep = ({ strict, tentative = false }: { strict: Strict; tentative?: boolean }) => {
    const m = readMembership(client);
    if (!tentative) learn(m);
    for (const query of client.getQueryCache().getAll()) {
      const kind = query.queryKey[2];
      if (query.queryKey[0] === 'account' && typeof kind === 'string' && REVOCABLE.has(kind) && query.state.fetchStatus === 'fetching') {
        if (tentative && strict) {
          // A read started before an unknown outcome must not land. A snapshot proven unaffected stays
          // (the read is cancelled back to it); anything else is dropped.
          const verdict = judge(query, m, strict);
          if (verdict.type === 'keep' && query.state.data !== undefined) { void query.cancel({ revert: true }); continue; }
          reset(query);
          continue;
        }
        if (strict) { const previous = strictOnArrival.get(query); strictOnArrival.set(query, previous ? mergeStrict(previous, strict) : strict); }
      }
      apply(query, judge(query, m, strict), tentative);
    }
    if (tentative) return;
    for (const classId of classes) notifyClass(classId);
    // A bound capture whose classroom cannot be placed outside the newly deleted scope stops capturing
    // locally; it is revoked only once its membership or a denial confirms it.
    const bound = hooks.boundClassId?.() ?? null;
    if (bound && strict?.cascade && classGone(bound, m, strict) === 'unknown') hooks.onCaptureSuspended?.(bound);
  };
  const scopeStrict = (scope: ScopeDeletion, prepared?: PreparedDeletion): Strict => scope.mode === 'cascade' ? { cascade: true, semesterCascade: scope.kind === 'semester', classes: true, exempt: prepared?.exempt ?? null } : null;

  const recordScope = (scope: ScopeDeletion) => {
    const map = scope.kind === 'course' ? courses : semesters;
    if (map.get(scope.id) !== 'cascade') map.set(scope.id, scope.mode === 'cascade' ? 'cascade' : 'self');
    if (scope.mode === 'move_children' && scope.destination && scope.destination !== scope.id) (scope.kind === 'course' ? movedCourse : movedSemester).set(scope.id, scope.destination);
  };

  // In a semester cascade, record every course known to belong to it, so later data is judged without
  // needing the course → semester link again.
  const expandSemester = (semesterId: string) => {
    const m = readMembership(client);
    for (const courseId of m.courseSemester.keys()) { const parent = parentOf(m.courseSemester, courseId); if (parent !== undefined && follow(movedSemester, parent) === semesterId && courses.get(courseId) !== 'cascade') courses.set(courseId, 'cascade'); }
  };

  const onCacheEvent = (event: QueryCacheNotifyEvent) => {
    if (event.type !== 'updated' && event.type !== 'added') return;
    if (tentativeActive) return;
    const query = event.query;
    if (query.queryKey[0] !== 'account') return;
    const state = query.state;
    const kind = query.queryKey[2];
    const id = query.queryKey[3];
    const resourceKey = typeof kind === 'string' && SERVED_KINDS.has(kind) && typeof id === 'string' && id ? `${kind}:${id}` : null;
    if (event.type === 'updated' && event.action.type === 'error') {
      const error = event.action.error;
      if (isUnauthenticated(error)) {
        // Authentication is lost for the session, not for one object: strip this query now and retire the epoch.
        if (state.data !== undefined) query.setState({ data: undefined, dataUpdatedAt: 0 });
        retireOnce();
        return;
      }
      if (revokedQueries.has(query) && !isAuthoritativeDenial(error)) { query.setState({ error: revokedQueries.get(query) }); return; }
      if (!isAuthoritativeDenial(error)) {
        // A first (offline) visit to something already confirmed revoked reports it as deleted, not unreachable.
        if (tombstoned(query)) { const gone = revokedError(); query.setState({ error: gone }); revokedQueries.set(query, gone); }
        return;
      }
      // A resource the server served before (in a detail or a list) is now refused: stop showing it, and
      // revoke what it identifies.
      const servedBefore = state.data !== undefined || (resourceKey !== null && served.has(resourceKey));
      if (state.data !== undefined) query.setState({ data: undefined, dataUpdatedAt: 0 });
      revokedQueries.set(query, error);
      if (!servedBefore || typeof id !== 'string' || !id) return;
      if (kind === 'class-session') revokeClass(id);
      else if (kind === 'course' && !courses.has(id)) { courses.set(id, 'self'); sweep({ strict: null }); }
      else if (kind === 'review-document' && !documents.has(id)) { documents.add(id); sweep({ strict: null }); }
      return;
    }
    if (state.data === undefined) return;
    const success = event.type === 'updated' && event.action.type === 'success';
    const manual = event.type === 'updated' && event.action.type === 'success' && event.action.manual === true;
    if (success && state.status === 'success') recordServed(query);
    // Checked before anything else (even with an empty ledger, e.g. after an ambiguous deletion restored it):
    // a mutation reply that predates the revocation which dropped this projection needs a fresh read instead.
    if (success && manual && droppedQueries.has(query)) { reset(query); return; }
    if (success && !manual) {
      droppedQueries.delete(query); revokedQueries.delete(query);
      if (kind === 'class-session' && typeof id === 'string') unverified.delete(id);
    }
    if (isEmpty()) return;
    const strict = success ? strictOnArrival.get(query) ?? null : null;
    if (strict) strictOnArrival.delete(query);
    // Membership first seen in this reply may confirm more revocations: record and propagate them first.
    if (learn(readMembership(client))) {
      for (const classId of classes) notifyClass(classId);
      sweep({ strict: null });
    }
    if (query.state.data !== undefined) apply(query, judge(query, readMembership(client), strict), false);
  };

  /** The query's key names something this ledger confirmed revoked. */
  const tombstoned = (query: Query) => {
    const kind = query.queryKey[2];
    const id = query.queryKey[3];
    if (typeof kind !== 'string' || typeof id !== 'string') return false;
    if (kind === 'class-session' || CLASS_KEYED.has(kind)) return classes.has(id);
    if (kind === 'review-document' || kind === 'review-revisions') return documents.has(id);
    if (kind === 'course' || kind === 'materials') return courses.has(id);
    if (kind === 'transcript-segments') return versions.has(id);
    return false;
  };

  const retireOnce = () => { if (!unauthenticated) { unauthenticated = true; queueMicrotask(() => hooks.onUnauthenticated?.()); } };
  /** A write refused with 401 means the session is gone just as much as a read refused with 401. */
  const onMutationEvent = (event: MutationCacheNotifyEvent) => {
    if (event.type === 'updated' && event.action.type === 'error' && isUnauthenticated(event.action.error)) retireOnce();
  };

  /** A successful read lists or shows resources: their later denial is evidence of revocation. */
  const recordServed = (query: Query) => {
    const kind = query.queryKey[2];
    const id = query.queryKey[3];
    const data = query.state.data;
    if (typeof kind === 'string' && SERVED_KINDS.has(kind) && typeof id === 'string' && id) served.add(`${kind}:${id}`);
    const listed = kind === 'class-sessions' ? 'class-session' : kind === 'courses' ? 'course' : kind === 'review-library' || kind === 'review-documents' ? 'review-document' : null;
    if (listed) for (const item of items(data) ?? []) { const itemId = str(record(item)?.id); if (itemId) served.add(`${listed}:${itemId}`); }
  };

  function revokeClass(classId: string) {
    const fresh = !classes.has(classId);
    classes.add(classId);
    // A single classroom is known by its ID: only projections whose classroom is unknown are affected.
    if (fresh) sweep({ strict: { cascade: false, semesterCascade: false, classes: true, exempt: null } });
    notifyClass(classId);
  }

  return {
    async prepareScopeDeletion(scope) {
      const bound = hooks.boundClassId?.() ?? null;
      if (!bound || !api) return { exempt: null };
      // Always a fresh read: a cached link may predate a move made on another device.
      const fresh = async <T>(kind: string, id: string, read: (signal: AbortSignal) => Promise<T>) => client.fetchQuery({ queryKey: ['account', accountKey, kind, id], queryFn: ({ signal }) => read(signal), staleTime: 0 });
      try {
        const session = await fresh('class-session', bound, signal => api.classSession(bound, signal));
        if (scope.kind === 'semester') await fresh('course', session.course_id, signal => api.course(session.course_id, signal));
        unverified.delete(bound);
        return { exempt: null };
      } catch (error) {
        // The server does not know this classroom right now (e.g. the demo classroom before registration):
        // this deletion cannot contain it. Other failures leave its membership unknown (not the cached link).
        const unknownToServer = error instanceof ApiClientError && error.kind === 'http' && (error.code === 'NOT_FOUND' || error.code === 'GONE') && !served.has(`class-session:${bound}`);
        if (!unknownToServer) unverified.add(bound);
        return { exempt: unknownToServer ? bound : null };
      }
    },
    applyScopeReceipt(scope, prepared) {
      recordScope(scope);
      if (scope.kind === 'semester' && scope.mode === 'cascade') expandSemester(scope.id);
      sweep({ strict: scopeStrict(scope, prepared) });
    },
    applyAmbiguousScopeDeletion(scope) {
      // Judge as if confirmed, but keep nothing in the ledger: a later read decides what really happened.
      const savedCourses = new Map(courses);
      const savedSemesters = new Map(semesters);
      const savedMovedCourse = new Map(movedCourse);
      const savedMovedSemester = new Map(movedSemester);
      recordScope(scope);
      if (scope.kind === 'semester' && scope.mode === 'cascade') expandSemester(scope.id);
      const restore = <K, V>(target: Map<K, V>, saved: Map<K, V>) => { target.clear(); for (const [key, value] of saved) target.set(key, value); };
      // Cache events the sweep causes synchronously (cancel-and-revert, resets) must not learn tombstones or
      // call revocation hooks from this temporary scope.
      tentativeActive = true;
      try { sweep({ strict: scopeStrict(scope) ?? { cascade: false, semesterCascade: false, classes: false, exempt: null }, tentative: true }); }
      finally {
        restore(courses, savedCourses); restore(semesters, savedSemesters); restore(movedCourse, savedMovedCourse); restore(movedSemester, savedMovedSemester);
        tentativeActive = false;
      }
    },
    revokeClass,
    revokeAudio(classId, captureEpochId) {
      const epochs = audio.get(classId) ?? new Set<string>();
      if (epochs.has(captureEpochId)) return;
      epochs.add(captureEpochId);
      audio.set(classId, epochs);
      sweep({ strict: null });
    },
    isClassRevoked: classId => classes.has(classId),
    reset() {
      semesters.clear(); courses.clear(); classes.clear(); documents.clear(); versions.clear(); audio.clear();
      notified.clear(); movedCourse.clear(); movedSemester.clear(); served.clear(); unverified.clear();
      strictOnArrival = new WeakMap(); revokedQueries = new WeakMap(); droppedQueries = new WeakSet(); unauthenticated = false;
    },
    install() {
      const stopQueries = client.getQueryCache().subscribe(onCacheEvent);
      const stopMutations = client.getMutationCache().subscribe(onMutationEvent);
      return () => { stopQueries(); stopMutations(); };
    },
  };
}
