import { describe, expect, it } from 'vitest';
import type { components } from '../src/generated/openapi';
import {
  alignTranscripts, audioState, filtersToParams, lineDiff, markdownBlocks, narrowFilters, parseFilters, parseReadingMemory, readingRevision, rememberReading,
  resolveSource, retranscribeErrors, revisionRole, taskFirst, undecidedCandidates,
} from '../src/domain/review';

type S = components['schemas'];
const id = (n: number) => `e1000000-0000-4000-8000-${n.toString(16).padStart(12, '0')}`;
const doc = (over: Partial<S['ReviewDocument']> = {}): S['ReviewDocument'] => ({ id: 'e0000000-0000-4000-8000-000000000001', revision: 3, created_at: '2026-10-27T08:00:00Z', updated_at: '2026-10-27T08:00:00Z',
  class_session_id: 'c0000000-0000-4000-8000-000000000001', kind: 'summary', title: '总结', adopted_revision_id: null, latest_personal_revision_id: null, candidate_revision_ids: [], understood: false, source_status: 'unchanged', ...over });
const rev = (n: number, kind: S['ReviewRevision']['kind'], created: string, body = `正文 ${n}`): S['ReviewRevision'] => ({ id: id(n), revision: 1, created_at: created, updated_at: created, document_id: doc().id, kind,
  parent_revision_id: null, source_snapshot_id: null, raw_output_ref: null, body_markdown: body, structured_content: null, personal_note: '' });
const segment = (n: number, start: number | null, end: number | null, text: string, revisionId = `73100000-0000-4000-8000-${String(n).padStart(12, '0')}`): S['TranscriptSegment'] => ({
  id: `73000000-0000-4000-8000-${String(n).padStart(12, '0')}`, revision: 1, created_at: 'x', updated_at: 'x', class_session_id: 'c', transcript_version_id: 'v', stream_id: null, segment_revision_id: revisionId,
  kind: 'machine', origin: 'live_asr', status: 'final', text, start_ms: start, end_ms: end, timestamp_source: 'capture_mapping', provider_request_id: null, source_chunk_ids: [], adopted_revision_id: null });

describe('PR-10b revision roles', () => {
  const ai1 = rev(1, 'ai_candidate', '2026-10-27T08:00:00Z');
  const ai2 = rev(2, 'ai_candidate', '2026-10-28T08:00:00Z');
  const mine = rev(3, 'personal', '2026-10-28T09:00:00Z');
  it('the reader opens the adopted revision, else the latest personal revision', () => {
    expect(readingRevision(doc({ adopted_revision_id: ai1.id, latest_personal_revision_id: mine.id, candidate_revision_ids: [ai2.id] }), [ai1, ai2, mine])).toMatchObject({ revision: { id: ai1.id }, role: 'adopted' });
    expect(readingRevision(doc({ latest_personal_revision_id: mine.id, candidate_revision_ids: [ai2.id] }), [ai1, ai2, mine])).toMatchObject({ revision: { id: mine.id }, role: 'personal_latest' });
  });
  it('without any adopted or personal revision the newest candidate is shown as a candidate, never promoted', () => {
    const result = readingRevision(doc({ candidate_revision_ids: [ai1.id, ai2.id] }), [ai1, ai2]);
    expect(result).toMatchObject({ revision: { id: ai2.id }, role: 'candidate' });
    expect(readingRevision(doc(), [])).toBeNull();
  });
  it('classifies every revision kind and lists undecided candidates', () => {
    const d = doc({ adopted_revision_id: ai1.id, latest_personal_revision_id: mine.id, candidate_revision_ids: [ai2.id] });
    expect([ai1, ai2, mine, rev(4, 'personal', 'a'), rev(5, 'ai_candidate', 'a')].map(item => revisionRole(d, item))).toEqual(['adopted', 'candidate', 'personal_latest', 'personal_earlier', 'ai_earlier']);
    expect(undecidedCandidates(doc({ adopted_revision_id: ai1.id, candidate_revision_ids: [ai1.id, ai2.id] }))).toEqual([ai2.id]);
  });
});

describe('PR-10b task-first grouping', () => {
  const question = (understood: boolean, state: S['Question']['state'] = 'succeeded') => ({ id: 'q', revision: 1, created_at: 'a', updated_at: 'a', class_session_id: 'c', task_id: 't', source_snapshot_id: 's', question: '?', answer_markdown: 'a', state, understood }) as S['Question'];
  it('needs-a-decision collects new candidates and changed sources; unresolved needs a readable version', () => {
    const adoptedWithNew = doc({ id: 'a1', adopted_revision_id: id(1), candidate_revision_ids: [id(2)] });
    const candidateOnly = doc({ id: 'a2', candidate_revision_ids: [id(3)], source_status: 'new_material_not_included' });
    const understoodNote = doc({ id: 'a3', kind: 'note', latest_personal_revision_id: id(4), understood: true });
    const result = taskFirst([adoptedWithNew, candidateOnly, understoodNote], [question(false), question(true), question(false, 'running')], 'a3');
    expect(result.continueDoc?.id).toBe('a3');
    expect(result.pending.map(item => item.doc.id).sort()).toEqual(['a1', 'a2']);
    expect(result.pending.find(item => item.doc.id === 'a2')!.reasons).toHaveLength(2);
    expect(result.unresolvedDocs.map(item => item.id)).toEqual(['a1']);
    expect(result.unresolvedQuestions).toHaveLength(1);
    expect(taskFirst([], [], 'gone').continueDoc).toBeNull();
  });
});

describe('PR-10b library filters', () => {
  const sem = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01'; const course = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1'; const cls = 'c0000000-0000-4000-8000-000000000001';
  it('round-trips valid values through the URL and drops malformed ones', () => {
    const filters = { semester_id: sem, course_id: course, class_session_id: cls, kind: 'card' as const, understood: false };
    expect(parseFilters(filtersToParams(filters))).toEqual(filters);
    expect(parseFilters(new URLSearchParams('course_id=../x&kind=video&understood=maybe'))).toEqual({});
  });
  it('a broader scope clears narrower ones', () => {
    expect(narrowFilters({ semester_id: sem, course_id: course, class_session_id: cls, kind: 'note' }, { semester_id: undefined })).toEqual({ kind: 'note' });
    expect(narrowFilters({ course_id: course, class_session_id: cls }, { course_id: course })).toEqual({ course_id: course });
    expect(narrowFilters({}, { understood: true })).toEqual({ understood: true });
  });
});

describe('PR-10b line comparison', () => {
  it('marks added and removed lines around common ones', () => {
    expect(lineDiff('a\nb\nc', 'a\nx\nc\nd')).toEqual([{ type: 'same', text: 'a' }, { type: 'removed', text: 'b' }, { type: 'added', text: 'x' }, { type: 'same', text: 'c' }, { type: 'added', text: 'd' }]);
    expect(lineDiff('same', 'same')).toEqual([{ type: 'same', text: 'same' }]);
  });
});

describe('PR-10b frozen source resolution', () => {
  const sha = 'a'.repeat(64);
  const current = { segments: [segment(1, 0, 1000, '当前文本')], slides: [{ id: 's1', revision: 2, created_at: 'a', updated_at: 'a', class_session_id: 'c', asset_id: 'x', version_id: 'v2', title: '板书', start_ms: 5, end_ms: null, rotation: '0', removed: false }] as S['Slide'][], marks: [] as S['Mark'][] };
  it('shows only the frozen revision; a newer revision is reported as changed, never substituted', () => {
    expect(resolveSource({ kind: 'transcript_segment', id: segment(1, 0, 0, '').id, revision_id: segment(1, 0, 0, '').segment_revision_id, content_sha256: sha }, current)).toMatchObject({ state: 'current', text: '当前文本' });
    const changed = resolveSource({ kind: 'transcript_segment', id: segment(1, 0, 0, '').id, revision_id: 'old', content_sha256: sha }, current);
    expect(changed).toMatchObject({ state: 'changed', text: null, startMs: 0 });
    expect(resolveSource({ kind: 'slide_version', id: 's1', revision_id: 'v1', content_sha256: sha }, current)).toMatchObject({ state: 'changed', text: null });
    expect(resolveSource({ kind: 'slide_version', id: 'gone', revision_id: 'v1', content_sha256: sha }, current)).toMatchObject({ state: 'missing' });
    expect(resolveSource({ kind: 'mark', id: 'gone', revision_id: 'v1', content_sha256: sha }, current)).toMatchObject({ state: 'missing' });
  });
  it('original audio keeps time positions only; expiry is judged on the given clock', () => {
    const now = Date.parse('2026-10-28T08:00:00Z');
    expect(audioState({ audio_expires_at: '2026-10-28T07:59:59Z' }, now)).toBe('expired');
    expect(audioState({ audio_expires_at: '2026-11-03T00:00:00Z' }, now)).toBe('in_period');
    expect(audioState({ audio_expires_at: null }, now)).toBe('unknown');
  });
});

describe('PR-10b retranscription helpers', () => {
  it('maps exactly the four 0.3.0 refusal codes to distinct messages', () => {
    const codes = ['AUDIO_EXPIRED', 'AUDIO_DELETED', 'QUOTA_SESSION_EXCEEDED', 'QUOTA_MONTH_EXCEEDED'] as const;
    expect(Object.keys(retranscribeErrors).sort()).toEqual([...codes].sort());
    expect(new Set(codes.map(code => retranscribeErrors[code])).size).toBe(4);
  });
  it('aligns two transcript versions by overlapping time and keeps unmatched segments', () => {
    const rows = alignTranscripts([segment(1, 0, 1000, 'a'), segment(2, 2000, 3000, 'b'), segment(3, null, null, 'u')], [segment(11, 100, 900, 'a2'), segment(12, 5000, 6000, 'c')]);
    expect(rows.map(row => [row.startMs, row.before?.text ?? null, row.after?.text ?? null])).toEqual([[0, 'a', 'a2'], [2000, 'b', null], [5000, null, 'c'], [null, 'u', null]]);
  });
});

describe('PR-10b reading memory and markdown', () => {
  const docId = 'e0000000-0000-4000-8000-000000000001';
  it('stores only document IDs and offsets; malformed storage is ignored', () => {
    expect(parseReadingMemory('not json')).toEqual({ last: null, positions: {} });
    expect(parseReadingMemory(JSON.stringify({ last: 'x', positions: { x: 5, [docId]: -1 } }))).toEqual({ last: null, positions: {} });
    const next = rememberReading({ last: null, positions: {} }, docId, 120.7);
    expect(next).toEqual({ last: docId, positions: { [docId]: 120 } });
    expect(Object.keys(parseReadingMemory(JSON.stringify(next)))).toEqual(['last', 'positions']);
  });
  it('renders a safe subset as blocks of text only', () => {
    expect(markdownBlocks('## 标题\n段落一\n续行\n\n- 项一\n- 项二\n<script>x</script>')).toEqual([
      { type: 'heading', level: 2, text: '标题' }, { type: 'paragraph', text: '段落一 续行' }, { type: 'list', items: ['项一', '项二'] }, { type: 'paragraph', text: '<script>x</script>' }]);
  });
});
