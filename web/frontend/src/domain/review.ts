import type { components } from '../generated/openapi';

/**
 * PR-10b pure review logic: which revision a reader sees, how documents are grouped task-first,
 * library filters <-> URL, line comparison of two revisions, frozen-source resolution, the 0.3.0
 * retranscription error codes and time alignment of two transcript versions. No DOM, no network.
 */
type S = components['schemas'];
export type Doc = S['ReviewDocument'];
export type Revision = S['ReviewRevision'];

export const kindLabel: Record<Doc['kind'], string> = { summary: '课堂总结', card: '复习卡片', note: '个人笔记' };

/** A revision's role in its document. AI output is never "adopted" unless the user chose it. */
export type RevisionRole = 'adopted' | 'candidate' | 'personal_latest' | 'personal_earlier' | 'ai_earlier';
export function revisionRole(doc: Doc, revision: Revision): RevisionRole {
  if (doc.adopted_revision_id === revision.id) return 'adopted';
  if (revision.kind === 'ai_candidate') return doc.candidate_revision_ids.includes(revision.id) ? 'candidate' : 'ai_earlier';
  return doc.latest_personal_revision_id === revision.id ? 'personal_latest' : 'personal_earlier';
}
export const roleLabel: Record<RevisionRole, string> = {
  adopted: '已采用', candidate: 'AI 候选（未采用）', personal_latest: '个人稿（最新）', personal_earlier: '个人稿（较早）', ai_earlier: 'AI 稿（较早）',
};

/**
 * The revision the reader opens with: the adopted one, else the latest personal revision, else the
 * newest candidate shown explicitly as an unadopted candidate. Never silently promotes a candidate.
 */
export function readingRevision(doc: Doc, revisions: readonly Revision[]): { revision: Revision; role: RevisionRole } | null {
  const byId = new Map(revisions.map(item => [item.id, item]));
  const pick = doc.adopted_revision_id ?? doc.latest_personal_revision_id;
  const chosen = pick ? byId.get(pick) : undefined;
  if (chosen) return { revision: chosen, role: revisionRole(doc, chosen) };
  const newestCandidate = [...revisions].filter(item => doc.candidate_revision_ids.includes(item.id)).sort(newestFirst)[0];
  return newestCandidate ? { revision: newestCandidate, role: 'candidate' } : null;
}
export const newestFirst = (a: { created_at: string; id: string }, b: { created_at: string; id: string }) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id);

/** Candidates the user has not decided on (any AI revision other than the adopted one counts). */
export const undecidedCandidates = (doc: Doc) => doc.candidate_revision_ids.filter(id => id !== doc.adopted_revision_id);

export const sourceStatusText: Record<Doc['source_status'], string | null> = {
  unchanged: null,
  changed: '课堂内容在生成后有更新，这份资料仍按生成时的来源，可在来源侧栏核对。',
  unavailable: '部分来源已不可用（例如原音过期或资料被删除），来源侧栏会标明缺失项。',
  new_material_not_included: '课堂有新材料没有纳入这份资料。',
};

/** Task-first grouping (UI-REVIEW-01): what to continue, what is unresolved, what needs a decision. */
export type TaskFirst = {
  continueDoc: Doc | null;
  pending: Array<{ doc: Doc; reasons: string[] }>;
  unresolvedDocs: Doc[];
  unresolvedQuestions: S['Question'][];
};
export function taskFirst(docs: readonly Doc[], questions: readonly S['Question'][], lastOpenedId: string | null): TaskFirst {
  const pending: TaskFirst['pending'] = [];
  for (const doc of docs) {
    const reasons: string[] = [];
    const undecided = undecidedCandidates(doc).length;
    if (undecided && doc.adopted_revision_id) reasons.push(`有 ${undecided} 个新的 AI 候选待比较`);
    else if (undecided) reasons.push('AI 候选尚未采用');
    if (doc.source_status !== 'unchanged') reasons.push(sourceStatusText[doc.source_status]!);
    if (reasons.length) pending.push({ doc, reasons });
  }
  const readable = (doc: Doc) => doc.adopted_revision_id !== null || doc.latest_personal_revision_id !== null;
  return {
    continueDoc: (lastOpenedId && docs.find(doc => doc.id === lastOpenedId)) || null,
    pending: pending.sort((a, b) => newestFirst({ created_at: a.doc.updated_at, id: a.doc.id }, { created_at: b.doc.updated_at, id: b.doc.id })),
    unresolvedDocs: docs.filter(doc => !doc.understood && readable(doc)),
    unresolvedQuestions: questions.filter(item => item.state === 'succeeded' && !item.understood),
  };
}

/** Library filters (UI-REVIEW-02); only well-formed values survive a round trip through the URL. */
export type LibraryFilters = { semester_id?: string; course_id?: string; class_session_id?: string; kind?: Doc['kind']; understood?: boolean };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export function parseFilters(params: URLSearchParams): LibraryFilters {
  const result: LibraryFilters = {};
  for (const key of ['semester_id', 'course_id', 'class_session_id'] as const) { const value = params.get(key); if (value && UUID.test(value)) result[key] = value; }
  const kind = params.get('kind'); if (kind === 'summary' || kind === 'card' || kind === 'note') result.kind = kind;
  const understood = params.get('understood'); if (understood === 'true' || understood === 'false') result.understood = understood === 'true';
  return result;
}
export function filtersToParams(filters: LibraryFilters): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of ['semester_id', 'course_id', 'class_session_id', 'kind'] as const) if (filters[key]) params.set(key, filters[key]!);
  if (filters.understood !== undefined) params.set('understood', String(filters.understood));
  return params;
}
/** Choosing a broader scope clears narrower ones that may no longer belong to it. */
export function narrowFilters(current: LibraryFilters, change: Partial<LibraryFilters>): LibraryFilters {
  const next: LibraryFilters = { ...current, ...change };
  if ('semester_id' in change) { delete next.course_id; delete next.class_session_id; }
  if ('course_id' in change) delete next.class_session_id;
  for (const key of Object.keys(next) as Array<keyof LibraryFilters>) if (next[key] === undefined || next[key] === '') delete next[key];
  return next;
}

/** Line comparison of two revisions (longest common subsequence over lines). */
export type DiffLine = { type: 'same' | 'added' | 'removed'; text: string };
export function lineDiff(before: string, after: string): DiffLine[] {
  const a = before.split('\n'); const b = after.split('\n');
  // Bounded input: revisions are at most 120 000 characters; past 2 000 lines compare coarsely.
  if (a.length * b.length > 4_000_000) return [...a.map(text => ({ type: 'removed' as const, text })), ...b.map(text => ({ type: 'added' as const, text }))];
  const table = Array.from({ length: a.length + 1 }, () => new Uint32Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
  const out: DiffLine[] = [];
  let i = 0; let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { out.push({ type: 'same', text: a[i] }); i++; j++; }
    else if (table[i + 1][j] >= table[i][j + 1]) out.push({ type: 'removed', text: a[i++] });
    else out.push({ type: 'added', text: b[j++] });
  }
  while (i < a.length) out.push({ type: 'removed', text: a[i++] });
  while (j < b.length) out.push({ type: 'added', text: b[j++] });
  return out;
}

/** A frozen source reference resolved against what is readable now (UI-REVIEW-07). */
export type ResolvedSource =
  | { kind: S['SourceRef']['kind']; state: 'current'; label: string; text: string; startMs: number | null; endMs: number | null }
  | { kind: S['SourceRef']['kind']; state: 'changed' | 'missing'; label: string; text: null; startMs: number | null; endMs: number | null };
export function resolveSource(ref: S['SourceRef'], current: { segments: readonly S['TranscriptSegment'][]; slides: readonly S['Slide'][]; marks: readonly S['Mark'][] }): ResolvedSource {
  if (ref.kind === 'transcript_segment') {
    const segment = current.segments.find(item => item.id === ref.id);
    if (!segment) return { kind: ref.kind, state: 'missing', label: '转写片段', text: null, startMs: null, endMs: null };
    // Only the frozen revision may be shown; a newer revision is never substituted (contract gap §3.2).
    if (segment.segment_revision_id !== ref.revision_id) return { kind: ref.kind, state: 'changed', label: '转写片段', text: null, startMs: segment.start_ms, endMs: segment.end_ms };
    return { kind: ref.kind, state: 'current', label: '转写片段', text: segment.text, startMs: segment.start_ms, endMs: segment.end_ms };
  }
  if (ref.kind === 'slide_version') {
    const slide = current.slides.find(item => item.id === ref.id);
    if (!slide || slide.removed) return { kind: ref.kind, state: 'missing', label: '课件图片', text: null, startMs: null, endMs: null };
    if (slide.version_id !== ref.revision_id) return { kind: ref.kind, state: 'changed', label: '课件图片', text: null, startMs: slide.start_ms, endMs: slide.end_ms };
    return { kind: ref.kind, state: 'current', label: '课件图片', text: slide.title || '未命名课件', startMs: slide.start_ms, endMs: slide.end_ms };
  }
  const mark = current.marks.find(item => item.id === ref.id);
  if (!mark) return { kind: ref.kind, state: 'missing', label: '标记', text: null, startMs: null, endMs: null };
  return { kind: ref.kind, state: 'current', label: mark.kind === 'unclear' ? '没懂标记' : '重点标记', text: mark.note || '（无备注）', startMs: null, endMs: null };
}

/** Original audio availability for tracing: time positions stay, playback is never offered (gap §3.3). */
export function audioState(session: Pick<S['ClassSession'], 'audio_expires_at'>, nowMs: number): 'in_period' | 'expired' | 'unknown' {
  if (session.audio_expires_at === null) return 'unknown';
  return Date.parse(session.audio_expires_at) <= nowMs ? 'expired' : 'in_period';
}

/** 0.3.0 retranscription refusals: branch only on error.code, never on the message text. */
export const retranscribeErrors: Partial<Record<S['Error']['error']['code'], string>> = {
  AUDIO_EXPIRED: '原音已过保留期限，不能再重新转写。已有的转写版本不受影响。',
  AUDIO_DELETED: '原音已被删除或访问已撤销，不能重新转写。已有的转写版本不受影响。',
  QUOTA_SESSION_EXCEEDED: '这节课的重新转写次数已用完；再追加一次需要审批。请求会保持待审批，不会自动提交。',
  QUOTA_MONTH_EXCEEDED: '本月重新转写时长不足，这次没有提交。',
};

/** Time alignment of two transcript versions for side-by-side comparison. */
export type AlignedRow = { startMs: number | null; before: S['TranscriptSegment'] | null; after: S['TranscriptSegment'] | null };
export function alignTranscripts(before: readonly S['TranscriptSegment'][], after: readonly S['TranscriptSegment'][]): AlignedRow[] {
  const sort = (list: readonly S['TranscriptSegment'][]) => [...list].sort((x, y) => (x.start_ms ?? Number.MAX_SAFE_INTEGER) - (y.start_ms ?? Number.MAX_SAFE_INTEGER) || x.id.localeCompare(y.id));
  const left = sort(before); const right = sort(after);
  const overlaps = (x: S['TranscriptSegment'], y: S['TranscriptSegment']) => x.start_ms !== null && y.start_ms !== null && (x.end_ms ?? x.start_ms) >= y.start_ms && (y.end_ms ?? y.start_ms) >= x.start_ms;
  const used = new Set<string>();
  const rows: AlignedRow[] = left.map(item => {
    const match = right.find(candidate => !used.has(candidate.id) && overlaps(item, candidate));
    if (match) used.add(match.id);
    return { startMs: item.start_ms, before: item, after: match ?? null };
  });
  for (const item of right) if (!used.has(item.id)) rows.push({ startMs: item.start_ms, before: null, after: item });
  return rows.sort((x, y) => (x.startMs ?? Number.MAX_SAFE_INTEGER) - (y.startMs ?? Number.MAX_SAFE_INTEGER));
}

export function formatMs(ms: number | null): string {
  if (ms === null) return '时间未知';
  const total = Math.floor(ms / 1000);
  const minutes = Math.floor(total / 60); const seconds = total % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

/** Minimal, safe Markdown subset (headings, bullet lists, paragraphs); rendered as React text only. */
export type Block = { type: 'heading'; level: 2 | 3; text: string } | { type: 'list'; items: string[] } | { type: 'paragraph'; text: string };
export function markdownBlocks(markdown: string): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = []; let list: string[] = [];
  const flush = () => {
    if (paragraph.length) { blocks.push({ type: 'paragraph', text: paragraph.join(' ') }); paragraph = []; }
    if (list.length) { blocks.push({ type: 'list', items: list }); list = []; }
  };
  for (const raw of markdown.split('\n')) {
    const line = raw.trimEnd();
    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) { flush(); blocks.push({ type: 'heading', level: heading[1].length >= 3 ? 3 : 2, text: heading[2] }); continue; }
    const item = /^\s*[-*]\s+(.+)$/.exec(line);
    if (item) { if (paragraph.length) { blocks.push({ type: 'paragraph', text: paragraph.join(' ') }); paragraph = []; } list.push(item[1]); continue; }
    if (!line.trim()) { flush(); continue; }
    if (list.length) { blocks.push({ type: 'list', items: list }); list = []; }
    paragraph.push(line.trim());
  }
  flush();
  return blocks;
}

/** Device-local reading memory: only document IDs and scroll offsets, never content (gap §3.5). */
export type ReadingMemory = { last: string | null; positions: Record<string, number> };
export function parseReadingMemory(raw: string | null): ReadingMemory {
  try {
    const value = JSON.parse(raw ?? '') as Partial<ReadingMemory>;
    const positions: Record<string, number> = {};
    for (const [id, offset] of Object.entries(value.positions ?? {})) if (UUID.test(id) && Number.isFinite(offset) && (offset as number) >= 0) positions[id] = Math.floor(offset as number);
    return { last: typeof value.last === 'string' && UUID.test(value.last) ? value.last : null, positions };
  } catch { return { last: null, positions: {} }; }
}
export function rememberReading(memory: ReadingMemory, id: string, offset: number, limit = 50): ReadingMemory {
  const positions = { ...memory.positions, [id]: Math.max(0, Math.floor(offset)) };
  const keys = Object.keys(positions);
  for (const key of keys.slice(0, Math.max(0, keys.length - limit))) if (key !== id) delete positions[key];
  return { last: id, positions };
}
