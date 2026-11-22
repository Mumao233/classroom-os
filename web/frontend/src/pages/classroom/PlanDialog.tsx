import { useState } from 'react';
import type { components } from '../../generated/openapi';
import { Dialog } from '../../ui';
import { describeError, useAppData } from '../../features/data';
import { ApiClientError } from '../../api/client';
import { useSlides } from '../../features/classroom';
import { formatClock, formatYuan, TASK_LABEL } from '../../domain/classroom';
import styles from './classroom.module.css';

type S = components['schemas'];
const SCOPE_LABEL: Record<S['GenerationPlanRequest']['scope'], string> = { recent30: '最近 30 秒', recent120: '最近 2 分钟', thought_group: '当前意群', slides: '课件', all: '整节课', marked: '已标记内容', range: '指定范围' };

/**
 * Shows exactly what a frozen plan will send (scope, missing material, sources, provider, batches,
 * cost ceiling and quota) and dispatches only after explicit confirmation. Images and a partial
 * scope each need their own confirmation. Nothing is sent when the dialog is dismissed.
 */
export function PlanDialog({ plan, scope, onClose, onExecuted }: { plan: S['GenerationPlan'] | null; scope: S['GenerationPlanRequest']['scope']; onClose: () => void; onExecuted: (task: S['Task']) => void }) {
  const { api } = useAppData();
  const [confirmSend, setConfirmSend] = useState(false);
  const [confirmImages, setConfirmImages] = useState(false);
  const [confirmPartial, setConfirmPartial] = useState(false);
  const [confirmOriginal, setConfirmOriginal] = useState(false);
  const [changed, setChanged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [key] = useState(() => crypto.randomUUID());
  // The changed-source confirmation is a different request body, so it has its own UUID key.
  const [originalKey] = useState(() => crypto.randomUUID());
  const slides = useSlides(plan?.class_session_id ?? '', plan !== null);
  if (!plan) return null;
  const snapshot = plan.source_snapshot;
  const refs = snapshot?.source_refs ?? [];
  const transcriptCount = refs.filter(ref => ref.kind === 'transcript_segment').length;
  const imageRefs = refs.filter(ref => ref.kind === 'slide_version');
  // Each frozen image is named, with the version it refers to, before the user confirms sending it.
  const imageLabel = (ref: S['SourceRef']) => {
    const slide = slides.data?.items.find(item => item.id === ref.id);
    return `${slide?.title ?? '课件图片'}（版本 ${ref.revision_id.slice(0, 8)}${slide && slide.version_id !== ref.revision_id ? '，准备后已更新' : ''}）`;
  };
  const partial = snapshot !== null && !snapshot.scope_complete;
  // A changed source is sent only after the user explicitly chooses the original snapshot.
  const sourceChanged = changed || plan.source_status === 'changed';
  const ready = confirmSend && (!plan.contains_images || confirmImages) && (!partial || confirmPartial) && (!sourceChanged || confirmOriginal) && plan.source_status !== 'unavailable' && !busy;
  const execute = async () => {
    setBusy(true); setError(null);
    try {
      const task = await api.executePlan(plan.id, { plan_sha256: plan.plan_sha256, confirmed_destination_revision: plan.destination.configuration_revision, confirm_send: true,
        confirm_images: plan.contains_images && confirmImages, confirm_original_snapshot: sourceChanged && confirmOriginal, confirm_partial_scope: partial && confirmPartial }, sourceChanged && confirmOriginal ? originalKey : key);
      onExecuted(task);
    } catch (caught) {
      if (caught instanceof ApiClientError && caught.code === 'SOURCE_CHANGED') setChanged(true);
      setError(caught);
    } finally { setBusy(false); }
  };
  return <Dialog open onOpenChange={open => { if (!open) onClose(); }} title={`确认发送：${TASK_LABEL[plan.kind]}`} description="确认前不会发送任何内容，也不会产生费用。">
    <div className={styles.stack} data-testid="plan-dialog">
      {partial && <div className={`${styles.banner} ${styles.bannerWarning}`} role="note"><div><strong>材料不完整，将按部分范围生成</strong><ul>{snapshot.missing_reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>生成结果会标注为部分范围，不会作为整节课的完整总结。</div></div>}
      <dl className={styles.dl}>
        <dt>范围</dt><dd>{SCOPE_LABEL[scope]}{snapshot ? `（${formatClock(snapshot.start_ms)}–${formatClock(snapshot.end_ms)}）` : ''}</dd>
        <dt>来源</dt><dd>{snapshot ? [transcriptCount ? `${transcriptCount} 段正式字幕` : null, imageRefs.length ? `${imageRefs.length} 张课件图片` : null].filter(Boolean).join('、') + '（固定版本）' : '无来源'}{plan.source_status === 'changed' ? '；来源在准备后有更新' : plan.source_status === 'unavailable' ? '；部分来源已不可用' : ''}</dd>
        <dt>服务</dt><dd>{plan.destination.provider} · {plan.destination.model} · {plan.destination.region}</dd>
        <dt>预计批次</dt><dd>{plan.estimated_batches}</dd>
        <dt>费用上限</dt><dd>{formatYuan(plan.maximum_cost_microyuan)}（占用额度 {plan.quota_units} 次）</dd>
        <dt>确认有效期</dt><dd>{plan.expires_at.replace('T', ' ').slice(0, 16)} 前</dd>
      </dl>
      {plan.contains_images && <><ul className={styles.list} aria-label="将发送的课件图片">{imageRefs.map(ref => <li key={`${ref.id}:${ref.revision_id}`} className={styles.listItem}>{imageLabel(ref)}</li>)}</ul>
        <label className={styles.checkbox}><input type="checkbox" checked={confirmImages} onChange={event => setConfirmImages(event.target.checked)} /><span>同时发送以上 {imageRefs.length} 张课件图片</span></label></>}
      {sourceChanged && <label className={styles.checkbox}><input type="checkbox" checked={confirmOriginal} onChange={event => setConfirmOriginal(event.target.checked)} /><span>来源在准备后已更新，仍按准备时的原版本发送（也可以取消后重新准备）</span></label>}
      {partial && <label className={styles.checkbox}><input type="checkbox" checked={confirmPartial} onChange={event => setConfirmPartial(event.target.checked)} /><span>我知道材料不完整，确认按部分范围生成</span></label>}
      <label className={styles.checkbox}><input type="checkbox" checked={confirmSend} onChange={event => setConfirmSend(event.target.checked)} /><span>确认把上述内容发送给 {plan.destination.provider} 处理</span></label>
      {error !== null && <p className={styles.error} role="alert">{describeError(error)}</p>}
      <div className={styles.row}><button type="button" className={styles.button} disabled={!ready} onClick={() => void execute()}>{busy ? '正在提交…' : '确认发送'}</button><button type="button" className={styles.buttonSecondary} onClick={onClose}>取消</button></div>
      <p className={`${styles.small} ${styles.muted}`}>结果为候选内容，不会自动替换已采用的版本；结果未知时不会自动重试。</p>
    </div>
  </Dialog>;
}
