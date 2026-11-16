import { type ReactNode, useEffect, useRef, useState } from 'react';
import { Link, Navigate, useLocation, useNavigate, useParams, useSearchParams } from 'react-router';
import { useForm, type FieldErrors as RHFErrors, type Resolver } from 'react-hook-form';
import { useMutation } from '@tanstack/react-query';
import type { components } from '../../generated/openapi';
import { ApiClientError } from '../../api/client';
import { colorLabels, colorTokens, coursePayload, DEFAULT_LANGUAGE, defaultSemester, iconLabels, iconTokens, languageLabels, sameNameCourses, semesterPayload, validateCourse, validateSemester, type CourseValues, type SemesterValues } from '../../domain/courses';
import { describeError, isConflict, useAppData, useCourse, useCourses, useInvalidateAccount, useSemesters } from '../../features/data';
import { DataSourceNote, Loading, OfflineBanner, PageHeading, QueryError } from '../common';
import { DeleteDialog } from './DeleteDialog';
import shell from '../../app/Shell.module.css';
import styles from '../pages.module.css';

type S = components['schemas'];

function resolverFor<T extends Record<string, unknown>>(validate: (values: T) => Partial<Record<keyof T, string>>): Resolver<T> {
  return async values => {
    const errors = validate(values);
    const keys = Object.keys(errors) as Array<keyof T>;
    if (!keys.length) return { values, errors: {} };
    return { values: {}, errors: Object.fromEntries(keys.map(key => [key, { type: 'validate', message: errors[key] }])) as RHFErrors<T> };
  };
}

/** One key per user intent; a changed body after a failure is a new intent (no IDEMPOTENCY_MISMATCH). */
function useIntentKey() {
  const last = useRef<{ body: string; key: string } | null>(null);
  return (body: unknown) => {
    const serialized = JSON.stringify(body);
    if (!last.current || last.current.body !== serialized) last.current = { body: serialized, key: crypto.randomUUID() };
    return last.current.key;
  };
}

function Field({ id, label, error, hint, children }: { id: string; label: string; error?: string; hint?: string; children: ReactNode }) {
  return <div className={styles.field}><label htmlFor={id}>{label}</label>{children}
    {(error || hint) && <span id={`${id}-desc`} className={error ? styles.error : styles.hint}>{error ?? hint}</span>}</div>;
}
const described = (id: string, error?: string, hint?: string) => ({ id, 'aria-invalid': error ? true as const : undefined, 'aria-describedby': error || hint ? `${id}-desc` : undefined });

/** Conflict view: the user's input is kept next to the server's latest values; nothing is overwritten silently. */
function ConflictPanel({ rows, onOverwrite, onDiscard, busy }: { rows: Array<{ label: string; mine: string; latest: string }>; onOverwrite: () => void; onDiscard: () => void; busy: boolean }) {
  return <div className={`${styles.banner} ${styles.bannerWarning}`} role="alert" data-testid="conflict-panel"><div className={styles.stack}>
    <strong>版本冲突：内容已在其他地方更新，你的输入仍保留。</strong>
    <div className={styles.compare}>
      <div><strong>你的输入</strong>{rows.map(row => <p key={row.label}>{row.label}：{row.mine || '（空）'}</p>)}</div>
      <div><strong>最新版本</strong>{rows.map(row => <p key={row.label}>{row.label}：{row.latest || '（空）'}</p>)}</div>
    </div>
    <div className={styles.row}><button type="button" className={styles.button} disabled={busy} onClick={onOverwrite}>以我的输入覆盖最新版本</button><button type="button" className={styles.buttonSecondary} onClick={onDiscard}>放弃我的修改，载入最新版本</button></div>
  </div></div>;
}

/**
 * True while this form and the account scope it was opened in are still current. An account
 * switch remounts the whole scope, so a save that finishes afterwards must not navigate.
 */
function useStillCurrent() {
  const { accountKey } = useAppData();
  const opened = useRef(accountKey);
  const latestKey = useRef(accountKey);
  const mounted = useRef(true);
  useEffect(() => { latestKey.current = accountKey; }, [accountKey]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  return () => mounted.current && latestKey.current === opened.current;
}

export function SemesterFormPage() {
  const { semesterId } = useParams();
  const semesters = useSemesters();
  if (!semesterId) return <SemesterForm />;
  if (semesters.isPending) return <main id="main-content" className={shell.page} tabIndex={-1}><Loading label="正在读取学期…" /></main>;
  // A failed background refetch keeps the cached list so a mounted form never loses its draft.
  if (!semesters.data) return <main id="main-content" className={shell.page} tabIndex={-1}><QueryError error={semesters.error} retry={() => void semesters.refetch()} /></main>;
  const found = semesters.data.items.find(item => item.id === semesterId);
  if (!found) return <main id="main-content" className={shell.page} tabIndex={-1}><PageHeading title="学期不存在" detail="该学期可能已被删除。" focusKey="missing" /><Link className={styles.buttonSecondary} to="/courses">返回课程</Link></main>;
  // Keyed by id only: a background refetch must never remount the form and drop the user's input.
  return <SemesterForm key={found.id} semester={found} others={semesters.data.items.filter(item => item.id !== found.id)} />;
}

function SemesterForm({ semester, others = [] }: { semester?: S['Semester']; others?: S['Semester'][] }) {
  const { api, online } = useAppData();
  const invalidate = useInvalidateAccount();
  const navigate = useNavigate();
  const stillCurrent = useStillCurrent();
  const intentKey = useIntentKey();
  // The revision this form was opened with; background refetches never move it silently.
  const [baseRevision, setBaseRevision] = useState(semester?.revision ?? 0);
  const [latest, setLatest] = useState<S['Semester'] | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const form = useForm<SemesterValues>({
    defaultValues: { name: semester?.name ?? '', academic_year: semester?.academic_year ?? '', starts_on: semester?.starts_on ?? '', ends_on: semester?.ends_on ?? '' },
    resolver: resolverFor(validateSemester),
  });
  const save = useMutation({
    mutationFn: async ({ values, revision }: { values: SemesterValues; revision: number }) => {
      const body = semesterPayload(values);
      return semester ? api.updateSemester(semester.id, { base_revision: revision, ...body }) : api.createSemester(body, intentKey(body));
    },
    onSuccess: async saved => { setLatest(null); await invalidate(); if (stillCurrent()) navigate(`/courses?semester=${saved.id}`); },
    onError: async error => { if (isConflict(error) && semester) setLatest(await api.semester(semester.id)); },
  });
  const archive = useMutation({
    mutationFn: () => api.updateSemester(semester!.id, { base_revision: baseRevision, archived: !semester!.archived }),
    onSuccess: async saved => { setBaseRevision(saved.revision); await invalidate(); },
  });
  const errors = form.formState.errors;
  const submit = form.handleSubmit(values => save.mutate({ values, revision: baseRevision }));
  const values = form.watch();
  return <main id="main-content" className={shell.page} tabIndex={-1}>
    <PageHeading title={semester ? `编辑学期` : '新建学期'} detail={semester ? semester.name : '支持当前学期，也可以补录历史学期。'} focusKey={semester?.id ?? 'new'} />
    <div className={styles.stack}>
      <OfflineBanner scope="学期信息只读。" /><DataSourceNote />
      <form className={`${styles.panel} ${styles.form}`} onSubmit={submit} noValidate aria-label="学期信息">
        <fieldset disabled={save.isPending} className={styles.stack} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>{/* Locked while saving so no edit made after submit is lost when the page navigates away. */}
        <Field id="semester-name" label="学期名称（必填）" error={errors.name?.message}><input {...form.register('name')} {...described('semester-name', errors.name?.message)} maxLength={100} autoComplete="off" /></Field>
        <Field id="semester-year" label="学年" error={errors.academic_year?.message} hint="例如 2026-2027，可留空。"><input {...form.register('academic_year')} {...described('semester-year', errors.academic_year?.message, 'hint')} maxLength={30} /></Field>
        <div className={styles.formGrid}>
          <Field id="semester-start" label="开始日期" error={errors.starts_on?.message}><input type="date" {...form.register('starts_on')} {...described('semester-start', errors.starts_on?.message)} /></Field>
          <Field id="semester-end" label="结束日期" error={errors.ends_on?.message}><input type="date" {...form.register('ends_on')} {...described('semester-end', errors.ends_on?.message)} /></Field>
        </div>
        </fieldset>
        {latest && semester && <ConflictPanel busy={save.isPending}
          rows={[{ label: '名称', mine: values.name, latest: latest.name }, { label: '学年', mine: values.academic_year, latest: latest.academic_year ?? '' }, { label: '日期', mine: `${values.starts_on || '—'} 至 ${values.ends_on || '—'}`, latest: `${latest.starts_on ?? '—'} 至 ${latest.ends_on ?? '—'}` }]}
          onOverwrite={() => { setBaseRevision(latest.revision); void form.handleSubmit(v => save.mutate({ values: v, revision: latest.revision }))(); }}
          onDiscard={() => { setBaseRevision(latest.revision); form.reset({ name: latest.name, academic_year: latest.academic_year ?? '', starts_on: latest.starts_on ?? '', ends_on: latest.ends_on ?? '' }); setLatest(null); save.reset(); }} />}
        {save.isError && !latest && <p role="alert" className={styles.error}>{describeError(save.error)}</p>}
        <div className={styles.row}>
          <button type="submit" className={styles.button} disabled={!online || save.isPending}>{save.isPending ? '正在保存…' : '保存'}</button>
          <Link className={styles.buttonSecondary} to={semester ? `/courses?semester=${semester.id}` : '/courses'}>取消</Link>
        </div>
      </form>
      {semester && <section className={styles.panel} aria-labelledby="semester-manage"><h2 id="semester-manage">归档与删除</h2>
        <p className={`${styles.small} ${styles.muted}`}>归档只是从默认视图隐藏，课程和课堂全部保留；删除需要先查看影响范围。</p>
        {archive.isError && <p role="alert" className={styles.error}>{describeError(archive.error)}</p>}
        <div className={styles.row}><button type="button" className={styles.buttonSecondary} disabled={!online || archive.isPending} onClick={() => archive.mutate()}>{semester.archived ? '取消归档' : '归档学期'}</button>
          <button type="button" className={styles.buttonDanger} disabled={!online} onClick={() => setDeleteOpen(true)}>删除学期…</button></div>
        <DeleteDialog open={deleteOpen} onOpenChange={setDeleteOpen} target={{ kind: 'semester', id: semester.id, name: semester.name, revision: baseRevision }}
          destinations={others.map(item => ({ id: item.id, name: item.name }))} onDeleted={() => navigate('/courses', { replace: true })} />
      </section>}
    </div>
  </main>;
}

export function CourseFormPage() {
  const { courseId } = useParams();
  const [searchParams] = useSearchParams();
  const semesters = useSemesters();
  const course = useCourse(courseId ?? '', Boolean(courseId));
  const existing = useCourses({ semester_id: undefined }, semesters.isSuccess);
  if (semesters.isPending || (courseId && course.isPending)) return <main id="main-content" className={shell.page} tabIndex={-1}><Loading label="正在读取…" /></main>;
  if (!semesters.data) return <main id="main-content" className={shell.page} tabIndex={-1}><QueryError error={semesters.error} retry={() => void semesters.refetch()} /></main>;
  const gone = Boolean(courseId) && course.isError && course.error instanceof ApiClientError && (course.error.status === 404 || course.error.status === 410);
  if (courseId && (gone || !course.data)) {
    return <main id="main-content" className={shell.page} tabIndex={-1}><PageHeading title={gone ? '课程不存在' : '无法读取课程'} focusKey="error" />{gone ? <Link className={styles.buttonSecondary} to="/courses">返回课程</Link> : <QueryError error={course.error} retry={() => void course.refetch()} />}</main>;
  }
  if (!courseId && semesters.data.items.length === 0) return <Navigate to="/semesters/new" replace />;
  const fallback = searchParams.get('semester') && semesters.data.items.some(item => item.id === searchParams.get('semester')) ? searchParams.get('semester')! : defaultSemester(semesters.data.items)?.id ?? '';
  return <CourseForm key={course.data?.id ?? 'new'} course={courseId ? course.data : undefined} semesters={semesters.data.items} defaultSemesterId={fallback} existing={existing.data?.items ?? []} />;
}

function CourseForm({ course, semesters, defaultSemesterId, existing }: { course?: S['Course']; semesters: S['Semester'][]; defaultSemesterId: string; existing: S['Course'][] }) {
  const { api, online } = useAppData();
  const invalidate = useInvalidateAccount();
  const navigate = useNavigate();
  const stillCurrent = useStillCurrent();
  const intentKey = useIntentKey();
  // The course-list return context travels through the editor and back.
  const returnState = useLocation().state;
  const [baseRevision, setBaseRevision] = useState(course?.revision ?? 0);
  const [latest, setLatest] = useState<S['Course'] | null>(null);
  const form = useForm<CourseValues>({
    defaultValues: course
      ? { name: course.name, english_name: course.english_name, semester_id: course.semester_id, language: course.language, teacher: course.teacher, room: course.room, color_token: course.color_token, icon_token: course.icon_token }
      : { name: '', english_name: '', semester_id: defaultSemesterId, language: DEFAULT_LANGUAGE, teacher: '', room: '', color_token: 'blue', icon_token: 'book' },
    resolver: resolverFor(validateCourse),
  });
  const save = useMutation({
    mutationFn: async ({ values, revision }: { values: CourseValues; revision: number }) => {
      const body = coursePayload(values);
      return course ? api.updateCourse(course.id, { base_revision: revision, ...body }) : api.createCourse(body, intentKey(body));
    },
    onSuccess: async saved => { setLatest(null); await invalidate(); if (stillCurrent()) navigate(`/courses/${saved.id}/overview`, { replace: !course, state: returnState }); },
    onError: async error => { if (isConflict(error) && course) setLatest(await api.course(course.id)); },
  });
  const errors = form.formState.errors;
  const values = form.watch();
  const sameName = sameNameCourses(existing, values.name, course?.id);
  const languageChanged = course && values.language !== course.language;
  const semesterName = (id: string) => semesters.find(item => item.id === id)?.name ?? '';
  return <main id="main-content" className={shell.page} tabIndex={-1}>
    <PageHeading title={course ? '编辑课程' : '新建课程'} detail={course ? course.name : '名称和学期必填；教师、教室、大纲和词表都可以之后再补。'} focusKey={course?.id ?? 'new'} />
    <div className={styles.stack}>
      <OfflineBanner scope="课程信息只读。" /><DataSourceNote />
      <form className={`${styles.panel} ${styles.form}`} onSubmit={form.handleSubmit(v => save.mutate({ values: v, revision: baseRevision }))} noValidate aria-label="课程信息">
        <fieldset disabled={save.isPending} className={styles.stack} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>{/* Locked while saving so no edit made after submit is lost when the page navigates away. */}
        <Field id="course-name" label="课程名称（必填）" error={errors.name?.message} hint={sameName.length ? `已有 ${sameName.length} 门同名课程。保存后会是一门独立的新课程，不会合并。` : undefined}>
          <input {...form.register('name')} {...described('course-name', errors.name?.message, sameName.length ? 'hint' : undefined)} maxLength={160} autoComplete="off" /></Field>
        <Field id="course-english" label="英文名" error={errors.english_name?.message}><input {...form.register('english_name')} {...described('course-english', errors.english_name?.message)} maxLength={200} lang="en" /></Field>
        <div className={styles.formGrid}>
          <Field id="course-semester" label="所属学期（必填）" error={errors.semester_id?.message}>
            <select {...form.register('semester_id')} {...described('course-semester', errors.semester_id?.message)}><option value="">请选择</option>{semesters.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
          <Field id="course-language" label="课堂语言" hint={languageChanged ? '修改语言只影响之后新建课堂的默认值，不会重新识别或重新生成历史结果。' : '默认自动识别。'}>
            <select {...form.register('language')} {...described('course-language', undefined, 'hint')}>{(Object.keys(languageLabels) as Array<keyof typeof languageLabels>).map(key => <option key={key} value={key}>{languageLabels[key]}</option>)}</select></Field>
          <Field id="course-teacher" label="教师" error={errors.teacher?.message}><input {...form.register('teacher')} {...described('course-teacher', errors.teacher?.message)} maxLength={100} /></Field>
          <Field id="course-room" label="教室" error={errors.room?.message}><input {...form.register('room')} {...described('course-room', errors.room?.message)} maxLength={100} /></Field>
          <Field id="course-color" label="课程颜色"><select {...form.register('color_token')} id="course-color">{colorTokens.map(key => <option key={key} value={key}>{colorLabels[key]}</option>)}{!(colorTokens as readonly string[]).includes(values.color_token) && <option value={values.color_token}>{values.color_token}</option>}</select></Field>
          <Field id="course-icon" label="课程图标"><select {...form.register('icon_token')} id="course-icon">{iconTokens.map(key => <option key={key} value={key}>{iconLabels[key]}</option>)}{!(iconTokens as readonly string[]).includes(values.icon_token) && <option value={values.icon_token}>{values.icon_token}</option>}</select></Field>
        </div>
        </fieldset>
        <p className={`${styles.small} ${styles.muted}`}>课程大纲和术语词表在课程详情的“资料”标签中维护。</p>
        {latest && course && <ConflictPanel busy={save.isPending}
          rows={[{ label: '名称', mine: values.name, latest: latest.name }, { label: '英文名', mine: values.english_name, latest: latest.english_name }, { label: '学期', mine: semesterName(values.semester_id), latest: semesterName(latest.semester_id) }, { label: '语言', mine: languageLabels[values.language], latest: languageLabels[latest.language] }, { label: '教师/教室', mine: `${values.teacher} ${values.room}`.trim(), latest: `${latest.teacher} ${latest.room}`.trim() }]}
          onOverwrite={() => { setBaseRevision(latest.revision); void form.handleSubmit(v => save.mutate({ values: v, revision: latest.revision }))(); }}
          onDiscard={() => { setBaseRevision(latest.revision); form.reset({ name: latest.name, english_name: latest.english_name, semester_id: latest.semester_id, language: latest.language, teacher: latest.teacher, room: latest.room, color_token: latest.color_token, icon_token: latest.icon_token }); setLatest(null); save.reset(); }} />}
        {save.isError && !latest && <p role="alert" className={styles.error}>{describeError(save.error)}</p>}
        <div className={styles.row}>
          <button type="submit" className={styles.button} disabled={!online || save.isPending}>{save.isPending ? '正在保存…' : course ? '保存' : '创建课程'}</button>
          <Link className={styles.buttonSecondary} to={course ? `/courses/${course.id}/overview` : '/courses'} state={returnState}>取消</Link>
        </div>
      </form>
    </div>
  </main>;
}
