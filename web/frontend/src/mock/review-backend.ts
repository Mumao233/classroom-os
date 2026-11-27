import type { components } from '../generated/openapi';
import { sha256Hex } from './sha256';

/**
 * PR-10b review routes for the development-only Mock backend: review documents, their immutable
 * revisions, personal revisions, explicit adoption, question understanding and frozen source
 * snapshots. Same rules as backend.ts: only fields of the frozen contract 0.3.0, synthetic content,
 * CAS on every write, no network, excluded from production builds.
 */
type S = components['schemas'];
type ErrorCode = S['Error']['error']['code'];
export type ReviewReply = { status: number; body: unknown };

export type ReviewStore = {
  revisions: S['ReviewRevision'][];
  snapshots: S['SourceSnapshot'][];
  /** Test helper: documents whose revision reads are refused (a revoked source) while metadata stays readable. */
  deniedRevisionDocs?: Set<string>;
};

/** What the review routes need from the owning account store. */
export type ReviewHost = {
  documents: () => S['ReviewDocument'][];
  saveDocument: (next: S['ReviewDocument']) => void;
  addDocument: (next: S['ReviewDocument']) => void;
  /** Visible (live, authorised) classes with their course and semester. */
  visibleSessions: () => Array<{ session: S['ClassSession']; courseId: string; semesterId: string }>;
  questions: () => S['Question'][];
  saveQuestion: (next: S['Question']) => void;
  /** Whether a frozen source reference is still readable in its frozen revision (projection of source_status). */
  sourceState?: (ref: S['SourceRef']) => 'current' | 'changed' | 'missing';
};
export type ReviewHelpers = {
  now: () => string;
  newId: () => string;
  error: (status: number, code: ErrorCode, message: string, currentRevision?: number | null) => ReviewReply;
  idempotent: (key: string | null, scope: string, body: unknown, run: () => ReviewReply) => ReviewReply;
};

const uuid = (prefix: string, n: number) => `${prefix}-0000-4000-8000-${n.toString(16).padStart(12, '0')}`;
const isUuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
const only = (body: Record<string, unknown>, keys: string[]) => Object.keys(body).every(key => keys.includes(key));
const positive = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 1;

/** Synthetic revisions and snapshots for the seeded account A (documents e0000000-1…4 in backend.ts). */
export function seedReview(seeded: boolean): ReviewStore {
  const store: ReviewStore = { revisions: [], snapshots: [] };
  if (!seeded) return store;
  const ended = uuid('c0000000', 1);
  const segRef = (n: number, text: string, revision = uuid('73100000', n)): S['SourceRef'] => ({ kind: 'transcript_segment', id: uuid('73000000', n), revision_id: revision, content_sha256: sha256Hex(text) });
  const lines = ['今天讨论特征值与特征向量的定义（合成）。', '若 Av 等于 λv 且 v 非零，则 λ 称为特征值（合成）。', '求特征值时先写出特征多项式（合成）。'];
  const slideRef: S['SourceRef'] = { kind: 'slide_version', id: uuid('76000000', 1), revision_id: uuid('76100000', 1), content_sha256: sha256Hex('mock-slide:0') };
  const markRef: S['SourceRef'] = { kind: 'mark', id: uuid('75000000', 1), revision_id: uuid('75100000', 1), content_sha256: sha256Hex('mark') };
  store.snapshots.push(
    { id: uuid('e2000000', 1), class_session_id: ended, snapshot_sha256: sha256Hex('snapshot-1'), source_refs: [segRef(1, lines[0]), segRef(2, lines[1]), segRef(3, lines[2]), slideRef, markRef], start_ms: 0, end_ms: 58_000, scope_complete: true, missing_reasons: [], created_at: '2026-10-27T07:40:00Z' },
    // Frozen before segment 2 was corrected: its revision no longer matches the current one.
    { id: uuid('e2000000', 2), class_session_id: ended, snapshot_sha256: sha256Hex('snapshot-2'), source_refs: [segRef(1, lines[0]), segRef(2, '若 Av 等于 λv，则 λ 称为特征值（合成，旧版）。', uuid('73100000', 0x99)), segRef(3, lines[2])], start_ms: 0, end_ms: 58_000, scope_complete: true, missing_reasons: [], created_at: '2026-10-28T07:00:00Z' },
    { id: uuid('e2000000', 4), class_session_id: uuid('c0000000', 4), snapshot_sha256: sha256Hex('snapshot-4'), source_refs: [{ kind: 'transcript_segment', id: uuid('73000000', 10), revision_id: uuid('73100000', 10), content_sha256: sha256Hex('红黑树插入后需要重新着色（合成）。') }], start_ms: 0, end_ms: 15_000, scope_complete: false, missing_reasons: ['原音有 59 个分片尚未持久确认'], created_at: '2026-10-25T05:00:00Z' },
  );
  const rev = (n: number, document: number, kind: S['ReviewRevision']['kind'], created: string, over: Partial<S['ReviewRevision']>): S['ReviewRevision'] => ({
    id: uuid('e1000000', n), revision: 1, created_at: created, updated_at: created, document_id: uuid('e0000000', document), kind, parent_revision_id: null,
    source_snapshot_id: null, raw_output_ref: null, body_markdown: '', structured_content: null, personal_note: '', ...over });
  store.revisions.push(
    rev(1, 1, 'ai_candidate', '2026-10-27T08:00:00Z', { source_snapshot_id: uuid('e2000000', 1), raw_output_ref: uuid('e3000000', 1),
      body_markdown: '## 课堂结构\n本节从定义出发，介绍特征值与特征向量（合成）。\n\n## 重点\n- 定义：Av = λv，且 v ≠ 0\n- 求法：解特征多项式 det(A − λI) = 0\n\n## 疑问\n特征向量为什么不唯一？',
      structured_content: { schema_version: 1, cards: [], notes: [
        { section: 'structure', title: '课堂结构', body: '本节从定义出发，介绍特征值与特征向量（合成）。', supplement: '', source_ids: ['S0001'], missing: [] },
        { section: 'focus', title: '重点', body: '定义：Av = λv，且 v ≠ 0。求法：解特征多项式 det(A − λI) = 0（合成）。', supplement: '', source_ids: ['S0002', 'S0003'], missing: [] },
        { section: 'questions', title: '疑问', body: '特征向量为什么不唯一？（合成）', supplement: '', source_ids: ['S0002'], missing: [] },
        { section: 'slides', title: '板书', body: '板书列出了特征多项式的展开（合成）。', supplement: '', source_ids: ['P0001'], missing: [] },
      ] } }),
    rev(5, 1, 'ai_candidate', '2026-10-28T07:05:00Z', { source_snapshot_id: uuid('e2000000', 2), raw_output_ref: uuid('e3000000', 5),
      body_markdown: '## 课堂结构\n本节从定义出发，介绍特征值与特征向量，并补充了几何意义（合成）。\n\n## 重点\n- 定义：Av = λv，且 v ≠ 0\n- 求法：解特征多项式 det(A − λI) = 0\n- 几何意义：沿特征向量方向只做伸缩\n\n## 疑问\n特征向量为什么不唯一？',
      structured_content: { schema_version: 1, cards: [], notes: [
        { section: 'structure', title: '课堂结构', body: '本节从定义出发，介绍特征值与特征向量，并补充了几何意义（合成）。', supplement: '', source_ids: ['S0001'], missing: [] },
        { section: 'focus', title: '重点', body: '定义、求法与几何意义（合成）。', supplement: '', source_ids: ['S0002', 'S0003'], missing: [] },
      ] } }),
    rev(2, 2, 'ai_candidate', '2026-10-27T08:10:00Z', { source_snapshot_id: uuid('e2000000', 1), raw_output_ref: uuid('e3000000', 2), body_markdown: '特征多项式复习卡片（合成）。',
      structured_content: { schema_version: 1, notes: [{ section: 'focus', title: '卡片说明', body: '三张卡片，按课堂内容生成（合成）。', supplement: '', source_ids: [], missing: [] }], cards: [
        { type: 'concept', title: '特征值', front: '什么是矩阵 A 的特征值？', back: '存在非零向量 v 使 Av = λv 的数 λ（合成）。', supplement: '', english: 'eigenvalue', english_meaning: '特征值', source_ids: ['S0002'], missing: [] },
        { type: 'derivation', title: '特征多项式', front: '如何求特征值？', back: '解 det(A − λI) = 0（合成）。', supplement: '对 n 阶矩阵是 n 次多项式。', english: 'characteristic polynomial', english_meaning: '特征多项式', source_ids: ['S0003'], missing: [] },
        { type: 'pitfall', title: '零向量', front: '零向量能作为特征向量吗？', back: '不能，定义要求 v ≠ 0（合成）。', supplement: '', english: '', english_meaning: '', source_ids: ['S0002'], missing: ['课堂未讨论复数特征值的情形'] },
      ] } }),
    rev(3, 3, 'personal', '2026-10-23T12:00:00Z', { body_markdown: '## 我的补充\n对角化的前提：有 n 个线性无关的特征向量（合成个人笔记）。', personal_note: '下次课前再看一遍。' }),
    rev(4, 4, 'ai_candidate', '2026-10-25T05:00:00Z', { source_snapshot_id: uuid('e2000000', 4), raw_output_ref: uuid('e3000000', 4),
      body_markdown: '## 课堂结构\n红黑树插入后通过重新着色和旋转恢复性质（合成，部分范围）。',
      structured_content: { schema_version: 1, cards: [], notes: [{ section: 'structure', title: '课堂结构', body: '红黑树插入后通过重新着色和旋转恢复性质（合成，部分范围）。', supplement: '', source_ids: ['S0001'], missing: ['后半节原音尚未上传，未纳入'] }] } }),
  );
  return store;
}

/** A summary task's result becomes a candidate revision with its frozen snapshot; never adopted. */
export function addCandidate(store: ReviewStore, input: { revisionId: string; documentId: string; snapshot: S['SourceSnapshot'] | null; created: string; rawOutputRef: string }) {
  if (input.snapshot && !store.snapshots.some(item => item.id === input.snapshot!.id)) store.snapshots.push(input.snapshot);
  store.revisions.push({ id: input.revisionId, revision: 1, created_at: input.created, updated_at: input.created, document_id: input.documentId, kind: 'ai_candidate', parent_revision_id: null,
    source_snapshot_id: input.snapshot?.id ?? null, raw_output_ref: input.rawOutputRef,
    body_markdown: input.snapshot && !input.snapshot.scope_complete ? '## 课堂结构\n合成候选总结（部分范围）：只覆盖已经持久确认的内容。' : '## 课堂结构\n合成候选总结：这是开发预览中的示例，没有调用任何模型。',
    structured_content: { schema_version: 1, cards: [], notes: [{ section: 'structure', title: '课堂结构', body: '合成候选总结：这是开发预览中的示例，没有调用任何模型。', supplement: '', source_ids: [], missing: input.snapshot?.missing_reasons ?? [] }] }, personal_note: '' });
}

export function handleReview(store: ReviewStore, host: ReviewHost, h: ReviewHelpers, method: string, route: string, id: string, query: URLSearchParams, body: Record<string, unknown>, key: string | null): ReviewReply | null {
  void method;
  const ok = (value: unknown, status = 200): ReviewReply => ({ status, body: value });
  // Cursor pages at the contract's page size (1-100); the cursor is an opaque offset token.
  const page = <T>(items: T[]): ReviewReply => {
    const limit = query.has('limit') ? Number(query.get('limit')) : 50;
    const cursor = query.get('cursor');
    if (!Number.isInteger(limit) || limit < 1 || limit > 100 || (cursor !== null && !/^r[0-9]{1,9}$/.test(cursor))) return h.error(400, 'VALIDATION_FAILED', '分页参数无效');
    const start = cursor ? Number(cursor.slice(1)) : 0;
    const end = start + limit;
    return ok({ items: items.slice(start, end), next_cursor: end < items.length ? `r${end}` : null });
  };
  const visible = () => new Map(host.visibleSessions().map(item => [item.session.id, item]));
  const findDocument = (documentId: string) => { const doc = host.documents().find(item => item.id === documentId); return doc && visible().has(doc.class_session_id) ? doc : undefined; };
  const notFound = () => h.error(404, 'NOT_FOUND', '复习资料不存在');
  const conflict = (current: { revision: number }) => h.error(409, 'REVISION_CONFLICT', '内容已在其他地方更新', current.revision);
  const save = (doc: S['ReviewDocument'], change: Partial<S['ReviewDocument']>) => { const next = { ...doc, ...change, revision: doc.revision + 1, updated_at: h.now() }; host.saveDocument(next); return next; };
  /** source_status is projected on every read from the active revision's frozen snapshot (the snapshot itself never changes). */
  const project = (doc: S['ReviewDocument']): S['ReviewDocument'] => {
    const activeId = doc.adopted_revision_id ?? doc.latest_personal_revision_id ?? [...doc.candidate_revision_ids].pop() ?? null;
    const snapshotId = store.revisions.find(item => item.id === activeId)?.source_snapshot_id;
    const snapshot = snapshotId ? store.snapshots.find(item => item.id === snapshotId) : undefined;
    if (!snapshot || !host.sourceState) return doc;
    const states = snapshot.source_refs.map(ref => host.sourceState!(ref));
    const status: S['ReviewDocument']['source_status'] = states.includes('missing') ? 'unavailable' : states.includes('changed') ? 'changed'
      : doc.source_status === 'new_material_not_included' ? 'new_material_not_included' : 'unchanged';
    return status === doc.source_status ? doc : { ...doc, source_status: status };
  };
  // Access is rechecked before any idempotent replay: a revoked classroom never returns an earlier receipt.
  const writeTarget = route === 'POST /review-documents' ? (isUuid(body.class_session_id) && visible().has(body.class_session_id) ? 'ok' : 'denied')
    : route.startsWith('POST /review-documents/:id') || route === 'PATCH /review-documents/:id' ? (findDocument(id) ? 'ok' : 'denied')
      : route === 'PATCH /questions/:id' ? (host.questions().some(item => item.id === id && visible().has(item.class_session_id)) ? 'ok' : 'denied') : 'ok';
  if (writeTarget === 'denied') return route === 'PATCH /questions/:id' ? h.error(404, 'NOT_FOUND', '疑问不存在') : route === 'POST /review-documents' ? h.error(404, 'NOT_FOUND', '课堂不存在') : notFound();

  switch (route) {
    case 'GET /review-documents': {
      const sessions = visible();
      const kind = query.get('kind'); const understood = query.get('understood');
      const course = query.get('course_id'); const semester = query.get('semester_id'); const classId = query.get('class_session_id');
      const items = host.documents().filter(item => {
        const owner = sessions.get(item.class_session_id);
        return owner && (!course || owner.courseId === course) && (!semester || owner.semesterId === semester) && (!classId || item.class_session_id === classId)
          && (!kind || item.kind === kind) && (understood === null || String(item.understood) === understood);
      });
      return page([...items].sort((a, b) => b.updated_at.localeCompare(a.updated_at) || b.id.localeCompare(a.id)).map(project));
    }
    case 'POST /review-documents': return h.idempotent(key, 'review-note', body, () => {
      if (!only(body, ['class_session_id', 'title', 'body_markdown', 'draft_id']) || !isUuid(body.class_session_id) || !isUuid(body.draft_id)
        || typeof body.title !== 'string' || body.title.length > 200 || typeof body.body_markdown !== 'string' || body.body_markdown.length > 120000) return h.error(400, 'VALIDATION_FAILED', '笔记内容无效');
      const created = h.now();
      const documentId = h.newId(); const revisionId = h.newId();
      store.revisions.push({ id: revisionId, revision: 1, created_at: created, updated_at: created, document_id: documentId, kind: 'personal', parent_revision_id: null, source_snapshot_id: null, raw_output_ref: null, body_markdown: body.body_markdown, structured_content: null, personal_note: '' });
      const doc: S['ReviewDocument'] = { id: documentId, revision: 1, created_at: created, updated_at: created, class_session_id: body.class_session_id, kind: 'note', title: body.title.trim() || '个人笔记',
        adopted_revision_id: null, latest_personal_revision_id: revisionId, candidate_revision_ids: [], understood: false, source_status: 'unchanged' };
      host.addDocument(doc);
      return ok(doc, 201);
    });
    case 'GET /review-documents/:id': {
      const doc = findDocument(id); if (!doc) return notFound();
      // An exact-revision read is served only for the current revision (history is not kept by the Mock).
      const exact = query.get('revision');
      if (exact !== null && (!/^[1-9][0-9]*$/.test(exact) || Number(exact) !== doc.revision)) return conflict(doc);
      return ok(project(doc));
    }
    case 'PATCH /review-documents/:id': return h.idempotent(key, `review-patch:${id}`, body, () => {
      const doc = findDocument(id)!;
      if (!only(body, ['base_revision', 'title', 'understood']) || !positive(body.base_revision) || ('title' in body && (typeof body.title !== 'string' || body.title.length > 200 || body.title.trim().length === 0))
        || ('understood' in body && typeof body.understood !== 'boolean')) return h.error(400, 'VALIDATION_FAILED', '修改内容无效');
      if (body.base_revision !== doc.revision) return conflict(doc);
      return ok(project(save(doc, { ...('title' in body ? { title: String(body.title).trim() } : {}), ...('understood' in body ? { understood: body.understood as boolean } : {}) })));
    });
    case 'GET /review-documents/:id/revisions': {
      const doc = findDocument(id); if (!doc) return notFound();
      if (store.deniedRevisionDocs?.has(id)) return h.error(403, 'FORBIDDEN', '版本来源已不可访问');
      return page(store.revisions.filter(item => item.document_id === id).sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id)));
    }
    case 'GET /review-revisions/:id': {
      const revision = store.revisions.find(item => item.id === id);
      if (!revision || !findDocument(revision.document_id)) return h.error(404, 'NOT_FOUND', '版本不存在');
      // The same source-access check as the document's revision list: an exact read is no way around it.
      if (store.deniedRevisionDocs?.has(revision.document_id)) return h.error(403, 'FORBIDDEN', '版本来源已不可访问');
      return ok(revision);
    }
    case 'POST /review-documents/:id/personal-revisions': return h.idempotent(key, `personal:${id}`, body, () => {
      const doc = findDocument(id)!;
      if (!only(body, ['base_revision', 'base_content_revision_id', 'draft_id', 'body_markdown', 'personal_note']) || !positive(body.base_revision) || !isUuid(body.draft_id)
        || !(body.base_content_revision_id === null || isUuid(body.base_content_revision_id)) || typeof body.body_markdown !== 'string' || body.body_markdown.length > 120000
        || typeof body.personal_note !== 'string' || body.personal_note.length > 12000) return h.error(400, 'VALIDATION_FAILED', '个人稿内容无效');
      const parent = body.base_content_revision_id === null ? null : store.revisions.find(item => item.id === body.base_content_revision_id && item.document_id === id);
      if (parent === undefined) return h.error(400, 'VALIDATION_FAILED', '基础版本不属于这份资料');
      const created = h.now();
      // A derived personal revision keeps its parent's frozen sources; an independent note has none.
      const revision: S['ReviewRevision'] = { id: h.newId(), revision: 1, created_at: created, updated_at: created, document_id: id, kind: 'personal', parent_revision_id: parent?.id ?? null,
        source_snapshot_id: parent?.source_snapshot_id ?? null, raw_output_ref: null, body_markdown: body.body_markdown, structured_content: null, personal_note: body.personal_note };
      store.revisions.push(revision);
      // On a stale base the submitted branch is kept (listed, never made active); pointers stay unchanged,
      // but the document's CAS clock advances so a later write based on the pre-branch revision is refused too.
      if (body.base_revision !== doc.revision) return conflict(save(doc, {}));
      // A personal revision never replaces the adopted version or any candidate.
      save(doc, { latest_personal_revision_id: revision.id });
      return ok(revision, 201);
    });
    case 'POST /review-documents/:id/adopt': return h.idempotent(key, `adopt:${id}`, body, () => {
      const doc = findDocument(id)!;
      if (!only(body, ['base_revision', 'revision_id']) || !positive(body.base_revision) || !isUuid(body.revision_id)) return h.error(400, 'VALIDATION_FAILED', '采用请求无效');
      const target = store.revisions.find(item => item.id === body.revision_id && item.document_id === id);
      if (!target) return h.error(400, 'VALIDATION_FAILED', '版本不属于这份资料');
      if (body.base_revision !== doc.revision) return conflict(doc);
      // Only AI revisions generated after the adopted one remain pending candidates; older ones stay listed (and adoptable) as history.
      const candidates = store.revisions.filter(item => item.document_id === id && item.kind === 'ai_candidate' && item.id !== target.id && item.created_at > target.created_at).map(item => item.id);
      return ok(project(save(doc, { adopted_revision_id: target.id, candidate_revision_ids: candidates })));
    });
    case 'PATCH /questions/:id': return h.idempotent(key, `question-patch:${id}`, body, () => {
      const question = host.questions().find(item => item.id === id)!;
      if (!only(body, ['base_revision', 'understood']) || !positive(body.base_revision) || typeof body.understood !== 'boolean') return h.error(400, 'VALIDATION_FAILED', '修改内容无效');
      if (body.base_revision !== question.revision) return conflict(question);
      const next = { ...question, understood: body.understood, revision: question.revision + 1, updated_at: h.now() };
      host.saveQuestion(next);
      return ok(next);
    });
    case 'GET /source-snapshots/:id': {
      const snapshot = store.snapshots.find(item => item.id === id);
      return snapshot && visible().has(snapshot.class_session_id) ? ok(snapshot) : h.error(404, 'NOT_FOUND', '来源快照不存在');
    }
    default: return null;
  }
}
