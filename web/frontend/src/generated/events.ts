/* Generated from the PR-01 events schema. Do not edit. */

export type ClassroomOSWebV1EventEnvelope =
  | Hello
  | Authenticate
  | Resume
  | ReplayComplete
  | ResyncRequired
  | Heartbeat
  | CaptureReady
  | CaptureChunkStart
  | FrameHeader
  | FrameReceived
  | ChunkDurable
  | TranscriptPartial
  | TranscriptFinal
  | TaskChanged
  | AIDelta
  | ObjectChanged
  | ObjectDeleted
  | QuotaChanged
  | NotificationCreated
  | CaptureLost
  | WsError;

export interface Hello {
  protocol_version: 1;
  type: "hello";
  sent_at: string;
  data: {
    connection_id: string;
    heartbeat_seconds: number;
    resume_cursor: string | null;
  };
  connection_seq: number;
}
export interface Authenticate {
  protocol_version: 1;
  type: "authenticate";
  sent_at: string;
  data: {
    ticket: string;
  };
  connection_seq: number;
}
export interface Resume {
  protocol_version: 1;
  type: "resume";
  sent_at: string;
  data: {
    after: string | null;
  };
  connection_seq: number;
}
export interface ReplayComplete {
  protocol_version: 1;
  type: "replay.complete";
  sent_at: string;
  data: {
    cursor: string;
  };
  connection_seq: number;
}
export interface ResyncRequired {
  protocol_version: 1;
  type: "resync.required";
  sent_at: string;
  data: {
    reason: "cursor_expired" | "incompatible_version";
    snapshot_path: "/api/web/v1/sync/bootstrap";
  };
  connection_seq: number;
}
export interface Heartbeat {
  protocol_version: 1;
  type: "heartbeat";
  sent_at: string;
  data: {
    last_cursor: string | null;
  };
  connection_seq: number;
}
export interface CaptureReady {
  protocol_version: 1;
  type: "capture.ready";
  sent_at: string;
  data: {
    class_session_id: string;
    capture_epoch_id: string;
    fence_token: number;
    max_frame_bytes: number;
    inflight_window_bytes: number;
  };
  connection_seq: number;
}
export interface CaptureChunkStart {
  protocol_version: 1;
  type: "capture.chunk_start";
  sent_at: string;
  data: {
    op_id: string;
    class_session_id: string;
    capture_epoch_id: string;
    fence_token: number;
    chunk_id: string;
    seq: number;
    start_sample: number;
    end_sample: number;
    byte_length: number;
    sha256: string;
  };
  connection_seq: number;
}
export interface FrameHeader {
  protocol_version: 1;
  type: "capture.frame";
  sent_at: string;
  data: {
    class_session_id: string;
    capture_epoch_id: string;
    fence_token: number;
    chunk_id: string;
    frame_seq: number;
    start_sample: number;
    sample_count: number;
    byte_length: number;
    sha256: string;
    chunk_final_frame: boolean;
  };
  connection_seq: number;
}
export interface FrameReceived {
  protocol_version: 1;
  type: "capture.frame_received";
  sent_at: string;
  data: {
    chunk_id: string;
    frame_seq: number;
    durable: false;
  };
  connection_seq: number;
}
export interface ChunkDurable {
  protocol_version: 1;
  type: "audio.chunk_durable";
  sent_at: string;
  data: {
    class_session_id: string;
    capture_epoch_id: string;
    chunk_id: string;
    seq: number;
    sha256: string;
    end_sample: number;
    receipt_id: string;
    durable_at: string;
  };
  event_id: string;
  cursor: string;
}
export interface TranscriptPartial {
  protocol_version: 1;
  type: "transcript.partial";
  sent_at: string;
  data: {
    class_session_id: string;
    stream_id: string;
    segment_id: string;
    sequence: number;
    audio_offset_ms: number;
    status: "partial";
    text: string;
    provider_request_id: string | null;
    revision: number;
  };
  connection_seq: number;
}
export interface TranscriptFinal {
  protocol_version: 1;
  type: "transcript.final";
  sent_at: string;
  data: {
    class_session_id: string;
    stream_id: string;
    segment_id: string;
    segment_revision_id: string;
    transcript_version_id: string;
    sequence: number;
    audio_offset_ms: number;
    end_ms: number;
    status: "final";
    text: string;
    provider_request_id: string | null;
    revision: number;
    timestamp_source: "provider_segment" | "capture_mapping";
  };
  event_id: string;
  cursor: string;
}
export interface TaskChanged {
  protocol_version: 1;
  type: "task.changed";
  sent_at: string;
  data: {
    task_id: string;
    class_session_id: string | null;
    revision: number;
    state: "queued" | "running" | "succeeded" | "failed" | "unknown" | "cancel_requested" | "cancelled";
    error_code: string | null;
    may_have_charged: boolean;
  };
  event_id: string;
  cursor: string;
}
export interface AIDelta {
  protocol_version: 1;
  type: "ai.delta";
  sent_at: string;
  data: {
    task_id: string;
    attempt_id: string;
    delta_seq: number;
    text: string;
    provisional: true;
  };
  connection_seq: number;
}
export interface ObjectChanged {
  protocol_version: 1;
  type: "object.changed";
  sent_at: string;
  data: {
    object_type:
      "semester" | "course" | "class_session" | "slide" | "question" | "review_document" | "mark" | "preferences";
    object_id: string;
    revision: number;
  };
  event_id: string;
  cursor: string;
}
export interface ObjectDeleted {
  protocol_version: 1;
  type: "object.deleted";
  sent_at: string;
  data: {
    object_type: "account" | "semester" | "course" | "class_session" | "audio" | "media_asset";
    object_id: string;
    deletion_id: string;
    state: "access_revoked" | "pending_delete" | "delete_failed" | "deleted";
  };
  event_id: string;
  cursor: string;
}
export interface QuotaChanged {
  protocol_version: 1;
  type: "quota.changed";
  sent_at: string;
  data: {
    reason: "reserved" | "settled" | "unknown" | "adjusted" | "period_changed";
    billing_state: "normal" | "warning60" | "warning80" | "blocked100" | "paused";
  };
  event_id: string;
  cursor: string;
}
export interface NotificationCreated {
  protocol_version: 1;
  type: "notification.created";
  sent_at: string;
  data: {
    notification_id: string;
    severity: "info" | "warning" | "critical";
    message_code: string;
  };
  event_id: string;
  cursor: string;
}
export interface CaptureLost {
  protocol_version: 1;
  type: "capture.lease_lost";
  sent_at: string;
  data: {
    class_session_id: string;
    capture_epoch_id: string;
    fence_token: number;
    reason: "expired" | "takeover" | "revoked";
  };
  event_id: string;
  cursor: string;
}
export interface WsError {
  protocol_version: 1;
  type: "error";
  sent_at: string;
  data: {
    code:
      | "AUTH_REVOKED"
      | "STALE_FENCE"
      | "RATE_LIMITED"
      | "INVALID_FRAME"
      | "STORAGE_UNAVAILABLE"
      | "BUDGET_BLOCKED"
      | "RESULT_UNKNOWN";
    message: string;
    request_id: string;
    retryable: boolean;
  };
  connection_seq: number;
}
