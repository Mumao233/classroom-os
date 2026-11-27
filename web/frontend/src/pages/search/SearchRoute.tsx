import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router';
import { useAppData } from '../../features/data';
import { useOperations, useSearchEpoch, useSearchHitRevoked } from '../../features/operations';
import type { SearchResult } from '../../domain/search';
import { SearchPage } from './SearchPage';

/**
 * `/search?q=…`: keyword search over the account's formal content; never a model call.
 *
 * A keyword this page wrote into the address is stamped with the account epoch that searched it.
 * An account switch remounts the shell before its navigation away commits, so the next account can
 * briefly mount on the previous address: it ignores a keyword stamped by another epoch instead of
 * searching it (and rewriting the address) under its own session. An unstamped address (opened
 * directly or linked) is searched by whoever opened it.
 *
 * The router keeps this page mounted across `/search` navigations, so a keyword in the address that
 * the form did not write itself (the header link, Back, Forward) starts a fresh form for it.
 */
export function SearchRoute() {
  const { accountKey, online } = useAppData();
  const operations = useOperations();
  const isRevoked = useSearchHitRevoked();
  const searchEpoch = useSearchEpoch();
  const navigate = useNavigate();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const addressQuery = params.get('q') ?? '';
  const stamp = (location.state as { searchAccount?: unknown } | null)?.searchAccount;
  const initialQuery = addressQuery && (stamp === undefined || stamp === accountKey) ? addressQuery : undefined;
  const written = useRef(addressQuery);
  const [generation, setGeneration] = useState(0);
  useEffect(() => {
    if (addressQuery === written.current) return;
    written.current = addressQuery;
    setGeneration(value => value + 1);
  }, [addressQuery]);
  // A direct entry is claimed for this account at once (online or not), and another account's keyword
  // leaves the address. Only while the browser still shows this entry: an account switch may already
  // have navigated away (that navigation commits later), and rewriting the address would undo it.
  useEffect(() => {
    if (!addressQuery || stamp === accountKey) return;
    if (window.location.search !== location.search || !window.location.pathname.endsWith('/search')) return;
    if (stamp === undefined) { setParams({ q: addressQuery }, { replace: true, state: { searchAccount: accountKey } }); return; }
    written.current = '';
    setParams({}, { replace: true, state: { searchAccount: accountKey } });
  }, [addressQuery, stamp, accountKey, location.search, setParams]);
  // A hit is a pointer: the destination's own read decides before the page opens.
  const openResult = useCallback(async (result: SearchResult, route: string, signal: AbortSignal) => {
    await operations.authorize(result, signal);
    navigate(route);
  }, [operations, navigate]);
  const onSubmitQuery = useCallback((q: string) => {
    written.current = q;
    setParams({ q }, { replace: true, state: { searchAccount: accountKey } });
  }, [setParams, accountKey]);
  return <SearchPage key={generation} accountKey={accountKey} online={online} search={operations.search} openResult={openResult}
    initialQuery={initialQuery} onSubmitQuery={onSubmitQuery} isRevoked={isRevoked} resetKey={searchEpoch} />;
}
