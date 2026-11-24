import { useCallback, useEffect, useRef, useState } from 'react';
import { needsAttention, notificationMessages, notificationSeverity, orderNotifications,
  type Notification, type NotificationPage } from '../../domain/notifications';
import { Button, Panel, StatusBadge } from '../../ui';
import styles from './NotificationCenter.module.css';

type Props = {
  accountKey: string;
  list: (cursor: string | null, signal: AbortSignal) => Promise<NotificationPage>;
  markRead: (id: string, key: string, signal: AbortSignal) => Promise<Notification>;
};

function isAuthorizationFailure(error: unknown): boolean {
  if (typeof error !== 'object' || error === null) return false;
  const { status, code } = error as { status?: number; code?: string };
  return status === 401 || status === 403 || code === 'UNAUTHENTICATED'
    || code === 'FORBIDDEN' || code === 'SERVICE_DISABLED';
}

export function NotificationCenter(props: Props) {
  return <Center key={props.accountKey} {...props} />;
}

function Center({ list, markRead }: Props) {
  const [items, setItems] = useState<Notification[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(true);
  const [writing, setWriting] = useState(false);
  const [notice, setNotice] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const listing = useRef<AbortController | null>(null);
  const marking = useRef<AbortController | null>(null);
  const listSequence = useRef(0);
  const keys = useRef(new Map<string, string>());
  const clearAuthorization = useCallback(() => {
    listSequence.current += 1;
    listing.current?.abort();
    marking.current?.abort();
    keys.current.clear();
    setItems([]); setCursor(null); setLoaded(false);
    setBusy(false); setWriting(false);
    setNotice('通知已无法访问，请重新加载。');
  }, []);

  const load = useCallback(async (next: string | null) => {
    listing.current?.abort();
    const run = new AbortController();
    listing.current = run;
    const sequence = ++listSequence.current;
    const isCurrent = () => sequence === listSequence.current && !run.signal.aborted;
    setBusy(true); setNotice('');
    try {
      const page = await list(next, run.signal);
      if (!isCurrent()) return;
      setItems(previous => orderNotifications(next ? [...previous, ...page.items] : page.items));
      setCursor(page.next_cursor); setLoaded(true);
    } catch (error) {
      if (!isCurrent()) return;
      if (isAuthorizationFailure(error)) clearAuthorization();
      else setNotice('通知暂不可用，请重试。');
    } finally {
      if (isCurrent()) setBusy(false);
    }
  }, [list, clearAuthorization]);

  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
    return () => marking.current?.abort();
  }, []);
  useEffect(() => {
    void load(null);
    return () => listing.current?.abort();
  }, [load]);

  function supersedeList() {
    listSequence.current += 1;
    listing.current?.abort();
    setBusy(false);
  }

  async function read(id: string) {
    const key = keys.current.get(id) ?? crypto.randomUUID();
    keys.current.set(id, key);
    const run = new AbortController();
    marking.current = run;
    setWriting(true); setNotice('');
    try {
      const item = await markRead(id, key, run.signal);
      if (run.signal.aborted) return;
      supersedeList();
      keys.current.delete(id);
      setItems(previous => previous.some(row => row.id === id) ? orderNotifications([...previous, item]) : previous);
    } catch (error) {
      if (run.signal.aborted) return;
      if (isAuthorizationFailure(error)) { clearAuthorization(); return; }
      const { status, code } = typeof error === 'object' && error !== null
        ? error as { status?: number; code?: string } : {};
      if (status === 404 || status === 410 || code === 'NOT_FOUND' || code === 'GONE') {
        supersedeList();
        keys.current.delete(id);
        setItems(previous => previous.filter(item => item.id !== id));
        setNotice('这条通知已无法访问，已从列表移除。');
        heading.current?.focus({ preventScroll: true });
      } else setNotice('暂时无法标为已读，请重试。');
    } finally {
      if (!run.signal.aborted) setWriting(false);
    }
  }

  return <main id="main-content" className={styles.page}>
    <h1 ref={heading} tabIndex={-1}>通知</h1>
    <p role="status">{notice || (busy || writing ? '正在处理…' : loaded && !items.length ? '暂无通知。' : '')}</p>
    {!loaded && !busy && <Button onClick={() => void load(null)}>重新加载</Button>}
    <ul className={styles.list} aria-label="通知列表" aria-busy={busy || writing}>
      {items.map(item => {
        const severity = notificationSeverity[item.severity];
        return <li key={item.id}><Panel className={styles.notice}>
          <div><StatusBadge tone={severity.tone}>{severity.label}</StatusBadge>
            <h2>{notificationMessages[item.message_code] ?? '课堂助手通知'}</h2>
            <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('zh-CN')}</time>
          </div>
          <div className={styles.actions}>
            {item.resolved_at && <StatusBadge tone="success">已解决</StatusBadge>}
            {needsAttention(item) && <StatusBadge tone="warning">待处理</StatusBadge>}
            {item.read_at ? <span>已读</span> : <Button disabled={writing} variant="secondary" onClick={() => void read(item.id)}>标为已读</Button>}
          </div>
        </Panel></li>;
      })}
    </ul>
    {cursor && <Button variant="secondary" disabled={busy || writing} onClick={() => void load(cursor)}>更多通知</Button>}
  </main>;
}
