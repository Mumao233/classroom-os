import { useEffect, useRef, useState, type ClipboardEvent } from 'react';
import type { components } from '../../generated/openapi';
import { describeError, isConflict, useAppData, useInvalidateAccount } from '../../features/data';
import { useSlides } from '../../features/classroom';
import { formatClock } from '../../domain/classroom';
import styles from './classroom.module.css';

type S = components['schemas'];
const TYPES = ['image/png', 'image/jpeg', 'image/webp'] as const;
const MAX_BYTES = 16_000_000;

async function digests(bytes: Uint8Array<ArrayBuffer>) {
  const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  return { hex: [...hash].map(value => value.toString(16).padStart(2, '0')).join(''), base64: btoa(String.fromCharCode(...hash)) };
}

/**
 * UI-CLASS-08 (partial): add a picture by file or paste, rotate or remove it. Every change creates
 * a new slide version; earlier versions stay valid sources. Sending a picture to a model needs its
 * own confirmation in the plan dialog, never here.
 */
export function SlidesPanel({ classId, positionMs, readOnly }: { classId: string; positionMs: number; readOnly: boolean }) {
  const { api, online } = useAppData();
  const invalidate = useInvalidateAccount();
  const slides = useSlides(classId);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const previews = useRef(new Map<string, string>());
  const [, setPreviewVersion] = useState(0);
  useEffect(() => () => { for (const url of previews.current.values()) URL.revokeObjectURL(url); }, []);

  const upload = async (file: File) => {
    setError(null); setStatus(null);
    if (!TYPES.includes(file.type as typeof TYPES[number])) { setStatus('只支持 PNG、JPEG 或 WebP 图片。'); return; }
    if (file.size < 1 || file.size > MAX_BYTES) { setStatus('图片不能超过 16,000,000 字节。'); return; }
    setBusy(true);
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const digest = await digests(bytes);
      const created = await api.createMediaUpload({ purpose: 'slide', class_session_id: classId, mime_type: file.type as S['MediaUploadCreate']['mime_type'], byte_length: bytes.byteLength, sha256: digest.hex, filename: file.name.slice(0, 200) || 'pasted-image' }, crypto.randomUUID());
      await api.putMediaContent(created, bytes, digest.base64, crypto.randomUUID());
      const asset = await api.completeMediaUpload(created.id, { sha256: digest.hex, byte_length: bytes.byteLength }, crypto.randomUUID());
      const slide = await api.createSlide(classId, { asset_id: asset.id, title: file.name.replace(/\.[a-z0-9]+$/i, '').slice(0, 160) || '课件图片', start_ms: Math.max(0, Math.floor(positionMs)), end_ms: null }, crypto.randomUUID());
      previews.current.set(slide.id, URL.createObjectURL(file));
      setPreviewVersion(value => value + 1);
      setStatus(`已添加「${slide.title}」，关联时间 ${formatClock(slide.start_ms)}。`);
      void invalidate();
    } catch (caught) { setError(caught); } finally { setBusy(false); }
  };
  const change = async (slide: S['Slide'], body: Omit<S['SlidePatch'], 'base_revision'>, done: string) => {
    setError(null); setStatus(null);
    try { await api.updateSlide(slide.id, { base_revision: slide.revision, ...body }, crypto.randomUUID()); setStatus(done); void invalidate(); }
    catch (caught) { setError(caught); if (isConflict(caught)) void invalidate(); }
  };
  const onPaste = (event: ClipboardEvent<HTMLDivElement>) => {
    if (readOnly) return;
    const file = [...event.clipboardData.files].find(item => item.type.startsWith('image/'));
    if (file) { event.preventDefault(); void upload(file); }
  };
  const visible = (slides.data?.items ?? []).filter(item => !item.removed).sort((a, b) => a.start_ms - b.start_ms);
  const removedCount = (slides.data?.items ?? []).length - visible.length;

  return <div className={styles.stack} onPaste={onPaste} data-testid="slides-panel">
    {!readOnly && <div className={styles.field}>
      <label htmlFor={`slide-file-${classId}`}>添加课件图片（也可以在此处粘贴）</label>
      <input id={`slide-file-${classId}`} type="file" accept={TYPES.join(',')} disabled={busy || !online} onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void upload(file); }} />
      <span className={styles.hint}>图片会关联到当前时间 {formatClock(positionMs)}。添加图片不会发送给 AI。{!online ? '离线时不能上传。' : ''}</span>
    </div>}
    {busy && <p role="status" className={styles.muted}>正在上传并校验图片…</p>}
    {status && <p role="status" className={styles.small}>{status}</p>}
    {error !== null && <p role="alert" className={styles.error}>{describeError(error)}</p>}
    {slides.isError ? <p role="alert" className={styles.error}>{describeError(slides.error)}</p> : visible.length === 0 ? <p className={`${styles.small} ${styles.muted}`}>还没有课件图片。</p>
      : <ul className={styles.list} aria-label="课件图片">{visible.map(slide => {
        const preview = previews.current.get(slide.id);
        return <li key={slide.id} className={styles.listItem} data-slide-version={slide.version_id}>
          <span className={styles.slideThumb} aria-hidden="true">{preview ? <img src={preview} alt="" style={{ transform: `rotate(${slide.rotation}deg)` }} /> : '预览不可用'}</span>
          <div><strong>{slide.title}</strong><p className={`${styles.small} ${styles.muted}`}>{formatClock(slide.start_ms)} 起 · 旋转 {slide.rotation}° · 版本 {slide.revision}</p></div>
          {!readOnly && <div className={styles.row}>
            <button type="button" className={styles.buttonGhost} disabled={!online} onClick={() => void change(slide, { rotate: 'left' }, `「${slide.title}」已向左旋转，旧版本仍作为历史来源保留。`)}>向左旋转</button>
            <button type="button" className={styles.buttonGhost} disabled={!online} onClick={() => void change(slide, { rotate: 'right' }, `「${slide.title}」已向右旋转，旧版本仍作为历史来源保留。`)}>向右旋转</button>
            <button type="button" className={styles.buttonGhost} disabled={!online} onClick={() => void change(slide, { removed: true }, `「${slide.title}」已从课堂移除；引用过它的旧来源不受影响。`)}>移除</button>
          </div>}
        </li>;
      })}</ul>}
    {removedCount > 0 && <p className={`${styles.small} ${styles.muted}`}>已移除 {removedCount} 张图片，历史来源仍保留原版本。</p>}
    <p className={`${styles.small} ${styles.muted}`}>开发预览不提供服务端图片内容，只能预览本页刚添加的图片。</p>
  </div>;
}
