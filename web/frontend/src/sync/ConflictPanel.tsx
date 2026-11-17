import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CacheWrite } from '../cache/types';
import type { SyncConflict, SyncOperation, SyncResolution } from './client';
import { OfflineSyncRuntime } from './runtime';
import type { ConflictDraft, ConflictDraftSource } from './runtime';

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
  const [drafts, setDrafts] = useState<ConflictDraft[]>([]);
  const [saveStatus, setSaveStatus] = useState('');
  const input = useRef({ body: '', note: '' });
  const editorId = useRef(crypto.randomUUID());
  const editGeneration = useRef(0);
  const savedReferences = useRef<CacheWrite[]>([]);
  const mounted = useRef(true);
  const saveFailed = useRef(false);
  const viewEpoch = useRef(0);
  useEffect(() => {
    let active = true;
    mounted.current = true;
    const epoch = ++viewEpoch.current;
    const invalidate = () => { viewEpoch.current++; };
    const lock = () => {
      invalidate(); input.current = { body: '', note: '' }; setDrafts([]);
      setBusy(false); setConflict(null); setBody(''); setNote('');
      setSaveStatus(saveFailed.current ? '合并输入尚未保存。请重试已加密的恢复副本。' : '');
      setMessage('同步已锁定，请重新验证身份。合并输入的保存结果会在下方显示。');
    };
    runtime.addEventListener('locked', lock);
    void Promise.all([runtime.inspectConflict(reference).then(value => ({ value, error: null as unknown })).catch(error => ({ value: null, error })), runtime.readConflictDrafts(reference)]).then(([inspection, recovered]) => {
      if (!active || viewEpoch.current !== epoch) return;
      const value = inspection.value;
      if (!value && !recovered.length) throw inspection.error;
      setConflict(value);
      const payload = value?.local_operation.payload;
      const initial = { body: payload ? ('body_markdown' in payload ? payload.body_markdown : JSON.stringify(payload, null, 2)) : '', note: payload && 'personal_note' in payload ? payload.personal_note : '' };
      const selected = recovered.length === 1 ? recovered[0] : null;
      input.current = selected ? { body: selected.body, note: selected.note } : initial;
      setBody(input.current.body); setNote(input.current.note); setDrafts(recovered);
      savedReferences.current = selected ? [selected.reference] : [];
      if (selected) setSaveStatus('合并输入已加密保存在本机。');
      if (!value) setMessage('原冲突已处理。本机尚未提交的合并输入继续保留，可在下方查看和复制。');
      if (recovered.length > 1) setMessage('发现多份本机合并草稿，请选择要恢复的一份。其他草稿继续保留。');
    }).catch(error => { if (active && viewEpoch.current === epoch) setMessage(explanation(error)); });
    return () => { active = false; mounted.current = false; invalidate(); runtime.removeEventListener('locked', lock); };
  }, [runtime, reference]);

  function saveInput(source = activeReference) {
    const epoch = viewEpoch.current;
    const sequence = ++editGeneration.current;
    setSaveStatus('合并输入正在保存…');
    let saving: Promise<CacheWrite>;
    try { saving = runtime.saveConflictDraft(source, input.current.body, input.current.note, editorId.current, sequence); }
    catch { saveFailed.current = true; setSaveStatus('合并输入尚未保存，请保留当前输入并重试。'); return; }
    void saving.then(saved => {
      if (!mounted.current || editGeneration.current !== sequence) return;
      savedReferences.current = [...savedReferences.current.filter(value => value.objectId !== saved.objectId), saved];
      saveFailed.current = false;
      setSaveStatus(viewEpoch.current === epoch ? '合并输入已加密保存在本机。' : '合并输入已加密保存在本机。重新验证后可恢复。');
    }).catch(() => {
      if (mounted.current && editGeneration.current === sequence) { saveFailed.current = true; setSaveStatus('合并输入尚未保存。请重试已加密的恢复副本。'); }
    });
  }

  async function retrySavedInput() {
    if (conflict) { saveInput(); return; }
    setSaveStatus('合并输入正在保存…');
    try { await runtime.retryEditorDraftSaves(); if (mounted.current) { saveFailed.current = false; setSaveStatus('合并输入已加密保存在本机。重新验证后可恢复。'); } }
    catch { if (mounted.current) { saveFailed.current = true; setSaveStatus('合并输入尚未保存。请重试已加密的恢复副本。'); } }
  }

  function edit(field: 'body' | 'note', value: string) {
    input.current = { ...input.current, [field]: value };
    if (field === 'body') setBody(value); else setNote(value);
    saveInput();
  }

  function recover(index: string) {
    const selected = drafts[Number(index)];
    if (!selected) return;
    input.current = { body: selected.body, note: selected.note };
    setBody(selected.body); setNote(selected.note); savedReferences.current = [selected.reference];
    setSaveStatus('合并输入已加密保存在本机。'); setMessage('已恢复所选本机草稿，其他分支继续保留。');
  }

  async function submit() {
    if (!conflict) return;
    const epoch = viewEpoch.current;
    setBusy(true); setMessage('');
    const submittedGeneration = nextGeneration;
    setNextGeneration(value => value + 1);
    try {
      await runtime.awaitEditorDraftSaves();
      if (viewEpoch.current !== epoch) return;
      const request: SyncResolution = { base_revision: conflict.server_revision, resolution };
      if (resolution === 'edited_merge') {
        const local = conflict.local_operation;
        const payload = local.object_type === 'personal_revision'
          ? { ...local.payload, base_revision: conflict.server_revision, body_markdown: body, personal_note: note }
          : { ...JSON.parse(body), base_revision: conflict.server_revision };
        request.merged_operation = { ...local, op_id: crypto.randomUUID(), base_revision: conflict.server_revision, payload } as SyncOperation;
      }
      if (resolution === 'copy_to_new_note') request.destination_class_session_id = destination;
      await runtime.resolve(activeReference, request, submittedGeneration, savedReferences.current);
      if (viewEpoch.current !== epoch) return;
      const remains = runtime.snapshot().pending.some(item => item.reference.operationId === activeReference.operationId);
      setMessage(remains ? '操作结果仍待确认，草稿已保留。' : '冲突处理已由服务器确认。');
      if (!remains) {
        const remaining = await runtime.readConflictDrafts(activeReference);
        if (viewEpoch.current !== epoch) return;
        setConflict(null); setDrafts(remaining);
        if (remaining.length === 1) { input.current = { body: remaining[0].body, note: remaining[0].note }; setBody(remaining[0].body); setNote(remaining[0].note); }
        else { input.current = { body: '', note: '' }; setBody(''); setNote(''); }
        if (remaining.length) setMessage('冲突处理已由服务器确认。尚未提交的合并输入保留在本机，可在下方查看和复制。');
        else setSaveStatus('');
      }
    } catch (error) { if (viewEpoch.current === epoch) setMessage(explanation(error)); }
    finally { if (viewEpoch.current === epoch) setBusy(false); }
  }

  async function recheck() {
    const epoch = viewEpoch.current;
    setBusy(true); setMessage('');
    const submittedGeneration = nextGeneration;
    setNextGeneration(value => value + 1);
    try {
      await runtime.awaitEditorDraftSaves();
      if (viewEpoch.current !== epoch) return;
      const next = await runtime.recheckConflict(activeReference, submittedGeneration);
      if (viewEpoch.current !== epoch) return;
      setActiveReference(next);
      const latest = await runtime.inspectConflict(next);
      if (viewEpoch.current !== epoch) return;
      setConflict(latest);
      if (savedReferences.current.length) { editorId.current = crypto.randomUUID(); editGeneration.current = 0; saveInput(next); }
      setMessage('已重新核对云端。合并输入已保留，请比较后确认。');
    } catch (error) { if (viewEpoch.current === epoch) setMessage(explanation(error)); }
    finally { if (viewEpoch.current === epoch) setBusy(false); }
  }

  return <section aria-label="同步冲突处理">
    <h2>比较并处理冲突</h2>
    {conflict && <>
      <h3>本机提交</h3><pre>{JSON.stringify(conflict.local_operation.payload, null, 2)}</pre>
      <h3>云端版本 {conflict.server_revision}</h3><pre>{JSON.stringify(conflict.server_content, null, 2)}</pre>
      {drafts.length > 1 && <label>本机合并草稿<select defaultValue="" onChange={event => recover(event.target.value)} disabled={busy}>
        <option value="" disabled>请选择恢复分支</option>{drafts.map((draft, index) => <option key={draft.reference.objectId} value={index}>本机草稿 {index + 1}（编辑序号 {draft.reference.generation}）</option>)}
      </select></label>}
      <fieldset disabled={busy}><legend>处理方式</legend>
        <label><input type="radio" name="sync-resolution" value="edited_merge" checked={resolution === 'edited_merge'} onChange={() => setResolution('edited_merge')} />手动合并</label>
        <label><input type="radio" name="sync-resolution" value="copy_to_new_note" checked={resolution === 'copy_to_new_note'} onChange={() => setResolution('copy_to_new_note')} />另存为新笔记</label>
        <label><input type="radio" name="sync-resolution" value="use_server" checked={resolution === 'use_server'} onChange={() => setResolution('use_server')} />采用云端版本</label>
      </fieldset>
      {resolution === 'edited_merge' && <>
        <label>{conflict.local_operation.object_type === 'personal_revision' ? '合并正文' : '合并字段（JSON）'}
          <textarea value={body} onChange={event => edit('body', event.target.value)} disabled={busy} rows={10} maxLength={120000} /></label>
        {conflict.local_operation.object_type === 'personal_revision' && <label>个人补充
          <textarea value={note} onChange={event => edit('note', event.target.value)} disabled={busy} rows={4} maxLength={12000} /></label>}
      </>}
      {resolution === 'copy_to_new_note' && <label>目标课堂
        {destinations.length ? <select value={destination} onChange={event => setDestination(event.target.value)} disabled={busy}>
          <option value="">请选择课堂</option>{destinations.map(value => <option key={value.id} value={value.id}>{value.name}</option>)}
        </select> : <input value={destination} onChange={event => setDestination(event.target.value)} disabled={busy} placeholder="课堂 ID" />}
      </label>}
      {resolution === 'use_server' && <p>确认后采用上方已比较的云端版本 {conflict.server_revision}，并清理本次冲突对应的本机提交。后续编辑继续保留。</p>}
      <button type="button" disabled={busy || saveStatus.startsWith('合并输入尚未保存') || (resolution === 'copy_to_new_note' && !destination)} onClick={() => void submit()}>{busy ? '正在核对…' : '确认处理'}</button>
      <button type="button" disabled={busy || saveStatus.startsWith('合并输入尚未保存')} onClick={() => void recheck()}>重新核对云端版本</button>
    </>}
    {!conflict && drafts.length > 0 && <>
      <h3>保留的本机合并输入</h3>
      {drafts.length > 1 && <label>本机合并草稿<select defaultValue="" onChange={event => recover(event.target.value)}>
        <option value="" disabled>请选择恢复分支</option>{drafts.map((draft, index) => <option key={draft.reference.objectId} value={index}>本机草稿 {index + 1}（编辑序号 {draft.reference.generation}）</option>)}
      </select></label>}
      <label>保留的合并正文<textarea value={body} readOnly rows={10} /></label>
      <label>保留的个人补充<textarea value={note} readOnly rows={4} /></label>
    </>}
    {saveStatus.startsWith('合并输入尚未保存') && <button type="button" onClick={() => void retrySavedInput()}>重试保存合并输入</button>}
    {saveStatus && <p role="status">{saveStatus}</p>}
    {message && <p role="status">{message}</p>}
  </section>;
}

/** A fresh authenticated page can discover retained inputs even after the original outbox ACK. */
export function ConflictRecoveryList({ runtime }: { runtime: OfflineSyncRuntime }) {
  const [sources, setSources] = useState<ConflictDraftSource[]>([]);
  const [selected, setSelected] = useState<CacheWrite | null>(null);
  const [message, setMessage] = useState('');
  const epoch = useRef(0);
  const refresh = useCallback(async () => {
    const current = epoch.current;
    try { const result = await runtime.listConflictDraftSources(); if (epoch.current === current) { setSources(result); setMessage(result.length ? '' : '没有保留的本机合并输入。'); } }
    catch (error) { if (epoch.current === current) setMessage(explanation(error)); }
  }, [runtime]);
  useEffect(() => {
    const invalidate = () => { epoch.current++; };
    const lock = () => { invalidate(); setSources([]); setSelected(null); setMessage('同步已锁定，请重新验证身份。'); };
    runtime.addEventListener('locked', lock);
    void refresh();
    return () => { invalidate(); runtime.removeEventListener('locked', lock); };
  }, [runtime, refresh]);
  return <section aria-label="本机合并输入恢复">
    <h2>保留的本机合并输入</h2><button type="button" onClick={() => void refresh()}>刷新本机合并草稿</button>
    {sources.map((item, index) => <p key={item.source?.operationId ?? item.reference?.operationId}>{item.state === 'restricted' ? `本机草稿 ${index + 1}（访问受限，密文仍保留）`
      : <button type="button" onClick={() => setSelected(item.source)}>查看本机合并草稿 {index + 1}</button>}</p>)}
    {message && <p role="status">{message}</p>}
    {selected && <ConflictPanel runtime={runtime} reference={selected} generation={Math.max(selected.generation, ...runtime.snapshot().pending.map(item => item.reference.generation)) + 1} />}
  </section>;
}
