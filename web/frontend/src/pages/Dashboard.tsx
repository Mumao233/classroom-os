import type { ReactNode } from 'react';
import { IconArrowRight, IconBolt, IconBook2, IconCalendar, IconChevronRight, IconFileText, IconListCheck, IconMicrophone, IconTimeline } from '@tabler/icons-react';
import { Link } from 'react-router';
import { activityItems, chooseContinueEntry, deriveTodos, EXPIRY_WARNING_MS, formatDate, formatDateTime, liveSessions, recentSessions, type ContinueEntry } from '../domain/dashboard';
import { defaultSemester } from '../domain/courses';
import { useAppData, useClassSessions, useCourses, useMe, useNotifications, useQuestionsFor, useReviewDocuments, useSemesters } from '../features/data';
import { Badge, CourseGlyph, coverClass, DataSourceNote, Empty, FlowSummaryView, Loading, OfflineBanner, QueryError } from './common';
import shell from '../app/Shell.module.css';
import styles from './pages.module.css';

const zhDate = (now: number) => new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long', timeZone: 'Asia/Shanghai' }).format(new Date(now));
const dayOf = (now: number) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date(now));
const todoLabel = { conflict: '冲突', failed: '失败', unknown: '待核对', unsynced: '未同步', expiring: '临近到期', not_understood: '未懂' } as const;

/**
 * UI-DASH-01…09. Module order is fixed for both empty and populated states:
 * 继续学习 → 快速开始 → 最近课程 → 待处理 → 学习动态. Every value comes from a server record or
 * the account runtime; nothing is invented to fill space (CF-09).
 */
export function Dashboard() {
  const app = useAppData();
  const me = useMe();
  const semesters = useSemesters();
  const active = useCourses({});
  const archived = useCourses({ archived: true });
  const sessions = useClassSessions();
  const documents = useReviewDocuments({ kind: 'note' });
  const notifications = useNotifications();
  const allCourses = [...(active.data?.items ?? []), ...(archived.data?.items ?? [])];
  // Unresolved questions come from every live loaded classroom, not just the five shown (UI-DASH-06).
  const questions = useQuestionsFor(liveSessions(sessions.data?.items ?? [], allCourses).map(item => item.id));
  const core = [semesters, active, archived, sessions];
  // A failed background refetch (e.g. offline) keeps showing what was already read.
  const failed = core.find(query => query.isError && !query.data);
  if (failed) return <main id="main-content" className={shell.page} tabIndex={-1}><Heading /><OfflineBanner scope="只能显示本次页面已读取的内容。" /><QueryError error={failed.error} retry={() => core.forEach(query => void query.refetch())} /></main>;
  if (core.some(query => query.isPending)) return <main id="main-content" className={shell.page} tabIndex={-1}><Heading /><Loading label="正在读取学习记录…" /></main>;

  const now = app.now();
  const courses = active.data!.items;
  const isNew = courses.length === 0 && archived.data!.items.length === 0;
  const entry = chooseContinueEntry({ capture: app.capture, localRisk: app.localRisk, sessions: sessions.data!.items, courses: allCourses, documents: documents.data?.items ?? [] });
  const todos = deriveTodos({ sessions: sessions.data!.items, courses: allCourses, questions: questions.items, now });
  const lastByCourse = new Map<string, ReturnType<typeof recentSessions>[number]>();
  for (const item of recentSessions(sessions.data!.items, allCourses, 1000)) if (!lastByCourse.has(item.course_id)) lastByCourse.set(item.course_id, item);
  const recentCourses = [...courses].sort((a, b) => (b.last_class_session_at ?? '').localeCompare(a.last_class_session_at ?? '') || (a.id < b.id ? -1 : 1)).slice(0, 3);
  const semester = defaultSemester(semesters.data!.items);
  const today = dayOf(now);
  const week = semester?.starts_on && semester.ends_on && today >= semester.starts_on && today <= semester.ends_on
    ? Math.floor((Date.parse(`${today}T00:00:00Z`) - Date.parse(`${semester.starts_on}T00:00:00Z`)) / 604800000) + 1 : null;
  const activity = activityItems(notifications.data?.items ?? []);

  return <main id="main-content" className={shell.page} tabIndex={-1} data-testid="dashboard">
    <Heading name={me.data?.nickname} detail={<>{semester ? <>{semester.name}{week ? ` · 第 ${week} 周` : ''}</> : '尚未创建学期'} <span aria-hidden="true">·</span> {zhDate(now)}</>} />
    <div className={styles.stack}>
      <OfflineBanner scope="首页只显示本次已读取的课程与课堂元数据；不能开始新课堂或修改内容。" />
      <DataSourceNote />
      <div className={styles.dashGrid}>
        <section className={styles.panel} aria-labelledby="dash-continue" data-module="continue">
          <div className={styles.sectionHead}><h2 id="dash-continue"><IconBook2 size={18} aria-hidden="true" /> 继续学习</h2></div>
          <ContinueModule entry={entry} isNew={isNew} />
        </section>
        <section className={styles.panel} aria-labelledby="dash-quick" data-module="quick">
          <div className={styles.sectionHead}><h2 id="dash-quick"><IconBolt size={18} aria-hidden="true" /> 快速开始</h2></div>
          <QuickStart hasCourse={courses.length > 0} />
        </section>
      </div>
      <section className={styles.panel} aria-labelledby="dash-recent" data-module="recent">
        <div className={styles.sectionHead}><h2 id="dash-recent">最近课程</h2>{!isNew && <Link to="/courses">查看全部 <IconChevronRight size={14} aria-hidden="true" /></Link>}</div>
        {recentCourses.length === 0 ? <Empty title="还没有课程记录" description={isNew ? '创建课程后，真实的课堂记录会显示在这里。' : '当前没有进行中的课程；已归档课程可在课程页查看。'} /> :
          <div className={styles.cards}>{recentCourses.map(course => {
            const last = lastByCourse.get(course.id);
            return <Link key={course.id} to={`/courses/${course.id}/overview`} className={styles.card}>
              <div className={`${styles.cover} ${coverClass(course.color_token)}`} />
              <div className={styles.cardBody}><CourseGlyph course={course} /><h3 className={styles.cardTitle}>{course.name}</h3>
                <p className={styles.cardMeta}>{course.class_session_count} 节课堂</p>
                <p className={styles.cardMeta}>{last ? <>最近：{last.title || '未命名课堂'} · {formatDate(last.started_at ?? last.created_at)}</> : '还没有课堂记录'}</p>
                {last && <FlowSummaryView flows={last.flows} />}
              </div>
            </Link>;
          })}</div>}
      </section>
      <div className={styles.lowerGrid}>
        <section className={styles.panel} aria-labelledby="dash-todo" data-module="todo">
          <div className={styles.sectionHead}><h2 id="dash-todo"><IconListCheck size={18} aria-hidden="true" /> 待处理</h2></div>
          {questions.error && <p className={`${styles.small} ${styles.warning}`}>部分课堂的疑问暂时无法读取，待办可能不完整。</p>}
          {todos.length === 0 ? <Empty title="没有需要处理的事项" description="未同步、冲突、失败、未懂和临近到期的原音会在这里出现。" /> :
            <ul className={styles.list} data-testid="todo-list">{todos.slice(0, 6).map(todo => <li key={todo.id} className={styles.listItem} data-kind={todo.kind}>
              <Badge tone={todo.kind === 'not_understood' || todo.kind === 'expiring' ? 'info' : todo.kind === 'failed' ? 'danger' : 'warning'}>{todoLabel[todo.kind]}</Badge>
              <div><strong>{todo.title}</strong><p>{todo.detail}</p></div><Link to={todo.to} aria-label={`定位：${todo.title}，${todoLabel[todo.kind]}`}>定位</Link></li>)}</ul>}
          {todos.some(todo => todo.kind === 'expiring') && <p className={`${styles.small} ${styles.muted}`}>“临近到期”指 {EXPIRY_WARNING_MS / 3600000} 小时内将自动删除的原音（产品参数）。</p>}
        </section>
        <section className={styles.panel} aria-labelledby="dash-activity" data-module="activity">
          <div className={styles.sectionHead}><h2 id="dash-activity"><IconTimeline size={18} aria-hidden="true" /> 学习动态</h2>{activity.length > 0 && <Link to="/notifications">通知 <IconChevronRight size={14} aria-hidden="true" /></Link>}</div>
          {notifications.isError && !notifications.data ? <QueryError error={notifications.error} /> : notifications.isPending ? <Loading label="正在读取动态…" /> :
            activity.length === 0 ? <Empty title="还没有学习动态" description="课堂处理完成、冲突和提醒等真实记录会显示在这里。" /> :
            <ul className={styles.list}>{activity.map(item => <li key={item.id} className={styles.listItem}>
              <div><strong>{item.label}</strong><p>{formatDateTime(item.at)}{item.resolved ? ' · 已解决' : item.read ? ' · 已读，尚未解决' : ''}</p></div>
              {item.severity !== 'info' && !item.resolved && <Badge tone="warning">需要关注</Badge>}</li>)}</ul>}
        </section>
      </div>
    </div>
  </main>;
}

function Heading({ name, detail }: { name?: string; detail?: ReactNode }) {
  return <div className={styles.heading}><div className={styles.wrap}><h1>{name ? `你好，${name}` : '学习首页'}</h1>{detail && <p><IconCalendar size={14} aria-hidden="true" /> {detail}</p>}</div></div>;
}

function ContinueModule({ entry, isNew }: { entry: ContinueEntry; isNew: boolean }) {
  const app = useAppData();
  if (app.classroomRevoked && !app.capture.active) return <div data-testid="continue" data-kind="revoked"><h3 className={styles.continueTitle}>模拟课堂已撤权</h3><p className={styles.muted}>该课堂的正文和学习动态已从当前展示中移除，不再提供进入入口。</p><Link className={styles.buttonSecondary} to="/courses">返回课程</Link></div>;
  switch (entry.kind) {
    case 'capture': return <div data-testid="continue" data-kind="capture"><Badge tone="recording">● 正在采集</Badge><h3 className={styles.continueTitle}>{entry.title ?? '进行中的课堂'}</h3><p className={styles.muted}>采集优先于其他入口。返回课堂查看五项状态或停止采集。</p>
      <Link className={styles.button} to={entry.classSessionId ? `/classrooms/${entry.classSessionId}/live` : '/classrooms'}>返回课堂 <IconArrowRight size={16} aria-hidden="true" /></Link></div>;
    case 'local_risk': return <div data-testid="continue" data-kind="local_risk"><Badge tone={entry.risk.kind === 'unsaved' ? 'danger' : 'warning'}>{entry.risk.kind === 'unsaved' ? '! 尚未安全保存' : '! 仅本机保存'}</Badge><h3 className={styles.continueTitle}>先处理未保存的内容</h3><p className={styles.muted}>{entry.risk.detail}</p>
      <Link className={styles.button} to="/classrooms">前往处理 <IconArrowRight size={16} aria-hidden="true" /></Link></div>;
    case 'class_session': return <div data-testid="continue" data-kind="class_session"><p className={`${styles.small} ${styles.muted}`}>最近课堂 · <Link to={`/courses/${entry.course.id}/overview`}>{entry.course.name}</Link></p>
      <h3 className={styles.continueTitle}>{entry.course.name} · {entry.session.title || '未命名课堂'}</h3>
      <p className={`${styles.small} ${styles.muted}`}>{formatDateTime(entry.session.started_at ?? entry.session.created_at)}</p>
      <FlowSummaryView flows={entry.session.flows} />
      <div className={styles.row} style={{ marginTop: 14 }}><Link className={styles.button} to={`/classrooms/${entry.session.id}/review`}>继续复习 <IconArrowRight size={16} aria-hidden="true" /></Link><Link className={styles.buttonSecondary} to={`/classrooms/${entry.session.id}/record`}><IconFileText size={16} aria-hidden="true" /> 查看记录</Link></div></div>;
    case 'note': return <div data-testid="continue" data-kind="note"><p className={`${styles.small} ${styles.muted}`}>最近笔记 · {entry.course.name}</p><h3 className={styles.continueTitle}>{entry.document.title || '未命名笔记'}</h3>
      <p className={`${styles.small} ${styles.muted}`}>更新于 {formatDateTime(entry.document.updated_at)}</p>
      <Link className={styles.button} to={`/review/items/${entry.document.id}`}>继续编辑 <IconArrowRight size={16} aria-hidden="true" /></Link></div>;
    default: return isNew
      ? <div data-testid="continue" data-kind="new"><h3 className={styles.continueTitle}>从第一门课程开始</h3><p className={styles.muted}>创建课程后，课堂和学习资料会在这里汇集。首页不会预填示例课堂。</p>{app.online ? <Link className={styles.button} to="/courses/new">创建第一门课程 <IconArrowRight size={16} aria-hidden="true" /></Link> : <p className={styles.small}>联网后可以创建第一门课程。</p>}</div>
      : <div data-testid="continue" data-kind="none"><h3 className={styles.continueTitle}>还没有课堂记录</h3><p className={styles.muted}>开始第一节课堂后，这里会显示最近的课堂或笔记。</p></div>;
  }
}

function QuickStart({ hasCourse }: { hasCourse: boolean }) {
  const app = useAppData();
  const canStart = app.capture.active || (app.online && hasCourse && !app.classroomRevoked);
  return <div className={styles.stack}>
    {app.onQuickStart
      ? canStart
        ? <button type="button" className={styles.button} onClick={app.onQuickStart}><IconMicrophone size={17} aria-hidden="true" /> {app.capture.active ? '返回模拟课堂' : '开始模拟课堂'}</button>
        : <p className={`${styles.small} ${styles.muted}`}>{app.classroomRevoked ? '该模拟课堂已撤权，不能重新开始。' : !app.online ? '离线时不能开始课堂。' : '开始课堂前需要先创建一门课程。'}</p>
      : <Link className={styles.button} to="/classrooms">进入课堂准备</Link>}
    {app.online ? <Link to="/courses/new" className={styles.buttonSecondary}>＋ 新建课程</Link> : <span className={`${styles.small} ${styles.muted}`}>离线时不能新建课程。</span>}
    <p className={`${styles.small} ${styles.muted}`}>开始课堂不会自动开麦或扣除 AI 次数。{app.onQuickStart ? '当前为开发模拟，不访问麦克风。' : ''}</p>
  </div>;
}
