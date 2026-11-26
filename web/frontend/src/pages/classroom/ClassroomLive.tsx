import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { IconFlag, IconHelpCircle, IconPhoto, IconPlayerPause, IconPlayerPlay, IconPlayerStop, IconInfoCircle, IconFocus2, IconTextIncrease, IconTextDecrease } from '@tabler/icons-react';
import type { components } from '../../generated/openapi';
import { ApiClientError } from '../../api/client';
import { Dialog } from '../../ui';
import { describeError, useAppData, useCourse, useInvalidateAccount, usePreferences, useQuota } from '../../features/data';
import { CONSENT_TEXT, commandCapture, reconcileCommand, startCapture, stopCapture, useClassSession, useClassroomShell, useMarks, useSlides, useTranscript, type BoundCapture } from '../../features/classroom';
import { buildTimeline, formatClock, preparationQuotaLines } from '../../domain/classroom';
import { flowItems } from '../../domain/flows';
import { DataSourceNote } from '../common';
import { TranscriptTimeline } from './TranscriptTimeline';
import { Workspace } from './Workspace';
import { ExplanationPanel, QaPanel } from './AiPanels';
import { SlidesPanel } from './SlidesPanel';
import styles from './classroom.module.css';

type S = components['schemas'];
type Phase = 'prep' | 'live' | 'stopped_here' | 'ended' | 'elsewhere';

const toneClass: Record<string, string> = { success: styles.toneSuccess, warning: styles.toneWarning, danger: styles.toneDanger, info: styles.toneInfo, recording: styles.toneRecording };
const CAPTURE_LABEL: Record<S['FlowStates']['capture'], string> = { idle: '未开始', recording: '采集中', paused: '已暂停', local_only: '仅本地采集（离线）', failed: '采集故障', ended: '已结束 · 处理中' };

/**
 * The capture view of this classroom. While capturing here, the local runtime wins over the server
 * projection; once stopped here, server-owned processing states apply and only the local capture
 * end and still-local audio are overlaid.
 */
function effectiveFlows(session: S['ClassSession'], bound: BoundCapture | null, localPending: number): S['FlowStates'] {
  if (!bound || bound.classId !== session.id) return session.flows;
  if (bound.active) return { ...bound.flows, ai: bound.flows.ai === 'idle' ? session.flows.ai : bound.flows.ai };
  return { ...session.flows, capture: 'ended', upload: localPending > 0 && session.flows.upload === 'durable' ? 'pending' : session.flows.upload };
}

export function FlowStrip({ flows }: { flows: S['FlowStates'] }) {
  return <ul className={styles.flows} aria-label="五项独立状态" data-testid="classroom-flows">{flowItems(flows).map(item =>
    <li key={item.key}><span className={`${styles.badge} ${toneClass[item.tone] ?? ''}`} data-flow={item.key} data-state={flows[item.key]}>{item.abnormal ? '! ' : ''}{item.name}：{item.label}</span></li>)}</ul>;
}

async function textDigest(text: string) {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(hash)].map(value => value.toString(16).padStart(2, '0')).join('');
}

export function ClassroomLive() {
  const { classId = '' } = useParams();
  const app = useAppData();
  const shell = useClassroomShell();
  const navigate = useNavigate();
  const invalidate = useInvalidateAccount();
  const session = useClassSession(classId);
  const transcript = useTranscript(classId);
  const slides = useSlides(classId);
  const course = useCourse(session.data?.course_id ?? '', Boolean(session.data));
  const bound = shell.capture.bound();
  // A capture that lost its lease (or classroom) is no longer bound here: no stop is pending for it.
  const boundHere = bound?.classId === classId && !bound.leaseLost ? bound : null;
  const lostHere = bound?.classId === classId && bound.leaseLost;
  const otherActive = bound && bound.active && bound.classId !== classId ? bound : null;
  const [focus, setFocus] = useState(false);
  // Subtitles start at the account's saved reading size (14-32 px); an in-page change overrides it.
  const preferences = usePreferences();
  const [fontOverride, setFontOverride] = useState<number | null>(null);
  const fontPx = fontOverride ?? Math.min(32, Math.max(14, preferences.data?.reading_font_px ?? 17));
  const setFontPx = (change: (value: number) => number) => setFontOverride(change(fontPx));
  const [notice, setNotice] = useState<{ tone: 'info' | 'warning' | 'danger'; text: string } | null>(null);
  const [details, setDetails] = useState(false);
  const [slidesOpen, setSlidesOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ensured = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);

  // The PR-09a demo capture creates its lease in the page; register it with the Mock once.
  const notFound = session.error instanceof ApiClientError && session.error.code === 'NOT_FOUND';
  // Authoritative answers (deleted, gone, no permission) revoke cached content; only transient
  // failures keep the last good state on screen.
  const accessLost = session.error instanceof ApiClientError && (session.error.code === 'NOT_FOUND' || session.error.code === 'GONE' || session.error.code === 'FORBIDDEN');
  // A pause/resume the server did not confirm is replayed (same body and key) once back online.
  const hasPendingCommand = shell.pendingCommands.get(classId) !== null;
  useEffect(() => {
    if (!app.online || !hasPendingCommand) return;
    void reconcileCommand(app.api, shell, classId).then(() => invalidate()).catch(() => undefined);
  }, [app.online, hasPendingCommand, app.api, shell, classId, invalidate]);
  useEffect(() => {
    if (notFound && !ensured.current && shell.ensureBoundClassroom?.(classId)) { ensured.current = true; void session.refetch(); }
  }, [notFound, classId, shell, session]);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [classId]);
  // Mock bridge: every epoch captured here is known to the Mock server (idempotent registration).
  const boundEpoch = boundHere?.active ? boundHere.captureEpochId : null;
  useEffect(() => { if (boundEpoch) shell.ensureBoundClassroom?.(classId); }, [boundEpoch, shell, classId]);

  const live = shell.capture.liveSegments(classId);
  const entries = useMemo(() => buildTimeline(classId, transcript.data?.segments ?? [], live), [classId, transcript.data, live]);
  // Slides attach to the capture clock while capturing here; otherwise to the end of known subtitles.
  const positionMs = shell.capture.positionMs(classId) ?? Math.max(0, ...entries.map(entry => entry.endMs ?? entry.startMs ?? 0));

  if (session.isPending) return <main id="main-content" className={styles.page}><p role="status" className={styles.muted}>正在读取课堂…</p></main>;
  if (!session.data || accessLost) return <main id="main-content" className={styles.page}><div className={`${styles.banner} ${styles.bannerDanger}`} role="alert"><div>{describeError(session.error)} <Link to="/classrooms">返回课堂入口</Link></div></div></main>;
  const data = session.data;
  const flows = effectiveFlows(data, boundHere, shell.capture.pendingChunks(classId));
  const phase: Phase = boundHere?.active ? 'live'
    : data.capture_ended_at !== null ? 'ended'
      : boundHere && shell.pendingStops.get(classId) !== null ? 'stopped_here'
        : data.flows.capture === 'recording' || data.flows.capture === 'paused' ? 'elsewhere' : 'prep';
  // Deletion revokes access first: no cached content, no further marks, slides or AI requests.
  const revoked = boundHere?.classroomRevoked === true;
  const readOnly = !app.online || phase === 'elsewhere' || revoked;
  // AI requests need connectivity and a writable view; transcript scopes also need a final subtitle,
  // the slide scope needs an available slide.
  const aiBlocked = !app.online ? '离线时不能发送 AI 请求。' : readOnly ? '本页只读，不能发起新的 AI 请求。' : null;
  const noFinal = entries.every(entry => entry.status !== 'final') ? '有正式字幕后才能请求解释或提问。' : null;
  const slidesAvailable = (slides.data?.items ?? []).some(slide => !slide.removed);

  const run = async (action: () => Promise<void>) => { setBusy(true); setNotice(null); try { await action(); } catch (error) { setNotice({ tone: 'danger', text: describeError(error) }); } finally { setBusy(false); } };
  const pauseOrResume = (action: 'pause' | 'resume') => run(async () => {
    const result = await commandCapture(app.api, shell, classId, action, crypto.randomUUID());
    setNotice(result.serverConfirmed ? null : { tone: 'warning', text: action === 'pause' ? `已在本机暂停，服务端尚未确认：${describeError(result.error)}` : `未能继续：服务端没有确认，采集仍处于暂停。${describeError(result.error)}` });
    void invalidate();
  });
  const stop = () => run(async () => {
    const result = await stopCapture(app.api, shell, classId, () => crypto.randomUUID());
    void invalidate();
    navigate(`/classrooms/${classId}/processing`, { state: result.confirmed ? null : { stopError: describeError(result.error) } });
  });
  const mark = (kind: 'important' | 'unclear') => run(async () => {
    const lastFinal = [...entries].reverse().find(entry => entry.status === 'final');
    let anchor: S['MarkAnchor'];
    if (lastFinal) {
      // The displayed (winning) revision supplies both the revision ID and the content hash.
      const revision = lastFinal.segmentRevisionId;
      anchor = revision ? { kind: 'source', source_ref: { kind: 'transcript_segment', id: lastFinal.segmentId, revision_id: revision, content_sha256: await textDigest(lastFinal.text) } }
        : { kind: 'timepoint', capture_epoch_id: boundHere!.captureEpochId, sample_offset: boundHere!.lastSample };
    } else if (boundHere) {
      // Without a final yet, a mark binds to this capture's time point instead of inventing a source.
      anchor = { kind: 'timepoint', capture_epoch_id: boundHere.captureEpochId, sample_offset: boundHere.lastSample };
    } else { setNotice({ tone: 'warning', text: '还没有可标记的内容。' }); return; }
    await app.api.createMark(classId, { kind, anchor, note: '' }, crypto.randomUUID());
    setNotice({ tone: 'info', text: `已标记${kind === 'important' ? '重点' : '没懂'}${lastFinal ? `（${formatClock(lastFinal.startMs)}）` : '（当前时间点）'}。` });
    void invalidate();
  });

  const panels = {
    transcript: <TranscriptTimeline entries={entries} label="课堂字幕" emptyText={phase === 'live' ? '正在等待转写结果。字幕会在服务器确认后出现。' : '还没有正式字幕。'} />,
    explanation: <ExplanationPanel classId={classId} blockedReason={aiBlocked} transcriptReason={noFinal} slidesAvailable={slidesAvailable} />,
    qa: <QaPanel classId={classId} disabled={(aiBlocked ?? noFinal) !== null} disabledReason={aiBlocked ?? noFinal} />,
  };

  return <main id="main-content" className={styles.page} style={{ ['--classroom-font' as string]: `${fontPx}px` }} data-phase={phase}>
    <header className={styles.header}>
      <div>
        <p className={styles.eyebrow}>{course.data?.name ?? '课程'} · 课堂</p>
        <h1 ref={heading} tabIndex={-1}>{data.title || '未命名课堂'}</h1>
        <p className={styles.small}><span className={`${styles.badge} ${phase === 'live' ? styles.toneRecording : ''}`} data-testid="capture-phase">{phase === 'live' ? CAPTURE_LABEL[flows.capture] : phase === 'prep' ? '准备中 · 未开始采集' : phase === 'elsewhere' ? '正在其他设备或页面采集' : phase === 'stopped_here' ? '本机已停止 · 等待服务端确认' : '已结束 · 处理中'}</span></p>
      </div>
      <FlowStrip flows={flows} />
    </header>
    <DataSourceNote />
    {session.isError && !revoked && <div className={`${styles.banner} ${styles.bannerWarning}`} role="status"><div><strong>课堂状态刷新失败</strong>：{describeError(session.error)} 以下为上次读取的内容。</div></div>}
    {!app.online && <div className={`${styles.banner} ${styles.bannerWarning}`} role="status"><div><strong>当前离线</strong>：已加载的字幕可以继续阅读；开始采集、AI 请求和上传图片已停用。</div></div>}
    {otherActive && <div className={`${styles.banner} ${styles.bannerWarning}`} role="status" data-testid="other-capture"><div>另一节课堂正在采集。同一时间只能采集一节课堂。<Link to={`/classrooms/${otherActive.classId}/live`}>返回正在采集的课堂</Link></div></div>}
    {lostHere && <div className={`${styles.banner} ${styles.bannerDanger}`} role="alert"><div><strong>采集租约已失效</strong>：本机已停止采集。已采集的音频仍会尝试上传，重新开始需要明确操作。</div></div>}
    {boundHere?.classroomRevoked && <div className={`${styles.banner} ${styles.bannerDanger}`} role="alert"><div><strong>该课堂已被删除或撤权</strong>：不能继续采集或上传，课堂内容已从本页移除。<Link to="/classrooms">返回课堂入口</Link></div></div>}
    {phase === 'elsewhere' && <div className={`${styles.banner} ${styles.bannerWarning}`} role="status"><div>这节课堂正在其他设备或页面采集，本页只读。接管采集需要在原设备结束，或在后续版本中明确确认接管。</div></div>}
    {phase === 'ended' && <div className={`${styles.banner} ${styles.bannerSuccess}`} role="status"><div>采集已结束。<Link to={`/classrooms/${classId}/processing`}>查看课后处理</Link> · <Link to={`/classrooms/${classId}/record`}>查看课堂记录</Link></div></div>}
    {phase === 'stopped_here' && <div className={`${styles.banner} ${styles.bannerWarning}`} role="status"><div>本机已停止采集，停止范围尚未被服务端确认。<Link to={`/classrooms/${classId}/processing`}>前往课后处理确认</Link></div></div>}
    {notice && <div className={`${styles.banner} ${notice.tone === 'danger' ? styles.bannerDanger : notice.tone === 'warning' ? styles.bannerWarning : ''}`} role={notice.tone === 'danger' ? 'alert' : 'status'}><div>{notice.text}</div></div>}

    {revoked ? null : phase === 'prep'
      ? <Preparation session={data} courseName={course.data?.name ?? null} blocked={otherActive !== null} busy={busy} onStart={() => run(async () => { await startCapture(app.api, shell, classId, () => crypto.randomUUID()); void invalidate(); })} />
      : <>
        <Workspace panels={panels} focus={focus} />
        <div className={styles.toolbar} role="toolbar" aria-label="课堂工具栏" data-testid="classroom-toolbar">
          <div className={styles.toolbarGroup}>
            {phase === 'live' && (flows.capture === 'paused'
              ? <button type="button" className={styles.button} disabled={busy} onClick={() => void pauseOrResume('resume')}><IconPlayerPlay size={17} aria-hidden="true" /> 继续</button>
              : <button type="button" className={styles.buttonSecondary} disabled={busy} onClick={() => void pauseOrResume('pause')}><IconPlayerPause size={17} aria-hidden="true" /> 暂停</button>)}
            {phase === 'live' && <button type="button" className={styles.buttonDanger} disabled={busy} onClick={() => void stop()}><IconPlayerStop size={17} aria-hidden="true" /> 结束课堂</button>}
          </div>
          <div className={styles.toolbarGroup}>
            <button type="button" className={styles.buttonSecondary} disabled={busy || readOnly} onClick={() => void mark('important')}><IconFlag size={17} aria-hidden="true" /> 重点</button>
            <button type="button" className={styles.buttonSecondary} disabled={busy || readOnly} onClick={() => void mark('unclear')}><IconHelpCircle size={17} aria-hidden="true" /> 没懂</button>
            <button type="button" className={styles.buttonSecondary} onClick={() => setSlidesOpen(true)}><IconPhoto size={17} aria-hidden="true" /> 课件</button>
            <button type="button" className={styles.buttonSecondary} onClick={() => setDetails(true)}><IconInfoCircle size={17} aria-hidden="true" /> 详情</button>
          </div>
          <span className={styles.toolbarSpacer} />
          <div className={styles.toolbarGroup}>
            <button type="button" className={`${styles.buttonGhost} ${focus ? styles.pressed : ''}`} aria-pressed={focus} onClick={() => setFocus(value => !value)}><IconFocus2 size={17} aria-hidden="true" /> {focus ? '退出专注' : '专注模式'}</button>
            <button type="button" className={styles.buttonGhost} aria-label="缩小字幕" disabled={fontPx <= 14} onClick={() => setFontPx(value => Math.max(14, value - 1))}><IconTextDecrease size={17} aria-hidden="true" /></button>
            <span className={styles.small} aria-live="polite">字号 {fontPx}</span>
            <button type="button" className={styles.buttonGhost} aria-label="放大字幕" disabled={fontPx >= 32} onClick={() => setFontPx(value => Math.min(32, value + 1))}><IconTextIncrease size={17} aria-hidden="true" /></button>
          </div>
        </div>
      </>}
    {!revoked && <><Dialog open={slidesOpen} onOpenChange={setSlidesOpen} title="课件图片" description="图片关联到课堂时间；修改会产生新版本，旧版本作为历史依据保留。">
      <SlidesPanel classId={classId} positionMs={positionMs} readOnly={readOnly} />
    </Dialog>
    <Dialog open={details} onOpenChange={setDetails} title="课堂详情" description="五项状态独立显示，任何一项未完成都不会显示为整节课完成。">
      <ClassroomDetails session={data} flows={flows} bound={boundHere} />
    </Dialog></>}
  </main>;
}

function Preparation({ session, courseName, blocked, busy, onStart }: { session: S['ClassSession']; courseName: string | null; blocked: boolean; busy: boolean; onStart: () => void }) {
  const { online } = useAppData();
  const quota = useQuota();
  const [consent, setConsent] = useState(false);
  const [testing, setTesting] = useState(false);
  const [level, setLevel] = useState(0);
  // Sound check is local and independent: it never creates a recording, uploads or charges.
  useEffect(() => {
    if (!testing) { setLevel(0); return; }
    let tick = 0;
    const timer = window.setInterval(() => { tick++; setLevel([35, 62, 48, 70, 41, 58][tick % 6]!); }, 300);
    return () => window.clearInterval(timer);
  }, [testing]);
  const billing = quota.data?.platform_billing_state;
  const canStart = online && consent && !blocked && !busy;
  return <section className={styles.stack} aria-labelledby="prep-title" data-testid="classroom-prep">
    <h2 id="prep-title">课前准备</h2>
    <div className={styles.prepGrid}>
      <div className={styles.panel}>
        <h3>课堂信息</h3>
        <dl className={styles.dl}><dt>课程</dt><dd>{courseName ?? '—'}</dd><dt>标题</dt><dd>{session.title || '未命名课堂'}</dd><dt>语言</dt><dd>{({ auto: '自动识别', zh: '中文', en: '英文', mixed: '中英混合' } as const)[session.language]}</dd></dl>
      </div>
      <div className={styles.panel}>
        <h3>设备与网络</h3>
        <ul className={styles.checkList}>
          <li><span aria-hidden="true">◌</span><span>麦克风：开发预览使用模拟输入，不访问真实设备。点击「开始采集」前不会请求麦克风。</span></li>
          <li><span aria-hidden="true">{online ? '✓' : '!'}</span><span>网络：{online ? '已连接' : '离线，不能开始采集'}</span></li>
        </ul>
        <div className={styles.stack} style={{ marginTop: '.6rem' }}>
          <button type="button" className={styles.buttonSecondary} aria-pressed={testing} onClick={() => setTesting(value => !value)}>{testing ? '结束试音' : '开始试音'}</button>
          <div className={styles.meter} role="meter" aria-label="模拟输入音量" aria-valuemin={0} aria-valuemax={100} aria-valuenow={level}><div className={styles.meterFill} style={{ width: `${level}%` }} /></div>
          <p className={`${styles.small} ${styles.muted}`}>试音只在本机进行，不创建录音、不上传、不计费。（模拟电平）</p>
        </div>
      </div>
      <div className={styles.panel}>
        <h3>额度</h3>
        {quota.isPending ? <p className={styles.muted} role="status">正在读取额度…</p> : quota.isError ? <p className={styles.error} role="alert">{describeError(quota.error)}</p> : <ul className={styles.checkList}>
          {preparationQuotaLines(quota.data).map(line => <li key={line}>{line}</li>)}
          {billing && billing !== 'normal' && <li><span aria-hidden="true">!</span><span>平台额度状态：{billing === 'paused' || billing === 'blocked100' ? '新收费请求已暂停' : '接近上限'}</span></li>}
        </ul>}
      </div>
    </div>
    <div className={styles.panel}>
      <h3>采集与处理说明</h3>
      <ul>{Object.values(CONSENT_TEXT).map(text => <li key={text}>{text}</li>)}</ul>
      <label className={styles.checkbox}><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} /><span>我已了解以上用途和原音 7 天保存期限，同意开始采集</span></label>
    </div>
    {blocked && <p className={styles.toneWarning}>另一节课堂正在采集，请先结束那节课堂。</p>}
    <div className={styles.row}><button type="button" className={styles.button} disabled={!canStart} onClick={onStart}><IconPlayerPlay size={17} aria-hidden="true" /> {busy ? '正在开始…' : '开始采集'}</button>
      <Link className={styles.buttonGhost} to="/classrooms">返回课堂入口</Link></div>
  </section>;
}

function ClassroomDetails({ session, flows, bound }: { session: S['ClassSession']; flows: S['FlowStates']; bound: BoundCapture | null }) {
  const marks = useMarks(session.id);
  const items = flowItems(flows);
  return <div className={styles.stack} data-testid="classroom-details">
    <ul className={styles.list}>{items.map(item => <li key={item.key} className={styles.listItem}><div><strong>{item.name}</strong>：<span className={toneClass[item.tone] ?? ''}>{item.label}</span></div></li>)}</ul>
    <dl className={styles.dl}>
      <dt>开始时间</dt><dd>{session.started_at?.replace('T', ' ').slice(0, 16) ?? '尚未开始'}</dd>
      <dt>本机采集</dt><dd>{bound ? `批次 ${bound.captureEpochId.slice(0, 8)} · 租约序号 ${bound.fenceToken}${bound.active ? '' : '（已停止）'}` : '本机没有绑定采集'}</dd>
      <dt>已知中断</dt><dd>{session.known_gap_count} 段{session.unknown_gap_count ? `；未知缺口 ${session.unknown_gap_count} 段` : ''}</dd>
    </dl>
    <h3>标记</h3>
    {marks.isError ? <p className={styles.error} role="alert">{describeError(marks.error)}</p> : (marks.data?.items.length ?? 0) === 0 ? <p className={`${styles.small} ${styles.muted}`}>还没有标记。</p>
      : <ul className={styles.list} aria-label="本节课的标记">{marks.data!.items.map(mark => <li key={mark.id} className={styles.listItem}><div><strong>{mark.kind === 'important' ? '重点' : '没懂'}</strong>{mark.note ? ` · ${mark.note}` : ''}<p className={`${styles.small} ${styles.muted}`}>{mark.anchor.kind === 'timepoint' ? '时间点标记（待正式字幕后关联）' : mark.anchor.kind === 'source' ? '关联到正式字幕' : '关联到复习内容'}{mark.understood ? ' · 已理解' : ''}</p></div></li>)}</ul>}
    <h3>数据去向</h3>
    <ul className={styles.small}>{Object.values(CONSENT_TEXT).map(text => <li key={text}>{text}</li>)}</ul>
  </div>;
}
