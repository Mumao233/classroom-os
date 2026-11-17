import { useEffect, useMemo, useRef, useState } from 'react';
import type { CacheWrite } from '../cache/types';
import type { SyncConflict, SyncOperation, SyncResolution } from './client';
import { OfflineSyncRuntime } from './runtime';

type Props = {
  runtime: OfflineSyncRuntime;
  reference: CacheWrite;
  /** A fresh editor generation greater than every generation already used by this editor. */
  generation: number;
  destinations?: Array<{ id: string; name: string }>;
};

const explanation = (error: unknown) => {
  const code = (error as { code?: string }).code;
  if (code === 'INCOMPATIBLE_CLIENT') return '此冲突的历史记录需要兼容处理。本机草稿仍保留，请从原始操作核对。';
  if (code === 'LOCKED' || code === 'GONE' || code === 'AUTHORITY_UNAVAILABLE') return '访问授权已变化。本机密文仍保留，请重新验证身份和同步水位。';
  if (code === 'INVALID_RESOLUTION' || error instanceof SyntaxError) return '请检查合并内容和目标课堂。原稿仍保留。';
  return '本次处理尚未确认完成。草稿仍保留，联网后可继续核对。';
};

/** Reusable PR-08b conflict interaction for the authenticated editor. All content is rendered as text. */
export function ConflictPanel(props: Props) {
  const { scope, objectId, revision, generation, operationId, objectType, verifiedAt, ancestry } = props.reference;
  const { semesterId, courseId, classSessionId } = ancestry;
  const reference = useMemo<CacheWrite>(() => ({ scope, objectId, revision, generation, operationId, objectType, verifiedAt,
    ancestry: { semesterId, courseId, classSessionId } }),
  [scope, objectId, revision, generation, operationId, objectType, verifiedAt, semesterId, courseId, classSessionId]);
  return <ConflictEditor {...props} reference={reference} key={JSON.stringify([objectId, revision, generation, operationId])} />;
}

function ConflictEditor({ runtime, reference, generation, destinations = [] }: Props) {
  const [conflict, setConflict] = useState<SyncConflict | null>(null);
  const [resolution, setResolution] = useState<SyncResolution['resolution']>('edited_merge');
  const [body, setBody] = useState('');
  const [note, setNote] = useState('');
  const [destination, setDestination] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [activeReference, setActiveReference] = useState(reference);
  const [nextGeneration, setNextGeneration] = useState(generation);
  const viewEpoch = useRef(0);
  useEffect(() => {
    let active = true;
    const epoch = ++viewEpoch.current;
    const invalidate = () => { viewEpoch.current++; };
    const lock = () => { invalidate(); setBusy(false); setConflict(null); setBody(''); setNote(''); setMessage('同步已锁定，请重新验证身份。'); };
    runtime.addEventListener('locked', lock);
    void runtime.inspectConflict(reference).then(value => {
      if (!active || viewEpoch.current !== epoch) return;
      setConflict(value);
      const payload = value.local_operation.payload;
      setBody('body_markdown' in payload ? payload.body_markdown : JSON.stringify(payload, null, 2));
      setNote('personal_note' in payload ? payload.personal_note : '');
    }).catch(error => { if (active && viewEpoch.current === epoch) setMessage(explanation(error)); });
    return () => { active = false; invalidate(); runtime.removeEventListener('locked', lock); };
  }, [runtime, reference]);

  async function submit() {
    if (!conflict) return;
    const epoch = viewEpoch.current;
    setBusy(true); setMessage('');
    const submittedGeneration = nextGeneration;
    setNextGeneration(value => value + 1);
    try {
      const request: SyncResolution = { base_revision: conflict.server_revision, resolution };
      if (resolution === 'edited_merge') {
        const local = conflict.local_operation;
        const payload = local.object_type === 'personal_revision'
          ? { ...local.payload, base_revision: conflict.server_revision, body_markdown: body, personal_note: note }
          : { ...JSON.parse(body), base_revision: conflict.server_revision };
        request.merged_operation = { ...local, op_id: crypto.randomUUID(), base_revision: conflict.server_revision, payload } as SyncOperation;
      }
      if (resolution === 'copy_to_new_note') request.destination_class_session_id = destination;
      await runtime.resolve(activeReference, request, submittedGeneration);
      if (viewEpoch.current !== epoch) return;
      const remains = runtime.snapshot().pending.some(item => item.reference.operationId === activeReference.operationId);
      setMessage(remains ? '操作结果仍待确认，草稿已保留。' : '冲突处理已由服务器确认。');
      if (!remains) { setConflict(null); setBody(''); setNote(''); }
    } catch (error) { if (viewEpoch.current === epoch) setMessage(explanation(error)); }
    finally { if (viewEpoch.current === epoch) setBusy(false); }
  }

  async function recheck() {
    const epoch = viewEpoch.current;
    setBusy(true); setMessage('');
    const submittedGeneration = nextGeneration;
    setNextGeneration(value => value + 1);
    try {
      const next = await runtime.recheckConflict(activeReference, submittedGeneration);
      if (viewEpoch.current !== epoch) return;
      setActiveReference(next);
      const latest = await runtime.inspectConflict(next);
      if (viewEpoch.current !== epoch) return;
      setConflict(latest);
      setMessage('已重新核对云端。合并输入已保留，请比较后确认。');
    } catch (error) { if (viewEpoch.current === epoch) setMessage(explanation(error)); }
    finally { if (viewEpoch.current === epoch) setBusy(false); }
  }

  return <section aria-label="同步冲突处理">
    <h2>比较并处理冲突</h2>
    {conflict && <>
      <h3>本机提交</h3><pre>{JSON.stringify(conflict.local_operation.payload, null, 2)}</pre>
      <h3>云端版本 {conflict.server_revision}</h3><pre>{JSON.stringify(conflict.server_content, null, 2)}</pre>
      <fieldset disabled={busy}><legend>处理方式</legend>
        <label><input type="radio" name="sync-resolution" value="edited_merge" checked={resolution === 'edited_merge'} onChange={() => setResolution('edited_merge')} />手动合并</label>
        <label><input type="radio" name="sync-resolution" value="copy_to_new_note" checked={resolution === 'copy_to_new_note'} onChange={() => setResolution('copy_to_new_note')} />另存为新笔记</label>
        <label><input type="radio" name="sync-resolution" value="use_server" checked={resolution === 'use_server'} onChange={() => setResolution('use_server')} />采用云端版本</label>
      </fieldset>
      {resolution === 'edited_merge' && <>
        <label>{conflict.local_operation.object_type === 'personal_revision' ? '合并正文' : '合并字段（JSON）'}
          <textarea value={body} onChange={event => setBody(event.target.value)} disabled={busy} rows={10} /></label>
        {conflict.local_operation.object_type === 'personal_revision' && <label>个人补充
          <textarea value={note} onChange={event => setNote(event.target.value)} disabled={busy} rows={4} /></label>}
      </>}
      {resolution === 'copy_to_new_note' && <label>目标课堂
        {destinations.length ? <select value={destination} onChange={event => setDestination(event.target.value)} disabled={busy}>
          <option value="">请选择课堂</option>{destinations.map(value => <option key={value.id} value={value.id}>{value.name}</option>)}
        </select> : <input value={destination} onChange={event => setDestination(event.target.value)} disabled={busy} placeholder="课堂 ID" />}
      </label>}
      {resolution === 'use_server' && <p>确认后采用上方已比较的云端版本 {conflict.server_revision}，并清理本次冲突对应的本机提交。后续编辑继续保留。</p>}
      <button type="button" disabled={busy || (resolution === 'copy_to_new_note' && !destination)} onClick={() => void submit()}>{busy ? '正在核对…' : '确认处理'}</button>
      <button type="button" disabled={busy} onClick={() => void recheck()}>重新核对云端版本</button>
    </>}
    {message && <p role="status">{message}</p>}
  </section>;
}
