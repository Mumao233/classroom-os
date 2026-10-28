import type { components } from '../generated/openapi';

// Fixed, synthetic fixtures. No actual account or classroom material.
export const coursePage = { items: [], next_cursor: null } satisfies components['schemas']['CoursePage'];
export const semesterPage = { items: [], next_cursor: null } satisfies components['schemas']['SemesterPage'];
export const meta = {
  product_version: '0.1.0-alpha.0', api_version: 'web/v1', schema_revision: '0.1.0',
  minimum_client_build: '0.1.0-alpha.0', contract_version: '0.1.0', server_time: '2026-10-28T08:00:00Z',
  features: { registration: false, asr: false, ai: false, persistent_recovery: false, trusted_cache: false },
  limits: { max_upload_bytes: 1000000, max_archive_entries: 100, max_audio_chunk_bytes: 100000, capture_lease_seconds: 30, heartbeat_seconds: 10 },
} satisfies components['schemas']['Meta'];
