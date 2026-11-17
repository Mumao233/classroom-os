import type { components } from '../generated/openapi';

export type CacheScope = components['schemas']['CacheUnlockRequest']['scope'];
export type CacheGrant = components['schemas']['CacheUnlockResult'];
export type AudioDescriptor = components['schemas']['AudioChunkDescriptor'];
export type AudioReceipt = components['schemas']['AudioChunkReceipt'];

export type CacheIdentity = { ownerId: string; deviceId: string; sessionId: string };
export type CacheAncestry = { semesterId: string | null; courseId: string | null; classSessionId: string | null };
export type CacheReference = {
  scope: CacheScope; objectId: string; revision: number; generation: number;
  /** PR-08b binds a learning object's domain type into its authenticated header. */
  objectType?: string;
  /** Exact review body authority; separate from the document projection's CAS revision. */
  contentRevisionId?: string;
};
export type CacheWrite = CacheReference & {
  ancestry: CacheAncestry;
  /** Required for drafts. Stable across transport retries of this generation. */
  operationId?: string;
  /** Learning TTL starts at an actual successful cloud-version verification. */
  verifiedAt?: number;
};
export type CacheLimits = { draftBytes: number; draftOperations: number; recordingBytes: number };
export type CacheOptions = {
  enabled?: boolean;
  recordingEnabled?: boolean;
  /** Separate limits require explicit reviewed policy when enabling the two unique-data pools. */
  limits?: CacheLimits;
  databaseName?: string;
};
export type DeletionNotice = {
  object_type: 'account' | 'semester' | 'course' | 'class_session' | 'audio' | 'media_asset' | string;
  object_id: string;
  deletion_id: string;
  state: 'access_revoked' | 'pending_delete' | 'delete_failed' | 'deleted';
};
export type CacheFailure = 'DISABLED' | 'LOCKED' | 'STALE_OPERATION' | 'INVALID_INPUT' | 'INVALID_GRANT'
  | 'DATABASE_BLOCKED' | 'STORAGE_UNAVAILABLE' | 'QUOTA_EXCEEDED' | 'REVISION_CONFLICT'
  | 'CORRUPT_RECORD' | 'GONE' | 'KEY_REQUIRED' | 'UNSUPPORTED_SCHEMA';
export class CacheError extends Error {
  constructor(readonly code: CacheFailure) { super(code); this.name = 'CacheError'; }
}

export type CacheHeader = CacheWrite & {
  ownerId: string;
  deviceId: string;
  keyId: string;
  keyVersion: number;
  schema: 1 | 2;
};
export type CacheMetadata = {
  id: string;
  namespace: string;
  header: CacheHeader;
  bytes: number;
  accessedAt: number;
};
export type CipherRecord = CacheMetadata & {
  nonce: Uint8Array<ArrayBuffer>;
  ciphertext: ArrayBuffer;
};
export type NamespaceState = { id: string; generation: number };
export type Tombstone = { id: string; namespace: string; kind: string; objectId: string };

export const LEARNING_LIMITS = Object.freeze({ ageMs: 14 * 24 * 60 * 60 * 1000, classes: 20, bytes: 50_000_000 });
export const namespaceOf = (identity: CacheIdentity) => JSON.stringify([identity.ownerId, identity.deviceId]);
export const recordId = (namespace: string, reference: CacheReference) => JSON.stringify([
  namespace, reference.scope, reference.objectId, reference.revision, reference.generation,
]);
