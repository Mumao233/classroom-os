import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { components } from '../../generated/openapi';
import { ApiClientError } from '../../api/client';
import { alignTranscripts, audioState, formatMs, kindLabel, retranscribeErrors } from '../../domain/review';
import { formatYuan, TASK_STATE_LABEL } from '../../domain/classroom';
import { describeError, isConflict, useAppData, useCourse, useInvalidateAccount } from '../../features/data';
import { isDefinitiveRejection, useClassSession, useDraft, useTasks } from '../../features/classroom';
import { useReviewLibrary, useTranscriptSegments, useTranscriptVersions } from '../../features/review';
import { DataSourceNote, Empty, Loading, OfflineBanner, QueryError } from '../common';
import { docState } from './ReviewHub';
import pages from '../pages.module.css';
import styles from './review.module.css';

type S = components['schemas'];
/** A definitive rejection ends an intent: the next attempt gets a new key. A lost or ambiguous reply is replayed with the same key. */
const answered = isDefinitiveRejection;

/**
 * Single-class review entry (UI-REVIEW-03): the same documents as the global and course entries,
 * a personal note, and the PR-06c retranscription flow — frozen plan, explicit confirmation, the
 * four 0.3.0 refusal codes, unknown results that can only be reconciled, and explicit adoption.
 */
export function ClassReview() {
  const { classId = '' } = useParams();
  const location = useLocation();
  const session = useClassSession(classId);
  const course = useCourse(session.data?.course_id ?? '', Boolean(session.data));
  const documents = useReviewLibrary({ class_session_id: classId });
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [classId]);
  if (session.isPending) return <main id="main-content" className={styles.page}><Loading label="正在读取课堂…" /></main>;
  // A denied classroom has no data left (revocation ledger); a transport failure keeps it readable.
  if (!session.data) return <main id="main-content" className={styles.page}><QueryError error={session.error} /><Link to="/review">返回复习</Link></main>;
  const data = session.data;
  const from = `${location.pathname}${location.search}`;
  return <main id="main-content" className={styles.page}>
    <div className={pages.heading}>
      <div className={pages.wrap}><p>{course.data?.name ?? '课程'} · 课堂复习</p><h1 ref={heading} tabIndex={-1}>{data.title || '未命名课堂'}</h1></div>
      <div className={pages.row}><Link className={pages.buttonSecondary} to={`/classrooms/${classId}/record`}>课堂记录</Link><Link className={pages.buttonGhost} to="/review">全部复习</Link></div>
    </div>
    <DataSourceNote />
    <OfflineBanner scope="已加载的复习资料可以阅读；" />
    <section className={pages.panel} aria-labelledby="class-docs"><h2 id="class-docs">本课资料</h2>
      {documents.isError && !documents.data ? <QueryError error={documents.error} retry={() => void documents.refetch()} /> : documents.isPending ? <Loading label="正在读取资料…" /> :
        documents.data.items.length === 0 ? <Empty title="这节课还没有复习资料" description="生成课堂总结或保存个人笔记后会出现在这里。" /> :
          <ul className={pages.list} data-testid="class-review-list">{documents.data.items.map(doc => <li key={doc.id} className={pages.listItem} data-document-id={doc.id}>
            <div><strong>{doc.title || '未命名'}</strong><p>{kindLabel[doc.kind]} · {docState(doc)} · {doc.understood ? '已理解' : '尚未理解'}</p></div>
            <Link to={`/review/items/${doc.id}`} state={{ from }}>打开</Link></li>)}</ul>}
      <NewNote classId={classId} />
    </section>
    <TranscriptVersions session={data} />
    <Retranscribe session={data} />
  </main>;
}

function NewNote({ classId }: { classId: string }) {
  const app = useAppData();
  const invalidate = useInvalidateAccount();
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [draftId, setDraftId] = useState(() => crypto.randomUUID());
  const keyRef = useRef<{ fingerprint: string; key: string } | null>(null);
  const create = useMutation({
    mutationFn: () => {
      const payload: S['ReviewDocumentCreate'] = { class_session_id: classId, title, body_markdown: body, draft_id: draftId };
      const fingerprint = JSON.stringify(payload);
      if (!keyRef.current || keyRef.current.fingerprint !== fingerprint) keyRef.current = { fingerprint, key: crypto.randomUUID() };
      return app.api.createPersonalNote(payload, keyRef.current.key);
    },
    onSuccess: () => { keyRef.current = null; setTitle(''); setBody(''); setDraftId(crypto.randomUUID()); void invalidate(); },
    onError: error => { if (answered(error)) keyRef.current = null; },
  });
  return <form className={pages.form} style={{ maxWidth: 'none', marginTop: 14 }} onSubmit={event => { event.preventDefault(); create.mutate(); }} aria-labelledby="new-note-heading">
    <h3 id="new-note-heading">新建个人笔记</h3>
    <fieldset disabled={create.isPending} className={pages.stack} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
    <div className={pages.field}><label htmlFor="note-title">标题</label><input id="note-title" value={title} maxLength={200} onChange={event => setTitle(event.target.value)} /></div>
    <div className={pages.field}><label htmlFor="note-body">内容</label><textarea id="note-body" value={body} maxLength={120000} onChange={event => setBody(event.target.value)} /></div>
    </fieldset>
    {create.isError && <p role="alert" className={pages.error}>{describeError(create.error)}</p>}
    {create.isSuccess && <p role="status" className={pages.success}>笔记已保存。</p>}
    <div><button type="submit" className={pages.button} disabled={!app.online || create.isPending || !title.trim() || !body.trim()}>{create.isPending ? '正在保存…' : '保存笔记'}</button></div>
  </form>;
}

/** Transcript versions: compare a retranscription with the adopted version by time; adopt or switch back explicitly. */
function TranscriptVersions({ session }: { session: S['ClassSession'] }) {
  const app = useAppData();
  const client = useQueryClient();
  const invalidate = useInvalidateAccount();
  const versions = useTranscriptVersions(session.id);
  const list = useMemo(() => [...(versions.data?.items ?? [])].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id)), [versions.data]);
  // Baseline: the adopted version, else the effective realtime transcript (a first retranscription has no explicit adoption yet).
  const adopted = list.find(item => item.id === session.adopted_transcript_version_id) ?? list.find(item => item.adopted) ?? list.filter(item => item.kind !== 'retranscription').at(-1) ?? null;
  const [candidateId, setCandidateId] = useState<string | null>(null);
  const candidate = list.find(item => item.id === candidateId) ?? list.filter(item => item.id !== adopted?.id && item.kind === 'retranscription').at(-1) ?? null;
  const left = useTranscriptSegments(adopted?.id ?? null);
  const right = useTranscriptSegments(candidate?.id ?? null);
  const [notice, setNotice] = useState<string | null>(null);
  const keyRef = useRef<{ target: string; base: number; key: string } | null>(null);
  const adopt = useMutation({
    mutationFn: (target: S['TranscriptVersion']) => {
      if (!keyRef.current || keyRef.current.target !== target.id || keyRef.current.base !== session.revision) keyRef.current = { target: target.id, base: session.revision, key: crypto.randomUUID() };
      return app.api.adoptTranscript(session.id, { base_revision: session.revision, transcript_version_id: target.id }, keyRef.current.key);
    },
    onSuccess: updated => { keyRef.current = null; client.setQueryData(['account', app.accountKey, 'class-session', session.id], updated); void invalidate(); setCandidateId(null); setNotice('已采用所选转写版本。之后新的问答、总结和导出会读取它；之前生成的内容和快照不变，可以随时切回。'); },
    onError: error => { if (answered(error)) keyRef.current = null; if (isConflict(error)) void invalidate(); setNotice(isConflict(error) ? '课堂已在其他地方更新，采用没有生效。请核对最新的转写版本后再选择。' : describeError(error)); },
  });
  const label = (version: S['TranscriptVersion']) => `${version.kind === 'realtime' ? '实时转写' : version.kind === 'retranscription' ? '重新转写' : '导入'} · ${version.created_at.slice(0, 16).replace('T', ' ')}`;
  const rows = alignTranscripts(left.data?.items ?? [], right.data?.items ?? []);
  const compareUnread = left.isPending || right.isPending || left.isError || right.isError;
  return <section className={pages.panel} aria-labelledby="transcript-versions"><h2 id="transcript-versions">转写版本</h2>
    {versions.isError ? <QueryError error={versions.error} /> : versions.isPending ? <Loading label="正在读取转写版本…" /> : list.length === 0 ? <Empty title="还没有转写版本" description="课堂转写完成后会出现在这里。" /> : <>
      <ul className={styles.versions} data-testid="transcript-version-list">{list.map(version => { const current = version.id === adopted?.id; return <li key={version.id} className={styles.version} aria-current={current} data-version-id={version.id} data-adopted={String(current)}>
        <span><strong>{label(version)}</strong>{current ? (version.adopted || session.adopted_transcript_version_id === version.id ? ' · 当前采用' : ' · 当前使用（尚未明确采用）') : version.kind === 'retranscription' ? ' · 候选（未采用）' : ''}</span>
        {!current && <button type="button" className={pages.buttonGhost} onClick={() => setCandidateId(version.id)}>与当前版本对照</button>}
        {!current && version.state === 'sealed' && <button type="button" className={pages.buttonSecondary} disabled={!app.online || adopt.isPending} onClick={() => adopt.mutate(version)} data-testid={`adopt-transcript-${version.id}`}>{version.kind === 'retranscription' ? '采用此版本' : '切回此版本'}</button>}
      </li>; })}</ul>
      {notice && <p role="status" className={pages.small} data-testid="transcript-notice">{notice}</p>}
      {candidate && !adopted && <div style={{ marginTop: 12, overflowX: 'auto' }} data-testid="transcript-preview">
        <p className={pages.small}>这节课没有更早的转写可以对照，下面直接预览候选内容；预览不会改变采用状态。</p>
        <table className={styles.align}><caption className={pages.srOnly}>候选转写预览</caption><thead><tr><th scope="col">时间</th><th scope="col">候选（{label(candidate)}）</th></tr></thead>
          <tbody>{right.isError ? <tr><td colSpan={2}><span role="alert">候选内容读取失败：{describeError(right.error)}</span></td></tr> : right.isPending ? <tr><td colSpan={2}>正在读取候选内容…</td></tr>
            : right.data.items.length === 0 ? <tr><td colSpan={2}>候选版本没有片段。</td></tr> : right.data.items.map(item => <tr key={item.id}><td>{formatMs(item.start_ms)}</td><td>{item.text}</td></tr>)}</tbody></table>
      </div>}
      {candidate && adopted && candidate.id !== adopted.id && <div style={{ marginTop: 12, overflowX: 'auto' }}>
        <table className={styles.align} data-testid="transcript-compare"><caption className={pages.srOnly}>按时间对照两个转写版本</caption>
          <thead><tr><th scope="col">时间</th><th scope="col">当前采用（{label(adopted)}）</th><th scope="col">候选（{label(candidate)}）</th></tr></thead>
          <tbody>{compareUnread ? <tr><td colSpan={3}>{left.isError || right.isError ? <span role="alert">转写内容读取失败，暂不能对照。</span> : '正在读取转写内容…'}</td></tr> : rows.map((row, index) => <tr key={index}><td>{formatMs(row.startMs)}</td><td>{row.before?.text ?? <span className={pages.muted}>（无对应片段）</span>}</td>
            <td className={row.before && row.after && row.before.text !== row.after.text ? styles.changed : undefined}>{row.after?.text ?? <span className={pages.muted}>（无对应片段）</span>}{row.after && row.after.timestamp_source === 'capture_mapping' && row.after.stream_id === null ? <span className={pages.srOnly}>（时间按采集位置映射）</span> : null}</td></tr>)}</tbody>
        </table>
        <p className={`${pages.small} ${pages.muted}`}>候选的时间按采集位置映射；对照不会改变采用状态。</p>
      </div>}
    </>}
  </section>;
}

/** PR-06c retranscription: prepare → confirm frozen scope and maximum cost → execute; refusals by code only. */
function Retranscribe({ session }: { session: S['ClassSession'] }) {
  const app = useAppData();
  const invalidate = useInvalidateAccount();
  const tasks = useTasks(session.id);
  const [scope, setScope] = useState<'all' | 'range'>('all');
  const [startSec, setStartSec] = useState('0');
  const [endSec, setEndSec] = useState('60');
  const [acceptPartial, setAcceptPartial] = useState(false);
  // The plan is shown and executed together with the exact request it was prepared from; any input change invalidates it.
  // A submitted (pending or unconfirmed) execution is kept in the account-scoped classroom shell, so leaving
  // and returning restores the same plan, intent and key for a same-key replay.
  const [pendingRaw, setPendingRaw] = useDraft(session.id, 'retranscribe:pending-execute');
  const restored = parsePending(pendingRaw);
  const [prepared, setPreparedState] = useState<{ plan: S['GenerationPlan']; request: S['GenerationPlanRequest'] } | null>(restored ? { plan: restored.plan, request: restored.request } : null);
  const setPrepared = (value: typeof prepared) => { setPreparedState(value); if (!value) setPendingRaw(''); };
  const plan = prepared?.plan ?? null;
  const [sourceChanged, setSourceChanged] = useState(false);
  // A lost or ambiguous execute reply: the outcome is unconfirmed, the plan and key are kept for a same-key replay.
  // A submission restored after leaving the page never got a definitive reply here: its outcome is unconfirmed.
  const [unconfirmed, setUnconfirmed] = useState(restored !== null);
  const change = <T,>(setter: (value: T) => void) => (value: T) => { setter(value); setPrepared(null); setSourceChanged(false); };
  const [refusal, setRefusal] = useState<{ code: string; text: string } | null>(null);
  const prepareKey = useRef<{ fingerprint: string; key: string } | null>(null);
  const lastRequest = useRef('{}');
  const executeKey = useRef<{ intent: string; key: string } | null>(restored ? { intent: restored.intent, key: restored.key } : null);
  const audio = audioState(session, app.now());
  const showError = (error: unknown) => {
    const code = error instanceof ApiClientError ? error.code : undefined;
    const text = code && retranscribeErrors[code] ? retranscribeErrors[code]! : describeError(error);
    setRefusal({ code: code ?? 'unknown', text });
  };
  const prepare = useMutation({
    mutationFn: () => {
      const body: S['GenerationPlanRequest'] = { kind: 'asr_retranscribe', scope, seen_sources: [], include_images: false, accept_partial_scope: acceptPartial,
        ...(scope === 'range' ? { start_ms: Math.round(Number(startSec) * 1000), end_ms: Math.round(Number(endSec) * 1000) } : {}) };
      const fingerprint = JSON.stringify(body);
      lastRequest.current = fingerprint;
      if (!prepareKey.current || prepareKey.current.fingerprint !== fingerprint) prepareKey.current = { fingerprint, key: crypto.randomUUID() };
      return app.api.createPlan(session.id, body, prepareKey.current.key);
    },
    onMutate: () => { setRefusal(null); setPrepared(null); setSourceChanged(false); },
    onSuccess: next => { prepareKey.current = null; setPrepared({ plan: next, request: JSON.parse(lastRequest.current) as S['GenerationPlanRequest'] }); },
    onError: error => { if (answered(error)) prepareKey.current = null; showError(error); },
  });
  const execute = useMutation({
    mutationFn: ({ current, original }: { current: NonNullable<typeof prepared>; original: boolean }) => {
      // Confirming the frozen original is a separate intent with its own key.
      const intent = `${current.plan.id}:${original}`;
      if (!executeKey.current || executeKey.current.intent !== intent) executeKey.current = { intent, key: crypto.randomUUID() };
      setPendingRaw(JSON.stringify({ plan: current.plan, request: current.request, intent, key: executeKey.current.key, unconfirmed: false } satisfies PendingExecute));
      return app.api.executePlan(current.plan.id, { plan_sha256: current.plan.plan_sha256, confirmed_destination_revision: current.plan.destination.configuration_revision, confirm_send: true, confirm_images: false,
        confirm_original_snapshot: original, confirm_partial_scope: current.request.accept_partial_scope }, executeKey.current.key);
    },
    onMutate: () => setRefusal(null),
    onSuccess: () => { executeKey.current = null; setPrepared(null); setSourceChanged(false); setUnconfirmed(false); void invalidate(); },
    onError: error => {
      if (!answered(error)) {
        setUnconfirmed(true);
        const kept = parsePending(pendingRawRef.current);
        if (kept) setPendingRaw(JSON.stringify({ ...kept, unconfirmed: true }));
        void tasks.refetch();
        return;
      }
      executeKey.current = null;
      setPendingRaw('');
      setUnconfirmed(false);
      if (error instanceof ApiClientError && error.code === 'SOURCE_CHANGED') { setSourceChanged(true); return; }
      showError(error);
    },
  });
  const pendingRawRef = useRef(pendingRaw);
  pendingRawRef.current = pendingRaw;
  const retranscriptions = (tasks.data?.items ?? []).filter(item => item.kind === 'asr_retranscribe');
  const rangeInvalid = scope === 'range' && !(Number(endSec) > Number(startSec) && Number(startSec) >= 0);
  return <section className={pages.panel} aria-labelledby="retranscribe-heading"><h2 id="retranscribe-heading">重新转写</h2>
    <p className={`${pages.small} ${pages.muted}`}>用已保存的原音重新识别，结果是新的转写候选，不会自动采用。开发预览只使用合成结果，不会调用真实服务。</p>
    <p className={pages.small} data-testid="retranscribe-audio">{audio === 'expired' ? '原音已过期，服务端会拒绝重新转写。' : audio === 'in_period' ? `原音保留至 ${session.audio_expires_at!.slice(0, 10)}。` : '原音期限未知。'}</p>
    <form className={pages.form} style={{ maxWidth: 'none' }} onSubmit={event => { event.preventDefault(); prepare.mutate(); }}>
      {/* Inputs are locked while a plan is prepared or submitted, so the confirmation always matches them. */}
      <fieldset disabled={prepare.isPending || execute.isPending || unconfirmed} className={pages.stack} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
      <fieldset className={pages.row} style={{ border: 0, padding: 0, margin: 0 }}><legend className={pages.fieldLegend}>范围</legend>
        <label><input type="radio" name="rt-scope" checked={scope === 'all'} onChange={() => change(setScope)('all')} /> 整节课</label>
        <label><input type="radio" name="rt-scope" checked={scope === 'range'} onChange={() => change(setScope)('range')} /> 时间范围</label>
      </fieldset>
      {scope === 'range' && <div className={pages.formGrid}>
        <div className={pages.field}><label htmlFor="rt-start">开始（秒）</label><input id="rt-start" type="number" min={0} value={startSec} onChange={event => change(setStartSec)(event.target.value)} aria-invalid={rangeInvalid || undefined} /></div>
        <div className={pages.field}><label htmlFor="rt-end">结束（秒）</label><input id="rt-end" type="number" min={1} value={endSec} onChange={event => change(setEndSec)(event.target.value)} aria-invalid={rangeInvalid || undefined} /></div>
      </div>}
      <label className={pages.small}><input type="checkbox" checked={acceptPartial} onChange={event => change(setAcceptPartial)(event.target.checked)} /> 原音有缺失分片时，同意只按已有部分重新转写</label>
      <div><button type="submit" className={pages.buttonSecondary} disabled={!app.online || prepare.isPending || rangeInvalid}>{prepare.isPending ? '正在准备…' : '准备重新转写'}</button></div>
      </fieldset>
    </form>
    {refusal && <div className={`${pages.banner} ${refusal.code === 'QUOTA_SESSION_EXCEEDED' ? pages.bannerWarning : pages.bannerDanger}`} role="alert" data-testid="retranscribe-refusal" data-code={refusal.code}>
      <div><strong>{refusal.code === 'QUOTA_SESSION_EXCEEDED' ? '待审批' : '未提交'}</strong>：{refusal.text}</div></div>}
    {plan && <div className={pages.banner} data-testid="retranscribe-plan" style={{ marginTop: 12 }}>
      <div className={pages.stack}>
        <strong>确认后才会提交</strong>
        <dl className={pages.dl}>
          <dt>范围</dt><dd data-testid="plan-scope">{prepared!.request.scope === 'range' ? `${formatMs(prepared!.request.start_ms ?? 0)}–${formatMs(prepared!.request.end_ms ?? 0)}` : '整节课'}</dd>
          <dt>原音清单</dt><dd>已冻结（摘要 {plan.audio_manifest_sha256?.slice(0, 12)}…，{plan.audio_chunk_ids.length} 个分片）</dd>
          <dt>计量时长</dt><dd>{formatMs(plan.quota_units)}（计入本月重新转写时长）</dd>
          <dt>最高费用</dt><dd>{formatYuan(plan.maximum_cost_microyuan)}</dd>
          <dt>发送到</dt><dd data-testid="plan-destination">{plan.destination.provider} · {plan.destination.model} · {plan.destination.region}（配置 {plan.destination.configuration_revision}）</dd>
          <dt>有效期至</dt><dd>{plan.expires_at.slice(0, 16).replace('T', ' ')}</dd>
          {prepared!.request.accept_partial_scope && <><dt>缺失分片</dt><dd className={pages.warning}>已同意按已有部分重新转写</dd></>}
        </dl>
        {unconfirmed ? <div className={`${pages.banner} ${pages.bannerWarning}`} role="alert" data-testid="retranscribe-unconfirmed"><div className={pages.stack}>
          <span><strong>提交结果待确认</strong>：没有收到服务端的明确答复，这次可能已经提交。请先查看下方任务列表；需要时用同一操作再确认一次，不会重复提交。</span>
          <div><button type="button" className={pages.buttonSecondary} disabled={!app.online || execute.isPending} onClick={() => execute.mutate({ current: prepared!, original: executeKey.current?.intent.endsWith(':true') ?? false })}>用同一操作再确认</button></div></div></div>
          : sourceChanged ? <div className={`${pages.banner} ${pages.bannerWarning}`} role="alert" data-testid="retranscribe-source-changed"><div className={pages.stack}>
          <span>原音在准备后有变化（例如又有分片上传完成）。可以重新准备，或明确选择按准备时冻结的原音提交。</span>
          <div className={pages.row}><button type="button" className={pages.buttonSecondary} disabled={execute.isPending} onClick={() => prepare.mutate()}>重新准备</button>
            <button type="button" className={pages.buttonSecondary} disabled={!app.online || execute.isPending} onClick={() => execute.mutate({ current: prepared!, original: true })}>按准备时的原音提交</button></div></div></div>
          : <div className={pages.row}><button type="button" className={pages.button} disabled={!app.online || execute.isPending} onClick={() => execute.mutate({ current: prepared!, original: false })} data-testid="retranscribe-confirm">确认并提交</button>
            {/* The submitted plan, body and key stay until a definitive reply: no cancel while it is pending. */}
            <button type="button" className={pages.buttonGhost} disabled={execute.isPending} onClick={() => setPrepared(null)}>取消</button></div>}
      </div>
    </div>}
    <h3 style={{ marginTop: 16 }}>重新转写任务</h3>
    {tasks.isError && !tasks.data ? <QueryError error={tasks.error} retry={() => void tasks.refetch()} /> : tasks.isPending ? <Loading label="正在读取任务…" /> : retranscriptions.length === 0 ? <p className={pages.muted}>还没有重新转写任务。</p> :
      <ul className={pages.list} data-testid="retranscribe-tasks">{retranscriptions.map(item => <RetranscribeTask key={item.id} task={item} retried={retranscriptions.some(other => other.parent_task_id === item.id)} />)}</ul>}
  </section>;
}

type PendingExecute = { plan: S['GenerationPlan']; request: S['GenerationPlanRequest']; intent: string; key: string; unconfirmed: boolean };
function parsePending(raw: string): PendingExecute | null {
  try {
    const value = JSON.parse(raw) as Partial<PendingExecute>;
    return value.plan && value.request && typeof value.intent === 'string' && typeof value.key === 'string' ? { ...value, unconfirmed: value.unconfirmed === true } as PendingExecute : null;
  } catch { return null; }
}

function RetranscribeTask({ task, retried }: { task: S['Task']; retried: boolean }) {
  const app = useAppData();
  const invalidate = useInvalidateAccount();
  const [confirmDuplicate, setConfirmDuplicate] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const keyRef = useRef<string | null>(null);
  const reconcile = useMutation({
    mutationFn: () => app.api.reconcileTask(task.id),
    onSuccess: value => { void invalidate(); setResult(`核对完成：状态仍为${TASK_STATE_LABEL[value.state]}。${value.provider_query_supported ? '' : '供应商不支持查询，'}${value.new_submission ? '' : '没有重新提交。'}`); },
  });
  const retry = useMutation({
    mutationFn: () => { keyRef.current ??= crypto.randomUUID(); return app.api.retryTask(task.id, { reason: '用户在复习页确认重试', confirm_duplicate_billing: confirmDuplicate, original_attempt_id: task.attempt_id! }, keyRef.current); },
    onSuccess: () => { keyRef.current = null; void invalidate(); setResult('已创建新的重试任务；原尝试保留不变。'); },
    onError: error => { if (answered(error)) keyRef.current = null; },
  });
  const retryError = retry.error instanceof ApiClientError && retry.error.code && retranscribeErrors[retry.error.code] ? retranscribeErrors[retry.error.code] : retry.isError ? describeError(retry.error) : null;
  // Only an unknown outcome may be retried (06c), once per attempt: after a linked retry exists, follow that task instead.
  const retryable = task.state === 'unknown' && task.provider_outcome === 'unknown' && task.attempt_id !== null && !retried;
  return <li className={pages.listItem} data-task-id={task.id} data-state={task.state}>
    <div>
      <strong>{TASK_STATE_LABEL[task.state]}</strong>
      <p>{task.created_at.slice(0, 16).replace('T', ' ')}{task.parent_task_id ? ' · 重试' : ''}{task.may_have_charged && task.state !== 'succeeded' ? ' · 可能已产生费用' : ''}</p>
      {task.state === 'unknown' && <p className={pages.warning}>结果未知：不会自动重试。请先核对；确需重试时必须确认可能重复计费。</p>}
      {task.state === 'failed' && task.provider_outcome === 'succeeded' && task.error_code === 'AUDIO_DELETED' && <p className={pages.warning}>识别已完成，但原音在完成前被删除或撤销，结果没有发布；费用记录保留。</p>}
      {retried && <p className={pages.small}>已有重试任务，请查看上方的新任务。</p>}
      {result && <p role="status" className={pages.small}>{result}</p>}
      {retryError && <p role="alert" className={pages.error}>{retryError}</p>}
      {reconcile.isError && <p role="alert" className={pages.error}>核对没有完成：{describeError(reconcile.error)}。任务状态仍按上方显示，请稍后再核对。</p>}
      {retryable && <div className={pages.row} style={{ marginTop: 6 }}>
        {task.may_have_charged && <label className={pages.small}><input type="checkbox" checked={confirmDuplicate} onChange={event => setConfirmDuplicate(event.target.checked)} /> 我知道原尝试可能已收费，重试可能重复计费</label>}
        <button type="button" className={pages.buttonSecondary} disabled={!app.online || retry.isPending || (task.may_have_charged && !confirmDuplicate)} onClick={() => retry.mutate()}>重试</button>
      </div>}
    </div>
    {task.state === 'unknown' && <button type="button" className={pages.buttonGhost} disabled={!app.online || reconcile.isPending} onClick={() => reconcile.mutate()}>核对状态</button>}
  </li>;
}
