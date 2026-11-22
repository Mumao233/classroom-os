import type { ClassroomOSWebV1EventEnvelope } from '../generated/events';

/**
 * Development-only, deterministic transcript events for one classroom. Each step first shows a
 * partial, then turns it into a final. It can also deliver a late earlier final and repeat the last
 * durable event, so ordering and de-duplication are visible without any real ASR.
 */
const LINES = [
  '我们先回顾矩阵乘法的定义。（合成）',
  '若 Av 等于 λv 且 v 不为零，λ 叫做特征值。（合成）',
  '特征向量的任意非零倍数仍然是特征向量。（合成）',
  '求特征值时，先写出特征多项式 det(A − λI)。（合成）',
  '二阶矩阵的特征多项式是一个二次多项式。（合成）',
  '重根对应的特征向量个数可能少于重数。（合成）',
  '对角化需要有足够多线性无关的特征向量。（合成）',
  '下节课讨论相似矩阵。（合成）',
];
const STEP_MS = 6000;
const SENT_AT = '2026-10-28T08:00:00Z';
type Final = Extract<ClassroomOSWebV1EventEnvelope, { type: 'transcript.final' }>;
type Partial = Extract<ClassroomOSWebV1EventEnvelope, { type: 'transcript.partial' }>;
type FeedState = { stream: string; version: string; step: number; pending: { segment: string; step: number } | null; lastFinal: Final | null; late: number; connectionSeq: number };

export function createTranscriptFeed() {
  const feeds = new Map<string, FeedState>();
  const state = (classId: string) => {
    let feed = feeds.get(classId);
    if (!feed) { feed = { stream: crypto.randomUUID(), version: crypto.randomUUID(), step: 0, pending: null, lastFinal: null, late: 0, connectionSeq: 100 }; feeds.set(classId, feed); }
    return feed;
  };
  const final = (classId: string, feed: FeedState, segment: string, step: number, text: string, start: number): Final => ({
    protocol_version: 1, type: 'transcript.final', sent_at: SENT_AT, event_id: crypto.randomUUID(), cursor: `mock.feed.${classId.slice(0, 8)}.${step}.${feed.late}`,
    data: { class_session_id: classId, stream_id: feed.stream, segment_id: segment, segment_revision_id: crypto.randomUUID(), transcript_version_id: feed.version,
      sequence: step, audio_offset_ms: start, end_ms: start + STEP_MS - 500, status: 'final', text, provider_request_id: null, revision: 1, timestamp_source: 'capture_mapping' },
  });
  return {
    /** Next visible change: a new partial, or the final for the pending partial. */
    advance(classId: string): ClassroomOSWebV1EventEnvelope {
      const feed = state(classId);
      if (feed.pending) {
        const { segment, step } = feed.pending;
        feed.pending = null;
        feed.lastFinal = final(classId, feed, segment, step, LINES[step % LINES.length]!, step * STEP_MS);
        return feed.lastFinal;
      }
      const step = feed.step++;
      const segment = crypto.randomUUID();
      feed.pending = { segment, step };
      const partial: Partial = { protocol_version: 1, type: 'transcript.partial', sent_at: SENT_AT, connection_seq: ++feed.connectionSeq,
        data: { class_session_id: classId, stream_id: feed.stream, segment_id: segment, sequence: step, audio_offset_ms: step * STEP_MS, status: 'partial', text: `${LINES[step % LINES.length]!.slice(0, 10)}…`, provider_request_id: null, revision: 1 } };
      return partial;
    },
    /** A final for an earlier time position that arrives late (reconnect / catch-up). */
    late(classId: string): Final | null {
      const feed = state(classId);
      if (feed.step < 2) return null;
      feed.late++;
      const start = Math.max(0, (feed.step - 2) * STEP_MS + STEP_MS / 2);
      return final(classId, feed, crypto.randomUUID(), feed.step * 100 + feed.late, `（补回的较早片段 ${feed.late}）这段在重连后才送达，按原时间插入。（合成）`, start);
    },
    /** The previous durable event delivered again; the runtime must ignore it. */
    duplicate(classId: string): Final | null { return state(classId).lastFinal; },
    reset() { feeds.clear(); },
  };
}
export type TranscriptFeed = ReturnType<typeof createTranscriptFeed>;
