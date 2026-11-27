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
/** Children of a cascaded scope, listed fresh by the server just before the deletion was sent. */
export type ScopeMembers = { courses: string[]; classes: string[] };
/**
 * What the pre-submit check established for this one deletion request.
 * - `members`: the cascade's children as the server listed them just before the deletion: the only
 *   membership that counts for it (cached parent links may predate a move made on another device).
 * - `unverifiedLinks`: that listing failed, so no cached classroom/course parent link is proof for this
 *   deletion (except the `verified` ones read fresh): affected projections are hidden, nothing is
 *   tombstoned from such a link, and a bound capture is only suspended.
 * - `unresolved`: classrooms (and, for a semester, courses) missing from the listing that may still belong to it
 *   (the bound classroom, or one cached under the scope) whose fresh detail read failed: hidden and, if bound,
 *   suspended for this deletion.
 * Neither (a ledger without an API, e.g. unit tests of the cache machinery): cached links are used.
 */
export type PreparedDeletion = { exempt: string | null; members?: ScopeMembers; unverifiedLinks?: boolean; verified?: string[]; unresolved?: string[] };

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

/** `revision_id`: the exact review revision a hit (or an excerpt) came from, when it names one. */
export type SearchHitRef = { object_type: string; object_id: string; class_session_id: string | null; locator: string; revision_id?: string | null };

export type RevocationLedger = {
  /** Before submitting a scope deletion: list a cascade's children fresh (else resolve the bound classroom's course and semester). */
  prepareScopeDeletion(scope: { kind: ScopeKind; id: string; mode?: DeleteMode }): Promise<PreparedDeletion>;
  /** The server confirmed a scope deletion with the submitted kind, id, mode and destination. */
  applyScopeReceipt(scope: ScopeDeletion, prepared?: PreparedDeletion): void;
  /** A scope deletion's outcome is unknown (its reply was lost or unreadable): drop what it may have removed, revoke nothing. */
  applyAmbiguousScopeDeletion(scope: ScopeDeletion, prepared?: PreparedDeletion): void;
  revokeClass(classId: string): void;
  revokeAudio(classId: string, captureEpochId: string): void;
  isClassRevoked(classId: string): boolean;
  /** The data set behind this account was replaced wholesale (Mock fixtures): forget every revocation. */
  reset(): void;
  /** Subscribe to the query cache (late responses, reverts, refetch denials). Returns the unsubscribe. */
  install(): () => void;
  /**
   * PR-11c: a search hit points at content this account has confirmed revoked (its course, classroom,
   * review document or transcript version). Search views hold hits outside the query cache, so they
   * filter them through this on every render and re-render on `subscribe`.
   */
  isSearchHitRevoked(hit: SearchHitRef): boolean;
  /**
   * One membership snapshot for filtering a whole result list (an empty ledger revokes nothing and
   * reads no cache). Take a new filter whenever `version` changes.
   */
  searchHitFilter(): (hit: SearchHitRef) => boolean;
  /**
   * Hits a search returned name resources the server served this account: a later denial of one of
   * them (e.g. its classroom refused when a hit is opened) revokes it, so every hit depending on it
   * disappears, not only the one that was opened. A review revision a later fresh revision list omits
   * is hidden the same way, in every view.
   */
  recordSearchServed(hits: readonly SearchHitRef[]): void;
  /** Notified after every revocation sweep and reset. Returns the unsubscribe. */
  subscribe(listener: () => void): () => void;
  /** Changes whenever revocations change (for `useSyncExternalStore`). */
  version(): number;
  /**
   * Changes when held search results can no longer be judged hit by hit: a cascade deletion (whose
   * membership may be unknown), an ambiguous deletion, or a replaced data set. Search views then drop
   * their results and in-flight replies; only a search started afterwards fills them again.
   */
  searchEpoch(): number;
  /** A request made outside the query cache was refused: a 401 retires the account epoch like any other read. */
  observeError(error: unknown): void;
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
    if (isIndeterminate(error)) revocation?.applyAmbiguousScopeDeletion(scope, prepared);
    throw error;
  }
  // Only a receipt for exactly the submitted scope confirms the deletion; anything else is an unknown outcome.
  const value = receipt as { scope_type?: unknown; scope_id?: unknown; state?: unknown } | null;
  const states = ['access_revoked', 'pending_delete', 'delete_failed', 'deleted'];
  if (!value || typeof value !== 'object' || value.scope_type !== submitted.kind || value.scope_id !== submitted.id || !states.includes(String(value.state))) {
    revocation?.applyAmbiguousScopeDeletion(scope, prepared);
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
 * `distrust`: the deletion's children could not be listed, so no parent link (except `verified` ones) is proof.
 * `unresolved`: classrooms and courses whose membership in this deletion could not be established either way.
 */
type Strict = { cascade: boolean; semesterCascade: boolean; classes: boolean; exempt: string | null; distrust: boolean; verified: ReadonlySet<string>; unresolved: ReadonlySet<string> } | null;
type Verdict = { type: 'keep' } | { type: 'gone' } | { type: 'unknown' } | { type: 'filter'; data: unknown };
const KEEP: Verdict = { type: 'keep' };
const GONE: Verdict = { type: 'gone' };
const UNKNOWN: Verdict = { type: 'unknown' };
const tri = (value: Tri): Verdict => value === true ? GONE : value === 'unknown' ? UNKNOWN : KEEP;
const NO_IDS: ReadonlySet<string> = new Set();
const mergeStrict = (a: NonNullable<Strict>, b: NonNullable<Strict>): NonNullable<Strict> => ({
  cascade: a.cascade || b.cascade, semesterCascade: a.semesterCascade || b.semesterCascade, classes: a.classes || b.classes, exempt: a.exempt === b.exempt ? a.exempt : null,
  distrust: a.distrust || b.distrust,
  verified: a.distrust && b.distrust ? new Set([...a.verified].filter(id => b.verified.has(id))) : a.distrust ? a.verified : b.verified,
  unresolved: new Set([...a.unresolved, ...b.unresolved]),
});
/** A single classroom revoked by ID (event or denial): only projections whose classroom is unknown are affected. */
const CLASS_ONLY: NonNullable<Strict> = { cascade: false, semesterCascade: false, classes: true, exempt: null, distrust: false, verified: NO_IDS, unresolved: NO_IDS };

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

/** Every outcome first: an expired session wins over any other failure (a sibling's transport error must not hide it). */
async function settleAll<T>(work: Promise<T>[]): Promise<T[]> {
  const results = await Promise.allSettled(work);
  const failures = results.flatMap(result => result.status === 'rejected' ? [result.reason as unknown] : []);
  if (failures.length) throw failures.find(isUnauthenticated) ?? failures[0];
  return results.map(result => (result as PromiseFulfilledResult<T>).value);
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

/**
 * The personal editor can hold text copied from a version, so it opens only on readable versions: never
 * after a refusal, and not while the re-read that follows one is pending (TanStack then reports a query
 * without data as pending, not failed). A successful empty list still allows a blank draft.
 */
export function personalEditorAccess(revisions: { data: unknown; isError: boolean; isPending: boolean; error: unknown }): 'open' | 'checking' | 'denied' | 'unavailable' {
  const transport = revisions.error instanceof ApiClientError && revisions.error.kind === 'network';
  if (revisions.isError && !transport) return 'denied';
  if (revisions.data === undefined) return revisions.isPending ? 'checking' : 'unavailable';
  return 'open';
}

/**
 * Deleted scopes. `cascade`: its children are inferred from cached parent links; `enumerated`: a cascade
 * whose children the server listed just before the deletion (revoked by ID, never inferred from links);
 * `self`: only the object itself (`move_children`).
 */
type ScopeState = 'cascade' | 'enumerated' | 'self';

export function createRevocationLedger({ client, accountKey, api, hooks = {} }: { client: QueryClient; accountKey: string; api?: ApiClient; hooks?: RevocationHooks }): RevocationLedger {
  const semesters = new Map<string, ScopeState>();
  const courses = new Map<string, ScopeState>();
  const classes = new Set<string>();
  const documents = new Set<string>();
  const versions = new Set<string>();
  const audio = new Map<string, Set<string>>();
  const notified = new Set<string>();
  /** Children moved by a confirmed `move_children`: old parent → new parent (classroom courses, course semesters). */
  const movedCourse = new Map<string, string>();
  const movedSemester = new Map<string, string>();
  /**
   * Resources the server served in this account epoch (a later denial of them is evidence, even without
   * cached data). A classroom also counts as served when served content names it (`class_session_id`).
   */
  const served = new Set<string>();
  /**
   * Classrooms (courses) whose cached parent link may predate a move, so it is never proof: a bound classroom
   * whose fresh read failed before a deletion, and every cached link when a cascade's children could not be
   * listed. Unknown until a server read started after that deletion shows them again.
   */
  const unverified = new Set<string>();
  const unverifiedCourses = new Set<string>();
  /** Requests in flight during a new revocation started before it: their replies are judged strictly once. */
  let strictOnArrival = new WeakMap<Query, NonNullable<Strict>>();
  /** Projections shown as revoked or denied: a later transport failure (offline refetch) must not relabel them as merely unreachable. */
  let revokedQueries = new WeakMap<Query, unknown>();
  /** Projections a revocation dropped as unknown: only a fresh read may fill them again, not a late mutation reply. */
  let droppedQueries = new WeakSet<Query>();
  let unauthenticated = false;
  /**
   * Documents whose revision list the server refused (403/404/410) while their metadata may still be
   * readable: search snippets of their revisions are hidden until a later list read succeeds.
   */
  const deniedRevisionDocs = new Set<string>();
  /**
   * Review revisions this account epoch saw served per document (in search hits or a fresh revision list),
   * and those a later fresh, complete revision list no longer offers while the document itself stays
   * readable: hits and excerpts of exactly those revisions are hidden everywhere, their siblings stay.
   */
  const knownRevisions = new Map<string, Set<string>>();
  const missingRevisions = new Map<string, Set<string>>();
  const knowRevision = (docId: string, revisionId: string) => { const known = knownRevisions.get(docId) ?? new Set<string>(); known.add(revisionId); knownRevisions.set(docId, known); };
  /** A fresh complete list of the document's revisions: what it omits of the known ones is missing now. Returns whether that changed. */
  const offerRevisions = (docId: string, offered: ReadonlySet<string>) => {
    const before = missingRevisions.get(docId) ?? new Set<string>();
    const missing = new Set([...(knownRevisions.get(docId) ?? [])].filter(revisionId => !offered.has(revisionId)));
    for (const revisionId of offered) knowRevision(docId, revisionId);
    if (missing.size) missingRevisions.set(docId, missing); else missingRevisions.delete(docId);
    return missing.size !== before.size || [...missing].some(revisionId => !before.has(revisionId));
  };
  let searchResets = 0;
  const listeners = new Set<() => void>();
  let changes = 0;
  const changed = () => { changes++; for (const listener of [...listeners]) listener(); };
  /**
   * Non-zero while the ledger itself writes to the cache (a sweep, or judging one arrival). The cache events
   * those writes cause synchronously are its own intermediate results: learning from them could turn a
   * half-applied sweep (or a tentative scope) into permanent revocations. Every such write was judged already.
   */
  let quiet = 0;
  const quietly = (run: () => void) => { quiet++; try { run(); } finally { quiet--; } };

  const hasSemesterCascade = () => [...semesters.values()].includes('cascade');
  const hasCascade = () => hasSemesterCascade() || [...courses.values()].includes('cascade');
  const follow = (moves: Map<string, string>, id: string) => { let current = id; for (let hops = 0; hops < 32 && moves.has(current); hops++) current = moves.get(current)!; return current; };
  const isEmpty = () => !semesters.size && !courses.size && !classes.size && !documents.size && !versions.size && !audio.size && !deniedRevisionDocs.size && !missingRevisions.size;
  const distrusted = (id: string, marks: Set<string>, strict: Strict) => marks.has(id) || (strict?.distrust === true && !strict.verified.has(id));

  // `strict` (applying a new revocation): a membership it could affect that cannot be established counts as affected.
  const semesterOf = (courseId: string, m: Membership, strict: Strict, hint?: string): Tri => {
    if (!hasSemesterCascade()) return false;
    if (distrusted(courseId, unverifiedCourses, strict)) return strict?.semesterCascade ? 'unknown' : false;
    // The newest cached revision decides (an entry's own field may be a stale copy); conflicts are unknown.
    const cached = m.courseSemester.has(courseId) ? parentOf(m.courseSemester, courseId) : hint;
    if (cached === undefined) return strict?.semesterCascade ? 'unknown' : false;
    return semesters.get(follow(movedSemester, cached)) === 'cascade';
  };
  /** The course object itself is gone (deleted directly, either mode, or inside a cascaded semester). */
  const courseGone = (courseId: string, m: Membership, strict: Strict, semesterHint?: string): Tri => courses.has(courseId) ? true : strict?.unresolved.has(courseId) ? 'unknown' : semesterOf(courseId, m, strict, semesterHint);
  /** The course's classrooms are revoked through their links (cascade only; listed cascades revoke them by ID; moved children survive). */
  const courseCascaded = (courseId: string, m: Membership, strict: Strict): Tri => courses.get(courseId) === 'cascade' ? true : courses.has(courseId) ? false : semesterOf(courseId, m, strict);
  const classGone = (classId: string, m: Membership, strict: Strict, courseHint?: string): Tri => {
    if (classes.has(classId)) return true;
    if (strict?.unresolved.has(classId)) return 'unknown';
    if (!hasCascade()) return false;
    if (distrusted(classId, unverified, strict)) return strict?.cascade && strict.exempt !== classId ? 'unknown' : false;
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
        if ((courseId && (courses.get(courseId) === 'enumerated' || courseCascaded(courseId, m, strict) === true)) || (classId && classGone(classId, m, strict) === true)) return filterList(data, () => true);
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
    if (hasSemesterCascade()) for (const id of m.courseSemester.keys()) { const parent = unverifiedCourses.has(id) ? undefined : parentOf(m.courseSemester, id); if (parent !== undefined && semesters.get(follow(movedSemester, parent)) === 'cascade' && courses.get(id) !== 'cascade') courses.set(id, 'cascade'); }
    for (const id of m.classCourse.keys()) if (classGone(id, m, null) === true) classes.add(id);
    for (const id of m.docClass.keys()) if (docGone(id, m, null) === true) documents.add(id);
    for (const id of m.versionClass.keys()) { const parent = parentOf(m.versionClass, id); if (parent !== undefined && classGone(parent, m, null) === true) versions.add(id); }
    return courses.size + classes.size + documents.size + versions.size !== before;
  };

  /**
   * Cancel a read in flight but keep the snapshot shown now. Cancelling with `revert` alone restores the
   * state from before the read started, which can still hold rows a later revocation filtered out.
   */
  const cancelKeepingSnapshot = (query: Query) => {
    const snapshot = query.state;
    void query.cancel({ revert: true });
    query.setState({ ...snapshot, fetchStatus: 'idle' });
  };

  /**
   * Sweep every cached projection of this account against one membership snapshot, then tell the shell
   * which classrooms are revoked. The cache events the sweep itself causes are not learned from.
   */
  const sweep = ({ strict, tentative = false }: { strict: Strict; tentative?: boolean }) => {
    const m = readMembership(client);
    if (!tentative) learn(m);
    quietly(() => {
      for (const query of client.getQueryCache().getAll()) {
        const kind = query.queryKey[2];
        if (query.queryKey[0] === 'account' && typeof kind === 'string' && REVOCABLE.has(kind) && query.state.fetchStatus === 'fetching') {
          if (tentative && strict) {
            // A read started before an unknown outcome must not land. A snapshot proven unaffected stays
            // (the read is cancelled, the snapshot kept); anything else is dropped.
            const verdict = judge(query, m, strict);
            if (verdict.type === 'keep' && query.state.data !== undefined) { cancelKeepingSnapshot(query); continue; }
            reset(query);
            continue;
          }
          if (strict) { const previous = strictOnArrival.get(query); strictOnArrival.set(query, previous ? mergeStrict(previous, strict) : strict); }
        }
        apply(query, judge(query, m, strict), tentative);
      }
    });
    changed();
    if (tentative) return;
    for (const classId of classes) notifyClass(classId);
    // A bound capture whose classroom cannot be placed outside the newly deleted scope stops capturing
    // locally; it is revoked only once its membership or a denial confirms it.
    const bound = hooks.boundClassId?.() ?? null;
    if (bound && (strict?.cascade || strict?.unresolved.has(bound)) && classGone(bound, m, strict) === 'unknown') hooks.onCaptureSuspended?.(bound);
  };

  const scopeStrict = (scope: ScopeDeletion, prepared?: PreparedDeletion): Strict => scope.mode === 'cascade'
    ? { cascade: true, semesterCascade: scope.kind === 'semester', classes: true, exempt: prepared?.exempt ?? null, distrust: prepared?.unverifiedLinks === true, verified: new Set(prepared?.verified ?? []), unresolved: new Set(prepared?.unresolved ?? []) }
    : null;
  /** A listed cascade: its children are revoked by ID; content whose classroom is unknown is unknown. */
  const listedStrict = (prepared?: PreparedDeletion): Strict => ({ ...CLASS_ONLY, exempt: prepared?.exempt ?? null, unresolved: new Set(prepared?.unresolved ?? []) });
  const listedMembers = (scope: ScopeDeletion, prepared?: PreparedDeletion): ScopeMembers | null => scope.mode === 'cascade' && prepared?.members ? prepared.members : null;

  const recordScope = (scope: ScopeDeletion, members: ScopeMembers | null) => {
    const map = scope.kind === 'course' ? courses : semesters;
    if (map.get(scope.id) !== 'cascade') map.set(scope.id, scope.mode !== 'cascade' ? 'self' : members ? 'enumerated' : 'cascade');
    if (scope.mode === 'move_children' && scope.destination && scope.destination !== scope.id) (scope.kind === 'course' ? movedCourse : movedSemester).set(scope.id, scope.destination);
    if (!members) return;
    // The server's listing is this deletion's membership: its children are revoked by ID.
    for (const id of members.courses) if (courses.get(id) !== 'cascade') courses.set(id, 'enumerated');
    for (const id of members.classes) classes.add(id);
  };

  // In a semester cascade, record every course known to belong to it, so later data is judged without
  // needing the course → semester link again.
  const expandSemester = (semesterId: string) => {
    const m = readMembership(client);
    for (const courseId of m.courseSemester.keys()) { const parent = parentOf(m.courseSemester, courseId); if (parent !== undefined && follow(movedSemester, parent) === semesterId && courses.get(courseId) !== 'cascade') courses.set(courseId, 'cascade'); }
  };

  /** The cascade's children could not be listed: no cached parent link (except fresh ones) is proof for it. */
  const distrustCachedLinks = (semester: boolean, verified: ReadonlySet<string>) => {
    const m = readMembership(client);
    for (const id of m.classCourse.keys()) if (!verified.has(id) && !classes.has(id)) unverified.add(id);
    if (semester) for (const id of m.courseSemester.keys()) if (!verified.has(id) && !courses.has(id)) unverifiedCourses.add(id);
  };
  /** Classroom and course IDs a classroom or course read places under a parent. */
  const placed = (query: Query) => {
    const kind = query.queryKey[2];
    const data = query.state.data;
    const ids = (list: unknown[] | null) => (list ?? []).flatMap(item => { const id = str(record(item)?.id); return id ? [id] : []; });
    return {
      classes: kind === 'class-session' ? ids([data]) : kind === 'class-sessions' ? ids(items(data)) : [],
      courses: kind === 'course' ? ids([data]) : kind === 'courses' ? ids(items(data)) : [],
    };
  };

  const onCacheEvent = (event: QueryCacheNotifyEvent) => {
    if (event.type !== 'updated' && event.type !== 'added') return;
    if (quiet) return;
    const query = event.query;
    if (query.queryKey[0] !== 'account') return;
    // A strict marker belongs to the read that was in flight when a revocation was applied: a failed read, or a
    // new one started after it, does not inherit it.
    if (event.type === 'updated' && (event.action.type === 'fetch' || event.action.type === 'error')) strictOnArrival.delete(query);
    const state = query.state;
    const kind = query.queryKey[2];
    const id = query.queryKey[3];
    const resourceKey = typeof kind === 'string' && SERVED_KINDS.has(kind) && typeof id === 'string' && id ? `${kind}:${id}` : null;
    if (event.type === 'updated' && event.action.type === 'error') {
      const error = event.action.error;
      if (isUnauthenticated(error)) {
        // Authentication is lost for the session, not for one object: strip this query now and retire the epoch.
        if (state.data !== undefined) quietly(() => query.setState({ data: undefined, dataUpdatedAt: 0 }));
        retireOnce();
        return;
      }
      if (revokedQueries.has(query) && !isAuthoritativeDenial(error)) { quietly(() => query.setState({ error: revokedQueries.get(query) })); return; }
      if (!isAuthoritativeDenial(error)) {
        // A first (offline) visit to something already confirmed revoked reports it as deleted, not unreachable.
        if (tombstoned(query)) { const gone = revokedError(); quietly(() => query.setState({ error: gone })); revokedQueries.set(query, gone); }
        return;
      }
      // A resource the server served before (in a detail, a list, or content naming it) is now refused:
      // stop showing it, and revoke what it identifies.
      if (kind === 'review-revisions' && typeof id === 'string' && id && !deniedRevisionDocs.has(id)) { deniedRevisionDocs.add(id); changed(); }
      const servedBefore = state.data !== undefined || (resourceKey !== null && served.has(resourceKey));
      if (state.data !== undefined) quietly(() => query.setState({ data: undefined, dataUpdatedAt: 0 }));
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
    if (success && !manual && kind === 'review-revisions' && typeof id === 'string' && id) {
      // A fresh server read of the whole list (every page): the document's revisions are readable again,
      // except the known ones it no longer offers.
      const offered = new Set((items(state.data) ?? []).flatMap(item => { const revisionId = str(record(item)?.id); return revisionId ? [revisionId] : []; }));
      const lifted = deniedRevisionDocs.delete(id);
      if (offerRevisions(id, offered) || lifted) changed();
    }
    if (success && manual && droppedQueries.has(query)) { quietly(() => reset(query)); return; }
    // A reply to a read in flight when a revocation was applied is judged strictly (once, by that read).
    const strict = success ? strictOnArrival.get(query) ?? null : null;
    if (strict && !manual) strictOnArrival.delete(query);
    if (success && !manual) {
      droppedQueries.delete(query); revokedQueries.delete(query);
      const where = placed(query);
      if (strict?.distrust) {
        // Read before a deletion whose children could not be listed: its parent links are no proof.
        for (const classId of where.classes) if (!strict.verified.has(classId)) unverified.add(classId);
        for (const courseId of where.courses) if (!strict.verified.has(courseId)) unverifiedCourses.add(courseId);
      } else if (!strict) {
        // Read after every revocation so far: what it shows is current, also for links distrusted before.
        for (const classId of where.classes) unverified.delete(classId);
        for (const courseId of where.courses) unverifiedCourses.delete(courseId);
      }
    }
    if (isEmpty()) return;
    // Membership first seen in this reply may confirm more revocations: record and propagate them first.
    if (learn(readMembership(client))) {
      for (const classId of classes) notifyClass(classId);
      sweep({ strict: null });
    }
    if (query.state.data !== undefined) { const verdict = judge(query, readMembership(client), strict); quietly(() => apply(query, verdict, false)); }
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

  function searchHitFilter(): (hit: SearchHitRef) => boolean {
    if (isEmpty()) return () => false;
    const m = readMembership(client);
    return hit => {
      const classId = hit.class_session_id ?? undefined;
      switch (hit.object_type) {
        case 'course': return courseGone(hit.object_id, m, null) === true;
        case 'class_session': return classGone(hit.object_id, m, null) === true;
        case 'transcript_segment': {
          const versionId = hit.locator.split('/')[2];
          return (versionId !== undefined && versions.has(versionId)) || (classId !== undefined && classGone(classId, m, null) === true);
        }
        case 'review_document': case 'card': return deniedRevisionDocs.has(hit.object_id)
          || (!!hit.revision_id && missingRevisions.get(hit.object_id)?.has(hit.revision_id) === true)
          || docGone(hit.object_id, m, null, classId) === true;
        default: return false;
      }
    };
  }

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
    // Content the server served that names its classroom shows the classroom existed for this account.
    const evidence = (value: unknown) => { const classId = str(record(value)?.class_session_id); if (classId) served.add(`class-session:${classId}`); };
    evidence(data);
    for (const item of items(data) ?? []) evidence(item);
  };

  function revokeClass(classId: string) {
    const fresh = !classes.has(classId);
    classes.add(classId);
    if (fresh) sweep({ strict: CLASS_ONLY });
    notifyClass(classId);
  }

  /** The children a cascade removes, listed fresh from the server (every page; archived courses too). */
  const listMembers = async (reader: ApiClient, scope: { kind: ScopeKind; id: string }): Promise<ScopeMembers> => {
    const under = (page: unknown, field: string, parent: string) => {
      const list = items(page);
      if (!list) throw new ApiClientError('invalid_response');
      return list.map(item => { const value = record(item); const itemId = str(value?.id); if (!itemId || value?.[field] !== parent) throw new ApiClientError('invalid_response'); return itemId; });
    };
    const courseIds = scope.kind === 'course' ? [scope.id] : under(await reader.allCourses({ semester_id: scope.id }), 'semester_id', scope.id);
    const classIds = (await settleAll(courseIds.map(async courseId => under(await reader.allClassSessions({ course_id: courseId }), 'course_id', courseId)))).flat();
    return { courses: scope.kind === 'semester' ? courseIds : [], classes: classIds };
  };
  /** A new server read through the cache: never joins a read already in flight, whose reply may predate the listing. */
  const freshRead = async <T>(kind: string, id: string, read: (signal: AbortSignal) => Promise<T>) => {
    const queryKey = ['account', accountKey, kind, id];
    await client.cancelQueries({ queryKey, exact: true });
    return client.fetchQuery({ queryKey, queryFn: ({ signal }) => read(signal), staleTime: 0 });
  };
  /** A classroom confirmed to live elsewhere: cached classroom lists show it there (or not, if filtered to its old course). */
  const reconcileMove = (session: Record<string, unknown>) => quietly(() => {
    const classId = str(session.id);
    const courseId = str(session.course_id);
    for (const query of client.getQueryCache().getAll()) {
      if (query.queryKey[0] !== 'account' || query.queryKey[2] !== 'class-sessions') continue;
      const list = items(query.state.data);
      if (!list || !list.some(item => str(record(item)?.id) === classId)) continue;
      const filter = typeof query.queryKey[3] === 'string' ? query.queryKey[3] : undefined;
      const next = filter && filter !== courseId ? list.filter(item => str(record(item)?.id) !== classId) : list.map(item => str(record(item)?.id) === classId ? session : item);
      query.setState({ data: { ...record(query.state.data), items: next } });
    }
  });

  return {
    async prepareScopeDeletion(scope) {
      // Moved children survive: nothing to establish. Without an API the cached links are all there is.
      if (!api || scope.mode === 'move_children') return { exempt: null };
      let members: ScopeMembers | null = null;
      try {
        // The server's own listing, just before the deletion, is its membership (a cached link may predate a
        // move made on another device, in either direction). The confirmation token binds the scope, so a
        // change after the preview makes the server refuse the deletion instead.
        members = await listMembers(api, scope);
      } catch (error) {
        // An expired session ends here (the deletion is not sent); otherwise no cached link is proof.
        if (isUnauthenticated(error)) throw error;
      }
      if (members) {
        // Listed by the server: visible to this account (evidence for a later denial, whatever the outcome).
        for (const id of members.classes) served.add(`class-session:${id}`);
        for (const id of members.courses) served.add(`course:${id}`);
        // Missing from the listing is not proof of a move: the bound classroom and children cached under the
        // scope are read again. A denial revokes them (guard); a read elsewhere confirms a move; a failure leaves
        // them unresolved (hidden, a bound capture suspended).
        const listed = new Set(members.classes);
        const parents = new Set(scope.kind === 'course' ? [scope.id] : members.courses);
        const unresolved: string[] = [];
        // A course cached in the semester but missing from its listing (moved, or deleted elsewhere) is read too.
        const cached = readMembership(client);
        const missingCourses = scope.kind === 'semester' ? [...cached.courseSemester].filter(([courseId, link]) => link.parent === scope.id && !parents.has(courseId)).map(([courseId]) => courseId) : [];
        await settleAll(missingCourses.map(async courseId => {
          try {
            const course = await freshRead('course', courseId, signal => api.course(courseId, signal));
            if (course.semester_id === scope.id) unresolved.push(courseId);
          } catch (error) {
            if (isUnauthenticated(error)) throw error;
            if (!isAuthoritativeDenial(error)) unresolved.push(courseId);
          }
        }));
        const under = new Set([...parents, ...missingCourses]);
        const candidates = new Set<string>();
        const bound = hooks.boundClassId?.() ?? null;
        if (bound && !listed.has(bound)) candidates.add(bound);
        for (const [classId, link] of cached.classCourse) if (!listed.has(classId) && under.has(link.parent)) candidates.add(classId);
        await settleAll([...candidates].map(async classId => {
          try {
            const session = await freshRead('class-session', classId, signal => api.classSession(classId, signal));
            if (parents.has(session.course_id)) unresolved.push(classId);
            else reconcileMove(session as unknown as Record<string, unknown>);
          } catch (error) {
            if (isUnauthenticated(error)) throw error;
            if (!isAuthoritativeDenial(error)) unresolved.push(classId);
          }
        }));
        return { exempt: null, members, ...(unresolved.length ? { unresolved } : {}) };
      }
      const bound = hooks.boundClassId?.() ?? null;
      if (!bound) return { exempt: null, unverifiedLinks: true };
      try {
        const session = await freshRead('class-session', bound, signal => api.classSession(bound, signal));
        const verified = [bound];
        if (scope.kind === 'semester') { await freshRead('course', session.course_id, signal => api.course(session.course_id, signal)); verified.push(session.course_id); }
        unverified.delete(bound);
        return { exempt: null, unverifiedLinks: true, verified };
      } catch (error) {
        if (isUnauthenticated(error)) throw error;
        // The server does not know this classroom right now (e.g. the demo classroom before registration, with
        // nothing served that names it): this deletion cannot contain it. Other failures leave it unknown.
        const unknownToServer = error instanceof ApiClientError && error.kind === 'http' && (error.code === 'NOT_FOUND' || error.code === 'GONE') && !served.has(`class-session:${bound}`);
        if (!unknownToServer) unverified.add(bound);
        return { exempt: unknownToServer ? bound : null, unverifiedLinks: true };
      }
    },
    applyScopeReceipt(scope, prepared) {
      if (scope.mode === 'cascade') searchResets++;
      const members = listedMembers(scope, prepared);
      recordScope(scope, members);
      if (members) { sweep({ strict: listedStrict(prepared) }); return; }
      if (scope.mode === 'cascade' && prepared?.unverifiedLinks) distrustCachedLinks(scope.kind === 'semester', new Set(prepared.verified ?? []));
      else if (scope.kind === 'semester' && scope.mode === 'cascade') expandSemester(scope.id);
      sweep({ strict: scopeStrict(scope, prepared) });
    },
    applyAmbiguousScopeDeletion(scope, prepared) {
      searchResets++;
      // Judge as if confirmed, but keep nothing in the ledger: a later read decides what really happened.
      const saved = { courses: new Map(courses), semesters: new Map(semesters), movedCourse: new Map(movedCourse), movedSemester: new Map(movedSemester), classes: new Set(classes) };
      const members = listedMembers(scope, prepared);
      recordScope(scope, members);
      if (!members && !prepared?.unverifiedLinks && scope.kind === 'semester' && scope.mode === 'cascade') expandSemester(scope.id);
      const restore = <K, V>(target: Map<K, V>, from: Map<K, V>) => { target.clear(); for (const [key, value] of from) target.set(key, value); };
      try { sweep({ strict: members ? listedStrict(prepared) : scopeStrict(scope, prepared) ?? { ...CLASS_ONLY, classes: false }, tentative: true }); }
      finally {
        restore(courses, saved.courses); restore(semesters, saved.semesters); restore(movedCourse, saved.movedCourse); restore(movedSemester, saved.movedSemester);
        classes.clear(); for (const classId of saved.classes) classes.add(classId);
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
      notified.clear(); movedCourse.clear(); movedSemester.clear(); served.clear(); unverified.clear(); unverifiedCourses.clear();
      strictOnArrival = new WeakMap(); revokedQueries = new WeakMap(); droppedQueries = new WeakSet(); unauthenticated = false;
      deniedRevisionDocs.clear(); knownRevisions.clear(); missingRevisions.clear();
      searchResets++;
      changed();
    },
    isSearchHitRevoked: hit => searchHitFilter()(hit),
    searchHitFilter,
    recordSearchServed(hits) {
      for (const hit of hits) {
        if (hit.class_session_id) served.add(`class-session:${hit.class_session_id}`);
        if (hit.object_type === 'course') served.add(`course:${hit.object_id}`);
        else if (hit.object_type === 'class_session') served.add(`class-session:${hit.object_id}`);
        else if (hit.object_type === 'review_document' || hit.object_type === 'card') {
          served.add(`review-document:${hit.object_id}`);
          if (hit.revision_id) knowRevision(hit.object_id, hit.revision_id);
        }
      }
    },
    subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    version: () => changes,
    searchEpoch: () => searchResets,
    observeError(error) { if (isUnauthenticated(error)) retireOnce(); },
    install() {
      const stopQueries = client.getQueryCache().subscribe(onCacheEvent);
      const stopMutations = client.getMutationCache().subscribe(onMutationEvent);
      return () => { stopQueries(); stopMutations(); };
    },
  };
}
