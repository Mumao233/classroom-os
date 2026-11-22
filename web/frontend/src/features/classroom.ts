import { useSyncExternalStore } from 'react';
import type { components } from '../generated/openapi';
import { ApiClientError, type ApiClient } from '../api/client';
import type { DraftStore, LiveSegmentInput } from '../domain/classroom';
import { useAccountQuery, useAppData } from './data';

type S = components['schemas'];

/** The capture currently bound in this page: active, or stopped here and not yet superseded. */
export type BoundCapture = {
  classId: string;
  captureEpochId: string;
  fenceToken: number;
  active: boolean;
  flows: S['FlowStates'];
  leaseLost: boolean;
  classroomRevoked: boolean;
  lastSeq: number;
  lastSample: number;
};

/**
 * Port to the account-level capture runtime. The Mock adapter drives the PR-09a in-page runtime;
 * a later integration PR supplies the PR-05b CaptureRuntime. Pages never touch either directly.
 */
export interface ClassroomCapturePort {
  bound(): BoundCapture | null;
  /** Bind a server-issued lease and start capturing. Never called without an explicit user action. */
  start(classId: string, lease: CaptureBinding): boolean;
  pause(): boolean;
  /** Bind the new epoch and fence the server issued while staying paused. */
  rebind(lease: CaptureBinding): boolean;
  /** Resume with the new epoch and fence the server issued (resuming retires the paused epoch). */
  resume(lease: CaptureBinding): boolean;
  /** Current capture position of the bound classroom on its capture timeline (ms), or null. */
  positionMs(classId: string): number | null;
  /** Stop new capture immediately in this device; the server range is confirmed separately. */
  stopLocal(): void;
  durableSeqs(classId: string, captureEpochId: string): number[];
  pendingChunks(classId: string): number;
  liveSegments(classId: string): LiveSegmentInput[];
}

/**
 * A stop the server has not confirmed. `afterCommand` marks a stop issued while a pause/resume was
 * still unconfirmed: its body is final only once that command is settled (a confirmed resume moves
 * the server to a new epoch and fence, which the stop must name). The key has not been sent yet
 * while `afterCommand` is set.
 */
export type PendingStop = { body: S['CaptureStop']; key: string; afterCommand?: boolean };
/** A pause or resume the server has not confirmed: replayed with the same body and key. */
export type PendingCommand = { action: 'pause' | 'resume'; body: S['CaptureCommand']; key: string };
export type CaptureBinding = { captureEpochId: string; fenceToken: number; timelineOffsetMs: number };

export type ClassroomShell = {
  capture: ClassroomCapturePort;
  drafts: DraftStore;
  /** Stable synthetic device identity of this browser profile (Mock). */
  deviceId: string;
  /** Consent texts the user sees before starting; versions are echoed to the server. */
  consent: { microphone: string; recovery: string; asr: string };
  pendingStops: { get(classId: string): PendingStop | null; set(classId: string, value: PendingStop | null): void };
  pendingCommands: { get(classId: string): PendingCommand | null; set(classId: string, value: PendingCommand | null): void };
  /** True while leaving would lose local work: an unconfirmed stop or stopped capture with local audio. */
  unsafeToLeave(): boolean;
  /** Change notifications for pending stops and commands (account-shell unload guard). */
  subscribe(listener: () => void): () => void;
  version(): number;
  /** Device-level layout memory (no private content). */
  layoutStorage: { read(): string | null; write(value: string): void };
  /** Mock only: register the PR-09a demo capture with the Mock server. */
  ensureBoundClassroom?: (classId: string) => boolean;
  /** Mock only: hand durable receipts seen in the page to the Mock server before stopping. */
  beforeServerStop?: (classId: string) => void;
  /** Wall-clock milliseconds; the Mock uses a fixed clock for reproducible screenshots. */
  now: () => number;
};

export function useClassroomShell(): ClassroomShell {
  const shell = useAppData().classroom;
  if (!shell) throw new Error('Classroom shell missing');
  return shell;
}

export function useDraft(classId: string, field: string): [string, (value: string) => void] {
  const { drafts } = useClassroomShell();
  useSyncExternalStore(drafts.subscribe, drafts.version, drafts.version);
  return [drafts.get(classId, field), (value: string) => drafts.set(classId, field, value)];
}

// --------------------------------------------------------------------------- data hooks

export const useClassSession = (id: string) => useAccountQuery(['class-session', id], (api, signal) => api.classSession(id, signal));
/** The epoch's manifest when the capture epoch is known; the contract allows omitting it otherwise. */
export const useAudioManifest = (id: string, epochId: string | null, enabled = true) => useAccountQuery(['audio-manifest', id, epochId], (api, signal) => api.audioManifest(id, epochId, signal), enabled);
export const useMarks = (id: string) => useAccountQuery(['marks', id], (api, signal) => api.marks(id, signal));
export const useSlides = (id: string, enabled = true) => useAccountQuery(['slides', id], (api, signal) => api.slides(id, signal), enabled);
export const useTasks = (id: string) => useAccountQuery(['tasks', id], (api, signal) => api.tasks(id, signal));
export const useClassQuestions = (id: string) => useAccountQuery(['questions', id], (api, signal) => api.questions(id, signal));
/**
 * Durable machine segments of the classroom's displayed transcript: the adopted version, or the
 * realtime version(s) while nothing is adopted. Unadopted retranscriptions stay out of the timeline.
 */
export const useTranscript = (id: string) => useAccountQuery(['transcript', id], async (api, signal) => {
  const versions = await api.transcriptVersions(id, signal);
  const adopted = versions.items.filter(version => version.adopted);
  const shown = adopted.length ? adopted : versions.items.filter(version => version.kind === 'realtime');
  const pages = await Promise.all(shown.map(version => api.transcriptSegments(version.id, signal)));
  return { versions: shown, segments: pages.flatMap(page => page.items) };
});

// --------------------------------------------------------------------------- capture commands

export class CaptureCommandError extends Error {
  constructor(readonly code: 'NOT_BOUND' | 'LOCAL_START_FAILED' | 'LOCAL_COMMAND_FAILED', readonly cause?: unknown) { super(code); this.name = 'CaptureCommandError'; }
}

/**
 * A definitive answer: the server processed the request and rejected it (4xx other than timeout or
 * rate limiting). Anything else (network, 5xx, 408/429) may still have taken effect, so the original
 * body and key are kept for replay.
 */
export function isDefinitiveRejection(error: unknown): boolean {
  return error instanceof ApiClientError && error.kind === 'http' && error.status !== undefined && error.status >= 400 && error.status < 500 && ![408, 425, 429].includes(error.status);
}

// Operation keys of start intents, per shell (account epoch) and classroom: kept until the lease is
// bound or definitively rejected, so a lost acquire response is recovered by replay, not competed with.
const startKeys = new WeakMap<ClassroomShell, Map<string, string>>();

/** Explicit start: acquire a server lease first, then bind it to the local runtime. */
export async function startCapture(api: ApiClient, shell: ClassroomShell, classId: string, newKey: () => string): Promise<S['CaptureLease']> {
  const keys = startKeys.get(shell) ?? new Map<string, string>();
  startKeys.set(shell, keys);
  const key = keys.get(classId) ?? newKey();
  keys.set(classId, key);
  let lease: S['CaptureLease'];
  try {
    lease = await api.acquireCapture(classId, {
      device_id: shell.deviceId, microphone_consent_version: shell.consent.microphone, recovery_consent_version: shell.consent.recovery,
      asr_consent_version: shell.consent.asr, confirm_takeover: false, expected_lease_id: null,
    }, key);
  } catch (error) {
    if (isDefinitiveRejection(error)) keys.delete(classId);
    throw error;
  }
  if (!shell.capture.start(classId, binding(lease))) throw new CaptureCommandError('LOCAL_START_FAILED');
  keys.delete(classId);
  return lease;
}

const binding = (lease: S['CaptureLease']): CaptureBinding => ({ captureEpochId: lease.capture_epoch_id, fenceToken: lease.fence_token, timelineOffsetMs: lease.timeline_offset_ms });

type CaptureRange = Pick<BoundCapture, 'captureEpochId' | 'fenceToken' | 'lastSeq' | 'lastSample'>;

function commandBody(bound: CaptureRange, now: number): S['CaptureCommand'] {
  return { capture_epoch_id: bound.captureEpochId, fence_token: bound.fenceToken, last_local_seq: bound.lastSeq, last_sample: bound.lastSample, client_monotonic_ms: Math.max(0, Math.floor(now)) };
}

async function stopBody(classId: string, bound: CaptureRange, now: number): Promise<S['CaptureStop']> {
  const manifest = await digest(JSON.stringify({ class_session_id: classId, capture_epoch_id: bound.captureEpochId, last_local_seq: bound.lastSeq, last_sample: bound.lastSample }));
  return { ...commandBody(bound, now), manifest_sha256: manifest, known_gaps: [] };
}

/**
 * Pause takes effect locally first (capture must stop immediately when asked); the server is then
 * informed, and a failed call is reported while the local pause stays. Resume is the opposite: the
 * server retires the paused epoch and issues a new epoch and fence, so recording restarts only
 * after that lease is bound; on failure capture stays paused.
 */
export async function commandCapture(api: ApiClient, shell: ClassroomShell, classId: string, action: 'pause' | 'resume', key: string): Promise<{ serverConfirmed: boolean; error?: unknown }> {
  const bound = shell.capture.bound();
  if (!bound || bound.classId !== classId || !bound.active) throw new CaptureCommandError('NOT_BOUND');
  // An earlier unconfirmed command is settled first, with its original body and key.
  const earlier = await reconcileCommand(api, shell, classId);
  if (!earlier.settled) return { serverConfirmed: false, error: earlier.error };
  const current = shell.capture.bound();
  if (!current || !current.active) throw new CaptureCommandError('NOT_BOUND');
  // The settled earlier command may already have produced the requested state.
  if ((action === 'resume' && current.flows.capture !== 'paused') || (action === 'pause' && current.flows.capture === 'paused')) return { serverConfirmed: true };
  if (action === 'pause') {
    if (!shell.capture.pause()) throw new CaptureCommandError('LOCAL_COMMAND_FAILED');
    shell.pendingCommands.set(classId, { action: 'pause', body: commandBody(current, shell.now()), key });
  } else {
    shell.pendingCommands.set(classId, { action: 'resume', body: commandBody(current, shell.now()), key });
  }
  const result = await reconcileCommand(api, shell, classId);
  return result.settled && !result.rejected ? { serverConfirmed: true } : { serverConfirmed: false, error: result.error };
}

export type Reconciled = { settled: boolean; rejected?: boolean; error?: unknown };
// One reconciliation per command key: concurrent callers (button, reconnect effect, stop) share it,
// so a receipt is applied exactly once.
const inflight = new Map<string, Promise<Reconciled>>();

/**
 * Replays the pending pause/resume of this classroom (same body, same key). A confirmed pause just
 * clears it. A confirmed resume completes the user's intent: capture resumes locally on the epoch
 * and fence the server issued, unless capture was stopped here meanwhile; then the stop, which
 * supersedes it, is retargeted to that epoch. A definitive rejection drops the command.
 */
export function reconcileCommand(api: ApiClient, shell: ClassroomShell, classId: string): Promise<Reconciled> {
  const pending = shell.pendingCommands.get(classId);
  if (!pending) return Promise.resolve({ settled: true });
  const running = inflight.get(pending.key);
  if (running) return running;
  const owned = () => shell.pendingCommands.get(classId)?.key === pending.key;
  const run = (async (): Promise<Reconciled> => {
    let lease: S['CaptureLease'];
    try { lease = await api.captureCommand(classId, pending.action, pending.body, pending.key); }
    catch (error) {
      if (!isDefinitiveRejection(error)) return { settled: false, error };
      if (owned()) { shell.pendingCommands.set(classId, null); await settleStopAfterCommand(shell, classId, null); }
      return { settled: true, rejected: true, error };
    }
    if (!owned()) return { settled: true };
    shell.pendingCommands.set(classId, null);
    if (pending.action === 'resume') {
      const current = shell.capture.bound();
      if (current?.classId === classId && current.active) {
        if (!shell.capture.resume(binding(lease))) throw new CaptureCommandError('LOCAL_COMMAND_FAILED');
        return { settled: true };
      }
      await settleStopAfterCommand(shell, classId, lease);
      return { settled: true };
    }
    await settleStopAfterCommand(shell, classId, null);
    return { settled: true };
  })().finally(() => inflight.delete(pending.key));
  inflight.set(pending.key, run);
  return run;
}

/** A stop waiting on a command becomes final: retargeted to a resumed epoch, or kept as recorded. */
async function settleStopAfterCommand(shell: ClassroomShell, classId: string, resumed: S['CaptureLease'] | null) {
  const stop = shell.pendingStops.get(classId);
  if (!stop?.afterCommand) return;
  // No audio was captured in the resumed epoch here: capture had already stopped locally.
  const body = resumed ? await stopBody(classId, { captureEpochId: resumed.capture_epoch_id, fenceToken: resumed.fence_token, lastSeq: 0, lastSample: 0 }, shell.now()) : stop.body;
  shell.pendingStops.set(classId, { key: stop.key, body });
}

async function digest(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(hash)].map(value => value.toString(16).padStart(2, '0')).join('');
}

/**
 * End of class: stop new capture now, record the range the device knows, then ask the server to
 * seal it. The pending stop (body and operation key) survives a failed call so it can be retried
 * with the same key from the processing page. An unconfirmed pause/resume is settled before the
 * stop is sent, so the stop names the epoch the server actually holds.
 */
export async function stopCapture(api: ApiClient, shell: ClassroomShell, classId: string, newKey: () => string): Promise<{ confirmed: boolean; manifest?: S['AudioManifest']; error?: unknown }> {
  const bound = shell.capture.bound();
  if (!bound || bound.classId !== classId) throw new CaptureCommandError('NOT_BOUND');
  shell.capture.stopLocal();
  if (!shell.pendingStops.get(classId)) {
    const afterCommand = shell.pendingCommands.get(classId) !== null;
    shell.pendingStops.set(classId, { key: newKey(), body: await stopBody(classId, bound, shell.now()), ...(afterCommand ? { afterCommand } : {}) });
  }
  return confirmStop(api, shell, classId);
}

export async function confirmStop(api: ApiClient, shell: ClassroomShell, classId: string): Promise<{ confirmed: boolean; manifest?: S['AudioManifest']; error?: unknown }> {
  if (shell.pendingStops.get(classId)?.afterCommand) {
    if (shell.pendingCommands.get(classId)) {
      const settled = await reconcileCommand(api, shell, classId);
      if (!settled.settled) return { confirmed: false, error: settled.error };
    } else await settleStopAfterCommand(shell, classId, null);
  }
  const pending = shell.pendingStops.get(classId);
  if (!pending) return { confirmed: true };
  shell.beforeServerStop?.(classId);
  try {
    const manifest = await api.stopCapture(classId, pending.body, pending.key);
    shell.pendingStops.set(classId, null);
    return { confirmed: true, manifest };
  } catch (error) { return { confirmed: false, error }; }
}

/** Consent wording shown before the first start (UI-CLASS-04, ASR-04). */
export const CONSENT_TEXT = {
  microphone: '开始后，本页面会使用麦克风采集课堂声音；暂停或结束后停止采集。开始前不会打开麦克风。',
  storage: '原音分片加密保存在服务器，用于转写、补传和回听，默认保存 7 天，到期后不能播放或重新识别。',
  asr: '音频经 Classroom OS 服务器转发给语音识别服务生成字幕；浏览器不保存任何服务密钥。',
  ai: 'AI 解释、问答和总结只在你确认后发送所选内容，每次确认前会显示范围与费用上限。',
};
