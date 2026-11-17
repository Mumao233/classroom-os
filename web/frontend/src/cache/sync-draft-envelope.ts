import type { components } from '../generated/openapi';
import type { CacheWrite } from './types';

type SyncOperation = components['schemas']['SyncOperation'];
type SyncResolution = components['schemas']['ResolveConflict'];
export type WriteEnvelope = { schema: 1; kind: 'write'; operation: SyncOperation; requestHash: string; batchId: string; supersedes?: CacheWrite[] };
export type ResolutionEnvelope = {
  schema: 1; kind: 'resolve'; operation: SyncOperation; requestHash: string;
  conflictId: string; resolution: SyncResolution; original: CacheWrite;
  supersedes?: CacheWrite[];
  recoveryReferences?: CacheWrite[];
};
export type Envelope = WriteEnvelope | ResolutionEnvelope;

const decoder = new TextDecoder('utf-8', { fatal: true });
const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[\da-f]{8}-[\da-f]{4}-[1-8][\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i.test(value);
const positive = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value > 0;

export function isSyncOperation(value: unknown): value is SyncOperation {
  return object(value) && uuid(value.op_id) && uuid(value.object_id) && uuid(value.draft_id)
    && ['personal_revision', 'mark', 'preferences', 'course'].includes(String(value.object_type))
    && positive(value.base_revision) && object(value.payload) && value.payload.base_revision === value.base_revision
    && (value.object_type !== 'personal_revision' || (value.payload.draft_id === value.draft_id
      && (value.payload.base_content_revision_id === null || uuid(value.payload.base_content_revision_id))));
}

/** Ciphertext with this envelope shape is reserved for the sync runtime. It still requires source authorization. */
export function decodeSyncDraftEnvelope(bytes: Uint8Array): Envelope | null {
  let value: unknown;
  try { value = JSON.parse(decoder.decode(bytes)); }
  catch { return null; }
  if (!object(value) || value.schema !== 1 || !isSyncOperation(value.operation)
    || typeof value.requestHash !== 'string' || !/^[a-f0-9]{64}$/.test(value.requestHash)) return null;
  const operation = value.operation;
  if (value.kind === 'write' && uuid(value.batchId)) return value as unknown as WriteEnvelope;
  if (value.kind === 'resolve' && uuid(value.conflictId) && object(value.resolution) && object(value.original)
    && value.original.scope === 'drafts' && value.original.objectId === operation.object_id
    && uuid(value.original.operationId) && positive(value.original.revision) && positive(value.original.generation)) {
    return value as unknown as ResolutionEnvelope;
  }
  return null;
}

export function syncDraftMatchesReference(envelope: Envelope, reference: CacheWrite): boolean {
  const operation = envelope.operation;
  return reference.scope === 'drafts' && operation.op_id === reference.operationId && operation.object_id === reference.objectId
    && operation.base_revision === reference.revision && (envelope.kind !== 'resolve' || envelope.original.generation < reference.generation);
}
