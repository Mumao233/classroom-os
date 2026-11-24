import { describe, expect, it } from 'vitest';
import { feedbackPayload } from '../src/domain/feedback';
import { needsAttention, orderNotifications, type Notification } from '../src/domain/notifications';
import { normalizeQuery, revokeSearchResult, searchResultRoute, searchScopeNotice } from '../src/domain/search';
import { searchFixture, seedSearchBackend } from '../src/mock/search-backend';

describe('PR-11c domain', () => {
  it('counts Unicode code points and keeps literal wildcard characters', () => {
    expect(normalizeQuery(' 中文%_ ')).toBe('中文%_');
    expect(normalizeQuery('甲')).toBe('甲');
    expect(normalizeQuery('𠮷'.repeat(200))).toHaveLength(400);
    for (const value of ['', '  ', '甲\0乙', '甲\u001f乙', '甲\ud800乙', '甲\udfff乙', 'a'.repeat(201)]) {
      expect(normalizeQuery(value)).toBeNull();
    }
  });

  it('maps only a locator bound to its result and drops revoked hits', () => {
    const { a } = searchFixture;
    const hits = seedSearchBackend().accounts.get(a.owner)!.hits.map(hit => hit.result);
    expect(hits.map(searchResultRoute)).toEqual([
      `/courses/${a.course}/overview`, `/classrooms/${a.classroom}/record`,
      `/review/items/${a.document}?revision=${hits[2].revision_id}`,
    ]);
    expect(searchResultRoute({ ...hits[0], locator: '//other.example/' })).toBeNull();
    expect(searchResultRoute({ ...hits[2], revision_id: searchFixture.b.document })).toBeNull();
    expect(revokeSearchResult({ code: 'GONE' })).toBe(true);
    expect(revokeSearchResult({ status: 404 })).toBe(true);
    expect(revokeSearchResult({ status: 503 })).toBe(false);
    expect(searchScopeNotice(false)).toContain('范围不完整');
    expect(searchScopeNotice(true)).toBeNull();
  });

  it('does not restore unread or unresolved state from an older duplicate', () => {
    const at = '2026-11-23T00:00:00Z';
    const fault: Notification = { id: searchFixture.a.document, object_id: null, kind: 'save_failure',
      severity: 'warning', message_code: 'SAVE_FAILED', created_at: at, read_at: at, resolved_at: null };
    expect(needsAttention(fault)).toBe(true);
    expect(needsAttention({ ...fault, kind: 'task_completed' })).toBe(false);
    const resolved = { ...fault, resolved_at: at };
    expect(orderNotifications([resolved, { ...fault, read_at: null }])).toEqual([resolved]);
    expect(needsAttention(resolved)).toBe(false);
  });

  it('keeps PostgreSQL microsecond order across paged results and read updates', () => {
    const latest: Notification = { id: searchFixture.a.document, object_id: null, kind: 'task_completed',
      severity: 'info', message_code: 'TASK_COMPLETED', created_at: '2026-11-23T00:00:00.123456Z',
      read_at: null, resolved_at: null };
    const earlier = { ...latest, id: searchFixture.b.document, created_at: '2026-11-23T08:00:00.123455+08:00' };
    const read = { ...latest, read_at: latest.created_at };
    expect(orderNotifications([earlier, latest, read])).toEqual([read, earlier]);
    expect(orderNotifications([{ ...earlier, created_at: '2026-11-23T08:00:00.1234560+08:00' }, latest])
      .map(item => item.id)).toEqual([earlier.id, latest.id]);
  });

  it('only adds an explicitly confirmed excerpt', () => {
    const body = { category: 'bug' as const, description: ' details ', request_id: null, build_id: 'dev' };
    expect(feedbackPayload(body, 'excerpt', false)).toMatchObject({ confirmed_excerpt: null, confirm_send_excerpt: false });
    expect(feedbackPayload(body, null, true)).toMatchObject({ confirmed_excerpt: null, confirm_send_excerpt: false });
    expect(feedbackPayload(body, 'excerpt', true)).toMatchObject({ description: 'details', confirmed_excerpt: 'excerpt', confirm_send_excerpt: true });
  });
});
