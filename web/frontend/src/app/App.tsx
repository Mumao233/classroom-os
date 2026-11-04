import { lazy, Suspense } from 'react';
import { BrowserRouter } from 'react-router';
import styles from './Shell.module.css';

// Vite removes the entire import from production builds. A missing API must never
// silently turn into a pretend authenticated session.
const MockWorkbench = import.meta.env.MODE === 'mock'
  ? lazy(() => import('./MockWorkbench'))
  : null;

export default function App({ mock }: { mock: boolean }) {
  if (mock && MockWorkbench) {
    return <BrowserRouter><Suspense fallback={<main className={styles.unavailable}>正在加载开发预览…</main>}><MockWorkbench /></Suspense></BrowserRouter>;
  }
  return <main className={styles.unavailable}><div className={styles.unavailableCard}><div className={styles.brandMark} aria-hidden="true" /><p className={styles.eyebrow}>CLASSROOM OS · WEB ALPHA</p><h1>服务暂不可用</h1><p>真实认证与服务端接口尚未接入。请连接可用的服务后重试。</p></div></main>;
}
