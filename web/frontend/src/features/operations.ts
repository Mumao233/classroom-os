import { useMemo, useSyncExternalStore } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { ApiClientError, type ApiClient } from '../api/client';
import type { components } from '../generated/openapi';
import type { SearchQuery, SearchResult } from '../domain/search';
import { readTranscript } from './classroom';
import { useAppData } from './data';
import { isAuthoritativeDenial, type RevocationLedger, type SearchHitRef } from './revocation';

type S = components['schemas'];

/** A target that no longer exists for this account: the search result is removed, never shown from cache. */
const gone = () => new ApiClientError('http', 404);

/**
 * Re-authorizes a search hit through the same reads, and the same cache entries, the destination page
 * uses before navigating (02 §5.5: a hit is only a pointer; the page read decides). Each read is fresh
 * (`staleTime: 0`), so the page opens on what the server just served, and a 401 or denial lands where
 * the account's revocation ledger sees it. Authorization errors propagate unchanged, so the search UI
 * can tell a removed target (404/410) from a lost session or closed gate.
 */
export async function authorizeSearchResult(client: QueryClient, accountKey: string, api: ApiClient, result: SearchResult, signal: AbortSignal): Promise<void> {
  const settled = () => { if (signal.aborted) throw new DOMException('Aborted', 'AbortError'); };
  // A new server read: never joins a read already in flight, whose reply may predate a revocation. An
  // abandoned opening (palette closed, keyword edited) starts nothing more and stops waiting at once; its
  // read is cancelled only if nobody else uses that entry. A mounted page reading the same entry keeps it
  // (it replaced the page's own request), or reads again if the cancellation above already took its request.
  const read = async <T>(parts: readonly unknown[], fn: (signal: AbortSignal) => Promise<T>) => {
    settled();
    const queryKey = ['account', accountKey, ...parts];
    const watched = () => (client.getQueryCache().find({ queryKey, exact: true })?.getObserversCount() ?? 0) > 0;
    await client.cancelQueries({ queryKey, exact: true });
    if (signal.aborted) {
      if (watched()) void client.refetchQueries({ queryKey, exact: true });
      settled();
    }
    const fetched = client.fetchQuery({ queryKey, queryFn: ({ signal: own }) => fn(own), staleTime: 0, retry: false });
    let abandon: () => void = () => undefined;
    const abandoned = new Promise<never>((_, reject) => {
      abandon = () => {
        if (!watched()) void client.cancelQueries({ queryKey, exact: true });
        reject(new DOMException('Aborted', 'AbortError'));
      };
    });
    signal.addEventListener('abort', abandon, { once: true });
    try {
      return await Promise.race([fetched, abandoned]);
    } catch (error) {
      settled(); // abandoned: report the abort, not the cancellation it caused
      throw error;
    } finally {
      signal.removeEventListener('abort', abandon);
      fetched.catch(() => undefined);
      abandoned.catch(() => undefined);
    }
  };
  switch (result.object_type) {
    case 'course':
      await read(['course', result.object_id], own => api.course(result.object_id, own));
      return settled();
    case 'class_session':
      await read(['class-session', result.object_id], own => api.classSession(result.object_id, own));
      return settled();
    case 'transcript_segment': {
      const [, classId, versionId, segmentId] = result.locator.split('/');
      if (!classId || !versionId || !segmentId) throw gone();
      await read(['class-session', classId], own => api.classSession(classId, own));
      const transcript = await read(['transcript', classId], own => readTranscript(api, classId, own));
      settled();
      // The record must show the version the hit came from, and that version must still serve the
      // exact formal revision: a superseded or changed segment is stale, not something to open.
      if (!transcript.versions.some(version => version.id === versionId)
          || !transcript.segments.some(item => item.id === segmentId && item.segment_revision_id === result.revision_id
            && item.class_session_id === classId)) throw gone();
      return;
    }
    case 'review_document':
    case 'card': {
      if (!result.revision_id) throw gone();
      const revisionId = result.revision_id;
      const doc = await read(['review-document', result.object_id], own => api.reviewDocument(result.object_id, own));
      // The reader's own revision list (it enforces source access) must offer this exact revision.
      const revisions = await read(['review-revisions', result.object_id], own => api.reviewRevisions(result.object_id, own));
      settled();
      if (doc.id !== result.object_id || !revisions.items.some(item => item.id === revisionId && item.document_id === result.object_id)) throw gone();
      return;
    }
  }
}

/** Reports a refusal of a request made outside the query cache to the ledger, then rethrows it. */
async function observed<T>(revocation: RevocationLedger | undefined, run: () => Promise<T>): Promise<T> {
  try { return await run(); } catch (error) { revocation?.observeError(error); throw error; }
}

export type Operations = {
  search: (query: SearchQuery, signal: AbortSignal) => Promise<S['SearchResultPage']>;
  authorize: (result: SearchResult, signal: AbortSignal) => Promise<void>;
  notificationPage: (cursor: string | null, signal: AbortSignal) => Promise<S['NotificationPage']>;
  readNotification: (id: string, key: string, signal: AbortSignal) => Promise<S['Notification']>;
  submitFeedback: (body: S['Feedback'], key: string, signal: AbortSignal) => Promise<S['Acknowledged']>;
};

export function createOperations(client: QueryClient, accountKey: string, api: ApiClient, revocation?: RevocationLedger): Operations {
  const notifications = ['account', accountKey, 'notifications'];
  return {
    search: async (query, signal) => {
      const page = await observed(revocation, () => api.search(query, signal));
      // What the hits name was served to this account: a later denial of it revokes every dependent hit.
      revocation?.recordSearchServed(page.items);
      return page;
    },
    // Reads through the cache: the ledger already observes them.
    authorize: (result, signal) => authorizeSearchResult(client, accountKey, api, result, signal),
    notificationPage: async (cursor, signal) => {
      try { return await observed(revocation, () => api.notificationPage(cursor, signal)); }
      catch (error) {
        // The list itself is refused: the dashboard's snapshot of it must not stay on screen either.
        if (isAuthoritativeDenial(error)) void client.resetQueries({ queryKey: notifications });
        throw error;
      }
    },
    readNotification: async (id, key, signal) => {
      let notice: S['Notification'];
      try { notice = await observed(revocation, () => api.readNotification(id, key, signal)); }
      catch (error) {
        // The server refuses this notice (its source is gone): the dashboard's snapshot drops it now, also
        // offline and without waiting for a refetch the shell's cache would not start.
        if (isAuthoritativeDenial(error)) {
          client.setQueriesData<S['NotificationPage']>({ queryKey: notifications }, page => page ? { ...page, items: page.items.filter(item => item.id !== id) } : page);
        }
        throw error;
      }
      // The dashboard's notification card reads the same account's notifications.
      void client.invalidateQueries({ queryKey: notifications });
      return notice;
    },
    submitFeedback: (body, key, signal) => observed(revocation, () => api.submitFeedback(body, key, signal)),
  };
}

/**
 * The account's search, notification and feedback requests. A 401 on any of them retires the account
 * epoch through the revocation ledger, exactly like a 401 on a cached read or a mutation.
 */
export function useOperations(): Operations {
  const client = useQueryClient();
  const { api, accountKey, revocation } = useAppData();
  return useMemo(() => createOperations(client, accountKey, api, revocation), [client, accountKey, api, revocation]);
}

const noSubscription = () => () => undefined;
const noVersion = () => 0;

/** Changes whenever the account's confirmed revocations change. */
export function useRevocationVersion(): number {
  const { revocation } = useAppData();
  return useSyncExternalStore(revocation?.subscribe ?? noSubscription, revocation?.version ?? noVersion);
}

/**
 * Search hits are held by the search views, outside the query cache: a confirmed revocation (deletion
 * event or receipt) hides the hits that point into it at once, including hits of a reply still in flight.
 * The returned predicate changes identity whenever revocations change.
 */
export function useSearchHitRevoked(): (hit: SearchHitRef) => boolean {
  const { revocation } = useAppData();
  const version = useRevocationVersion();
  // One membership snapshot per revocation change, shared by every row of the list.
  return useMemo(() => { void version; return revocation?.searchHitFilter() ?? (() => false); }, [revocation, version]);
}

/**
 * Changes when held search results must be dropped rather than filtered (a cascade or ambiguous
 * deletion, a replaced data set): search views key their form on it, which also aborts replies in flight.
 */
export function useSearchEpoch(): number {
  const { revocation } = useAppData();
  return useSyncExternalStore(revocation?.subscribe ?? noSubscription, revocation?.searchEpoch ?? noVersion);
}
