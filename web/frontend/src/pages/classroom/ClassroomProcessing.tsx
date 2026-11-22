import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router';
import type { components } from '../../generated/openapi';
import { describeError, useAppData, useInvalidateAccount } from '../../features/data';
import { ApiClientError } from '../../api/client';
import { confirmStop, useAudioManifest, useClassSession, useClassroomShell, useTasks } from '../../features/classroom';
import { assessProcessing, TASK_STATE_LABEL } from '../../domain/classroom';
import { DataSourceNote } from '../common';
import { PlanDialog } from './PlanDialog';
import { TaskList } from './AiPanels';
import { FlowStrip } from './ClassroomLive';
import styles from './classroom.module.css';

type S = components['schemas'];
const toneClass = { success: styles.toneSuccess, info: styles.toneInfo, warning: styles.toneWarning, danger: styles.toneDanger } as const;

/**
 * UI-CLASS-11 independent processing page. The stop range is recorded first; local-only audio is
 * warned about continuously; leaving is described as safe only for work the server accepted. A
 * summary is never started automatically, and incomplete material needs an explicit partial choice.
 */
export function ClassroomProcessing() {
  const { classId = '' } = useParams();
  const location = useLocation();
  const app = useAppData();
  const shell = useClassroomShell();
  const invalidate = useInvalidateAccount();
  const session = useClassSession(classId);
  const stopped = session.data?.capture_ended_at != null;
  const bound = shell.capture.bound();
  const manifest = useAudioManifest(classId, bound?.classId === classId ? bound.captureEpochId : null, stopped);
  const tasks = useTasks(classId);
  const [retrying, setRetrying] = useState(false);
  const [stopError, setStopError] = useState<string | null>((location.state as { stopError?: string } | null)?.stopError ?? null);
  const [plan, setPlan] = useState<S['GenerationPlan'] | null>(null);
  const [planError, setPlanError] = useState<unknown>(null);
  const [preparing, setPreparing] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [classId]);

  const pendingStop = shell.pendingStops.get(classId);
  const localPending = shell.capture.pendingChunks(classId);
  const assessment = session.data ? assessProcessing({ session: session.data, manifest: manifest.data ?? null, localStopPending: pendingStop !== null, localPendingChunks: localPending, tasks: tasks.data?.items ?? [] }) : null;
  const unsafe = assessment !== null && !assessment.canLeaveSafely;
  // Closing the tab cannot continue an upload; the browser asks before discarding local-only audio.
  useEffect(() => {
    if (!unsafe) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [unsafe]);

  if (session.isPending) return <main id="main-content" className={styles.page}><p role="status" className={styles.muted}>正在读取课后处理状态…</p></main>;
  // A failed refresh keeps the cached record on screen: pending-stop recovery must stay reachable.
  const accessLost = session.error instanceof ApiClientError && (session.error.code === 'NOT_FOUND' || session.error.code === 'GONE' || session.error.code === 'FORBIDDEN');
  if (!session.data || !assessment || accessLost) return <main id="main-content" className={styles.page}><div className={`${styles.banner} ${styles.bannerDanger}`} role="alert"><div>{describeError(session.error)} <Link to="/classrooms">返回课堂入口</Link></div></div></main>;
  const data = session.data;

  const retryStop = async () => {
    setRetrying(true);
    const result = await confirmStop(app.api, shell, classId);
    setRetrying(false);
    setStopError(result.confirmed ? null : describeError(result.error));
    void invalidate();
  };
  const prepare = async (partial: boolean) => {
    setPreparing(true); setPlanError(null); setSubmitted(false);
    try { setPlan(await app.api.createPlan(classId, { kind: 'review_summary', scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: partial }, crypto.randomUUID())); }
    catch (caught) { setPlanError(caught); } finally { setPreparing(false); }
  };
  const summaries = (tasks.data?.items ?? []).filter(task => task.kind === 'review_summary');
  const succeeded = summaries.some(task => task.state === 'succeeded');
  const canPrepare = app.online && stopped && pendingStop === null;

  return <main id="main-content" className={styles.page}>
    <header className={styles.header}>
      <div><p className={styles.eyebrow}>课后处理</p><h1 ref={heading} tabIndex={-1}>{data.title || '未命名课堂'}</h1>
        <p className={styles.small} data-testid="processing-headline">{assessment.complete ? '五项处理均已完成。' : '处理尚未全部完成，各项状态分别显示如下。'}</p></div>
      <FlowStrip flows={data.flows} />
    </header>
    <DataSourceNote />
    {session.isError && <div className={`${styles.banner} ${styles.bannerWarning}`} role="status" data-testid="refresh-error"><div><strong>状态刷新失败</strong>：{describeError(session.error)} 以下为上次读取的状态。</div></div>}
    {assessment.leaveWarning && <div className={`${styles.banner} ${styles.bannerDanger}`} role="alert" data-testid="leave-warning"><div><strong>请先不要离开或关闭页面</strong>：{assessment.leaveWarning}</div></div>}
    {!assessment.leaveWarning && !assessment.canLeaveSafely && <div className={`${styles.banner} ${styles.bannerWarning}`} role="status" data-testid="capture-not-ended"><div>这节课堂的采集尚未结束，服务端还没有记录停止范围。</div></div>}
    {assessment.canLeaveSafely && <div className={`${styles.banner} ${styles.bannerSuccess}`} role="status" data-testid="leave-safe"><div>服务器已接收停止范围和已上传的音频。离开本页后，服务器上已接收的处理会继续；未开始的总结不会自动生成。</div></div>}

    <div className={styles.processingGrid}>
      <section className={styles.panel} aria-labelledby="processing-status">
        <h2 id="processing-status">处理状态</h2>
        <ul className={styles.list} data-testid="processing-items">{assessment.items.map(item => <li key={item.key} className={styles.listItem} data-key={item.key} data-tone={item.tone}><div>
          <strong className={toneClass[item.tone]}>{item.tone === 'success' ? '✓ ' : item.tone === 'danger' ? '! ' : item.tone === 'warning' ? '! ' : '◌ '}{item.title}</strong>
          <p className={`${styles.small} ${styles.muted}`}>{item.detail}</p>
          {item.key === 'stop' && pendingStop && <div className={styles.row}><button type="button" className={styles.button} disabled={retrying || !app.online} onClick={() => void retryStop()}>{retrying ? '正在确认…' : '重试确认停止范围'}</button>{stopError && <span className={styles.error} role="alert">{stopError}</span>}</div>}
        </div></li>)}</ul>
        <h3 style={{ marginTop: '1rem' }}>任务</h3>
        <TaskList classId={classId} kinds={['asr_live', 'asr_retranscribe', 'ai_question', 'ai_explanation', 'review_summary']} emptyText="这节课还没有任务。" />
        <p className={`${styles.small} ${styles.muted}`}>结果未知的任务只能核对，不会自动重试，以免重复计费。</p>
      </section>

      <section className={styles.panel} aria-labelledby="summary-title" data-testid="summary-section">
        <h2 id="summary-title">课堂总结</h2>
        <p className={`${styles.small} ${styles.muted}`}>结束课堂不会自动生成总结或重新转写。生成前会显示范围、缺失项、来源版本、服务与费用上限。</p>
        {!stopped && <p className={styles.toneWarning}>停止范围确认后才能生成总结。</p>}
        {stopped && assessment.missing.length > 0 && <div className={`${styles.banner} ${styles.bannerWarning}`} role="note" data-testid="summary-missing"><div><strong>材料尚未齐全</strong><ul>{assessment.missing.map(item => <li key={item}>{item}</li>)}</ul>可以等待材料齐全，或明确选择只对已有内容生成部分范围总结。</div></div>}
        <div className={styles.row}>
          {assessment.missing.length > 0
            ? <><button type="button" className={styles.buttonSecondary} onClick={() => void invalidate()}>等待并刷新状态</button>
              <button type="button" className={styles.button} disabled={!canPrepare || preparing} onClick={() => void prepare(true)}>生成部分范围总结…</button></>
            : <button type="button" className={styles.button} disabled={!canPrepare || preparing} onClick={() => void prepare(false)}>生成课堂总结…</button>}
        </div>
        {!app.online && <p className={styles.toneWarning}>离线时不能请求总结。</p>}
        {planError !== null && <p className={styles.error} role="alert">{describeError(planError)}</p>}
        {submitted && <p role="status" className={styles.small}>总结请求已提交。完成后会显示「可查看候选」，候选不会自动采用。</p>}
        {summaries.length > 0 && <ul className={styles.list} aria-label="总结请求">{summaries.map(task => <li key={task.id} className={styles.listItem}><div>课堂总结 · {TASK_STATE_LABEL[task.state]}</div></li>)}</ul>}
        {succeeded && <p className={styles.small} data-testid="candidate-ready">已有可查看的候选总结。候选不会自动替换已采用版本，请在复习中查看并决定是否采用。<Link to={`/classrooms/${classId}/review`}>查看候选</Link></p>}
      </section>
    </div>

    <nav className={styles.row} style={{ marginTop: '1rem' }} aria-label="课后去向">
      <Link className={styles.buttonSecondary} to={`/classrooms/${classId}/record`}>查看课堂记录</Link>
      <Link className={styles.buttonGhost} to={`/courses/${data.course_id}/classrooms`}>回到课程</Link>
      <Link className={styles.buttonGhost} to={`/classrooms/${classId}/live`}>返回课堂页</Link>
    </nav>
    <PlanDialog key={plan?.id ?? 'none'} plan={plan} scope="all" onClose={() => setPlan(null)} onExecuted={() => { setPlan(null); setSubmitted(true); void invalidate(); }} />
  </main>;
}
