import { describe, expect, it } from 'vitest';
import type { components } from '../src/generated/openapi';
import { createMockBackend, mockIds, type MockAccountKey } from '../src/mock/backend';

type S = components['schemas'];
const q = (value = '') => new URLSearchParams(value);
const ENDED = 'c0000000-0000-4000-8000-000000000001';
const UPLOAD_FAILED = 'c0000000-0000-4000-8000-000000000004';
const SUMMARY = 'e0000000-0000-4000-8000-000000000001';
const NOTE = 'e0000000-0000-4000-8000-000000000003';
const ADOPTED = 'e1000000-0000-4000-8000-000000000001';
const CANDIDATE = 'e1000000-0000-4000-8000-000000000005';
function harness() {
  const backend = createMockBackend();
  let key = 0;
  const call = (account: MockAccountKey, method: string, path: string, body?: unknown, query = '', idempotencyKey?: string) =>
    backend.handle(account, method, path, q(query), body, idempotencyKey ?? (method === 'GET' ? null : `00000000-0000-4000-8000-${String(++key).padStart(12, '0')}`))!;
  return { backend, call };
}
const err = (reply: { body: unknown }) => (reply.body as S['Error']).error;
const items = <T>(reply: { body: unknown }) => (reply.body as { items: T[] }).items;
const retranscribe = (over: Partial<S['GenerationPlanRequest']> = {}) => ({ kind: 'asr_retranscribe', scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: false, ...over });
const execute = (plan: S['GenerationPlan'], over: Partial<S['ExecutePlan']> = {}) => ({ plan_sha256: plan.plan_sha256, confirmed_destination_revision: plan.destination.configuration_revision, confirm_send: true, confirm_images: false, confirm_original_snapshot: false, confirm_partial_scope: false, ...over });

describe('PR-10b mock review documents', () => {
  it('the library combines semester, course, class, kind and understood filters over authorised classes', () => {
    const { call } = harness();
    expect(items<S['ReviewDocument']>(call('A', 'GET', '/review-documents')).map(item => item.id)).toHaveLength(4);
    expect(items<S['ReviewDocument']>(call('A', 'GET', '/review-documents', undefined, `class_session_id=${ENDED}`)).map(item => item.id).sort()).toEqual([SUMMARY, 'e0000000-0000-4000-8000-000000000002']);
    expect(items(call('A', 'GET', '/review-documents', undefined, `semester_id=${mockIds.spring}`))).toHaveLength(0);
    expect(items(call('A', 'GET', '/review-documents', undefined, `course_id=${mockIds.la}&kind=note&understood=true`))).toHaveLength(1);
    expect(items(call('B', 'GET', '/review-documents'))).toHaveLength(0);
    expect(err(call('B', 'GET', `/review-documents/${SUMMARY}`)).code).toBe('NOT_FOUND');
  });

  it('revisions and frozen snapshots are readable; a summary result becomes a readable candidate, never adopted', () => {
    const { backend, call } = harness();
    const revisions = items<S['ReviewRevision']>(call('A', 'GET', `/review-documents/${SUMMARY}/revisions`));
    expect(revisions.map(item => item.id)).toEqual([CANDIDATE, ADOPTED]);
    const snapshot = call('A', 'GET', `/source-snapshots/${revisions[0].source_snapshot_id}`).body as S['SourceSnapshot'];
    expect(snapshot.source_refs).toHaveLength(3);
    expect(err(call('B', 'GET', `/source-snapshots/${revisions[0].source_snapshot_id}`)).code).toBe('NOT_FOUND');
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, { kind: 'review_summary', scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: false }).body as S['GenerationPlan'];
    const task = call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan)).body as S['Task'];
    backend.advanceTask('A', task.id, 'succeeded');
    const created = items<S['ReviewDocument']>(call('A', 'GET', '/review-documents', undefined, `class_session_id=${ENDED}`)).find(item => item.candidate_revision_ids.length === 1 && item.adopted_revision_id === null)!;
    const candidate = call('A', 'GET', `/review-revisions/${created.candidate_revision_ids[0]}`).body as S['ReviewRevision'];
    expect(candidate).toMatchObject({ kind: 'ai_candidate', document_id: created.id, source_snapshot_id: plan.source_snapshot!.id });
    expect(call('A', 'GET', `/source-snapshots/${plan.source_snapshot!.id}`).status).toBe(200);
  });

  it('understood is a CAS write; a stale base_revision is rejected with the current revision', () => {
    const { call } = harness();
    const updated = call('A', 'PATCH', `/review-documents/${SUMMARY}`, { base_revision: 1, understood: true }).body as S['ReviewDocument'];
    expect(updated).toMatchObject({ understood: true, revision: 2 });
    const stale = err(call('A', 'PATCH', `/review-documents/${SUMMARY}`, { base_revision: 1, understood: false }));
    expect(stale).toMatchObject({ code: 'REVISION_CONFLICT', current_revision: 2 });
    expect(err(call('A', 'PATCH', `/review-documents/${SUMMARY}`, { base_revision: 2, understood: 'yes' })).code).toBe('VALIDATION_FAILED');
    const question = items<S['Question']>(call('A', 'GET', '/questions', undefined, `class_session_id=${ENDED}`))[0];
    expect(call('A', 'PATCH', `/questions/${question.id}`, { base_revision: question.revision, understood: true }).body).toMatchObject({ understood: true, revision: question.revision + 1 });
    expect(err(call('A', 'PATCH', `/questions/${question.id}`, { base_revision: question.revision, understood: false })).code).toBe('REVISION_CONFLICT');
  });

  it('a personal revision never replaces the adopted revision or candidates, and is idempotent per key', () => {
    const { call } = harness();
    const body = { base_revision: 1, base_content_revision_id: ADOPTED, draft_id: '5a000000-0000-4000-8000-000000000001', body_markdown: '我的整理（合成）', personal_note: '' };
    const first = call('A', 'POST', `/review-documents/${SUMMARY}/personal-revisions`, body, '', 'k-personal');
    expect(first.status).toBe(201);
    expect(call('A', 'POST', `/review-documents/${SUMMARY}/personal-revisions`, body, '', 'k-personal').body).toEqual(first.body);
    const doc = call('A', 'GET', `/review-documents/${SUMMARY}`).body as S['ReviewDocument'];
    expect(doc).toMatchObject({ adopted_revision_id: ADOPTED, candidate_revision_ids: [CANDIDATE], latest_personal_revision_id: (first.body as S['ReviewRevision']).id, revision: 2 });
    expect(err(call('A', 'POST', `/review-documents/${SUMMARY}/personal-revisions`, body)).code).toBe('REVISION_CONFLICT');
    expect(err(call('A', 'POST', `/review-documents/${SUMMARY}/personal-revisions`, { ...body, base_revision: 2, base_content_revision_id: 'e1000000-0000-4000-8000-000000000003' })).code).toBe('VALIDATION_FAILED');
  });

  it('adoption is explicit and CAS-checked; an older AI revision stays history (adoptable), only newer ones stay pending', () => {
    const { call } = harness();
    expect(err(call('A', 'POST', `/review-documents/${SUMMARY}/adopt`, { base_revision: 9, revision_id: CANDIDATE })).code).toBe('REVISION_CONFLICT');
    expect(err(call('A', 'POST', `/review-documents/${SUMMARY}/adopt`, { base_revision: 1, revision_id: 'e1000000-0000-4000-8000-000000000003' })).code).toBe('VALIDATION_FAILED');
    const adopted = call('A', 'POST', `/review-documents/${SUMMARY}/adopt`, { base_revision: 1, revision_id: CANDIDATE }).body as S['ReviewDocument'];
    expect(adopted).toMatchObject({ adopted_revision_id: CANDIDATE, candidate_revision_ids: [], revision: 2 });
    const back = call('A', 'POST', `/review-documents/${SUMMARY}/adopt`, { base_revision: 2, revision_id: ADOPTED }).body as S['ReviewDocument'];
    expect(back).toMatchObject({ adopted_revision_id: ADOPTED, candidate_revision_ids: [CANDIDATE] });
    expect(items(call('A', 'GET', `/review-documents/${SUMMARY}/revisions`))).toHaveLength(2);
  });

  it('a personal note creates a note document with one personal revision', () => {
    const { call } = harness();
    const created = call('A', 'POST', '/review-documents', { class_session_id: ENDED, title: '补充', body_markdown: '内容（合成）', draft_id: '5a000000-0000-4000-8000-000000000002' });
    expect(created.status).toBe(201);
    const doc = created.body as S['ReviewDocument'];
    expect(doc).toMatchObject({ kind: 'note', adopted_revision_id: null, candidate_revision_ids: [] });
    expect(items<S['ReviewRevision']>(call('A', 'GET', `/review-documents/${doc.id}/revisions`))[0]).toMatchObject({ kind: 'personal', body_markdown: '内容（合成）' });
    expect(err(call('B', 'POST', '/review-documents', { class_session_id: ENDED, title: 'x', body_markdown: 'y', draft_id: '5a000000-0000-4000-8000-000000000003' })).code).toBe('NOT_FOUND');
    expect(call('A', 'GET', `/review-documents/${NOTE}`).status).toBe(200);
  });
});

describe('PR-10b mock review: Astra r0 findings', () => {
  it('a revoked classroom never replays an earlier review receipt', () => {
    const { backend, call } = harness();
    const body = { base_revision: 1, base_content_revision_id: ADOPTED, draft_id: '5a000000-0000-4000-8000-000000000010', body_markdown: '合成', personal_note: '' };
    expect(call('A', 'POST', `/review-documents/${SUMMARY}/personal-revisions`, body, '', 'k-revoked').status).toBe(201);
    backend.revokeClassroom('A', ENDED);
    expect(err(call('A', 'POST', `/review-documents/${SUMMARY}/personal-revisions`, body, '', 'k-revoked')).code).toBe('NOT_FOUND');
  });
  it('an understood patch replayed with the same key returns its receipt instead of a conflict', () => {
    const { call } = harness();
    const first = call('A', 'PATCH', `/review-documents/${SUMMARY}`, { base_revision: 1, understood: true }, '', 'k-patch');
    expect(call('A', 'PATCH', `/review-documents/${SUMMARY}`, { base_revision: 1, understood: true }, '', 'k-patch')).toEqual(first);
  });
  it('a stale personal save keeps the submitted branch without moving any pointer; a derived revision keeps the parent snapshot', () => {
    const { call } = harness();
    const stale = call('A', 'POST', `/review-documents/${SUMMARY}/personal-revisions`, { base_revision: 7, base_content_revision_id: ADOPTED, draft_id: '5a000000-0000-4000-8000-000000000011', body_markdown: '分支（合成）', personal_note: '' });
    // The branch is stored and the document's CAS clock advances (pointers unchanged).
    expect(err(stale)).toMatchObject({ code: 'REVISION_CONFLICT', current_revision: 2 });
    const revisions = items<S['ReviewRevision']>(call('A', 'GET', `/review-documents/${SUMMARY}/revisions`));
    const branch = revisions.find(item => item.body_markdown === '分支（合成）')!;
    expect(branch).toMatchObject({ kind: 'personal', parent_revision_id: ADOPTED, source_snapshot_id: 'e2000000-0000-4000-8000-000000000001' });
    expect(call('A', 'GET', `/review-documents/${SUMMARY}`).body).toMatchObject({ revision: 2, latest_personal_revision_id: null, adopted_revision_id: ADOPTED });
    expect(err(call('A', 'PATCH', `/review-documents/${SUMMARY}`, { base_revision: 1, understood: true })).code).toBe('REVISION_CONFLICT');
  });
  it('lists are paged at the requested limit with a continuation cursor', () => {
    const { call } = harness();
    const first = call('A', 'GET', '/review-documents', undefined, 'limit=3').body as { items: unknown[]; next_cursor: string | null };
    expect(first.items).toHaveLength(3);
    const second = call('A', 'GET', '/review-documents', undefined, `limit=3&cursor=${first.next_cursor}`).body as { items: unknown[]; next_cursor: string | null };
    expect([second.items.length, second.next_cursor]).toEqual([1, null]);
    expect(err(call('A', 'GET', '/review-documents', undefined, 'limit=101')).code).toBe('VALIDATION_FAILED');
  });
  it('source_status is projected from the active revision against current sources', () => {
    const { backend, call } = harness();
    expect(call('A', 'GET', `/review-documents/${SUMMARY}`).body).toMatchObject({ source_status: 'unchanged' });
    call('A', 'POST', `/review-documents/${SUMMARY}/adopt`, { base_revision: 1, revision_id: CANDIDATE });
    expect(call('A', 'GET', `/review-documents/${SUMMARY}`).body).toMatchObject({ source_status: 'changed' });
    call('A', 'POST', `/review-documents/${SUMMARY}/adopt`, { base_revision: 2, revision_id: ADOPTED });
    backend.classroomStore('A').slides = backend.classroomStore('A').slides.map(item => ({ ...item, removed: true }));
    expect(call('A', 'GET', `/review-documents/${SUMMARY}`).body).toMatchObject({ source_status: 'unavailable' });
  });
});

describe('PR-10b mock retranscription (PR-06c handoff)', () => {
  it('generation reads only the adopted transcript; an unadopted retranscription is never a source', () => {
    const { backend, call } = harness();
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe()).body as S['GenerationPlan'];
    const task = call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan)).body as S['Task'];
    const versionId = backend.advanceTask('A', task.id, 'succeeded')!.result_id!;
    const candidateIds = new Set(items<S['TranscriptSegment']>(call('A', 'GET', `/transcript-versions/${versionId}/segments`)).map(item => item.id));
    const summary = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, { kind: 'review_summary', scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: false }).body as S['GenerationPlan'];
    expect(summary.source_snapshot!.source_refs.some(ref => candidateIds.has(ref.id))).toBe(false);
    expect(summary.source_snapshot!.source_refs).toHaveLength(3);
    const session = call('A', 'GET', `/class-sessions/${ENDED}`).body as S['ClassSession'];
    call('A', 'POST', `/class-sessions/${ENDED}/adopt-transcript`, { base_revision: session.revision, transcript_version_id: versionId });
    const after = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, { kind: 'review_summary', scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: false }).body as S['GenerationPlan'];
    expect(after.source_snapshot!.source_refs.every(ref => candidateIds.has(ref.id))).toBe(true);
  });
  it('per-epoch retention and empty ranges are refused; further runs of a class need an approval', () => {
    const { backend, call } = harness();
    // Class 3 has only a historical capture whose own deadline has passed (the session field is null).
    expect(err(call('A', 'POST', '/class-sessions/c0000000-0000-4000-8000-000000000003/generation-plans', retranscribe())).code).toBe('AUDIO_EXPIRED');
    expect(err(call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe({ scope: 'range', start_ms: 9_000_000, end_ms: 9_100_000 }))).code).toBe('SOURCE_UNAVAILABLE');
    const first = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe()).body as S['GenerationPlan'];
    expect(call('A', 'POST', `/generation-plans/${first.id}/execute`, execute(first)).status).toBe(202);
    const second = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe()).body as S['GenerationPlan'];
    expect(err(call('A', 'POST', `/generation-plans/${second.id}/execute`, execute(second))).code).toBe('QUOTA_SESSION_EXCEEDED');
    backend.setRetranscribeDev('A', { classId: ENDED, approve: true });
    expect(call('A', 'POST', `/generation-plans/${second.id}/execute`, execute(second)).status).toBe(202);
  });
  it('an audio selection that changed after preparation needs a new plan or an explicit original-snapshot confirmation', () => {
    const { backend, call } = harness();
    const plan = call('A', 'POST', `/class-sessions/${UPLOAD_FAILED}/generation-plans`, retranscribe({ accept_partial_scope: true })).body as S['GenerationPlan'];
    const manifest = backend.classroomStore('A').manifests.get(UPLOAD_FAILED)!;
    backend.classroomStore('A').manifests.set(UPLOAD_FAILED, { ...manifest, durable_ranges: [{ first_seq: 0, last_seq: 299 }], missing_ranges: [] });
    expect(call('A', 'GET', `/generation-plans/${plan.id}`).body).toMatchObject({ source_status: 'changed' });
    expect(err(call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan, { confirm_partial_scope: true }))).code).toBe('SOURCE_CHANGED');
    expect(call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan, { confirm_partial_scope: true, confirm_original_snapshot: true })).status).toBe(202);
  });

  it('prepare freezes the audio manifest; execute queues a task; success yields an unadopted retranscription version', () => {
    const { backend, call } = harness();
    expect(err(call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe({ scope: 'recent30' }))).code).toBe('VALIDATION_FAILED');
    expect(err(call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe({ include_images: true }))).code).toBe('VALIDATION_FAILED');
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe({ scope: 'range', start_ms: 0, end_ms: 30_000 })).body as S['GenerationPlan'];
    // 285 chunks over 95 minutes: 20 s each; 0–30 s freezes chunks 0 and 1 and meters only the selected 30 s.
    expect(plan).toMatchObject({ kind: 'asr_retranscribe', source_snapshot: null, quota_units: 30_000, maximum_cost_microyuan: '1500', contains_images: false });
    expect(plan.audio_manifest_sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(plan.audio_chunk_ids).toHaveLength(2);
    const task = call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan)).body as S['Task'];
    expect(task).toMatchObject({ kind: 'asr_retranscribe', state: 'queued', source_snapshot_id: null });
    expect(task.attempt_id).not.toBeNull();
    const done = backend.advanceTask('A', task.id, 'succeeded')!;
    const versions = items<S['TranscriptVersion']>(call('A', 'GET', `/class-sessions/${ENDED}/transcript-versions`));
    const candidate = versions.find(item => item.id === done.result_id)!;
    expect(candidate).toMatchObject({ kind: 'retranscription', adopted: false, state: 'sealed', source_task_id: task.id });
    const segments = items<S['TranscriptSegment']>(call('A', 'GET', `/transcript-versions/${candidate.id}/segments`));
    // The 0–30 s range wholly contains only the first seeded segment (0–18 s); the second ends at 38 s.
    expect(segments.map(item => item.start_ms)).toEqual([0]);
    expect(segments.every(item => item.stream_id === null && item.origin === 'retranscription' && item.timestamp_source === 'capture_mapping')).toBe(true);
    expect((call('A', 'GET', `/class-sessions/${ENDED}`).body as S['ClassSession']).adopted_transcript_version_id).toBeNull();
  });

  it('the four refusals are distinguished only by error code with the 0.3.0 HTTP statuses', () => {
    const { backend, call } = harness();
    backend.setRetranscribeDev('A', { classId: ENDED, audio: 'expired' });
    expect(call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe())).toMatchObject({ status: 410, body: { error: { code: 'AUDIO_EXPIRED' } } });
    backend.setRetranscribeDev('A', { classId: ENDED, audio: 'deleted' });
    expect(call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe())).toMatchObject({ status: 410, body: { error: { code: 'AUDIO_DELETED' } } });
    backend.setRetranscribeDev('A', { classId: ENDED, audio: 'ok' });
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe()).body as S['GenerationPlan'];
    backend.setRetranscribeDev('A', { quota: 'session' });
    expect(call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan))).toMatchObject({ status: 409, body: { error: { code: 'QUOTA_SESSION_EXCEEDED' } } });
    backend.setRetranscribeDev('A', { quota: 'month' });
    expect(call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan))).toMatchObject({ status: 409, body: { error: { code: 'QUOTA_MONTH_EXCEEDED' } } });
    // Audio that expired between prepare and execute is refused at execution too.
    backend.setRetranscribeDev('A', { quota: 'ok', classId: ENDED, audio: 'expired' });
    expect(err(call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan))).code).toBe('AUDIO_EXPIRED');
    expect(items<S['Task']>(call('A', 'GET', '/tasks', undefined, `class_session_id=${ENDED}`)).filter(item => item.kind === 'asr_retranscribe')).toHaveLength(0);
  });

  it('missing audio chunks need an explicit partial choice at prepare and at execute', () => {
    const { call } = harness();
    expect(err(call('A', 'POST', `/class-sessions/${UPLOAD_FAILED}/generation-plans`, retranscribe())).code).toBe('SOURCE_UNAVAILABLE');
    const plan = call('A', 'POST', `/class-sessions/${UPLOAD_FAILED}/generation-plans`, retranscribe({ accept_partial_scope: true })).body as S['GenerationPlan'];
    expect(err(call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan))).code).toBe('VALIDATION_FAILED');
    expect(call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan, { confirm_partial_scope: true })).status).toBe(202);
  });

  it('a retry names the original attempt, needs duplicate-billing consent after a possible charge, and leaves the original intact', () => {
    const { backend, call } = harness();
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe()).body as S['GenerationPlan'];
    const task = call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan)).body as S['Task'];
    expect(err(call('A', 'POST', `/tasks/${task.id}/retry`, { reason: 'x', confirm_duplicate_billing: true, original_attempt_id: task.attempt_id })).code).toBe('VALIDATION_FAILED');
    const unknown = backend.advanceTask('A', task.id, 'unknown')!;
    expect(call('A', 'POST', `/tasks/${task.id}/reconcile`, {}).body).toMatchObject({ provider_query_supported: false, new_submission: false });
    expect(err(call('A', 'POST', `/tasks/${task.id}/retry`, { reason: 'x', confirm_duplicate_billing: false, original_attempt_id: unknown.attempt_id })).code).toBe('VALIDATION_FAILED');
    expect(err(call('A', 'POST', `/tasks/${task.id}/retry`, { reason: 'x', confirm_duplicate_billing: true, original_attempt_id: '5a000000-0000-4000-8000-0000000000ff' })).code).toBe('VALIDATION_FAILED');
    // A retry is an additional run of the class: it needs an approval (synthetic, internal only).
    expect(err(call('A', 'POST', `/tasks/${task.id}/retry`, { reason: 'x', confirm_duplicate_billing: true, original_attempt_id: unknown.attempt_id })).code).toBe('QUOTA_SESSION_EXCEEDED');
    backend.setRetranscribeDev('A', { classId: ENDED, approve: true });
    const retry = call('A', 'POST', `/tasks/${task.id}/retry`, { reason: 'x', confirm_duplicate_billing: true, original_attempt_id: unknown.attempt_id });
    expect(retry.status).toBe(202);
    backend.setRetranscribeDev('A', { classId: ENDED, approve: true });
    expect(err(call('A', 'POST', `/tasks/${task.id}/retry`, { reason: 'y', confirm_duplicate_billing: true, original_attempt_id: unknown.attempt_id })).code).toBe('VALIDATION_FAILED');
    expect(retry.body).toMatchObject({ kind: 'asr_retranscribe', parent_task_id: task.id, state: 'queued' });
    expect((retry.body as S['Task']).attempt_id).not.toBe(unknown.attempt_id);
    expect(call('A', 'GET', `/tasks/${task.id}`).body).toMatchObject({ state: 'unknown', attempt_id: unknown.attempt_id, may_have_charged: true });
  });

  it('adopting a transcript version is a CAS write on the classroom; switching back is the same operation', () => {
    const { backend, call } = harness();
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe()).body as S['GenerationPlan'];
    const task = call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan)).body as S['Task'];
    const versionId = backend.advanceTask('A', task.id, 'succeeded')!.result_id!;
    const session = call('A', 'GET', `/class-sessions/${ENDED}`).body as S['ClassSession'];
    expect(err(call('A', 'POST', `/class-sessions/${ENDED}/adopt-transcript`, { base_revision: session.revision - 1, transcript_version_id: versionId })).code).toBe('REVISION_CONFLICT');
    const adopted = call('A', 'POST', `/class-sessions/${ENDED}/adopt-transcript`, { base_revision: session.revision, transcript_version_id: versionId }).body as S['ClassSession'];
    expect(adopted).toMatchObject({ adopted_transcript_version_id: versionId, revision: session.revision + 1 });
    const flags = () => Object.fromEntries(items<S['TranscriptVersion']>(call('A', 'GET', `/class-sessions/${ENDED}/transcript-versions`)).map(item => [item.kind, item.adopted]));
    expect(flags()).toEqual({ realtime: false, retranscription: true });
    const original = items<S['TranscriptVersion']>(call('A', 'GET', `/class-sessions/${ENDED}/transcript-versions`)).find(item => item.kind === 'realtime')!;
    call('A', 'POST', `/class-sessions/${ENDED}/adopt-transcript`, { base_revision: adopted.revision, transcript_version_id: original.id });
    expect(flags()).toEqual({ realtime: true, retranscription: false });
    expect(err(call('A', 'POST', `/class-sessions/${ENDED}/adopt-transcript`, { base_revision: adopted.revision + 1, transcript_version_id: '5a000000-0000-4000-8000-0000000000aa' })).code).toBe('VALIDATION_FAILED');
    expect(err(call('B', 'POST', `/class-sessions/${ENDED}/adopt-transcript`, { base_revision: 1, transcript_version_id: versionId })).code).toBe('NOT_FOUND');
  });
});

describe('PR-10b mock: Astra r1 findings', () => {
  const EPOCH = '74000000-0000-4000-8000-000000000001';
  it('a window inside the missing tail freezes nothing and is refused even with partial consent', () => {
    const { call } = harness();
    // 300 chunks over 100 minutes (20 s each); chunks 241–299 (from 4 820 s) were never confirmed.
    expect(err(call('A', 'POST', `/class-sessions/${UPLOAD_FAILED}/generation-plans`, retranscribe({ scope: 'range', start_ms: 5_000_000, end_ms: 5_100_000, accept_partial_scope: true }))).code).toBe('SOURCE_UNAVAILABLE');
    const head = call('A', 'POST', `/class-sessions/${UPLOAD_FAILED}/generation-plans`, retranscribe({ scope: 'range', start_ms: 0, end_ms: 40_000 })).body as S['GenerationPlan'];
    expect(head.audio_chunk_ids).toHaveLength(2);
  });
  it('revoked frozen audio is refused at execution even when the original selection is confirmed', () => {
    const { backend, call } = harness();
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe()).body as S['GenerationPlan'];
    backend.revokeAudio('A', ENDED, EPOCH);
    expect(err(call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan, { confirm_original_snapshot: true }))).code).toBe('AUDIO_DELETED');
  });
  it('a result completed after the audio was revoked is not published; the charge stays recorded within the confirmed maximum', () => {
    const { backend, call } = harness();
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe()).body as S['GenerationPlan'];
    const task = call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan)).body as S['Task'];
    const versions = items(call('A', 'GET', `/class-sessions/${ENDED}/transcript-versions`)).length;
    backend.revokeAudio('A', ENDED, EPOCH);
    const done = backend.advanceTask('A', task.id, 'succeeded')!;
    expect(done).toMatchObject({ state: 'failed', provider_outcome: 'succeeded', error_code: 'AUDIO_DELETED', result_id: null, may_have_charged: true });
    expect(BigInt(done.settled_cost_microyuan!)).toBeLessThanOrEqual(BigInt(plan.maximum_cost_microyuan));
    expect(items(call('A', 'GET', `/class-sessions/${ENDED}/transcript-versions`))).toHaveLength(versions);
  });
  it('the monthly bucket reserves the frozen units; an approval is bound to its plan', () => {
    const { backend, call } = harness();
    const bucket = () => (call('A', 'GET', '/me/quota').body as S['QuotaView']).buckets.find(item => item.kind === 'retranscribe_month_ms')!;
    const before = bucket().remaining;
    const first = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe({ scope: 'range', start_ms: 0, end_ms: 20_000 })).body as S['GenerationPlan'];
    const task = call('A', 'POST', `/generation-plans/${first.id}/execute`, execute(first)).body as S['Task'];
    expect(bucket()).toMatchObject({ remaining: before - first.quota_units, reserved: first.quota_units });
    expect(backend.advanceTask('A', task.id, 'succeeded')!.settled_cost_microyuan).toBe(first.maximum_cost_microyuan);
    const approved = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe({ scope: 'range', start_ms: 0, end_ms: 20_000 })).body as S['GenerationPlan'];
    const other = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe()).body as S['GenerationPlan'];
    backend.setRetranscribeDev('A', { approvePlan: approved.id });
    expect(err(call('A', 'POST', `/generation-plans/${other.id}/execute`, execute(other))).code).toBe('QUOTA_SESSION_EXCEEDED');
    expect(call('A', 'POST', `/generation-plans/${approved.id}/execute`, execute(approved)).status).toBe(202);
  });
  it('a finished realtime stream is sealed and can be switched back to; adopting another transcript marks summaries as changed', () => {
    const { backend, call } = harness();
    const created = call('A', 'POST', '/class-sessions', { course_id: mockIds.la, title: '封存检查', language: 'auto' }).body as S['ClassSession'];
    backend.persistFinal('A', { classId: created.id, streamId: '5e000000-0000-4000-8000-000000000011', segmentId: '5e000000-0000-4000-8000-000000000012', segmentRevisionId: '5e000000-0000-4000-8000-000000000013', versionId: '5e000000-0000-4000-8000-000000000014', text: '合成', startMs: 0, endMs: 4000, revision: 1 });
    expect(items<S['TranscriptVersion']>(call('A', 'GET', `/class-sessions/${created.id}/transcript-versions`))[0].state).toBe('open');
    backend.finishAsr('A', created.id, 'succeeded');
    expect(items<S['TranscriptVersion']>(call('A', 'GET', `/class-sessions/${created.id}/transcript-versions`))[0].state).toBe('sealed');
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe()).body as S['GenerationPlan'];
    const task = call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan)).body as S['Task'];
    const versionId = backend.advanceTask('A', task.id, 'succeeded')!.result_id!;
    const session = call('A', 'GET', `/class-sessions/${ENDED}`).body as S['ClassSession'];
    call('A', 'POST', `/class-sessions/${ENDED}/adopt-transcript`, { base_revision: session.revision, transcript_version_id: versionId });
    expect(call('A', 'GET', `/review-documents/${SUMMARY}`).body).toMatchObject({ source_status: 'changed' });
  });
  it('an exact-revision read is served only for the current revision', () => {
    const { call } = harness();
    expect(call('A', 'GET', `/review-documents/${SUMMARY}`, undefined, 'revision=1').status).toBe(200);
    expect(err(call('A', 'GET', `/review-documents/${SUMMARY}`, undefined, 'revision=2'))).toMatchObject({ code: 'REVISION_CONFLICT', current_revision: 1 });
  });
});

describe('PR-10b mock: Astra r2 findings', () => {
  const EPOCH = '74000000-0000-4000-8000-000000000001';
  it('a window reaching past the recording needs partial consent at prepare and execute', () => {
    const { call } = harness();
    // The seeded class records 95 minutes (5 700 s).
    expect(err(call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe({ scope: 'range', start_ms: 5_690_000, end_ms: 5_800_000 }))).code).toBe('SOURCE_UNAVAILABLE');
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe({ scope: 'range', start_ms: 5_690_000, end_ms: 5_800_000, accept_partial_scope: true })).body as S['GenerationPlan'];
    expect(err(call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan))).code).toBe('VALIDATION_FAILED');
    expect(call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan, { confirm_partial_scope: true })).status).toBe(202);
  });
  it('the panel shortcut issues exactly one approval', () => {
    const { backend, call } = harness();
    const first = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe({ scope: 'range', start_ms: 0, end_ms: 20_000 })).body as S['GenerationPlan'];
    const task = call('A', 'POST', `/generation-plans/${first.id}/execute`, execute(first)).body as S['Task'];
    backend.advanceTask('A', task.id, 'unknown');
    const second = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe({ scope: 'range', start_ms: 0, end_ms: 20_000 })).body as S['GenerationPlan'];
    expect(backend.setRetranscribeDev('A', { classId: ENDED, approve: true }).approvals).toEqual([`plan:${second.id}`]);
    expect(call('A', 'POST', `/generation-plans/${second.id}/execute`, execute(second)).status).toBe(202);
    expect(err(call('A', 'POST', `/tasks/${task.id}/retry`, { reason: 'x', confirm_duplicate_billing: true, original_attempt_id: task.attempt_id })).code).toBe('QUOTA_SESSION_EXCEEDED');
  });
  it('a candidate is not published after its classroom was deleted, but is published after natural audio expiry', () => {
    const { backend, call } = harness();
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe()).body as S['GenerationPlan'];
    const task = call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan)).body as S['Task'];
    backend.revokeClassroom('A', ENDED);
    expect(backend.advanceTask('A', task.id, 'succeeded')).toMatchObject({ state: 'failed', provider_outcome: 'succeeded', error_code: 'AUDIO_DELETED', result_id: null });
    expect(backend.classroomStore('A').versions.some(item => item.source_task_id === task.id)).toBe(false);
    const other = harness();
    const plan2 = other.call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe()).body as S['GenerationPlan'];
    const task2 = other.call('A', 'POST', `/generation-plans/${plan2.id}/execute`, execute(plan2)).body as S['Task'];
    // The epoch's own deadline passes after the authorised submission.
    other.backend.classroomStore('A').epochOrder.set(EPOCH, { ...(other.backend.classroomStore('A').epochOrder.get(EPOCH) ?? { offset: 0, fence: 1, samples: 0 }), expires: '2026-10-01T00:00:00Z' });
    expect(other.backend.advanceTask('A', task2.id, 'succeeded')).toMatchObject({ state: 'succeeded' });
  });
  it('a matching approval admits a run even in the forced session-quota scenario', () => {
    const { backend, call } = harness();
    backend.setRetranscribeDev('A', { quota: 'session' });
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe()).body as S['GenerationPlan'];
    expect(err(call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan))).code).toBe('QUOTA_SESSION_EXCEEDED');
    backend.setRetranscribeDev('A', { approvePlan: plan.id });
    expect(call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan)).status).toBe(202);
  });
});

describe('PR-10b mock: third-round review findings', () => {
  it('an unsealed capture epoch is never frozen as complete audio', () => {
    const { backend, call } = harness();
    const manifest = backend.classroomStore('A').manifests.get(ENDED)!;
    backend.classroomStore('A').manifests.set(ENDED, { ...manifest, sealed: false });
    expect(err(call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe())).code).toBe('SOURCE_UNAVAILABLE');
    expect(err(call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe({ accept_partial_scope: true }))).code).toBe('SOURCE_UNAVAILABLE');
  });
  it('an AI plan frozen before a transcript adoption needs a new plan or an explicit original-snapshot confirmation', () => {
    const { backend, call } = harness();
    const summary = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, { kind: 'review_summary', scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: false }).body as S['GenerationPlan'];
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe()).body as S['GenerationPlan'];
    const task = call('A', 'POST', `/generation-plans/${plan.id}/execute`, execute(plan)).body as S['Task'];
    const versionId = backend.advanceTask('A', task.id, 'succeeded')!.result_id!;
    const session = call('A', 'GET', `/class-sessions/${ENDED}`).body as S['ClassSession'];
    call('A', 'POST', `/class-sessions/${ENDED}/adopt-transcript`, { base_revision: session.revision, transcript_version_id: versionId });
    expect(call('A', 'GET', `/generation-plans/${summary.id}`).body).toMatchObject({ source_status: 'changed' });
    expect(err(call('A', 'POST', `/generation-plans/${summary.id}/execute`, execute(summary))).code).toBe('SOURCE_CHANGED');
    expect(call('A', 'POST', `/generation-plans/${summary.id}/execute`, execute(summary, { confirm_original_snapshot: true })).status).toBe(202);
  });
});
