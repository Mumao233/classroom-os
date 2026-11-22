import { useMemo } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router';
import { useMutation } from '@tanstack/react-query';
import type { components } from '../../generated/openapi';
import { filtersToParams, kindLabel, narrowFilters, parseFilters, taskFirst, undecidedCandidates, type Doc, type LibraryFilters } from '../../domain/review';
import { describeError, isConflict, useAppData, useClassSessions, useCourses, useInvalidateAccount, useQuestionsFor, useSemesters } from '../../features/data';
import { useReadingMemory, useReviewLibrary } from '../../features/review';
import { DataSourceNote, Empty, Loading, OfflineBanner, PageHeading, QueryError } from '../common';
import pages from '../pages.module.css';
import styles from './review.module.css';

type S = components['schemas'];

export const docState = (doc: Doc) => doc.adopted_revision_id ? (undecidedCandidates(doc).length ? '已采用 · 有新候选' : '已采用版本') : doc.latest_personal_revision_id ? '个人稿' : doc.candidate_revision_ids.length ? '候选未采用' : '尚无版本';

/**
 * UI-REVIEW-01/02/03: task-first entry (continue / needs a decision / unresolved) above the full
 * library. Filters live in the URL so returning from a document restores the same view.
 */
export function ReviewHub() {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const filters = parseFilters(params);
  const all = useReviewLibrary({});
  const library = useReviewLibrary(filters);
  const semesters = useSemesters();
  const courses = useCourses({});
  const archived = useCourses({ archived: true });
  const sessions = useClassSessions();
  const memory = useReadingMemory();
  const sessionList = useMemo(() => sessions.data?.items ?? [], [sessions.data]);
  const questions = useQuestionsFor(sessionList.map(item => item.id));
  const sessionById = useMemo(() => new Map(sessionList.map(item => [item.id, item])), [sessionList]);
  // Archived courses keep their documents: they stay in the filters and labels.
  const allCourses = useMemo(() => [...(courses.data?.items ?? []), ...(archived.data?.items ?? [])].filter((item, index, list) => list.findIndex(other => other.id === item.id) === index), [courses.data, archived.data]);
  const courseById = useMemo(() => new Map(allCourses.map(item => [item.id, item])), [allCourses]);
  const where = (classId: string) => { const session = sessionById.get(classId); const course = session ? courseById.get(session.course_id) : undefined; return `${course?.name ?? '课程'} · ${session?.title || '课堂'}`; };
  const tasks = taskFirst(all.data?.items ?? [], questions.items, memory.read().last);
  const from = `${location.pathname}${location.search}`;
  const set = (change: Partial<LibraryFilters>) => setParams(filtersToParams(narrowFilters(filters, change)), { replace: true });
  const visibleCourses = allCourses.filter(item => !filters.semester_id || item.semester_id === filters.semester_id);
  const visibleSessions = sessionList.filter(item => (!filters.course_id || item.course_id === filters.course_id) && (!filters.semester_id || courseById.get(item.course_id)?.semester_id === filters.semester_id));

  return <main id="main-content" className={styles.page}>
    <PageHeading title="复习" detail="先处理需要你决定的内容，完整资料库在下方。" focusKey="review" />
    <DataSourceNote />
    <OfflineBanner scope="复习资料只读显示已加载的内容；" />
    {all.isError && !all.data ? <QueryError error={all.error} retry={() => void all.refetch()} /> : all.isPending ? <Loading label="正在读取复习资料…" /> : <>
      <div className={pages.lowerGrid}>
        <section className={pages.panel} aria-labelledby="hub-continue"><h2 id="hub-continue">继续</h2>
          {tasks.continueDoc ? <><p className={pages.wrap}><strong>{tasks.continueDoc.title || '未命名'}</strong></p><p className={`${pages.small} ${pages.muted}`}>{kindLabel[tasks.continueDoc.kind]} · {where(tasks.continueDoc.class_session_id)}</p>
            <Link className={pages.button} to={`/review/items/${tasks.continueDoc.id}`} state={{ from }} data-testid="continue-reading">继续阅读</Link></>
            : <Empty title="还没有最近阅读" description="打开任意资料后，这里会记住本机最近读到的位置。" />}
        </section>
        <section className={pages.panel} aria-labelledby="hub-pending"><h2 id="hub-pending">待处理</h2>
          {tasks.pending.length === 0 ? <Empty title="没有待决定的内容" description="新的 AI 候选或来源变化会出现在这里。" /> :
            <ul className={pages.list} data-testid="pending-list">{tasks.pending.map(({ doc, reasons }) => <li key={doc.id} className={pages.listItem}>
              <div><strong>{doc.title || '未命名'}</strong><p>{reasons.join('；')}</p><p>{where(doc.class_session_id)}</p></div>
              <Link to={`/review/items/${doc.id}`} state={{ from }}>查看</Link></li>)}</ul>}
        </section>
      </div>
      <section className={pages.panel} aria-labelledby="hub-unresolved"><h2 id="hub-unresolved">未解决</h2>
        <p className={`${pages.small} ${pages.muted}`}>尚未标记为已理解的资料和疑问。“已理解”只由你手动标记。</p>
        {tasks.unresolvedDocs.length === 0 && tasks.unresolvedQuestions.length === 0 ? <Empty title="没有未解决的内容" description="全部资料和疑问都已标记为已理解。" /> :
          <ul className={pages.list} data-testid="unresolved-list">
            {tasks.unresolvedDocs.map(doc => <li key={doc.id} className={pages.listItem}><div><strong>{doc.title || '未命名'}</strong><p>{kindLabel[doc.kind]} · {where(doc.class_session_id)}</p></div><Link to={`/review/items/${doc.id}`} state={{ from }}>阅读</Link></li>)}
            {tasks.unresolvedQuestions.map(question => <QuestionRow key={question.id} question={question} where={where(question.class_session_id)} />)}
          </ul>}
        {questions.error && <p className={`${pages.small} ${pages.warning}`}>部分课堂的疑问暂时无法读取。</p>}
      </section>
    </>}

    <section className={pages.panel} aria-labelledby="hub-library"><h2 id="hub-library">资料库</h2>
      <div className={pages.toolbar} role="group" aria-label="资料库筛选">
        <div className={pages.field}><label htmlFor="lib-semester">学期</label><select id="lib-semester" value={filters.semester_id ?? ''} onChange={event => set({ semester_id: event.target.value || undefined })}><option value="">全部学期</option>{(semesters.data?.items ?? []).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div>
        <div className={pages.field}><label htmlFor="lib-course">课程</label><select id="lib-course" value={filters.course_id ?? ''} onChange={event => set({ course_id: event.target.value || undefined })}><option value="">全部课程</option>{visibleCourses.map(item => <option key={item.id} value={item.id}>{item.name}{item.archived ? '（已归档）' : ''}</option>)}</select></div>
        <div className={pages.field}><label htmlFor="lib-class">课堂</label><select id="lib-class" value={filters.class_session_id ?? ''} onChange={event => set({ class_session_id: event.target.value || undefined })}><option value="">全部课堂</option>{visibleSessions.map(item => <option key={item.id} value={item.id}>{item.title || '未命名课堂'}</option>)}</select></div>
        <div className={pages.field}><label htmlFor="lib-kind">类型</label><select id="lib-kind" value={filters.kind ?? ''} onChange={event => set({ kind: (event.target.value || undefined) as LibraryFilters['kind'] })}><option value="">全部</option><option value="summary">课堂总结</option><option value="card">复习卡片</option><option value="note">个人笔记</option></select></div>
        <div className={pages.field}><label htmlFor="lib-understood">理解状态</label><select id="lib-understood" value={filters.understood === undefined ? '' : String(filters.understood)} onChange={event => set({ understood: event.target.value === '' ? undefined : event.target.value === 'true' })}><option value="">全部</option><option value="false">尚未理解</option><option value="true">已理解</option></select></div>
      </div>
      {library.isError && !library.data ? <QueryError error={library.error} retry={() => void library.refetch()} /> : library.isPending ? <Loading label="正在读取资料库…" /> :
        library.data.items.length === 0 ? <Empty title={Object.keys(filters).length ? '没有符合筛选的资料' : '还没有复习资料'} description="课堂结束并生成或保存资料后会出现在这里。" /> :
          <ul className={pages.list} data-testid="library-list">{library.data.items.map(doc => <li key={doc.id} className={pages.listItem} data-document-id={doc.id}>
            <div><strong>{doc.title || '未命名'}</strong><p>{kindLabel[doc.kind]} · {docState(doc)} · {doc.understood ? '已理解' : '尚未理解'}</p><p>{where(doc.class_session_id)}</p></div>
            <Link to={`/review/items/${doc.id}`} state={{ from }}>打开</Link></li>)}</ul>}
      <p className={`${pages.small} ${pages.muted}`}>全局、课程和单课三个入口显示的是同一批资料；在任一入口的修改都会同步出现。正式文本搜索在后续版本提供。</p>
    </section>
  </main>;
}

function QuestionRow({ question, where }: { question: S['Question']; where: string }) {
  const app = useAppData();
  const invalidate = useInvalidateAccount();
  const toggle = useMutation({ mutationFn: () => app.api.updateQuestion(question.id, { base_revision: question.revision, understood: true }), onSettled: () => void invalidate() });
  return <li className={pages.listItem} data-question-id={question.id}>
    <div><strong>{question.question}</strong><p>疑问 · {where}</p>{question.answer_markdown && <p>{question.answer_markdown}</p>}
      {toggle.isError && <p role="alert" className={pages.error}>{isConflict(toggle.error) ? '这条疑问已在其他地方更新，请刷新后再标记。' : describeError(toggle.error)}</p>}</div>
    <button type="button" className={pages.buttonGhost} disabled={!app.online || toggle.isPending} onClick={() => toggle.mutate()}>标记已理解</button>
    <Link to={`/classrooms/${question.class_session_id}/record`} aria-label={`在课堂记录中查看疑问：${question.question}`}>查看</Link>
  </li>;
}
