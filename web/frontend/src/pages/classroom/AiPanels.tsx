import { useState } from 'react';
import type { components } from '../../generated/openapi';
import { describeError, useAppData, useInvalidateAccount } from '../../features/data';
import { useClassQuestions, useDraft, useTasks } from '../../features/classroom';
import { TASK_LABEL, TASK_STATE_LABEL, formatYuan } from '../../domain/classroom';
import { PlanDialog } from './PlanDialog';
import styles from './classroom.module.css';

type S = components['schemas'];
type Scope = S['GenerationPlanRequest']['scope'];

/** Prepare a frozen plan (no dispatch, no charge) and open the confirmation dialog. */
function usePlanFlow(classId: string) {
  const { api } = useAppData();
  const invalidate = useInvalidateAccount();
  const [plan, setPlan] = useState<{ value: S['GenerationPlan']; scope: Scope } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState<S['Task'] | null>(null);
  const prepare = async (request: S['GenerationPlanRequest']) => {
    setBusy(true); setError(null); setSubmitted(null);
    try { setPlan({ value: await api.createPlan(classId, request, crypto.randomUUID()), scope: request.scope }); }
    catch (caught) { setError(caught); } finally { setBusy(false); }
  };
  const dialog = <PlanDialog key={plan?.value.id ?? 'none'} plan={plan?.value ?? null} scope={plan?.scope ?? 'all'} onClose={() => setPlan(null)}
    onExecuted={task => { setPlan(null); setSubmitted(task); void invalidate(); }} />;
  return { prepare, dialog, error, busy, submitted };
}

export function TaskList({ classId, kinds, emptyText }: { classId: string; kinds: ReadonlyArray<S['Task']['kind']>; emptyText: string }) {
  const tasks = useTasks(classId);
  const { api } = useAppData();
  const invalidate = useInvalidateAccount();
  const [checked, setChecked] = useState<Record<string, S['TaskReconcile'] | 'error'>>({});
  const items = (tasks.data?.items ?? []).filter(task => kinds.includes(task.kind));
  if (tasks.isPending) return <p className={styles.muted} role="status">正在读取任务…</p>;
  if (tasks.isError) return <p className={styles.error} role="alert">{describeError(tasks.error)}</p>;
  if (!items.length) return <p className={`${styles.small} ${styles.muted}`}>{emptyText}</p>;
  const reconcile = async (task: S['Task']) => {
    try { const result = await api.reconcileTask(task.id, crypto.randomUUID()); setChecked(value => ({ ...value, [task.id]: result })); void invalidate(); }
    catch { setChecked(value => ({ ...value, [task.id]: 'error' })); }
  };
  return <ul className={styles.list} data-testid="task-list">{items.map(task => {
    const result = checked[task.id];
    return <li key={task.id} className={styles.listItem} data-task-state={task.state}>
      <div><strong>{TASK_LABEL[task.kind]}</strong> · <span className={task.state === 'unknown' ? styles.toneWarning : task.state === 'failed' ? styles.toneDanger : ''}>{TASK_STATE_LABEL[task.state]}</span>
        <p className={`${styles.small} ${styles.muted}`}>费用上限 {formatYuan(task.maximum_cost_microyuan)}{task.settled_cost_microyuan ? `，已结算 ${formatYuan(task.settled_cost_microyuan)}` : ''}{task.may_have_charged && !task.settled_cost_microyuan ? '，可能已产生费用' : ''}</p>
        {result && result !== 'error' && <p className={styles.small} role="status">已核对：{TASK_STATE_LABEL[result.state]}。{result.provider_query_supported ? '' : '服务暂不支持远端查询，'}没有重新发送请求。</p>}
        {result === 'error' && <p className={styles.error} role="alert">核对失败，请稍后再试。没有重新发送请求。</p>}
      </div>
      {task.state === 'unknown' && <button type="button" className={styles.buttonSecondary} onClick={() => void reconcile(task)}>核对结果</button>}
    </li>;
  })}</ul>;
}

const EXPLAIN_SCOPES: Array<{ scope: Scope; label: string }> = [{ scope: 'recent30', label: '最近 30 秒' }, { scope: 'recent120', label: '最近 2 分钟' }, { scope: 'thought_group', label: '当前意群' }, { scope: 'slides', label: '课件' }];

/** UI-CLASS-09 on-demand explanation: a request is prepared only when the user picks a scope. */
export function ExplanationPanel({ classId, blockedReason, transcriptReason, slidesAvailable }: { classId: string; blockedReason: string | null; transcriptReason: string | null; slidesAvailable: boolean }) {
  const flow = usePlanFlow(classId);
  const scopeReason = (scope: Scope) => blockedReason ?? (scope === 'slides' ? (slidesAvailable ? null : '还没有可解释的课件图片。') : transcriptReason);
  const notes = [...new Set(EXPLAIN_SCOPES.map(item => scopeReason(item.scope)).filter((reason): reason is string => reason !== null))];
  return <div className={styles.stack}>
    <p className={`${styles.small} ${styles.muted}`}>按需解释：选择范围后先显示将发送的内容与费用，确认后才请求。自动解释默认关闭。</p>
    <div className={styles.row} role="group" aria-label="解释范围">{EXPLAIN_SCOPES.map(item => <button key={item.scope} type="button" className={styles.buttonSecondary} disabled={scopeReason(item.scope) !== null || flow.busy}
      onClick={() => void flow.prepare({ kind: 'ai_explanation', scope: item.scope, seen_sources: [], include_images: item.scope === 'slides', accept_partial_scope: false })}>解释{item.label}</button>)}</div>
    {notes.map(note => <p key={note} className={`${styles.small} ${styles.muted}`}>{note}</p>)}
    {flow.error !== null && <p className={styles.error} role="alert">{describeError(flow.error)}</p>}
    {flow.submitted && <p className={styles.small} role="status">已提交解释请求，完成后结果显示为候选。</p>}
    <TaskList classId={classId} kinds={['ai_explanation']} emptyText="还没有解释请求。" />
    <p className={`${styles.small} ${styles.muted}`}>解释结果的读取接口尚未在当前契约中定义，这里只显示请求状态（见 PR10A-SCOPE-GAPS 第 3.1 条）。</p>
    {flow.dialog}
  </div>;
}

/** Questions keep their draft across layout, focus mode and navigation; drafts are per classroom. */
export function QaPanel({ classId, disabled, disabledReason }: { classId: string; disabled: boolean; disabledReason: string | null }) {
  const [draft, setDraft] = useDraft(classId, 'question');
  const flow = usePlanFlow(classId);
  const questions = useClassQuestions(classId);
  const items = [...(questions.data?.items ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const submit = () => void flow.prepare({ kind: 'ai_question', scope: 'recent120', question: draft.trim(), seen_sources: [], include_images: false, accept_partial_scope: false });
  return <div className={styles.stack}>
    <div className={styles.field}>
      <label htmlFor={`question-${classId}`}>向 AI 提问（基于最近 2 分钟的正式字幕）</label>
      <textarea id={`question-${classId}`} value={draft} maxLength={2000} onChange={event => setDraft(event.target.value)} disabled={disabled} />
      <span className={styles.hint}>草稿只保存在当前页面会话中，切换布局或页面不会丢失。</span>
    </div>
    <div className={styles.row}><button type="button" className={styles.button} disabled={disabled || flow.busy || !draft.trim()} onClick={submit}>准备发送</button></div>
    {disabled && disabledReason && <p className={`${styles.small} ${styles.muted}`}>{disabledReason}</p>}
    {flow.error !== null && <p className={styles.error} role="alert">{describeError(flow.error)}</p>}
    {flow.submitted && <p className={styles.small} role="status">问题已提交，回答完成后显示在下方。</p>}
    {questions.isError ? <p className={styles.error} role="alert">{describeError(questions.error)}</p> : items.length === 0 ? <p className={`${styles.small} ${styles.muted}`}>本节课还没有问题。</p>
      : <ul className={styles.list} aria-label="本节课的问题">{items.map(item => <li key={item.id} className={styles.listItem} data-question-state={item.state}><div>
        <strong>{item.question}</strong>
        <p className={`${styles.small} ${item.state === 'unknown' ? styles.toneWarning : styles.muted}`}>{TASK_STATE_LABEL[item.state]}{item.understood ? ' · 已理解' : ''}</p>
        {item.answer_markdown && <p className={styles.answer}>{item.answer_markdown}</p>}
      </div></li>)}</ul>}
    <TaskList classId={classId} kinds={['ai_question']} emptyText="" />
    {flow.dialog}
  </div>;
}
