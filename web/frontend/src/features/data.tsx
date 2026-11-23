import { createContext, useContext, type ReactNode } from 'react';
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiClientError, type ApiClient } from '../api/client';
import type { CaptureView, LocalRisk } from '../domain/dashboard';
import type { ClassroomShell } from './classroom';
import type { RevocationLedger } from './revocation';

/**
 * Everything a page needs from the authenticated account shell. Pages never import fetch,
 * MSW or runtime internals directly; the shell supplies this per account epoch.
 */
export type AppData = {
  api: ApiClient;
  /** Account + epoch; the first segments of every private query key. */
  accountKey: string;
  online: boolean;
  now: () => number;
  capture: CaptureView;
  localRisk: LocalRisk;
  /** Shown verbatim so mock data can never be mistaken for real records. */
  dataSourceLabel: string | null;
  /** Mock-only quick start (simulated classroom; no microphone, no charge). */
  onQuickStart?: () => void;
  /** Mock-only projection of a revoked classroom from scripted events (PR-09a scenario). */
  classroomRevoked?: boolean;
  /** PR-10a classroom shell (capture port, drafts, layout memory); absent where not provided. */
  classroom?: ClassroomShell;
  /** Account-shell revocation ledger: deletion receipts revoke cached content at once (REVOCATION-DESIGN.md). */
  revocation?: RevocationLedger;
};

const Context = createContext<AppData | null>(null);
export function AppDataProvider({ value, children }: { value: AppData; children: ReactNode }) { return <Context.Provider value={value}>{children}</Context.Provider>; }
export function useAppData() {
  const value = useContext(Context);
  if (!value) throw new Error('AppDataProvider missing');
  return value;
}

export const isTransportFailure = (error: unknown) => error instanceof ApiClientError && error.kind === 'network';
/**
 * Cached data a page may still show: only while its latest failure (if any) was the transport. Any
 * other failure (denial, expired session, unreadable reply) shows no private content.
 */
export function readableCache<T>(query: { data: T | undefined; isError: boolean; error: unknown }): T | undefined {
  return query.data !== undefined && (!query.isError || isTransportFailure(query.error)) ? query.data : undefined;
}
/** While a page shows a cached fallback after a failed refresh, every server write below it is disabled like offline. */
export function ReadOnlyWhile({ stale, children }: { stale: boolean; children: ReactNode }) {
  const app = useAppData();
  // Always the same provider element: switching read-only on or off must not remount (and reset) drafts below.
  return <Context.Provider value={stale ? { ...app, online: false } : app}>{children}</Context.Provider>;
}

export function useAccountQuery<T>(parts: readonly unknown[], fn: (api: ApiClient, signal: AbortSignal) => Promise<T>, enabled = true) {
  const { api, accountKey } = useAppData();
  return useQuery({ queryKey: ['account', accountKey, ...parts], queryFn: ({ signal }) => fn(api, signal), enabled });
}
/** After any successful write, every private projection of this account is re-read from the server. */
export function useInvalidateAccount() {
  const client = useQueryClient();
  const { accountKey } = useAppData();
  return () => client.invalidateQueries({ queryKey: ['account', accountKey] });
}

export const useMe = () => useAccountQuery(['me'], (api, signal) => api.me(signal));
export const useSemesters = () => useAccountQuery(['semesters'], (api, signal) => api.semesters(signal));
export const useCourses = (query: { semester_id?: string; archived?: boolean; q?: string; sort?: 'recent' | 'name' } = {}, enabled = true) =>
  useAccountQuery(['courses', query], (api, signal) => api.courses(query, signal), enabled);
export const useCourse = (id: string, enabled = true) => useAccountQuery(['course', id], (api, signal) => api.course(id, signal), enabled);
export const useClassSessions = (courseId?: string) => useAccountQuery(['class-sessions', courseId ?? null], (api, signal) => api.classSessions(courseId ? { course_id: courseId } : {}, signal));
export const useMaterials = (courseId: string) => useAccountQuery(['materials', courseId], (api, signal) => api.materials(courseId, signal));
export const useReviewDocuments = (query: { course_id?: string; kind?: 'summary' | 'card' | 'note'; understood?: boolean } = {}) =>
  useAccountQuery(['review-documents', query], (api, signal) => api.reviewDocuments(query, signal));
export const useNotifications = () => useAccountQuery(['notifications'], (api, signal) => api.notifications(signal));
export const usePreferences = () => useAccountQuery(['preferences'], (api, signal) => api.preferences(signal));
export const useQuota = () => useAccountQuery(['quota'], (api, signal) => api.quota(signal));
export const useDeviceSessions = () => useAccountQuery(['device-sessions'], (api, signal) => api.sessions(signal));
export const useDevices = () => useAccountQuery(['devices'], (api, signal) => api.devices(signal));
export const useMeta = () => useAccountQuery(['meta'], (api, signal) => api.meta(signal));

/** Questions are only filterable per class (contract gap §3.4); query each listed class. */
export function useQuestionsFor(sessionIds: readonly string[]) {
  const { api, accountKey } = useAppData();
  const results = useQueries({ queries: sessionIds.map(id => ({ queryKey: ['account', accountKey, 'questions', id], queryFn: ({ signal }: { signal: AbortSignal }) => api.questions(id, signal) })) });
  return { items: results.flatMap(result => result.data?.items ?? []), pending: results.some(result => result.isPending), error: results.some(result => result.isError) };
}

const messages: Partial<Record<NonNullable<ApiClientError['code']>, string>> = {
  REVISION_CONFLICT: '内容已在其他设备或页面更新。你的输入仍保留，请核对最新版本后再提交。',
  VALIDATION_FAILED: '提交内容未通过服务端校验，请检查各字段。',
  NOT_FOUND: '内容不存在或已被删除。',
  GONE: '内容已被删除。',
  FORBIDDEN: '当前账号无权执行此操作。',
  UNAUTHENTICATED: '会话已失效，请重新登录。',
  PLAN_EXPIRED: '确认已过期，请重新查看影响范围。',
  IDEMPOTENCY_MISMATCH: '同一次操作的内容发生变化，请重新提交。',
  RATE_LIMITED: '操作过于频繁，请稍后再试。',
  RESULT_UNKNOWN: '提交结果暂不确定，可能已经生效。请刷新核对后再决定是否重试。',
  SERVICE_DISABLED: '这项服务暂未开放。',
  SOURCE_CHANGED: '来源内容在准备后已更新。可以重新准备，或明确选择按准备时的原版本发送。',
  SOURCE_UNAVAILABLE: '所需的来源内容暂不可用。',
  LEASE_CONFLICT: '已有课堂正在采集，或采集状态已变化。同一时间只能采集一节课堂。',
  INCOMPATIBLE_CLIENT: '客户端版本与服务不兼容，请刷新页面。',
};
export function describeError(error: unknown): string {
  if (error instanceof ApiClientError) {
    if (error.kind === 'network') return '无法连接服务。已加载的内容保持只读，联网后再操作。';
    if (error.kind === 'stale') return '账号已切换，旧请求结果已丢弃。';
    if (error.kind === 'invalid_response') return '服务返回了无法识别的结果，请刷新后重试。';
    return (error.code && messages[error.code]) ?? '服务暂时不可用，请稍后重试。';
  }
  return '服务暂时不可用，请稍后重试。';
}
export const isConflict = (error: unknown) => error instanceof ApiClientError && error.code === 'REVISION_CONFLICT';
