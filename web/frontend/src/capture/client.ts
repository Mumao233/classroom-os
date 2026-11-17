import { CaptureError, sha256 } from './ports';
import type { CaptureSchemas, CaptureTransport, Lease, PendingChunk, Receipt } from './ports';

/** Cookie-only same-origin transport. Recovery capabilities remain in memory. */
export class CaptureClient implements CaptureTransport {
  constructor(private readonly identity: { csrf: string }, private readonly fetcher: typeof fetch = fetch,
    private readonly useWebSocket = true) {}

  private async request<T>(path: string, body: unknown, key: string, options: { token?: string; bytes?: Uint8Array<ArrayBuffer> } = {}): Promise<T> {
    const csrf = this.identity.csrf;
    const headers: Record<string, string> = { 'X-CSRF-Token': csrf, 'Idempotency-Key': key,
      'Content-Type': options.bytes ? 'application/octet-stream' : 'application/json' };
    if (options.token) headers['X-Recovery-Upload-Token'] = options.token;
    if (options.bytes) {
      const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', options.bytes));
      headers['Content-Digest'] = `sha-256=:${btoa(String.fromCharCode(...digest))}:`;
    }
    let response: Response;
    try {
      response = await this.fetcher.call(globalThis, '/api/web/v1' + path, { method: options.bytes ? 'PUT' : 'POST',
        credentials: 'same-origin', cache: 'no-store', redirect: 'error', headers,
        body: options.bytes ?? JSON.stringify(body) });
    } catch { throw new CaptureError('RESULT_UNKNOWN'); }
    if (csrf !== this.identity.csrf) throw new CaptureError('STALE_ACCOUNT');
    let value: unknown;
    try { value = await response.json(); } catch { throw new CaptureError(response.ok ? 'RESULT_UNKNOWN' : 'OUTPUT_INVALID'); }
    if (!response.ok) throw new CaptureError((value as { error?: { code?: string } }).error?.code ?? 'STORAGE_UNAVAILABLE');
    return value as T;
  }

  acquire(classroom: string, body: CaptureSchemas['CaptureAcquire'], key: string): Promise<Lease> {
    return this.request(`/class-sessions/${classroom}/capture/acquire`, body, key);
  }
  command(classroom: string, action: 'renew' | 'pause' | 'resume', body: CaptureSchemas['CaptureCommand'], key: string): Promise<Lease> {
    return this.request(`/class-sessions/${classroom}/capture/${action}`, body, key);
  }
  stop(classroom: string, body: CaptureSchemas['CaptureStop'], key: string): Promise<CaptureSchemas['AudioManifest']> {
    return this.request(`/class-sessions/${classroom}/capture/stop`, body, key);
  }
  recover(classroom: string, body: CaptureSchemas['RecoveryAuthorization'], key: string): Promise<CaptureSchemas['RecoveryAuthorizationResult']> {
    return this.request(`/class-sessions/${classroom}/capture/recovery-authorize`, body, key);
  }

  async upload(chunk: PendingChunk, bytes: Uint8Array<ArrayBuffer>, lease: Lease, recoveryToken?: string): Promise<Receipt> {
    if (this.useWebSocket && !recoveryToken) return this.socketUpload(chunk, bytes, lease);
    const descriptor = chunk.descriptor;
    const receipt = await this.request<Receipt>(`/class-sessions/${descriptor.class_session_id}/audio-chunks`,
      { capture_epoch_id: descriptor.capture_epoch_id, fence_token: lease.fence_token, seq: descriptor.seq,
        start_sample: descriptor.start_sample, end_sample: descriptor.end_sample, byte_length: descriptor.byte_length,
        sha256: descriptor.sha256 }, chunk.prepareKey, { token: recoveryToken });
    // HTTP allocates the stable server ID. Lost responses resolve by the same epoch/sequence/hash tuple.
    return this.request(`/audio-chunks/${receipt.id}/content`, null, chunk.putKey, { bytes });
  }

  private async socketUpload(chunk: PendingChunk, bytes: Uint8Array<ArrayBuffer>, lease: Lease): Promise<Receipt> {
    const csrf = this.identity.csrf;
    const scope = { class_session_id: lease.class_session_id, capture_epoch_id: lease.capture_epoch_id, fence_token: lease.fence_token };
    const ticket = await this.request<CaptureSchemas['WsTicket']>('/ws-tickets', { channel: 'capture', ...scope }, crypto.randomUUID());
    const url = new URL(ticket.path, location.href);
    if (url.origin !== location.origin || url.protocol !== 'https:') throw new CaptureError('FORBIDDEN');
    url.protocol = 'wss:';
    const socket = new WebSocket(url, ticket.subprotocol);
    type Event = { type: string; data: Record<string, unknown>; cursor?: string };
    const waiting: { resolve: (event: Event) => void; reject: (error: Error) => void }[] = [];
    const queued: Event[] = [];
    let failure: Error | undefined;
    let connectionSeq = 0;
    const fail = () => {
      failure = new CaptureError('RESULT_UNKNOWN');
      for (const waiter of waiting.splice(0)) waiter.reject(failure);
    };
    socket.addEventListener('close', fail);
    socket.addEventListener('error', fail);
    socket.addEventListener('message', message => {
      try {
        const event = JSON.parse(String(message.data)) as Event;
        if (typeof event.type !== 'string' || typeof event.data !== 'object' || !event.data) throw new Error();
        const waiter = waiting.shift();
        if (waiter) waiter.resolve(event); else queued.push(event);
      } catch { socket.close(); fail(); }
    });
    const next = (): Promise<Event> => {
      if (failure) return Promise.reject(failure);
      const event = queued.shift();
      if (event) return Promise.resolve(event);
      return new Promise((resolve, reject) => waiting.push({ resolve, reject }));
    };
    const send = (type: string, data: unknown) => socket.send(JSON.stringify({ protocol_version: 1, type,
      sent_at: new Date().toISOString(), connection_seq: connectionSeq++, data }));
    const timeout = setTimeout(() => { socket.close(); fail(); }, 15000);
    try {
      await new Promise<void>((resolve, reject) => {
        socket.addEventListener('open', () => resolve(), { once: true });
        socket.addEventListener('error', () => reject(new CaptureError('STORAGE_UNAVAILABLE')), { once: true });
        socket.addEventListener('close', () => reject(new CaptureError('STORAGE_UNAVAILABLE')), { once: true });
      });
      socket.send(JSON.stringify({ type: 'authenticate', ticket: ticket.ticket }));
      const ready = await next();
      if (ready.type !== 'capture.ready' || ready.data.capture_epoch_id !== lease.capture_epoch_id
        || ready.data.fence_token !== lease.fence_token) throw new CaptureError('INVALID_CAPTURE_READY');
      send('heartbeat', { last_cursor: null });
      const descriptor = chunk.descriptor;
      send('capture.chunk_start', { ...scope, chunk_id: chunk.chunkId, op_id: chunk.prepareKey, seq: descriptor.seq,
        start_sample: descriptor.start_sample, end_sample: descriptor.end_sample, byte_length: bytes.length, sha256: descriptor.sha256 });
      for (let offset = 0; offset < bytes.length; offset += 3200) {
        const frame = bytes.slice(offset, offset + 3200);
        send('capture.frame', { ...scope, chunk_id: chunk.chunkId, frame_seq: offset / 3200,
          start_sample: descriptor.start_sample + offset / 2, sample_count: frame.length / 2,
          byte_length: frame.length, sha256: await sha256(frame), chunk_final_frame: offset + frame.length === bytes.length });
        socket.send(frame);
        const received = await next();
        if (received.type !== 'capture.frame_received' || received.data.chunk_id !== chunk.chunkId
          || received.data.frame_seq !== offset / 3200 || received.data.durable !== false) throw new CaptureError('INVALID_FRAME_ACK');
      }
      const durable = await next();
      if (csrf !== this.identity.csrf) throw new CaptureError('STALE_ACCOUNT');
      if (durable.type !== 'audio.chunk_durable' || !durable.cursor || durable.data.chunk_id !== chunk.chunkId
        || durable.data.class_session_id !== descriptor.class_session_id || durable.data.capture_epoch_id !== descriptor.capture_epoch_id
        || durable.data.seq !== descriptor.seq || durable.data.sha256 !== descriptor.sha256 || durable.data.end_sample !== descriptor.end_sample
        || typeof durable.data.receipt_id !== 'string' || typeof durable.data.durable_at !== 'string') throw new CaptureError('INVALID_DURABLE_ACK');
      return { id: chunk.chunkId, descriptor, state: 'durable', durable_at: durable.data.durable_at,
        receipt_id: durable.data.receipt_id, asr_submission: 'not_submitted' };
    } finally { clearTimeout(timeout); socket.close(); }
  }
}
