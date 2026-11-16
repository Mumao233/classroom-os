import type { components } from '../generated/openapi';
import { flowItems } from './flows';

type S = components['schemas'];
export type ClassSession = S['ClassSession'];
export type Course = S['Course'];
export type ReviewDocument = S['ReviewDocument'];
export type Question = S['Question'];
export type Notification = S['Notification'];

/** Runtime facts that only the account runtime knows (not server records). */
export type CaptureView = { active: boolean; classSessionId: string | null; title: string | null };
export type LocalRisk = { kind: 'unsaved' | 'local_only'; detail: string } | null;

export type ContinueEntry =
  | { kind: 'capture'; classSessionId: string | null; title: string | null }
  | { kind: 'local_risk'; risk: NonNullable<LocalRisk> }
  | { kind: 'class_session'; session: ClassSession; course: Course }
  | { kind: 'note'; document: ReviewDocument; session: ClassSession; course: Course }
  | { kind: 'none' };

const time = (value: string | null | undefined) => (value ? Date.parse(value) : Number.NEGATIVE_INFINITY);
/** A class's learning time is when it actually started; created_at only for never-started records. */
export const sessionTime = (session: ClassSession) => time(session.started_at ?? session.created_at);

/** Live, owned records only: deleted classes and classes of removed/unknown courses are dropped. */
export function liveSessions(sessions: readonly ClassSession[], courses: readonly Course[]) {
  const ids = new Set(courses.map(course => course.id));
  return sessions.filter(session => session.deleted_at === null && ids.has(session.course_id));
}

/** Most recent real classes first; equal times fall back to id so the order is deterministic. */
export function recentSessions(sessions: readonly ClassSession[], courses: readonly Course[], limit = 5) {
  return liveSessions(sessions, courses)
    .sort((a, b) => sessionTime(b) - sessionTime(a) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    .slice(0, limit);
}

/**
 * UI-DASH-04 deterministic priority: active capture → local unsaved risk → the newer of the most
 * recent real class and the most recent personal note. Ties prefer the class, then id.
 */
export function chooseContinueEntry(input: { capture: CaptureView; localRisk: LocalRisk; sessions: readonly ClassSession[]; courses: readonly Course[]; documents: readonly ReviewDocument[] }): ContinueEntry {
  if (input.capture.active) return { kind: 'capture', classSessionId: input.capture.classSessionId, title: input.capture.title };
  if (input.localRisk) return { kind: 'local_risk', risk: input.localRisk };
  const sessions = liveSessions(input.sessions, input.courses);
  const byId = new Map(sessions.map(session => [session.id, session]));
  const courseById = new Map(input.courses.map(course => [course.id, course]));
  const [session] = recentSessions(sessions, input.courses, 1);
  const notes = input.documents
    .filter(document => document.kind === 'note' && document.latest_personal_revision_id !== null && byId.has(document.class_session_id))
    .sort((a, b) => time(b.updated_at) - time(a.updated_at) || (a.id < b.id ? -1 : 1));
  const note = notes[0];
  if (note && (!session || time(note.updated_at) > sessionTime(session))) {
    const noteSession = byId.get(note.class_session_id)!;
    return { kind: 'note', document: note, session: noteSession, course: courseById.get(noteSession.course_id)! };
  }
  if (session) return { kind: 'class_session', session, course: courseById.get(session.course_id)! };
  return { kind: 'none' };
}

export type TodoKind = 'conflict' | 'failed' | 'unknown' | 'unsynced' | 'expiring' | 'not_understood';
export type Todo = { id: string; kind: TodoKind; title: string; detail: string; to: string };
const todoRank: Record<TodoKind, number> = { conflict: 0, failed: 1, unknown: 2, unsynced: 3, expiring: 4, not_understood: 5 };
/** Audio approaching its automatic deletion. A product parameter, displayed as such. */
export const EXPIRY_WARNING_MS = 48 * 60 * 60 * 1000;

/**
 * UI-DASH-06: only real items derived from current records. A resolved state is simply no
 * longer derived, so the item disappears without a separate "dismissed" store.
 */
export function deriveTodos(input: { sessions: readonly ClassSession[]; courses: readonly Course[]; questions: readonly Question[]; now: number }): Todo[] {
  const courseById = new Map(input.courses.map(course => [course.id, course]));
  const sessions = liveSessions(input.sessions, input.courses);
  const sessionById = new Map(sessions.map(session => [session.id, session]));
  const todos: Array<Todo & { sort: number }> = [];
  for (const session of sessions) {
    const label = `${courseById.get(session.course_id)!.name} · ${session.title || '未命名课堂'}`;
    const to = `/classrooms/${session.id}/processing`;
    const { flows } = session;
    if (flows.sync === 'conflict') todos.push({ id: `${session.id}:conflict`, kind: 'conflict', title: label, detail: '版本冲突：两个版本均保留，需要比较后处理', to, sort: 0 });
    if (flows.sync === 'failed' || flows.sync === 'local_only') todos.push({ id: `${session.id}:sync`, kind: 'unsynced', title: label, detail: flows.sync === 'failed' ? '同步失败，内容尚未到达云端' : '仅保存在本机，尚未同步', to, sort: 0 });
    const failed = flowItems(flows).filter(item => item.abnormal && item.tone === 'danger' && item.key !== 'sync');
    if (failed.length) todos.push({ id: `${session.id}:failed`, kind: 'failed', title: label, detail: failed.map(item => item.label).join('、'), to, sort: 0 });
    if (flows.asr === 'unknown' || flows.ai === 'unknown') todos.push({ id: `${session.id}:unknown`, kind: 'unknown', title: label, detail: flows.ai === 'unknown' ? 'AI 结果未知，可能已计费；先核对，不要直接重试' : '转写结果未知；先核对状态', to, sort: 0 });
    if (flows.upload === 'pending' && flows.capture === 'ended') todos.push({ id: `${session.id}:pending-upload`, kind: 'unsynced', title: label, detail: '录音已结束，仍有音频待上传', to, sort: 0 });
    const expires = time(session.audio_expires_at);
    if (expires > input.now && expires - input.now <= EXPIRY_WARNING_MS) todos.push({ id: `${session.id}:expiring`, kind: 'expiring', title: label, detail: `原音将于 ${formatDateTime(session.audio_expires_at!)} 到期删除`, to: `/classrooms/${session.id}/record`, sort: expires });
  }
  for (const question of input.questions) {
    const session = sessionById.get(question.class_session_id);
    if (!session || question.understood || question.state !== 'succeeded') continue;
    todos.push({ id: `question:${question.id}`, kind: 'not_understood', title: `${courseById.get(session.course_id)!.name} · ${session.title || '未命名课堂'}`, detail: `未懂：${question.question}`, to: `/classrooms/${session.id}/review`, sort: -time(question.updated_at) });
  }
  return todos.sort((a, b) => todoRank[a.kind] - todoRank[b.kind] || a.sort - b.sort || (a.id < b.id ? -1 : 1)).map(({ sort: _sort, ...todo }) => { void _sort; return todo; });
}

const notificationLabels: Record<Notification['kind'], string> = {
  task_completed: '处理任务已完成', conflict: '出现版本冲突', audio_expiry: '原音即将到期', capture_failure: '采集中断', save_failure: '保存失败', security: '账号安全提醒',
};
/** Activity from real notification records only; no synthetic streak/score items. */
export function activityItems(notifications: readonly Notification[], limit = 5) {
  return [...notifications].sort((a, b) => time(b.created_at) - time(a.created_at) || (a.id < b.id ? -1 : 1)).slice(0, limit)
    .map(item => ({ id: item.id, label: notificationLabels[item.kind], severity: item.severity, at: item.created_at, resolved: item.resolved_at !== null, read: item.read_at !== null }));
}

export function formatDateTime(value: string) {
  const date = new Date(value);
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Shanghai' }).format(date);
}
export function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'numeric', day: 'numeric', timeZone: 'Asia/Shanghai' }).format(new Date(value));
}
export function formatDuration(ms: number) {
  const minutes = Math.round(ms / 60000);
  if (minutes < 60) return `${minutes} 分钟`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`;
}
