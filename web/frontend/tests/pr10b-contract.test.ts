import { describe, expect, it } from 'vitest';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createMockBackend, mockIds } from '../src/mock/backend';

const source = resolve(process.cwd(), '../../docs/web-alpha/architecture/contracts');
const read = (name: string) => JSON.parse(readFileSync(resolve(source, name), 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
const openapi = read('openapi.json');
ajv.addSchema({ $id: 'https://classroom.invalid/openapi', components: openapi.components });
const schema = (name: string) => ajv.compile({ $ref: `https://classroom.invalid/openapi#/components/schemas/${name}` });
const check = (name: string, value: unknown) => { const validate = schema(name); expect(validate(value), `${name}: ${JSON.stringify(validate.errors)}`).toBe(true); };
const ENDED = 'c0000000-0000-4000-8000-000000000001';
const UPLOAD_FAILED = 'c0000000-0000-4000-8000-000000000004';
const SUMMARY = 'e0000000-0000-4000-8000-000000000001';
const CARDS = 'e0000000-0000-4000-8000-000000000002';

describe('PR-10b review mock responses conform to contract 0.3.0', () => {
  it('uses the frozen 0.3.0 contract with the four retranscription error codes', () => {
    expect(openapi.info.version).toBe('0.3.0');
    const codes: string[] = openapi.components.schemas.Error.properties.error.properties.code.enum;
    for (const code of ['AUDIO_EXPIRED', 'AUDIO_DELETED', 'QUOTA_SESSION_EXCEEDED', 'QUOTA_MONTH_EXCEEDED']) expect(codes).toContain(code);
  });

  it('reads, writes and errors of every review and retranscription route', () => {
    const backend = createMockBackend();
    let n = 0;
    const call = (method: string, path: string, body?: unknown, query = '') => backend.handle('A', method, path, new URLSearchParams(query), body, method === 'GET' ? null : `00000000-0000-4000-8000-${String(++n).padStart(12, '0')}`)!;
    const ok = (name: string, reply: { status: number; body: unknown }) => { expect(reply.status, JSON.stringify(reply.body)).toBeLessThan(300); check(name, reply.body); return reply.body as Record<string, unknown>; };
    const fail = (reply: { status: number; body: unknown }) => { expect(reply.status).toBeGreaterThanOrEqual(400); check('Error', reply.body); return (reply.body as { error: { code: string } }).error.code; };

    for (const query of ['', `semester_id=${mockIds.fall}`, `course_id=${mockIds.la}`, `class_session_id=${ENDED}`, 'kind=card', 'understood=false']) ok('ReviewDocumentPage', call('GET', '/review-documents', undefined, query));
    for (const documentId of [SUMMARY, CARDS, 'e0000000-0000-4000-8000-000000000003', 'e0000000-0000-4000-8000-000000000004']) {
      ok('ReviewDocument', call('GET', `/review-documents/${documentId}`));
      const revisions = ok('ReviewRevisionPage', call('GET', `/review-documents/${documentId}/revisions`));
      for (const revision of revisions.items as Array<{ id: string; source_snapshot_id: string | null }>) {
        ok('ReviewRevision', call('GET', `/review-revisions/${revision.id}`));
        if (revision.source_snapshot_id) ok('SourceSnapshot', call('GET', `/source-snapshots/${revision.source_snapshot_id}`));
      }
    }
    ok('ReviewDocument', call('PATCH', `/review-documents/${SUMMARY}`, { base_revision: 1, understood: true }));
    const personal = ok('ReviewRevision', call('POST', `/review-documents/${SUMMARY}/personal-revisions`, { base_revision: 2, base_content_revision_id: 'e1000000-0000-4000-8000-000000000001', draft_id: '5a000000-0000-4000-8000-000000000001', body_markdown: '合成', personal_note: '' }));
    ok('ReviewDocument', call('POST', `/review-documents/${SUMMARY}/adopt`, { base_revision: 3, revision_id: personal.id }));
    ok('ReviewDocument', call('POST', '/review-documents', { class_session_id: ENDED, title: '笔记', body_markdown: '合成', draft_id: '5a000000-0000-4000-8000-000000000002' }));
    const question = (call('GET', '/questions', undefined, `class_session_id=${ENDED}`).body as { items: Array<{ id: string; revision: number }> }).items[0];
    ok('Question', call('PATCH', `/questions/${question.id}`, { base_revision: question.revision, understood: true }));

    const plan = ok('GenerationPlan', call('POST', `/class-sessions/${ENDED}/generation-plans`, { kind: 'asr_retranscribe', scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: false }));
    ok('GenerationPlan', call('GET', `/generation-plans/${plan.id as string}`));
    const executeBody = { plan_sha256: plan.plan_sha256, confirmed_destination_revision: (plan.destination as { configuration_revision: string }).configuration_revision, confirm_send: true, confirm_images: false, confirm_original_snapshot: false, confirm_partial_scope: false };
    const task = ok('Task', call('POST', `/generation-plans/${plan.id as string}/execute`, executeBody));
    const unknown = backend.advanceTask('A', task.id as string, 'unknown')!;
    check('Task', unknown);
    ok('TaskReconcile', call('POST', `/tasks/${task.id as string}/reconcile`, {}));
    const retry = ok('Task', call('POST', `/tasks/${task.id as string}/retry`, { reason: '合成重试', confirm_duplicate_billing: true, original_attempt_id: unknown.attempt_id }));
    const succeeded = backend.advanceTask('A', retry.id as string, 'succeeded')!;
    check('Task', succeeded);
    const versions = ok('TranscriptVersionPage', call('GET', `/class-sessions/${ENDED}/transcript-versions`));
    for (const version of versions.items as Array<{ id: string }>) ok('TranscriptSegmentPage', call('GET', `/transcript-versions/${version.id}/segments`));
    const session = call('GET', `/class-sessions/${ENDED}`).body as { revision: number };
    ok('ClassSession', call('POST', `/class-sessions/${ENDED}/adopt-transcript`, { base_revision: session.revision, transcript_version_id: succeeded.result_id }));

    const codes = [
      fail(call('GET', '/review-documents/ffffffff-ffff-4fff-8fff-ffffffffffff')),
      fail(call('PATCH', `/review-documents/${SUMMARY}`, { base_revision: 1, understood: false })),
      fail(call('POST', `/review-documents/${SUMMARY}/adopt`, { base_revision: 1, revision_id: personal.id })),
      fail(call('POST', `/review-documents/${SUMMARY}/personal-revisions`, { base_revision: 1, base_content_revision_id: null, draft_id: 'x', body_markdown: '', personal_note: '' })),
      fail(call('GET', '/source-snapshots/ffffffff-ffff-4fff-8fff-ffffffffffff')),
      fail(call('POST', `/class-sessions/${UPLOAD_FAILED}/generation-plans`, { kind: 'asr_retranscribe', scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: false })),
      fail(call('POST', `/class-sessions/${ENDED}/adopt-transcript`, { base_revision: 1, transcript_version_id: succeeded.result_id })),
    ];
    expect(codes).toEqual(['NOT_FOUND', 'REVISION_CONFLICT', 'REVISION_CONFLICT', 'VALIDATION_FAILED', 'NOT_FOUND', 'SOURCE_UNAVAILABLE', 'REVISION_CONFLICT']);
    for (const [value, code] of [['expired', 'AUDIO_EXPIRED'], ['deleted', 'AUDIO_DELETED']] as const) {
      backend.setRetranscribeDev('A', { classId: ENDED, audio: value });
      expect(fail(call('POST', `/class-sessions/${ENDED}/generation-plans`, { kind: 'asr_retranscribe', scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: false }))).toBe(code);
    }
    backend.setRetranscribeDev('A', { classId: ENDED, audio: 'ok' });
    const second = ok('GenerationPlan', call('POST', `/class-sessions/${ENDED}/generation-plans`, { kind: 'asr_retranscribe', scope: 'range', start_ms: 0, end_ms: 20_000, seen_sources: [], include_images: false, accept_partial_scope: false }));
    for (const [quota, code] of [['session', 'QUOTA_SESSION_EXCEEDED'], ['month', 'QUOTA_MONTH_EXCEEDED']] as const) {
      backend.setRetranscribeDev('A', { quota });
      expect(fail(call('POST', `/generation-plans/${second.id as string}/execute`, { ...executeBody, plan_sha256: second.plan_sha256 }))).toBe(code);
    }
  });

  it('strict schemas reject invented review fields', () => {
    const backend = createMockBackend();
    const revision = backend.reviewStore('A').revisions[0]!;
    expect(schema('ReviewRevision')({ ...revision, auto_adopted: true })).toBe(false);
    expect(schema('ReviewRevision')({ ...revision, kind: 'adopted' })).toBe(false);
    const snapshot = backend.reviewStore('A').snapshots[0]!;
    expect(schema('SourceSnapshot')({ ...snapshot, items: [] })).toBe(false);
  });
});
