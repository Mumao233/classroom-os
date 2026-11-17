import { CaptureError, matchesReceipt, sha256 } from './ports';
import type { Ancestry, CaptureSchemas, CaptureTransport, Lease, PendingChunk, Receipt, RecordingCachePort } from './ports';

export interface CaptureState {
  version: 1;
  lease: Lease;
  generation: number;
  phase: 'ready' | 'recording' | 'paused' | 'stopped' | 'recovery';
  nextSeq: number;
  nextSample: number;
  pending: PendingChunk[];
  cleanup: Receipt[];
  unresolvedCommands: NonNullable<CaptureState['command']>[];
  command?: { action: 'renew' | 'pause' | 'resume' | 'stop'; key: string; body: CaptureSchemas['CaptureCommand'] | CaptureSchemas['CaptureStop'] };
}

/** One serialized writer. Restoring state never starts a microphone or renews a lease. */
export class CaptureRuntime {
  private state: CaptureState | null = null;
  private tail = Promise.resolve();
  private recoveryToken: string | undefined;
  private queuedBytes = 0;
  private readonly ancestry: Ancestry;

  constructor(private readonly transport: CaptureTransport, private readonly cache: RecordingCachePort,
    ancestry: Ancestry, private readonly maxPendingBytes: number) {
    if (!Number.isSafeInteger(maxPendingBytes) || maxPendingBytes < 32000) throw new CaptureError('INVALID_CAPACITY');
    this.ancestry = structuredClone(ancestry);
  }

  snapshot(): CaptureState | null { return this.state ? structuredClone(this.state) : null; }

  private serial<T>(action: () => Promise<T>): Promise<T> {
    const result = this.tail.then(action);
    this.tail = result.then(() => undefined, () => undefined);
    return result;
  }

  private current(): CaptureState {
    if (!this.state) throw new CaptureError('CAPTURE_NOT_PREPARED');
    return this.state;
  }

  private async persist(state: CaptureState): Promise<void> {
    const next = { ...state, generation: state.generation + 1 };
    await this.cache.saveCaptureState(next.lease.class_session_id, next.lease.capture_epoch_id,
      new TextEncoder().encode(JSON.stringify(next)), this.ancestry, next.lease.fence_token, next.generation);
    this.state = next;
  }

  /** Call after explicit acquisition and the PR-05a recording-key unlock. A cache write proves readiness. */
  prepare(lease: Lease): Promise<void> {
    const ownedLease = structuredClone(lease);
    return this.serial(async () => {
      if (this.state?.pending.length) throw new CaptureError('RECOVERY_REQUIRED');
      if (ownedLease.class_session_id !== this.ancestry.classSessionId) throw new CaptureError('SCOPE_MISMATCH');
      await this.persist({ version: 1, lease: ownedLease, generation: 0, phase: 'ready', nextSeq: 0, nextSample: 0,
        pending: [], cleanup: [], unresolvedCommands: [] });
    });
  }

  restore(classroom: string, epoch: string): Promise<boolean> {
    return this.serial(async () => {
      if (this.state) throw new CaptureError('CAPTURE_ALREADY_PREPARED');
      const bytes = await this.cache.readCaptureState(classroom, epoch);
      if (!bytes) return false;
      let state: CaptureState;
      try { state = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)) as CaptureState; }
      catch { throw new CaptureError('INVALID_CAPTURE_STATE'); }
      if (!state || typeof state !== 'object') throw new CaptureError('INVALID_CAPTURE_STATE');
      if (state.unresolvedCommands === undefined) state.unresolvedCommands = [];
      if (!Array.isArray(state.unresolvedCommands)) throw new CaptureError('INVALID_CAPTURE_STATE');
      if (state.version !== 1 || state.lease?.class_session_id !== classroom || state.lease.capture_epoch_id !== epoch
        || classroom !== this.ancestry.classSessionId || !Number.isSafeInteger(state.generation) || state.generation < 1
        || !Number.isSafeInteger(state.nextSeq) || state.nextSeq < 0 || !Number.isSafeInteger(state.nextSample) || state.nextSample < 0
        || !Array.isArray(state.cleanup) || !Array.isArray(state.pending) || state.pending.some(chunk => chunk.descriptor.class_session_id !== classroom
          || chunk.descriptor.capture_epoch_id !== epoch || chunk.descriptor.seq >= state.nextSeq
          || chunk.descriptor.end_sample > state.nextSample || !Number.isSafeInteger(chunk.descriptor.byte_length)
          || chunk.descriptor.byte_length < 2 || chunk.descriptor.byte_length > 32000)) throw new CaptureError('INVALID_CAPTURE_STATE');
      this.state = { ...state, phase: 'recovery' };
      this.recoveryToken = undefined;
      return true;
    });
  }

  start(): Promise<void> {
    return this.serial(async () => {
      const state = this.current();
      if (state.phase !== 'ready' || Date.parse(state.lease.expires_at) <= Date.now()) throw new CaptureError('LEASE_REQUIRED');
      await this.persist({ ...state, phase: 'recording' });
    });
  }

  /** Worklet adapter supplies at most one second of mono 16 kHz PCM16, including the final short tail. */
  append(bytes: Uint8Array<ArrayBuffer>): Promise<void> {
    if (this.queuedBytes + bytes.length > this.maxPendingBytes) return Promise.reject(new CaptureError('CAPACITY_EXCEEDED'));
    const copy = bytes.slice();
    this.queuedBytes += copy.length;
    return this.serial(async () => {
      const state = this.current();
      if (state.phase !== 'recording') throw new CaptureError('CAPTURE_NOT_RECORDING');
      if (!copy.length || copy.length > 32000 || copy.length % 2) throw new CaptureError('INVALID_PCM');
      if (state.pending.reduce((sum, item) => sum + item.descriptor.byte_length, 0) + copy.length > this.maxPendingBytes)
        throw new CaptureError('CAPACITY_EXCEEDED');
      const descriptor = { class_session_id: state.lease.class_session_id, capture_epoch_id: state.lease.capture_epoch_id,
        seq: state.nextSeq, start_sample: state.nextSample, end_sample: state.nextSample + copy.length / 2,
        byte_length: copy.length, sha256: await sha256(copy), encoding: 'pcm_s16le' as const, sample_rate: 16000 as const, channels: 1 as const };
      const chunk = { descriptor, chunkId: crypto.randomUUID(), prepareKey: crypto.randomUUID(), putKey: crypto.randomUUID() };
      // A crash between these writes leaves an explicit missing-local-chunk recovery record.
      await this.persist({ ...state, nextSeq: state.nextSeq + 1, nextSample: descriptor.end_sample, pending: [...state.pending, chunk] });
      await this.cache.putRecording(descriptor, copy, this.ancestry, state.lease.fence_token);
    }).finally(() => { this.queuedBytes -= copy.length; });
  }

  uploadPending(): Promise<void> {
    return this.serial(async () => {
      const initial = this.current();
      await this.cleanup();
      if (['recovery', 'stopped'].includes(initial.phase) && !this.recoveryToken) throw new CaptureError('RECOVERY_AUTHORIZATION_REQUIRED');
      for (const chunk of [...initial.pending]) {
        const bytes = await this.cache.readRecording(chunk.descriptor);
        if (!bytes) throw new CaptureError('LOCAL_CHUNK_MISSING');
        if (bytes.length !== chunk.descriptor.byte_length || await sha256(bytes) !== chunk.descriptor.sha256) throw new CaptureError('LOCAL_CHUNK_CORRUPT');
        const receipt = await this.transport.upload(chunk, bytes, this.current().lease, this.recoveryToken);
        if (!matchesReceipt(chunk.descriptor, receipt)) throw new CaptureError('INVALID_DURABLE_ACK');
        // First persist the authoritative receipt progress, then let the cache remove confirmed bytes.
        await this.persist({ ...this.current(), pending: this.current().pending.filter(item => item.descriptor.seq !== chunk.descriptor.seq),
          cleanup: [...this.current().cleanup, receipt] });
        await this.cleanup();
      }
    });
  }

  private async cleanup(): Promise<void> {
    for (const receipt of [...this.current().cleanup]) {
      await this.cache.acknowledgeRecording(receipt);
      await this.persist({ ...this.current(), cleanup: this.current().cleanup.filter(item => item.receipt_id !== receipt.receipt_id) });
    }
  }

  private async intent(action: 'renew' | 'pause' | 'resume' | 'stop', body: CaptureSchemas['CaptureCommand'] | CaptureSchemas['CaptureStop']) {
    const state = this.current();
    if (state.command) {
      if (state.command.action === action) return state.command;
      if (!['pause', 'stop'].includes(action)) throw new CaptureError('COMMAND_RESULT_UNKNOWN');
      // Safety actions may supersede an uncertain renew, while retaining its exact key and body for reconciliation.
      await this.persist({ ...state, unresolvedCommands: [...state.unresolvedCommands, state.command], command: undefined });
    }
    const command = { action, key: crypto.randomUUID(), body };
    await this.persist({ ...this.current(), command });
    return command;
  }

  private async halt(phase: 'paused' | 'stopped'): Promise<void> {
    this.state = { ...this.current(), phase };
    await this.persist(this.state);
  }

  private commandBody(): CaptureSchemas['CaptureCommand'] {
    const state = this.current();
    return { capture_epoch_id: state.lease.capture_epoch_id, fence_token: state.lease.fence_token,
      last_local_seq: Math.max(0, state.nextSeq - 1), last_sample: state.nextSample, client_monotonic_ms: Math.floor(performance.now()) };
  }

  renew(): Promise<void> {
    return this.serial(async () => {
      const state = this.current();
      if (!['ready', 'recording', 'paused'].includes(state.phase)) throw new CaptureError('LEASE_REQUIRED');
      const intent = await this.intent('renew', this.commandBody());
      const lease = await this.transport.command(state.lease.class_session_id, 'renew', intent.body, intent.key);
      await this.persist({ ...this.current(), lease, command: undefined });
    });
  }

  /** Caller flushes/stops its worklet first, awaiting append promises before pause or stop. */
  pause(): Promise<void> {
    return this.serial(async () => {
      const state = this.current();
      if (!['recording', 'paused'].includes(state.phase)) throw new CaptureError('CAPTURE_NOT_RECORDING');
      await this.halt('paused');
      const intent = await this.intent('pause', this.commandBody());
      // Local input is paused even while offline. Pending chunks keep the server epoch live until they drain.
      await this.persist({ ...this.current(), phase: 'paused' });
      if (this.current().pending.length) return;
      const lease = await this.transport.command(state.lease.class_session_id, 'pause', intent.body, intent.key);
      await this.persist({ ...this.current(), lease, phase: 'paused', command: undefined });
    });
  }

  resume(): Promise<Lease> {
    return this.serial(async () => {
      const state = this.current();
      if (state.phase !== 'paused' || state.pending.length) throw new CaptureError('RECOVERY_REQUIRED');
      if (state.command?.action === 'pause') {
        await this.transport.command(state.lease.class_session_id, 'pause', state.command.body, state.command.key);
        await this.persist({ ...this.current(), command: undefined });
      }
      const intent = await this.intent('resume', this.commandBody());
      const lease = await this.transport.command(state.lease.class_session_id, 'resume', intent.body, intent.key);
      await this.persist({ ...this.current(), phase: 'stopped', command: undefined });
      this.state = null;
      // New epoch needs an explicit recording-key unlock and prepare/start sequence.
      return lease;
    });
  }

  private async manifestHash(): Promise<string> {
    const state = this.current();
    return sha256(new TextEncoder().encode(JSON.stringify({ epoch: state.lease.capture_epoch_id,
      lastSeq: Math.max(0, state.nextSeq - 1), lastSample: state.nextSample })));
  }

  stop(): Promise<CaptureSchemas['AudioManifest']> {
    return this.serial(async () => {
      const state = this.current();
      await this.halt('stopped');
      // A queued local pause with pending bytes has never reached the server and can be superseded by stop.
      if (state.command?.action === 'pause' && state.pending.length) await this.persist({ ...this.current(), command: undefined });
      const intent = await this.intent('stop', { ...this.commandBody(), manifest_sha256: await this.manifestHash(), known_gaps: [] });
      await this.persist({ ...this.current(), phase: 'stopped' });
      const manifest = await this.transport.stop(state.lease.class_session_id, intent.body as CaptureSchemas['CaptureStop'], intent.key);
      await this.persist({ ...this.current(), phase: 'stopped', command: undefined });
      return manifest;
    });
  }

  authorizeRecovery(): Promise<void> {
    return this.serial(async () => {
      const state = this.current();
      if (!['recovery', 'stopped'].includes(state.phase) || state.nextSample === 0) throw new CaptureError('RECOVERY_NOT_REQUIRED');
      if (state.command?.action === 'stop') {
        try {
          await this.transport.stop(state.lease.class_session_id, state.command.body as CaptureSchemas['CaptureStop'], state.command.key);
          await this.persist({ ...this.current(), command: undefined });
        } catch (error) {
          if (!(error instanceof CaptureError) || error.code !== 'STALE_FENCE') throw error;
          // Lease expiry/takeover definitively forbids a new live stop. The same frozen local range can seek recovery.
          await this.persist({ ...this.current(), unresolvedCommands: [...this.current().unresolvedCommands, state.command], command: undefined });
        }
      }
      const grant = await this.transport.recover(state.lease.class_session_id, { capture_epoch_id: state.lease.capture_epoch_id,
        known_last_seq: state.nextSeq - 1, known_last_sample: state.nextSample,
        manifest_sha256: await this.manifestHash(), confirm_retired_epoch: true }, crypto.randomUUID());
      if (grant.asr_authorized !== false || grant.manifest.capture_epoch_id !== state.lease.capture_epoch_id)
        throw new CaptureError('INVALID_RECOVERY_GRANT');
      this.recoveryToken = grant.recovery_upload_token;
    });
  }
}
