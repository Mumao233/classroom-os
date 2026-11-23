import type { ReviewLibraryQuery } from '../api/client';
import { parseReadingMemory, rememberReading, type ReadingMemory } from '../domain/review';
import { useAccountQuery, useAppData } from './data';

/**
 * PR-10b review data. Pages read through the account-scoped query cache (account + epoch in every
 * key) and write through the API client with CAS and stable keys; nothing here talks to storage
 * except the device-local reading memory, which holds document IDs and scroll offsets only.
 */
export const useReviewLibrary = (query: ReviewLibraryQuery) => useAccountQuery(['review-library', query], (api, signal) => api.reviewLibrary(query, signal));
export const useReviewDocument = (id: string) => useAccountQuery(['review-document', id], (api, signal) => api.reviewDocument(id, signal));
export const useReviewRevisions = (id: string) => useAccountQuery(['review-revisions', id], (api, signal) => api.reviewRevisions(id, signal));
export const useSourceSnapshot = (id: string | null) => useAccountQuery(['source-snapshot', id], (api, signal) => api.sourceSnapshot(id!, signal), id !== null);
export const useTranscriptVersions = (classId: string) => useAccountQuery(['transcript-versions', classId], (api, signal) => api.transcriptVersions(classId, signal));
export const useTranscriptSegments = (versionId: string | null) => useAccountQuery(['transcript-segments', versionId], (api, signal) => api.transcriptSegments(versionId!, signal), versionId !== null);
/** Every segment of every version of a class: frozen sources may point into any of them. */
export const useClassSegments = (classId: string | null) => useAccountQuery(['class-segments', classId], async (api, signal) => {
  const versions = await api.transcriptVersions(classId!, signal);
  const pages = await Promise.all(versions.items.map(version => api.transcriptSegments(version.id, signal)));
  return pages.flatMap(page => page.items);
}, classId !== null);

// Deleted and revoked content leaves these projections through the account-shell revocation ledger
// (features/revocation.ts), not through per-page eviction.

const STORAGE_KEY = 'classroom-os.review.reading.v1';
function storageKey(accountKey: string) { return `${STORAGE_KEY}:${accountKey.split(':')[0]}`; }
/** Device-local "continue" memory (no content); failures to read or write are ignored. */
export function useReadingMemory(): { read(): ReadingMemory; remember(id: string, offset: number): void } {
  const { accountKey } = useAppData();
  const key = storageKey(accountKey);
  return {
    read() { try { return parseReadingMemory(globalThis.localStorage?.getItem(key) ?? null); } catch { return { last: null, positions: {} }; } },
    remember(id, offset) {
      try {
        const next = rememberReading(parseReadingMemory(globalThis.localStorage?.getItem(key) ?? null), id, offset);
        globalThis.localStorage?.setItem(key, JSON.stringify(next));
      } catch { /* storage unavailable: reading continues without memory */ }
    },
  };
}
