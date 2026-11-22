import { useEffect, useMemo, useRef, useState, type UIEvent } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { components } from '../../generated/openapi';
import { ApiClientError } from '../../api/client';
import {
  audioState, formatMs, kindLabel, lineDiff, markdownBlocks, newestFirst, readingRevision, resolveSource, revisionRole, roleLabel, sourceStatusText,
  type Block, type Doc, type Revision,
} from '../../domain/review';
import { describeError, isConflict, useAppData, useCourse, useInvalidateAccount, usePreferences } from '../../features/data';
import { useClassSession, useDraft, useMarks, useSlides } from '../../features/classroom';
import { useClassSegments, useReadingMemory, useReviewDocument, useReviewRevisions, useSourceSnapshot } from '../../features/review';
import { DataSourceNote, OfflineBanner } from '../common';
import pages from '../pages.module.css';
import styles from './review.module.css';

type S = components['schemas'];
/** A definite server reply ends an intent: the next attempt gets a new key. Only a lost reply is replayed with the same key. */
const answered = (error: unknown) => error instanceof ApiClientError && error.kind === 'http';

/**
 * UI-REVIEW-04…09: one reading page for every entry (global, course, class). The reader opens the
 * adopted version, else the latest personal revision, else the newest candidate shown as such.
 * AI candidates are never adopted implicitly; every write carries base_revision.
 */
export function ReviewReader() {
  const { documentId = '' } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const app = useAppData();
  const client = useQueryClient();
  const invalidate = useInvalidateAccount();
  const document = useReviewDocument(documentId);
  const revisions = useReviewRevisions(documentId);
  const doc = document.data;
  const session = useClassSession(doc?.class_session_id ?? '');
  const course = useCourse(session.data?.course_id ?? '', Boolean(session.data));
  const memory = useReadingMemory();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showSources, setShowSources] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'success' | 'warning' | 'danger'; text: string } | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [documentId]);

  const list = useMemo(() => [...(revisions.data?.items ?? [])].sort(newestFirst), [revisions.data]);
  const opening = doc ? readingRevision(doc, list) : null;
  const shown = list.find(item => item.id === selectedId) ?? opening?.revision ?? null;
  const back = (location.state as { from?: string } | null)?.from;

  const understood = useMutation({
    mutationFn: (next: boolean) => app.api.updateReviewDocument(documentId, { base_revision: doc!.revision, understood: next }),
    onSuccess: updated => { client.setQueryData(['account', app.accountKey, 'review-document', documentId], updated); void invalidate(); setNotice({ tone: 'success', text: updated.understood ? '已标记为已理解。' : '已改回尚未理解。' }); },
    onError: error => { if (isConflict(error)) void document.refetch(); setNotice({ tone: 'warning', text: isConflict(error) ? '这份资料已在其他设备更新，理解状态未改动。请核对后再切换。' : describeError(error) }); },
  });
  const adoptKey = useRef<{ target: string; base: number; key: string } | null>(null);
  const adopt = useMutation({
    mutationFn: (target: Revision) => {
      // One intent, one key: a retry of the same choice on the same base reuses it.
      if (!adoptKey.current || adoptKey.current.target !== target.id || adoptKey.current.base !== doc!.revision) adoptKey.current = { target: target.id, base: doc!.revision, key: crypto.randomUUID() };
      return app.api.adoptReviewRevision(documentId, { base_revision: doc!.revision, revision_id: target.id }, adoptKey.current.key);
    },
    onSuccess: updated => { client.setQueryData(['account', app.accountKey, 'review-document', documentId], updated); adoptKey.current = null; void invalidate(); setSelectedId(updated.adopted_revision_id); setNotice({ tone: 'success', text: '已采用所选版本。其他版本都保留，可以随时切回。' }); },
    onError: error => {
      if (answered(error)) adoptKey.current = null;
      if (isConflict(error)) { void document.refetch(); void revisions.refetch(); }
      setNotice({ tone: 'warning', text: isConflict(error) ? '这份资料已在其他地方更新（可能已采用了别的版本）。没有改动，请核对最新状态后再决定。' : describeError(error) });
    },
  });

  if (document.isPending) return <main id="main-content" className={styles.page}><p role="status" className={pages.muted}>正在读取复习资料…</p></main>;
  if (document.isError || !doc) return <main id="main-content" className={styles.page}><div className={`${pages.banner} ${pages.bannerDanger}`} role="alert"><div>{describeError(document.error)} <Link to="/review">返回复习</Link></div></div></main>;

  const role = shown ? revisionRole(doc, shown) : null;
  const sourceNote = sourceStatusText[doc.source_status];
  return <main id="main-content" className={styles.page}>
    <div className={pages.heading}>
      <div className={pages.wrap}>
        <p>{course.data?.name ?? '课程'} · {session.data?.title || '课堂'} · {kindLabel[doc.kind]}</p>
        <h1 ref={heading} tabIndex={-1}>{doc.title || '未命名'}</h1>
        <p data-testid="document-state">{doc.understood ? '已理解' : '尚未理解'} · {doc.adopted_revision_id ? '已有采用版本' : doc.latest_personal_revision_id ? '只有个人稿' : '尚未采用任何版本'}</p>
      </div>
      <div className={pages.row}>
        {back ? <button type="button" className={pages.buttonGhost} onClick={() => navigate(-1)}>返回上一页</button> : <Link className={pages.buttonGhost} to="/review">返回复习</Link>}
        <Link className={pages.buttonSecondary} to={`/classrooms/${doc.class_session_id}/review`}>本课复习</Link>
        <button type="button" className={pages.buttonSecondary} aria-pressed={doc.understood} disabled={!app.online || understood.isPending} onClick={() => understood.mutate(!doc.understood)} data-testid="toggle-understood">
          {doc.understood ? '改回尚未理解' : '标记为已理解'}</button>
      </div>
    </div>
    <DataSourceNote />
    <OfflineBanner scope="复习资料仍可阅读已加载的内容；" />
    {sourceNote && <div className={`${pages.banner} ${pages.bannerWarning}`} role="status" data-testid="source-status">{sourceNote}</div>}
    {notice && <div className={`${pages.banner} ${notice.tone === 'danger' ? pages.bannerDanger : notice.tone === 'warning' ? pages.bannerWarning : ''}`} role={notice.tone === 'success' ? 'status' : 'alert'} data-testid="review-notice">{notice.text}</div>}

    <section className={pages.panel} aria-labelledby="versions-heading">
      <div className={pages.sectionHead}><h2 id="versions-heading">版本</h2>
        <div className={pages.row}><button type="button" className={pages.buttonSecondary} aria-pressed={comparing} onClick={() => setComparing(value => !value)} disabled={list.length < 2}>比较版本</button></div></div>
      <p className={`${pages.small} ${pages.muted}`}>AI 生成的内容都是候选，只有你选择“采用”后才会成为采用版本；保存个人稿不会覆盖任何版本。</p>
      {revisions.isError ? <p role="alert" className={pages.error}>{describeError(revisions.error)}</p> : revisions.isPending ? <p role="status" className={pages.muted}>正在读取版本…</p> :
        <ul className={styles.versions} data-testid="version-list">{list.map(item => {
          const itemRole = revisionRole(doc, item);
          return <li key={item.id} className={styles.version} aria-current={shown?.id === item.id} data-revision-id={item.id} data-role={itemRole}>
            <span><strong>{roleLabel[itemRole]}</strong> · {item.created_at.slice(0, 16).replace('T', ' ')}{item.source_snapshot_id ? ' · 有冻结来源' : ''}</span>
            <button type="button" className={pages.buttonGhost} onClick={() => setSelectedId(item.id)} aria-label={`查看${roleLabel[itemRole]}（${item.created_at.slice(0, 16).replace('T', ' ')}）`}>查看</button>
            {itemRole !== 'adopted' && <button type="button" className={pages.buttonSecondary} disabled={!app.online || adopt.isPending} onClick={() => adopt.mutate(item)} data-testid={`adopt-${item.id}`}>采用此版本</button>}
          </li>;
        })}</ul>}
      {comparing && <Compare revisions={list} doc={doc} />}
    </section>

    {shown ? <ReadingArea key={shown.id} doc={doc} revision={shown} role={role!} showSources={showSources} setShowSources={setShowSources} memory={memory} session={session.data} />
      : <div className={pages.empty}><h3>还没有可阅读的版本</h3><p>生成的候选或保存的个人稿会出现在这里。</p></div>}

    <PersonalEditor doc={doc} basis={shown} onSaved={revision => { setSelectedId(revision.id); setNotice({ tone: 'success', text: '个人稿已保存为新版本；采用版本和 AI 候选保持不变。' }); }}
      onConflict={() => { void document.refetch(); void revisions.refetch(); setNotice({ tone: 'warning', text: '这份资料已在其他地方更新，你的个人稿仍保留在编辑框里。请看过最新版本后再保存。' }); }} />
  </main>;
}

function ReadingArea({ doc, revision, role, showSources, setShowSources, memory, session }: {
  doc: Doc; revision: Revision; role: ReturnType<typeof revisionRole>; showSources: boolean; setShowSources: (value: boolean) => void;
  memory: ReturnType<typeof useReadingMemory>; session: S['ClassSession'] | undefined;
}) {
  const app = useAppData();
  const preferences = usePreferences();
  const client = useQueryClient();
  const font = preferences.data?.reading_font_px ?? 17;
  const reader = useRef<HTMLDivElement>(null);
  const timer = useRef<number | null>(null);
  useEffect(() => {
    const offset = memory.read().positions[doc.id];
    if (reader.current && offset) reader.current.scrollTop = offset;
    memory.remember(doc.id, offset ?? 0);
    // Restore once per opened revision; the memory object is recreated on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc.id, revision.id]);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  const onScroll = (event: UIEvent<HTMLDivElement>) => {
    const top = event.currentTarget.scrollTop;
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => memory.remember(doc.id, top), 250);
  };
  const setFont = useMutation({
    mutationFn: (next: number) => app.api.updatePreferences({ base_revision: preferences.data!.revision, reading_font_px: next }),
    onSuccess: updated => client.setQueryData(['account', app.accountKey, 'preferences'], updated),
    onError: () => void preferences.refetch(),
  });
  const structured = revision.structured_content;
  const sections = structured?.notes ?? [];
  const cards = structured?.cards ?? [];
  return <div className={styles.readerLayout} data-sources={showSources ? 'open' : 'closed'}>
    <section aria-labelledby="reading-heading" className={pages.stack} style={{ minWidth: 0 }}>
      <div className={pages.spread}>
        <h2 id="reading-heading" style={{ margin: 0, fontSize: 17 }}>{roleLabel[role]}</h2>
        <div className={pages.row}>
          <button type="button" className={pages.buttonGhost} aria-label="缩小字号" disabled={!preferences.data || font <= 14 || setFont.isPending || !app.online} onClick={() => setFont.mutate(font - 1)}>A−</button>
          <span className={pages.small} data-testid="reading-font">{font}px</span>
          <button type="button" className={pages.buttonGhost} aria-label="放大字号" disabled={!preferences.data || font >= 32 || setFont.isPending || !app.online} onClick={() => setFont.mutate(font + 1)}>A+</button>
          <button type="button" className={pages.buttonSecondary} aria-expanded={showSources} aria-controls="source-panel" onClick={() => setShowSources(!showSources)} data-testid="toggle-sources">{showSources ? '收起来源' : '查看来源'}</button>
        </div>
      </div>
      {role === 'candidate' && <p className={`${pages.small} ${pages.warning}`} data-testid="candidate-warning">这是尚未采用的 AI 候选。阅读不会改变采用状态。</p>}
      {sections.length > 1 && <nav aria-label="目录"><ul className={styles.toc}>{sections.map((section, index) => <li key={index}><a href={`#section-${index}`} onClick={event => { event.preventDefault(); reader.current?.querySelector(`#section-${index}`)?.scrollIntoView({ block: 'start' }); }}>{section.title}</a></li>)}</ul></nav>}
      <div ref={reader} className={styles.reader} style={{ fontSize: font }} tabIndex={0} role="region" aria-label="正文" onScroll={onScroll} data-testid="reader">
        {sections.length > 0 ? sections.map((section, index) => <section key={index} id={`section-${index}`}>
          <h2>{section.title}</h2><p>{section.body}</p>{section.supplement && <p className={pages.muted}>{section.supplement}</p>}
          <SourceLabels ids={section.source_ids} missing={section.missing} />
        </section>) : <Markdown blocks={markdownBlocks(revision.body_markdown)} />}
        {revision.personal_note && <p className={pages.muted}>备注：{revision.personal_note}</p>}
      </div>
      {cards.length > 0 && <CardDeck doc={doc} revision={revision} cards={cards} />}
    </section>
    {showSources && <SourcePanel revision={revision} session={session} classId={doc.class_session_id} />}
  </div>;
}

function Markdown({ blocks }: { blocks: Block[] }) {
  if (blocks.length === 0) return <p className={pages.muted}>（空白内容）</p>;
  return <>{blocks.map((block, index) => block.type === 'heading' ? (block.level === 2 ? <h2 key={index}>{block.text}</h2> : <h3 key={index}>{block.text}</h3>)
    : block.type === 'list' ? <ul key={index}>{block.items.map((item, i) => <li key={i}>{item}</li>)}</ul> : <p key={index}>{block.text}</p>)}</>;
}

/** Labels are shown verbatim: 0.3.0 does not map S/P labels to snapshot refs (gap §3.1). */
function SourceLabels({ ids, missing }: { ids: readonly string[]; missing: readonly string[] }) {
  if (!ids.length && !missing.length) return null;
  return <p className={pages.small}>
    {ids.length > 0 && <>来源标签：{ids.map(id => <span key={id} className={styles.sourceTag}>{id}</span>)}</>}
    {missing.length > 0 && <span className={pages.warning}> 缺失：{missing.join('；')}</span>}
  </p>;
}

/** UI-REVIEW-05: one card at a time; reveal, previous/next, sources, "没懂" mark; never padded. */
function CardDeck({ doc, revision, cards }: { doc: Doc; revision: Revision; cards: S['ReviewCard'][] }) {
  const app = useAppData();
  const invalidate = useInvalidateAccount();
  const [index, setIndex] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const marks = useMarks(doc.class_session_id);
  const card = cards[Math.min(index, cards.length - 1)];
  const typeLabel: Record<S['ReviewCard']['type'], string> = { concept: '概念', derivation: '推导', example: '例题', pitfall: '易错点' };
  const mark = useMutation({
    mutationFn: async () => {
      const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(revision.body_markdown)));
      const content = [...digest].map(byte => byte.toString(16).padStart(2, '0')).join('');
      return app.api.createMark(doc.class_session_id, { kind: 'unclear', note: `复习卡片：${card.title}`, anchor: { kind: 'review_revision', document_id: doc.id, revision_id: revision.id, content_sha256: content } });
    },
    onSuccess: () => { void invalidate(); void marks.refetch(); },
  });
  const go = (next: number) => { setIndex(next); setRevealed(false); };
  return <section className={pages.panel} aria-labelledby="cards-heading" data-testid="card-deck">
    <div className={pages.sectionHead}><h2 id="cards-heading">复习卡片</h2><span className={pages.small} aria-live="polite">第 {index + 1} / {cards.length} 张</span></div>
    <article className={styles.card} data-testid="review-card">
      <p className={pages.small}><span className={styles.sourceTag}>{typeLabel[card.type]}</span>{card.title}</p>
      <p className={styles.cardFront}>{card.front}</p>
      {revealed ? <div className={styles.cardBack} data-testid="card-back"><p style={{ margin: 0 }}>{card.back}</p>{card.supplement && <p className={pages.muted}>{card.supplement}</p>}
        {card.english && <p className={pages.small}>{card.english}{card.english_meaning ? ` · ${card.english_meaning}` : ''}</p>}</div>
        : <button type="button" className={pages.button} onClick={() => setRevealed(true)}>揭晓答案</button>}
      <SourceLabels ids={card.source_ids} missing={card.missing} />
    </article>
    <div className={pages.row} style={{ marginTop: 10 }}>
      <button type="button" className={pages.buttonSecondary} disabled={index === 0} onClick={() => go(index - 1)}>上一张</button>
      <button type="button" className={pages.buttonSecondary} disabled={index >= cards.length - 1} onClick={() => go(index + 1)}>下一张</button>
      <button type="button" className={pages.buttonGhost} disabled={!app.online || mark.isPending || mark.isSuccess} onClick={() => mark.mutate()}>{mark.isSuccess ? '已标记为没懂' : '标记没懂'}</button>
    </div>
    {mark.isError && <p role="alert" className={pages.error}>{describeError(mark.error)}</p>}
    <p className={`${pages.small} ${pages.muted}`}>卡片数量按生成结果显示，不会为凑数补卡；揭晓答案不会自动标记为已理解。</p>
  </section>;
}

/** UI-REVIEW-07: frozen refs resolved against what is readable now; changed sources are never swapped. */
function SourcePanel({ revision, session, classId }: { revision: Revision; session: S['ClassSession'] | undefined; classId: string }) {
  const app = useAppData();
  const snapshot = useSourceSnapshot(revision.source_snapshot_id);
  const segments = useClassSegments(revision.source_snapshot_id ? classId : null);
  const slides = useSlides(classId, revision.source_snapshot_id !== null);
  const marks = useMarks(classId);
  const audio = session ? audioState(session, app.now()) : 'unknown';
  const audioText = audio === 'expired' ? '原音已过期：只保留时间位置，不能播放或重新识别。' : audio === 'in_period' ? `原音保留至 ${session!.audio_expires_at!.slice(0, 10)}；开发预览不提供播放。` : '原音不可用或未保存：只保留时间位置。';
  return <aside id="source-panel" className={styles.sources} aria-labelledby="sources-heading" data-testid="source-panel">
    <h2 id="sources-heading">来源</h2>
    <p className={pages.small} data-testid="audio-trace">{audioText}</p>
    {!revision.source_snapshot_id ? <p className={pages.muted}>这个版本没有冻结来源（个人稿或导入内容）。</p>
      : snapshot.isError ? <p role="alert" className={pages.error}>{describeError(snapshot.error)}</p>
        : snapshot.isPending || segments.isPending ? <p role="status" className={pages.muted}>正在读取来源…</p>
          : <>
            <p className={pages.small}>冻结于 {snapshot.data.created_at.slice(0, 16).replace('T', ' ')} · 范围 {formatMs(snapshot.data.start_ms)}–{formatMs(snapshot.data.end_ms)}{snapshot.data.scope_complete ? '' : ' · 部分范围'}</p>
            {snapshot.data.missing_reasons.length > 0 && <p className={`${pages.small} ${pages.warning}`}>未纳入：{snapshot.data.missing_reasons.join('；')}</p>}
            <p className={`${pages.small} ${pages.muted}`}>正文里的来源标签（S…/P…）与下面的引用还没有正式对应关系，这里列出整份快照。</p>
            {snapshot.data.source_refs.map((ref, index) => {
              const resolved = resolveSource(ref, { segments: segments.data ?? [], slides: slides.data?.items ?? [], marks: marks.data?.items ?? [] });
              return <div key={`${ref.kind}-${ref.id}-${index}`} className={`${styles.sourceItem} ${resolved.state !== 'current' ? styles.changed : ''}`} data-source-state={resolved.state} data-testid="source-item">
                <p style={{ margin: 0 }}><span className={styles.sourceTag}>{resolved.label}</span>{resolved.startMs !== null && <span className={pages.small}>{formatMs(resolved.startMs)}</span>}</p>
                {resolved.state === 'current' ? <p style={{ margin: '4px 0 0' }}>{resolved.text}</p>
                  : resolved.state === 'changed' ? <p className={pages.warning} style={{ margin: '4px 0 0' }}>来源已更新，生成时使用的原版本暂不可读；不会用新版本代替。</p>
                    : <p className={pages.warning} style={{ margin: '4px 0 0' }}>{ref.kind === 'slide_version' ? '课件图片当前不可用（已移除或离线缺失）。' : '来源当前不可用。'}</p>}
              </div>;
            })}
          </>}
  </aside>;
}

/** UI-REVIEW-09: line comparison of any two revisions. */
function Compare({ revisions, doc }: { revisions: Revision[]; doc: Doc }) {
  const [left, setLeft] = useState(() => revisions.find(item => item.id === doc.adopted_revision_id)?.id ?? revisions[1]?.id ?? revisions[0]?.id ?? '');
  const [right, setRight] = useState(() => revisions.find(item => item.id !== left)?.id ?? '');
  const a = revisions.find(item => item.id === left); const b = revisions.find(item => item.id === right);
  const label = (item: Revision) => `${roleLabel[revisionRole(doc, item)]} · ${item.created_at.slice(0, 16).replace('T', ' ')}`;
  const diff = a && b ? lineDiff(a.body_markdown, b.body_markdown) : [];
  return <div className={pages.stack} style={{ marginTop: 12 }} data-testid="compare">
    <div className={pages.toolbar}>
      <div className={pages.field}><label htmlFor="compare-left">旧版本</label><select id="compare-left" value={left} onChange={event => setLeft(event.target.value)}>{revisions.map(item => <option key={item.id} value={item.id}>{label(item)}</option>)}</select></div>
      <div className={pages.field}><label htmlFor="compare-right">新版本</label><select id="compare-right" value={right} onChange={event => setRight(event.target.value)}>{revisions.map(item => <option key={item.id} value={item.id}>{label(item)}</option>)}</select></div>
    </div>
    {a && b && a.source_snapshot_id !== b.source_snapshot_id && <p className={`${pages.small} ${pages.warning}`}>两个版本基于不同的来源快照生成。</p>}
    <pre className={styles.diff} aria-label="逐行比较结果">{diff.map((line, index) => <span key={index} className={line.type === 'added' ? styles.diffAdded : line.type === 'removed' ? styles.diffRemoved : styles.diffSame}>
      <span className={pages.srOnly}>{line.type === 'added' ? '新增：' : line.type === 'removed' ? '删除：' : ''}</span>{line.type === 'added' ? '+ ' : line.type === 'removed' ? '− ' : '  '}{line.text || ' '}</span>)}</pre>
  </div>;
}

/** Personal revision editor: the draft survives navigation; saving never overwrites other versions. */
function PersonalEditor({ doc, basis, onSaved, onConflict }: { doc: Doc; basis: Revision | null; onSaved: (revision: Revision) => void; onConflict: () => void }) {
  const app = useAppData();
  const invalidate = useInvalidateAccount();
  const [open, setOpen] = useState(false);
  const [body, setBody] = useDraft(doc.class_session_id, `review:${doc.id}:body`);
  const [note, setNote] = useDraft(doc.class_session_id, `review:${doc.id}:note`);
  const [draftId, setDraftId] = useState(() => crypto.randomUUID());
  const keyRef = useRef<{ fingerprint: string; key: string } | null>(null);
  const save = useMutation({
    mutationFn: () => {
      const payload: S['PersonalRevisionWrite'] = { base_revision: doc.revision, base_content_revision_id: basis?.id ?? null, draft_id: draftId, body_markdown: body, personal_note: note };
      const fingerprint = JSON.stringify(payload);
      if (!keyRef.current || keyRef.current.fingerprint !== fingerprint) keyRef.current = { fingerprint, key: crypto.randomUUID() };
      return app.api.writePersonalRevision(doc.id, payload, keyRef.current.key);
    },
    onSuccess: revision => { keyRef.current = null; setBody(''); setNote(''); setDraftId(crypto.randomUUID()); setOpen(false); void invalidate(); onSaved(revision); },
    onError: error => { if (answered(error)) keyRef.current = null; if (isConflict(error)) onConflict(); },
  });
  const start = () => { if (!body && basis) setBody(basis.body_markdown); setOpen(true); };
  return <section className={pages.panel} aria-labelledby="personal-heading">
    <div className={pages.sectionHead}><h2 id="personal-heading">个人稿</h2>
      {!open && <button type="button" className={pages.buttonSecondary} onClick={start} disabled={!app.online} data-testid="edit-personal">{body ? '继续编辑草稿' : '基于当前版本写个人稿'}</button>}</div>
    {open && <form className={pages.form} style={{ maxWidth: 'none' }} onSubmit={event => { event.preventDefault(); save.mutate(); }}>
      <div className={pages.field}><label htmlFor="personal-body">正文</label><textarea id="personal-body" value={body} maxLength={120000} onChange={event => setBody(event.target.value)} /></div>
      <div className={pages.field}><label htmlFor="personal-note">备注（可选）</label><textarea id="personal-note" value={note} maxLength={12000} style={{ minHeight: 60 }} onChange={event => setNote(event.target.value)} /></div>
      <p className={`${pages.small} ${pages.muted}`}>草稿只保存在本页面会话中，离开页面再回来仍在；保存后成为新的个人版本，不覆盖采用版本和 AI 候选。</p>
      {save.isError && !isConflict(save.error) && <p role="alert" className={pages.error}>{save.error instanceof ApiClientError && save.error.kind === 'network' ? '无法连接服务，草稿仍保留，联网后再保存。' : describeError(save.error)}</p>}
      <div className={pages.row}><button type="submit" className={pages.button} disabled={!app.online || save.isPending || !body.trim()}>{save.isPending ? '正在保存…' : '保存为个人稿'}</button>
        <button type="button" className={pages.buttonGhost} onClick={() => setOpen(false)}>收起（保留草稿）</button></div>
    </form>}
  </section>;
}
