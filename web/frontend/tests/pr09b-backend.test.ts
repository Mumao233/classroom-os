import { describe, expect, it, vi } from 'vitest';
import type { components } from '../src/generated/openapi';
import { createMockBackend, mockIds, type MockAccountKey } from '../src/mock/backend';
import { ApiClientError, createApiClient } from '../src/api/client';

type S = components['schemas'];
const q = (value = '') => new URLSearchParams(value);
function harness() {
  const backend = createMockBackend();
  let key = 0;
  const call = (account: MockAccountKey, method: string, path: string, body?: unknown, query = '', idempotencyKey?: string) =>
    backend.handle(account, method, path, q(query), body, idempotencyKey ?? (method === 'GET' ? null : `00000000-0000-4000-8000-${String(++key).padStart(12, '0')}`))!;
  return { backend, call };
}
const items = <T>(reply: { body: unknown }) => (reply.body as { items: T[] }).items;
const err = (reply: { body: unknown }) => (reply.body as S['Error']).error;

describe('mock backend failure modes used by PR-09b pages', () => {
  it('same-name courses get independent stable IDs; renaming keeps the ID', () => {
    const { call } = harness();
    const first = call('A', 'POST', '/courses', { semester_id: mockIds.fall, name: '线性代数' });
    expect(first.status).toBe(201);
    const id = (first.body as S['Course']).id;
    expect(id).not.toBe(mockIds.la);
    const list = items<S['Course']>(call('A', 'GET', '/courses', undefined, `semester_id=${mockIds.fall}`)).filter(course => course.name === '线性代数');
    expect(new Set(list.map(course => course.id)).size).toBe(2);
    const renamed = call('A', 'PATCH', `/courses/${id}`, { base_revision: 1, name: '线性代数（重修）' });
    expect(renamed.body).toMatchObject({ id, revision: 2, name: '线性代数（重修）' });
    expect((call('A', 'GET', `/courses/${id}`).body as S['Course']).id).toBe(id);
  });

  it('a retried create with the same key is not duplicated; a changed body under that key is rejected', () => {
    const { call } = harness();
    const body = { semester_id: mockIds.fall, name: '网络重试' };
    const key = '11111111-2222-4333-8444-555555555555';
    const once = call('A', 'POST', '/courses', body, '', key);
    const again = call('A', 'POST', '/courses', body, '', key);
    expect((again.body as S['Course']).id).toBe((once.body as S['Course']).id);
    expect(items<S['Course']>(call('A', 'GET', '/courses', undefined, 'q=网络重试'))).toHaveLength(1);
    const changed = call('A', 'POST', '/courses', { ...body, name: '别的' }, '', key);
    expect(changed.status).toBe(409);
    expect(err(changed).code).toBe('IDEMPOTENCY_MISMATCH');
  });

  it('CAS: a stale base revision is rejected with the current revision and changes nothing', () => {
    const { call, backend } = harness();
    backend.bumpElsewhere('A', 'semester', mockIds.fall, { name: '其他设备的名称' });
    const stale = call('A', 'PATCH', `/semesters/${mockIds.fall}`, { base_revision: 1, name: '我的名称' });
    expect(stale.status).toBe(409);
    expect(err(stale)).toMatchObject({ code: 'REVISION_CONFLICT', current_revision: 2 });
    expect((call('A', 'GET', `/semesters/${mockIds.fall}`).body as S['Semester']).name).toBe('其他设备的名称');
    expect(call('A', 'PATCH', `/semesters/${mockIds.fall}`, { base_revision: 2, name: '我的名称' }).status).toBe(200);
  });

  it('semester dates: an end before the start is refused on create and on edit', () => {
    const { call } = harness();
    expect(call('A', 'POST', '/semesters', { name: '倒序', starts_on: '2026-09-07', ends_on: '2026-09-01' }).status).toBe(400);
    expect(call('A', 'PATCH', `/semesters/${mockIds.fall}`, { base_revision: 1, ends_on: '2026-01-01' }).status).toBe(400);
    expect(call('A', 'POST', '/semesters', { name: '历史学期', academic_year: '2019-2020', starts_on: '2019-09-01', ends_on: '2020-01-15' }).status).toBe(201);
  });

  it('A/B isolation: B never lists, reads or edits A records', () => {
    const { call } = harness();
    const bCourses = items<S['Course']>(call('B', 'GET', '/courses', undefined, ''));
    expect(bCourses.map(course => course.name)).toEqual(['组合数学（模拟账号 B）']);
    expect(call('B', 'GET', `/courses/${mockIds.la}`).status).toBe(404);
    expect(call('B', 'PATCH', `/courses/${mockIds.la}`, { base_revision: 1, name: 'x' }).status).toBe(404);
    expect(items(call('B', 'GET', '/class-sessions'))).toHaveLength(1);
    expect((call('A', 'GET', `/courses/${mockIds.la}`).body as S['Course']).name).toBe('线性代数');
  });

  it('archiving hides from the default list but keeps content and counts', () => {
    const { call } = harness();
    const archived = call('A', 'PATCH', `/courses/${mockIds.ds}`, { base_revision: 1, archived: true });
    expect(archived.body).toMatchObject({ archived: true, class_session_count: 2 });
    expect(items<S['Course']>(call('A', 'GET', '/courses', undefined, `semester_id=${mockIds.fall}`)).some(course => course.id === mockIds.ds)).toBe(false);
    expect(items<S['Course']>(call('A', 'GET', '/courses', undefined, `semester_id=${mockIds.fall}&archived=true`)).some(course => course.id === mockIds.ds)).toBe(true);
    expect(items<S['ClassSession']>(call('A', 'GET', '/class-sessions', undefined, `course_id=${mockIds.ds}`))).toHaveLength(2);
  });

  it('moving a class changes only its course; history and counts stay consistent', () => {
    const { call } = harness();
    const [target] = items<S['ClassSession']>(call('A', 'GET', '/class-sessions', undefined, `course_id=${mockIds.la}`)).filter(item => item.title === '行列式的性质');
    const moved = call('A', 'PATCH', `/class-sessions/${target.id}`, { base_revision: target.revision, course_id: mockIds.ds });
    expect(moved.body).toMatchObject({ id: target.id, course_id: mockIds.ds, started_at: target.started_at, capture_ended_at: target.capture_ended_at, flows: target.flows, revision: target.revision + 1 });
    expect((call('A', 'GET', `/courses/${mockIds.la}`).body as S['Course']).class_session_count).toBe(2);
    expect((call('A', 'GET', `/courses/${mockIds.ds}`).body as S['Course']).class_session_count).toBe(3);
    const stale = call('A', 'PATCH', `/class-sessions/${target.id}`, { base_revision: target.revision, course_id: mockIds.la });
    expect(err(stale).code).toBe('REVISION_CONFLICT');
  });

  it('deletion requires a fresh preview token; cascade and move-children behave differently', () => {
    const { call } = harness();
    const noPreview = call('A', 'POST', `/courses/${mockIds.mp}/delete`, { base_revision: 1, mode: 'cascade', confirmation_token: 'guess' });
    expect(err(noPreview).code).toBe('PLAN_EXPIRED');
    const preview = call('A', 'GET', `/courses/${mockIds.mp}/deletion-preview`).body as S['DeletionPreview'];
    expect(preview).toMatchObject({ scope_type: 'course', class_session_count: 2 });
    expect(err(call('A', 'POST', `/courses/${mockIds.mp}/delete`, { base_revision: 9, mode: 'cascade', confirmation_token: preview.confirmation_token })).code).toBe('REVISION_CONFLICT');
    const done = call('A', 'POST', `/courses/${mockIds.mp}/delete`, { base_revision: 1, mode: 'cascade', confirmation_token: preview.confirmation_token });
    expect(done.status).toBe(202);
    expect(call('A', 'GET', `/courses/${mockIds.mp}`).status).toBe(404);
    expect(items<S['ClassSession']>(call('A', 'GET', '/class-sessions')).some(item => item.course_id === mockIds.mp)).toBe(false);
    // Moving children keeps the classes under another course.
    const dsPreview = call('A', 'GET', `/courses/${mockIds.ds}/deletion-preview`).body as S['DeletionPreview'];
    expect(call('A', 'POST', `/courses/${mockIds.ds}/delete`, { base_revision: 1, mode: 'move_children', destination_id: mockIds.ds, confirmation_token: dsPreview.confirmation_token }).status).toBe(400);
    expect(call('A', 'POST', `/courses/${mockIds.ds}/delete`, { base_revision: 1, mode: 'move_children', destination_id: mockIds.la, confirmation_token: dsPreview.confirmation_token }).status).toBe(202);
    expect((call('A', 'GET', `/courses/${mockIds.la}`).body as S['Course']).class_session_count).toBe(5);
  });

  it('course materials: CAS, validation, and language change stored for later defaults only', () => {
    const { call } = harness();
    const [material] = items<S['CourseMaterial']>(call('A', 'GET', `/courses/${mockIds.la}/materials`));
    expect(err(call('A', 'POST', `/courses/${mockIds.la}/materials`, { base_revision: 1, syllabus: '', glossary: [], language: 'en' })).code).toBe('REVISION_CONFLICT');
    expect(call('A', 'POST', `/courses/${mockIds.la}/materials`, { base_revision: material.revision, syllabus: '', glossary: [{ term: '', meaning: '', language: 'en' }], language: 'en' }).status).toBe(400);
    const saved = call('A', 'POST', `/courses/${mockIds.la}/materials`, { base_revision: material.revision, syllabus: '新大纲', glossary: [], language: 'en' });
    expect(saved.body).toMatchObject({ revision: material.revision + 1, language: 'en', syllabus: '新大纲' });
    // Each write is a new immutable version with its own ID; the list returns only the current one.
    expect((saved.body as S['CourseMaterial']).id).not.toBe(material.id);
    expect(items<S['CourseMaterial']>(call('A', 'GET', `/courses/${mockIds.la}/materials`)).map(item => item.id)).toEqual([(saved.body as S['CourseMaterial']).id]);
    const sessions = items<S['ClassSession']>(call('A', 'GET', '/class-sessions', undefined, `course_id=${mockIds.la}`));
    expect(sessions.every(item => item.language === 'auto')).toBe(true);
  });

  it('course review filter returns only that course’s shared documents', () => {
    const { call } = harness();
    const la = items<S['ReviewDocument']>(call('A', 'GET', '/review-documents', undefined, `course_id=${mockIds.la}`));
    const sessions = new Set(items<S['ClassSession']>(call('A', 'GET', '/class-sessions', undefined, `course_id=${mockIds.la}`)).map(item => item.id));
    expect(la.length).toBeGreaterThan(0);
    expect(la.every(doc => sessions.has(doc.class_session_id))).toBe(true);
    const all = items<S['ReviewDocument']>(call('A', 'GET', '/review-documents'));
    expect(la.every(doc => all.some(item => item.id === doc.id && item.revision === doc.revision))).toBe(true);
  });

  it('an empty account (MOCK-01) has no records at all', () => {
    const { call, backend } = harness();
    backend.reset('A', 'empty');
    for (const path of ['/semesters', '/courses', '/class-sessions', '/review-documents', '/notifications']) expect(items(call('A', 'GET', path))).toEqual([]);
    expect(call('A', 'GET', '/me').status).toBe(200);
  });
});

describe('typed client writes', () => {
  it('sends JSON with a fresh idempotency key and the session CSRF token, and decodes errors', async () => {
    const transport = vi.fn(async () => Response.json({ error: { code: 'REVISION_CONFLICT', message: 'x', request_id: '00000000-0000-4000-8000-000000000000', retryable: false, retry_after_seconds: null, current_revision: 7, conflict_id: null, task_id: null } }, { status: 409 }));
    const client = createApiClient({ epoch: 1, isCurrent: () => true }, { transport, csrfToken: () => 'csrf-synthetic', newKey: () => 'key-1' });
    const failure = await client.updateCourse('c 1', { base_revision: 1, name: 'n' }).catch(error => error);
    expect(failure).toBeInstanceOf(ApiClientError);
    expect(failure.code).toBe('REVISION_CONFLICT');
    expect(failure.currentRevision).toBe(7);
    expect(transport).toHaveBeenCalledWith('/api/web/v1/courses/c%201', expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ base_revision: 1, name: 'n' }), headers: expect.objectContaining({ 'Idempotency-Key': 'key-1', 'X-CSRF-Token': 'csrf-synthetic', 'Content-Type': 'application/json' }) }));
  });
  it('encodes list filters and omits empty values', async () => {
    const transport = vi.fn(async () => Response.json({ items: [], next_cursor: null }));
    const client = createApiClient({ epoch: 1, isCurrent: () => true }, transport);
    await client.courses({ semester_id: 's', q: '线 代&x', archived: false, sort: 'name' });
    await client.courses({ q: '' });
    expect(transport.mock.calls[0][0]).toBe('/api/web/v1/courses?semester_id=s&q=%E7%BA%BF+%E4%BB%A3%26x&archived=false&sort=name&limit=100');
    expect(transport.mock.calls[1][0]).toBe('/api/web/v1/courses?limit=100');
  });
  it('a write from a superseded account epoch never reaches the transport', async () => {
    const transport = vi.fn();
    const client = createApiClient({ epoch: 1, isCurrent: epoch => epoch === 2 }, transport);
    await expect(client.createCourse({ semester_id: 's', name: 'n' })).rejects.toMatchObject({ kind: 'stale' });
    expect(transport).not.toHaveBeenCalled();
  });
});
