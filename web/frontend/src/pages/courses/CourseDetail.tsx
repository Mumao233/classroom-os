import { useEffect, useRef, useState } from 'react';
import { Link, Navigate, NavLink, useLocation, useNavigate, useParams, useSearchParams } from 'react-router';
import { useMutation } from '@tanstack/react-query';
import { IconArchive, IconEdit, IconPlus, IconTrash, IconX } from '@tabler/icons-react';
import type { components } from '../../generated/openapi';
import { ApiClientError } from '../../api/client';
import { languageLabels, safeReturnTo } from '../../domain/courses';
import { formatDateTime, formatDuration, recentSessions } from '../../domain/dashboard';
import { describeError, isConflict, useAppData, useClassSessions, useCourse, useCourses, useInvalidateAccount, useMaterials, useQuestionsFor, useReviewDocuments, useSemesters } from '../../features/data';
import { Dialog } from '../../ui';
import { Badge, CourseGlyph, coverClass, DataSourceNote, Empty, FlowSummaryView, Loading, OfflineBanner, PageHeading, QueryError } from '../common';
import { DeleteDialog } from './DeleteDialog';
import shell from '../../app/Shell.module.css';
import styles from '../pages.module.css';

type S = components['schemas'];
const tabs = [{ key: 'overview', label: '概览' }, { key: 'classrooms', label: '课堂' }, { key: 'materials', label: '资料' }, { key: 'review', label: '复习' }] as const;
type TabKey = (typeof tabs)[number]['key'];

/** UI-COURSE-06: four fixed, linkable tabs. Focus moves to the tab panel heading on switch. */
export function CourseDetail() {
  const { courseId = '', tab = 'overview' } = useParams();
  const { online, api } = useAppData();
  const location = useLocation();
  const navigate = useNavigate();
  const invalidate = useInvalidateAccount();
  const course = useCourse(courseId);
  const semesters = useSemesters();
  const siblings = useCourses({ semester_id: undefined });
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [archiveError, setArchiveError] = useState<unknown>(null);
  const from = safeReturnTo((location.state as { from?: string } | null)?.from ?? null, '/courses');
  const archive = useMutation({
    mutationFn: (value: S['Course']) => api.updateCourse(value.id, { base_revision: value.revision, archived: !value.archived }),
    onSuccess: async () => { setArchiveError(null); await invalidate(); },
    onError: async error => { setArchiveError(error); if (isConflict(error)) await invalidate(); },
  });
  if (!tabs.some(item => item.key === tab)) return <Navigate to={`/courses/${courseId}/overview`} replace />;
  if (course.isPending) return <main id="main-content" className={shell.page} tabIndex={-1}><Loading label="正在读取课程…" /></main>;
  const gone = course.isError && course.error instanceof ApiClientError && (course.error.status === 404 || course.error.status === 410);
  // Cached data survives a failed refetch (offline), but never a confirmed deletion.
  if (gone || (course.isError && !course.data)) {
    // A deleted course leaves no stale entry: the page says so and offers only a way back.
    return <main id="main-content" className={shell.page} tabIndex={-1}><PageHeading title={gone ? '课程不存在或已删除' : '无法读取课程'} focusKey="error" />
      {gone ? <Link className={styles.buttonSecondary} to="/courses">返回课程列表</Link> : <QueryError error={course.error} retry={() => void course.refetch()} />}</main>;
  }
  const data = course.data;
  const semester = semesters.data?.items.find(item => item.id === data.semester_id);
  return <main id="main-content" className={shell.page} tabIndex={-1} data-testid="course-detail" data-course-id={data.id}>
    <p className={styles.small}><Link to={from}>← 课程列表</Link></p>
    <div className={`${styles.panel}`} style={{ padding: 0, overflow: 'hidden', marginBottom: 16 }}>
      <div className={`${styles.cover} ${coverClass(data.color_token)}`} />
      <div style={{ padding: '0 20px 18px' }}>
        <CourseGlyph course={data} />
        <PageHeading title={data.name} detail={<>{data.english_name && <span lang="en">{data.english_name} · </span>}{semester?.name ?? '学期'} · {languageLabels[data.language]}{data.teacher ? ` · ${data.teacher}` : ''}{data.room ? ` · ${data.room}` : ''}</>}
          actions={online ? <>
            <Link className={styles.buttonSecondary} to={`/courses/${data.id}/edit`} state={location.state}><IconEdit size={15} aria-hidden="true" /> 编辑</Link>
            <button type="button" className={styles.buttonSecondary} disabled={archive.isPending} onClick={() => archive.mutate(data)}><IconArchive size={15} aria-hidden="true" /> {data.archived ? '取消归档' : '归档'}</button>
            <button type="button" className={styles.buttonDanger} onClick={() => setDeleteOpen(true)}><IconTrash size={15} aria-hidden="true" /> 删除…</button></> : undefined} />
        <div className={styles.row}>{data.archived && <Badge>已归档 · 内容全部保留</Badge>}<Badge>{data.class_session_count} 节课堂</Badge></div>
        {archiveError !== null && <p role="alert" className={styles.error}>{describeError(archiveError)}</p>}
      </div>
    </div>
    <OfflineBanner scope="课程只读，显示本次已读取的元数据。" />
    <DataSourceNote />
    <nav className={styles.tabs} aria-label="课程内容">{tabs.map(item => <NavLink key={item.key} to={`/courses/${data.id}/${item.key}`} state={location.state} replace className={styles.tab} end>{item.label}</NavLink>)}</nav>
    <TabPanel key={tab} tab={tab as TabKey} course={data} />
    <DeleteDialog open={deleteOpen} onOpenChange={setDeleteOpen} target={{ kind: 'course', id: data.id, name: data.name, revision: data.revision }}
      destinations={(siblings.data?.items ?? []).filter(item => item.id !== data.id).map(item => ({ id: item.id, name: item.name }))} onDeleted={() => navigate('/courses', { replace: true })} />
  </main>;
}

function TabPanel({ tab, course }: { tab: TabKey; course: S['Course'] }) {
  const ref = useRef<HTMLHeadingElement>(null);
  // Focus only after a user switch (not on first load) so screen readers hear the new panel.
  const mounted = useRef(false);
  useEffect(() => { if (mounted.current || document.activeElement?.closest('nav')) ref.current?.focus({ preventScroll: true }); mounted.current = true; }, []);
  const label = tabs.find(item => item.key === tab)!.label;
  return <section aria-labelledby="course-tab-heading" data-tab={tab}>
    <h2 id="course-tab-heading" ref={ref} tabIndex={-1} className={styles.srOnly}>{label}</h2>
    {tab === 'overview' && <Overview course={course} />}
    {tab === 'classrooms' && <Classrooms course={course} />}
    {tab === 'materials' && <Materials course={course} />}
    {tab === 'review' && <ReviewTab course={course} />}
  </section>;
}

/** UI-COURSE-07: everything here is a real object of this course. */
function Overview({ course }: { course: S['Course'] }) {
  const sessions = useClassSessions(course.id);
  const documents = useReviewDocuments({ course_id: course.id });
  const recent = recentSessions(sessions.data?.items ?? [], [course], 5);
  const questions = useQuestionsFor(recent.map(item => item.id));
  if (sessions.isError && !sessions.data) return <QueryError error={sessions.error} retry={() => void sessions.refetch()} />;
  if (sessions.isPending) return <Loading label="正在读取课堂…" />;
  const latest = recent[0];
  const sessionTitle = new Map((sessions.data.items).map(item => [item.id, item.title || '未命名课堂']));
  const open = questions.items.filter(item => !item.understood && item.state === 'succeeded');
  const docs = documents.data?.items ?? [];
  return <div className={styles.stack}>
    <div className={styles.lowerGrid}>
      <section className={styles.panel} aria-labelledby="ov-continue"><h3 id="ov-continue">继续学习</h3>
        {latest ? <><p className={styles.wrap}><strong>{latest.title || '未命名课堂'}</strong></p><p className={`${styles.small} ${styles.muted}`}>{formatDateTime(latest.started_at ?? latest.created_at)}</p><FlowSummaryView flows={latest.flows} />
          <div className={styles.row} style={{ marginTop: 10 }}><Link className={styles.button} to={`/classrooms/${latest.id}/review`}>继续复习</Link><Link className={styles.buttonSecondary} to={`/classrooms/${latest.id}/record`}>查看记录</Link></div></>
          : <Empty title="还没有课堂" description="这门课开始第一节课堂后，会在这里继续学习。" />}
      </section>
      <section className={styles.panel} aria-labelledby="ov-questions"><h3 id="ov-questions">未解决的疑问</h3>
        {questions.pending ? <Loading label="正在读取疑问…" /> : open.length === 0 ? <Empty title="没有未解决的疑问" description={`统计范围：本课程最近 ${recent.length} 节课堂。`} /> :
          <ul className={styles.list}>{open.map(item => <li key={item.id} className={styles.listItem}><div><strong>{item.question}</strong><p>{sessionTitle.get(item.class_session_id)}</p></div><Link to={`/classrooms/${item.class_session_id}/review`}>定位</Link></li>)}</ul>}
        {questions.error && <p className={`${styles.small} ${styles.warning}`}>部分疑问暂时无法读取。</p>}
      </section>
    </div>
    <section className={styles.panel} aria-labelledby="ov-recent"><h3 id="ov-recent">最近课堂</h3>
      {recent.length === 0 ? <Empty title="还没有课堂记录" description="这里只显示真实发生的课堂，不展示计划排课。" /> :
        <ul className={styles.list}>{recent.slice(0, 3).map(item => <li key={item.id} className={styles.listItem}><div><strong>{item.title || '未命名课堂'}</strong><p>{formatDateTime(item.started_at ?? item.created_at)} · {formatDuration(item.effective_capture_ms)}</p><FlowSummaryView flows={item.flows} /></div><Link to={`/classrooms/${item.id}/record`}>查看</Link></li>)}</ul>}
    </section>
    <section className={styles.panel} aria-labelledby="ov-results"><h3 id="ov-results">学习成果</h3>
      {documents.isError && !documents.data ? <QueryError error={documents.error} /> : documents.isPending ? <Loading label="正在读取复习资料…" /> : docs.length === 0 ? <Empty title="还没有复习资料" description="课堂总结、卡片和个人笔记生成或保存后会出现在这里。" /> :
        <ul className={styles.list}>{docs.map(doc => <li key={doc.id} className={styles.listItem}><div><strong>{doc.title || '未命名'}</strong><p>{kindLabel[doc.kind]} · {docState(doc)} · {sessionTitle.get(doc.class_session_id)}</p></div><Link to={`/review/items/${doc.id}`}>打开</Link></li>)}</ul>}
    </section>
    <p className={`${styles.small} ${styles.muted}`}>本版不提供课表或计划课；以上均为已经发生的实际课堂（UI-COURSE-12）。</p>
  </div>;
}
const kindLabel: Record<S['ReviewDocument']['kind'], string> = { summary: '课堂总结', card: '复习卡片', note: '个人笔记' };
const docState = (doc: S['ReviewDocument']) => doc.adopted_revision_id ? '已采用版本' : doc.latest_personal_revision_id ? '个人稿' : doc.candidate_revision_ids.length ? '候选未采用' : '尚无版本';

/** UI-COURSE-08: time, duration, status; moving to another course is an explicit, CAS-checked action. */
function Classrooms({ course }: { course: S['Course'] }) {
  const { online } = useAppData();
  const sessions = useClassSessions(course.id);
  const courses = useCourses({});
  const [moving, setMoving] = useState<S['ClassSession'] | null>(null);
  if (sessions.isError && !sessions.data) return <QueryError error={sessions.error} retry={() => void sessions.refetch()} />;
  if (sessions.isPending) return <Loading label="正在读取课堂…" />;
  const list = recentSessions(sessions.data.items, [course], 1000);
  return <section className={styles.panel}>
    {list.length === 0 ? <Empty title="还没有课堂" description="开始课堂后会自动归入这门课程；此处不会显示计划排课。" /> :
      <div className={styles.tableWrap}><table className={styles.table} data-testid="classroom-table"><caption className={styles.srOnly}>{course.name} 的课堂</caption>
        <thead><tr><th scope="col">课堂</th><th scope="col">时间</th><th scope="col">有效时长</th><th scope="col">处理状态</th><th scope="col">操作</th></tr></thead>
        <tbody>{list.map(item => <tr key={item.id} data-session-id={item.id}>
          <td><Link to={`/classrooms/${item.id}/record`}>{item.title || '未命名课堂'}</Link></td>
          <td>{formatDateTime(item.started_at ?? item.created_at)}</td>
          <td>{formatDuration(item.effective_capture_ms)}{item.known_gap_count + item.unknown_gap_count > 0 ? `（${item.known_gap_count + item.unknown_gap_count} 处缺口）` : ''}</td>
          <td><FlowSummaryView flows={item.flows} /></td>
          <td>{online ? <button type="button" className={styles.buttonGhost} onClick={() => setMoving(item)}>改归属…</button> : '—'}</td></tr>)}</tbody></table></div>}
    {moving && <MoveDialog session={moving} current={course} targets={(courses.data?.items ?? []).filter(item => item.id !== course.id)} onClose={() => setMoving(null)} />}
  </section>;
}

function MoveDialog({ session, current, targets, onClose }: { session: S['ClassSession']; current: S['Course']; targets: S['Course'][]; onClose: () => void }) {
  const { api } = useAppData();
  const invalidate = useInvalidateAccount();
  const [target, setTarget] = useState('');
  const move = useMutation({
    mutationFn: () => api.updateClassSession(session.id, { base_revision: session.revision, course_id: target }),
    onSuccess: async () => { await invalidate(); onClose(); },
    onError: async error => { if (isConflict(error)) await invalidate(); },
  });
  return <Dialog open onOpenChange={value => { if (!value) onClose(); }} title={`调整「${session.title || '未命名课堂'}」的归属`} description={`当前属于「${current.name}」。只改变归属，课堂的采集时间、转写和历史来源保持不变。`}>
    <div className={styles.stack}>
      <div className={styles.field}><label htmlFor="move-target">移到课程</label>
        <select id="move-target" value={target} onChange={event => setTarget(event.target.value)}><option value="">请选择</option>{targets.map(item => <option key={item.id} value={item.id}>{item.name}{item.english_name ? `（${item.english_name}）` : ''}</option>)}</select></div>
      {move.isError && <p role="alert" className={styles.error}>{isConflict(move.error) ? '该课堂已在其他地方更新，未做任何更改。请关闭后重新选择。' : describeError(move.error)}</p>}
      <div className={styles.row}><button type="button" className={styles.button} disabled={!target || move.isPending || isConflict(move.error)} onClick={() => move.mutate()}>{move.isPending ? '正在移动…' : '确认移动'}</button><button type="button" className={styles.buttonSecondary} onClick={onClose}>取消</button></div>
    </div>
  </Dialog>;
}

/** UI-COURSE-09: course syllabus/glossary only; classroom images stay with each classroom. */
function Materials({ course }: { course: S['Course'] }) {
  const materials = useMaterials(course.id);
  if (materials.isError && !materials.data) return <QueryError error={materials.error} retry={() => void materials.refetch()} />;
  if (materials.isPending) return <Loading label="正在读取课程资料…" />;
  const material = materials.data.items[0];
  return <div className={styles.stack}>
    {material ? <MaterialEditor key={course.id} material={material} /> : <section className={styles.panel}><Empty title="课程资料暂不可用" description="服务端尚未提供这门课程的资料记录。" /></section>}
    <section className={styles.panel} aria-labelledby="mat-images"><h3 id="mat-images">课堂图片</h3>
      <p className={`${styles.small} ${styles.muted}`}>课件照片和板书图片在各自课堂中上传与管理（PR-10a），不会混入课程资料。本版不要求上传整份 PDF/PPT。</p>
      <Link className={styles.buttonGhost} to={`/courses/${course.id}/classrooms`}>查看本课程的课堂</Link></section>
  </div>;
}

function MaterialEditor({ material }: { material: S['CourseMaterial'] }) {
  const { api, online } = useAppData();
  const invalidate = useInvalidateAccount();
  const [base, setBase] = useState(material.revision);
  const [syllabus, setSyllabus] = useState(material.syllabus);
  const [language, setLanguage] = useState(material.language);
  const [glossary, setGlossary] = useState<S['GlossaryTerm'][]>(material.glossary);
  const [latest, setLatest] = useState<S['CourseMaterial'] | null>(null);
  // The saved notice applies only while the draft still equals what the server acknowledged.
  const [acked, setAcked] = useState<S['CourseMaterial'] | null>(null);
  const draftKey = (value: Pick<S['CourseMaterial'], 'syllabus' | 'glossary' | 'language'>) => JSON.stringify([value.syllabus, value.language, value.glossary.filter(term => term.term.trim()).map(term => [term.term, term.meaning, term.language])]);
  const saved = acked !== null && draftKey(acked) === draftKey({ syllabus, glossary, language });
  const save = useMutation({
    mutationFn: (revision: number) => api.writeMaterial(material.course_id, { base_revision: revision, syllabus, glossary: glossary.filter(term => term.term.trim()), language }),
    onSuccess: async next => { setBase(next.revision); setLatest(null); setAcked(next); await invalidate(); },
    onError: async error => { setAcked(null); if (isConflict(error)) setLatest((await api.materials(material.course_id)).items[0] ?? null); },
  });
  const invalidTerm = glossary.some(term => term.term.length > 160 || term.meaning.length > 1000);
  return <form className={`${styles.panel} ${styles.form}`} style={{ maxWidth: 'none' }} onSubmit={event => { event.preventDefault(); setAcked(null); save.mutate(base); }} aria-labelledby="mat-heading">
    <h3 id="mat-heading">课程大纲与术语词表</h3>
    <fieldset disabled={save.isPending} className={styles.stack} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>{/* Locked while saving so no edit is lost when the new version arrives. */}
    <div className={styles.field}><label htmlFor="mat-syllabus">课程大纲</label><textarea id="mat-syllabus" value={syllabus} maxLength={120000} onChange={event => setSyllabus(event.target.value)} aria-describedby="mat-syllabus-hint" /><span id="mat-syllabus-hint" className={styles.hint}>可留空，之后再补。</span></div>
    <div className={styles.field}><label htmlFor="mat-language">资料语言</label><select id="mat-language" value={language} onChange={event => setLanguage(event.target.value as S['CourseMaterial']['language'])} aria-describedby="mat-language-hint">{(Object.keys(languageLabels) as Array<keyof typeof languageLabels>).map(key => <option key={key} value={key}>{languageLabels[key]}</option>)}</select>
      <span id="mat-language-hint" className={styles.hint}>修改语言或词表只影响之后课堂的默认设置，不会重新识别或重新生成历史结果。</span></div>
    <fieldset className={styles.stack} style={{ border: 0, padding: 0, margin: 0 }}><legend className={styles.fieldLegend}>术语词表（{glossary.length} 条）</legend>
      {glossary.map((term, index) => <div key={index} className={styles.formGrid} style={{ alignItems: 'end' }} data-testid="glossary-row">
        <div className={styles.field}><label htmlFor={`term-${index}`}>术语 {index + 1}</label><input id={`term-${index}`} value={term.term} maxLength={160} onChange={event => setGlossary(glossary.map((item, i) => i === index ? { ...item, term: event.target.value } : item))} /></div>
        <div className={styles.row}><div className={styles.field} style={{ flex: 1 }}><label htmlFor={`meaning-${index}`}>释义 {index + 1}</label><input id={`meaning-${index}`} value={term.meaning} maxLength={1000} onChange={event => setGlossary(glossary.map((item, i) => i === index ? { ...item, meaning: event.target.value } : item))} /></div>
          <button type="button" className={styles.buttonGhost} aria-label={`删除术语 ${index + 1}`} onClick={() => setGlossary(glossary.filter((_, i) => i !== index))}><IconX size={16} aria-hidden="true" /></button></div>
      </div>)}
      <div><button type="button" className={styles.buttonSecondary} disabled={glossary.length >= 2000} onClick={() => setGlossary([...glossary, { term: '', meaning: '', language: 'auto' }])}><IconPlus size={15} aria-hidden="true" /> 添加术语</button></div>
    </fieldset>
    </fieldset>
    {latest && <div className={`${styles.banner} ${styles.bannerWarning}`} role="alert" data-testid="conflict-panel"><div className={styles.stack}><strong>版本冲突：资料已在其他地方更新，你的输入仍保留。</strong>
      <div className={styles.compare}><div><strong>你的大纲</strong><p>{syllabus || '（空）'}</p><p>词表 {glossary.length} 条</p></div><div><strong>最新大纲</strong><p>{latest.syllabus || '（空）'}</p><p>词表 {latest.glossary.length} 条</p></div></div>
      <div className={styles.row}><button type="button" className={styles.button} onClick={() => { setBase(latest.revision); save.mutate(latest.revision); }}>以我的输入覆盖最新版本</button>
        <button type="button" className={styles.buttonSecondary} onClick={() => { setBase(latest.revision); setSyllabus(latest.syllabus); setGlossary(latest.glossary); setLanguage(latest.language); setLatest(null); save.reset(); }}>放弃我的修改，载入最新版本</button></div></div></div>}
    {save.isError && !latest && <p role="alert" className={styles.error}>{describeError(save.error)}</p>}
    {saved && <p role="status" className={styles.success}>已保存到云端（版本 {base}）。</p>}
    <div className={styles.row}><button type="submit" className={styles.button} disabled={!online || save.isPending || invalidTerm}>{save.isPending ? '正在保存…' : '保存资料'}</button></div>
  </form>;
}

/** UI-REVIEW-03: the course entry groups the shared documents by class, newest class first. */
function groupByClass(docs: S['ReviewDocument'][], sessions: S['ClassSession'][]) {
  const order = new Map([...sessions].sort((a, b) => (b.started_at ?? b.created_at).localeCompare(a.started_at ?? a.created_at)).map((item, index) => [item.id, index]));
  const groups = new Map<string, S['ReviewDocument'][]>();
  for (const doc of docs) groups.set(doc.class_session_id, [...(groups.get(doc.class_session_id) ?? []), doc]);
  return [...groups].map(([classId, items]) => ({ classId, docs: items })).sort((a, b) => (order.get(a.classId) ?? Number.MAX_SAFE_INTEGER) - (order.get(b.classId) ?? Number.MAX_SAFE_INTEGER));
}

/** UI-COURSE-10 / UI-REVIEW-02 (course part): filters the shared review objects; no copies. */
function ReviewTab({ course }: { course: S['Course'] }) {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const kind = (['summary', 'card', 'note'] as const).find(value => value === params.get('kind'));
  const understood = params.get('understood') === 'true' ? true : params.get('understood') === 'false' ? false : undefined;
  const documents = useReviewDocuments({ course_id: course.id, kind, understood });
  const sessions = useClassSessions(course.id);
  const sessionTitle = new Map((sessions.data?.items ?? []).map(item => [item.id, item.title || '未命名课堂']));
  const set = (key: string, value: string) => { const next = new URLSearchParams(params); if (value) next.set(key, value); else next.delete(key); setParams(next, { replace: true, state: location.state }); };
  return <section className={styles.panel}>
    <div className={styles.toolbar}>
      <div className={styles.field}><label htmlFor="review-kind">类型</label><select id="review-kind" value={kind ?? ''} onChange={event => set('kind', event.target.value)}><option value="">全部</option><option value="summary">课堂总结</option><option value="card">复习卡片</option><option value="note">个人笔记</option></select></div>
      <div className={styles.field}><label htmlFor="review-understood">理解状态</label><select id="review-understood" value={understood === undefined ? '' : String(understood)} onChange={event => set('understood', event.target.value)}><option value="">全部</option><option value="false">尚未理解</option><option value="true">已理解</option></select></div>
    </div>
    <p className={`${styles.small} ${styles.muted}`}>这里与全局复习、单课复习是同一批资料，只按当前课程筛选并按课堂分组；在任一入口的修改都会同步出现。</p>
    {documents.isError && !documents.data ? <QueryError error={documents.error} retry={() => void documents.refetch()} /> : documents.isPending ? <Loading label="正在读取复习资料…" /> :
      documents.data.items.length === 0 ? <Empty title={kind || understood !== undefined ? '没有符合筛选的资料' : '这门课还没有复习资料'} description="课堂结束并生成或保存资料后会出现在这里。" /> :
      <div data-testid="course-review-list">{groupByClass(documents.data.items, sessions.data?.items ?? []).map(group => <section key={group.classId} aria-label={sessionTitle.get(group.classId) ?? '课堂'} data-class-id={group.classId}>
        <h3 className={styles.small} style={{ margin: '12px 0 4px' }}><Link to={`/classrooms/${group.classId}/review`} state={location.state}>{sessionTitle.get(group.classId) ?? '课堂'}</Link></h3>
        <ul className={styles.list}>{group.docs.map(doc => <li key={doc.id} className={styles.listItem} data-document-id={doc.id}><div><strong>{doc.title || '未命名'}</strong><p>{kindLabel[doc.kind]} · {docState(doc)} · {doc.understood ? '已理解' : '尚未理解'}</p></div><Link to={`/review/items/${doc.id}`} state={{ from: `${location.pathname}${location.search}` }}>打开</Link></li>)}</ul>
      </section>)}</div>}
  </section>;
}
