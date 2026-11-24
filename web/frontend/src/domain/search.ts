import type { components } from '../generated/openapi';

export type SearchResult = components['schemas']['SearchResult'];
export type SearchPageData = components['schemas']['SearchResultPage'];
export type SearchQuery = { q: string; cursor?: string; limit?: number; course_id?: string; class_session_id?: string };

export function normalizeQuery(value: string): string | null {
  const query = value.trim();
  if (!query || [...value].length > 200) return null;
  for (const character of query) {
    const code = character.codePointAt(0)!;
    if (code < 0x20 || (code >= 0xd800 && code <= 0xdfff)) return null;
  }
  return query;
}

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export function searchResultRoute(result: SearchResult): string | null {
  const [scope, ...ids] = result.locator.split('/');
  if (!ids.every(id => uuid.test(id))) return null;
  const [id, revision, segment] = ids;
  switch (result.object_type) {
    case 'course':
      return scope === 'course' && ids.length === 1 && id === result.object_id && result.class_session_id === null
        && result.revision_id === null ? `/courses/${id}/overview` : null;
    case 'class_session':
      return scope === 'classroom' && ids.length === 1 && id === result.object_id && id === result.class_session_id
        && result.revision_id === null ? `/classrooms/${id}/record` : null;
    case 'transcript_segment':
      return scope === 'transcript' && ids.length === 3 && id === result.class_session_id
        && segment === result.object_id && result.revision_id && uuid.test(result.revision_id)
        ? `/classrooms/${id}/record?transcriptVersion=${revision}&segment=${segment}&segmentRevision=${result.revision_id}` : null;
    case 'review_document':
    case 'card':
      return scope === 'review' && ids.length === 2 && id === result.object_id && revision === result.revision_id
        && result.class_session_id && uuid.test(result.class_session_id) ? `/review/items/${id}?revision=${revision}` : null;
  }
}

export function revokeSearchResult(error: unknown): boolean {
  if (typeof error !== 'object' || error === null) return false;
  const { status, code } = error as { status?: number; code?: string };
  return status === 404 || status === 410 || code === 'NOT_FOUND' || code === 'GONE';
}

export function searchScopeNotice(online: boolean, unlockedSearch = false): string | null {
  if (online) return null;
  return unlockedSearch ? '离线搜索仅覆盖当前账号已解锁、未过期的缓存，结果范围不完整。'
    : '离线期间无法搜索，搜索范围不完整。';
}

export const searchResultKey = (item: SearchResult) => `${item.object_type}:${item.object_id}:${item.revision_id ?? ''}`;
