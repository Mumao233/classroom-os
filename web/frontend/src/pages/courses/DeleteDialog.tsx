import { useEffect, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import type { components } from '../../generated/openapi';
import { Dialog } from '../../ui';
import { describeError, useAppData, useInvalidateAccount } from '../../features/data';
import styles from '../pages.module.css';

type S = components['schemas'];
type Target = { kind: 'course' | 'semester'; id: string; name: string; revision: number };

/**
 * UI-COURSE-11: deletion shows its server-computed impact first, can be cancelled at every
 * step, and is separate from archiving. "Move children" lets the user keep classes/courses.
 */
export function DeleteDialog({ open, onOpenChange, target, destinations, onDeleted }: {
  open: boolean; onOpenChange: (open: boolean) => void; target: Target;
  destinations: ReadonlyArray<{ id: string; name: string }>; onDeleted: () => void;
}) {
  const { api, online } = useAppData();
  const invalidate = useInvalidateAccount();
  const [preview, setPreview] = useState<S['DeletionPreview'] | null>(null);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [mode, setMode] = useState<'cascade' | 'move_children'>('cascade');
  const [destination, setDestination] = useState('');
  const childName = target.kind === 'course' ? '课堂' : '课程';
  const load = () => {
    setPreview(null); setPreviewError(null);
    (target.kind === 'course' ? api.courseDeletionPreview(target.id) : api.semesterDeletionPreview(target.id)).then(setPreview, setPreviewError);
  };
  // Each opening fetches a fresh preview; a stale confirmation token is never reused.
  useEffect(() => { if (open) load(); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const remove = useMutation({
    mutationFn: () => {
      const body: S['DeleteScopeRequest'] = { base_revision: preview!.revision, mode, confirmation_token: preview!.confirmation_token, ...(mode === 'move_children' ? { destination_id: destination } : {}) };
      return target.kind === 'course' ? api.deleteCourse(target.id, body) : api.deleteSemester(target.id, body);
    },
    onSuccess: async () => { onOpenChange(false); onDeleted(); await invalidate(); },
  });
  const moveInvalid = mode === 'move_children' && !destination;
  return <Dialog open={open} onOpenChange={value => { if (!value) remove.reset(); onOpenChange(value); }} title={`删除${target.kind === 'course' ? '课程' : '学期'}「${target.name}」`} description="删除前请核对影响范围。归档可以保留全部内容；删除会先撤销访问，再由服务端清理。">
    <div className={styles.stack} data-testid="delete-dialog">
      {previewError ? <p role="alert" className={styles.error}>{describeError(previewError)} <button type="button" className={styles.buttonGhost} onClick={load}>重新获取</button></p> :
        !preview ? <p role="status" className={styles.muted}>正在计算影响范围…</p> :
        <dl className={styles.dl} data-testid="delete-impact">
          <dt>关联课堂</dt><dd>{preview.class_session_count} 节</dd>
          <dt>关联媒体</dt><dd>{(preview.media_bytes / 1_000_000).toFixed(1)} MB（原音、图片等）</dd>
          <dt>确认有效至</dt><dd>{new Date(preview.expires_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}</dd>
        </dl>}
      <fieldset className={styles.stack} style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className={styles.fieldLegend}>处理方式</legend>
        <label className={styles.choice}><input type="radio" name="delete-mode" checked={mode === 'cascade'} onChange={() => setMode('cascade')} /> <span>连同其中的{childName}一并删除</span></label>
        <label className={styles.choice}><input type="radio" name="delete-mode" checked={mode === 'move_children'} disabled={destinations.length === 0} onChange={() => setMode('move_children')} /> <span>先把{childName}移到其他{target.kind === 'course' ? '课程' : '学期'}，再删除{destinations.length === 0 ? `（没有可接收的${target.kind === 'course' ? '课程' : '学期'}）` : ''}</span></label>
        {mode === 'move_children' && <div className={styles.field}><label htmlFor="delete-destination">接收位置</label>
          <select id="delete-destination" value={destination} onChange={event => setDestination(event.target.value)} aria-invalid={moveInvalid || undefined}>
            <option value="">请选择</option>{destinations.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
          <span className={styles.hint}>移动只改变归属，课堂的采集时间和历史来源不变。</span></div>}
      </fieldset>
      {remove.isError && <p role="alert" className={styles.error}>{describeError(remove.error)}</p>}
      {!online && <p className={styles.error}>离线时不能删除。</p>}
      <div className={styles.row}>
        <button type="button" className={styles.buttonDanger} disabled={!preview || moveInvalid || remove.isPending || !online} onClick={() => remove.mutate()}>{remove.isPending ? '正在删除…' : '确认删除'}</button>
        <button type="button" className={styles.buttonSecondary} onClick={() => onOpenChange(false)}>取消</button>
      </div>
    </div>
  </Dialog>;
}
