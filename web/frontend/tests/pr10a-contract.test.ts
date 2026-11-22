import { describe, expect, it } from 'vitest';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createMockBackend, mockIds } from '../src/mock/backend';
import { createTranscriptFeed } from '../src/mock/classroom-feed';

const source = resolve(process.cwd(), '../../docs/web-alpha/architecture/contracts');
const read = (name: string) => JSON.parse(readFileSync(resolve(source, name), 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema({ $id: 'https://classroom.invalid/openapi', components: read('openapi.json').components });
ajv.addSchema(read('events.schema.json'), 'https://classroom.invalid/events');
const schema = (name: string) => ajv.compile({ $ref: `https://classroom.invalid/openapi#/components/schemas/${name}` });
const check = (name: string, value: unknown) => { const validate = schema(name); expect(validate(value), `${name}: ${JSON.stringify(validate.errors)}`).toBe(true); };
const SHA = 'a'.repeat(64);
const ENDED = 'c0000000-0000-4000-8000-000000000001';

describe('PR-10a classroom mock responses conform to contract 0.2.1', () => {
  it('reads, writes and errors of every classroom route', () => {
    const backend = createMockBackend();
    let n = 0;
    const call = (method: string, path: string, body?: unknown, query = '') => backend.handle('A', method, path, new URLSearchParams(query), body, method === 'GET' ? null : `00000000-0000-4000-8000-${String(++n).padStart(12, '0')}`)!;
    const ok = (name: string, reply: { status: number; body: unknown }) => { expect(reply.status, JSON.stringify(reply.body)).toBeLessThan(300); check(name, reply.body); return reply.body as Record<string, unknown>; };

    const created = ok('ClassSession', call('POST', '/class-sessions', { course_id: mockIds.la, title: '契约检查', language: 'auto' }));
    const id = created.id as string;
    const lease = ok('CaptureLease', call('POST', `/class-sessions/${id}/capture/acquire`, { device_id: 'a2000000-0000-4000-8000-000000000001', microphone_consent_version: 'm', recovery_consent_version: 'r', asr_consent_version: 'a', confirm_takeover: false, expected_lease_id: null }));
    const command = { capture_epoch_id: lease.capture_epoch_id, fence_token: lease.fence_token, last_local_seq: 1, last_sample: 16000, client_monotonic_ms: 5 };
    ok('CaptureLease', call('POST', `/class-sessions/${id}/capture/pause`, command));
    // Resuming retires the paused epoch: later commands use the new epoch and fence.
    const resumed = ok('CaptureLease', call('POST', `/class-sessions/${id}/capture/resume`, command));
    const current = { ...command, capture_epoch_id: resumed.capture_epoch_id, fence_token: resumed.fence_token };
    ok('Mark', call('POST', `/class-sessions/${id}/marks`, { kind: 'important', anchor: { kind: 'timepoint', capture_epoch_id: lease.capture_epoch_id, sample_offset: 0 }, note: '' }));
    backend.persistFinal('A', { classId: id, streamId: '5e000000-0000-4000-8000-000000000001', segmentId: '5e000000-0000-4000-8000-000000000002', segmentRevisionId: '5e000000-0000-4000-8000-000000000003', versionId: '5e000000-0000-4000-8000-000000000004', text: '合成', startMs: 0, endMs: 4000, revision: 1 });
    ok('AudioManifest', call('POST', `/class-sessions/${id}/capture/stop`, { ...current, manifest_sha256: SHA, known_gaps: [] }));
    ok('AudioManifest', call('GET', `/class-sessions/${id}/audio-manifest`, undefined, `capture_epoch_id=${current.capture_epoch_id}`));
    const listed = ok('AudioManifestPage', call('GET', `/class-sessions/${id}/audio-manifests`));
    expect((listed.items as unknown[]).length).toBe(2);
    ok('ClassSession', call('GET', `/class-sessions/${id}`));

    for (const classId of [id, ENDED]) {
      const versions = ok('TranscriptVersionPage', call('GET', `/class-sessions/${classId}/transcript-versions`));
      for (const version of versions.items as Array<{ id: string }>) ok('TranscriptSegmentPage', call('GET', `/transcript-versions/${version.id}/segments`));
      ok('MarkPage', call('GET', `/class-sessions/${classId}/marks`));
      ok('SlidePage', call('GET', `/class-sessions/${classId}/slides`));
      ok('TaskPage', call('GET', '/tasks', undefined, `class_session_id=${classId}`));
    }
    ok('AudioManifest', call('GET', `/class-sessions/${ENDED}/audio-manifest`, undefined, 'capture_epoch_id=74000000-0000-4000-8000-000000000001'));
    ok('AudioManifestPage', call('GET', `/class-sessions/${ENDED}/audio-manifests`, undefined, 'limit=1'));

    const upload = ok('MediaUpload', call('POST', '/media/uploads', { purpose: 'slide', class_session_id: ENDED, mime_type: 'image/webp', byte_length: 42, sha256: SHA, filename: 'board.webp' }));
    ok('MediaUpload', call('PUT', `/media/uploads/${upload.id as string}/content`, { byte_length: 42, sha256: SHA, signature: '52494646000000005745425056503820' }));
    const asset = ok('MediaAsset', call('POST', `/media/uploads/${upload.id as string}/complete`, { sha256: SHA, byte_length: 42 }));
    const slide = ok('Slide', call('POST', `/class-sessions/${ENDED}/slides`, { asset_id: asset.id, title: '板书', start_ms: 0, end_ms: 1000 }));
    ok('Slide', call('PATCH', `/slides/${slide.id as string}`, { base_revision: 1, rotate: 'left', removed: true }));

    for (const request of [
      { kind: 'ai_question', scope: 'recent120', question: '为什么？', seen_sources: [], include_images: false, accept_partial_scope: false },
      { kind: 'ai_explanation', scope: 'slides', seen_sources: [], include_images: true, accept_partial_scope: false },
      { kind: 'review_summary', scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: true },
    ]) {
      const plan = ok('GenerationPlan', call('POST', `/class-sessions/${request.kind === 'review_summary' ? id : ENDED}/generation-plans`, request));
      ok('GenerationPlan', call('GET', `/generation-plans/${plan.id as string}`));
      const snapshot = plan.source_snapshot as { scope_complete: boolean } | null;
      const task = ok('Task', call('POST', `/generation-plans/${plan.id as string}/execute`, { plan_sha256: plan.plan_sha256, confirmed_destination_revision: (plan.destination as { configuration_revision: string }).configuration_revision, confirm_send: true, confirm_images: Boolean(plan.contains_images), confirm_original_snapshot: false, confirm_partial_scope: snapshot ? !snapshot.scope_complete : false }));
      ok('Task', call('GET', `/tasks/${task.id as string}`));
      ok('Task', backend.advanceTask('A', task.id as string, 'unknown') ? { status: 200, body: backend.classroomStore('A').tasks.find(item => item.id === task.id) } : { status: 500, body: null });
      ok('TaskReconcile', call('POST', `/tasks/${task.id as string}/reconcile`, {}));
      ok('Task', call('POST', `/tasks/${task.id as string}/cancel`, {}));
    }
    for (const question of (call('GET', '/questions', undefined, `class_session_id=${ENDED}`).body as { items: unknown[] }).items) check('Question', question);
    for (const document of (call('GET', '/review-documents').body as { items: unknown[] }).items) check('ReviewDocument', document);

    for (const reply of [
      call('POST', '/class-sessions', { course_id: mockIds.la, title: 'x', extra: 1 }),
      call('GET', '/class-sessions/ffffffff-ffff-4fff-8fff-ffffffffffff'),
      call('POST', `/class-sessions/${id}/capture/acquire`, { device_id: 'a2000000-0000-4000-8000-000000000001', microphone_consent_version: 'm', recovery_consent_version: 'r', asr_consent_version: 'a', confirm_takeover: false }),
      call('POST', `/class-sessions/${id}/capture/pause`, command),
      call('POST', `/class-sessions/${id}/generation-plans`, { kind: 'asr_retranscribe', scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: false }),
      call('POST', `/class-sessions/${id}/generation-plans`, { kind: 'review_summary', scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: false }),
      call('PATCH', `/slides/${slide.id as string}`, { base_revision: 1 }),
      call('GET', '/tasks/ffffffff-ffff-4fff-8fff-ffffffffffff'),
    ]) { expect(reply.status).toBeGreaterThanOrEqual(400); check('Error', reply.body); }
  });

  it('strict schemas reject an invented task or plan field', () => {
    const backend = createMockBackend();
    const task = backend.classroomStore('A').tasks[0]!;
    expect(schema('Task')({ ...task, explanation_text: 'not in contract' })).toBe(false);
    expect(schema('Task')({ ...task, state: 'done' })).toBe(false);
  });
});

describe('PR-10a development transcript feed emits valid v1 events', () => {
  it('partials, finals, late finals and duplicates all validate', () => {
    const validate = ajv.getSchema('https://classroom.invalid/events')!;
    const feed = createTranscriptFeed();
    const classId = 'c1000000-0000-4000-8000-000000000001';
    const events = [];
    for (let index = 0; index < 6; index++) events.push(feed.advance(classId));
    events.push(feed.late(classId)!, feed.duplicate(classId)!);
    for (const event of events) expect(validate(event), JSON.stringify(validate.errors)).toBe(true);
    expect(events.map(event => event.type)).toEqual(['transcript.partial', 'transcript.final', 'transcript.partial', 'transcript.final', 'transcript.partial', 'transcript.final', 'transcript.final', 'transcript.final']);
    expect(events[7]).toBe(events[5]);
  });
});
