import { lazy, Suspense } from 'react';
import { BrowserRouter } from 'react-router';
import styles from './Shell.module.css';
const AuthWorkbench = lazy(() => import('./AuthWorkbench'));
const RecoveryReceiver = lazy(() => import('./RecoveryReceiver'));

// Vite removes the entire import from production builds. A missing API must never
// silently turn into a pretend authenticated session.
const MockWorkbench = import.meta.env.MODE === 'mock'
  ? lazy(() => import('./MockWorkbench'))
  : null;

export default function App({ mock }: { mock: boolean }) {
  if (mock && MockWorkbench) {
    return <BrowserRouter><Suspense fallback={<main className={styles.unavailable}>正在加载开发预览…</main>}><MockWorkbench /></Suspense></BrowserRouter>;
  }
  return <Suspense fallback={<main className={styles.unavailable}>正在检查会话…</main>}>{location.pathname==='/recover-receiver'?<RecoveryReceiver/>:<AuthWorkbench />}</Suspense>;
}
