import type { components } from '../generated/openapi';
import { advanceTask, ensureDemoClassroom, finishAsr, handleClassroom, expireManifestCursors, persistFinal, projectTask, recordDurable, releaseAccountLeases, releaseLease, revokeAudio, revokeClassroom, seedClassroom, seedHistoricalManifests, setManifestDev, setRetranscribeDev, activeVersionIds, type ClassroomHelpers, type ClassroomHost, type ClassroomStore, type TaskOutcome } from './classroom-backend';
import { createAdminBackend, type AdminHealthDev, type AdminReauthBinding } from './admin-backend';
import { addCandidate, handleReview, seedReview, type ReviewHelpers, type ReviewHost, type ReviewStore } from './review-backend';

/**
 * Development-only, in-memory synthetic backend for endpoints that the real backend does not
 * implement yet (see PR09B-SCOPE-GAPS.md). Every response uses only fields of the approved
 * contract (0.2.1); tests validate the outputs against openapi.json. It never touches the
 * network, disk or a real account, and it is excluded from production builds.
 */
type S = components['schemas'];
/** A and B are students; C is the synthetic platform administrator (PR-11b). */
export type MockAccountKey = 'A' | 'B' | 'C';
export type MockDataset = 'seeded' | 'empty';
export type MockReply = { status: number; body: unknown };
type ErrorCode = S['Error']['error']['code'];

const FIXED_NOW = '2026-10-28T08:00:00Z';
const ids = {
  fall: '11111111-1111-4111-8111-111111111111', spring: '11111111-1111-4111-8111-111111111112',
  la: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1', ds: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2', mp: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3',
  laSpring: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4', writing: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa5',
  bSemester: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb01', bCourse: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1',
};
export const mockIds = ids;

type Store = {
  user: S['User']; preferences: S['Preferences']; quota: S['QuotaView'];
  semesters: S['Semester'][]; courses: Omit<S['Course'], 'class_session_count' | 'last_class_session_at'>[];
  deletedCourses: Set<string>; deletedSemesters: Set<string>;
  sessions: S['ClassSession'][]; materials: S['CourseMaterial'][]; documents: S['ReviewDocument'][];
  questions: S['Question'][]; notifications: S['Notification'][]; deviceSessions: S['DeviceSession'][]; devices: S['TrustedDevice'][];
  previews: Map<string, S['DeletionPreview']>; idempotency: Map<string, { body: string; reply: MockReply }>;
};

const uuid = (prefix: string, n: number) => `${prefix}-0000-4000-8000-${n.toString(16).padStart(12, '0')}`;
const base = (id: string, created = '2026-09-01T08:00:00Z', revision = 1) => ({ id, revision, created_at: created, updated_at: created });
const flows = (over: Partial<S['FlowStates']> = {}): S['FlowStates'] => ({ capture: 'ended', upload: 'durable', asr: 'succeeded', ai: 'idle', sync: 'synced', ...over });
const sha = (n: number) => n.toString(16).padStart(64, '0');

function session(id: string, course: string, title: string, started: string, minutes: number, over: Partial<S['ClassSession']> & { flowsOver?: Partial<S['FlowStates']> } = {}): S['ClassSession'] {
  const { flowsOver, ...rest } = over;
  const end = new Date(Date.parse(started) + minutes * 60000).toISOString().replace('.000Z', 'Z');
  return { ...base(id, started), updated_at: end, course_id: course, title, language: 'auto', started_at: started, capture_ended_at: end,
    audio_expires_at: null, adopted_transcript_version_id: null, flows: flows(flowsOver), effective_capture_ms: minutes * 60000, known_gap_count: 0, unknown_gap_count: 0, deleted_at: null, ...rest };
}
const material = (id: string, course: string, created: string): S['CourseMaterial'] => ({ ...base(id, created), course_id: course, syllabus: '', glossary: [], language: 'auto', content_sha256: sha(0) });

function seedA(dataset: MockDataset): Store {
  const user: S['User'] = { ...base('a0000000-0000-4000-8000-000000000001', '2026-09-01T08:00:00Z'), email: 'student-a@example.invalid', nickname: '林同学', school: '', major: '', role: 'student', status: 'active', mfa_enabled: true };
  const common = {
    user, preferences: { revision: 1, theme: 'system', reduced_effects: false, reading_font_px: 17, explanation_language: 'auto', automatic_explanation: false } as S['Preferences'],
    quota: quota(), deletedCourses: new Set<string>(), deletedSemesters: new Set<string>(), previews: new Map(), idempotency: new Map(),
    deviceSessions: [
      { ...base('a1000000-0000-4000-8000-000000000001', '2026-10-20T08:00:00Z'), device_id: 'a2000000-0000-4000-8000-000000000001', device_name: '本机浏览器（模拟）', last_active_at: FIXED_NOW, expires_at: '2026-11-27T08:00:00Z', is_current: true, revoked_at: null },
      { ...base('a1000000-0000-4000-8000-000000000002', '2026-10-02T08:00:00Z'), device_id: 'a2000000-0000-4000-8000-000000000002', device_name: '宿舍平板（模拟）', last_active_at: '2026-10-26T13:00:00Z', expires_at: '2026-11-25T08:00:00Z', is_current: false, revoked_at: null },
    ],
    devices: [{ ...base('a2000000-0000-4000-8000-000000000001', '2026-10-20T08:00:00Z'), device_name: '本机浏览器（模拟）', trusted: false, revoked_at: null, key_epoch: 1 }],
  };
  if (dataset === 'empty') return { ...common, semesters: [], courses: [], sessions: [], materials: [], documents: [], questions: [], notifications: [] };
  const course = (id: string, semester: string, name: string, english: string, color: string, icon: string, created: string, over: Partial<S['Course']> = {}) =>
    ({ ...base(id, created), semester_id: semester, name, english_name: english, language: 'auto' as const, teacher: '', room: '', color_token: color, icon_token: icon, archived: false, ...over });
  const s = (n: number) => uuid('c0000000', n);
  const sessions = [
    session(s(1), ids.la, '特征值与特征向量', '2026-10-27T06:00:00Z', 95, { flowsOver: { ai: 'succeeded' }, audio_expires_at: '2026-11-03T07:35:00Z' }),
    session(s(2), ids.la, '矩阵对角化', '2026-10-22T06:00:00Z', 90, { audio_expires_at: '2026-10-29T07:30:00Z' }),
    session(s(3), ids.la, '行列式的性质', '2026-10-15T06:00:00Z', 88),
    session(s(4), ids.ds, '红黑树的插入', '2026-10-25T02:00:00Z', 100, { flowsOver: { upload: 'failed' } }),
    session(s(5), ids.ds, '哈希表与冲突处理', '2026-10-18T02:00:00Z', 96),
    session(s(6), ids.mp, '电磁感应', '2026-10-24T01:00:00Z', 92, { flowsOver: { ai: 'unknown', sync: 'conflict' } }),
    session(s(7), ids.mp, '静电场与高斯定理', '2026-10-10T01:00:00Z', 90),
    session(s(8), ids.laSpring, '向量空间', '2026-04-10T06:00:00Z', 90),
    session(s(9), ids.writing, '论文引言结构', '2026-09-20T02:00:00Z', 80),
  ];
  return {
    ...common,
    semesters: [
      { ...base(ids.fall, '2026-09-01T08:00:00Z'), name: '2026 秋季学期', academic_year: '2026-2027', starts_on: '2026-09-07', ends_on: '2027-01-17', archived: false },
      { ...base(ids.spring, '2026-02-20T08:00:00Z'), name: '2026 春季学期', academic_year: '2025-2026', starts_on: '2026-02-23', ends_on: '2026-07-05', archived: false },
    ],
    courses: [
      course(ids.la, ids.fall, '线性代数', 'Linear Algebra', 'blue', 'math', '2026-09-02T08:00:00Z', { teacher: '王老师（合成）', room: '教一 201' }),
      course(ids.ds, ids.fall, '数据结构与算法分析（双语）', 'Data Structures and Algorithm Analysis', 'teal', 'code', '2026-09-02T09:00:00Z', { language: 'mixed' }),
      course(ids.mp, ids.fall, '大学物理 II', 'University Physics II', 'violet', 'atom', '2026-09-02T10:00:00Z'),
      // Same name as the fall course on purpose: a separate stable ID, never merged (UI-COURSE-01/05).
      course(ids.laSpring, ids.spring, '线性代数', '', 'amber', 'math', '2026-02-24T08:00:00Z'),
      course(ids.writing, ids.fall, '学术英语写作', 'Academic English Writing', 'rose', 'language', '2026-09-03T08:00:00Z', { archived: true, language: 'en' }),
    ],
    sessions,
    materials: [
      { ...material(uuid('d0000000', 1), ids.la, '2026-09-02T08:00:00Z'), revision: 2, syllabus: '第 1–4 章：行列式、矩阵、向量空间、特征值（合成大纲）。', glossary: [{ term: 'eigenvalue', meaning: '特征值', language: 'en' }], content_sha256: sha(1) },
      material(uuid('d0000000', 2), ids.ds, '2026-09-02T09:00:00Z'), material(uuid('d0000000', 3), ids.mp, '2026-09-02T10:00:00Z'),
      material(uuid('d0000000', 4), ids.laSpring, '2026-02-24T08:00:00Z'), material(uuid('d0000000', 5), ids.writing, '2026-09-03T08:00:00Z'),
    ],
    documents: [
      { ...base(uuid('e0000000', 1), '2026-10-27T08:00:00Z'), class_session_id: s(1), kind: 'summary', title: '特征值与特征向量 · 课堂总结', adopted_revision_id: uuid('e1000000', 1), latest_personal_revision_id: null, candidate_revision_ids: [uuid('e1000000', 5)], understood: false, source_status: 'unchanged' },
      { ...base(uuid('e0000000', 2), '2026-10-27T08:10:00Z'), class_session_id: s(1), kind: 'card', title: '特征多项式', adopted_revision_id: uuid('e1000000', 2), latest_personal_revision_id: null, candidate_revision_ids: [], understood: false, source_status: 'unchanged' },
      { ...base(uuid('e0000000', 3), '2026-10-23T12:00:00Z'), class_session_id: s(2), kind: 'note', title: '矩阵对角化 · 个人补充', adopted_revision_id: null, latest_personal_revision_id: uuid('e1000000', 3), candidate_revision_ids: [], understood: true, source_status: 'unchanged' },
      { ...base(uuid('e0000000', 4), '2026-10-25T05:00:00Z'), class_session_id: s(4), kind: 'summary', title: '红黑树的插入 · 候选总结', adopted_revision_id: null, latest_personal_revision_id: null, candidate_revision_ids: [uuid('e1000000', 4)], understood: false, source_status: 'new_material_not_included' },
    ],
    questions: [
      { ...base(uuid('f0000000', 1), '2026-10-27T06:40:00Z'), class_session_id: s(1), task_id: uuid('f1000000', 1), source_snapshot_id: uuid('f2000000', 1), question: '为什么特征向量不唯一？', answer_markdown: '合成回答：任意非零倍数仍满足定义。', state: 'succeeded', understood: false },
      { ...base(uuid('f0000000', 2), '2026-10-27T06:50:00Z'), class_session_id: s(1), task_id: uuid('f1000000', 2), source_snapshot_id: uuid('f2000000', 2), question: '特征值可以是复数吗？', answer_markdown: '合成回答：可以。', state: 'succeeded', understood: true },
      { ...base(uuid('f0000000', 3), '2026-10-10T02:10:00Z'), class_session_id: s(7), task_id: uuid('f1000000', 3), source_snapshot_id: uuid('f2000000', 3), question: '高斯面为什么可以任意选？', answer_markdown: '合成回答：只要包围同样的电荷。', state: 'succeeded', understood: false },
    ],
    notifications: [
      { id: uuid('90000000', 1), kind: 'task_completed', severity: 'info', object_id: null, read_at: null, resolved_at: '2026-10-27T08:00:00Z', created_at: '2026-10-27T08:00:00Z', message_code: 'review_summary.completed' },
      { id: uuid('90000000', 2), kind: 'audio_expiry', severity: 'warning', object_id: null, read_at: null, resolved_at: null, created_at: '2026-10-27T07:30:00Z', message_code: 'audio.expiring' },
      { id: uuid('90000000', 3), kind: 'conflict', severity: 'warning', object_id: null, read_at: '2026-10-24T09:00:00Z', resolved_at: null, created_at: '2026-10-24T03:00:00Z', message_code: 'sync.conflict' },
    ],
  };
}

function seedB(): Store {
  return {
    user: { ...base('b0000000-0000-4000-8000-000000000001'), email: 'student-b@example.invalid', nickname: '陈同学', school: '', major: '', role: 'student', status: 'active', mfa_enabled: false },
    preferences: { revision: 1, theme: 'system', reduced_effects: false, reading_font_px: 17, explanation_language: 'auto', automatic_explanation: false },
    quota: quota(), deletedCourses: new Set(), deletedSemesters: new Set(), previews: new Map(), idempotency: new Map(),
    semesters: [{ ...base(ids.bSemester), name: '2026 秋季学期', academic_year: '2026-2027', starts_on: '2026-09-07', ends_on: '2027-01-17', archived: false }],
    courses: [{ ...base(ids.bCourse, '2026-09-04T08:00:00Z'), semester_id: ids.bSemester, name: '组合数学（模拟账号 B）', english_name: 'Combinatorics', language: 'auto', teacher: '', room: '', color_token: 'green', icon_token: 'math', archived: false }],
    sessions: [session(uuid('cb000000', 1), ids.bCourse, '鸽巢原理', '2026-10-26T03:00:00Z', 90)],
    materials: [material(uuid('db000000', 1), ids.bCourse, '2026-09-04T08:00:00Z')],
    documents: [], questions: [], notifications: [],
    deviceSessions: [{ ...base('b1000000-0000-4000-8000-000000000001', '2026-10-20T08:00:00Z'), device_id: 'b2000000-0000-4000-8000-000000000001', device_name: '本机浏览器（模拟）', last_active_at: FIXED_NOW, expires_at: '2026-11-27T08:00:00Z', is_current: true, revoked_at: null }],
    devices: [],
  };
}

/** PR-11b: the synthetic administrator. Admin data is platform metadata only; this tenant has no study records. */
function seedC(): Store {
  return {
    user: { ...base('c9000000-0000-4000-8000-000000000001', '2026-08-20T08:00:00Z'), email: 'admin@example.invalid', nickname: '运营管理员（模拟）', school: '', major: '', role: 'admin', status: 'active', mfa_enabled: true },
    preferences: { revision: 1, theme: 'system', reduced_effects: false, reading_font_px: 17, explanation_language: 'auto', automatic_explanation: false },
    quota: quota(), deletedCourses: new Set(), deletedSemesters: new Set(), previews: new Map(), idempotency: new Map(),
    semesters: [], courses: [], sessions: [], materials: [], documents: [], questions: [], notifications: [],
    deviceSessions: [{ ...base('c1000000-0000-4000-8000-000000000001', '2026-10-28T07:50:00Z'), device_id: 'c2000000-0000-4000-8000-000000000001', device_name: '运营电脑（模拟）', last_active_at: FIXED_NOW, expires_at: '2026-10-28T15:50:00Z', is_current: true, revoked_at: null }],
    devices: [],
  };
}

function quota(): S['QuotaView'] {
  const period = { period_start: '2026-10-01T00:00:00Z', period_end: '2026-11-01T00:00:00Z' };
  return {
    platform_billing_state: 'normal', asr_enabled: true, ai_enabled: true,
    buckets: [
      { kind: 'asr_month_ms', ...period, limit: 72000000, reserved: 0, submitted: 0, settled: 31800000, unknown: 0, remaining: 40200000 },
      { kind: 'asr_day_ms', period_start: '2026-10-28T00:00:00Z', period_end: '2026-10-29T00:00:00Z', limit: 14400000, reserved: 0, submitted: 0, settled: 0, unknown: 0, remaining: 14400000 },
      { kind: 'interactive_month_count', ...period, limit: 300, reserved: 0, submitted: 0, settled: 42, unknown: 1, remaining: 257 },
      { kind: 'summary_month_count', ...period, limit: 60, reserved: 0, submitted: 0, settled: 6, unknown: 0, remaining: 54 },
      { kind: 'retranscribe_month_ms', ...period, limit: 14400000, reserved: 0, submitted: 0, settled: 0, unknown: 0, remaining: 14400000 },
    ],
  };
}

/** Pure request router over the in-memory store; MSW handlers and node tests share it. */
export function createMockBackend(options: { now?: () => string; newId?: () => string; clockMs?: () => number } = {}) {
  const now = options.now ?? (() => FIXED_NOW);
  let counter = 0;
  const newId = options.newId ?? (() => uuid('99999999', ++counter));
  const stores: Record<MockAccountKey, Store> = { A: seedA('seeded'), B: seedB(), C: seedC() };
  const seededClassroom = () => seedClassroom({ ended: uuid('c0000000', 1), uploadFailed: uuid('c0000000', 4), unknownAi: uuid('c0000000', 6) });
  // Historical classrooms with retained audio also have their recorded epoch.
  const withHistory = (account: MockAccountKey, store: ClassroomStore) => seedHistoricalManifests(store, stores[account].sessions);
  const classrooms: Record<MockAccountKey, ClassroomStore> = { A: withHistory('A', seededClassroom()), B: withHistory('B', seedClassroom(null)), C: withHistory('C', seedClassroom(null)) };
  const reviews: Record<MockAccountKey, ReviewStore> = { A: seedReview(true), B: seedReview(false), C: seedReview(false) };
  const accounts: MockAccountKey[] = ['A', 'B', 'C'];
  /** Mock session generation per account: a sign-out or account switch retires the proofs issued before it. */
  const generations: Record<MockAccountKey, number> = { A: 0, B: 0, C: 0 };
  /** Accounts whose existing sessions an administrator ended; only a fresh sign-in (beginSession) admits them again. */
  const revokedSessions = new Set<MockAccountKey>();
  /** Accounts whose session history includes an administrator's revocation (kept across dataset reseeds). */
  const adminRevoked = new Set<MockAccountKey>();
  /** Live transcription streams admitted per account; a stream admitted while ASR was on keeps going. */
  const admittedStreams: Record<MockAccountKey, Set<string>> = { A: new Set(), B: new Set(), C: new Set() };
  const asrAdmits = (account: MockAccountKey, streamId: string) => {
    if (admittedStreams[account].has(streamId)) return true;
    // Live ASR checks its own switch (and the billing master), not the group summary.
    if (!admin.serviceEnabled('asr_streaming_enabled')) return false;
    admittedStreams[account].add(streamId);
    return true;
  };
  const accountOf = (userId: string) => accounts.find(account => stores[account].user.id === userId) ?? null;
  // The platform operations backend sees only each account's user record and quota buckets, never its study records.
  const admin = createAdminBackend({
    now, newId, clockMs: options.clockMs ?? (() => Date.now()),
    accountUser: userId => { const account = accountOf(userId); return account ? stores[account].user : null; },
    setAccountUser: user => { const account = accountOf(user.id); if (account) stores[account].user = user; },
    accountQuota: userId => { const account = accountOf(userId); return account ? stores[account].quota : null; },
    setAccountQuota: (userId, value) => { const account = accountOf(userId); if (account) stores[account].quota = value; },
    applySwitches: () => { for (const account of accounts) stores[account].quota = admin.gate(stores[account].quota); },
    sessionGeneration: userId => { const account = accountOf(userId); return account ? generations[account] : 0; },
    revokeSessions: userId => {
      const account = accountOf(userId);
      if (!account) return;
      generations[account] += 1;
      revokedSessions.add(account);
      adminRevoked.add(account);
      stores[account].deviceSessions = stores[account].deviceSessions.map(item => item.revoked_at ? { ...item, is_current: false } : { ...item, is_current: false, revoked_at: now(), revision: item.revision + 1, updated_at: now() });
    },
  }, accounts.map(account => stores[account].user));
  for (const account of accounts) stores[account].quota = admin.gate(stores[account].quota);

  const error = (status: number, code: ErrorCode, message: string, currentRevision: number | null = null): MockReply =>
    ({ status, body: { error: { code, message, request_id: newId(), retryable: false, retry_after_seconds: null, current_revision: currentRevision, conflict_id: null, task_id: null } } });
  const ok = (body: unknown, status = 200): MockReply => ({ status, body });
  const page = <T>(items: T[]) => ok({ items, next_cursor: null });
  const touch = <T extends { revision: number; updated_at: string }>(value: T): T => ({ ...value, revision: value.revision + 1, updated_at: now() });

  function liveCourses(store: Store) { return store.courses.filter(course => !store.deletedCourses.has(course.id)); }
  function liveSessions(store: Store) { return store.sessions.filter(item => item.deleted_at === null); }
  function courseView(store: Store, course: Store['courses'][number]): S['Course'] {
    const owned = liveSessions(store).filter(item => item.course_id === course.id);
    const last = owned.map(item => item.started_at).filter((v): v is string => v !== null).sort().at(-1) ?? null;
    return { ...course, class_session_count: owned.length, last_class_session_at: last };
  }
  function findCourse(store: Store, id: string) { return liveCourses(store).find(course => course.id === id); }
  function findSemester(store: Store, id: string) { return store.semesters.find(item => item.id === id && !store.deletedSemesters.has(item.id)); }
  const str = (value: unknown, max: number, min = 0) => typeof value === 'string' && value.length <= max && value.trim().length >= min;
  const date = (value: unknown) => value === null || value === undefined || (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value));
  const languages = ['auto', 'zh', 'en', 'mixed'];
  const only = (body: Record<string, unknown>, keys: string[]) => Object.keys(body).every(key => keys.includes(key));

  function idempotent(store: Store, key: string | null, scope: string, body: unknown, run: () => MockReply): MockReply {
    if (!key) return error(400, 'VALIDATION_FAILED', '缺少幂等键');
    const serialized = JSON.stringify(body);
    const prior = store.idempotency.get(`${scope}:${key}`);
    if (prior) return prior.body === serialized ? prior.reply : error(409, 'IDEMPOTENCY_MISMATCH', '同一操作键对应了不同内容');
    const reply = run();
    if (reply.status < 500) store.idempotency.set(`${scope}:${key}`, { body: serialized, reply });
    return reply;
  }
  function cas<T extends { revision: number }>(current: T, body: Record<string, unknown>): MockReply | null {
    if (!Number.isInteger(body.base_revision) || (body.base_revision as number) < 1) return error(400, 'VALIDATION_FAILED', '缺少基础版本');
    if (body.base_revision !== current.revision) return error(409, 'REVISION_CONFLICT', '内容已在其他地方更新', current.revision);
    return null;
  }
  function validateSemesterBody(body: Record<string, unknown>, partial: boolean) {
    if (!partial && !str(body.name, 100, 1)) return false;
    if (partial && 'name' in body && !str(body.name, 100, 1)) return false;
    if ('academic_year' in body && body.academic_year !== null && !str(body.academic_year, 30)) return false;
    if (!date(body.starts_on) || !date(body.ends_on)) return false;
    return true;
  }
  function validateCourseBody(body: Record<string, unknown>, partial: boolean) {
    if (!partial && (!str(body.name, 160, 1) || typeof body.semester_id !== 'string')) return false;
    if ('name' in body && !str(body.name, 160, 1)) return false;
    if ('english_name' in body && !str(body.english_name, 200)) return false;
    if ('teacher' in body && !str(body.teacher, 100)) return false;
    if ('room' in body && !str(body.room, 100)) return false;
    if ('language' in body && !languages.includes(String(body.language))) return false;
    if ('color_token' in body && !str(body.color_token, 64)) return false;
    if ('icon_token' in body && !str(body.icon_token, 64)) return false;
    return true;
  }
  /** What a scope deletion would remove right now: its courses (semester) and classrooms. */
  function impactOf(store: Store, scope: 'semester' | 'course', id: string) {
    const courseIds = scope === 'course' ? [id] : liveCourses(store).filter(course => course.semester_id === id).map(course => course.id);
    const sessionIds = liveSessions(store).filter(item => courseIds.includes(item.course_id)).map(item => item.id);
    return JSON.stringify([[...courseIds].sort(), sessionIds.sort(), moves(store, `${scope}:${id}`)]);
  }
  /**
   * Moves into or out of a scope since its preview, counted per course and semester: a classroom that moved in and
   * back out leaves the same members but may have been listed in between, so the token expires too.
   */
  const scopeMoves = new WeakMap<Store, Map<string, number>>();
  const moves = (store: Store, key: string) => scopeMoves.get(store)?.get(key) ?? 0;
  function countMove(store: Store, courseIds: string[], semesterIds: string[]) {
    const counts = scopeMoves.get(store) ?? new Map<string, number>();
    scopeMoves.set(store, counts);
    const semesters = new Set([...semesterIds, ...courseIds.map(courseId => findCourse(store, courseId)?.semester_id).filter((value): value is string => typeof value === 'string')]);
    for (const key of [...courseIds.map(courseId => `course:${courseId}`), ...[...semesters].map(semesterId => `semester:${semesterId}`)]) counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  /** The confirmation token is bound to the object's revision and its impact scope (04-CONTRACTS-FLOWS: 删除). */
  const previewImpact = new WeakMap<S['DeletionPreview'], string>();
  function issuePreview(store: Store, scope: 'semester' | 'course', id: string, revision: number, sessionCount: number): MockReply {
    const preview: S['DeletionPreview'] = { scope_type: scope, scope_id: id, revision, class_session_count: sessionCount, media_bytes: sessionCount * 48000000, confirmation_token: `mock-confirm-${newId()}`, expires_at: new Date(Date.parse(now()) + 10 * 60000).toISOString().replace('.000Z', 'Z') };
    store.previews.set(`${scope}:${id}`, preview);
    previewImpact.set(preview, impactOf(store, scope, id));
    return ok(preview);
  }
  function deletion(scope: 'semester' | 'course', id: string): S['Deletion'] {
    return { ...base(newId(), now()), scope_type: scope, scope_id: id, state: 'access_revoked', access_revoked_at: now(), physical_completed_at: null, failure_code: null };
  }
  function checkDeleteBody(store: Store, scope: 'semester' | 'course', id: string, current: { revision: number }, body: Record<string, unknown>): MockReply | null {
    const preview = store.previews.get(`${scope}:${id}`);
    if (!preview || preview.confirmation_token !== body.confirmation_token) return error(409, 'PLAN_EXPIRED', '删除确认已失效，请重新查看影响范围');
    // A classroom or course moved into or out of the scope since the preview changes what would be removed.
    if (previewImpact.get(preview) !== impactOf(store, scope, id)) return error(409, 'PLAN_EXPIRED', '删除范围已变化，请重新查看影响范围');
    if (body.base_revision !== current.revision) return error(409, 'REVISION_CONFLICT', '内容已在其他地方更新', current.revision);
    if (body.mode !== 'cascade' && body.mode !== 'move_children') return error(400, 'VALIDATION_FAILED', '删除方式无效');
    return null;
  }

  function classroomHost(account: MockAccountKey): ClassroomHost {
    const store = stores[account];
    return {
      sessions: () => store.sessions,
      saveSession: next => { store.sessions = store.sessions.map(item => item.id === next.id ? next : item); },
      addSession: next => { store.sessions = [...store.sessions, next]; },
      courseExists: id => Boolean(findCourse(store, id)),
      firstCourseId: () => liveCourses(store).find(course => !course.archived)?.id ?? null,
      quota: () => store.quota,
      service: switch_ => admin.serviceEnabled(switch_),
      addQuestion: question => { store.questions = [...store.questions, question]; },
      updateQuestion: (taskId, change) => { store.questions = store.questions.map(item => item.task_id === taskId ? { ...item, ...change, revision: item.revision + 1, updated_at: now() } : item); },
      addDocument: document => { store.documents = [...store.documents, document]; },
      document: id => store.documents.find(item => item.id === id),
      addCandidate: input => addCandidate(reviews[account], input),
    };
  }
  function reviewHost(account: MockAccountKey): ReviewHost {
    const store = stores[account];
    return {
      documents: () => store.documents,
      saveDocument: next => { store.documents = store.documents.map(item => item.id === next.id ? next : item); },
      addDocument: next => { store.documents = [...store.documents, next]; },
      visibleSessions: () => liveSessions(store).flatMap(session => { const course = findCourse(store, session.course_id); return course ? [{ session, courseId: course.id, semesterId: course.semester_id }] : []; }),
      questions: () => store.questions,
      saveQuestion: next => { store.questions = store.questions.map(item => item.id === next.id ? next : item); },
      sourceState: ref => {
        const room = classrooms[account];
        if (ref.kind === 'transcript_segment') {
          const segment = room.segments.find(item => item.id === ref.id);
          if (!segment) return 'missing';
          // A source outside the transcript generation now reads (another version was adopted) also counts as changed.
          const session = store.sessions.find(item => item.id === segment.class_session_id);
          return segment.segment_revision_id === ref.revision_id && (!session || activeVersionIds(room, session).has(segment.transcript_version_id)) ? 'current' : 'changed';
        }
        if (ref.kind === 'slide_version') { const slide = room.slides.find(item => item.id === ref.id); return !slide || slide.removed ? 'missing' : slide.version_id === ref.revision_id ? 'current' : 'changed'; }
        return room.marks.some(item => item.id === ref.id) ? 'current' : 'missing';
      },
    };
  }
  function reviewHelpers(account: MockAccountKey): ReviewHelpers {
    return { now, newId, error, idempotent: (key, scope, body, run) => idempotent(stores[account], key, scope, body, run) };
  }
  function classroomHelpers(account: MockAccountKey): ClassroomHelpers {
    return { now, newId, error, idempotent: (key, scope, body, run) => idempotent(stores[account], key, scope, body, run) };
  }

  function handle(account: MockAccountKey, method: string, path: string, query: URLSearchParams, rawBody: unknown, key: string | null, reauth: string | null = null, generation?: number): MockReply | null {
    const isId = (part: string) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(part);
    if (['POST', 'PATCH', 'PUT', 'DELETE'].includes(method) && (!key || !isId(key))) {
      return error(422, 'VALIDATION_FAILED', 'Idempotency-Key 必须为小写且带连字符的 UUID');
    }
    const store = stores[account];
    // A request dispatched by a session that has since ended is refused, even if the account signed in again.
    if (generation !== undefined && generation !== generations[account]) return error(401, 'UNAUTHENTICATED', '会话已失效');
    // A disabled (or deleting) account's sessions are revoked: every request is unauthenticated from then on.
    if (store.user.status !== 'active' || revokedSessions.has(account)) return error(401, 'UNAUTHENTICATED', '会话已失效');
    const body = (typeof rawBody === 'object' && rawBody !== null && !Array.isArray(rawBody) ? rawBody : {}) as Record<string, unknown>;
    const parts = path.split('/').filter(Boolean);
    const route = `${method} /${parts.map(part => (isId(part) ? ':id' : part)).join('/')}`;
    const id = parts.find(isId) ?? '';
    if (parts[0] === 'admin') return admin.handle(store.user.id, method, parts, query, rawBody, key, reauth);
    // HTTP 0.4.0: purpose-bound reauthentication for the admin writes (the Mock serves only those purposes).
    if (method === 'POST' && path === '/auth/reauth') return admin.reauthenticate(store.user.id, rawBody);

    switch (route) {
      case 'GET /me': return ok(store.user);
      case 'PATCH /me': {
        if (!only(body, ['base_revision', 'nickname', 'school', 'major']) || ('nickname' in body && !str(body.nickname, 100)) || ('school' in body && !str(body.school, 200)) || ('major' in body && !str(body.major, 200))) return error(400, 'VALIDATION_FAILED', '资料格式无效');
        const conflict = cas(store.user, body); if (conflict) return conflict;
        const { base_revision: _ignored, ...change } = body; void _ignored;
        store.user = touch({ ...store.user, ...change });
        return ok(store.user);
      }
      case 'GET /me/preferences': return ok(store.preferences);
      case 'PATCH /me/preferences': {
        if (!only(body, ['base_revision', 'theme', 'reduced_effects', 'reading_font_px', 'explanation_language', 'automatic_explanation'])) return error(400, 'VALIDATION_FAILED', '偏好格式无效');
        if ('reading_font_px' in body && (!Number.isInteger(body.reading_font_px) || (body.reading_font_px as number) < 14 || (body.reading_font_px as number) > 32)) return error(400, 'VALIDATION_FAILED', '字号超出范围');
        if (body.base_revision !== store.preferences.revision) return error(409, 'REVISION_CONFLICT', '偏好已在其他设备更新', store.preferences.revision);
        const { base_revision: _ignored, ...change } = body; void _ignored;
        store.preferences = { ...store.preferences, ...change, revision: store.preferences.revision + 1 } as S['Preferences'];
        return ok(store.preferences);
      }
      case 'GET /me/quota': return ok(store.quota);
      case 'GET /me/sessions': return page(store.deviceSessions);
      case 'GET /me/devices': return page(store.devices);
      case 'POST /me/sessions/:id/revoke': {
        const target = store.deviceSessions.find(item => item.id === id);
        if (!target) return error(404, 'NOT_FOUND', '会话不存在');
        store.deviceSessions = store.deviceSessions.map(item => item.id === target.id ? touch({ ...item, revoked_at: now() }) : item);
        return ok({ accepted: true, request_id: newId() });
      }
      case 'POST /me/devices/:id/revoke': {
        const target = store.devices.find(item => item.id === id);
        if (!target) return error(404, 'NOT_FOUND', '设备不存在');
        store.devices = store.devices.map(item => item.id === target.id ? touch({ ...item, trusted: false, revoked_at: now() }) : item);
        return ok({ accepted: true, request_id: newId() });
      }

      case 'GET /semesters': return page(store.semesters.filter(item => !store.deletedSemesters.has(item.id)));
      case 'POST /semesters': return idempotent(store, key, 'semester', body, () => {
        if (!only(body, ['name', 'academic_year', 'starts_on', 'ends_on']) || !validateSemesterBody(body, false)) return error(400, 'VALIDATION_FAILED', '学期信息无效');
        if (body.starts_on && body.ends_on && String(body.ends_on) < String(body.starts_on)) return error(400, 'VALIDATION_FAILED', '结束日期早于开始日期');
        const created: S['Semester'] = { ...base(newId(), now()), name: String(body.name).trim(), academic_year: (body.academic_year as string | null) ?? null, starts_on: (body.starts_on as string | null) ?? null, ends_on: (body.ends_on as string | null) ?? null, archived: false };
        store.semesters.push(created);
        return ok(created, 201);
      });
      case 'GET /semesters/:id': { const found = findSemester(store, id); return found ? ok(found) : error(404, 'NOT_FOUND', '学期不存在'); }
      case 'PATCH /semesters/:id': {
        const found = findSemester(store, id); if (!found) return error(404, 'NOT_FOUND', '学期不存在');
        if (!only(body, ['base_revision', 'name', 'academic_year', 'starts_on', 'ends_on', 'archived']) || !validateSemesterBody(body, true)) return error(400, 'VALIDATION_FAILED', '学期信息无效');
        const conflict = cas(found, body); if (conflict) return conflict;
        const { base_revision: _ignored, ...change } = body; void _ignored;
        const next = touch({ ...found, ...change }) as S['Semester'];
        if (next.starts_on && next.ends_on && next.ends_on < next.starts_on) return error(400, 'VALIDATION_FAILED', '结束日期早于开始日期');
        store.semesters = store.semesters.map(item => item.id === id ? next : item);
        return ok(next);
      }
      case 'GET /semesters/:id/deletion-preview': {
        const found = findSemester(store, id); if (!found) return error(404, 'NOT_FOUND', '学期不存在');
        const courseIds = new Set(liveCourses(store).filter(course => course.semester_id === id).map(course => course.id));
        return issuePreview(store, 'semester', id, found.revision, liveSessions(store).filter(item => courseIds.has(item.course_id)).length);
      }
      case 'POST /semesters/:id/delete': {
        const found = findSemester(store, id); if (!found) return error(404, 'NOT_FOUND', '学期不存在');
        return idempotent(store, key, `semester-delete:${id}`, body, () => {
          const bad = checkDeleteBody(store, 'semester', id, found, body); if (bad) return bad;
          const children = liveCourses(store).filter(course => course.semester_id === id);
          if (body.mode === 'move_children') {
            const target = typeof body.destination_id === 'string' ? findSemester(store, body.destination_id) : undefined;
            if (!target || target.id === id) return error(400, 'VALIDATION_FAILED', '请选择另一个学期接收课程');
            store.courses = store.courses.map(course => course.semester_id === id ? touch({ ...course, semester_id: target.id }) : course);
          } else {
            for (const course of children) store.deletedCourses.add(course.id);
            const removed = new Set(children.map(course => course.id));
            store.sessions = store.sessions.map(item => removed.has(item.course_id) && item.deleted_at === null ? { ...item, deleted_at: now() } : item);
          }
          store.deletedSemesters.add(id);
          store.previews.delete(`semester:${id}`);
          return ok(deletion('semester', id), 202);
        });
      }

      case 'GET /courses': {
        const semester = query.get('semester_id');
        const archived = query.get('archived');
        const q = (query.get('q') ?? '').trim().toLowerCase();
        const sort = query.get('sort') ?? 'recent';
        let items = liveCourses(store).map(course => courseView(store, course));
        if (semester) items = items.filter(course => course.semester_id === semester);
        items = items.filter(course => archived === 'true' ? course.archived : archived === 'false' || archived === null ? !course.archived : true);
        if (q) items = items.filter(course => course.name.toLowerCase().includes(q) || course.english_name.toLowerCase().includes(q));
        items.sort(sort === 'name'
          ? (a, b) => a.name.localeCompare(b.name, 'zh-CN') || (a.id < b.id ? -1 : 1)
          : (a, b) => (b.last_class_session_at ?? '').localeCompare(a.last_class_session_at ?? '') || b.updated_at.localeCompare(a.updated_at) || (a.id < b.id ? -1 : 1));
        return page(items);
      }
      case 'POST /courses': return idempotent(store, key, 'course', body, () => {
        if (!only(body, ['semester_id', 'name', 'english_name', 'language', 'teacher', 'room', 'color_token', 'icon_token']) || !validateCourseBody(body, false)) return error(400, 'VALIDATION_FAILED', '课程信息无效');
        if (!findSemester(store, String(body.semester_id))) return error(404, 'NOT_FOUND', '学期不存在');
        const created = { ...base(newId(), now()), semester_id: String(body.semester_id), name: String(body.name).trim(), english_name: String(body.english_name ?? ''), language: (body.language ?? 'auto') as S['Course']['language'],
          teacher: String(body.teacher ?? ''), room: String(body.room ?? ''), color_token: String(body.color_token ?? 'blue'), icon_token: String(body.icon_token ?? 'book'), archived: false };
        store.courses.push(created);
        // Assumption recorded in PR09B-SCOPE-GAPS §3.1: the server creates an empty revision-1 material.
        store.materials.push(material(newId(), created.id, now()));
        return ok(courseView(store, created), 201);
      });
      case 'GET /courses/:id': {
        const found = findCourse(store, id); if (!found) return error(404, 'NOT_FOUND', '课程不存在');
        const revision = query.get('revision');
        if (revision !== null && Number(revision) !== found.revision) return error(409, 'REVISION_CONFLICT', '课程已更新', found.revision);
        return ok(courseView(store, found));
      }
      case 'PATCH /courses/:id': {
        const found = findCourse(store, id); if (!found) return error(404, 'NOT_FOUND', '课程不存在');
        if (!only(body, ['base_revision', 'semester_id', 'name', 'english_name', 'language', 'teacher', 'room', 'color_token', 'icon_token', 'archived']) || !validateCourseBody(body, true)) return error(400, 'VALIDATION_FAILED', '课程信息无效');
        if ('semester_id' in body && !findSemester(store, String(body.semester_id))) return error(404, 'NOT_FOUND', '学期不存在');
        const conflict = cas(found, body); if (conflict) return conflict;
        const { base_revision: _ignored, ...change } = body; void _ignored;
        if ('semester_id' in change && change.semester_id !== found.semester_id) countMove(store, [], [found.semester_id, String(change.semester_id)]);
        const next = touch({ ...found, ...change }) as Store['courses'][number];
        store.courses = store.courses.map(course => course.id === id ? next : course);
        return ok(courseView(store, next));
      }
      case 'GET /courses/:id/deletion-preview': {
        const found = findCourse(store, id); if (!found) return error(404, 'NOT_FOUND', '课程不存在');
        return issuePreview(store, 'course', id, found.revision, liveSessions(store).filter(item => item.course_id === id).length);
      }
      case 'POST /courses/:id/delete': {
        const found = findCourse(store, id); if (!found) return error(404, 'NOT_FOUND', '课程不存在');
        return idempotent(store, key, `course-delete:${id}`, body, () => {
          const bad = checkDeleteBody(store, 'course', id, found, body); if (bad) return bad;
          if (body.mode === 'move_children') {
            const target = typeof body.destination_id === 'string' ? findCourse(store, body.destination_id) : undefined;
            if (!target || target.id === id) return error(400, 'VALIDATION_FAILED', '请选择另一门课程接收课堂');
            // Moving keeps the original start/capture times and history; only ownership changes.
            store.sessions = store.sessions.map(item => item.course_id === id && item.deleted_at === null ? touch({ ...item, course_id: target.id }) : item);
          } else {
            store.sessions = store.sessions.map(item => item.course_id === id && item.deleted_at === null ? { ...item, deleted_at: now() } : item);
          }
          store.deletedCourses.add(id);
          store.previews.delete(`course:${id}`);
          return ok(deletion('course', id), 202);
        });
      }
      case 'GET /courses/:id/materials': {
        if (!findCourse(store, id)) return error(404, 'NOT_FOUND', '课程不存在');
        return page(store.materials.filter(item => item.course_id === id));
      }
      case 'POST /courses/:id/materials': {
        if (!findCourse(store, id)) return error(404, 'NOT_FOUND', '课程不存在');
        const current = store.materials.find(item => item.course_id === id);
        if (!current) return error(404, 'NOT_FOUND', '课程资料不存在');
        if (!only(body, ['base_revision', 'syllabus', 'glossary', 'language']) || !str(body.syllabus, 120000) || !Array.isArray(body.glossary) || body.glossary.length > 2000 || !languages.includes(String(body.language))
          || !(body.glossary as unknown[]).every(term => typeof term === 'object' && term !== null && str((term as Record<string, unknown>).term, 160, 1) && str((term as Record<string, unknown>).meaning, 1000) && languages.includes(String((term as Record<string, unknown>).language))))
          return error(400, 'VALIDATION_FAILED', '资料格式无效');
        const conflict = cas(current, body); if (conflict) return conflict;
        // Like the backend's update_material: every write appends a new immutable version with its own ID.
        const stamp = now();
        const next: S['CourseMaterial'] = { ...current, id: newId(), revision: current.revision + 1, created_at: stamp, updated_at: stamp, syllabus: String(body.syllabus), glossary: body.glossary as S['GlossaryTerm'][], language: body.language as S['CourseMaterial']['language'], content_sha256: sha(current.revision + 100) };
        store.materials = store.materials.map(item => item.id === current.id ? next : item);
        return ok(next, 201);
      }

      case 'GET /class-sessions': {
        const course = query.get('course_id');
        const live = new Set(liveCourses(store).map(item => item.id));
        return page(liveSessions(store).filter(item => live.has(item.course_id) && (!course || item.course_id === course)));
      }
      case 'GET /class-sessions/:id': { const found = liveSessions(store).find(item => item.id === id); return found ? ok(found) : error(404, 'NOT_FOUND', '课堂不存在'); }
      case 'PATCH /class-sessions/:id': {
        const found = liveSessions(store).find(item => item.id === id); if (!found) return error(404, 'NOT_FOUND', '课堂不存在');
        if (!only(body, ['base_revision', 'course_id', 'title', 'language']) || ('title' in body && !str(body.title, 200)) || ('language' in body && !languages.includes(String(body.language)))) return error(400, 'VALIDATION_FAILED', '课堂信息无效');
        if ('course_id' in body && !findCourse(store, String(body.course_id))) return error(404, 'NOT_FOUND', '目标课程不存在');
        const conflict = cas(found, body); if (conflict) return conflict;
        const { base_revision: _ignored, ...change } = body; void _ignored;
        if ('course_id' in change && change.course_id !== found.course_id) countMove(store, [found.course_id, String(change.course_id)], []);
        // started_at / capture_ended_at / flows are history and never rewritten by a move.
        const next = touch({ ...found, ...change }) as S['ClassSession'];
        store.sessions = store.sessions.map(item => item.id === id ? next : item);
        return ok(next);
      }
      case 'GET /questions': {
        const sessionId = query.get('class_session_id');
        const sessions = new Set(liveSessions(store).filter(item => findCourse(store, item.course_id)).map(item => item.id));
        return page(store.questions.filter(item => sessions.has(item.class_session_id) && (!sessionId || item.class_session_id === sessionId)));
      }
      case 'GET /notifications': return page(store.notifications);
      default: return handleReview(reviews[account], reviewHost(account), reviewHelpers(account), method, route, id, query, body, key)
        ?? handleClassroom(classrooms[account], classroomHost(account), classroomHelpers(account), method, route, id, query, body, key);
    }
  }

  return {
    handle,
    reset(account: MockAccountKey, dataset: MockDataset = 'seeded') {
      // Learning fixtures are reseeded; admin-owned account state (status, switches, quota adjustments) stays.
      const previous = stores[account];
      const seeded = account === 'A' ? seedA(dataset) : account === 'B' ? seedB() : seedC();
      stores[account] = { ...seeded, user: previous.user, quota: admin.reapply(previous.user.id, seeded.quota), deviceSessions: adminRevoked.has(account) ? previous.deviceSessions : seeded.deviceSessions };
      classrooms[account] = withHistory(account, account === 'A' && dataset === 'seeded' ? seededClassroom() : seedClassroom(null));
      reviews[account] = seedReview(account === 'A' && dataset === 'seeded');
    },
    /** PR-11b development stand-in for the missing admin reauthentication purpose (contract gap §1); not an HTTP route. */
    issueAdminProof: (account: MockAccountKey, binding: AdminReauthBinding, password: string, code: string) => admin.issueProof(stores[account].user.id, binding, password, code),
    setAdminHealth: (value: AdminHealthDev) => admin.setHealthDev(value),
    /** The account's Mock session ended (sign-out, switch, revocation): its admin proofs are no longer valid. */
    retireSession: (account: MockAccountKey) => { generations[account] += 1; },
    /**
     * A fresh Mock sign-in. After an administrator ended the account's sessions it gets a new current session
     * row; the revoked rows stay as history (a restore never revives them).
     */
    beginSession: (account: MockAccountKey) => {
      if (!revokedSessions.delete(account)) return;
      const store = stores[account];
      const device = store.deviceSessions[0]?.device_id ?? newId();
      store.deviceSessions = [{ ...base(newId(), now()), device_id: device, device_name: '本机浏览器（模拟）', last_active_at: now(), expires_at: new Date(Date.parse(now()) + 30 * 86_400_000).toISOString().replace('.000Z', 'Z'), is_current: true, revoked_at: null }, ...store.deviceSessions.map(item => ({ ...item, is_current: false }))];
    },
    sessionGeneration: (account: MockAccountKey) => generations[account],
    /** PR-11b: the platform ASR switch admits new live streams; a stream admitted before keeps going. */
    asrAdmits,
    /** Development panel: the administrator has (or has not) enrolled MFA. */
    setAdminMfa: (enabled: boolean) => { stores.C.user = { ...stores.C.user, mfa_enabled: enabled }; },
    adminAudit: () => admin.audit(),
    accountUser: (account: MockAccountKey) => stores[account].user,
    /** PR-10a development helpers; none of them is an HTTP route. */
    advanceTask: (account: MockAccountKey, taskId: string, outcome: TaskOutcome) => advanceTask(classrooms[account], classroomHost(account), classroomHelpers(account), taskId, outcome),
    finishAsr: (account: MockAccountKey, classId: string, state: 'succeeded' | 'failed' | 'unknown') => {
      const done = finishAsr(classroomHost(account), classroomHelpers(account), classId, state);
      // A finished realtime stream seals its versions, so they can be adopted (switched back to) later.
      if (done && state === 'succeeded') classrooms[account].versions = classrooms[account].versions.map(item => item.class_session_id === classId && item.kind === 'realtime' && item.state === 'open' ? { ...item, state: 'sealed', revision: item.revision + 1, updated_at: now() } : item);
      return done;
    },
    persistFinal: (account: MockAccountKey, input: Parameters<typeof persistFinal>[3]) => {
      if (!asrAdmits(account, input.streamId)) return;
      persistFinal(classrooms[account], classroomHost(account), classroomHelpers(account), input);
    },
    ensureDemoClassroom: (account: MockAccountKey, input: Parameters<typeof ensureDemoClassroom>[3]) => ensureDemoClassroom(classrooms[account], classroomHost(account), classroomHelpers(account), input),
    setDurable: (account: MockAccountKey, classId: string, captureEpochId: string, seqs: number[]) => recordDurable(classrooms[account], classroomHost(account), classroomHelpers(account), classId, captureEpochId, seqs),
    releaseLease: (account: MockAccountKey, classId: string) => releaseLease(classrooms[account], classroomHost(account), classroomHelpers(account), classId),
    releaseAccountLeases: (account: MockAccountKey) => releaseAccountLeases(classrooms[account], classroomHost(account), classroomHelpers(account)),
    projectTask: (account: MockAccountKey, input: Parameters<typeof projectTask>[3]) => projectTask(classrooms[account], classroomHost(account), classroomHelpers(account), input),
    revokeClassroom: (account: MockAccountKey, classId: string) => revokeClassroom(classrooms[account], classroomHost(account), classroomHelpers(account), classId),
    classroomStore: (account: MockAccountKey) => classrooms[account],
    reviewStore: (account: MockAccountKey) => reviews[account],
    /** Test helper: refuse a document's revision reads (FORBIDDEN) while its metadata stays readable. */
    denyReviewRevisions: (account: MockAccountKey, documentId: string) => { (reviews[account].deniedRevisionDocs ??= new Set()).add(documentId); },
    /** PR-10b development panel: retranscription refusals by original-audio state and quota. */
    setRetranscribeDev: (account: MockAccountKey, value: Parameters<typeof setRetranscribeDev>[1]) => setRetranscribeDev(classrooms[account], value),
    expireManifestCursors: (account: MockAccountKey) => expireManifestCursors(classrooms[account]),
    setManifestDev: (account: MockAccountKey, value: Parameters<typeof setManifestDev>[1]) => setManifestDev(classrooms[account], value),
    revokeAudio: (account: MockAccountKey, classId: string, captureEpochId: string) => revokeAudio(classrooms[account], classId, captureEpochId),
    /** Test helper: simulate a write by "another device" so CAS conflicts are reproducible. */
    bumpElsewhere(account: MockAccountKey, kind: 'semester' | 'course' | 'class_session' | 'preferences', id: string, change: Record<string, unknown> = {}) {
      const store = stores[account];
      if (kind === 'preferences') store.preferences = { ...store.preferences, ...change, revision: store.preferences.revision + 1 } as S['Preferences'];
      if (kind === 'semester') store.semesters = store.semesters.map(item => item.id === id ? touch({ ...item, ...change }) : item);
      if (kind === 'course') store.courses = store.courses.map(item => item.id === id ? touch({ ...item, ...change }) : item);
      if (kind === 'class_session') store.sessions = store.sessions.map(item => item.id === id ? touch({ ...item, ...change }) : item);
    },
  };
}
export type MockBackend = ReturnType<typeof createMockBackend>;
