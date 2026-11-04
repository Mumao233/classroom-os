import { Link } from 'react-router';
import styles from '../app/Shell.module.css';

export function Placeholder({ title, detail }: { title: string; detail: string }) {
  return <main id="main-content" className={styles.page} tabIndex={-1}>
    <div className={styles.pageHeading}><div><p className={styles.eyebrow}>CLASSROOM OS · 开发预览</p><h1>{title}</h1><p>{detail}</p></div></div>
    <section className={`${styles.panel} ${styles.placeholder}`}>
      <span className={styles.placeholderIcon} aria-hidden="true">✳</span>
      <h2>页面正在开发中</h2><p>这里是 PR-09a 的导航占位。完整功能将在后续工作包中实现。</p>
      <Link className={styles.secondaryButton} to="/dashboard">返回首页</Link>
    </section>
  </main>;
}
