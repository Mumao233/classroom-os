import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router';
import type { components } from '../../generated/openapi';
import { describeError, useAppData, useClassSessions, useCourses, useInvalidateAccount } from '../../features/data';
import { useClassroomShell } from '../../features/classroom';
import { DataSourceNote } from '../common';
import styles from './classroom.module.css';

type S = components['schemas'];

function destination(session: S['ClassSession']): { to: string; label: string } {
  if (session.capture_ended_at !== null) return { to: `/classrooms/${session.id}/processing`, label: '课后处理' };
  return { to: `/classrooms/${session.id}/live`, label: session.started_at ? '返回课堂' : '继续准备' };
}

/**
 * /classrooms: an active capture always wins (UX-02); otherwise the user creates a classroom
 * explicitly. Creating a classroom never opens the microphone or starts charging.
 */
export function ClassroomEntry() {
  const app = useAppData();
  const shell = useClassroomShell();
  const navigate = useNavigate();
  const invalidate = useInvalidateAccount();
  const [params] = useSearchParams();
  const courses = useCourses({ archived: false });
  const sessions = useClassSessions();
  const [courseId, setCourseId] = useState(params.get('course') ?? '');
  const [title, setTitle] = useState('');
  const [language, setLanguage] = useState<S['ClassSession']['language']>('auto');
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const key = useRef(crypto.randomUUID());
  const keyBody = useRef<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, []);
  const bound = shell.capture.bound();
  if (bound?.active) return <Navigate to={`/classrooms/${bound.classId}/live`} replace />;

  const available = (courses.data?.items ?? []).filter(item => !item.archived);
  const selected = courseId || available[0]?.id || '';
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!selected || !title.trim()) { setError(null); return; }
    setBusy(true); setError(null);
    try {
      // The operation key belongs to one request body: an identical retry reuses it, a changed one gets a new key.
      const body = { course_id: selected, title: title.trim(), language };
      const serialized = JSON.stringify(body);
      if (keyBody.current !== null && keyBody.current !== serialized) key.current = crypto.randomUUID();
      keyBody.current = serialized;
      const created = await app.api.createClassSession(body, key.current);
      key.current = crypto.randomUUID();
      keyBody.current = null;
      void invalidate();
      navigate(`/classrooms/${created.id}/live`);
    } catch (caught) { setError(caught); } finally { setBusy(false); }
  };
  const recent = [...(sessions.data?.items ?? [])].sort((a, b) => (b.started_at ?? b.created_at).localeCompare(a.started_at ?? a.created_at) || b.id.localeCompare(a.id)).slice(0, 8);
  const courseName = new Map((courses.data?.items ?? []).map(item => [item.id, item.name]));

  return <main id="main-content" className={styles.page}>
    <header className={styles.header}><div><p className={styles.eyebrow}>CLASSROOM</p><h1 ref={heading} tabIndex={-1}>课堂</h1><p className={styles.muted}>新建课堂后进入课前准备；只有点击「开始采集」才会开始。</p></div></header>
    <DataSourceNote />
    <div className={styles.processingGrid}>
      <section className={styles.panel} aria-labelledby="new-class">
        <h2 id="new-class">新建课堂</h2>
        {courses.isPending ? <p role="status" className={styles.muted}>正在读取课程…</p> : available.length === 0 ? <p>还没有可用的课程。<Link to="/courses/new">先创建一门课程</Link></p>
          : <form className={styles.stack} onSubmit={event => void submit(event)} noValidate>
            <fieldset disabled={busy || !app.online} style={{ border: 0, padding: 0, margin: 0, display: 'grid', gap: '.75rem' }}>
              <div className={styles.field}><label htmlFor="class-course">课程</label><select id="class-course" value={selected} onChange={event => setCourseId(event.target.value)}>{available.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div>
              <div className={styles.field}><label htmlFor="class-title">课堂标题</label><input id="class-title" value={title} maxLength={200} onChange={event => setTitle(event.target.value)} aria-invalid={!title.trim() && error === null && busy ? true : undefined} aria-describedby="class-title-hint" />
                <span id="class-title-hint" className={styles.hint}>例如「第 6 讲 · 特征值」。必填。</span></div>
              <div className={styles.field}><label htmlFor="class-language">语言</label><select id="class-language" value={language} onChange={event => setLanguage(event.target.value as S['ClassSession']['language'])}>
                <option value="auto">自动识别</option><option value="zh">中文</option><option value="en">英文</option><option value="mixed">中英混合</option></select></div>
            </fieldset>
            {!app.online && <p className={styles.toneWarning}>离线时不能新建课堂。</p>}
            {error !== null && <p className={styles.error} role="alert">{describeError(error)}</p>}
            <div className={styles.row}><button type="submit" className={styles.button} disabled={busy || !app.online || !title.trim()}>{busy ? '正在创建…' : '创建并进入准备'}</button></div>
          </form>}
      </section>
      <section className={styles.panel} aria-labelledby="recent-classes">
        <h2 id="recent-classes">最近课堂</h2>
        {sessions.isError ? <p className={styles.error} role="alert">{describeError(sessions.error)}</p> : recent.length === 0 ? <p className={`${styles.small} ${styles.muted}`}>还没有课堂。</p>
          : <ul className={styles.list}>{recent.map(item => { const target = destination(item); return <li key={item.id} className={styles.listItem}><div><strong>{item.title || '未命名课堂'}</strong><p className={`${styles.small} ${styles.muted}`}>{courseName.get(item.course_id) ?? '课程'} · {(item.started_at ?? item.created_at).slice(0, 10)}</p></div><Link className={styles.buttonGhost} to={target.to}>{target.label}</Link></li>; })}</ul>}
      </section>
    </div>
  </main>;
}
