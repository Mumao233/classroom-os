import type { ClassroomOSWebV1EventEnvelope } from '../generated/events';

// Fixed synthetic identities and clock. These are event envelopes, not HTTP fields.
const timestamp = '2026-10-28T08:00:00Z';
const classroom = '22222222-2222-4222-8222-222222222222';
const stream = '33333333-3333-4333-8333-333333333333';
const segment = '44444444-4444-4444-8444-444444444444';
const epoch = '55555555-5555-4555-8555-555555555555';
const chunk = '66666666-6666-4666-8666-666666666666';
const transcriptFinal = {
  protocol_version: 1, type: 'transcript.final', sent_at: timestamp,
  event_id: '77777777-7777-4777-8777-777777777777', cursor: 'mock.cursor.2',
  data: { class_session_id: classroom, stream_id: stream, segment_id: segment,
    segment_revision_id: '88888888-8888-4888-8888-888888888888', transcript_version_id: stream,
    sequence: 5, audio_offset_ms: 1000, end_ms: 2000, status: 'final',
    text: '合成的正式片段', provider_request_id: null, revision: 2, timestamp_source: 'capture_mapping' },
} as const;

export const eventScripts = {
  transcript: [
    { protocol_version: 1, type: 'transcript.partial', sent_at: timestamp, connection_seq: 1,
      data: { class_session_id: classroom, stream_id: stream, segment_id: segment, sequence: 5,
        audio_offset_ms: 1000, status: 'partial', text: '合成的临时片段', provider_request_id: null, revision: 1 } },
    transcriptFinal,
    transcriptFinal, // Duplicate durable delivery is intentional.
    { ...transcriptFinal, event_id: '99999999-9999-4999-8999-999999999999', cursor: 'mock.cursor.1',
      data: { ...transcriptFinal.data, revision: 1, text: '迟到的旧片段' } },
  ],
  upload: [
    { protocol_version: 1, type: 'capture.frame_received', sent_at: timestamp, connection_seq: 2,
      data: { chunk_id: chunk, frame_seq: 1, durable: false } },
    { protocol_version: 1, type: 'audio.chunk_durable', sent_at: timestamp,
      event_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4', cursor: 'mock.cursor.3',
      data: { class_session_id: classroom, capture_epoch_id: epoch, chunk_id: chunk, seq: 1,
        sha256: 'a'.repeat(64), end_sample: 16000,
        receipt_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb4', durable_at: timestamp } },
  ],
  taskUnknown: [
    { protocol_version: 1, type: 'task.changed', sent_at: timestamp,
      event_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa5', cursor: 'mock.cursor.4',
      data: { task_id: segment, class_session_id: classroom, revision: 1, state: 'unknown', error_code: null, may_have_charged: true } },
  ],
  deletion: [
    { protocol_version: 1, type: 'object.deleted', sent_at: timestamp,
      event_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa6', cursor: 'mock.cursor.5',
      data: { object_type: 'audio', object_id: chunk, deletion_id: epoch, state: 'access_revoked' } },
  ],
  classroomDeletion: [
    { protocol_version: 1, type: 'object.deleted', sent_at: timestamp,
      event_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa8', cursor: 'mock.cursor.7',
      data: { object_type: 'class_session', object_id: classroom, deletion_id: epoch, state: 'access_revoked' } },
  ],
  leaseLoss: [
    { protocol_version: 1, type: 'capture.lease_lost', sent_at: timestamp,
      event_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa7', cursor: 'mock.cursor.6',
      data: { class_session_id: classroom, capture_epoch_id: epoch, fence_token: 1, reason: 'revoked' } },
  ],
  revocation: [
    { protocol_version: 1, type: 'error', sent_at: timestamp, connection_seq: 3,
      data: { code: 'AUTH_REVOKED', message: '模拟会话已撤销', request_id: segment, retryable: false } },
  ],
} satisfies Record<string, readonly ClassroomOSWebV1EventEnvelope[]>;

export type EventScriptName = keyof typeof eventScripts;
