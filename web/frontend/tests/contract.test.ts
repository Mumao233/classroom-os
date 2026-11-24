import { describe, expect, it } from 'vitest';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { coursePage, coursePages, meta, semesterPage, unauthenticated } from '../src/mock/fixtures';
import { eventScripts } from '../src/mock/event-scripts';
import { createMockBackend, mockIds } from '../src/mock/backend';

const source = resolve(process.cwd(), '../../docs/web-alpha/architecture/contracts');
const read = (name: string) => JSON.parse(readFileSync(resolve(source, name), 'utf8'));
const openapi = read('openapi.json');
const events = read('events.schema.json');
const cases = read('examples.json').cases as Array<{name: string; schema_file: string; schema_pointer: string; expected_valid: boolean; value: unknown}>;
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema({ $id: 'https://classroom.invalid/openapi', components: openapi.components });
ajv.addSchema(events, 'https://classroom.invalid/events');
ajv.addSchema(read('identity-control.schema.json'), 'https://classroom.invalid/identity-control');

describe('approved PR-01 contract examples', () => {
  for (const item of cases) {
    it(item.name, () => {
      const base = item.schema_file === 'openapi.json' ? 'https://classroom.invalid/openapi' : item.schema_file === 'identity-control.schema.json' ? 'https://classroom.invalid/identity-control' : 'https://classroom.invalid/events';
      const validate = ajv.compile({ $ref: base + item.schema_pointer });
      expect(validate(item.value), JSON.stringify(validate.errors)).toBe(item.expected_valid);
    });
  }
});

describe('PR-11b admin write and reauthentication contracts', () => {
  it.each([
    ['/admin/users/{user_id}', 'patch', 'adminUpdateUser', 'user'],
    ['/admin/invitations', 'post', 'adminCreateInvitation', 'account'],
    ['/admin/invitations/{invitation_id}/revoke', 'post', 'adminRevokeInvitation', 'invitation'],
    ['/admin/invitations/{invitation_id}/reissue', 'post', 'adminReissueInvitation', 'invitation'],
    ['/admin/quota-adjustments', 'post', 'adminAdjustQuota', 'account'],
    ['/admin/switches', 'patch', 'adminUpdateSwitches', 'account'],
  ])('%s binds the reauthentication proof to the same write DTO', (path, method, purpose, kind) => {
    const write = openapi.paths[path][method];
    expect(write.operationId).toBe(purpose);
    expect(write['x-required-role']).toBe('admin');
    expect(write['x-requires-mfa']).toBe(true);
    expect(write['x-requires-reauth']).toBe(true);
    for (const header of ['IdempotencyKey', 'CsrfToken', 'ReauthToken']) {
      expect(write.parameters).toContainEqual({ $ref: '#/components/parameters/' + header });
    }
    const operations = openapi.components.schemas.ReauthOperation.oneOf;
    const matches = operations.filter((op: {properties: {purpose: {const: string}}}) => op.properties.purpose.const === purpose);
    expect(matches).toHaveLength(1);
    const { target, candidate_parameters: parameters } = matches[0].properties;
    expect(target.properties.kind.const).toBe(kind);
    expect(parameters.required).toEqual(expect.arrayContaining(['idempotency_key', 'body']));
    expect(parameters.properties.body).toEqual(write.requestBody.content['application/json'].schema);
  });
});

describe('mock HTTP fixtures', () => {
  for (const [name, schema, fixture] of [['empty', 'CoursePage', coursePage], ['account A', 'CoursePage', coursePages.A], ['account B', 'CoursePage', coursePages.B], ['session revoked', 'Error', unauthenticated], ['semesters', 'SemesterPage', semesterPage], ['meta', 'Meta', meta]] as const) {
    it(`${name}: ${schema} conforms`, () => {
      const validate = ajv.compile({ $ref: `https://classroom.invalid/openapi#/components/schemas/${schema}` });
      expect(validate(fixture), JSON.stringify(validate.errors)).toBe(true);
    });
  }
});

describe('scripted mock event envelopes', () => {
  const validate = ajv.getSchema('https://classroom.invalid/events')!;
  for (const [script, steps] of Object.entries(eventScripts)) {
    for (const [index, value] of steps.entries()) {
      it(`${script} step ${index + 1}: ${value.type}`, () => {
        expect(validate(value), JSON.stringify(validate.errors)).toBe(true);
      });
    }
  }
  it('rejects a malformed final and a frame claiming durable persistence', () => {
    const final = structuredClone(eventScripts.transcript[1]);
    const missingCursor = { ...final, cursor: undefined };
    expect(validate(missingCursor)).toBe(false);
    const frame = structuredClone(eventScripts.upload[0]);
    expect(validate({ ...frame, data: { ...frame.data, durable: true } })).toBe(false);
  });
});

describe('PR-09b stateful mock responses conform to the approved contract', () => {
  const schema = (name: string) => ajv.compile({ $ref: `https://classroom.invalid/openapi#/components/schemas/${name}` });
  let n = 0;
  const key = () => `00000000-0000-4000-8000-${String(++n).padStart(12, '0')}`;
  const check = (name: string, value: unknown) => { const validate = schema(name); expect(validate(value), `${name}: ${JSON.stringify(validate.errors)}`).toBe(true); };
  for (const account of ['A', 'B'] as const) {
    it(`account ${account}: every read endpoint returns its schema`, () => {
      const backend = createMockBackend();
      const get = (path: string, query = '') => backend.handle(account, 'GET', path, new URLSearchParams(query), undefined, null)!;
      for (const [path, name] of [['/me', 'User'], ['/me/preferences', 'Preferences'], ['/me/quota', 'QuotaView'], ['/me/sessions', 'DeviceSessionPage'], ['/me/devices', 'TrustedDevicePage'], ['/semesters', 'SemesterPage'], ['/courses', 'CoursePage'], ['/class-sessions', 'ClassSessionPage'], ['/review-documents', 'ReviewDocumentPage'], ['/notifications', 'NotificationPage']] as const) {
        const reply = get(path); expect(reply.status).toBe(200); check(name, reply.body);
      }
      for (const query of ['archived=true', 'sort=name', 'q=x']) check('CoursePage', get('/courses', query).body);
      const courses = (get('/courses').body as { items: Array<{ id: string; semester_id: string }> }).items;
      for (const course of courses) {
        check('Course', get(`/courses/${course.id}`).body);
        check('CourseMaterialPage', get(`/courses/${course.id}/materials`).body);
        check('DeletionPreview', get(`/courses/${course.id}/deletion-preview`).body);
        check('Semester', get(`/semesters/${course.semester_id}`).body);
        check('DeletionPreview', get(`/semesters/${course.semester_id}/deletion-preview`).body);
      }
      for (const item of (get('/class-sessions').body as { items: Array<{ id: string }> }).items) { check('ClassSession', get(`/class-sessions/${item.id}`).body); check('QuestionPage', get('/questions', `class_session_id=${item.id}`).body); }
    });
  }
  it('write responses and every error envelope conform, including CAS and deletion', () => {
    const backend = createMockBackend();
    const call = (method: string, path: string, body: unknown) => backend.handle('A', method, path, new URLSearchParams(), body, key())!;
    const semester = call('POST', '/semesters', { name: '新学期', academic_year: null, starts_on: '2027-02-20', ends_on: '2027-07-01' }); check('Semester', semester.body);
    const created = call('POST', '/courses', { semester_id: (semester.body as { id: string }).id, name: '线性代数' }); expect(created.status).toBe(201); check('Course', created.body);
    const id = (created.body as { id: string }).id;
    check('Course', call('PATCH', `/courses/${id}`, { base_revision: 1, name: '改名', archived: true }).body);
    check('Semester', call('PATCH', `/semesters/${mockIds.fall}`, { base_revision: 1, academic_year: '2026-2027' }).body);
    check('CourseMaterial', call('POST', `/courses/${mockIds.la}/materials`, { base_revision: 2, syllabus: 's', glossary: [{ term: 't', meaning: 'm', language: 'zh' }], language: 'zh' }).body);
    const session = (backend.handle('A', 'GET', '/class-sessions', new URLSearchParams(), undefined, null)!.body as { items: Array<{ id: string; revision: number }> }).items[0];
    check('ClassSession', call('PATCH', `/class-sessions/${session.id}`, { base_revision: session.revision, title: '改名' }).body);
    check('User', call('PATCH', '/me', { base_revision: 1, nickname: '新昵称' }).body);
    check('Preferences', call('PATCH', '/me/preferences', { base_revision: 1, reading_font_px: 20 }).body);
    check('Acknowledged', call('POST', '/me/sessions/a1000000-0000-4000-8000-000000000002/revoke', {}).body);
    const preview = backend.handle('A', 'GET', `/courses/${mockIds.mp}/deletion-preview`, new URLSearchParams(), undefined, null)!.body as { confirmation_token: string };
    check('Deletion', call('POST', `/courses/${mockIds.mp}/delete`, { base_revision: 1, mode: 'cascade', confirmation_token: preview.confirmation_token }).body);
    for (const reply of [call('PATCH', `/courses/${mockIds.la}`, { base_revision: 99 }), call('POST', '/courses', { name: '' }), call('GET', `/courses/${mockIds.mp}`, undefined), call('POST', `/courses/${mockIds.ds}/delete`, { base_revision: 1, mode: 'cascade', confirmation_token: 'x' }), call('PATCH', '/me', { base_revision: 1, email: 'x@example.invalid' })]) {
      expect(reply.status).toBeGreaterThanOrEqual(400); check('Error', reply.body);
    }
  });
  it('strict schemas would reject an invented field, so passing above proves none were added', () => {
    const backend = createMockBackend();
    const course = backend.handle('A', 'GET', `/courses/${mockIds.la}`, new URLSearchParams(), undefined, null)!.body as Record<string, unknown>;
    expect(schema('Course')({ ...course, description: 'not in contract' })).toBe(false);
    expect(schema('Course')({ ...course, language: 'fr' })).toBe(false);
  });
});
