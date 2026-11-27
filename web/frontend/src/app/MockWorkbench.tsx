import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router';
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query';
import { IconBell, IconBook2, IconBooks, IconCards, IconChalkboard, IconChevronRight, IconCircleDot, IconHeadphones, IconLayoutDashboard, IconMessageReport, IconPlayerStop, IconSearch, IconSettings, IconShieldCheck, IconWaveSine } from '@tabler/icons-react';
import { Dashboard } from '../pages/Dashboard';
import { Placeholder } from '../pages/Placeholder';
import { CourseList } from '../pages/courses/CourseList';
import { CourseFormPage, SemesterFormPage } from '../pages/courses/CourseForms';
import { CourseDetail } from '../pages/courses/CourseDetail';
import { Settings, type Effects, type Theme } from '../pages/settings/Settings';
import { AppDataProvider, describeError, type AppData } from '../features/data';
import type { LocalRisk } from '../domain/dashboard';
import { Catalog } from '../ui';
import { useMockRuntime } from '../mock/runtime';
import { createMockClassroomShell } from '../mock/classroom-shell';
import { createTranscriptFeed } from '../mock/classroom-feed';
import { advanceMockTask, finishMockAsr, persistMockFinal, projectMockTask, releaseMockAccountLeases, releaseMockLease, revokeMockAudio, revokeMockClassroom, setMockDurable, setMockRetranscribe } from '../mock/start';
import { formatClock } from '../domain/classroom';
import { stopCapture } from '../features/classroom';
import { ClassroomEntry } from '../pages/classroom/ClassroomEntry';
import { ClassroomLive } from '../pages/classroom/ClassroomLive';
import { ClassroomProcessing } from '../pages/classroom/ClassroomProcessing';
import { ClassroomRecord } from '../pages/classroom/ClassroomRecord';
import { ReviewHub } from '../pages/review/ReviewHub';
import { createRevocationLedger, type RevocationLedger } from '../features/revocation';
import { ReviewReader } from '../pages/review/ReviewReader';
import { ClassReview } from '../pages/review/ClassReview';
import type { ClassroomOSWebV1EventEnvelope } from '../generated/events';
import { isMockOffline, setMockAccount, setMockDataset, setMockOffline } from '../mock/start';
import { eventScripts, type EventScriptName } from '../mock/event-scripts';
import { createApiClient } from '../api/client';
import { createHttpAdminReauth } from '../features/admin';
import { mockAsrAdmits, mockCsrfToken, setMockAdminHealth, setMockAdminMfa } from '../mock/start';
import { AdminUsers } from '../pages/admin/AdminUsers';
import { AdminInvites } from '../pages/admin/AdminInvites';
import { AdminUsage } from '../pages/admin/AdminUsage';
import { AdminRuntime } from '../pages/admin/AdminRuntime';
import { SearchRoute } from '../pages/search/SearchRoute';
import { NotificationsRoute } from '../pages/notifications/NotificationsRoute';
import { FeedbackPage } from '../pages/feedback/FeedbackPage';
import { ShellSearchPalette } from '../pages/search/ShellSearchPalette';
import { forgetFeedbackContexts } from '../features/feedback/context';
import { noticeMockLeaseLost, subscribeMockCreatedNotifications } from '../mock/start';
import styles from './Shell.module.css';

const baseNav = [
  { to: '/dashboard', text: '首页', Icon: IconLayoutDashboard, DesktopIcon: IconLayoutDashboard },
  { to: '/courses', text: '课程', Icon: IconBook2, DesktopIcon: IconBooks },
  { to: '/classrooms', text: '课堂', Icon: IconHeadphones, DesktopIcon: IconChalkboard },
  { to: '/review', text: '复习', Icon: IconCards, DesktopIcon: IconCards },
  { to: '/settings/appearance', text: '设置', Icon: IconSettings, DesktopIcon: IconSettings },
];
/** Shown only when the server says the signed-in user is an administrator (AUTH-07, FE-13). */
const adminNav = { to: '/admin/users', text: '运营', Icon: IconShieldCheck, DesktopIcon: IconShieldCheck };
const scenarioNames = ['首次使用 · 空课程', '合成的最近记录', '模拟课堂跨页面', '模拟上传失败', '离线与暂存状态', 'AI 结果未知', '候选与个人稿', '版本冲突', '来源不可用', '额度限制', '账号切换', '组件状态'];

/** Fixed synthetic clock so mock dates, weeks and expiry warnings are reproducible. */
const MOCK_NOW = Date.parse('2026-10-28T08:00:00Z');
function CourseRedirect() { const { courseId } = useParams(); return <Navigate to={`/courses/${courseId}/overview`} replace />; }
function ClassroomRedirect() { const { classId } = useParams(); return <Navigate to={`/classrooms/${classId}/live`} replace />; }

export default function MockWorkbench() {
  const runtime = useMockRuntime();
  const scopeEpoch = runtime.state.epoch;
  // Each authenticated epoch owns its client; account teardown cannot reuse an old cache.
  const queryClient = useMemo(() => new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, meta: { mockScopeEpoch: scopeEpoch } } } }), [scopeEpoch]);
  useEffect(() => () => { void queryClient.cancelQueries(); queryClient.clear(); }, [queryClient]);
  // Every account-epoch teardown (switch, revoke button, AUTH_REVOKED event) retires the leases the
  // outgoing account still holds, including a locally stopped capture whose stop was never confirmed.
  const account = runtime.state.account;
  const outgoing = useRef<{ account: 'A' | 'B' | 'C' | null; epoch: number }>({ account, epoch: scopeEpoch });
  useEffect(() => {
    const previous = outgoing.current;
    if (previous.epoch !== scopeEpoch && previous.account) releaseMockAccountLeases(previous.account);
    outgoing.current = { account, epoch: scopeEpoch };
  }, [account, scopeEpoch]);
  return <QueryClientProvider key={scopeEpoch} client={queryClient}><MockWorkbenchContent runtime={runtime} /></QueryClientProvider>;
}

function MockWorkbenchContent({ runtime }: { runtime: ReturnType<typeof useMockRuntime> }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [theme, setTheme] = useState<Theme>(() => { const stored = localStorage.getItem('aurora-theme'); return stored === 'day' || stored === 'night' ? stored : 'system'; });
  const [effects, setEffects] = useState<Effects>(() => localStorage.getItem('aurora-effects') === 'reduced' ? 'reduced' : 'normal');
  const [systemNight, setSystemNight] = useState(() => matchMedia('(prefers-color-scheme: dark)').matches);
  const [eventScript, setEventScript] = useState<EventScriptName>('transcript');
  const [eventStep, setEventStep] = useState(0);
  // Seeded from the transport so an account-epoch remount never shows online while requests fail.
  const [mockOffline, setMockOfflineState] = useState(isMockOffline);
  // Connectivity in the preview comes only from the simulated toggle: navigator.onLine is
  // false on an offline machine that still serves loopback, and "online" never proves the
  // service is reachable (02 §9 ConnectivityPort). Real reachability is per request.
  const { scenario, active, account, epoch, flows } = runtime.state;
  const { acceptAccountResponse, isCurrent } = runtime;
  const api = useMemo(() => createApiClient({ epoch, isCurrent }, { csrfToken: mockCsrfToken }), [epoch, isCurrent]);
  // This component remounts per account epoch, so drafts, pending stops and the feed never cross accounts.
  const [classroomShell] = useState(() => createMockClassroomShell(runtime, () => MOCK_NOW));
  const [feed] = useState(createTranscriptFeed);
  // One revocation ledger per account epoch: deletion receipts, deletion events and authoritative
  // denials revoke cached content and the bound capture at once, without relying on a refetch.
  const { revokeClassroomAccess, revokeAccount: revokeRuntimeAccount } = runtime;
  const [revocation] = useState(() => { const ledger: RevocationLedger = createRevocationLedger({ client: queryClient, accountKey: `${account}:${epoch}`, api, hooks: {
    boundClassId: () => classroomShell.capture.bound()?.classId ?? null,
    onClassRevoked: classId => {
      // Stops a capture bound to it, rejects its unconfirmed uploads and clears its live transcript.
      revokeClassroomAccess(classId);
      // Nothing of a revoked classroom can be confirmed any more: drop its pending stop and command.
      classroomShell.pendingStops.set(classId, null);
      classroomShell.pendingCommands.set(classId, null);
    },
    // Unconfirmed: the normal stop path (local stop now, server stop sent; kept as a retryable pending
    // stop if it cannot be confirmed). No deletion event and no server tombstone: the classroom is
    // revoked only once a read or receipt confirms it, which then discards the pending stop.
    onCaptureSuspended: classId => {
      const bound = classroomShell.capture.bound();
      if (!bound?.active || bound.classId !== classId) return;
      void stopCapture(api, classroomShell, classId, () => crypto.randomUUID()).then(() => {
        // Revoked while the stop was being prepared: nothing of it can be confirmed any more.
        if (ledger.isClassRevoked(classId)) { classroomShell.pendingStops.set(classId, null); classroomShell.pendingCommands.set(classId, null); }
        return queryClient.invalidateQueries();
      }).catch(() => undefined);
    },
    // An expired session retires the whole account epoch (runtime, events, capture and every cached page).
    onUnauthenticated: () => { revokeRuntimeAccount(); setMockAccount(null); void queryClient.cancelQueries(); queryClient.clear(); navigate('/dashboard'); },
  } }); return ledger; });
  useEffect(() => revocation.install(), [revocation]);
  const classQueries = (ids: ReadonlySet<unknown>) => ({ predicate: (query: { queryKey: readonly unknown[] }) => query.queryKey.some(part => ids.has(part)) });
  const location = useLocation();
  const routeClassId = /^\/classrooms\/([0-9a-f-]{36})(?:\/|$)/.exec(location.pathname)?.[1] ?? null;
  const boundClassId = runtime.state.captureScope?.classSessionId ?? null;
  const liveTarget = active && boundClassId ? `/classrooms/${boundClassId}/live` : '/classrooms';
  // The capture bar names the classroom actually bound (same cache entry as the classroom page).
  const boundSession = useQuery({
    queryKey: ['account', `${account}:${epoch}`, 'class-session', boundClassId],
    enabled: active && account !== null && boundClassId !== null,
    queryFn: ({ signal }) => api.classSession(boundClassId ?? '', signal),
  });
  useEffect(() => { const media = matchMedia('(prefers-color-scheme: dark)'); const update = () => setSystemNight(media.matches); media.addEventListener('change', update); return () => media.removeEventListener('change', update); }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme === 'system' ? (systemNight ? 'night' : 'day') : theme; localStorage.setItem('aurora-theme', theme); }, [theme, systemNight]);
  useEffect(() => { document.documentElement.dataset.effects = effects; localStorage.setItem('aurora-effects', effects); }, [effects]);
  // An unsolicited revocation also clears the mock transport and query cache.
  useEffect(() => { if (!account) { setMockAccount(null); void queryClient.cancelQueries(); queryClient.clear(); } }, [account, epoch, queryClient]);
  const coursesQuery = useQuery({
    queryKey: ['account', account, 'courses', epoch],
    enabled: account !== null,
    queryFn: async ({ signal }) => {
      const requestEpoch = epoch;
      if (!isCurrent(requestEpoch)) throw new Error('stale account request');
      const data = await api.getCourses(signal);
      if (!isCurrent(requestEpoch) || !acceptAccountResponse(requestEpoch, data)) throw new Error('stale account response');
      return data;
    },
  });
  // Same cache entry as the pages' useMe: the role decides whether the operations entry exists at all.
  const me = useQuery({ queryKey: ['account', `${account}:${epoch}`, 'me'], enabled: account !== null, queryFn: ({ signal }) => api.me(signal) });
  const nav = me.data?.role === 'admin' && !me.isError ? [...baseNav, adminNav] : baseNav;
  const adminId = me.data?.role === 'admin' && !me.isError ? me.data.id : null;
  const adminIdRef = useRef(adminId);
  adminIdRef.current = adminId;
  const adminReauth = useMemo(() => createHttpAdminReauth(api, () => adminIdRef.current), [api]);
  const apiStatus = !account ? 'revoked' : coursesQuery.isPending ? 'loading' : coursesQuery.isError ? 'error' : 'ready';
  // MOCK-01 is a first-time account with no records; every other scenario uses the seeded records.
  const dataset = scenario === '01' ? 'empty' : 'seeded';
  const appliedDataset = useRef('seeded');
  useEffect(() => {
    if (account !== 'A' || appliedDataset.current === dataset) return;
    appliedDataset.current = dataset;
    setMockDataset('A', dataset);
    // The fixtures behind the same IDs were replaced wholesale: earlier revocations no longer apply.
    revocation.reset();
    void queryClient.invalidateQueries();
  }, [account, dataset, queryClient, revocation]);
  // Only a data set change (account A entering or leaving MOCK-01) forgets deletions; any other scenario keeps
  // the same fixtures, so the runtime keeps its deleted classrooms and never binds one the ledger revoked.
  const chooseScenario = (next: string) => {
    const replaced = account === 'A' && (next === '01' ? 'empty' : 'seeded') !== appliedDataset.current;
    runtime.chooseScenario(next, replaced ? {} : { keepDeleted: true, isRevoked: classId => revocation.isClassRevoked(classId) });
  };
  const toggleOffline = (value: boolean) => { setMockOffline(value); setMockOfflineState(value); };
  const localRisk: LocalRisk = ['write_failed', 'locked', 'quota'].includes(runtime.state.localWriteResult) || flows.sync === 'failed'
    ? { kind: 'unsaved', detail: '模拟本地写入未成功：内容尚未安全保存，只在当前页面。请先处理再离开。' }
    : flows.sync === 'local_only' ? { kind: 'local_only', detail: '模拟内容只保存在本机，尚未联网核对。' } : null;
  const appData: AppData = {
    api, accountKey: `${account}:${epoch}`, online: !mockOffline, now: () => MOCK_NOW,
    capture: { active, classSessionId: active ? runtime.state.captureScope?.classSessionId ?? null : null, title: active ? (account === 'A' ? '模拟课堂 · 线性代数' : '模拟课堂 B') : null },
    localRisk, dataSourceLabel: '开发预览：以下课程、课堂和设置均为合成数据。学期/课程/课堂等接口后端尚未实现，由 Mock 按已批准契约提供。',
    onQuickStart: () => start(), classroomRevoked: runtime.state.eventProjection.classroomDeleted,
    classroom: classroomShell,
    revocation,
    // HTTP 0.4.0: POST /auth/reauth with the admin purpose, answered by the Mock like the real service.
    adminReauth,
  };
  const start = () => {
    // The demo quick start always uses the PR-09a demo classroom, never a PR-10a classroom bound earlier.
    const demo = eventScripts.leaseLoss[0].data.class_session_id;
    // A classroom this account epoch revoked is never bound again.
    if (!active && revocation.isClassRevoked(demo)) return;
    if (!active && runtime.getState().currentClassroomId !== demo) runtime.acquireMockLease(demo);
    if (active || runtime.start()) {
      const classId = runtime.getState().captureScope?.classSessionId ?? '';
      // Every demo run is registered with the Mock server so pause and stop have a matching lease.
      classroomShell.ensureBoundClassroom?.(classId);
      void queryClient.invalidateQueries(classQueries(new Set([classId])));
      navigate(`/classrooms/${classId}/live`);
    }
  };
  // Invalidate the scope before cancelling requests so an uncancellable late reply is rejected too.
  const clearQueries = () => { void queryClient.cancelQueries(); queryClient.clear(); };
  const switchAccount = () => { runtime.switchAccount(); setMockAccount(runtime.getState().account); clearQueries(); navigate('/dashboard'); };
  const revokeAccount = () => { runtime.revokeAccount(); setMockAccount(null); clearQueries(); navigate('/dashboard'); };
  const loginAccount = (next: 'A' | 'B' | 'C') => { runtime.loginAccount(next); setMockAccount(next); clearQueries(); navigate('/dashboard'); };
  const runEventScript = () => {
    runtime.runEventScript(eventScript);
    setEventStep(0);
  };
  const runEventStep = () => {
    runtime.runEventStep(eventScript, eventStep);
    setEventStep((eventStep + 1) % eventScripts[eventScript].length);
  };
  const emitFeed = (event: ClassroomOSWebV1EventEnvelope | null) => {
    if (!event) return;
    // Transcript events pass the simulated server's ASR admission, like scripted ones.
    if (event.type === 'transcript.partial' || event.type === 'transcript.final') { runtime.deliverTranscript(event); return; }
    runtime.getEventClient().emit(event);
  };
  // One admission rule for the feed and the event scripts: the platform ASR switch (PR-11b).
  const { setTranscriptAdmission } = runtime;
  useEffect(() => { setTranscriptAdmission(mockAsrAdmits); }, [setTranscriptAdmission]);
  const feedClassId = active ? boundClassId : null;
  // Durable receipts, including ones that arrive after the stop, reach the Mock server per epoch.
  const durableUploads = runtime.state.durableUploads;
  useEffect(() => {
    if (!durableUploads.length) return;
    const pairs = new Map(durableUploads.map(item => [`${item.classSessionId}:${item.captureEpochId}`, item]));
    for (const item of pairs.values()) setMockDurable(item.classSessionId, item.captureEpochId, classroomShell.capture.durableSeqs(item.classSessionId, item.captureEpochId));
    void queryClient.invalidateQueries(classQueries(new Set(durableUploads.map(item => item.classSessionId))));
  }, [durableUploads, classroomShell, queryClient]);
  // Every final the runtime accepted (feed buttons or event scripts) is persisted by the Mock server.
  const liveSegments = runtime.state.liveSegments;
  useEffect(() => {
    for (const item of liveSegments) {
      if (item.status !== 'final' || !item.segmentRevisionId || !item.versionId) continue;
      persistMockFinal({ classId: item.classroomId, streamId: item.streamId, segmentId: item.segmentId, segmentRevisionId: item.segmentRevisionId, versionId: item.versionId, text: item.text, startMs: item.startMs, endMs: item.endMs, revision: item.revision });
    }
  }, [liveSegments]);
  // An audio deletion event revokes the server's reads of the epoch that recorded the deleted chunk
  // (the deleted object, never whatever capture happens to be bound now).
  const deletedAudioKey = [...new Set(runtime.state.uploadHistory.filter(item => item.audioDeleted).map(item => `${item.classSessionId}:${item.captureEpochId}`))].sort().join('|');
  useEffect(() => {
    if (!deletedAudioKey) return;
    const touched = new Set<string>();
    for (const pair of deletedAudioKey.split('|')) {
      const [classId = '', epochId = ''] = pair.split(':');
      if (revokeMockAudio(classId, epochId)) touched.add(classId);
      // The deleted epoch leaves the cached manifests now, not when a (possibly offline) refetch succeeds.
      revocation.revokeAudio(classId, epochId);
    }
    if (touched.size) void queryClient.invalidateQueries(classQueries(touched));
  }, [deletedAudioKey, queryClient, revocation]);
  // A classroom deletion event revokes server access, then revokes everything of it in this account.
  const deletedClassId = runtime.state.eventProjection.classroomDeleted ? runtime.state.currentClassroomId : null;
  useEffect(() => {
    if (!deletedClassId) return;
    revokeMockClassroom(deletedClassId);
    revocation.revokeClass(deletedClassId);
    // Lists may also have changed on the server: read them again (cached content is already gone).
    void queryClient.invalidateQueries();
  }, [deletedClassId, queryClient, revocation]);
  // Account-shell unload guard: an unconfirmed stop or stopped capture with local-only audio is at
  // risk on every route, not only while the processing page is open.
  useSyncExternalStore(classroomShell.subscribe, classroomShell.version, classroomShell.version);
  const unsafeToLeave = classroomShell.unsafeToLeave();
  useEffect(() => {
    if (!unsafeToLeave) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [unsafeToLeave]);
  // Task events accepted by the runtime (event scripts) reach the same task store the pages read.
  const runtimeTasks = runtime.state.tasks;
  useEffect(() => {
    let changed = false;
    for (const [id, item] of Object.entries(runtimeTasks)) {
      if (!item.classroomId || item.state === 'idle') continue;
      changed = projectMockTask({ id, classId: item.classroomId, state: item.state, revision: item.revision, mayHaveCharged: item.mayHaveCharged }) || changed;
    }
    if (changed) void queryClient.invalidateQueries();
  }, [runtimeTasks, queryClient]);
  // A lost lease is no longer held by the server either, so it never blocks a later classroom; the
  // refreshed projection then offers an explicit new start.
  const lostClassId = runtime.state.eventProjection.leaseLost ? boundClassId : null;
  const lostEpochId = lostClassId ? runtime.state.captureScope?.captureEpochId ?? null : null;
  useEffect(() => {
    if (!lostClassId) return;
    if (!releaseMockLease(lostClassId)) return;
    // PR-11c: only a lease the server actually held and lost records its capture-failure notice (one per
    // capture epoch, for a live classroom) and announces it.
    noticeMockLeaseLost(lostClassId, lostEpochId);
    void queryClient.invalidateQueries();
  }, [lostClassId, lostEpochId, queryClient]);
  // A bound classroom the server now refuses (it served it before) is revoked by the ledger's cache
  // guard, like a deletion event; the PR-09a demo classroom is unknown until it is registered.
  // The global AI indicator follows the bound classroom's tasks too; an unknown outcome always wins.
  const boundAi = boundSession.data?.flows.ai;
  const aiFlow = flows.ai === 'unknown' || boundAi === 'unknown' ? 'unknown' : boundAi && boundAi !== 'idle' ? boundAi : flows.ai;
  // Every stop entry (global bar, catalogue, scenarios) records the range and asks the server to seal it.
  const stopFromShell = () => {
    const bound = classroomShell.capture.bound();
    if (!bound?.active) { runtime.stop(); return; }
    classroomShell.ensureBoundClassroom?.(bound.classId);
    void stopCapture(api, classroomShell, bound.classId, () => crypto.randomUUID()).then(result => {
      void queryClient.invalidateQueries();
      // An unconfirmed stop opens the processing page, where the retry and the unload guard live.
      if (!result.confirmed) navigate(`/classrooms/${bound.classId}/processing`, { state: { stopError: describeError(result.error) } });
    }).catch(() => undefined);
  };
  const devTask = (outcome: 'running' | 'succeeded' | 'failed' | 'unknown') => {
    if (!routeClassId) return;
    void api.tasks(routeClassId).then(page => {
      const target = page.items.find(task => task.state === 'queued' || task.state === 'running');
      if (target && advanceMockTask(target.id, outcome)) void queryClient.invalidateQueries();
    }).catch(() => undefined);
  };
  // PR-11c: the simulated server announces each notification as it creates it, on any page, on this
  // account epoch's event stream (the subscription ends with the epoch). Both bridges follow the event
  // client itself, which lives as long as the runtime, never the per-render runtime wrapper: a runtime
  // update (e.g. the lease loss that creates a notice) must not leave a gap without listeners.
  const eventClient = runtime.getEventClient();
  useEffect(() => subscribeMockCreatedNotifications(item => eventClient.emit({ protocol_version: 1, type: 'notification.created',
    sent_at: new Date(MOCK_NOW).toISOString(), event_id: crypto.randomUUID(), cursor: `mock-notification-${item.id}`,
    data: { notification_id: item.id, severity: item.severity, message_code: item.message_code } })), [eventClient]);
  // PR-11c: notification.created on this account epoch's stream reloads the notification centre and the dashboard card.
  const [noticeToken, setNoticeToken] = useState(0);
  useEffect(() => {
    const stop = eventClient.subscribe(event => {
      if (event.type !== 'notification.created') return;
      setNoticeToken(value => value + 1);
      void queryClient.invalidateQueries({ queryKey: ['account', `${account}:${epoch}`, 'notifications'] });
    });
    return () => { stop(); };
  }, [eventClient, queryClient, account, epoch]);
  // PR-11c: private feedback context (an excerpt) never outlives the account epoch that captured it.
  useEffect(() => () => forgetFeedbackContexts(), [account, epoch]);
  // PR-11c: the dashboard's notification snapshot may name revoked content; a confirmed revocation drops
  // it before reading again (an offline refetch must not leave the old snapshot on screen).
  useEffect(() => revocation.subscribe(() => queueMicrotask(() => {
    void queryClient.resetQueries({ queryKey: ['account', `${account}:${epoch}`, 'notifications'] });
  })), [revocation, queryClient, account, epoch]);
  // PR-11c: Ctrl/⌘K opens the keyword search palette from anywhere in a signed-in account.
  const [paletteOpen, setPaletteOpen] = useState(false);
  useEffect(() => {
    if (!account) return;
    const open = (event: KeyboardEvent) => {
      if (event.isComposing || event.altKey || event.shiftKey || !(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'k') return;
      event.preventDefault();
      setPaletteOpen(true);
    };
    window.addEventListener('keydown', open);
    return () => window.removeEventListener('keydown', open);
  }, [account]);
  const closePalette = useCallback(() => setPaletteOpen(false), []);
  const devAsr = (state: 'succeeded' | 'failed') => { if (routeClassId && finishMockAsr(routeClassId, state)) void queryClient.invalidateQueries(); };
  const failure = flows.upload === 'failed';
  const unknownRiskCount = Object.values(runtime.state.tasks).filter(task => task.mayHaveCharged).length;
  const syncLabel = flows.sync === 'local_only' ? '仅模拟本地保存' : flows.sync === 'failed' ? '未安全保存' : flows.sync === 'synced' ? '已同步' : flows.sync;
  return <div className={styles.app}>
    <a className={styles.skipLink} href="#main-content">跳至主要内容</a>
    <header className={styles.header}><NavLink to="/dashboard" className={styles.brand} data-testid="brand" aria-label="Classroom OS 首页"><span className={styles.brandMark}/></NavLink>
      <nav className={styles.desktopNav} data-testid="desktop-nav" aria-label="主导航">{nav.map(({to,text,DesktopIcon}) => <NavLink key={to} to={to === '/classrooms' ? liveTarget : to} aria-label={text} title={text} className={({isActive}: {isActive: boolean}) => `${styles.navItem} ${isActive ? styles.navActive : ''}`}><DesktopIcon size={18}/></NavLink>)}</nav>
      <div className={styles.headerActions} data-testid="header-actions"><LinkWithIcon to="/search" label="搜索"><IconSearch size={20}/></LinkWithIcon><LinkWithIcon to="/notifications" label="通知"><IconBell size={20}/></LinkWithIcon><LinkWithIcon to="/feedback" label="问题反馈"><IconMessageReport size={20}/></LinkWithIcon><NavLink className={styles.avatar} aria-label="外观设置" to="/settings/appearance">{account === 'A' ? '林' : account === 'B' ? '陈' : account === 'C' ? '管' : '·'}</NavLink></div>
    </header>
    <div className={styles.devBanner}><span className={styles.devDot}/> 开发预览 · 合成数据 <span className={styles.devDivider}>/</span> <span>{apiStatus === 'ready' ? 'Mock API 已连接' : apiStatus === 'loading' ? 'Mock API 连接中' : apiStatus === 'revoked' ? '模拟会话已撤销' : 'Mock API 不可用'}</span><NavLink to="/__dev/catalog">组件目录 ↗</NavLink></div>
    {!account ? <main id="main-content" className={styles.page}><section className={`${styles.panel} ${styles.empty}`}><h1>模拟会话已撤销</h1><p>已清除模拟账号内容、事件订阅和采集状态。</p><button type="button" className={styles.primaryButton} onClick={() => loginAccount('A')}>进入模拟账号 A</button><button type="button" className={styles.secondaryButton} onClick={() => loginAccount('B')}>进入模拟账号 B</button><button type="button" className={styles.secondaryButton} onClick={() => loginAccount('C')}>进入模拟管理员 C</button></section></main> :
    <AppDataProvider value={appData}><Routes><Route path="/" element={<Navigate to="/dashboard" replace/>}/><Route path="/dashboard" element={<Dashboard/>}/><Route path="/__dev/catalog" element={<main id="main-content" className={styles.page}><Catalog theme={theme === 'system' ? (systemNight ? 'night' : 'day') : theme} onThemeChange={setTheme} onStopCapture={active ? stopFromShell : undefined}/></main>}/><Route path="/settings" element={<Navigate to="/settings/appearance" replace/>}/><Route path="/settings/:section" element={<Settings theme={theme} setTheme={setTheme} effects={effects} setEffects={setEffects}/>}/><Route path="/courses" element={<CourseList/>}/><Route path="/courses/new" element={<CourseFormPage/>}/><Route path="/courses/:courseId/edit" element={<CourseFormPage/>}/><Route path="/courses/:courseId" element={<CourseRedirect/>}/><Route path="/courses/:courseId/:tab" element={<CourseDetail/>}/><Route path="/semesters/new" element={<SemesterFormPage/>}/><Route path="/semesters/:semesterId/edit" element={<SemesterFormPage/>}/><Route path="/classrooms" element={<ClassroomEntry/>}/><Route path="/classrooms/mock/live" element={<Navigate to={liveTarget} replace/>}/><Route path="/classrooms/:classId" element={<ClassroomRedirect/>}/><Route path="/classrooms/:classId/live" element={<PerClassroom page={ClassroomLive}/>}/><Route path="/classrooms/:classId/processing" element={<PerClassroom page={ClassroomProcessing}/>}/><Route path="/classrooms/:classId/record" element={<PerClassroom page={ClassroomRecord}/>}/><Route path="/classrooms/:classId/review" element={<PerClassroom page={ClassReview}/>}/><Route path="/review/items/:documentId" element={<PerDocument/>}/><Route path="/review/*" element={<ReviewHub notice={scenario === '10' ? <section className={styles.panel}><h2>模拟只读资料</h2><p>合成的学习资料示例：额度限制时，已有资料仍可阅读。</p><p>下方资料库照常可用。</p><p>只有需要新额度的操作（生成、重新转写）会被拒绝；阅读和标记理解状态不调用模型，也不会发起收费请求。</p></section> : undefined}/>}/><Route path="/admin" element={<Navigate to="/admin/users" replace/>}/><Route path="/admin/users" element={<AdminUsers/>}/><Route path="/admin/invites" element={<AdminInvites/>}/><Route path="/admin/usage" element={<AdminUsage/>}/><Route path="/admin/runtime" element={<AdminRuntime/>}/><Route path="/tasks" element={<Placeholder title="待处理" detail="查看需要关注的任务。"/>}/><Route path="/notifications" element={<NotificationsRoute refreshToken={noticeToken}/>}/><Route path="/search" element={<SearchRoute/>}/><Route path="/feedback" element={<FeedbackPage/>}/><Route path="*" element={<Placeholder title="页面不可用" detail="该页面不存在或尚未开放。"/>}/></Routes></AppDataProvider>}
    {account && <aside className={styles.scenarioPanel} aria-label="开发模拟控制"><label htmlFor="scenario">模拟场景</label><select id="scenario" value={scenario} onChange={event => chooseScenario(event.target.value)}>{scenarioNames.map((name,index) => { const number = String(index + 1).padStart(2,'0'); return <option key={number} value={number}>MOCK-{number} · {name}</option>; })}</select><button onClick={switchAccount} type="button">切换模拟账号 {account} <IconChevronRight size={14}/></button><button onClick={revokeAccount} type="button">撤销模拟会话</button><label><input type="checkbox" checked={mockOffline} onChange={event => toggleOffline(event.target.checked)}/> 模拟离线</label><ScenarioNotice scenario={scenario}/><ScenarioView key={scenario} scenario={scenario} localWrite={runtime.state.localWriteResult} localRecord={runtime.state.mockLocalRecord} vaultLocked={runtime.state.vaultLocked} localHistory={runtime.state.localRecordHistory} writeLocal={runtime.simulateLocalWrite} reconcileLocal={runtime.reconcileLocalWrite} lockVault={runtime.lockMockVault} unlockVault={runtime.unlockMockVault} stopCapture={stopFromShell} checkRetry={runtime.requestAiRetry}/>
      <div className={styles.scenarioDetail}><label htmlFor="event-script">模拟事件脚本</label>{' '}<select id="event-script" value={eventScript} onChange={event => { setEventScript(event.target.value as EventScriptName); setEventStep(0); }}><option value="transcript">临时与正式转写</option><option value="upload">帧确认与持久上传</option><option value="taskUnknown">AI 结果未知</option><option value="deletion">原音撤权</option><option value="classroomDeletion">课堂撤权</option><option value="leaseLoss">采集租约失效</option><option value="revocation">会话撤销</option></select>{' '}<button type="button" onClick={runEventScript}>运行模拟事件</button>{' '}<button type="button" onClick={runEventStep}>模拟下一步事件</button>{runtime.state.eventProjection.leaseLost && !runtime.state.eventProjection.classroomDeleted && <button type="button" onClick={() => runtime.acquireMockLease()}>重新获取模拟租约</button>}<div role="region" aria-label="模拟事件结果" data-port-capture={runtime.state.eventProjection.portCaptureState} data-port-upload-pending={String(runtime.state.eventProjection.uploadPending)}><p>正式片段：{runtime.state.eventProjection.finalText ?? '无'}；临时片段：{runtime.state.eventProjection.partial ?? '无'}</p><p>帧确认：{runtime.state.eventProjection.frameReceived ? '已收到' : '未收到'}；持久上传：{runtime.state.eventProjection.chunkDurable ? '已确认' : '未确认'}</p><p>当前课堂 AI 结果：{flows.ai === 'unknown' ? '未知，可能已收费' : flows.ai}；原音撤权：{runtime.state.eventProjection.audioDeleted ? '已撤权' : '无'}；课堂撤权：{runtime.state.eventProjection.classroomDeleted ? '已撤权' : '无'}；采集租约：{runtime.state.eventProjection.leaseLost ? '已失效' : '正常'}</p><p>全部课堂的未知收费风险：{unknownRiskCount} 项{unknownRiskCount > 0 ? '，请先核对，不能自动重试' : ''}</p></div></div>
      <section className={styles.scenarioDetail} aria-label="课堂模拟控制"><strong>课堂模拟（PR-10a）</strong>
        <div><button type="button" disabled={!feedClassId} onClick={() => feedClassId && emitFeed(feed.advance(feedClassId))}>推进转写</button>{' '}<button type="button" disabled={!feedClassId} onClick={() => feedClassId && emitFeed(feed.late(feedClassId))}>送达迟到的早段</button>{' '}<button type="button" disabled={!feedClassId} onClick={() => feedClassId && emitFeed(feed.duplicate(feedClassId))}>重复送达上一条</button></div>
        <div><button type="button" disabled={!routeClassId} onClick={() => devTask('running')}>任务 → 生成中</button>{' '}<button type="button" disabled={!routeClassId} onClick={() => devTask('succeeded')}>任务 → 完成</button>{' '}<button type="button" disabled={!routeClassId} onClick={() => devTask('failed')}>任务 → 失败</button>{' '}<button type="button" disabled={!routeClassId} onClick={() => devTask('unknown')}>任务 → 结果未知</button></div>
        <div><button type="button" disabled={!routeClassId} onClick={() => devAsr('succeeded')}>转写收尾完成</button>{' '}<button type="button" disabled={!routeClassId} onClick={() => devAsr('failed')}>转写收尾失败</button></div>
        <div aria-label="重新转写模拟（PR-10b）"><strong>重新转写（PR-10b）</strong>{' '}<button type="button" disabled={!routeClassId} onClick={() => routeClassId && setMockRetranscribe({ classId: routeClassId, audio: 'ok' })}>原音正常</button>{' '}<button type="button" disabled={!routeClassId} onClick={() => routeClassId && setMockRetranscribe({ classId: routeClassId, audio: 'expired' })}>原音到期</button>{' '}<button type="button" disabled={!routeClassId} onClick={() => routeClassId && setMockRetranscribe({ classId: routeClassId, audio: 'deleted' })}>原音撤权</button>
          {' '}<button type="button" onClick={() => setMockRetranscribe({ quota: 'ok' })}>额度正常</button>{' '}<button type="button" onClick={() => setMockRetranscribe({ quota: 'session' })}>本课需审批</button>{' '}<button type="button" onClick={() => setMockRetranscribe({ quota: 'month' })}>本月额度不足</button>{' '}<button type="button" disabled={!routeClassId} onClick={() => routeClassId && setMockRetranscribe({ classId: routeClassId, approve: true })}>批准追加一次</button></div>
        <p>只作用于当前采集或当前页面的课堂；不调用任何真实服务。</p>
        <div aria-label="运营后台模拟（PR-11b）"><strong>运营后台（PR-11b）</strong>{' '}<button type="button" onClick={() => loginAccount('C')}>登录模拟管理员 C</button>{' '}<button type="button" onClick={() => loginAccount('A')}>回到学生 A</button>
          {' '}<button type="button" onClick={() => { setMockAdminHealth('healthy'); void queryClient.invalidateQueries(); }}>运行正常</button>{' '}<button type="button" onClick={() => { setMockAdminHealth('degraded'); void queryClient.invalidateQueries(); }}>部分降级</button>{' '}<button type="button" onClick={() => { setMockAdminHealth('stalled'); void queryClient.invalidateQueries(); }}>处理停滞</button>
          {' '}<button type="button" onClick={() => { setMockAdminMfa(false); void queryClient.invalidateQueries(); }}>管理员未启用 MFA</button>{' '}<button type="button" onClick={() => { setMockAdminMfa(true); void queryClient.invalidateQueries(); }}>管理员已启用 MFA</button></div>
        <p>再认证使用合成凭据：任意非空密码，任意 6 位验证码（000000 表示验证码错误）。</p>
      </section>
      <section className={styles.scenarioDetail} aria-label="模拟上传历史"><strong>模拟上传历史</strong>{runtime.state.uploadHistory.length === 0 ? <p>暂无模拟分片。</p> : <ol>{runtime.state.uploadHistory.map(item => <li key={`${item.captureEpochId}:${item.chunkId}:${item.seq}`} data-chunk-id={item.chunkId} data-capture-epoch={item.captureEpochId} data-status={item.status}><span>分片 {item.seq} · {item.status === 'durable' ? '已持久确认' : item.canAccept ? '待持久确认' : '已失效，无法确认'} · 帧确认{item.frameReceived ? '已收到' : '未收到'}{item.chunkId === runtime.state.captureScope?.chunkId ? ' · 当前采集' : ' · 历史采集'}</span>{item.status === 'pending' && item.canAccept && item.chunkId !== runtime.state.captureScope?.chunkId && <> <button type="button" onClick={() => runtime.runHistoricalDurable(item.chunkId)}>模拟迟到持久回执</button></>}</li>)}</ol>}</section>
    </aside>}
    {active && <div className={styles.captureBar} role="region" aria-label="模拟课堂状态"><button type="button" onClick={() => navigate(liveTarget)} className={styles.captureTitle}><span className={styles.recordDot}/><span><strong>{flows.capture === 'paused' ? '模拟采集已暂停' : '模拟录音中'}</strong><small>{boundSession.data?.title || (account === 'A' ? '模拟课堂 · 线性代数' : '模拟课堂 B')}</small></span><IconWaveSine className={styles.wave} size={24}/><strong aria-label="采集时长">{formatClock(boundClassId ? classroomShell.capture.positionMs(boundClassId) : null)}</strong></button><div className={styles.flowList} aria-label="五流独立状态"><span>● 采集 · {flows.capture === 'recording' ? '录音中' : flows.capture === 'paused' ? '已暂停' : flows.capture}</span><span className={failure ? styles.flowWarning : ''}>{failure ? '！上传 · 失败' : flows.upload === 'durable' ? '✓ 上传 · 已确认' : '◌ 上传 · 待处理'}</span><span>◌ 转写 · {flows.asr === 'streaming' ? '进行中' : flows.asr === 'idle' ? '未开始' : flows.asr}</span><span>– AI · {aiFlow === 'idle' ? '无任务' : aiFlow === 'unknown' ? '结果未知' : aiFlow}</span><span className={flows.sync !== 'synced' ? styles.flowWarning : ''}>同步 · {syncLabel}</span></div><button type="button" className={styles.stopButton} onClick={stopFromShell}><IconPlayerStop size={17}/> 停止</button></div>}
    {account && <AppDataProvider value={appData}><ShellSearchPalette open={paletteOpen} onClose={closePalette} onStopCapture={active ? stopFromShell : undefined}/></AppDataProvider>}
    <nav className={styles.mobileNav} data-testid="mobile-nav" aria-label="移动主导航"><span className={styles.mobileIndicator} data-testid="mobile-indicator" aria-hidden="true"/>{nav.slice(0,4).map(({to,text,Icon}) => <NavLink key={to} to={to === '/classrooms' ? liveTarget : to} className={({isActive}: {isActive: boolean}) => `${styles.mobileItem} ${isActive ? styles.mobileActive : ''}`}><Icon size={23}/><span>{text}</span></NavLink>)}</nav>
  </div>;
}

/** Remount a classroom page per classroom: prepared plans, dialogs and notices never carry over. */
function PerClassroom({ page: Page }: { page: React.ComponentType }) { const { classId } = useParams(); return <Page key={classId} />; }
/** Each review document mounts its own reader (selection, drafts and scroll restore never leak across documents). */
function PerDocument() { const { documentId } = useParams(); return <ReviewReader key={documentId} />; }
function LinkWithIcon({to,label,children}: {to:string; label:string; children:React.ReactNode}) { return <NavLink className={styles.headerIcon} to={to} aria-label={label}>{children}</NavLink>; }
function ScenarioNotice({scenario}: {scenario:string}) {
  const notices: Record<string,string> = {'04':'上传失败；其余状态独立显示。','05':'这是模拟暂存状态，不代表真实持久化。','06':'模拟结果未知，可能产生费用；应先核对，不自动重试。','07':'模拟候选尚未采用，个人稿保留。','08':'模拟冲突：两个版本均应保留。','09':'模拟原音已删除或图片缺失，不能播放。','10':'模拟额度受限，阅读入口仍可访问。','11':'切换模拟账号后，当前状态清除。','12':'请在组件目录查看状态示例。'};
  return notices[scenario] ? <p className={styles.scenarioNotice}><IconCircleDot size={15}/> {notices[scenario]}</p> : null;
}

function ScenarioView({scenario, localWrite, localRecord, vaultLocked, localHistory, writeLocal, reconcileLocal, lockVault, unlockVault, stopCapture, checkRetry}: {scenario:string; vaultLocked:boolean; localHistory:Array<{recordId:string; captureEpochId:string|null; sync:'pending'|'reconciled'}>; localWrite:'none'|'stored'|'write_failed'|'locked'|'quota'|'reconciled'; localRecord:string|null; writeLocal: (success:boolean) => boolean; reconcileLocal: () => boolean; lockVault: () => void; unlockVault: () => void; stopCapture: () => void; checkRetry: () => boolean}) {
  const [checked, setChecked] = useState(false);
  const [compare, setCompare] = useState(false);
  const [conflictAction, setConflictAction] = useState('');
  if (scenario === '05') return <div className={styles.scenarioDetail}><strong>离线写入模拟</strong><div><button type="button" onClick={() => writeLocal(true)}>模拟写入成功</button> <button type="button" onClick={() => writeLocal(false)}>模拟写入失败</button></div><div><button type="button" onClick={lockVault}>锁定模拟 Vault</button> <button type="button" onClick={unlockVault}>解锁模拟 Vault</button></div><p role="status">{localWrite === 'stored' ? '模拟本地保存成功，等待联网核对。' : localWrite === 'write_failed' ? '尚未安全保存，仅在当前页面。请处理后再离开。' : localWrite === 'locked' ? (vaultLocked ? '模拟 Vault 已锁定，无法写入。' : '最近一次写入因 Vault 锁定被拒绝，尚未安全保存。') : localWrite === 'quota' ? '模拟 Vault 空间不足，写入被拒绝。' : localWrite === 'reconciled' ? '模拟联网核对完成；没有真实上传。' : '尚未安全保存。请选择模拟写入结果。'}</p><p>模拟 Vault 记录：{vaultLocked ? '已锁定，无法读取' : localRecord ?? '无'}</p><section aria-label="模拟 Vault 保存历史"><strong>模拟保存历史</strong>{vaultLocked ? <p>已锁定，无法读取</p> : localHistory.length === 0 ? <p>暂无模拟记录。</p> : <ol>{localHistory.map(item => <li key={item.recordId} data-record-id={item.recordId} data-capture-epoch={item.captureEpochId} data-sync={item.sync}>记录 {item.captureEpochId ?? '无采集'} · {item.sync === 'reconciled' ? '已核对' : '待核对'}</li>)}</ol>}</section>{localWrite === 'stored' && <button type="button" onClick={() => reconcileLocal()}>模拟联网核对</button>}{(localWrite === 'write_failed' || localWrite === 'locked' || localWrite === 'quota') && <button type="button" onClick={stopCapture}>停止模拟课堂</button>}</div>;
  if (scenario === '06') return <div className={styles.scenarioDetail}><strong>模拟 AI 结果未知</strong><p>请求可能已计费。请先核对，不会自动重试或发起真实请求。</p><button type="button" onClick={() => { checkRetry(); setChecked(true); }}>核对状态</button>{checked && <p>模拟核对：结果仍未知；未重新发送。</p>}</div>;
  if (scenario === '07' || scenario === '08') return <div className={styles.scenarioDetail}><strong>{scenario === '07' ? '候选尚未采用' : '模拟版本冲突'}</strong><p>个人稿与{scenario === '07' ? '新候选' : '云端版本'}均保留。</p><button type="button" onClick={() => setCompare(!compare)}>{compare ? '收起对比' : '查看对比'}</button>{compare && <div className={styles.compare}><p>个人稿：合成的原有笔记。</p><p>{scenario === '07' ? '候选' : '云端'}：合成的另一版本。</p></div>}{scenario === '08' && <><div><button type="button" onClick={() => setConflictAction('合并')}>模拟合并</button> <button type="button" onClick={() => setConflictAction('另存')}>模拟另存</button> <button type="button" onClick={() => setConflictAction('采用云端')}>模拟采用云端</button></div>{conflictAction && <p role="status">已选择模拟{conflictAction}路径；仅作预览，个人稿与云端版本仍保留，未保存任何更改。</p>}</>}</div>;
  if (scenario === '09') return <div className={styles.scenarioDetail}><strong>来源不可用</strong><p>模拟原音已删除；保留位置 00:12:30。图片离线缺失。</p><button type="button" disabled>播放原音不可用</button></div>;
  if (scenario === '10') return <div className={styles.scenarioDetail}><strong>模拟额度限制</strong><p>新收费任务已暂停；仍可阅读已有资料。</p><NavLink to="/review">前往复习入口</NavLink></div>;
  return null;
}
