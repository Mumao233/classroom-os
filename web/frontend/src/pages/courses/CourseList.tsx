import { useEffect, useState } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router';
import { IconLayoutGrid, IconList, IconPlus } from '@tabler/icons-react';
import { defaultSemester, languageLabels, readCourseListParams, writeCourseListParams, type CourseListParams } from '../../domain/courses';
import { formatDate } from '../../domain/dashboard';
import { useAppData, useCourses, useSemesters } from '../../features/data';
import { Badge, CourseGlyph, coverClass, DataSourceNote, Empty, Loading, OfflineBanner, PageHeading, QueryError } from '../common';
import shell from '../../app/Shell.module.css';
import styles from '../pages.module.css';

/** UI-COURSE-03/04: semester first; search, sort, card/list and archive filter live in the URL. */
export function CourseList() {
  const { online } = useAppData();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const params = readCourseListParams(searchParams);
  const semesters = useSemesters();
  const fallback = semesters.data ? defaultSemester(semesters.data.items) : null;
  const semesterId = params.semester === 'all' ? undefined : params.semester ?? fallback?.id;
  const courses = useCourses({ semester_id: semesterId, archived: params.archived, q: params.q || undefined, sort: params.sort }, semesters.isSuccess);
  const [query, setQuery] = useState(params.q);
  useEffect(() => { setQuery(params.q); }, [params.q]);
  // The URL update runs in a router transition; mirror the checkbox locally so it never
  // shows its old state between the click and the committed navigation.
  const [archivedOnly, setArchivedOnly] = useState(params.archived);
  useEffect(() => { setArchivedOnly(params.archived); }, [params.archived]);
  // Search is debounced into the URL so back/forward and reloads keep the same context.
  useEffect(() => {
    if (query === params.q) return;
    const handle = setTimeout(() => update({ q: query.trim() }), 300);
    return () => clearTimeout(handle);
  });
  const update = (change: Partial<CourseListParams>) => setSearchParams(writeCourseListParams({ ...params, semester: params.semester ?? fallback?.id ?? null }, change), { replace: true });
  const semesterName = new Map((semesters.data?.items ?? []).map(item => [item.id, item.name]));
  const currentSemester = semesters.data?.items.find(item => item.id === semesterId);
  const items = courses.data?.items ?? [];
  const nameCounts = new Map<string, number>();
  for (const course of items) nameCounts.set(course.name, (nameCounts.get(course.name) ?? 0) + 1);

  return <main id="main-content" className={shell.page} tabIndex={-1} data-testid="course-list">
    <PageHeading title="课程" detail="按学期整理课程，课堂记录归属于具体课程。" actions={online && semesters.data && semesters.data.items.length > 0 ? <>
      <Link className={styles.buttonSecondary} to="/semesters/new">新建学期</Link>
      <Link className={styles.button} to={`/courses/new${semesterId ? `?semester=${semesterId}` : ''}`}><IconPlus size={16} aria-hidden="true" /> 新建课程</Link></> : undefined} />
    <div className={styles.stack}>
      <OfflineBanner scope="课程列表只显示本次已读取的元数据，不含任何课堂正文。" />
      <DataSourceNote />
      {semesters.isError && !semesters.data ? <QueryError error={semesters.error} retry={() => void semesters.refetch()} /> : semesters.isPending ? <Loading label="正在读取学期…" /> :
        semesters.data.items.length === 0 ? <section className={styles.panel}><Empty title="先创建一个学期" description="课程按学期整理。可以创建当前学期，也可以补录历史学期。">{online && <Link className={styles.button} to="/semesters/new">创建第一个学期</Link>}</Empty></section> : <>
        <section className={styles.panel} aria-label="筛选与视图">
          <div className={styles.toolbar}>
            <div className={styles.field}><label htmlFor="course-semester">学期</label>
              <select id="course-semester" value={params.semester === 'all' ? 'all' : semesterId ?? ''} onChange={event => update({ semester: event.target.value })}>
                {semesters.data.items.map(item => <option key={item.id} value={item.id}>{item.name}{item.archived ? '（已归档）' : ''}</option>)}
                <option value="all">全部学期</option>
              </select></div>
            <div className={styles.field}><label htmlFor="course-search">搜索课程</label><input id="course-search" type="search" value={query} maxLength={200} placeholder="课程名或英文名" onChange={event => setQuery(event.target.value)} /></div>
            <div className={styles.field}><label htmlFor="course-sort">排序</label>
              <select id="course-sort" value={params.sort} onChange={event => update({ sort: event.target.value === 'name' ? 'name' : 'recent' })}><option value="recent">最近上课</option><option value="name">名称</option></select></div>
            <label className={styles.choice}><input type="checkbox" checked={archivedOnly} onChange={event => { setArchivedOnly(event.target.checked); update({ archived: event.target.checked }); }} /> 只看已归档</label>
            <div className={styles.segmented} role="group" aria-label="视图">
              <button type="button" aria-pressed={params.view === 'card'} onClick={() => update({ view: 'card' })}><IconLayoutGrid size={15} aria-hidden="true" /> 卡片</button>
              <button type="button" aria-pressed={params.view === 'list'} onClick={() => update({ view: 'list' })}><IconList size={15} aria-hidden="true" /> 列表</button>
            </div>
          </div>
          {currentSemester && <div className={styles.spread}><p className={`${styles.small} ${styles.muted}`}>{currentSemester.academic_year ? `${currentSemester.academic_year} 学年 · ` : ''}{currentSemester.starts_on && currentSemester.ends_on ? `${currentSemester.starts_on} 至 ${currentSemester.ends_on}` : '未设置起止日期'}</p>
            {online && <Link className={styles.buttonGhost} to={`/semesters/${currentSemester.id}/edit`}>编辑学期</Link>}</div>}
        </section>
        <section aria-live="polite" aria-busy={courses.isFetching}>
          {courses.isError && !courses.data ? <QueryError error={courses.error} retry={() => void courses.refetch()} /> : courses.isPending ? <Loading label="正在读取课程…" /> :
            items.length === 0 ? <section className={styles.panel}><Empty title={params.q ? '没有匹配的课程' : params.archived ? '没有已归档课程' : '这个学期还没有课程'} description={params.q ? '换个关键词，或切换学期后再试。' : '课程名称和学期是必填项，教师、教室、大纲和词表可以之后再补。'}>
              {online && !params.q && !params.archived && <Link className={styles.button} to={`/courses/new${semesterId ? `?semester=${semesterId}` : ''}`}>新建课程</Link>}</Empty></section> :
            params.view === 'card'
              ? <div className={styles.cards} data-testid="course-cards">{items.map(course => <Link key={course.id} to={`/courses/${course.id}/overview`} state={{ from: `${location.pathname}${location.search}` }} className={styles.card} data-course-id={course.id}>
                  <div className={`${styles.cover} ${coverClass(course.color_token)}`} />
                  <div className={styles.cardBody}><CourseGlyph course={course} />
                    <h2 className={styles.cardTitle}>{course.name}</h2>
                    {course.english_name && <p className={styles.cardMeta} lang="en">{course.english_name}</p>}
                    <p className={styles.cardMeta}>{languageLabels[course.language]} · {course.class_session_count} 节课堂{params.semester === 'all' ? ` · ${semesterName.get(course.semester_id) ?? ''}` : ''}</p>
                    <p className={styles.cardMeta}>{course.last_class_session_at ? `最近课堂 ${formatDate(course.last_class_session_at)}` : '还没有课堂记录'}</p>
                    <div className={styles.row}>{course.archived && <Badge>已归档</Badge>}{(nameCounts.get(course.name) ?? 0) > 1 && <Badge tone="info">同名课程 · 各自独立</Badge>}</div>
                  </div></Link>)}</div>
              : <div className={`${styles.panel} ${styles.tableWrap}`}><table className={styles.table} data-testid="course-table">
                  <caption className={styles.srOnly}>课程列表</caption>
                  <thead><tr><th scope="col">课程</th><th scope="col">语言</th><th scope="col">课堂数</th><th scope="col">最近课堂</th><th scope="col">状态</th></tr></thead>
                  <tbody>{items.map(course => <tr key={course.id} data-course-id={course.id}>
                    <td><Link to={`/courses/${course.id}/overview`} state={{ from: `${location.pathname}${location.search}` }}>{course.name}</Link>{course.english_name && <div className={styles.muted} lang="en">{course.english_name}</div>}</td>
                    <td>{languageLabels[course.language]}</td><td>{course.class_session_count}</td>
                    <td>{course.last_class_session_at ? formatDate(course.last_class_session_at) : '—'}</td>
                    <td>{course.archived ? '已归档' : '进行中'}{(nameCounts.get(course.name) ?? 0) > 1 ? ' · 同名独立' : ''}</td></tr>)}</tbody>
                </table></div>}
        </section>
      </>}
    </div>
  </main>;
}
