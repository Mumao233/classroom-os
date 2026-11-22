import type { components } from '../generated/openapi';
import { sha256Hex } from './sha256';

/**
 * PR-10a classroom routes for the development-only Mock backend. Same rules as backend.ts:
 * only fields of the frozen contract 0.2.1, synthetic content, no network, disk or real
 * provider, excluded from production builds. Tests validate every reply against openapi.json.
 */
type S = components['schemas'];
type ErrorCode = S['Error']['error']['code'];
export type ClassroomReply = { status: number; body: unknown };
export type TaskOutcome = 'running' | 'succeeded' | 'failed' | 'unknown';

/** Everything the classroom routes need from the owning account store. */
export type ClassroomHost = {
  sessions: () => S['ClassSession'][];
  saveSession: (next: S['ClassSession']) => void;
  addSession: (next: S['ClassSession']) => void;
  courseExists: (id: string) => boolean;
  firstCourseId: () => string | null;
  quota: () => S['QuotaView'];
  addQuestion: (question: S['Question']) => void;
  updateQuestion: (taskId: string, change: Partial<S['Question']>) => void;
  addDocument: (document: S['ReviewDocument']) => void;
  document: (id: string) => S['ReviewDocument'] | undefined;
  /** Stores a summary result as a candidate revision with its frozen snapshot (PR-10b review store). */
  addCandidate: (input: { revisionId: string; documentId: string; snapshot: S['SourceSnapshot'] | null; created: string; rawOutputRef: string }) => void;
};
export type ClassroomHelpers = {
  now: () => string;
  newId: () => string;
  error: (status: number, code: ErrorCode, message: string, currentRevision?: number | null) => ClassroomReply;
  idempotent: (key: string | null, scope: string, body: unknown, run: () => ClassroomReply) => ClassroomReply;
};

type PlanRecord = { plan: S['GenerationPlan']; request: S['GenerationPlanRequest']; taskId: string | null; audio?: AudioSlice[] };
type UploadRecord = { upload: S['MediaUpload']; create: S['MediaUploadCreate']; received: boolean };
export type ClassroomStore = {
  versions: S['TranscriptVersion'][];
  segments: S['TranscriptSegment'][];
  marks: S['Mark'][];
  slides: S['Slide'][];
  uploads: Map<string, UploadRecord>;
  assets: S['MediaAsset'][];
  manifests: Map<string, S['AudioManifest']>;
  leases: Map<string, S['CaptureLease']>;
  plans: Map<string, PlanRecord>;
  tasks: S['Task'][];
  /** Development bridge: durable sequence numbers known to the in-page capture runtime, per capture epoch. */
  durable: Map<string, number[]>;
  /** Samples captured in retired epochs of each classroom (effective duration excludes pauses). */
  captured: Map<string, number>;
  /** Earlier immutable slide versions, kept so frozen plan sources stay resolvable. */
  slideHistory: S['Slide'][];
  /** Earlier immutable transcript segment revisions (same purpose). */
  segmentHistory: S['TranscriptSegment'][];
  /** Every capture epoch issued for a classroom (epoch ID -> classroom ID). */
  epochs: Map<string, string>;
  /** Expected last chunk of each retired (resumed-over) epoch, per classroom. */
  retired: Map<string, Array<{ epochId: string; expected: number | null }>>;
  /** Sealed manifest of every epoch, retired or stopped. */
  epochManifests: Map<string, S['AudioManifest']>;
  /** Immutable timeline position of each epoch (list order: timeline offset, fence, epoch ID). */
  epochOrder: Map<string, { offset: number; fence: number; expires: string | null; samples: number }>;
  /** Development controls for the manifest list (browser tests): a page-size cap and failing continuations. */
  manifestDev: { pageSize: number | null; failContinuations: boolean };
  /** Epochs whose original audio was deleted or revoked: reads of them fail. */
  revokedAudio: Set<string>;
  /** Issued manifest-list cursors: opaque IDs bound to the classroom and the first page's epoch IDs. */
  manifestCursors: Map<string, { classId: string; ids: string[]; next: number; expired: boolean }>;
  /** Synthetic quota units reserved by each executed task, moved once as the task progresses. */
  reservations: Map<string, { bucket: S['QuotaBucket']['kind']; units: number; phase: 'reserved' | 'submitted' | 'done' }>;
  /** PR-10b development controls for retranscription refusals (original audio state, session and month quota). */
  /** approvals: one-use synthetic approvals of additional runs, keyed `plan:<id>` or `task:<original task id>`. */
  retranscribeDev: { audio: Map<string, 'expired' | 'deleted'>; quota: 'ok' | 'session' | 'month'; approvals: Set<string> };
  /** Retranscription plans that cover only part of the audio (missing chunks): execution needs an explicit confirmation. */
  partialPlans: Set<string>;
};

const DAY = 86_400_000;
const uuid = (prefix: string, n: number) => `${prefix}-0000-4000-8000-${n.toString(16).padStart(12, '0')}`;
const hex = (n: number) => n.toString(16).padStart(64, '0');
/** Digest of an immutable transcript revision's content (its text). */
const segmentDigest = (segment: S['TranscriptSegment']) => sha256Hex(segment.text);
/** Digest of an immutable slide version's content: the image bytes' digest and the applied rotation. */
const slideDigest = (store: ClassroomStore, slide: S['Slide']) => sha256Hex(`${store.assets.find(item => item.id === slide.asset_id)?.sha256 ?? slide.asset_id}:${slide.rotation}`);

/** Moves a task's reserved quota units exactly once along its outcome. */
function moveReservation(store: ClassroomStore, host: ClassroomHost, taskId: string, to: 'submitted' | 'settled' | 'unknown' | 'released') {
  const record = store.reservations.get(taskId);
  if (!record || record.phase === 'done') return;
  const bucket = host.quota().buckets.find(item => item.kind === record.bucket);
  if (!bucket) return;
  if (record.phase === 'reserved') bucket.reserved -= record.units; else bucket.submitted -= record.units;
  if (to === 'submitted') { bucket.submitted += record.units; record.phase = 'submitted'; return; }
  if (to === 'settled') bucket.settled += record.units;
  else if (to === 'unknown') bucket.unknown += record.units;
  else bucket.remaining += record.units;
  record.phase = 'done';
}
const isUuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
const isHex64 = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const nonNegative = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;
const only = (body: Record<string, unknown>, keys: string[]) => Object.keys(body).every(key => keys.includes(key));
const iso = (ms: number) => new Date(ms).toISOString().replace('.000Z', 'Z');

export const MOCK_DESTINATION: S['Destination'] = { provider: 'mock', model: 'synthetic-model', region: 'local', configuration_revision: 'mock-destination-1' };
const CAPTURE_KEYS = ['capture_epoch_id', 'fence_token', 'last_local_seq', 'last_sample', 'client_monotonic_ms'];

/** Synthetic records for seeded, already ended classes (record page and processing page demos). */
export function seedClassroom(sessionIds: { ended: string; uploadFailed: string; unknownAi: string } | null): ClassroomStore {
  const store: ClassroomStore = { versions: [], segments: [], marks: [], slides: [], uploads: new Map(), assets: [], manifests: new Map(), leases: new Map(), plans: new Map(), tasks: [], durable: new Map(), captured: new Map(), slideHistory: [], segmentHistory: [], epochs: new Map(), retired: new Map(), epochManifests: new Map(), epochOrder: new Map(), manifestCursors: new Map(), revokedAudio: new Set(), manifestDev: { pageSize: null, failContinuations: false }, reservations: new Map(), retranscribeDev: { audio: new Map(), quota: 'ok', approvals: new Set() }, partialPlans: new Set() };
  if (!sessionIds) return store;
  const created = '2026-10-27T06:00:00Z';
  const version = (n: number, classId: string): S['TranscriptVersion'] => ({ id: uuid('71000000', n), revision: 1, created_at: created, updated_at: created, class_session_id: classId, kind: 'realtime', state: 'sealed', source_task_id: uuid('72000000', n), adopted: true });
  store.versions.push(version(1, sessionIds.ended), version(2, sessionIds.uploadFailed));
  const lines = ['今天讨论特征值与特征向量的定义（合成）。', '若 Av 等于 λv 且 v 非零，则 λ 称为特征值（合成）。', '求特征值时先写出特征多项式（合成）。'];
  lines.forEach((text, index) => store.segments.push(segment(uuid('73000000', index + 1), sessionIds.ended, uuid('71000000', 1), index * 20_000, index * 20_000 + 18_000, text, created)));
  store.segments.push(segment(uuid('73000000', 10), sessionIds.uploadFailed, uuid('71000000', 2), 0, 15_000, '红黑树插入后需要重新着色（合成）。', '2026-10-25T02:00:00Z'));
  store.manifests.set(sessionIds.ended, { class_session_id: sessionIds.ended, capture_epoch_id: uuid('74000000', 1), sealed: true, expected_last_seq: 284, durable_ranges: [{ first_seq: 0, last_seq: 284 }], missing_ranges: [], unknown_provider_ranges: [], audio_expires_at: '2026-11-03T07:35:00Z' });
  store.manifests.set(sessionIds.uploadFailed, { class_session_id: sessionIds.uploadFailed, capture_epoch_id: uuid('74000000', 2), sealed: true, expected_last_seq: 299, durable_ranges: [{ first_seq: 0, last_seq: 240 }], missing_ranges: [{ first_seq: 241, last_seq: 299 }], unknown_provider_ranges: [], audio_expires_at: null });
  store.marks.push({ id: uuid('75000000', 1), revision: 1, created_at: created, updated_at: created, class_session_id: sessionIds.ended, kind: 'unclear', note: '特征多项式的符号（合成）', understood: false,
    anchor: { kind: 'source', source_ref: { kind: 'transcript_segment', id: uuid('73000000', 3), revision_id: uuid('73100000', 3), content_sha256: hex(3) } } });
  store.slides.push({ id: uuid('76000000', 1), revision: 1, created_at: created, updated_at: created, class_session_id: sessionIds.ended, asset_id: uuid('77000000', 1), version_id: uuid('76100000', 1), title: '板书：特征多项式（合成）', start_ms: 40_000, end_ms: null, rotation: '0', removed: false });
  store.tasks.push(task({ id: uuid('78000000', 1), classId: sessionIds.unknownAi, kind: 'review_summary', state: 'unknown', created: '2026-10-24T03:00:00Z', mayHaveCharged: true, providerOutcome: 'unknown', snapshotId: uuid('79000000', 1) }));
  return store;
}

function segment(id: string, classId: string, versionId: string, start: number, end: number, text: string, created: string): S['TranscriptSegment'] {
  const n = Number.parseInt(id.slice(-4), 16);
  return { id, revision: 1, created_at: created, updated_at: created, class_session_id: classId, transcript_version_id: versionId, stream_id: null,
    segment_revision_id: uuid('73100000', n), kind: 'machine', origin: 'live_asr', status: 'final', text, start_ms: start, end_ms: end,
    timestamp_source: 'capture_mapping', provider_request_id: null, source_chunk_ids: [], adopted_revision_id: uuid('73100000', n) };
}

function task(input: { id: string; classId: string; kind: S['Task']['kind']; state: S['Task']['state']; created: string; mayHaveCharged?: boolean; providerOutcome?: S['Task']['provider_outcome']; snapshotId: string | null; attemptId?: string | null; maximum?: string }): S['Task'] {
  return { id: input.id, revision: 1, created_at: input.created, updated_at: input.created, class_session_id: input.classId, kind: input.kind, state: input.state,
    attempt_id: input.attemptId ?? null, parent_task_id: null, completed_units: 0, total_units: 1, error_code: null, result_id: null, source_snapshot_id: input.snapshotId,
    provider_outcome: input.providerOutcome ?? 'not_submitted', maximum_cost_microyuan: input.maximum ?? '20000', settled_cost_microyuan: null, may_have_charged: input.mayHaveCharged ?? false };
}

/** Flows.ai follows the most relevant task: an unresolved unknown stays visible ahead of later tasks. */
function aiFlow(tasks: S['Task'][]): S['FlowStates']['ai'] {
  const ai = tasks.filter(item => item.kind !== 'asr_live' && item.kind !== 'asr_retranscribe');
  const unknown = ai.find(item => item.state === 'unknown');
  if (unknown) return 'unknown';
  const latest = [...ai].sort((a, b) => b.updated_at.localeCompare(a.updated_at) || b.id.localeCompare(a.id))[0];
  return latest?.state ?? 'idle';
}

const SIGNATURES: Record<string, (hex: string) => boolean> = {
  'image/png': hex => hex.startsWith('89504e470d0a1a0a'),
  'image/jpeg': hex => hex.startsWith('ffd8ff'),
  'image/webp': hex => hex.startsWith('52494646') && hex.slice(16, 24) === '57454250',
};

/**
 * Frozen plan sources are compared with the current versions on every read and before execution.
 * A changed source is still resolvable through the retained immutable revision; a removed or lost
 * one is unavailable.
 */
function sourceStatus(store: ClassroomStore, plan: S['GenerationPlan'], now: string, session?: S['ClassSession']): S['GenerationPlan']['source_status'] {
  if (plan.kind === 'asr_retranscribe') {
    const request = store.plans.get(plan.id)?.request as unknown as { start_ms: number; end_ms: number } | undefined;
    return session && request && selectAudio(store, session, request.start_ms, request.end_ms, now).digest !== plan.audio_manifest_sha256 ? 'changed' : 'unchanged';
  }
  let status: S['GenerationPlan']['source_status'] = 'unchanged';
  for (const ref of plan.source_snapshot?.source_refs ?? []) {
    if (ref.kind === 'slide_version') {
      const slide = store.slides.find(item => item.id === ref.id);
      if (!slide || slide.removed) return 'unavailable';
      if (slide.version_id !== ref.revision_id) {
        if (!store.slideHistory.some(item => item.id === ref.id && item.version_id === ref.revision_id)) return 'unavailable';
        status = 'changed';
      }
    } else if (ref.kind === 'transcript_segment') {
      const segment = store.segments.find(item => item.id === ref.id);
      if (!segment) return 'unavailable';
      if (segment.segment_revision_id !== ref.revision_id) {
        if (!store.segmentHistory.some(item => item.id === ref.id && item.segment_revision_id === ref.revision_id)) return 'unavailable';
        status = 'changed';
      }
    }
  }
  return status;
}

/** Missing chunks of an epoch with a known expected last chunk. */
function missingOf(store: ClassroomStore, epochId: string, expected: number | null) {
  if (expected === null) return [];
  const durable = (store.durable.get(epochId) ?? []).filter(seq => seq <= expected);
  return contiguous(Array.from({ length: expected + 1 }, (_, seq) => seq).filter(seq => !durable.includes(seq)));
}

/** Outstanding chunks across every epoch of the classroom: a later epoch's receipt never covers earlier audio. */
function outstandingChunks(store: ClassroomStore, classId: string): number {
  // The same per-epoch gaps the manifest list reports (resumed-over, taken-over and final epochs alike).
  return epochIdsOf(store, classId).map(epochId => manifestOf(store, classId, epochId)).flatMap(item => item.missing_ranges).reduce((n, range) => n + range.last_seq - range.first_seq + 1, 0);
}

/**
 * Records an epoch's expected range. Only a stop seals it (it carries the manifest digest); an epoch
 * retired by resume keeps its range but stays unsealed, like the capture service.
 */
function sealEpoch(store: ClassroomStore, classId: string, epochId: string, expected: number | null, expires: string | null, sealed = true): S['AudioManifest'] {
  const durable = expected === null ? [] : (store.durable.get(epochId) ?? []).filter(seq => seq <= expected);
  const manifest: S['AudioManifest'] = { class_session_id: classId, capture_epoch_id: epochId, sealed, expected_last_seq: expected, durable_ranges: contiguous(durable), missing_ranges: missingOf(store, epochId, expected), unknown_provider_ranges: [], audio_expires_at: expires };
  store.epochManifests.set(epochId, manifest);
  return manifest;
}

/**
 * Every capture epoch of a classroom in list order: sealed epochs as sealed, the open (recording or
 * paused) epoch with its durable chunks so far. Seeded classrooms contribute their stored manifest.
 */
function epochIdsOf(store: ClassroomStore, classId: string): string[] {
  const ids = new Set([...store.epochs].filter(([, owner]) => owner === classId).map(([epochId]) => epochId));
  const seeded = store.manifests.get(classId);
  if (seeded) ids.add(seeded.capture_epoch_id);
  const order = (epochId: string) => store.epochOrder.get(epochId) ?? { offset: 0, fence: 1, expires: null, samples: 0 };
  return [...ids].sort((a, b) => order(a).offset - order(b).offset || order(a).fence - order(b).fence || a.localeCompare(b));
}

/**
 * One epoch's manifest: a recorded (stopped or resumed-over) epoch as recorded; an open epoch with
 * its durable chunks and the holes up to the highest chunk seen, never a guessed tail beyond it.
 * Every epoch carries its retention deadline.
 */
function manifestOf(store: ClassroomStore, classId: string, epochId: string): S['AudioManifest'] {
  const seeded = store.manifests.get(classId);
  const recorded = store.epochManifests.get(epochId) ?? (seeded?.capture_epoch_id === epochId ? seeded : undefined);
  const expires = store.epochOrder.get(epochId)?.expires ?? null;
  // The epoch's own deadline wins (a stop moves it to the stop's retention deadline).
  if (recorded) return expires !== null && recorded.audio_expires_at !== expires ? { ...recorded, audio_expires_at: expires } : recorded;
  const durable = contiguous(store.durable.get(epochId) ?? []);
  const holes = durable.map((range, index) => ({ first_seq: index === 0 ? 0 : durable[index - 1]!.last_seq + 1, last_seq: range.first_seq - 1 })).filter(range => range.last_seq >= range.first_seq);
  return { class_session_id: classId, capture_epoch_id: epochId, sealed: false, expected_last_seq: null, durable_ranges: durable, missing_ranges: holes, unknown_provider_ranges: [], audio_expires_at: expires };
}

/** Development fixture: historical classrooms with retained audio get one recorded epoch each. */
export function seedHistoricalManifests(store: ClassroomStore, sessions: readonly S['ClassSession'][]) {
  for (const session of sessions) {
    // Membership does not depend on whether the audio is still readable: an epoch past its deadline
    // stays listed and its read fails, unlike a classroom that never captured.
    if (session.deleted_at !== null || session.capture_ended_at === null || session.effective_capture_ms <= 0 || store.manifests.has(session.id)) continue;
    const last = Math.ceil(session.effective_capture_ms / 1000) - 1;
    const durable = session.flows.upload === 'durable';
    const deadline = session.audio_expires_at ?? iso(Date.parse(session.capture_ended_at) + 7 * DAY);
    store.manifests.set(session.id, { class_session_id: session.id, capture_epoch_id: `7e${session.id.slice(2)}`, sealed: true, expected_last_seq: last,
      durable_ranges: durable ? [{ first_seq: 0, last_seq: last }] : [], missing_ranges: durable ? [] : [{ first_seq: 0, last_seq: last }], unknown_provider_ranges: [], audio_expires_at: deadline });
  }
  return store;
}

function contiguous(seqs: number[]): Array<{ first_seq: number; last_seq: number }> {
  const sorted = [...new Set(seqs)].filter(nonNegative).sort((a, b) => a - b);
  const ranges: Array<{ first_seq: number; last_seq: number }> = [];
  for (const seq of sorted) {
    const last = ranges.at(-1);
    if (last && seq === last.last_seq + 1) last.last_seq = seq; else ranges.push({ first_seq: seq, last_seq: seq });
  }
  return ranges;
}

export function handleClassroom(store: ClassroomStore, host: ClassroomHost, h: ClassroomHelpers, method: string, route: string, id: string, query: URLSearchParams, body: Record<string, unknown>, key: string | null): ClassroomReply | null {
  const ok = (value: unknown, status = 200): ClassroomReply => ({ status, body: value });
  // Cursor pages at the contract's page size (1-100); the cursor is an opaque offset token.
  const page = <T>(items: T[]): ClassroomReply => {
    const limit = query.has('limit') ? Number(query.get('limit')) : 50;
    const cursor = query.get('cursor');
    if (!Number.isInteger(limit) || limit < 1 || limit > 100 || (cursor !== null && !/^o[0-9]{1,9}$/.test(cursor))) return h.error(400, 'VALIDATION_FAILED', '分页参数无效');
    const start = cursor ? Number(cursor.slice(1)) : 0;
    const end = start + limit;
    return ok({ items: items.slice(start, end), next_cursor: end < items.length ? `o${end}` : null });
  };
  const findSession = (classId: string) => host.sessions().find(item => item.id === classId && item.deleted_at === null && host.courseExists(item.course_id));
  const touchSession = (session: S['ClassSession'], change: Partial<S['ClassSession']>) => { const next = { ...session, ...change, revision: session.revision + 1, updated_at: h.now() }; host.saveSession(next); return next; };
  const notFound = () => h.error(404, 'NOT_FOUND', '课堂不存在');
  // A stop receipt names an epoch's manifest: its current readability is rechecked, also on replay.
  const stopChecked = (reply: ClassroomReply) => reply.status === 200 && unreadable(manifestOf(store, id, (reply.body as S['AudioManifest']).capture_epoch_id)) ? h.error(410, 'GONE', '这次采集的原音已过保留期限或已删除') : reply;
  const unreadable = (manifest: S['AudioManifest']) => store.revokedAudio.has(manifest.capture_epoch_id) || (manifest.audio_expires_at !== null && Date.parse(manifest.audio_expires_at) <= Date.parse(h.now()));
  // A new epoch starts at the end of the classroom's capture timeline (also after a takeover).
  const timelineEnd = (classId: string, session: S['ClassSession']) => Math.max(session.effective_capture_ms, Math.floor((store.captured.get(classId) ?? 0) / 16),
    ...epochIdsOf(store, classId).map(epochId => { const order = store.epochOrder.get(epochId); return order ? order.offset + Math.floor(order.samples / 16) : 0; }));
  /** Records the last sample observed for an epoch (pause, resume, stop): the next epoch starts after it. */
  const observe = (epochId: string, sample: number) => { const order = store.epochOrder.get(epochId); if (order && sample > order.samples) store.epochOrder.set(epochId, { ...order, samples: sample }); };
  const epochDeadline = () => iso(Date.parse(h.now()) + 7 * DAY);
  void method;
  // Access is rechecked before any route or idempotent replay runs: a revoked classroom never
  // returns an earlier receipt, also for resources reached through a plan, upload, slide or task ID.
  const owner = route.includes(' /class-sessions/:id') ? id
    : route === 'GET /transcript-versions/:id/segments' ? store.versions.find(item => item.id === id)?.class_session_id
      : route === 'PATCH /slides/:id' ? store.slides.find(item => item.id === id)?.class_session_id
        : route.includes(' /generation-plans/:id') ? store.plans.get(id)?.plan.class_session_id
          : route.includes(' /media/uploads/:id') ? store.uploads.get(id)?.create.class_session_id
            : route.includes(' /tasks/:id') ? store.tasks.find(item => item.id === id)?.class_session_id ?? undefined
              : route === 'POST /media/uploads' && isUuid(body.class_session_id) ? body.class_session_id : undefined;
  if (typeof owner === 'string' && !findSession(owner)) return notFound();
  // A creation receipt names its classroom only after the request ran: recheck access before replaying it.
  const replayChecked = (reply: ClassroomReply) => reply.status === 201 && !findSession((reply.body as S['ClassSession']).id) ? notFound() : reply;

  switch (route) {
    case 'POST /class-sessions': return replayChecked(h.idempotent(key, 'class-session', body, () => {
      if (!only(body, ['course_id', 'title', 'language']) || !isUuid(body.course_id) || typeof body.title !== 'string' || body.title.length > 200
        || ('language' in body && !['auto', 'zh', 'en', 'mixed'].includes(String(body.language)))) return h.error(400, 'VALIDATION_FAILED', '课堂信息无效');
      if (!host.courseExists(body.course_id)) return h.error(404, 'NOT_FOUND', '课程不存在');
      const created = h.now();
      const session: S['ClassSession'] = { id: h.newId(), revision: 1, created_at: created, updated_at: created, course_id: body.course_id, title: body.title.trim(),
        language: (body.language as S['ClassSession']['language']) ?? 'auto', started_at: null, capture_ended_at: null, audio_expires_at: null, adopted_transcript_version_id: null,
        flows: { capture: 'idle', upload: 'pending', asr: 'idle', ai: 'idle', sync: 'synced' }, effective_capture_ms: 0, known_gap_count: 0, unknown_gap_count: 0, deleted_at: null };
      host.addSession(session);
      return ok(session, 201);
    }));

    case 'POST /class-sessions/:id/capture/acquire': return h.idempotent(key, `acquire:${id}`, body, () => {
      const session = findSession(id); if (!session) return notFound();
      if (!only(body, ['device_id', 'microphone_consent_version', 'recovery_consent_version', 'asr_consent_version', 'confirm_takeover', 'expected_lease_id'])
        || !isUuid(body.device_id) || typeof body.confirm_takeover !== 'boolean'
        || !['microphone_consent_version', 'recovery_consent_version', 'asr_consent_version'].every(name => typeof body[name] === 'string' && (body[name] as string).length > 0)) return h.error(400, 'VALIDATION_FAILED', '采集请求无效');
      if (session.capture_ended_at !== null) return h.error(409, 'LEASE_CONFLICT', '该课堂的采集已结束');
      for (const [classId, item] of store.leases) if (Date.parse(item.expires_at) <= Date.parse(h.now())) store.leases.delete(classId);
      const holder = [...store.leases.values()][0];
      if (holder) {
        if (body.confirm_takeover !== true || body.expected_lease_id !== holder.id) return h.error(409, 'LEASE_CONFLICT', '已有课堂正在采集；接管需要明确确认');
        store.leases.delete(holder.class_session_id);
      }
      const previous = holder?.class_session_id === id ? holder : undefined;
      const lease: S['CaptureLease'] = { id: h.newId(), class_session_id: id, capture_epoch_id: h.newId(), fence_token: (previous?.fence_token ?? 0) + 1,
        expires_at: iso(Date.parse(h.now()) + 30_000), renew_after_seconds: 10, timeline_offset_ms: timelineEnd(id, session), sample_rate: 16000, channel_count: 1, encoding: 'pcm_s16le' };
      store.leases.set(id, lease);
      store.epochs.set(lease.capture_epoch_id, id);
      store.epochOrder.set(lease.capture_epoch_id, { offset: lease.timeline_offset_ms, fence: lease.fence_token, expires: epochDeadline(), samples: 0 });
      touchSession(session, { started_at: session.started_at ?? h.now(), flows: { ...session.flows, capture: 'recording' } });
      return ok(lease, 201);
    });

    case 'POST /class-sessions/:id/capture/pause':
    case 'POST /class-sessions/:id/capture/resume': return h.idempotent(key, `${route}:${id}`, body, () => {
      const session = findSession(id); if (!session) return notFound();
      const lease = store.leases.get(id);
      if (!only(body, CAPTURE_KEYS) || !isUuid(body.capture_epoch_id) || !CAPTURE_KEYS.slice(1).every(name => nonNegative(body[name]))) return h.error(400, 'VALIDATION_FAILED', '采集命令无效');
      if (!lease || lease.capture_epoch_id !== body.capture_epoch_id || lease.fence_token !== body.fence_token) return h.error(409, 'STALE_FENCE', '采集租约已失效');
      const paused = route.endsWith('/pause');
      if (paused !== (session.flows.capture === 'recording')) return h.error(409, 'LEASE_CONFLICT', paused ? '采集未在进行' : '采集未暂停');
      touchSession(session, { flows: { ...session.flows, capture: paused ? 'paused' : 'recording' } });
      observe(lease.capture_epoch_id, body.last_sample as number);
      if (paused) return ok(lease);
      // Same as the capture service: resuming retires the paused epoch and issues a new epoch and fence.
      store.captured.set(id, (store.captured.get(id) ?? 0) + (body.last_sample as number));
      const retiredExpected = (body.last_sample as number) > 0 ? body.last_local_seq as number : null;
      store.retired.set(id, [...(store.retired.get(id) ?? []), { epochId: lease.capture_epoch_id, expected: retiredExpected }]);
      sealEpoch(store, id, lease.capture_epoch_id, retiredExpected, store.epochOrder.get(lease.capture_epoch_id)?.expires ?? null, false);
      const next: S['CaptureLease'] = { ...lease, id: h.newId(), capture_epoch_id: h.newId(), fence_token: lease.fence_token + 1, timeline_offset_ms: Math.floor((store.captured.get(id) ?? 0) / 16) };
      store.leases.set(id, next);
      store.epochs.set(next.capture_epoch_id, id);
      store.epochOrder.set(next.capture_epoch_id, { offset: next.timeline_offset_ms, fence: next.fence_token, expires: epochDeadline(), samples: 0 });
      return ok(next);
    });

    case 'POST /class-sessions/:id/capture/stop': return stopChecked(h.idempotent(key, `stop:${id}`, body, () => {
      const session = findSession(id); if (!session) return notFound();
      const lease = store.leases.get(id);
      if (!only(body, [...CAPTURE_KEYS, 'manifest_sha256', 'known_gaps']) || !isUuid(body.capture_epoch_id) || !CAPTURE_KEYS.slice(1).every(name => nonNegative(body[name]))
        || !isHex64(body.manifest_sha256) || !Array.isArray(body.known_gaps)) return h.error(400, 'VALIDATION_FAILED', '停止请求无效');
      if (!lease || lease.capture_epoch_id !== body.capture_epoch_id || lease.fence_token !== body.fence_token) return h.error(409, 'STALE_FENCE', '采集租约已失效');
      // Like the capture service, an epoch without samples has no expected last chunk.
      const expected = (body.last_sample as number) > 0 ? body.last_local_seq as number : null;
      const ended = h.now();
      // The epoch keeps the hard deadline it was issued with; a stop never extends retention.
      const expires = store.epochOrder.get(lease.capture_epoch_id)?.expires ?? iso(Date.parse(ended) + 7 * DAY);
      observe(lease.capture_epoch_id, body.last_sample as number);
      const manifest = sealEpoch(store, id, lease.capture_epoch_id, expected, expires);
      store.manifests.set(id, manifest);
      store.leases.delete(id);
      // Effective duration comes from captured samples across epochs (16 kHz), never from wall time.
      const samples = (store.captured.get(id) ?? 0) + (body.last_sample as number);
      touchSession(session, { capture_ended_at: ended, audio_expires_at: expires, effective_capture_ms: Math.floor(samples / 16),
        known_gap_count: (body.known_gaps as unknown[]).length, flows: { ...session.flows, capture: 'ended', upload: outstandingChunks(store, id) ? 'pending' : 'durable', asr: session.flows.asr === 'streaming' ? 'draining' : session.flows.asr } });
      return ok(manifest);
    }));

    case 'GET /class-sessions/:id/audio-manifest': {
      if (!findSession(id)) return notFound();
      // 0.3.0: exactly one valid capture_epoch_id; a caller without one reads the list instead.
      const epochIds = query.getAll('capture_epoch_id');
      if (epochIds.length !== 1 || [...query.keys()].some(name => name !== 'capture_epoch_id') || !isUuid(epochIds[0])) return h.error(422, 'VALIDATION_FAILED', '需要且只能提供一个有效的 capture_epoch_id');
      if (!epochIdsOf(store, id).includes(epochIds[0]!)) return h.error(404, 'NOT_FOUND', '该课堂没有这次采集记录');
      const manifest = manifestOf(store, id, epochIds[0]!);
      return unreadable(manifest) ? h.error(410, 'GONE', '这次采集的原音已过保留期限或已删除') : ok(manifest);
    }
    case 'GET /class-sessions/:id/audio-manifests': {
      if (!findSession(id)) return notFound();
      // Only limit and cursor, each at most once.
      if ([...query.keys()].some(name => name !== 'limit' && name !== 'cursor') || query.getAll('limit').length > 1 || query.getAll('cursor').length > 1) return h.error(422, 'VALIDATION_FAILED', '分页参数无效');
      const requested = query.has('limit') ? Number(query.get('limit')) : 20;
      const cursor = query.get('cursor');
      if (!Number.isInteger(requested) || requested < 1 || requested > 100 || (cursor !== null && (cursor.length < 1 || cursor.length > 512))) return h.error(422, 'VALIDATION_FAILED', '分页参数无效');
      // Development controls (browser tests): a smaller page size and failing continuations.
      const limit = Math.min(requested, store.manifestDev.pageSize ?? requested);
      if (cursor !== null && store.manifestDev.failContinuations) return h.error(503, 'STORAGE_UNAVAILABLE', '模拟的续页读取失败');
      // Membership is fixed at the first page: the cursor names those epoch IDs; contents are read per page.
      const issued = cursor === null ? null : store.manifestCursors.get(cursor);
      if (cursor !== null && (!issued || issued.classId !== id)) return h.error(422, 'VALIDATION_FAILED', '分页游标无效');
      if (issued?.expired) return h.error(409, 'CURSOR_EXPIRED', '分页游标已过期，请从第一页重新读取');
      const known = epochIdsOf(store, id);
      const ids = issued ? issued.ids : known;
      const start = issued ? issued.next : 0;
      const members = ids.slice(start, start + limit).map(epochId => known.includes(epochId) ? manifestOf(store, id, epochId) : undefined);
      // A member that can no longer be disclosed fails the whole page; nothing is skipped silently.
      if (members.some(item => item === undefined || unreadable(item))) return h.error(410, 'GONE', '部分采集的原音已不可读取');
      const end = start + members.length;
      let next: string | null = null;
      if (end < ids.length) { next = h.newId(); store.manifestCursors.set(next, { classId: id, ids, next: end, expired: false }); }
      return ok({ items: members as S['AudioManifest'][], next_cursor: next } satisfies S['AudioManifestPage']);
    }

    case 'GET /class-sessions/:id/transcript-versions': return findSession(id) ? page(store.versions.filter(item => item.class_session_id === id)) : notFound();
    case 'GET /transcript-versions/:id/segments': {
      const version = store.versions.find(item => item.id === id);
      if (!version || !findSession(version.class_session_id)) return h.error(404, 'NOT_FOUND', '转写版本不存在');
      return page(store.segments.filter(item => item.transcript_version_id === id).sort((a, b) => (a.start_ms ?? 0) - (b.start_ms ?? 0)));
    }

    case 'GET /class-sessions/:id/marks': return findSession(id) ? page(store.marks.filter(item => item.class_session_id === id)) : notFound();
    case 'POST /class-sessions/:id/marks': return h.idempotent(key, `mark:${id}`, body, () => {
      if (!findSession(id)) return notFound();
      const anchor = body.anchor as Record<string, unknown> | undefined;
      // Every anchor variant of the contract, resolved to an immutable record of this classroom.
      const ref = anchor?.source_ref as Record<string, unknown> | undefined;
      const sourceOk = (value: Record<string, unknown> | undefined) => !!value && typeof value === 'object' && only(value, ['kind', 'id', 'revision_id', 'content_sha256'])
        && isUuid(value.id) && isUuid(value.revision_id) && isHex64(value.content_sha256) && (
          (value.kind === 'transcript_segment' && [...store.segments, ...store.segmentHistory].some(item => item.id === value.id && item.segment_revision_id === value.revision_id && item.class_session_id === id && segmentDigest(item) === value.content_sha256))
          || (value.kind === 'slide_version' && [...store.slides, ...store.slideHistory].some(item => item.id === value.id && item.version_id === value.revision_id && item.class_session_id === id && slideDigest(store, item) === value.content_sha256))
          || (value.kind === 'mark' && store.marks.some(item => item.id === value.id && item.class_session_id === id)));
      const reviewDocument = anchor?.kind === 'review_revision' && isUuid(anchor.document_id) ? host.document(anchor.document_id) : undefined;
      const validAnchor = !!anchor && typeof anchor === 'object' && (
        (anchor.kind === 'timepoint' && only(anchor, ['kind', 'capture_epoch_id', 'sample_offset']) && isUuid(anchor.capture_epoch_id) && nonNegative(anchor.sample_offset) && store.epochs.get(anchor.capture_epoch_id) === id)
        || (anchor.kind === 'source' && only(anchor, ['kind', 'source_ref']) && sourceOk(ref))
        || (anchor.kind === 'review_revision' && only(anchor, ['kind', 'document_id', 'revision_id', 'content_sha256']) && isUuid(anchor.revision_id) && isHex64(anchor.content_sha256)
          && reviewDocument?.class_session_id === id && [reviewDocument.adopted_revision_id, reviewDocument.latest_personal_revision_id, ...reviewDocument.candidate_revision_ids].includes(anchor.revision_id)));
      if (!only(body, ['kind', 'anchor', 'note']) || !['important', 'unclear'].includes(String(body.kind)) || !validAnchor || typeof body.note !== 'string' || body.note.length > 2000) return h.error(400, 'VALIDATION_FAILED', '标记无效');
      const created = h.now();
      const mark: S['Mark'] = { id: h.newId(), revision: 1, created_at: created, updated_at: created, class_session_id: id, kind: body.kind as S['Mark']['kind'], anchor: anchor as S['Mark']['anchor'], note: body.note, understood: false };
      store.marks.push(mark);
      return ok(mark, 201);
    });

    case 'GET /class-sessions/:id/slides': return findSession(id) ? page(store.slides.filter(item => item.class_session_id === id)) : notFound();
    case 'POST /media/uploads': return h.idempotent(key, 'media-upload', body, () => {
      if (!only(body, ['purpose', 'class_session_id', 'mime_type', 'byte_length', 'sha256', 'filename']) || body.purpose !== 'slide' || !isUuid(body.class_session_id)
        || !['image/png', 'image/jpeg', 'image/webp'].includes(String(body.mime_type)) || !Number.isSafeInteger(body.byte_length) || (body.byte_length as number) < 1
        || (body.byte_length as number) > 16_000_000 || !isHex64(body.sha256) || typeof body.filename !== 'string' || body.filename.length > 200) return h.error(400, 'VALIDATION_FAILED', '图片不符合要求（PNG/JPEG/WebP，不超过 16,000,000 字节）');
      if (!findSession(body.class_session_id)) return notFound();
      const upload: S['MediaUpload'] = { id: h.newId(), asset_id: h.newId(), upload_path: '', state: 'staging', expires_at: iso(Date.parse(h.now()) + 3_600_000) };
      upload.upload_path = `/api/web/v1/media/uploads/${upload.id}/content`;
      store.uploads.set(upload.id, { upload, create: body as unknown as S['MediaUploadCreate'], received: false });
      return ok(upload, 201);
    });
    case 'PUT /media/uploads/:id/content': {
      if (!key) return h.error(422, 'VALIDATION_FAILED', '上传内容缺少 Idempotency-Key');
      const record = store.uploads.get(id);
      if (!record) return h.error(404, 'NOT_FOUND', '上传不存在');
      // The worker hands over the size, digest and leading bytes of the raw body it received.
      const signature = SIGNATURES[record.create.mime_type];
      if (body.byte_length !== record.create.byte_length || body.sha256 !== record.create.sha256 || typeof body.signature !== 'string' || !signature?.(body.signature)) {
        return h.error(422, 'VALIDATION_FAILED', '上传内容与声明的图片不一致');
      }
      record.received = true;
      record.upload = { ...record.upload, state: 'uploaded' };
      return ok(record.upload);
    }
    case 'POST /media/uploads/:id/complete': return h.idempotent(key, `media-complete:${id}`, body, () => {
      const record = store.uploads.get(id);
      if (!record) return h.error(404, 'NOT_FOUND', '上传不存在');
      if (!only(body, ['sha256', 'byte_length']) || body.sha256 !== record.create.sha256 || body.byte_length !== record.create.byte_length) return h.error(400, 'VALIDATION_FAILED', '校验值不一致');
      if (!record.received) return h.error(409, 'STORAGE_UNAVAILABLE', '图片内容尚未上传');
      const created = h.now();
      const asset: S['MediaAsset'] = { id: record.upload.asset_id, revision: 1, created_at: created, updated_at: created, class_session_id: record.create.class_session_id, kind: 'slide',
        sha256: record.create.sha256, byte_length: record.create.byte_length, mime_type: record.create.mime_type, state: 'available', expires_at: null };
      store.assets.push(asset);
      record.upload = { ...record.upload, state: 'verified' };
      return ok(asset);
    });
    case 'POST /class-sessions/:id/slides': return h.idempotent(key, `slide:${id}`, body, () => {
      if (!findSession(id)) return notFound();
      if (!only(body, ['asset_id', 'title', 'start_ms', 'end_ms']) || !isUuid(body.asset_id) || typeof body.title !== 'string' || body.title.length > 160 || !nonNegative(body.start_ms)
        || !(body.end_ms === null || (nonNegative(body.end_ms) && (body.end_ms as number) >= (body.start_ms as number)))) return h.error(400, 'VALIDATION_FAILED', '课件信息无效');
      const asset = store.assets.find(item => item.id === body.asset_id && item.class_session_id === id && item.state === 'available');
      if (!asset) return h.error(409, 'SOURCE_UNAVAILABLE', '图片尚未校验完成');
      const created = h.now();
      const slide: S['Slide'] = { id: h.newId(), revision: 1, created_at: created, updated_at: created, class_session_id: id, asset_id: asset.id, version_id: h.newId(), title: body.title, start_ms: body.start_ms as number, end_ms: body.end_ms as number | null, rotation: '0', removed: false };
      store.slides.push(slide);
      return ok(slide, 201);
    });
    case 'PATCH /slides/:id': return h.idempotent(key, `slide-patch:${id}`, body, () => {
      const slide = store.slides.find(item => item.id === id);
      if (!slide || !findSession(slide.class_session_id)) return h.error(404, 'NOT_FOUND', '课件不存在');
      if (!only(body, ['base_revision', 'title', 'start_ms', 'end_ms', 'rotate', 'removed']) || ('rotate' in body && !['left', 'right'].includes(String(body.rotate))) || ('removed' in body && typeof body.removed !== 'boolean')
        || ('title' in body && (typeof body.title !== 'string' || body.title.length > 160))) return h.error(400, 'VALIDATION_FAILED', '课件修改无效');
      if (!Number.isInteger(body.base_revision)) return h.error(400, 'VALIDATION_FAILED', '缺少基础版本');
      if (body.base_revision !== slide.revision) return h.error(409, 'REVISION_CONFLICT', '课件已在其他地方更新', slide.revision);
      const turns = ['0', '90', '180', '270'] as const;
      const rotation = body.rotate ? turns[(turns.indexOf(slide.rotation) + (body.rotate === 'right' ? 1 : 3)) % 4] : slide.rotation;
      // Every change is a new immutable version; earlier versions stay valid history for sources.
      const next: S['Slide'] = { ...slide, revision: slide.revision + 1, updated_at: h.now(), version_id: h.newId(), rotation,
        ...(typeof body.title === 'string' ? { title: body.title } : {}), ...(nonNegative(body.start_ms) ? { start_ms: body.start_ms } : {}), ...('end_ms' in body ? { end_ms: body.end_ms as number | null } : {}), ...('removed' in body ? { removed: body.removed as boolean } : {}) };
      store.slideHistory.push(slide);
      store.slides = store.slides.map(item => item.id === id ? next : item);
      return ok(next);
    });

    case 'POST /class-sessions/:id/generation-plans': return h.idempotent(key, `plan:${id}`, body, () => planFor(store, host, h, id, body));
    case 'GET /generation-plans/:id': { const record = store.plans.get(id); return record && findSession(record.plan.class_session_id) ? ok({ ...record.plan, source_status: sourceStatus(store, record.plan, h.now(), findSession(record.plan.class_session_id)) }) : h.error(404, 'NOT_FOUND', '计划不存在'); }
    case 'POST /generation-plans/:id/execute': return h.idempotent(key, `execute:${id}`, body, () => {
      const record = store.plans.get(id);
      if (!record || !findSession(record.plan.class_session_id)) return h.error(404, 'NOT_FOUND', '计划不存在');
      const plan = record.plan;
      if (!only(body, ['plan_sha256', 'confirmed_destination_revision', 'confirm_send', 'confirm_images', 'confirm_original_snapshot', 'confirm_partial_scope'])
        || body.confirm_send !== true || typeof body.confirm_images !== 'boolean' || typeof body.confirm_original_snapshot !== 'boolean' || typeof body.confirm_partial_scope !== 'boolean') return h.error(400, 'VALIDATION_FAILED', '确认内容无效');
      // A consumed plan returns its original task even under a new operation key (PR-07a semantics).
      if (record.taskId) { const existing = store.tasks.find(item => item.id === record.taskId); if (existing) return ok(existing, 202); }
      if (Date.parse(plan.expires_at) <= Date.parse(h.now())) return h.error(409, 'PLAN_EXPIRED', '计划已过期，请重新准备');
      if (body.plan_sha256 !== plan.plan_sha256 || body.confirmed_destination_revision !== plan.destination.configuration_revision) return h.error(409, 'PLAN_EXPIRED', '计划内容或目的地已变化，请重新准备');
      if (plan.kind === 'asr_retranscribe') {
        const session = findSession(plan.class_session_id)!;
        // Every frozen chunk is reauthorised first: confirming the original selection never overrides a revocation.
        const refusal = audioRefusal(store, h, session, record.audio) ?? quotaRefusal(store, host, h, session.id, `plan:${plan.id}`, plan.quota_units);
        if (refusal) return refusal;
        if (store.partialPlans.has(plan.id) && body.confirm_partial_scope !== true) return h.error(400, 'VALIDATION_FAILED', '原音不完整，需要确认按已有部分重新转写');
        const request = record.request as unknown as { start_ms: number; end_ms: number };
        if (selectAudio(store, session, request.start_ms, request.end_ms, h.now()).digest !== plan.audio_manifest_sha256 && body.confirm_original_snapshot !== true) return h.error(409, 'SOURCE_CHANGED', '原音在准备后有变化，可以重新准备，或明确选择按准备时冻结的原音提交');
        const next = task({ id: h.newId(), classId: plan.class_session_id, kind: 'asr_retranscribe', state: 'queued', created: h.now(), snapshotId: null, attemptId: h.newId(), maximum: plan.maximum_cost_microyuan });
        store.tasks.push(next);
        record.taskId = next.id;
        admit(store, host, `plan:${plan.id}`, next.id, plan.quota_units);
        return ok(next, 202);
      }
      if (plan.contains_images && body.confirm_images !== true) return h.error(400, 'VALIDATION_FAILED', '计划包含图片，需要确认发送图片');
      if (plan.source_snapshot && !plan.source_snapshot.scope_complete && body.confirm_partial_scope !== true) return h.error(400, 'VALIDATION_FAILED', '材料不完整，需要确认按部分范围生成');
      const status = sourceStatus(store, plan, h.now());
      if (status === 'unavailable') return h.error(409, 'SOURCE_UNAVAILABLE', '部分来源已不可用，请重新准备');
      if (status === 'changed' && body.confirm_original_snapshot !== true) return h.error(409, 'SOURCE_CHANGED', '来源在准备后已更新，需要明确选择按原版本发送或重新准备');
      // Synthetic units are rechecked and reserved on first execution (a replay returned the task above).
      const bucket = host.quota().buckets.find(item => item.kind === (plan.kind === 'review_summary' ? 'summary_month_count' : 'interactive_month_count'));
      if (bucket) {
        if (bucket.remaining < plan.quota_units) return h.error(409, 'QUOTA_EXCEEDED', '本月额度已用完');
        bucket.remaining -= plan.quota_units;
        bucket.reserved += plan.quota_units;
      }
      const created = h.now();
      const next = task({ id: h.newId(), classId: plan.class_session_id, kind: plan.kind, state: 'queued', created, snapshotId: plan.source_snapshot?.id ?? null, attemptId: h.newId(), maximum: plan.maximum_cost_microyuan });
      store.tasks.push(next);
      record.taskId = next.id;
      if (bucket) store.reservations.set(next.id, { bucket: bucket.kind, units: plan.quota_units, phase: 'reserved' });
      if (plan.kind === 'ai_question') host.addQuestion({ id: h.newId(), revision: 1, created_at: created, updated_at: created, class_session_id: plan.class_session_id, task_id: next.id,
        source_snapshot_id: plan.source_snapshot!.id, question: record.request.question ?? '', answer_markdown: null, state: 'queued', understood: false });
      syncAi(store, host, h, plan.class_session_id);
      return ok(next, 202);
    });

    case 'POST /tasks/:id/retry': return h.idempotent(key, `retry:${id}`, body, () => {
      const found = store.tasks.find(item => item.id === id && (!item.class_session_id || findSession(item.class_session_id)));
      if (!found) return h.error(404, 'NOT_FOUND', '任务不存在');
      if (!only(body, ['reason', 'confirm_duplicate_billing', 'original_attempt_id']) || typeof body.reason !== 'string' || body.reason.trim().length === 0 || body.reason.length > 500
        || typeof body.confirm_duplicate_billing !== 'boolean' || !isUuid(body.original_attempt_id)) return h.error(400, 'VALIDATION_FAILED', '重试请求无效');
      if (found.kind !== 'asr_retranscribe' || found.state !== 'unknown' || found.provider_outcome !== 'unknown') return h.error(409, 'VALIDATION_FAILED', '只有结果未知的重新转写可以重试');
      if (store.tasks.some(item => item.parent_task_id === found.id)) return h.error(409, 'VALIDATION_FAILED', '这次尝试已经有重试任务');
      if (found.attempt_id !== body.original_attempt_id) return h.error(409, 'VALIDATION_FAILED', '重试必须指明原尝试');
      if (found.may_have_charged && body.confirm_duplicate_billing !== true) return h.error(409, 'VALIDATION_FAILED', '原尝试可能已收费，重试需要确认可能重复计费');
      const session = found.class_session_id ? findSession(found.class_session_id) : undefined;
      if (!session) return notFound();
      const origin = originPlan(store, found);
      const units = origin?.plan.quota_units ?? 1;
      const refusal = audioRefusal(store, h, session, origin?.audio) ?? quotaRefusal(store, host, h, session.id, `task:${found.id}`, units);
      if (refusal) return refusal;
      // The original attempt is immutable; the retry is a new linked task with its own attempt.
      const next: S['Task'] = { ...task({ id: h.newId(), classId: session.id, kind: 'asr_retranscribe', state: 'queued', created: h.now(), snapshotId: null, attemptId: h.newId(), maximum: found.maximum_cost_microyuan }), parent_task_id: found.id };
      store.tasks.push(next);
      admit(store, host, `task:${found.id}`, next.id, units);
      return ok(next, 202);
    });
    case 'POST /class-sessions/:id/adopt-transcript': return h.idempotent(key, `adopt-transcript:${id}`, body, () => {
      const session = findSession(id); if (!session) return notFound();
      if (!only(body, ['base_revision', 'transcript_version_id']) || !Number.isSafeInteger(body.base_revision) || (body.base_revision as number) < 1 || !isUuid(body.transcript_version_id)) return h.error(400, 'VALIDATION_FAILED', '采用请求无效');
      const version = store.versions.find(item => item.id === body.transcript_version_id && item.class_session_id === id);
      if (!version || version.state !== 'sealed') return h.error(400, 'VALIDATION_FAILED', '只能采用本课堂已完成的转写版本');
      if (body.base_revision !== session.revision) return h.error(409, 'REVISION_CONFLICT', '课堂已在其他地方更新', session.revision);
      const at = h.now();
      store.versions = store.versions.map(item => item.class_session_id !== id || item.adopted === (item.id === version.id) ? item : { ...item, adopted: item.id === version.id, revision: item.revision + 1, updated_at: at });
      const next = { ...session, adopted_transcript_version_id: version.id, revision: session.revision + 1, updated_at: at };
      host.saveSession(next);
      return ok(next);
    });
    case 'GET /tasks': {
      const classId = query.get('class_session_id');
      const state = query.get('state');
      const visible = store.tasks.filter(item => (item.class_session_id === null || findSession(item.class_session_id)) && (!classId || item.class_session_id === classId) && (!state || item.state === state));
      return page([...visible].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id)));
    }
    case 'GET /tasks/:id': { const found = store.tasks.find(item => item.id === id && (!item.class_session_id || findSession(item.class_session_id))); return found ? ok(found) : h.error(404, 'NOT_FOUND', '任务不存在'); }
    case 'POST /tasks/:id/reconcile': {
      const found = store.tasks.find(item => item.id === id && (!item.class_session_id || findSession(item.class_session_id)));
      if (!found) return h.error(404, 'NOT_FOUND', '任务不存在');
      // Local reconciliation only reads durable facts; it never submits a new provider request.
      return ok({ state: found.state, provider_query_supported: false, checked_at: h.now(), new_submission: false } satisfies S['TaskReconcile']);
    }
    case 'POST /tasks/:id/cancel': {
      const found = store.tasks.find(item => item.id === id && (!item.class_session_id || findSession(item.class_session_id)));
      if (!found) return h.error(404, 'NOT_FOUND', '任务不存在');
      if (found.state !== 'queued' && found.state !== 'running') return ok(found);
      const next = { ...found, revision: found.revision + 1, updated_at: h.now(), state: found.state === 'queued' ? 'cancelled' as const : 'cancel_requested' as const };
      store.tasks = store.tasks.map(item => item.id === id ? next : item);
      if (next.state === 'cancelled') moveReservation(store, host, id, 'released');
      if (next.class_session_id) syncAi(store, host, h, next.class_session_id);
      return ok(next);
    }
    default: return null;
  }
}

function syncAi(store: ClassroomStore, host: ClassroomHost, h: ClassroomHelpers, classId: string) {
  const session = host.sessions().find(item => item.id === classId);
  if (!session) return;
  const ai = aiFlow(store.tasks.filter(item => item.class_session_id === classId));
  if (session.flows.ai !== ai) host.saveSession({ ...session, revision: session.revision + 1, updated_at: h.now(), flows: { ...session.flows, ai } });
}

/** Transcript versions generation reads: the adopted one, else every realtime/imported version (never an unadopted retranscription). */
export function activeVersionIds(store: ClassroomStore, session: S['ClassSession']): Set<string | null> {
  const versions = store.versions.filter(item => item.class_session_id === session.id);
  const adopted = session.adopted_transcript_version_id ?? versions.find(item => item.adopted)?.id ?? null;
  return new Set(adopted ? [adopted] : versions.filter(item => item.kind !== 'retranscription').map(item => item.id));
}

/** Every capture epoch's manifest of a class with its own readability (deadline and revocation). */
function audioEpochs(store: ClassroomStore, classId: string, now: string) {
  return epochIdsOf(store, classId).map(epochId => {
    const manifest = manifestOf(store, classId, epochId);
    const revoked = store.revokedAudio.has(epochId);
    const expired = manifest.audio_expires_at !== null && Date.parse(manifest.audio_expires_at) <= Date.parse(now);
    return { epochId, manifest, revoked, expired, order: store.epochOrder.get(epochId) ?? { offset: 0, fence: 1, expires: null, samples: 0 } };
  });
}
export type AudioSlice = { epochId: string; seqs: number[]; missing: number[]; chunkMs: number };
/**
 * The durable chunks of readable epochs that overlap [from, to) on the class timeline. Each chunk of
 * an epoch covers an equal share of that epoch's recorded duration; chunks outside the window are not frozen.
 */
function selectAudio(store: ClassroomStore, session: S['ClassSession'], from: number, to: number, now: string) {
  const slices: AudioSlice[] = [];
  for (const epoch of audioEpochs(store, session.id, now)) {
    if (epoch.revoked || epoch.expired) continue;
    const durable = new Set(epoch.manifest.durable_ranges.flatMap(range => Array.from({ length: range.last_seq - range.first_seq + 1 }, (_, i) => range.first_seq + i)));
    if (!durable.size) continue;
    const count = epoch.manifest.expected_last_seq !== null ? epoch.manifest.expected_last_seq + 1 : Math.max(...durable) + 1;
    const duration = epoch.order.samples > 0 ? epoch.order.samples / 16 : session.effective_capture_ms;
    const chunkMs = Math.max(1, duration / count);
    const slice: AudioSlice = { epochId: epoch.epochId, seqs: [], missing: [], chunkMs };
    for (let seq = 0; seq < count; seq++) {
      const start = epoch.order.offset + seq * chunkMs; const end = start + chunkMs;
      if (end <= from || start >= to) continue;
      (durable.has(seq) ? slice.seqs : slice.missing).push(seq);
    }
    if (slice.seqs.length || slice.missing.length) slices.push(slice);
  }
  const frozen = slices.map(slice => ({ epochId: slice.epochId, seqs: slice.seqs }));
  return { slices, digest: sha256Hex(JSON.stringify(frozen)), ms: Math.round(slices.reduce((sum, slice) => sum + slice.seqs.length * slice.chunkMs, 0)) };
}

/** Original-audio refusals of 06c, distinguished only by error code (HTTP 0.3.0); a frozen selection is rechecked chunk epoch by epoch. */
function audioRefusal(store: ClassroomStore, h: ClassroomHelpers, session: S['ClassSession'], frozen?: AudioSlice[]): ClassroomReply | null {
  const dev = store.retranscribeDev.audio.get(session.id);
  const epochs = audioEpochs(store, session.id, h.now());
  const usable = epochs.filter(item => !item.revoked && !item.expired);
  const used = frozen ? epochs.filter(item => frozen.some(slice => slice.epochId === item.epochId && slice.seqs.length > 0)) : [];
  if (dev === 'deleted' || used.some(item => item.revoked) || (epochs.length > 0 && usable.length === 0 && epochs.some(item => item.revoked))) return h.error(410, 'AUDIO_DELETED', '原音已删除或访问已撤销');
  if (dev === 'expired' || used.some(item => item.expired) || (session.audio_expires_at !== null && Date.parse(session.audio_expires_at) <= Date.parse(h.now())) || (epochs.length > 0 && usable.length === 0)) return h.error(410, 'AUDIO_EXPIRED', '原音已到期');
  return null;
}
/**
 * Admission of 06c: the first retranscription of a class needs no approval; every further run
 * (a retry included) needs a one-use approval bound to that plan or original task. The monthly
 * millisecond bucket must cover the frozen units.
 */
function quotaRefusal(store: ClassroomStore, host: ClassroomHost, h: ClassroomHelpers, classId: string, approval: string, units: number): ClassroomReply | null {
  const bucket = host.quota().buckets.find(item => item.kind === 'retranscribe_month_ms');
  if (store.retranscribeDev.quota === 'month' || (bucket && bucket.remaining < units)) return h.error(409, 'QUOTA_MONTH_EXCEEDED', '本月重新转写时长不足');
  const admitted = store.tasks.filter(item => item.class_session_id === classId && item.kind === 'asr_retranscribe').length;
  if (store.retranscribeDev.quota === 'session' || (admitted >= 1 && !store.retranscribeDev.approvals.has(approval))) return h.error(409, 'QUOTA_SESSION_EXCEEDED', '本节课追加重新转写需要审批');
  return null;
}
/** Consumes the matching approval and reserves the frozen units for the new task (moved once with its outcome). */
function admit(store: ClassroomStore, host: ClassroomHost, approval: string, taskId: string, units: number) {
  store.retranscribeDev.approvals.delete(approval);
  const bucket = host.quota().buckets.find(item => item.kind === 'retranscribe_month_ms');
  if (!bucket) return;
  bucket.remaining -= units;
  bucket.reserved += units;
  store.reservations.set(taskId, { bucket: bucket.kind, units, phase: 'reserved' });
}
/** The original frozen plan of an attempt, also through a chain of retries. */
function originPlan(store: ClassroomStore, source: S['Task']) {
  let origin: S['Task'] | undefined = source;
  while (origin?.parent_task_id) origin = store.tasks.find(item => item.id === origin!.parent_task_id);
  return [...store.plans.values()].find(record => record.taskId === (origin ?? source).id);
}

/** A frozen retranscription plan: only durable chunks inside the requested window are selected. */
function retranscribePlan(store: ClassroomStore, h: ClassroomHelpers, session: S['ClassSession'], body: Record<string, unknown>): ClassroomReply {
  if (!['all', 'range'].includes(String(body.scope)) || (Array.isArray(body.seen_sources) && body.seen_sources.length > 0) || body.include_images !== false) return h.error(400, 'VALIDATION_FAILED', '重新转写只能选择整节或时间范围，且不发送图片');
  if (body.scope === 'range' && !(nonNegative(body.start_ms) && nonNegative(body.end_ms) && (body.end_ms as number) > (body.start_ms as number))) return h.error(400, 'VALIDATION_FAILED', '范围需要起止时间');
  if (session.capture_ended_at === null) return h.error(409, 'SOURCE_UNAVAILABLE', '课堂仍在采集，结束后才能重新转写');
  const refusal = audioRefusal(store, h, session);
  if (refusal) return refusal;
  const from = body.scope === 'range' ? body.start_ms as number : 0;
  const to = body.scope === 'range' ? body.end_ms as number : Number.MAX_SAFE_INTEGER;
  const selection = selectAudio(store, session, from, to, h.now());
  const chunks = selection.slices.reduce((sum, slice) => sum + slice.seqs.length, 0);
  if (chunks === 0) return h.error(409, 'SOURCE_UNAVAILABLE', '所选范围没有已持久保存的原音，无法重新转写');
  const partial = selection.slices.some(slice => slice.missing.length > 0);
  if (partial && body.accept_partial_scope !== true) return h.error(409, 'SOURCE_UNAVAILABLE', '所选范围内有分片缺失。可以等待上传完成，或明确选择按已有部分重新转写。');
  const created = h.now();
  const serial = store.plans.size + 1;
  const ms = Math.max(1, selection.ms);
  const plan: S['GenerationPlan'] = { id: h.newId(), class_session_id: session.id, kind: 'asr_retranscribe', plan_sha256: sha256Hex(`retranscribe:${session.id}:${serial}:${selection.digest}`), source_snapshot: null,
    audio_manifest_sha256: selection.digest, audio_chunk_ids: selection.slices.flatMap((slice, e) => slice.seqs.map(seq => uuid('7a000000', serial * 10_000_000 + e * 100_000 + seq + 1))).slice(0, 50_000),
    destination: MOCK_DESTINATION, prompt_version: 'mock-filetrans-1', output_schema_version: 'mock-transcript-1', price_version: 'mock-price-1', estimated_batches: 1,
    maximum_cost_microyuan: String(Math.ceil(ms / 1000) * 50), quota_units: ms, expires_at: iso(Date.parse(created) + 10 * 60_000), source_status: 'unchanged', contains_images: false };
  store.plans.set(plan.id, { plan, request: { ...body, start_ms: from, end_ms: Math.min(to, Number.MAX_SAFE_INTEGER) } as unknown as S['GenerationPlanRequest'], taskId: null, audio: selection.slices });
  if (partial) store.partialPlans.add(plan.id);
  return { status: 201, body: plan };
}

/**
 * A succeeded retranscription yields a new, unadopted transcript version (a candidate) and returns its ID.
 * When the frozen audio was revoked or expired meanwhile, nothing is published (null).
 */
function retranscribed(store: ClassroomStore, h: ClassroomHelpers, source: S['Task']): string | null {
  const classId = source.class_session_id!;
  const record = originPlan(store, source);
  const epochs = audioEpochs(store, classId, h.now());
  const frozen = record?.audio ?? [];
  if (store.retranscribeDev.audio.get(classId) === 'deleted' || frozen.some(slice => slice.seqs.length > 0 && epochs.some(item => item.epochId === slice.epochId && (item.revoked || item.expired)))) return null;
  const created = h.now();
  const versionId = h.newId();
  const request = record?.request as unknown as { start_ms: number; end_ms: number } | undefined;
  const from = request?.start_ms ?? 0; const to = request?.end_ms ?? Number.MAX_SAFE_INTEGER;
  // Synthetic recognition of the frozen audio: realtime finals inside the window stand in for the audio content.
  const realtime = new Set(store.versions.filter(item => item.class_session_id === classId && item.kind !== 'retranscription').map(item => item.id));
  const basis = store.segments.filter(item => item.class_session_id === classId && item.kind === 'machine' && item.status === 'final' && realtime.has(item.transcript_version_id)
    && item.start_ms !== null && item.start_ms >= from && (item.end_ms ?? item.start_ms) <= to);
  store.versions.push({ id: versionId, revision: 1, created_at: created, updated_at: created, class_session_id: classId, kind: 'retranscription', state: 'sealed', source_task_id: source.id, adopted: false });
  const base = (item: Partial<S['TranscriptSegment']>): S['TranscriptSegment'] => ({ id: h.newId(), revision: 1, created_at: created, updated_at: created, class_session_id: classId, transcript_version_id: versionId, stream_id: null,
    segment_revision_id: h.newId(), kind: 'machine', origin: 'retranscription', status: 'final', text: '', start_ms: null, end_ms: null, timestamp_source: 'capture_mapping', provider_request_id: null, source_chunk_ids: [], adopted_revision_id: null, ...item });
  if (basis.length) for (const item of basis) store.segments.push(base({ text: item.text.replace('（合成）', '（合成·重新转写）'), start_ms: item.start_ms, end_ms: item.end_ms }));
  else store.segments.push(base({ text: '（合成·重新转写）这段原音没有实时转写对照。', start_ms: from, end_ms: Math.min(to, from + 20_000) }));
  return versionId;
}

function planFor(store: ClassroomStore, host: ClassroomHost, h: ClassroomHelpers, classId: string, body: Record<string, unknown>): ClassroomReply {
  const session = host.sessions().find(item => item.id === classId && item.deleted_at === null && host.courseExists(item.course_id));
  if (!session) return h.error(404, 'NOT_FOUND', '课堂不存在');
  const kinds = ['ai_question', 'ai_explanation', 'review_summary', 'asr_retranscribe'];
  const scopes = ['recent30', 'recent120', 'thought_group', 'slides', 'all', 'marked', 'range'];
  if (!only(body, ['kind', 'scope', 'anchor_ms', 'start_ms', 'end_ms', 'seen_sources', 'question', 'include_images', 'card_types', 'english_answer', 'accept_partial_scope'])
    || !kinds.includes(String(body.kind)) || !scopes.includes(String(body.scope)) || !Array.isArray(body.seen_sources) || typeof body.include_images !== 'boolean' || typeof body.accept_partial_scope !== 'boolean') return h.error(400, 'VALIDATION_FAILED', '生成请求无效');
  const kind = body.kind as S['GenerationPlan']['kind'];
  if (kind === 'asr_retranscribe') return retranscribePlan(store, h, session, body);
  if (kind === 'ai_question' && (typeof body.question !== 'string' || body.question.trim().length === 0 || body.question.length > 2000)) return h.error(400, 'VALIDATION_FAILED', '请输入问题');
  if (body.scope === 'range' && !(nonNegative(body.start_ms) && nonNegative(body.end_ms) && (body.end_ms as number) > (body.start_ms as number))) return h.error(400, 'VALIDATION_FAILED', '范围需要起止时间');
  const quota = host.quota();
  const bucketKind = kind === 'review_summary' ? 'summary_month_count' : 'interactive_month_count';
  const bucket = quota.buckets.find(item => item.kind === bucketKind);
  if (!quota.ai_enabled || quota.platform_billing_state === 'blocked100' || quota.platform_billing_state === 'paused') return h.error(409, 'BUDGET_BLOCKED', '新的 AI 请求已暂停');
  if (bucket && bucket.remaining < 1) return h.error(409, 'QUOTA_EXCEEDED', '本月额度已用完');

  // Whole-class scopes include finals without a time position; time scopes need wholly contained times.
  const active = activeVersionIds(store, session);
  const allFinals = store.segments.filter(item => item.class_session_id === classId && item.kind === 'machine' && item.status === 'final' && active.has(item.transcript_version_id));
  const finals = allFinals.filter(item => item.start_ms !== null);
  const latestEnd = Math.max(0, ...finals.map(item => item.end_ms ?? item.start_ms ?? 0));
  const window = body.scope === 'recent30' ? 30_000 : body.scope === 'recent120' ? 120_000 : body.scope === 'thought_group' ? 45_000 : null;
  const anchor = nonNegative(body.anchor_ms) ? body.anchor_ms as number : latestEnd;
  const slides = store.slides.filter(item => item.class_session_id === classId && !item.removed);
  if (body.scope === 'slides') {
    if (!slides.length) return h.error(409, 'SOURCE_UNAVAILABLE', '还没有可用的课件图片，暂不能解释课件');
    if (body.include_images !== true) return h.error(400, 'VALIDATION_FAILED', '解释课件需要确认发送图片');
  }
  const within = (from: number, to: number) => finals.filter(item => item.start_ms !== null && item.end_ms !== null && item.start_ms >= from && item.end_ms <= to);
  const markedIds = new Set(store.marks.filter(item => item.class_session_id === classId && item.anchor.kind === 'source' && item.anchor.source_ref.kind === 'transcript_segment').map(item => (item.anchor as { source_ref: S['SourceRef'] }).source_ref.id));
  const selected = body.scope === 'slides' ? []
    : body.scope === 'range' ? within(body.start_ms as number, body.end_ms as number)
      : window !== null ? within(anchor - window, anchor)
        : body.scope === 'marked' ? allFinals.filter(item => markedIds.has(item.id))
          : allFinals;
  if (selected.length === 0 && body.scope !== 'slides') return h.error(409, 'SOURCE_UNAVAILABLE', '所选范围还没有正式转写，暂不能生成');

  // Missing material is explicit: the class is still recording, audio is missing, or ASR has not finished.
  const manifest = store.manifests.get(classId);
  const missing: string[] = [];
  if (kind === 'review_summary') {
    if (session.capture_ended_at === null) missing.push('课堂仍在采集，结束前的内容尚未完整');
    const outstanding = manifest ? outstandingChunks(store, classId) : 0;
    if (outstanding) missing.push(`原音有 ${outstanding} 个分片尚未持久确认`);
    if (session.flows.asr !== 'succeeded') missing.push('转写尚未全部完成');
  }
  if (missing.length && !body.accept_partial_scope) return h.error(409, 'SOURCE_UNAVAILABLE', `材料尚未齐全：${missing.join('；')}。可以等待，或明确选择按部分范围生成。`);

  const created = h.now();
  const refs: S['SourceRef'][] = selected.map(item => ({ kind: 'transcript_segment', id: item.id, revision_id: item.segment_revision_id, content_sha256: segmentDigest(item) }));
  if (body.include_images) for (const slide of slides) refs.push({ kind: 'slide_version', id: slide.id, revision_id: slide.version_id, content_sha256: slideDigest(store, slide) });
  const timed = selected.filter(item => item.start_ms !== null);
  const starts = timed.map(item => item.start_ms as number);
  const snapshot: S['SourceSnapshot'] = { id: h.newId(), class_session_id: classId, snapshot_sha256: hex(store.plans.size + 4096), source_refs: refs,
    start_ms: starts.length ? Math.min(...starts) : 0, end_ms: timed.length ? Math.max(...timed.map(item => item.end_ms ?? item.start_ms ?? 0)) : 0, scope_complete: missing.length === 0, missing_reasons: missing, created_at: created };
  const plan: S['GenerationPlan'] = { id: h.newId(), class_session_id: classId, kind, plan_sha256: hex(store.plans.size + 8192), source_snapshot: snapshot, audio_manifest_sha256: null, audio_chunk_ids: [],
    destination: MOCK_DESTINATION, prompt_version: 'mock-prompt-1', output_schema_version: 'mock-output-1', price_version: 'mock-price-1',
    estimated_batches: kind === 'review_summary' ? Math.max(1, Math.ceil(selected.length / 40)) : 1, maximum_cost_microyuan: kind === 'review_summary' ? '120000' : '20000', quota_units: 1,
    expires_at: iso(Date.parse(created) + 10 * 60_000), source_status: 'unchanged', contains_images: Boolean(body.include_images) && slides.length > 0 };
  store.plans.set(plan.id, { plan, request: body as unknown as S['GenerationPlanRequest'], taskId: null });
  return { status: 201, body: plan };
}

/** PR-10b development panel: original audio state and quota refusals for retranscription. */
export function setRetranscribeDev(store: ClassroomStore, value: { classId?: string; audio?: 'ok' | 'expired' | 'deleted'; quota?: 'ok' | 'session' | 'month'; approve?: boolean; approvePlan?: string; approveTask?: string }) {
  if (value.classId && value.audio) { if (value.audio === 'ok') store.retranscribeDev.audio.delete(value.classId); else store.retranscribeDev.audio.set(value.classId, value.audio); }
  if (value.quota) store.retranscribeDev.quota = value.quota;
  // Synthetic internal approvals (the browser has no approval entry), each bound to one plan or one original task.
  if (value.approvePlan) store.retranscribeDev.approvals.add(`plan:${value.approvePlan}`);
  if (value.approveTask) store.retranscribeDev.approvals.add(`task:${value.approveTask}`);
  if (value.classId && value.approve) {
    // Panel shortcut: approve the newest unexecuted plan and the newest retryable unknown attempt of the class.
    const plan = [...store.plans.values()].filter(record => record.plan.class_session_id === value.classId && record.plan.kind === 'asr_retranscribe' && !record.taskId).at(-1);
    if (plan) store.retranscribeDev.approvals.add(`plan:${plan.plan.id}`);
    const pending = store.tasks.filter(item => item.class_session_id === value.classId && item.kind === 'asr_retranscribe' && item.state === 'unknown' && !store.tasks.some(other => other.parent_task_id === item.id)).at(-1);
    if (pending) store.retranscribeDev.approvals.add(`task:${pending.id}`);
  }
  return { audio: Object.fromEntries(store.retranscribeDev.audio), quota: store.retranscribeDev.quota, approvals: [...store.retranscribeDev.approvals] };
}

/** Development-panel helper: move a task to a later state the way a worker would report it. */
export function advanceTask(store: ClassroomStore, host: ClassroomHost, h: ClassroomHelpers, taskId: string, outcome: TaskOutcome): S['Task'] | null {
  const found = store.tasks.find(item => item.id === taskId);
  if (!found || ['succeeded', 'failed', 'cancelled'].includes(found.state)) return null;
  const terminal = outcome !== 'running';
  const next: S['Task'] = { ...found, revision: found.revision + 1, updated_at: h.now(), state: outcome,
    provider_outcome: outcome === 'running' ? 'not_submitted' : outcome, completed_units: terminal && outcome === 'succeeded' ? found.total_units ?? 1 : found.completed_units,
    may_have_charged: outcome === 'unknown' || outcome === 'succeeded' || found.may_have_charged,
    // A retranscription settles within the maximum the user confirmed.
    settled_cost_microyuan: outcome === 'succeeded' ? (found.kind === 'asr_retranscribe' ? found.maximum_cost_microyuan : '9000') : found.settled_cost_microyuan,
    error_code: outcome === 'failed' ? 'PROVIDER_FAILED' : null, result_id: outcome === 'succeeded' && found.kind !== 'asr_retranscribe' ? h.newId() : null };
  if (found.kind === 'asr_retranscribe' && outcome === 'succeeded') {
    const versionId = retranscribed(store, h, found);
    // Audio revoked or expired before completion: the provider work and its charge stay recorded, no text is published.
    if (versionId) next.result_id = versionId; else Object.assign(next, { state: 'failed', provider_outcome: 'succeeded', error_code: 'AUDIO_DELETED' });
  }
  store.tasks = store.tasks.map(item => item.id === taskId ? next : item);
  // Units move once: submitted when running, settled on success or a failure after submission,
  // kept as unknown (never refunded) on an unknown outcome, released when never submitted.
  moveReservation(store, host, taskId, outcome === 'running' ? 'submitted' : outcome === 'succeeded' ? 'settled' : outcome === 'unknown' ? 'unknown' : found.state === 'running' ? 'settled' : 'released');
  if (next.kind === 'ai_question') host.updateQuestion(taskId, { state: outcome, answer_markdown: outcome === 'succeeded' ? '合成回答：这是开发预览中的示例回答，没有调用任何模型。' : null });
  if (next.kind === 'review_summary' && outcome === 'succeeded' && next.class_session_id) {
    // A generated summary is only a candidate; nothing becomes the adopted version automatically.
    const scope = [...store.plans.values()].find(record => record.taskId === taskId)?.plan.source_snapshot;
    const documentId = h.newId();
    host.addDocument({ id: documentId, revision: 1, created_at: h.now(), updated_at: h.now(), class_session_id: next.class_session_id, kind: 'summary',
      title: scope && !scope.scope_complete ? '课堂总结（部分范围候选）' : '课堂总结（候选）', adopted_revision_id: null, latest_personal_revision_id: null, candidate_revision_ids: [next.result_id!], understood: false, source_status: 'unchanged' });
    host.addCandidate({ revisionId: next.result_id!, documentId, snapshot: scope ?? null, created: h.now(), rawOutputRef: h.newId() });
  }
  if (next.class_session_id) syncAi(store, host, h, next.class_session_id);
  return next;
}

/** Development-panel helper: the ASR flow of a stopped class finishes draining. */
export function finishAsr(host: ClassroomHost, h: ClassroomHelpers, classId: string, state: 'succeeded' | 'failed' | 'unknown') {
  const session = host.sessions().find(item => item.id === classId);
  if (!session) return false;
  host.saveSession({ ...session, revision: session.revision + 1, updated_at: h.now(), flows: { ...session.flows, asr: state } });
  return true;
}

/**
 * Development bridge for the PR-09a demo capture, whose lease is generated inside the page runtime:
 * the Mock registers that classroom and lease so the classroom pages can read and stop it.
 */
export function ensureDemoClassroom(store: ClassroomStore, host: ClassroomHost, h: ClassroomHelpers, input: { classId: string; title: string; captureEpochId: string; fenceToken: number }) {
  let session = host.sessions().find(item => item.id === input.classId);
  const courseId = host.firstCourseId();
  if (!session && !courseId) return false;
  if (!session) {
    const created = h.now();
    session = { id: input.classId, revision: 1, created_at: created, updated_at: created, course_id: courseId!, title: input.title, language: 'auto', started_at: created, capture_ended_at: null,
      audio_expires_at: null, adopted_transcript_version_id: null, flows: { capture: 'recording', upload: 'pending', asr: 'idle', ai: 'idle', sync: 'synced' }, effective_capture_ms: 0, known_gap_count: 0, unknown_gap_count: 0, deleted_at: null };
    host.addSession(session);
  } else if (session.deleted_at !== null) return false;
  else if (session.capture_ended_at !== null) {
    // Demo fixture only: each PR-09a quick start is a new demo run on the same synthetic classroom.
    if (store.manifests.get(input.classId)?.capture_epoch_id === input.captureEpochId) return false;
    store.manifests.delete(input.classId);
    for (const [epochId, classId] of [...store.epochs]) if (classId === input.classId) { store.epochs.delete(epochId); store.epochManifests.delete(epochId); store.epochOrder.delete(epochId); }
    store.captured.delete(input.classId);
    store.retired.delete(input.classId);
    session = { ...session, revision: session.revision + 1, updated_at: h.now(), capture_ended_at: null, audio_expires_at: null, effective_capture_ms: 0, flows: { ...session.flows, capture: 'recording', upload: 'pending', asr: 'idle' } };
    host.saveSession(session);
  }
  const lease = store.leases.get(input.classId);
  store.epochs.set(input.captureEpochId, input.classId);
  if (!store.epochOrder.has(input.captureEpochId)) store.epochOrder.set(input.captureEpochId, { offset: session.effective_capture_ms, fence: Math.max(1, input.fenceToken), expires: iso(Date.parse(h.now()) + 7 * DAY), samples: 0 });
  if (session.flows.capture !== 'recording' && session.flows.capture !== 'paused') {
    session = { ...session, revision: session.revision + 1, updated_at: h.now(), flows: { ...session.flows, capture: 'recording' } };
    host.saveSession(session);
  }
  if (!lease || lease.capture_epoch_id !== input.captureEpochId || lease.fence_token !== input.fenceToken) {
    store.leases.set(input.classId, { id: h.newId(), class_session_id: input.classId, capture_epoch_id: input.captureEpochId, fence_token: Math.max(1, input.fenceToken),
      expires_at: iso(Date.parse(h.now()) + 30_000), renew_after_seconds: 10, timeline_offset_ms: 0, sample_rate: 16000, channel_count: 1, encoding: 'pcm_s16le' });
  }
  return true;
}

/**
 * Development bridge: durable receipts observed in the page (also those arriving after the stop) are
 * recorded by the Mock; a sealed manifest and the upload flow are recomputed from them.
 */
export function recordDurable(store: ClassroomStore, host: ClassroomHost, h: ClassroomHelpers, classId: string, captureEpochId: string, seqs: number[]) {
  // Receipts are scoped to their capture epoch: chunk numbers restart in every epoch.
  const durable = [...new Set([...(store.durable.get(captureEpochId) ?? []), ...seqs])].sort((a, b) => a - b);
  store.durable.set(captureEpochId, durable);
  if (store.epochs.get(captureEpochId) !== classId && store.manifests.get(classId)?.capture_epoch_id !== captureEpochId) return;
  // A recorded epoch's range is refreshed; an open or taken-over epoch reads its durable set directly.
  const sealed = store.epochManifests.get(captureEpochId);
  if (sealed && sealed.class_session_id === classId) {
    const next = sealEpoch(store, classId, captureEpochId, sealed.expected_last_seq, sealed.audio_expires_at, sealed.sealed);
    if (store.manifests.get(classId)?.capture_epoch_id === captureEpochId) store.manifests.set(classId, next);
  }
  const session = host.sessions().find(item => item.id === classId);
  if (session && session.capture_ended_at !== null && !outstandingChunks(store, classId) && session.flows.upload !== 'durable') {
    host.saveSession({ ...session, flows: { ...session.flows, upload: 'durable' }, revision: session.revision + 1, updated_at: h.now() });
  }
}

/**
 * Development bridge: a lost lease or an account teardown means the server no longer holds that
 * classroom's lease. Its capture projection becomes failed, so the classroom can be started again
 * explicitly instead of looking captured elsewhere.
 */
export function releaseLease(store: ClassroomStore, host: ClassroomHost, h: ClassroomHelpers, classId: string) {
  const released = store.leases.delete(classId);
  const session = host.sessions().find(item => item.id === classId);
  if (released && session && session.capture_ended_at === null && (session.flows.capture === 'recording' || session.flows.capture === 'paused')) {
    host.saveSession({ ...session, revision: session.revision + 1, updated_at: h.now(), flows: { ...session.flows, capture: 'failed' } });
  }
  return released;
}

/** Development bridge: an audio deletion event revokes reads of that epoch's original audio. */
export function revokeAudio(store: ClassroomStore, classId: string, captureEpochId: string) {
  if (store.epochs.get(captureEpochId) !== classId && store.manifests.get(classId)?.capture_epoch_id !== captureEpochId) return false;
  store.revokedAudio.add(captureEpochId);
  return true;
}

/** Development control (browser tests): cap the manifest page size and/or fail every continuation. */
export function setManifestDev(store: ClassroomStore, value: Partial<ClassroomStore['manifestDev']>) { store.manifestDev = { ...store.manifestDev, ...value }; return store.manifestDev; }

/** Test helper: every issued manifest-list cursor expires (the next continuation gets CURSOR_EXPIRED). */
export function expireManifestCursors(store: ClassroomStore) { for (const cursor of store.manifestCursors.values()) cursor.expired = true; }

/** Development bridge: an account teardown retires every capture lease that account still holds. */
export function releaseAccountLeases(store: ClassroomStore, host: ClassroomHost, h: ClassroomHelpers) {
  let released = 0;
  for (const classId of [...store.leases.keys()]) if (releaseLease(store, host, h, classId)) released++;
  return released;
}

/** Development bridge: task events accepted by the in-page runtime are projected into the task store. */
export function projectTask(store: ClassroomStore, host: ClassroomHost, h: ClassroomHelpers, input: { id: string; classId: string; state: S['Task']['state']; revision: number; mayHaveCharged: boolean }) {
  if (!host.sessions().some(item => item.id === input.classId && item.deleted_at === null)) return false;
  const existing = store.tasks.find(item => item.id === input.id);
  if (existing && (existing.revision >= input.revision || existing.class_session_id !== input.classId)) return false;
  const now = h.now();
  const next: S['Task'] = existing
    ? { ...existing, revision: input.revision, updated_at: now, state: input.state, may_have_charged: input.mayHaveCharged, provider_outcome: input.state === 'unknown' ? 'unknown' : existing.provider_outcome }
    : { ...task({ id: input.id, classId: input.classId, kind: 'ai_explanation', state: input.state, created: now, mayHaveCharged: input.mayHaveCharged, providerOutcome: input.state === 'unknown' ? 'unknown' : 'not_submitted', snapshotId: null }), revision: input.revision };
  store.tasks = existing ? store.tasks.map(item => item.id === input.id ? next : item) : [...store.tasks, next];
  syncAi(store, host, h, input.classId);
  return true;
}

/** Development bridge: a classroom deletion event revokes server access before any cleanup. */
export function revokeClassroom(store: ClassroomStore, host: ClassroomHost, h: ClassroomHelpers, classId: string) {
  const session = host.sessions().find(item => item.id === classId);
  store.leases.delete(classId);
  if (!session || session.deleted_at !== null) return false;
  const now = h.now();
  host.saveSession({ ...session, deleted_at: now, revision: session.revision + 1, updated_at: now });
  return true;
}

/** Development bridge: final transcript events observed in the page are also persisted by the Mock. */
export function persistFinal(store: ClassroomStore, host: ClassroomHost, h: ClassroomHelpers, input: { classId: string; streamId: string; segmentId: string; segmentRevisionId: string; versionId: string; text: string; startMs: number | null; endMs: number | null; revision: number }) {
  const session = host.sessions().find(item => item.id === input.classId);
  if (!session) return;
  if (!store.versions.some(item => item.id === input.versionId)) {
    store.versions.push({ id: input.versionId, revision: 1, created_at: h.now(), updated_at: h.now(), class_session_id: input.classId, kind: 'realtime', state: 'open', source_task_id: null, adopted: false });
  }
  const existing = store.segments.find(item => item.id === input.segmentId);
  if (existing && existing.revision >= input.revision) return;
  const next: S['TranscriptSegment'] = { id: input.segmentId, revision: input.revision, created_at: existing?.created_at ?? h.now(), updated_at: h.now(), class_session_id: input.classId,
    transcript_version_id: input.versionId, stream_id: input.streamId, segment_revision_id: input.segmentRevisionId, kind: 'machine', origin: 'live_asr', status: 'final', text: input.text,
    start_ms: input.startMs, end_ms: input.endMs, timestamp_source: input.startMs === null ? 'unknown' : 'capture_mapping', provider_request_id: null, source_chunk_ids: [], adopted_revision_id: null };
  if (existing) store.segmentHistory.push(existing);
  store.segments = existing ? store.segments.map(item => item.id === input.segmentId ? next : item) : [...store.segments, next];
  if (session.flows.asr === 'idle') host.saveSession({ ...session, revision: session.revision + 1, updated_at: h.now(), flows: { ...session.flows, asr: 'streaming' } });
}
