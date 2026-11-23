import { useEffect, useMemo, useRef } from 'react';
import { Link, useParams } from 'react-router';
import { describeError, readableCache, ReadOnlyWhile, useAppData, useCourse } from '../../features/data';
import { useClassQuestions, useClassSession, useClassroomShell, useMarks, useTranscript } from '../../features/classroom';
import { buildTimeline, TASK_STATE_LABEL } from '../../domain/classroom';
import { DataSourceNote } from '../common';
import { TranscriptTimeline } from './TranscriptTimeline';
import { SlidesPanel } from './SlidesPanel';
import { FlowStrip } from './ClassroomLive';
import styles from './classroom.module.css';

/**
 * Ended-class record: formal transcript, pictures, marks and questions. Audio playback is not part
 * of this preview; when the original audio has expired the time positions remain, never playable.
 */
export function ClassroomRecord() {
  const { classId = '' } = useParams();
  const app = useAppData();
  const shell = useClassroomShell();
  const session = useClassSession(classId);
  const course = useCourse(session.data?.course_id ?? '', Boolean(session.data));
  const transcript = useTranscript(classId);
  const marks = useMarks(classId);
  const questions = useClassQuestions(classId);
  const live = shell.capture.liveSegments(classId);
  const entries = useMemo(() => buildTimeline(classId, transcript.data?.segments ?? [], live).filter(entry => entry.status === 'final'), [classId, transcript.data, live]);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [classId]);
  if (session.isPending) return <main id="main-content" className={styles.page}><p role="status" className={styles.muted}>正在读取课堂记录…</p></main>;
  // Only data the account still holds is shown, and only after a transport failure: a denial removed
  // it (revocation ledger), an expired session or unreadable reply shows nothing.
  const data = readableCache(session);
  if (!data) return <main id="main-content" className={styles.page}><div className={`${styles.banner} ${styles.bannerDanger}`} role="alert"><div>{describeError(session.error)} <Link to="/classrooms">返回课堂入口</Link></div></div></main>;
  const expires = data.audio_expires_at ? Date.parse(data.audio_expires_at) : null;
  const audio = data.capture_ended_at === null ? '课堂尚未结束。'
    : expires === null ? '原音不可用（已删除或未保存），字幕保留原时间位置，不能播放。'
      : expires <= app.now() ? '原音已过期，不能播放或重新识别；字幕保留原时间位置。'
        : `原音保留至 ${data.audio_expires_at!.slice(0, 10)}。开发预览不提供播放。`;
  return <ReadOnlyWhile stale={session.isError}><main id="main-content" className={styles.page}>
    <header className={styles.header}>
      <div><p className={styles.eyebrow}>{course.data?.name ?? '课程'} · 课堂记录</p><h1 ref={heading} tabIndex={-1}>{data.title || '未命名课堂'}</h1><p className={`${styles.small} ${styles.muted}`} data-testid="audio-status">{audio}</p></div>
      <FlowStrip flows={data.flows} />
    </header>
    <DataSourceNote />
    {session.isError && <div className={`${styles.banner} ${styles.bannerWarning}`} role="status" data-testid="record-refresh-error"><div>{describeError(session.error)}</div></div>}
    <div className={styles.processingGrid}>
      <section className={styles.panel} aria-labelledby="record-transcript"><h2 id="record-transcript">正式字幕</h2>
        {transcript.isError ? <p className={styles.error} role="alert">{describeError(transcript.error)}</p> : <TranscriptTimeline entries={entries} label="课堂正式字幕" emptyText="这节课没有正式字幕。" />}
      </section>
      <div className={styles.stack}>
        <section className={styles.panel} aria-labelledby="record-marks"><h2 id="record-marks">标记</h2>
          {marks.isError ? <p className={styles.error} role="alert">{describeError(marks.error)}</p> : (marks.data?.items.length ?? 0) === 0 ? <p className={`${styles.small} ${styles.muted}`}>没有标记。</p>
            : <ul className={styles.list}>{marks.data!.items.map(mark => <li key={mark.id} className={styles.listItem}><div><strong>{mark.kind === 'important' ? '重点' : '没懂'}</strong>{mark.note ? ` · ${mark.note}` : ''}{mark.understood ? ' · 已理解' : ''}</div></li>)}</ul>}
        </section>
        <section className={styles.panel} aria-labelledby="record-questions"><h2 id="record-questions">问题</h2>
          {questions.isError ? <p className={styles.error} role="alert">{describeError(questions.error)}</p> : (questions.data?.items.length ?? 0) === 0 ? <p className={`${styles.small} ${styles.muted}`}>没有问题。</p>
            : <ul className={styles.list}>{questions.data!.items.map(item => <li key={item.id} className={styles.listItem}><div><strong>{item.question}</strong><p className={`${styles.small} ${styles.muted}`}>{TASK_STATE_LABEL[item.state]}{item.understood ? ' · 已理解' : ''}</p>{item.answer_markdown && <p className={styles.answer}>{item.answer_markdown}</p>}</div></li>)}</ul>}
        </section>
        <section className={styles.panel} aria-labelledby="record-slides"><h2 id="record-slides">课件图片</h2><SlidesPanel classId={classId} positionMs={0} readOnly /></section>
      </div>
    </div>
    <nav className={styles.row} style={{ marginTop: '1rem' }} aria-label="记录去向">
      {data.capture_ended_at !== null && <Link className={styles.buttonSecondary} to={`/classrooms/${classId}/processing`}>课后处理</Link>}
      <Link className={styles.buttonGhost} to={`/courses/${data.course_id}/classrooms`}>回到课程</Link>
    </nav>
  </main></ReadOnlyWhile>;
}
