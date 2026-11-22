import { describe, expect, it, vi } from 'vitest';
import { ApiClientError, createApiClient, type ApiClient } from '../src/api/client';
import { commandCapture, confirmStop, reconcileCommand, startCapture, stopCapture, type CaptureBinding, type ClassroomShell, type PendingCommand, type PendingStop } from '../src/features/classroom';
import { sha256Hex } from '../src/mock/sha256';
import type { components } from '../src/generated/openapi';
import { createMockBackend, mockIds, type MockAccountKey } from '../src/mock/backend';
import { MOCK_DESTINATION } from '../src/mock/classroom-backend';

type S = components['schemas'];
const q = (value = '') => new URLSearchParams(value);
const ENDED = 'c0000000-0000-4000-8000-000000000001';
const UNKNOWN_AI = 'c0000000-0000-4000-8000-000000000006';
const SHA = 'a'.repeat(64);
function harness() {
  const backend = createMockBackend();
  let key = 0;
  const call = (account: MockAccountKey, method: string, path: string, body?: unknown, query = '', idempotencyKey?: string) =>
    backend.handle(account, method, path, q(query), body, idempotencyKey ?? (method === 'GET' ? null : `00000000-0000-4000-8000-${String(++key).padStart(12, '0')}`))!;
  const acquire = { device_id: 'a2000000-0000-4000-8000-000000000001', microphone_consent_version: 'm', recovery_consent_version: 'r', asr_consent_version: 'a', confirm_takeover: false, expected_lease_id: null };
  const newClass = (account: MockAccountKey = 'A', course: string = mockIds.la) => call(account, 'POST', '/class-sessions', { course_id: course, title: '第 6 讲', language: 'zh' }).body as S['ClassSession'];
  const start = (classId: string) => call('A', 'POST', `/class-sessions/${classId}/capture/acquire`, acquire).body as S['CaptureLease'];
  const stopBody = (lease: S['CaptureLease'], lastSeq = 3) => ({ capture_epoch_id: lease.capture_epoch_id, fence_token: lease.fence_token, last_local_seq: lastSeq, last_sample: lastSeq * 16000, client_monotonic_ms: 1, manifest_sha256: SHA, known_gaps: [] });
  return { backend, call, newClass, start, stopBody, acquire };
}
const err = (reply: { body: unknown }) => (reply.body as S['Error']).error;
const items = <T>(reply: { body: unknown }) => (reply.body as { items: T[] }).items;

describe('PR-10a mock classroom lifecycle', () => {
  it('creating a classroom never starts capture; acquiring a lease is a separate, explicit step', () => {
    const { call, newClass, start } = harness();
    const created = newClass();
    expect(created).toMatchObject({ started_at: null, capture_ended_at: null, flows: { capture: 'idle', upload: 'pending', asr: 'idle', ai: 'idle', sync: 'synced' } });
    expect((call('A', 'GET', `/courses/${mockIds.la}`).body as S['Course']).class_session_count).toBe(4);
    const lease = start(created.id);
    expect(lease).toMatchObject({ class_session_id: created.id, fence_token: 1, sample_rate: 16000, encoding: 'pcm_s16le' });
    expect(call('A', 'GET', `/class-sessions/${created.id}`).body).toMatchObject({ flows: { capture: 'recording' } });
  });

  it('pause with a stale fence is rejected; stop seals a manifest from durable receipts and is idempotent', () => {
    const { backend, call, newClass, start, stopBody } = harness();
    const created = newClass();
    const lease = start(created.id);
    const stale = call('A', 'POST', `/class-sessions/${created.id}/capture/pause`, { capture_epoch_id: lease.capture_epoch_id, fence_token: lease.fence_token + 1, last_local_seq: 0, last_sample: 0, client_monotonic_ms: 0 });
    expect(err(stale).code).toBe('STALE_FENCE');
    backend.setDurable('A', created.id, lease.capture_epoch_id, [0, 1]);
    const key = '11111111-1111-4111-8111-111111111111';
    const first = call('A', 'POST', `/class-sessions/${created.id}/capture/stop`, stopBody(lease), '', key);
    expect(first.body).toMatchObject({ sealed: true, expected_last_seq: 3, durable_ranges: [{ first_seq: 0, last_seq: 1 }], missing_ranges: [{ first_seq: 2, last_seq: 3 }] });
    expect(call('A', 'POST', `/class-sessions/${created.id}/capture/stop`, stopBody(lease), '', key).body).toEqual(first.body);
    const ended = call('A', 'GET', `/class-sessions/${created.id}`).body as S['ClassSession'];
    expect(ended.capture_ended_at).not.toBeNull();
    expect(ended.flows).toMatchObject({ capture: 'ended', upload: 'pending' });
    expect(err(call('A', 'POST', `/class-sessions/${created.id}/capture/acquire`, harness().acquire)).code).toBe('LEASE_CONFLICT');
  });

  it('marks accept a time point without a final and reject an invented anchor', () => {
    const { call, newClass, start } = harness();
    const created = newClass();
    const lease = start(created.id);
    const mark = call('A', 'POST', `/class-sessions/${created.id}/marks`, { kind: 'unclear', anchor: { kind: 'timepoint', capture_epoch_id: lease.capture_epoch_id, sample_offset: 32000 }, note: '' });
    expect(mark.status).toBe(201);
    expect(err(call('A', 'POST', `/class-sessions/${created.id}/marks`, { kind: 'unclear', anchor: { kind: 'guess', at: 1 }, note: '' })).code).toBe('VALIDATION_FAILED');
    expect(items<S['Mark']>(call('A', 'GET', `/class-sessions/${created.id}/marks`))).toHaveLength(1);
  });

  it('slides go through upload → content → verified asset; every change is a new version', () => {
    const { call } = harness();
    const upload = call('A', 'POST', '/media/uploads', { purpose: 'slide', class_session_id: ENDED, mime_type: 'image/png', byte_length: 10, sha256: SHA, filename: 'board.png' }).body as S['MediaUpload'];
    expect(err(call('A', 'POST', `/media/uploads/${upload.id}/complete`, { sha256: SHA, byte_length: 10 })).code).toBe('STORAGE_UNAVAILABLE');
    call('A', 'PUT', `/media/uploads/${upload.id}/content`, { byte_length: 10, sha256: SHA, signature: '89504e470d0a1a0a0000000d49484452' });
    expect(err(call('A', 'POST', `/media/uploads/${upload.id}/complete`, { sha256: 'b'.repeat(64), byte_length: 10 })).code).toBe('VALIDATION_FAILED');
    const asset = call('A', 'POST', `/media/uploads/${upload.id}/complete`, { sha256: SHA, byte_length: 10 }).body as S['MediaAsset'];
    const slide = call('A', 'POST', `/class-sessions/${ENDED}/slides`, { asset_id: asset.id, title: '板书', start_ms: 1000, end_ms: null }).body as S['Slide'];
    const rotated = call('A', 'PATCH', `/slides/${slide.id}`, { base_revision: 1, rotate: 'right' }).body as S['Slide'];
    expect(rotated).toMatchObject({ id: slide.id, revision: 2, rotation: '90' });
    expect(rotated.version_id).not.toBe(slide.version_id);
    expect(err(call('A', 'PATCH', `/slides/${slide.id}`, { base_revision: 1, removed: true })).code).toBe('REVISION_CONFLICT');
    expect(err(call('A', 'POST', '/media/uploads', { purpose: 'slide', class_session_id: ENDED, mime_type: 'image/gif', byte_length: 10, sha256: SHA, filename: 'x.gif' })).code).toBe('VALIDATION_FAILED');
  });
});

describe('PR-10a generation plans and tasks', () => {
  const plan = (over: Partial<S['GenerationPlanRequest']> = {}) => ({ kind: 'review_summary', scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: false, ...over });
  const execute = (value: S['GenerationPlan'], over: Partial<S['ExecutePlan']> = {}) => ({ plan_sha256: value.plan_sha256, confirmed_destination_revision: value.destination.configuration_revision, confirm_send: true, confirm_images: false, confirm_original_snapshot: false, confirm_partial_scope: false, ...over });

  it('no formal transcript means nothing to send', () => {
    const { call, newClass } = harness();
    expect(err(call('A', 'POST', `/class-sessions/${newClass().id}/generation-plans`, plan({ kind: 'ai_explanation', scope: 'recent30' }))).code).toBe('SOURCE_UNAVAILABLE');
  });

  it('incomplete material needs an explicit partial choice at plan and at execution; replays reuse the task', () => {
    const { backend, call, newClass, start, stopBody } = harness();
    const created = newClass();
    const lease = start(created.id);
    backend.persistFinal('A', { classId: created.id, streamId: '5e000000-0000-4000-8000-000000000001', segmentId: '5e000000-0000-4000-8000-000000000002', segmentRevisionId: '5e000000-0000-4000-8000-000000000003', versionId: '5e000000-0000-4000-8000-000000000004', text: '合成', startMs: 0, endMs: 5000, revision: 1 });
    call('A', 'POST', `/class-sessions/${created.id}/capture/stop`, stopBody(lease, 0));
    const refused = call('A', 'POST', `/class-sessions/${created.id}/generation-plans`, plan());
    expect(err(refused).code).toBe('SOURCE_UNAVAILABLE');
    expect(err(refused).message).toContain('部分范围');
    const partial = call('A', 'POST', `/class-sessions/${created.id}/generation-plans`, plan({ accept_partial_scope: true })).body as S['GenerationPlan'];
    expect(partial.source_snapshot).toMatchObject({ scope_complete: false });
    expect(partial.source_snapshot!.missing_reasons.length).toBeGreaterThan(0);
    expect(partial.destination).toEqual(MOCK_DESTINATION);
    expect(err(call('A', 'POST', `/generation-plans/${partial.id}/execute`, execute(partial))).code).toBe('VALIDATION_FAILED');
    const task = call('A', 'POST', `/generation-plans/${partial.id}/execute`, execute(partial, { confirm_partial_scope: true }));
    expect(task.status).toBe(202);
    expect(task.body).toMatchObject({ kind: 'review_summary', state: 'queued', provider_outcome: 'not_submitted', may_have_charged: false });
    const replay = call('A', 'POST', `/generation-plans/${partial.id}/execute`, execute(partial, { confirm_partial_scope: true }));
    expect((replay.body as S['Task']).id).toBe((task.body as S['Task']).id);
    expect(items<S['Task']>(call('A', 'GET', '/tasks', undefined, `class_session_id=${created.id}`))).toHaveLength(1);
    // A succeeded summary is only a candidate; nothing is adopted automatically.
    backend.advanceTask('A', (task.body as S['Task']).id, 'succeeded');
    const document = items<S['ReviewDocument']>(call('A', 'GET', '/review-documents')).find(item => item.class_session_id === created.id)!;
    expect(document).toMatchObject({ kind: 'summary', adopted_revision_id: null, title: '课堂总结（部分范围候选）' });
    expect(document.candidate_revision_ids).toHaveLength(1);
  });

  it('a question plan creates its Question only on execution; an unknown task is reconciled without a new submission', () => {
    const { backend, call } = harness();
    const prepared = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, plan({ kind: 'ai_question', scope: 'recent120', question: '为什么？' })).body as S['GenerationPlan'];
    const before = items<S['Question']>(call('A', 'GET', '/questions', undefined, `class_session_id=${ENDED}`)).length;
    const task = call('A', 'POST', `/generation-plans/${prepared.id}/execute`, execute(prepared)).body as S['Task'];
    expect(items<S['Question']>(call('A', 'GET', '/questions', undefined, `class_session_id=${ENDED}`))).toHaveLength(before + 1);
    backend.advanceTask('A', task.id, 'unknown');
    const reconcile = call('A', 'POST', `/tasks/${task.id}/reconcile`, {});
    expect(reconcile.body).toMatchObject({ state: 'unknown', new_submission: false });
    expect(items<S['Task']>(call('A', 'GET', '/tasks', undefined, `class_session_id=${ENDED}`)).filter(item => item.kind === 'ai_question')).toHaveLength(1);
    expect(call('A', 'GET', `/class-sessions/${ENDED}`).body).toMatchObject({ flows: { ai: 'unknown' } });
  });

  it('seeded unknown task stays unknown; quota, retranscription and accounts are enforced', () => {
    const { call } = harness();
    expect(items<S['Task']>(call('A', 'GET', '/tasks', undefined, `class_session_id=${UNKNOWN_AI}`))[0]).toMatchObject({ state: 'unknown', may_have_charged: true });
    expect(err(call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, plan({ kind: 'asr_retranscribe' }))).code).toBe('SERVICE_DISABLED');
    expect(err(call('B', 'GET', `/class-sessions/${ENDED}`)).code).toBe('NOT_FOUND');
    expect(items<S['Task']>(call('B', 'GET', '/tasks', undefined, `class_session_id=${UNKNOWN_AI}`))).toHaveLength(0);
    expect(err(call('B', 'POST', `/class-sessions/${ENDED}/generation-plans`, plan({ kind: 'ai_question', scope: 'all', question: 'x' }))).code).toBe('NOT_FOUND');
  });

  it('an expired or changed plan is refused', () => {
    let clock = '2026-10-28T08:00:00Z';
    const backend = createMockBackend({ now: () => clock });
    let n = 0;
    const call = (method: string, path: string, body?: unknown) => backend.handle('A', method, path, q(), body, `33333333-3333-4333-8333-${String(++n).padStart(12, '0')}`)!;
    const prepared = call('POST', `/class-sessions/${ENDED}/generation-plans`, plan({ kind: 'ai_explanation', scope: 'recent120' })).body as S['GenerationPlan'];
    expect(prepared.expires_at).toBe('2026-10-28T08:10:00Z');
    expect(err(call('POST', `/generation-plans/${prepared.id}/execute`, execute(prepared, { plan_sha256: 'f'.repeat(64) }))).code).toBe('PLAN_EXPIRED');
    expect(err(call('POST', `/generation-plans/${prepared.id}/execute`, execute(prepared, { confirm_send: false as unknown as true }))).code).toBe('VALIDATION_FAILED');
    clock = '2026-10-28T08:10:00Z';
    expect(err(call('POST', `/generation-plans/${prepared.id}/execute`, execute(prepared))).code).toBe('PLAN_EXPIRED');
  });
});

describe('PR-10a review round 0 regressions', () => {
  it('durable receipts after the stop update the sealed manifest and the upload flow', () => {
    const { backend, call, newClass, start, stopBody } = harness();
    const created = newClass();
    const lease = start(created.id);
    backend.setDurable('A', created.id, lease.capture_epoch_id, [0]);
    const sealed = call('A', 'POST', `/class-sessions/${created.id}/capture/stop`, stopBody(lease, 1)).body as S['AudioManifest'];
    expect(sealed.missing_ranges).toEqual([{ first_seq: 1, last_seq: 1 }]);
    backend.setDurable('A', created.id, lease.capture_epoch_id, [1]);
    expect(call('A', 'GET', `/class-sessions/${created.id}/audio-manifest`).body).toMatchObject({ durable_ranges: [{ first_seq: 0, last_seq: 1 }], missing_ranges: [] });
    expect((call('A', 'GET', `/class-sessions/${created.id}`).body as S['ClassSession']).flows.upload).toBe('durable');
  });

  it('a slide-scoped plan needs slides and never falls back to the transcript', () => {
    const { call } = harness();
    const request = { kind: 'ai_explanation', scope: 'slides', seen_sources: [], include_images: true, accept_partial_scope: false };
    const noSlides = 'c0000000-0000-4000-8000-000000000004';
    expect(err(call('A', 'POST', `/class-sessions/${noSlides}/generation-plans`, request)).code).toBe('SOURCE_UNAVAILABLE');
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, request).body as S['GenerationPlan'];
    expect(plan.source_snapshot!.source_refs.length).toBeGreaterThan(0);
    expect(plan.source_snapshot!.source_refs.every(ref => ref.kind === 'slide_version')).toBe(true);
    expect(err(call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, { ...request, include_images: false })).code).toBe('VALIDATION_FAILED');
  });

  it('media content without an Idempotency-Key is rejected like the media service', () => {
    const { backend, call } = harness();
    const upload = call('A', 'POST', '/media/uploads', { purpose: 'slide', class_session_id: ENDED, mime_type: 'image/png', byte_length: 1, sha256: SHA, filename: 'a.png' }).body as S['MediaUpload'];
    const path = upload.upload_path.replace('/api/web/v1', '');
    const bytes = { byte_length: 1, sha256: SHA, signature: '89504e470d0a1a0a0000000d49484452' };
    expect(backend.handle('A', 'PUT', path, q(), bytes, null)!.status).toBe(422);
    expect(call('A', 'PUT', path, { ...bytes, sha256: 'b'.repeat(64) }).status).toBe(422);
    expect(call('A', 'PUT', path, { ...bytes, signature: 'ffd8ff' + '0'.repeat(26) }).status).toBe(422);
    expect(call('A', 'PUT', path, bytes).status).toBe(200);
  });

  it('a revoked classroom is no longer served and cannot be re-leased as the demo classroom', () => {
    const { backend, call } = harness();
    expect(backend.revokeClassroom('A', ENDED)).toBe(true);
    expect(err(call('A', 'GET', `/class-sessions/${ENDED}`)).code).toBe('NOT_FOUND');
    expect(err(call('A', 'GET', `/class-sessions/${ENDED}/marks`)).code).toBe('NOT_FOUND');
  });
});

describe('PR-10a API client follows the frozen contract', () => {
  const scope = { epoch: 1, isCurrent: () => true };
  it('list pages request at most 100 items and follow next_cursor', async () => {
    const urls: string[] = [];
    const transport = vi.fn(async (url: string | URL | Request) => {
      urls.push(String(url));
      const second = String(url).includes('cursor=');
      return new Response(JSON.stringify({ items: [{ id: second ? 'b' : 'a' }], next_cursor: second ? null : 'next' }), { status: 200 });
    });
    const client = createApiClient(scope, transport as typeof fetch);
    expect((await client.marks(ENDED)).items).toEqual([{ id: 'a' }, { id: 'b' }]);
    expect(urls).toEqual([`/api/web/v1/class-sessions/${ENDED}/marks?limit=100`, `/api/web/v1/class-sessions/${ENDED}/marks?limit=100&cursor=next`]);
  });

  it('media content is sent as octet-stream with an Idempotency-Key', async () => {
    const transport = vi.fn(async () => new Response(JSON.stringify({}), { status: 200 }));
    const client = createApiClient(scope, transport as typeof fetch);
    const upload = { id: 'u', asset_id: 'a', upload_path: '/api/web/v1/media/uploads/u/content', state: 'staging', expires_at: '2026-11-21T00:00:00Z' } as S['MediaUpload'];
    await client.putMediaContent(upload, new Uint8Array([1]), 'AQ==', 'key-1');
    expect(transport).toHaveBeenCalledWith(upload.upload_path, expect.objectContaining({ method: 'PUT', headers: expect.objectContaining({ 'Content-Type': 'application/octet-stream', 'Idempotency-Key': 'key-1' }) }));
  });
});

describe('PR-10a review round 1 regressions', () => {
  it('resume retires the paused epoch: a new epoch and fence are issued and the old fence is stale', () => {
    const { call, newClass, start } = harness();
    const created = newClass();
    const lease = start(created.id);
    const body = { capture_epoch_id: lease.capture_epoch_id, fence_token: lease.fence_token, last_local_seq: 0, last_sample: 16000, client_monotonic_ms: 1 };
    expect(call('A', 'POST', `/class-sessions/${created.id}/capture/pause`, body).status).toBe(200);
    const resumed = call('A', 'POST', `/class-sessions/${created.id}/capture/resume`, body).body as S['CaptureLease'];
    expect(resumed.capture_epoch_id).not.toBe(lease.capture_epoch_id);
    expect(resumed.fence_token).toBe(lease.fence_token + 1);
    expect(err(call('A', 'POST', `/class-sessions/${created.id}/capture/pause`, body)).code).toBe('STALE_FENCE');
    const stop = { capture_epoch_id: resumed.capture_epoch_id, fence_token: resumed.fence_token, last_local_seq: 0, last_sample: 32000, client_monotonic_ms: 2, manifest_sha256: SHA, known_gaps: [] };
    expect(call('A', 'POST', `/class-sessions/${created.id}/capture/stop`, stop).body).toMatchObject({ capture_epoch_id: resumed.capture_epoch_id, expected_last_seq: 0 });
    // Effective duration is counted from samples of both epochs (16 kHz), not from wall time.
    expect((call('A', 'GET', `/class-sessions/${created.id}`).body as S['ClassSession']).effective_capture_ms).toBe(3000);
  });

  it('receipts of an earlier epoch never clear the current manifest', () => {
    const { backend, call, newClass, start } = harness();
    const created = newClass();
    const lease = start(created.id);
    const body = { capture_epoch_id: lease.capture_epoch_id, fence_token: lease.fence_token, last_local_seq: 0, last_sample: 16000, client_monotonic_ms: 1 };
    call('A', 'POST', `/class-sessions/${created.id}/capture/pause`, body);
    const resumed = call('A', 'POST', `/class-sessions/${created.id}/capture/resume`, body).body as S['CaptureLease'];
    backend.setDurable('A', created.id, lease.capture_epoch_id, [0]);
    const sealed = call('A', 'POST', `/class-sessions/${created.id}/capture/stop`, { capture_epoch_id: resumed.capture_epoch_id, fence_token: resumed.fence_token, last_local_seq: 0, last_sample: 16000, client_monotonic_ms: 2, manifest_sha256: SHA, known_gaps: [] }).body as S['AudioManifest'];
    expect(sealed.missing_ranges).toEqual([{ first_seq: 0, last_seq: 0 }]);
    backend.setDurable('A', created.id, lease.capture_epoch_id, [0]);
    expect((call('A', 'GET', `/class-sessions/${created.id}/audio-manifest`).body as S['AudioManifest']).missing_ranges).toEqual([{ first_seq: 0, last_seq: 0 }]);
  });

  it('only one capture lease per account unless takeover is confirmed against the current lease', () => {
    const { call, newClass, start, acquire } = harness();
    const first = newClass();
    const lease = start(first.id);
    const second = newClass();
    expect(err(call('A', 'POST', `/class-sessions/${second.id}/capture/acquire`, acquire)).code).toBe('LEASE_CONFLICT');
    expect(err(call('A', 'POST', `/class-sessions/${first.id}/capture/acquire`, acquire)).code).toBe('LEASE_CONFLICT');
    const taken = call('A', 'POST', `/class-sessions/${second.id}/capture/acquire`, { ...acquire, confirm_takeover: true, expected_lease_id: lease.id });
    expect(taken.status).toBe(201);
    expect(err(call('A', 'POST', `/class-sessions/${first.id}/capture/pause`, { capture_epoch_id: lease.capture_epoch_id, fence_token: lease.fence_token, last_local_seq: 0, last_sample: 0, client_monotonic_ms: 0 })).code).toBe('STALE_FENCE');
  });

  it('a revoked classroom never replays an earlier receipt', () => {
    const { backend, call } = harness();
    const key = '22222222-2222-4222-8222-000000000001';
    const request = { kind: 'ai_question', scope: 'recent120', question: '为什么？', seen_sources: [], include_images: false, accept_partial_scope: false };
    const prepared = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, request, '', key);
    expect(prepared.status).toBe(201);
    backend.revokeClassroom('A', ENDED);
    expect(err(call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, request, '', key)).code).toBe('NOT_FOUND');
    expect(err(call('A', 'GET', `/generation-plans/${(prepared.body as S['GenerationPlan']).id}`)).code).toBe('NOT_FOUND');
  });

  it('a slide changed after planning needs an explicit original-snapshot choice; a removed one is unavailable', () => {
    const { call } = harness();
    const request = { kind: 'ai_explanation', scope: 'slides', seen_sources: [], include_images: true, accept_partial_scope: false };
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, request).body as S['GenerationPlan'];
    const slide = items<S['Slide']>(call('A', 'GET', `/class-sessions/${ENDED}/slides`))[0]!;
    call('A', 'PATCH', `/slides/${slide.id}`, { base_revision: slide.revision, rotate: 'right' });
    expect((call('A', 'GET', `/generation-plans/${plan.id}`).body as S['GenerationPlan']).source_status).toBe('changed');
    const confirm = { plan_sha256: plan.plan_sha256, confirmed_destination_revision: plan.destination.configuration_revision, confirm_send: true, confirm_images: true, confirm_original_snapshot: false, confirm_partial_scope: false };
    expect(err(call('A', 'POST', `/generation-plans/${plan.id}/execute`, confirm)).code).toBe('SOURCE_CHANGED');
    expect(call('A', 'POST', `/generation-plans/${plan.id}/execute`, { ...confirm, confirm_original_snapshot: true }).status).toBe(202);
    const again = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, request).body as S['GenerationPlan'];
    call('A', 'PATCH', `/slides/${slide.id}`, { base_revision: slide.revision + 1, removed: true });
    expect(err(call('A', 'POST', `/generation-plans/${again.id}/execute`, { ...confirm, plan_sha256: again.plan_sha256, confirm_original_snapshot: true })).code).toBe('SOURCE_UNAVAILABLE');
  });

  it('classroom lists page at the contract limit with a continuation cursor', () => {
    const { call } = harness();
    const first = call('A', 'GET', `/class-sessions/${ENDED}/marks`, undefined, 'limit=1');
    expect(items(first)).toHaveLength(1);
    const cursor = (first.body as { next_cursor: string | null }).next_cursor;
    if (cursor) expect(call('A', 'GET', `/class-sessions/${ENDED}/marks`, undefined, `limit=1&cursor=${cursor}`).status).toBe(200);
    expect(err(call('A', 'GET', `/class-sessions/${ENDED}/marks`, undefined, 'limit=101')).code).toBe('VALIDATION_FAILED');
  });
});

describe('PR-10a review round 2 regressions', () => {
  const pauseBody = (lease: S['CaptureLease'], sample = 16000) => ({ capture_epoch_id: lease.capture_epoch_id, fence_token: lease.fence_token, last_local_seq: 0, last_sample: sample, client_monotonic_ms: 1 });
  const stopOf = (lease: S['CaptureLease'], sample = 16000) => ({ ...pauseBody(lease, sample), client_monotonic_ms: 2, manifest_sha256: SHA, known_gaps: [] });

  it('outstanding chunks of a retired epoch keep upload pending and the summary partial', () => {
    const { backend, call, newClass, start } = harness();
    const created = newClass();
    const first = start(created.id);
    call('A', 'POST', `/class-sessions/${created.id}/capture/pause`, pauseBody(first));
    const second = call('A', 'POST', `/class-sessions/${created.id}/capture/resume`, pauseBody(first)).body as S['CaptureLease'];
    backend.setDurable('A', created.id, second.capture_epoch_id, [0]);
    expect(call('A', 'POST', `/class-sessions/${created.id}/capture/stop`, stopOf(second)).body).toMatchObject({ missing_ranges: [] });
    expect((call('A', 'GET', `/class-sessions/${created.id}`).body as S['ClassSession']).flows.upload).toBe('pending');
    backend.persistFinal('A', { classId: created.id, streamId: '5e000000-0000-4000-8000-000000000011', segmentId: '5e000000-0000-4000-8000-000000000012', segmentRevisionId: '5e000000-0000-4000-8000-000000000013', versionId: '5e000000-0000-4000-8000-000000000014', text: '合成', startMs: 0, endMs: 4000, revision: 1 });
    backend.finishAsr('A', created.id, 'succeeded');
    const refused = err(call('A', 'POST', `/class-sessions/${created.id}/generation-plans`, { kind: 'review_summary', scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: false }));
    expect(refused.code).toBe('SOURCE_UNAVAILABLE');
    expect(refused.message).toContain('1 个分片');
    // The retired epoch's manifest is readable by its epoch ID; its late receipt completes the upload.
    expect(call('A', 'GET', `/class-sessions/${created.id}/audio-manifest`, undefined, `capture_epoch_id=${first.capture_epoch_id}`).body).toMatchObject({ capture_epoch_id: first.capture_epoch_id, missing_ranges: [{ first_seq: 0, last_seq: 0 }] });
    backend.setDurable('A', created.id, first.capture_epoch_id, [0]);
    expect((call('A', 'GET', `/class-sessions/${created.id}`).body as S['ClassSession']).flows.upload).toBe('durable');
    expect(err(call('A', 'GET', `/class-sessions/${created.id}/audio-manifest`, undefined, 'capture_epoch_id=ffffffff-ffff-4fff-8fff-ffffffffffff')).code).toBe('NOT_FOUND');
    expect(err(call('A', 'GET', `/class-sessions/${created.id}/audio-manifest`, undefined, 'capture_epoch_id=x')).code).toBe('VALIDATION_FAILED');
  });

  it('a released lease (account teardown or lease loss) no longer blocks another classroom', () => {
    const { backend, call, newClass, start, acquire } = harness();
    const first = newClass();
    start(first.id);
    const second = newClass();
    expect(err(call('A', 'POST', `/class-sessions/${second.id}/capture/acquire`, acquire)).code).toBe('LEASE_CONFLICT');
    backend.releaseLease('A', first.id);
    expect((call('A', 'GET', `/class-sessions/${first.id}`).body as S['ClassSession']).flows.capture).toBe('failed');
    expect(call('A', 'POST', `/class-sessions/${second.id}/capture/acquire`, acquire).status).toBe(201);
  });

  it('time scopes only freeze wholly contained finals; marked uses marked sources; whole class includes untimed finals', () => {
    const { backend, call } = harness();
    const request = (scope: string) => ({ kind: 'ai_explanation', scope, seen_sources: [], include_images: false, accept_partial_scope: false });
    const recent = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, request('recent30')).body as S['GenerationPlan'];
    const segments = backend.classroomStore('A').segments;
    for (const ref of recent.source_snapshot!.source_refs) {
      const segment = segments.find(item => item.id === ref.id)!;
      expect(segment.start_ms!).toBeGreaterThanOrEqual(recent.source_snapshot!.end_ms - 30_000);
    }
    const marked = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, request('marked')).body as S['GenerationPlan'];
    expect(marked.source_snapshot!.source_refs.map(ref => ref.id)).toEqual(['73000000-0000-4000-8000-000000000003']);
    backend.persistFinal('A', { classId: ENDED, streamId: '5e000000-0000-4000-8000-000000000021', segmentId: '5e000000-0000-4000-8000-000000000022', segmentRevisionId: '5e000000-0000-4000-8000-000000000023', versionId: '71000000-0000-4000-8000-000000000001', text: '无时间', startMs: null, endMs: null, revision: 1 });
    const all = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, request('all')).body as S['GenerationPlan'];
    expect(all.source_snapshot!.source_refs.some(ref => ref.id === '5e000000-0000-4000-8000-000000000022')).toBe(true);
  });

  it('synthetic quota units are reserved once, on the first execution', () => {
    const { call } = harness();
    const remaining = () => (call('A', 'GET', '/me/quota').body as S['QuotaView']).buckets.find(item => item.kind === 'interactive_month_count')!.remaining;
    const before = remaining();
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, { kind: 'ai_question', scope: 'recent120', question: '为什么？', seen_sources: [], include_images: false, accept_partial_scope: false }).body as S['GenerationPlan'];
    const confirm = { plan_sha256: plan.plan_sha256, confirmed_destination_revision: plan.destination.configuration_revision, confirm_send: true, confirm_images: false, confirm_original_snapshot: false, confirm_partial_scope: false };
    call('A', 'POST', `/generation-plans/${plan.id}/execute`, confirm);
    call('A', 'POST', `/generation-plans/${plan.id}/execute`, confirm);
    expect(remaining()).toBe(before - 1);
  });

  it('mark anchors resolve inside the target classroom, including review revisions', () => {
    const { backend, call, newClass, start } = harness();
    const digest = sha256Hex(backend.classroomStore('A').segments.find(item => item.segment_revision_id === '73100000-0000-4000-8000-000000000003')!.text);
    const created = newClass();
    const lease = start(created.id);
    const post = (classId: string, anchor: unknown) => call('A', 'POST', `/class-sessions/${classId}/marks`, { kind: 'important', anchor, note: '' });
    expect(err(post(ENDED, { kind: 'timepoint', capture_epoch_id: lease.capture_epoch_id, sample_offset: 0 })).code).toBe('VALIDATION_FAILED');
    expect(err(post(ENDED, { kind: 'source', source_ref: { kind: 'transcript_segment', id: '73000000-0000-4000-8000-000000000003', revision_id: 'ffffffff-ffff-4fff-8fff-ffffffffffff', content_sha256: SHA } })).code).toBe('VALIDATION_FAILED');
    expect(post(ENDED, { kind: 'source', source_ref: { kind: 'transcript_segment', id: '73000000-0000-4000-8000-000000000003', revision_id: '73100000-0000-4000-8000-000000000003', content_sha256: digest } }).status).toBe(201);
    expect(post(ENDED, { kind: 'review_revision', document_id: 'e0000000-0000-4000-8000-000000000001', revision_id: 'e1000000-0000-4000-8000-000000000001', content_sha256: SHA }).status).toBe(201);
    expect(err(post(created.id, { kind: 'review_revision', document_id: 'e0000000-0000-4000-8000-000000000001', revision_id: 'e1000000-0000-4000-8000-000000000001', content_sha256: SHA })).code).toBe('VALIDATION_FAILED');
  });

  it('a newer transcript revision keeps the frozen one resolvable for an explicit original-snapshot send', () => {
    const { backend, call } = harness();
    const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, { kind: 'ai_explanation', scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: false }).body as S['GenerationPlan'];
    const ref = plan.source_snapshot!.source_refs.find(item => item.kind === 'transcript_segment')!;
    const segment = backend.classroomStore('A').segments.find(item => item.id === ref.id)!;
    backend.persistFinal('A', { classId: ENDED, streamId: '5e000000-0000-4000-8000-000000000031', segmentId: segment.id, segmentRevisionId: '5e000000-0000-4000-8000-000000000033', versionId: segment.transcript_version_id, text: '修订', startMs: segment.start_ms, endMs: segment.end_ms, revision: segment.revision + 1 });
    expect((call('A', 'GET', `/generation-plans/${plan.id}`).body as S['GenerationPlan']).source_status).toBe('changed');
    const confirm = { plan_sha256: plan.plan_sha256, confirmed_destination_revision: plan.destination.configuration_revision, confirm_send: true, confirm_images: false, confirm_original_snapshot: true, confirm_partial_scope: false };
    expect(call('A', 'POST', `/generation-plans/${plan.id}/execute`, confirm).status).toBe(202);
  });

  it('task events accepted in the page are projected into the classroom task store', () => {
    const { backend, call } = harness();
    const id = '6a000000-0000-4000-8000-000000000001';
    expect(backend.projectTask('A', { id, classId: ENDED, state: 'unknown', revision: 1, mayHaveCharged: true })).toBe(true);
    expect(backend.projectTask('A', { id, classId: ENDED, state: 'unknown', revision: 1, mayHaveCharged: true })).toBe(false);
    expect(items<S['Task']>(call('A', 'GET', '/tasks', undefined, `class_session_id=${ENDED}`)).find(item => item.id === id)).toMatchObject({ state: 'unknown', may_have_charged: true });
    expect(call('A', 'GET', `/class-sessions/${ENDED}`).body).toMatchObject({ flows: { ai: 'unknown' } });
  });

  it('the manifest client sends the capture epoch when it is known', async () => {
    const transport = vi.fn(async () => new Response(JSON.stringify({}), { status: 200 }));
    const client = createApiClient({ epoch: 1, isCurrent: () => true }, transport as typeof fetch);
    await client.audioManifest(ENDED, '74000000-0000-4000-8000-000000000001');
    await client.audioManifest(ENDED, null);
    expect(transport).toHaveBeenNthCalledWith(1, `/api/web/v1/class-sessions/${ENDED}/audio-manifest?capture_epoch_id=74000000-0000-4000-8000-000000000001`, expect.anything());
    expect(transport).toHaveBeenNthCalledWith(2, `/api/web/v1/class-sessions/${ENDED}/audio-manifest`, expect.anything());
  });
});

describe('PR-10a review round 3 regressions', () => {
  // A capture port and shell double over the real Mock server; `lose` makes the next response vanish
  // after the server processed the request (a lost response, not a rejected one).
  function rig() {
    const h = harness();
    const created = h.newClass();
    const state = { classId: created.id, captureEpochId: '', fenceToken: 0, active: false, paused: false, lastSeq: 0, lastSample: 0 };
    let lose = 0;
    let offline = false;
    const calls: string[] = [];
    const send = <T>(method: string, path: string, body: unknown, key?: string): Promise<T> => {
      calls.push(path);
      if (offline) return Promise.reject(new ApiClientError('network'));
      const reply = h.backend.handle('A', method, path, q(), body, key ?? null)!;
      if (lose > 0) { lose--; return Promise.reject(new ApiClientError('network')); }
      return reply.status >= 400 ? Promise.reject(new ApiClientError('http', reply.status, reply.body as S['Error'])) : Promise.resolve(reply.body as T);
    };
    const api = {
      acquireCapture: (id: string, body: unknown, key?: string) => send<S['CaptureLease']>('POST', `/class-sessions/${id}/capture/acquire`, body, key),
      captureCommand: (id: string, action: string, body: unknown, key?: string) => send<S['CaptureLease']>('POST', `/class-sessions/${id}/capture/${action}`, body, key),
      stopCapture: (id: string, body: unknown, key?: string) => send<S['AudioManifest']>('POST', `/class-sessions/${id}/capture/stop`, body, key),
    } as unknown as ApiClient;
    const stops = new Map<string, PendingStop>();
    const commands = new Map<string, PendingCommand>();
    let resumes = 0;
    const bindLease = (lease: CaptureBinding) => { state.captureEpochId = lease.captureEpochId; state.fenceToken = lease.fenceToken; state.lastSeq = 0; state.lastSample = 0; return true; };
    const shell = {
      deviceId: h.acquire.device_id, consent: { microphone: 'm', recovery: 'r', asr: 'a' }, now: () => 1,
      capture: {
        bound: () => state.captureEpochId ? { classId: state.classId, captureEpochId: state.captureEpochId, fenceToken: state.fenceToken, active: state.active, flows: { ...created.flows, capture: state.active ? (state.paused ? 'paused' : 'recording') : 'ended' }, leaseLost: false, classroomRevoked: false, lastSeq: state.lastSeq, lastSample: state.lastSample } : null,
        start: (_classId: string, lease: CaptureBinding) => { bindLease(lease); state.active = true; state.paused = false; return true; },
        pause: () => { if (!state.active || state.paused) return false; state.paused = true; return true; },
        rebind: (lease: CaptureBinding) => bindLease(lease),
        resume: (lease: CaptureBinding) => { if (!state.active || !state.paused) return false; resumes++; bindLease(lease); state.paused = false; return true; },
        positionMs: () => null, stopLocal: () => { state.active = false; }, durableSeqs: () => [], pendingChunks: () => 0, liveSegments: () => [],
      },
      pendingStops: { get: (id: string) => stops.get(id) ?? null, set: (id: string, value: PendingStop | null) => { if (value) stops.set(id, value); else stops.delete(id); } },
      pendingCommands: { get: (id: string) => commands.get(id) ?? null, set: (id: string, value: PendingCommand | null) => { if (value) commands.set(id, value); else commands.delete(id); } },
      unsafeToLeave: () => false, subscribe: () => () => undefined, version: () => 0,
    } as unknown as ClassroomShell;
    let n = 0;
    const key = () => `90000000-0000-4000-8000-${String(++n).padStart(12, '0')}`;
    return { ...h, created, state, api, shell, calls, key, lose: (count = 1) => { lose = count; }, setOffline: (value: boolean) => { offline = value; }, resumes: () => resumes, session: () => h.call('A', 'GET', `/class-sessions/${created.id}`).body as S['ClassSession'] };
  }

  it('an offline 继续 confirmed on reconnect resumes capture locally (P1)', async () => {
    const r = rig();
    await startCapture(r.api, r.shell, r.created.id, r.key);
    expect((await commandCapture(r.api, r.shell, r.created.id, 'pause', r.key())).serverConfirmed).toBe(true);
    r.setOffline(true);
    expect((await commandCapture(r.api, r.shell, r.created.id, 'resume', r.key())).serverConfirmed).toBe(false);
    expect(r.state.paused).toBe(true);
    r.setOffline(false);
    await reconcileCommand(r.api, r.shell, r.created.id);
    expect(r.state).toMatchObject({ active: true, paused: false });
    expect(r.session().flows.capture).toBe('recording');
    expect(r.shell.capture.bound()!.fenceToken).toBe(2);
  });

  it('a stop after a resume whose response was lost names the epoch the server resumed (P1)', async () => {
    const r = rig();
    await startCapture(r.api, r.shell, r.created.id, r.key);
    const paused = r.shell.capture.bound()!;
    await commandCapture(r.api, r.shell, r.created.id, 'pause', r.key());
    r.lose();
    expect((await commandCapture(r.api, r.shell, r.created.id, 'resume', r.key())).serverConfirmed).toBe(false);
    r.setOffline(true);
    expect((await stopCapture(r.api, r.shell, r.created.id, r.key)).confirmed).toBe(false);
    expect(r.shell.pendingStops.get(r.created.id)).toMatchObject({ afterCommand: true });
    r.setOffline(false);
    const confirmed = await confirmStop(r.api, r.shell, r.created.id);
    expect(confirmed.confirmed).toBe(true);
    expect(confirmed.manifest!.capture_epoch_id).not.toBe(paused.captureEpochId);
    expect(r.session()).toMatchObject({ flows: { capture: 'ended' } });
    expect(r.resumes()).toBe(0);
  });

  it('concurrent reconciliations of one command share a single request and apply the receipt once (P2)', async () => {
    const r = rig();
    await startCapture(r.api, r.shell, r.created.id, r.key);
    await commandCapture(r.api, r.shell, r.created.id, 'pause', r.key());
    r.setOffline(true);
    await commandCapture(r.api, r.shell, r.created.id, 'resume', r.key());
    r.setOffline(false);
    const before = r.calls.length;
    await Promise.all([reconcileCommand(r.api, r.shell, r.created.id), reconcileCommand(r.api, r.shell, r.created.id)]);
    expect(r.calls.length - before).toBe(1);
    expect(r.resumes()).toBe(1);
  });

  it('a start whose response was lost is recovered by replaying its key, not competed with (P2)', async () => {
    const r = rig();
    r.lose();
    await expect(startCapture(r.api, r.shell, r.created.id, r.key)).rejects.toBeInstanceOf(ApiClientError);
    const lease = await startCapture(r.api, r.shell, r.created.id, r.key);
    expect(lease.fence_token).toBe(1);
    expect(r.state.active).toBe(true);
  });

  it('a creation receipt of a deleted classroom is not replayed (P2)', () => {
    const { backend, call } = harness();
    const key = '91000000-0000-4000-8000-000000000001';
    const body = { course_id: mockIds.la, title: '将被删除', language: 'zh' };
    const created = call('A', 'POST', '/class-sessions', body, '', key).body as S['ClassSession'];
    backend.revokeClassroom('A', created.id);
    expect(err(call('A', 'POST', '/class-sessions', body, '', key)).code).toBe('NOT_FOUND');
  });

  it('quota reservations move once with the task outcome (P2)', () => {
    const { backend, call } = harness();
    const bucket = () => ({ ...(call('A', 'GET', '/me/quota').body as S['QuotaView']).buckets.find(item => item.kind === 'interactive_month_count')! });
    const execute = () => {
      const plan = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, { kind: 'ai_explanation', scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: false }).body as S['GenerationPlan'];
      return call('A', 'POST', `/generation-plans/${plan.id}/execute`, { plan_sha256: plan.plan_sha256, confirmed_destination_revision: plan.destination.configuration_revision, confirm_send: true, confirm_images: false, confirm_original_snapshot: false, confirm_partial_scope: false }).body as S['Task'];
    };
    const start = bucket();
    const done = execute();
    expect(bucket()).toMatchObject({ reserved: start.reserved + 1, remaining: start.remaining - 1 });
    backend.advanceTask('A', done.id, 'running');
    backend.advanceTask('A', done.id, 'succeeded');
    expect(bucket()).toMatchObject({ reserved: start.reserved, submitted: start.submitted, settled: start.settled + 1 });
    const unknown = execute();
    backend.advanceTask('A', unknown.id, 'unknown');
    expect(bucket()).toMatchObject({ reserved: start.reserved, unknown: start.unknown + 1 });
    const cancelled = execute();
    call('A', 'POST', `/tasks/${cancelled.id}/cancel`, {});
    expect(bucket()).toMatchObject({ reserved: start.reserved, remaining: start.remaining - 2 });
  });

  it('source references carry the digest of the immutable revision they name (P2)', () => {
    const { backend, call } = harness();
    const plan = () => call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, { kind: 'ai_explanation', scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: false }).body as S['GenerationPlan'];
    const first = plan().source_snapshot!.source_refs.find(item => item.kind === 'transcript_segment')!;
    const segment = backend.classroomStore('A').segments.find(item => item.id === first.id)!;
    expect(first.content_sha256).toBe(sha256Hex(segment.text));
    backend.persistFinal('A', { classId: ENDED, streamId: '5e000000-0000-4000-8000-000000000041', segmentId: segment.id, segmentRevisionId: '5e000000-0000-4000-8000-000000000043', versionId: segment.transcript_version_id, text: '修订后的文字', startMs: segment.start_ms, endMs: segment.end_ms, revision: segment.revision + 1 });
    const second = plan().source_snapshot!.source_refs.find(item => item.id === segment.id)!;
    expect(second.content_sha256).toBe(sha256Hex('修订后的文字'));
    expect(second.content_sha256).not.toBe(first.content_sha256);
    const mark = (ref: S['SourceRef']) => call('A', 'POST', `/class-sessions/${ENDED}/marks`, { kind: 'important', anchor: { kind: 'source', source_ref: ref }, note: '' });
    expect(err(mark({ ...first, content_sha256: second.content_sha256 })).code).toBe('VALIDATION_FAILED');
    expect(mark(first).status).toBe(201);
  });

  it('a replayed slide edit returns its receipt; titles follow the 160-character contract limit (P2)', () => {
    const { call } = harness();
    const upload = call('A', 'POST', '/media/uploads', { purpose: 'slide', class_session_id: ENDED, mime_type: 'image/png', byte_length: 10, sha256: SHA, filename: 'board.png' }).body as S['MediaUpload'];
    call('A', 'PUT', `/media/uploads/${upload.id}/content`, { byte_length: 10, sha256: SHA, signature: '89504e470d0a1a0a0000000d49484452' });
    const asset = call('A', 'POST', `/media/uploads/${upload.id}/complete`, { sha256: SHA, byte_length: 10 }).body as S['MediaAsset'];
    expect(err(call('A', 'POST', `/class-sessions/${ENDED}/slides`, { asset_id: asset.id, title: 'x'.repeat(161), start_ms: 0, end_ms: null })).code).toBe('VALIDATION_FAILED');
    const slide = call('A', 'POST', `/class-sessions/${ENDED}/slides`, { asset_id: asset.id, title: 'x'.repeat(160), start_ms: 0, end_ms: null }).body as S['Slide'];
    const key = '92000000-0000-4000-8000-000000000001';
    const first = call('A', 'PATCH', `/slides/${slide.id}`, { base_revision: 1, rotate: 'right' }, '', key);
    expect(call('A', 'PATCH', `/slides/${slide.id}`, { base_revision: 1, rotate: 'right' }, '', key).body).toEqual(first.body);
    expect(items<S['Slide']>(call('A', 'GET', `/class-sessions/${ENDED}/slides`)).find(item => item.id === slide.id)).toMatchObject({ revision: 2, rotation: '90' });
    expect(err(call('A', 'PATCH', `/slides/${slide.id}`, { base_revision: 2, title: 'y'.repeat(161) })).code).toBe('VALIDATION_FAILED');
  });

  it('an account teardown releases every lease that account still holds (P1)', () => {
    const { backend, call, newClass, start, acquire } = harness();
    const first = newClass();
    start(first.id);
    const second = newClass();
    expect(backend.releaseAccountLeases('A')).toBe(1);
    expect(call('A', 'POST', `/class-sessions/${second.id}/capture/acquire`, acquire).status).toBe(201);
  });

  it('the slide edit client sends an operation key', async () => {
    const transport = vi.fn(async () => new Response(JSON.stringify({}), { status: 200 }));
    const client = createApiClient({ epoch: 1, isCurrent: () => true }, transport as typeof fetch);
    await client.updateSlide(ENDED, { base_revision: 1, rotate: 'left' }, '93000000-0000-4000-8000-000000000001');
    const init = (transport.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(new Headers(init.headers).get('Idempotency-Key')).toBe('93000000-0000-4000-8000-000000000001');
  });
});
