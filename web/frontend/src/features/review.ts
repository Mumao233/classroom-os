import type { QueryClient } from '@tanstack/react-query';
import type { components } from '../generated/openapi';
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

/**
 * A classroom deletion removes that classroom from every cached review projection at once (lists,
 * documents, their revisions and snapshots), without waiting for a refetch that may fail offline.
 */
export function evictClassroomFromReview(client: QueryClient, classId: string) {
  type Doc = components['schemas']['ReviewDocument'];
  const revoked = new Set<string>();
  for (const [key, data] of client.getQueriesData<{ items: Doc[]; next_cursor: null }>({ predicate: query => query.queryKey.includes('review-library') })) {
    if (!data) continue;
    for (const doc of data.items) if (doc.class_session_id === classId) revoked.add(doc.id);
    client.setQueryData(key, { ...data, items: data.items.filter(doc => doc.class_session_id !== classId) });
  }
  for (const [key, data] of client.getQueriesData<Doc>({ predicate: query => query.queryKey.includes('review-document') })) if (data?.class_session_id === classId) { revoked.add(data.id); client.removeQueries({ queryKey: key, exact: true }); }
  client.removeQueries({ predicate: query => (query.queryKey.includes('review-revisions') || query.queryKey.includes('review-document')) && query.queryKey.some(part => typeof part === 'string' && revoked.has(part)) });
  client.removeQueries({ predicate: query => query.queryKey.includes('source-snapshot') && (query.state.data as { class_session_id?: string } | undefined)?.class_session_id === classId });
}

/**
 * A cascade deletion of a course or semester evicts the review projections of every classroom it
 * contained (known from cached course and classroom data). Moved children are not passed here.
 */
export function evictScopeFromReview(client: QueryClient, scope: { kind: 'course' | 'semester'; id: string }) {
  type Course = components['schemas']['Course'];
  type Session = components['schemas']['ClassSession'];
  const courseIds = new Set<string>(scope.kind === 'course' ? [scope.id] : []);
  if (scope.kind === 'semester') {
    for (const [, data] of client.getQueriesData<{ items: Course[] }>({ predicate: query => query.queryKey.includes('courses') })) for (const course of data?.items ?? []) if (course.semester_id === scope.id) courseIds.add(course.id);
    for (const [, data] of client.getQueriesData<Course>({ predicate: query => query.queryKey.includes('course') })) if (data?.semester_id === scope.id) courseIds.add(data.id);
  }
  const classIds = new Set<string>();
  for (const [, data] of client.getQueriesData<{ items: Session[] }>({ predicate: query => query.queryKey.includes('class-sessions') })) for (const session of data?.items ?? []) if (courseIds.has(session.course_id)) classIds.add(session.id);
  for (const [, data] of client.getQueriesData<Session>({ predicate: query => query.queryKey.includes('class-session') })) if (data && courseIds.has(data.course_id)) classIds.add(data.id);
  for (const classId of classIds) evictClassroomFromReview(client, classId);
  // The classrooms themselves and everything keyed by them (questions, transcripts, tasks…) leave the cache too,
  // so nothing of a deleted course is rendered from cache while a refetch cannot succeed (offline).
  for (const [key, data] of client.getQueriesData<{ items: Session[] }>({ predicate: query => query.queryKey.includes('class-sessions') })) {
    if (data) client.setQueryData(key, { ...data, items: data.items.filter(session => !classIds.has(session.id) && !courseIds.has(session.course_id)) });
  }
  // The classroom detail entry is kept: the account shell relies on its failed refetch (NOT_FOUND) to
  // revoke a capture still bound to that classroom. Everything else keyed by the classroom is dropped.
  client.removeQueries({ predicate: query => !query.queryKey.includes('class-session') && query.queryKey.some(part => typeof part === 'string' && classIds.has(part)) });
  return classIds;
}

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
