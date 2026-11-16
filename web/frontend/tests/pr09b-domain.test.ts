import { describe, expect, it } from 'vitest';
import type { components } from '../src/generated/openapi';
import { flowItems, isClassComplete, summarizeFlows, type FlowStates } from '../src/domain/flows';
import { activityItems, chooseContinueEntry, deriveTodos, EXPIRY_WARNING_MS, recentSessions } from '../src/domain/dashboard';
import { defaultSemester, readCourseListParams, safeReturnTo, sameNameCourses, semesterPayload, validateCourse, validateSemester, writeCourseListParams } from '../src/domain/courses';

type S = components['schemas'];
const ok: FlowStates = { capture: 'ended', upload: 'durable', asr: 'succeeded', ai: 'idle', sync: 'synced' };
const course = (id: string, name = id, over: Partial<S['Course']> = {}): S['Course'] => ({ id, revision: 1, created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z', semester_id: 's', name, english_name: '', language: 'auto', teacher: '', room: '', color_token: 'blue', icon_token: 'book', archived: false, class_session_count: 0, last_class_session_at: null, ...over });
const session = (id: string, courseId: string, started: string | null, over: Partial<S['ClassSession']> = {}): S['ClassSession'] => ({ id, revision: 1, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', course_id: courseId, title: id, language: 'auto', started_at: started, capture_ended_at: null, audio_expires_at: null, adopted_transcript_version_id: null, flows: ok, effective_capture_ms: 0, known_gap_count: 0, unknown_gap_count: 0, deleted_at: null, ...over });
const note = (id: string, sessionId: string, updated: string): S['ReviewDocument'] => ({ id, revision: 1, created_at: updated, updated_at: updated, class_session_id: sessionId, kind: 'note', title: id, adopted_revision_id: null, latest_personal_revision_id: 'p', candidate_revision_ids: [], understood: false, source_status: 'unchanged' });
const idle = { active: false, classSessionId: null, title: null };

describe('five independent flows (UI-DASH-07)', () => {
  it('capture fine but upload failed is never a completed class', () => {
    const flows: FlowStates = { ...ok, upload: 'failed' };
    expect(isClassComplete(flows)).toBe(false);
    const summary = summarizeFlows(flows);
    expect(summary.complete).toBe(false);
    expect(summary.abnormal.map(item => item.key)).toEqual(['upload']);
    expect(summary.tone).toBe('danger');
  });
  it('an ended capture still waiting for upload is flagged, not shown as a neutral wait', () => {
    const item = flowItems({ ...ok, upload: 'pending' }).find(entry => entry.key === 'upload')!;
    expect(item).toMatchObject({ abnormal: true, label: '录音已结束，仍待上传' });
    expect(isClassComplete({ ...ok, upload: 'pending' })).toBe(false);
  });
  it.each<[Partial<FlowStates>, boolean]>([[{}, true], [{ ai: 'succeeded' }, true], [{ ai: 'cancelled' }, true], [{ ai: 'unknown' }, false], [{ ai: 'running' }, false], [{ asr: 'unknown' }, false], [{ sync: 'conflict' }, false], [{ sync: 'local_only' }, false], [{ capture: 'recording' }, false]])('completion of %o is %s', (change, expected) => {
    expect(isClassComplete({ ...ok, ...change })).toBe(expected);
  });
  it('keeps five separate items and summarises only when nothing is abnormal', () => {
    expect(flowItems(ok).map(item => item.key)).toEqual(['capture', 'upload', 'asr', 'ai', 'sync']);
    expect(summarizeFlows(ok)).toMatchObject({ complete: true, abnormal: [] });
    expect(summarizeFlows({ ...ok, capture: 'recording', upload: 'uploading', asr: 'streaming' })).toMatchObject({ complete: false, headline: '正在采集' });
    const several = summarizeFlows({ ...ok, upload: 'failed', ai: 'unknown', sync: 'conflict' });
    expect(several.abnormal.map(item => item.key)).toEqual(['upload', 'ai', 'sync']);
  });
});

describe('deterministic continue entry (UI-DASH-04)', () => {
  const courses = [course('c1'), course('c2')];
  const sessions = [session('s-old', 'c1', '2026-10-01T00:00:00Z'), session('s-new', 'c2', '2026-10-20T00:00:00Z')];
  it('active capture outranks a local risk and recent records', () => {
    expect(chooseContinueEntry({ capture: { active: true, classSessionId: 'x', title: 't' }, localRisk: { kind: 'unsaved', detail: 'd' }, sessions, courses, documents: [] }).kind).toBe('capture');
  });
  it('local unsaved risk outranks the most recent class and note', () => {
    expect(chooseContinueEntry({ capture: idle, localRisk: { kind: 'local_only', detail: 'd' }, sessions, courses, documents: [note('n', 's-new', '2026-10-30T00:00:00Z')] }).kind).toBe('local_risk');
  });
  it('otherwise the newer of the latest class and the latest personal note wins', () => {
    const byClass = chooseContinueEntry({ capture: idle, localRisk: null, sessions, courses, documents: [note('n', 's-old', '2026-10-05T00:00:00Z')] });
    expect(byClass).toMatchObject({ kind: 'class_session', session: { id: 's-new' } });
    const byNote = chooseContinueEntry({ capture: idle, localRisk: null, sessions, courses, documents: [note('n', 's-old', '2026-10-25T00:00:00Z')] });
    expect(byNote).toMatchObject({ kind: 'note', document: { id: 'n' }, course: { id: 'c1' } });
  });
  it('a tie prefers the class; the result does not depend on input order', () => {
    const tie = [note('n', 's-new', '2026-10-20T00:00:00Z')];
    expect(chooseContinueEntry({ capture: idle, localRisk: null, sessions, courses, documents: tie }).kind).toBe('class_session');
    expect(chooseContinueEntry({ capture: idle, localRisk: null, sessions: [...sessions].reverse(), courses, documents: tie }).kind).toBe('class_session');
  });
  it('deleted classes and classes of removed courses leave no stale entry (UI-DASH-09)', () => {
    const deleted = [session('s1', 'c1', '2026-10-30T00:00:00Z', { deleted_at: '2026-10-31T00:00:00Z' }), session('s2', 'gone', '2026-10-29T00:00:00Z'), session('s3', 'c1', '2026-10-01T00:00:00Z')];
    expect(chooseContinueEntry({ capture: idle, localRisk: null, sessions: deleted, courses, documents: [note('n', 's2', '2026-11-01T00:00:00Z')] })).toMatchObject({ kind: 'class_session', session: { id: 's3' } });
    expect(chooseContinueEntry({ capture: idle, localRisk: null, sessions: [], courses: [], documents: [] }).kind).toBe('none');
  });
  it('recent sessions order by actual start, tie-broken by id', () => {
    const list = [session('b', 'c1', '2026-10-01T00:00:00Z'), session('a', 'c1', '2026-10-01T00:00:00Z'), session('never', 'c1', null, { created_at: '2026-10-05T00:00:00Z' })];
    expect(recentSessions(list, courses).map(item => item.id)).toEqual(['never', 'a', 'b']);
  });
});

describe('real todos only (UI-DASH-06)', () => {
  const now = Date.parse('2026-10-28T08:00:00Z');
  const courses = [course('c1', '线代')];
  const question = (id: string, sessionId: string, over: Partial<S['Question']> = {}): S['Question'] => ({ id, revision: 1, created_at: '2026-10-27T00:00:00Z', updated_at: '2026-10-27T00:00:00Z', class_session_id: sessionId, task_id: 't', source_snapshot_id: 'x', question: `问题${id}`, answer_markdown: 'a', state: 'succeeded', understood: false, ...over });
  it('derives each kind from records and removes it once resolved', () => {
    const sessions = [
      session('conflict', 'c1', '2026-10-20T00:00:00Z', { flows: { ...ok, sync: 'conflict' } }),
      session('fail', 'c1', '2026-10-21T00:00:00Z', { flows: { ...ok, upload: 'failed' } }),
      session('unknown', 'c1', '2026-10-22T00:00:00Z', { flows: { ...ok, ai: 'unknown' } }),
      session('expiring', 'c1', '2026-10-22T00:00:00Z', { audio_expires_at: new Date(now + EXPIRY_WARNING_MS - 1000).toISOString() }),
      session('expired', 'c1', '2026-10-01T00:00:00Z', { audio_expires_at: new Date(now - 1000).toISOString() }),
      session('later', 'c1', '2026-10-02T00:00:00Z', { audio_expires_at: new Date(now + EXPIRY_WARNING_MS + 1000).toISOString() }),
    ];
    const todos = deriveTodos({ sessions, courses, questions: [question('q1', 'fail'), question('q2', 'fail', { understood: true }), question('q3', 'fail', { state: 'running' })], now });
    expect(todos.map(todo => todo.kind)).toEqual(['conflict', 'failed', 'unknown', 'expiring', 'not_understood']);
    expect(todos.every(todo => todo.to.startsWith('/classrooms/'))).toBe(true);
    const resolved = sessions.map(item => item.id === 'conflict' ? { ...item, flows: ok } : item);
    expect(deriveTodos({ sessions: resolved, courses, questions: [], now }).some(todo => todo.kind === 'conflict')).toBe(false);
  });
  it('ignores questions of deleted classes and is empty without problems', () => {
    const sessions = [session('gone', 'c1', '2026-10-20T00:00:00Z', { deleted_at: '2026-10-21T00:00:00Z' })];
    expect(deriveTodos({ sessions, courses, questions: [question('q', 'gone')], now })).toEqual([]);
  });
  it('activity is built from notifications only and is empty for a new account', () => {
    expect(activityItems([])).toEqual([]);
    const items = activityItems([{ id: 'n1', kind: 'conflict', severity: 'warning', object_id: null, read_at: '2026-10-02T00:00:00Z', resolved_at: null, created_at: '2026-10-01T00:00:00Z', message_code: 'x' }]);
    expect(items).toEqual([{ id: 'n1', label: '出现版本冲突', severity: 'warning', at: '2026-10-01T00:00:00Z', resolved: false, read: true }]);
  });
});

describe('course and semester forms', () => {
  it('semester: required name, date validity and end ≥ start', () => {
    expect(validateSemester({ name: ' ', academic_year: '', starts_on: '', ends_on: '' })).toMatchObject({ name: expect.any(String) });
    expect(validateSemester({ name: '秋', academic_year: '', starts_on: '2026-09-07', ends_on: '2026-09-06' })).toEqual({ ends_on: '结束日期不能早于开始日期。' });
    expect(validateSemester({ name: '秋', academic_year: '', starts_on: '2026-02-30', ends_on: '' })).toMatchObject({ starts_on: expect.any(String) });
    expect(validateSemester({ name: '秋', academic_year: 'x'.repeat(31), starts_on: '', ends_on: '' })).toMatchObject({ academic_year: expect.any(String) });
    expect(validateSemester({ name: '历史学期', academic_year: '', starts_on: '2019-02-20', ends_on: '2019-02-20' })).toEqual({});
    expect(semesterPayload({ name: ' 秋 ', academic_year: '', starts_on: '', ends_on: '' })).toEqual({ name: '秋', academic_year: null, starts_on: null, ends_on: null });
  });
  it('course: name and semester required; optional fields may stay empty', () => {
    const base = { name: '', english_name: '', semester_id: '', language: 'auto' as const, teacher: '', room: '', color_token: 'blue', icon_token: 'book' };
    expect(Object.keys(validateCourse(base)).sort()).toEqual(['name', 'semester_id']);
    expect(validateCourse({ ...base, name: '线代', semester_id: 's' })).toEqual({});
    expect(validateCourse({ ...base, name: 'x'.repeat(161), semester_id: 's', teacher: 'y'.repeat(101) })).toMatchObject({ name: expect.any(String), teacher: expect.any(String) });
  });
  it('same-name courses are reported but never merged', () => {
    const list = [course('a', '线性代数'), course('b', '线性代数'), course('c', '物理')];
    expect(sameNameCourses(list, ' 线性代数 ').map(item => item.id)).toEqual(['a', 'b']);
    expect(sameNameCourses(list, '线性代数', 'a').map(item => item.id)).toEqual(['b']);
  });
  it('list URL state: changing one control keeps the rest; invalid values fall back', () => {
    const params = readCourseListParams(new URLSearchParams('semester=s1&view=list&sort=name&q=物理&archived=1'));
    expect(params).toEqual({ semester: 's1', view: 'list', sort: 'name', q: '物理', archived: true });
    expect(writeCourseListParams(params, { view: 'card' }).toString()).toBe(new URLSearchParams('semester=s1&sort=name&q=物理&archived=1').toString());
    expect(readCourseListParams(new URLSearchParams('view=grid&sort=random'))).toMatchObject({ view: 'card', sort: 'recent', semester: null });
  });
  it('default semester is the latest non-archived by start date', () => {
    const semester = (id: string, starts: string | null, archived = false): S['Semester'] => ({ id, revision: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', name: id, academic_year: null, starts_on: starts, ends_on: null, archived });
    expect(defaultSemester([semester('old', '2025-09-01'), semester('new', '2026-09-01'), semester('arch', '2027-02-01', true)])!.id).toBe('new');
    expect(defaultSemester([])).toBeNull();
  });
  it('return paths are restricted to in-app course routes', () => {
    expect(safeReturnTo('/courses?semester=x&q=y', '/courses')).toBe('/courses?semester=x&q=y');
    for (const bad of ['//evil.example', 'https://evil.example', '/admin/users', 'javascript:alert(1)', null]) expect(safeReturnTo(bad, '/courses')).toBe('/courses');
  });
});
