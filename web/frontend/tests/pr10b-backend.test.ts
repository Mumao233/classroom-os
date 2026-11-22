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

  it('adoption is explicit and CAS-checked; the previously adopted AI revision returns to the candidates', () => {
    const { call } = harness();
    expect(err(call('A', 'POST', `/review-documents/${SUMMARY}/adopt`, { base_revision: 9, revision_id: CANDIDATE })).code).toBe('REVISION_CONFLICT');
    expect(err(call('A', 'POST', `/review-documents/${SUMMARY}/adopt`, { base_revision: 1, revision_id: 'e1000000-0000-4000-8000-000000000003' })).code).toBe('VALIDATION_FAILED');
    const adopted = call('A', 'POST', `/review-documents/${SUMMARY}/adopt`, { base_revision: 1, revision_id: CANDIDATE }).body as S['ReviewDocument'];
    expect(adopted).toMatchObject({ adopted_revision_id: CANDIDATE, candidate_revision_ids: [ADOPTED], revision: 2 });
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

describe('PR-10b mock retranscription (PR-06c handoff)', () => {
  it('prepare freezes the audio manifest; execute queues a task; success yields an unadopted retranscription version', () => {
    const { backend, call } = harness();
    expect(err(call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe({ scope: 'recent30' }))).code).toBe('VALIDATION_FAILED');
    expect(err(call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe({ include_images: true }))).code).toBe('VALIDATION_FAILED');
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, retranscribe({ scope: 'range', start_ms: 0, end_ms: 30_000 })).body as S['GenerationPlan'];
    expect(plan).toMatchObject({ kind: 'asr_retranscribe', source_snapshot: null, quota_units: 30_000, contains_images: false });
    expect(plan.audio_manifest_sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(plan.audio_chunk_ids.length).toBeGreaterThan(0);
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
    const retry = call('A', 'POST', `/tasks/${task.id}/retry`, { reason: 'x', confirm_duplicate_billing: true, original_attempt_id: unknown.attempt_id });
    expect(retry.status).toBe(202);
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
