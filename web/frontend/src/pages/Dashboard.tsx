import { IconArrowRight, IconBolt, IconBook2, IconCalendar, IconCheck, IconChevronRight, IconCircleCheck, IconCloudCheck, IconFileText, IconMicrophone, IconSparkles } from '@tabler/icons-react';
import { Link } from 'react-router';
import type { components } from '../generated/openapi';
import styles from '../app/Shell.module.css';

type Props = { empty: boolean; onStart: () => void; active: boolean; account: 'A' | 'B'; coursePage: components['schemas']['CoursePage']; classroomDeleted: boolean };

const courseDetails = [
  { initial: 'LA', title: '线性代数', info: '周二 · 周四 · 12 节课堂', recent: '最近 10月27日', tone: 'blue', status: '已同步' },
  { initial: 'DS', title: '数据结构与算法分析（双语）', info: '周三 · 9 节课堂', recent: '最近 10月25日', tone: 'teal', status: '1 段待上传' },
  { initial: 'MP', title: '大学物理 II', info: '周一 · 周五 · 15 节课堂', recent: '最近 10月24日', tone: 'violet', status: '已同步' },
];

export function Dashboard({ empty, onStart, active, account, coursePage, classroomDeleted }: Props) {
  const courses = coursePage.items.map((course, index) => {
    const detail = account === 'A' ? courseDetails.find(item => item.title === course.name) : undefined;
    return detail ?? { initial: course.icon_token || '课', title: course.name, info: `${course.class_session_count} 节课堂`, recent: '模拟课程', tone: ['blue', 'teal', 'violet'][index % 3], status: '已同步' };
  });
  return <main id="main-content" className={styles.page} tabIndex={-1}>
    <div className={styles.pageHeading}><div><p className={styles.eyebrow}>YOUR LEARNING SPACE</p><h1>下午好，{account === 'A' ? '林同学' : '陈同学'} <span aria-hidden="true">✳</span></h1><p>2026 秋季学期 <span aria-hidden="true">·</span> 第 9 周</p></div><span className={styles.dayLabel}><IconCalendar size={17} /> 10月28日，星期三</span></div>
    {empty ? <section className={`${styles.panel} ${styles.empty}`}><div className={styles.emptyIcon}><IconBook2 size={27}/></div><h2>从第一门课程开始</h2><p>创建课程后，课堂和学习资料会在这里汇集。</p><Link className={styles.primaryButton} to="/courses/new">创建第一门课程 <IconArrowRight size={17}/></Link></section> : account === 'B' ? <section className={`${styles.panel} ${styles.empty}`}><h2>模拟账号 B 的课程</h2><p>以下内容来自当前账号的合成响应。</p>{courses.map(course => <Link key={course.title} to="/courses">{course.title}</Link>)}</section> : classroomDeleted ? <section className={`${styles.panel} ${styles.empty}`}><h2>模拟课堂已撤权</h2><p>该课堂的正文和学习动态已从当前展示中移除。</p><Link className={styles.secondaryButton} to="/courses">返回课程</Link></section> : <>
      <div className={styles.heroGrid}>
        <section className={`${styles.panel} ${styles.continueCard}`}>
          <div className={styles.sectionLabel}><IconBook2 size={17}/> 继续学习 <span className={styles.subtleTag}>最近课堂</span></div>
          <div className={styles.heroBody}><div><p className={styles.heroKicker}>线性代数 / 第 12 讲</p><h2><span className={styles.desktopCourseName}>线性代数 · </span>特征值与特征向量</h2><p className={styles.heroMeta}><span><IconCalendar size={15}/> 10月27日课堂</span><span><IconFileText size={15}/> 转写 48 段</span><span><IconSparkles size={15}/> 候选总结待查看</span></p></div><span className={styles.heroArt} aria-hidden="true">✳</span></div>
          <div className={styles.progress} aria-label="示意复习进度"><span /></div>
          <div className={styles.heroActions}><Link to="/review" className={styles.primaryButton}>继续复习 <IconArrowRight size={17}/></Link><Link to="/classrooms/mock/record" className={styles.secondaryButton}><IconFileText size={17}/> 查看原文</Link></div>
        </section>
        <section className={`${styles.panel} ${styles.quickCard}`}><h2 className={styles.sectionLabel}><IconBolt size={17}/> 快速开始</h2><h2 className={styles.desktopSrOnly}>准备好开始了吗？</h2><div className={styles.quickIntro}><span className={styles.quickIcon}><IconMicrophone size={24}/></span><div><h2>准备好开始了吗？</h2><p>为接下来的课堂留好位置。</p></div></div><button type="button" className={styles.primaryButton} onClick={onStart}><IconMicrophone size={18}/> {active ? '返回模拟课堂' : '开始模拟课堂'}</button><Link to="/courses/new" className={styles.secondaryButton}>＋ 新建课程</Link><p className={styles.note}>ⓘ 仅为开发模拟，不会访问麦克风或产生费用。</p></section>
      </div>
      <h2 className={styles.desktopSrOnly}>最近课程</h2><div className={styles.sectionTitle}><div><p className={styles.eyebrow}>YOUR COURSES</p><h2>最近课程</h2></div><Link to="/courses">查看全部 <IconChevronRight size={16}/></Link></div>
      <div className={styles.courseGrid}>{courses.map(course => <Link to="/courses" className={`${styles.courseCard} ${styles.panel}`} key={course.initial}><div className={`${styles.courseCover} ${styles[course.tone]}`}><span className={styles.courseGlyph} aria-hidden="true">✳</span></div><div className={styles.courseContent}><span className={styles.courseInitial}>{course.initial}</span><h3>{course.title}</h3><p>{course.info}</p><div className={styles.courseFooter}><span>{course.recent}</span><span className={course.status === '已同步' ? styles.success : styles.warning}><IconCloudCheck size={15}/> {course.status}</span></div></div></Link>)}</div>
      <div className={styles.lowerGrid}><section className={styles.panel}><div className={styles.panelHeader}><h2><IconFileText size={18}/> 待处理</h2><Link to="/tasks">全部 <IconChevronRight size={15}/></Link></div><div className={styles.listRow}><span className={styles.listIcon}><IconSparkles size={17}/></span><div><strong>线性代数 · 课堂总结</strong><p>候选内容待查看</p></div><Link to="/review">查看</Link></div><div className={styles.listRow}><span className={styles.listIcon}><IconFileText size={17}/></span><div><strong>大学物理 · 课堂总结</strong><p>有一项待核对</p></div><Link to="/tasks">查看</Link></div></section>
      <section className={styles.panel}><div className={styles.panelHeader}><h2><IconBolt size={18}/> 学习动态</h2><Link to="/notifications">通知 <IconChevronRight size={15}/></Link></div><div className={styles.listRow}><span className={styles.listIcon}><IconCircleCheck size={17}/></span><div><strong>线性代数 · 转写已完成</strong><p>10月27日</p></div><IconCheck size={18}/></div><div className={styles.listRow}><span className={styles.listIcon}><IconFileText size={17}/></span><div><strong>大学物理 · 课堂已结束</strong><p>10月24日</p></div><IconCheck size={18}/></div></section></div>
    </>}
  </main>;
}
