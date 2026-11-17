import type { components } from '../generated/openapi';

export type CaptureSchemas = components['schemas'];
export type Descriptor = CaptureSchemas['AudioChunkDescriptor'];
export type Receipt = CaptureSchemas['AudioChunkReceipt'];
export type Lease = CaptureSchemas['CaptureLease'];
export type Ancestry = { semesterId: string | null; courseId: string | null; classSessionId: string | null };

/** PR-08a owns encryption, access checks, quota and invalidation. Capture state stays opaque there. */
export interface RecordingCachePort {
  putRecording(descriptor: Descriptor, bytes: Uint8Array, ancestry: Ancestry, fenceToken: number): Promise<void>;
  readRecording(descriptor: Descriptor): Promise<Uint8Array<ArrayBuffer> | null>;
  acknowledgeRecording(receipt: Receipt): Promise<void>;
  saveCaptureState(classSessionId: string, epochId: string, state: Uint8Array,
    ancestry: Ancestry, fenceToken: number, generation: number): Promise<void>;
  readCaptureState(classSessionId: string, epochId: string): Promise<Uint8Array<ArrayBuffer> | null>;
}

export interface PendingChunk {
  descriptor: Descriptor;
  chunkId: string;
  prepareKey: string;
  putKey: string;
}

export interface CaptureTransport {
  acquire(classroom: string, body: CaptureSchemas['CaptureAcquire'], key: string): Promise<Lease>;
  command(classroom: string, action: 'renew' | 'pause' | 'resume', body: CaptureSchemas['CaptureCommand'], key: string): Promise<Lease>;
  stop(classroom: string, body: CaptureSchemas['CaptureStop'], key: string): Promise<CaptureSchemas['AudioManifest']>;
  recover(classroom: string, body: CaptureSchemas['RecoveryAuthorization'], key: string): Promise<CaptureSchemas['RecoveryAuthorizationResult']>;
  upload(chunk: PendingChunk, bytes: Uint8Array<ArrayBuffer>, lease: Lease, recoveryToken?: string): Promise<Receipt>;
}

export class CaptureError extends Error {
  constructor(readonly code: string, readonly conflictId: string | null = null) { super(code); this.name = 'CaptureError'; }
}

export async function sha256(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('');
}

export function matchesReceipt(expected: Descriptor, receipt: Receipt): boolean {
  return receipt.state === 'durable' && typeof receipt.durable_at === 'string'
    && Number.isFinite(Date.parse(receipt.durable_at)) && typeof receipt.id === 'string' && receipt.id.length > 0
    && typeof receipt.receipt_id === 'string' && receipt.receipt_id.length > 0
    && Object.keys(expected).every(key => expected[key as keyof Descriptor] === receipt.descriptor?.[key as keyof Descriptor]);
}
