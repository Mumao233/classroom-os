import { useEffect, useId, useRef, useState, type Ref } from 'react';
import { normalizeQuery, revokeSearchResult, searchResultKey, searchResultRoute, searchScopeNotice,
  type SearchPageData, type SearchQuery, type SearchResult } from '../../domain/search';
import { Button, Panel } from '../../ui';
import styles from './search.module.css';

export type SearchProps = {
  accountKey: string;
  online: boolean;
  search: (query: SearchQuery, signal: AbortSignal) => Promise<SearchPageData>;
  /** Re-read the exact resource under current authorization before navigating. */
  openResult: (result: SearchResult, route: string, signal: AbortSignal) => Promise<void>;
  /** Keyword from the address (`/search?q=…`), searched once when the form mounts. */
  initialQuery?: string;
  /** Reports each submitted keyword, so the page can keep it in its address. */
  onSubmitQuery?: (query: string) => void;
  /**
   * Hits pointing into content this account has since confirmed revoked: hidden on every render, so a
   * deletion event removes them at once and a reply still in flight cannot bring them back.
   */
  isRevoked?: (item: SearchResult) => boolean;
  /** A change drops the held results and any reply in flight (a deletion whose effect on them is unknown). */
  resetKey?: string | number;
};

const empty: SearchPageData = { items: [], next_cursor: null };

function isSearchTransportFailure(error: unknown): boolean {
  if (typeof error !== 'object' || error === null) return false;
  const { kind, status, code } = error as { kind?: string; status?: number; code?: string };
  return kind === 'network' && status === undefined && code === undefined;
}

export function SearchForm(props: SearchProps & { inputRef?: Ref<HTMLInputElement> }) {
  // Unmount private results, query and in-flight requests when authorization or connectivity changes.
  return <SearchSession key={JSON.stringify([props.accountKey, props.online, props.resetKey ?? null])} {...props} />;
}

function SearchSession({ online, search, openResult, inputRef, initialQuery, onSubmitQuery, isRevoked }: SearchProps & { inputRef?: Ref<HTMLInputElement> }) {
  const [input, setInput] = useState(initialQuery ?? '');
  const [page, setPage] = useState<SearchPageData>(empty);
  const [submitted, setSubmitted] = useState(false);
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState('');
  const inputId = useId();
  const form = useRef<HTMLFormElement>(null);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  // A keyword carried in the address is searched once per mount (the session remounts per account and connectivity).
  useEffect(() => {
    if (online && initialQuery && normalizeQuery(initialQuery)) void request();
    return () => active.current?.abort();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function request(cursor?: string) {
    const query = normalizeQuery(input);
    if (!query) { setNotice('请输入 1 至 200 个字符的关键词。'); return; }
    if (!cursor) onSubmitQuery?.(query);
    active.current?.abort();
    const run = new AbortController();
    active.current = run;
    setPending(true); setNotice('');
    try {
      const next = await search({ q: query, cursor }, run.signal);
      if (run.signal.aborted) return;
      setPage(previous => cursor
        ? { items: [...new Map([...previous.items, ...next.items].map(item => [searchResultKey(item), item])).values()],
            next_cursor: next.next_cursor }
        : next);
      setSubmitted(true);
    } catch (error) {
      if (run.signal.aborted) return;
      if (!isSearchTransportFailure(error)) setPage(empty);
      setNotice('搜索暂不可用，请重试。');
    } finally {
      if (!run.signal.aborted) setPending(false);
    }
  }

  async function open(item: SearchResult) {
    const route = searchResultRoute(item);
    if (!route) { setNotice('无法打开此结果。'); return; }
    const run = new AbortController();
    active.current?.abort();
    active.current = run;
    setPending(true); setNotice('');
    try {
      await openResult(item, route, run.signal);
    } catch (error) {
      if (run.signal.aborted) return;
      if (revokeSearchResult(error)) {
        setPage(previous => ({ ...previous, items: previous.items.filter(row => searchResultKey(row) !== searchResultKey(item)) }));
        setNotice('这条结果已无法访问，已从列表移除。');
        form.current?.querySelector('input')?.focus();
      } else {
        if (!isSearchTransportFailure(error)) setPage(empty);
        setNotice('暂时无法打开，请重试。');
      }
    } finally {
      if (!run.signal.aborted) setPending(false);
    }
  }

  const visible = isRevoked ? page.items.filter(item => !isRevoked(item)) : page.items;
  const scope = searchScopeNotice(online);
  if (scope) return <p role="status">{scope}</p>;
  return <div className={styles.stack}>
    <form ref={form} className={styles.form} onSubmit={event => { event.preventDefault(); void request(); }}>
      <div className={styles.field}>
        <label htmlFor={inputId}>关键词</label>
        <input ref={inputRef} id={inputId} type="search" value={input} autoComplete="off" maxLength={400} onChange={event => {
          active.current?.abort(); setInput(event.target.value); setPage(empty); setSubmitted(false);
          setPending(false); setNotice('');
        }} />
      </div>
      <Button type="submit" disabled={pending}>搜索</Button>
    </form>
    <div role="status" className={styles.status}>{notice || (pending ? '正在处理…' : submitted && !visible.length ? '没有匹配的结果。' : '')}</div>
    {visible.length > 0 && <ul className={styles.results} aria-label="搜索结果" aria-busy={pending}>
      {visible.map(item => <li key={searchResultKey(item)}><Panel>
        <Button variant="ghost" onClick={() => void open(item)} disabled={pending}>{item.title || '打开结果'}</Button>
        {item.snippet && item.snippet !== item.title && <p className={styles.snippet}>{item.snippet}</p>}
      </Panel></li>)}
    </ul>}
    {page.next_cursor && <Button variant="secondary" disabled={pending} onClick={() => void request(page.next_cursor!)}>更多结果</Button>}
  </div>;
}
