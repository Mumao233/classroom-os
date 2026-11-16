import type { components } from '../generated/openapi';

type S = components['schemas'];
export type Language = S['Course']['language'];
export const languageLabels: Record<Language, string> = { auto: '自动识别', zh: '中文', en: '英文', mixed: '中英混合' };
/** Default per UI-COURSE-05; the server applies the same default when omitted. */
export const DEFAULT_LANGUAGE: Language = 'auto';

export const colorTokens = ['blue', 'teal', 'violet', 'amber', 'rose', 'green'] as const;
export const colorLabels: Record<(typeof colorTokens)[number], string> = { blue: '蓝', teal: '青', violet: '紫', amber: '琥珀', rose: '玫红', green: '绿' };
export const iconTokens = ['book', 'math', 'atom', 'code', 'language', 'flask'] as const;
export const iconLabels: Record<(typeof iconTokens)[number], string> = { book: '书本', math: '数学', atom: '物理', code: '编程', language: '语言', flask: '实验' };
export const normalizeColor = (token: string) => (colorTokens as readonly string[]).includes(token) ? token as (typeof colorTokens)[number] : 'blue';

export type FieldErrors<T extends string> = Partial<Record<T, string>>;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const validDate = (value: string) => ISO_DATE.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`)) && new Date(`${value}T00:00:00Z`).toISOString().startsWith(value);

export type SemesterValues = { name: string; academic_year: string; starts_on: string; ends_on: string };
export function validateSemester(values: SemesterValues): FieldErrors<keyof SemesterValues> {
  const errors: FieldErrors<keyof SemesterValues> = {};
  if (!values.name.trim()) errors.name = '请输入学期名称。';
  else if (values.name.trim().length > 100) errors.name = '学期名称最多 100 个字符。';
  if (values.academic_year.trim().length > 30) errors.academic_year = '学年最多 30 个字符。';
  if (values.starts_on && !validDate(values.starts_on)) errors.starts_on = '请输入有效的开始日期。';
  if (values.ends_on && !validDate(values.ends_on)) errors.ends_on = '请输入有效的结束日期。';
  if (!errors.starts_on && !errors.ends_on && values.starts_on && values.ends_on && values.ends_on < values.starts_on) errors.ends_on = '结束日期不能早于开始日期。';
  return errors;
}
/** Empty optional fields are sent as null, matching the nullable contract fields. */
export function semesterPayload(values: SemesterValues) {
  return { name: values.name.trim(), academic_year: values.academic_year.trim() || null, starts_on: values.starts_on || null, ends_on: values.ends_on || null };
}

export type CourseValues = { name: string; english_name: string; semester_id: string; language: Language; teacher: string; room: string; color_token: string; icon_token: string };
export function validateCourse(values: CourseValues): FieldErrors<keyof CourseValues> {
  const errors: FieldErrors<keyof CourseValues> = {};
  if (!values.name.trim()) errors.name = '请输入课程名称。';
  else if (values.name.trim().length > 160) errors.name = '课程名称最多 160 个字符。';
  if (!values.semester_id) errors.semester_id = '请选择所属学期。';
  if (values.english_name.trim().length > 200) errors.english_name = '英文名最多 200 个字符。';
  if (values.teacher.trim().length > 100) errors.teacher = '教师最多 100 个字符。';
  if (values.room.trim().length > 100) errors.room = '教室最多 100 个字符。';
  return errors;
}
/** Optional fields may stay empty and be filled later (UI-COURSE-05). */
export function coursePayload(values: CourseValues) {
  return { semester_id: values.semester_id, name: values.name.trim(), english_name: values.english_name.trim(), language: values.language, teacher: values.teacher.trim(), room: values.room.trim(), color_token: values.color_token, icon_token: values.icon_token };
}
/** Same-name courses are allowed and stay separate; this only informs the user. */
export function sameNameCourses(courses: readonly S['Course'][], name: string, excludeId?: string) {
  const target = name.trim();
  return target ? courses.filter(course => course.name === target && course.id !== excludeId) : [];
}

export type CourseListParams = { semester: string | null; view: 'card' | 'list'; sort: 'recent' | 'name'; q: string; archived: boolean };
export function readCourseListParams(params: URLSearchParams): CourseListParams {
  return {
    semester: params.get('semester') || null,
    view: params.get('view') === 'list' ? 'list' : 'card',
    sort: params.get('sort') === 'name' ? 'name' : 'recent',
    q: (params.get('q') ?? '').slice(0, 200),
    archived: params.get('archived') === '1',
  };
}
/** Changing one control keeps all the others (UI-COURSE-03 context retention). */
export function writeCourseListParams(current: CourseListParams, change: Partial<CourseListParams>) {
  const next = { ...current, ...change };
  const params = new URLSearchParams();
  if (next.semester) params.set('semester', next.semester);
  if (next.view !== 'card') params.set('view', next.view);
  if (next.sort !== 'recent') params.set('sort', next.sort);
  if (next.q) params.set('q', next.q);
  if (next.archived) params.set('archived', '1');
  return params;
}

/** Default semester: the latest by start date, else most recently created. Deterministic. */
export function defaultSemester(semesters: readonly S['Semester'][]) {
  const live = semesters.filter(semester => !semester.archived);
  const pool = live.length ? live : [...semesters];
  return [...pool].sort((a, b) => (b.starts_on ?? '').localeCompare(a.starts_on ?? '') || b.created_at.localeCompare(a.created_at) || (a.id < b.id ? -1 : 1))[0] ?? null;
}

/** Only allowed in-app return paths (no open redirect). */
export function safeReturnTo(value: string | null, fallback: string) {
  return value && /^\/(courses|semesters|dashboard)(\/|\?|$)/.test(value) && !value.startsWith('//') ? value : fallback;
}
