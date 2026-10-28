import type { components } from '../generated/openapi';

// Fixed, synthetic fixtures. No actual account or classroom material.
type Course = components['schemas']['Course'];
const course = (id: string, name: string, color: string, icon: string, count: number): Course => ({
  id, name, color_token: color, icon_token: icon, class_session_count: count,
  revision: 1, created_at: '2026-10-01T08:00:00Z', updated_at: '2026-10-28T08:00:00Z',
  semester_id: '11111111-1111-4111-8111-111111111111', english_name: '', language: 'auto',
  teacher: '', room: '', archived: false, last_class_session_at: '2026-10-27T08:00:00Z',
});
export const coursePages = {
  A: { items: [course('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1', '线性代数', 'blue', 'LA', 12), course('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2', '数据结构与算法分析（双语）', 'teal', 'DS', 9), course('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3', '大学物理 II', 'violet', 'MP', 15)], next_cursor: null },
  B: { items: [course('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1', '组合数学（模拟账号 B）', 'blue', 'CM', 2)], next_cursor: null },
} satisfies Record<'A' | 'B', components['schemas']['CoursePage']>;
// Retained export for callers that specifically need the empty scenario.
export const coursePage = { items: [], next_cursor: null } satisfies components['schemas']['CoursePage'];
export const unauthenticated = { error: { code: 'UNAUTHENTICATED', message: '模拟会话已撤销', request_id: 'cccccccc-cccc-4ccc-8ccc-ccccccccccc1', retryable: false, retry_after_seconds: null, current_revision: null, conflict_id: null, task_id: null } } satisfies components['schemas']['Error'];
export const semesterPage = { items: [], next_cursor: null } satisfies components['schemas']['SemesterPage'];
export const meta = {
  product_version: '0.1.0-alpha.0', api_version: 'web/v1', schema_revision: '0.1.0',
  minimum_client_build: '0.1.0-alpha.0', contract_version: '0.1.0', server_time: '2026-10-28T08:00:00Z',
  features: { registration: false, asr: false, ai: false, persistent_recovery: false, trusted_cache: false },
  limits: { max_upload_bytes: 1000000, max_archive_entries: 100, max_audio_chunk_bytes: 100000, capture_lease_seconds: 30, heartbeat_seconds: 10 },
} satisfies components['schemas']['Meta'];
