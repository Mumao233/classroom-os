import type { components } from '../generated/openapi';
import type { CacheAncestry, CacheReference, CacheWrite, DeletionNotice } from '../cache/types';
import { syncFingerprint } from './client';
import type { SyncConflict, SyncOperation, SyncReceipt, SyncResolution, SyncTransport } from './client';

type S = components['schemas'];
type Projection = S['BootstrapProjection'];
export type SyncState = 'local_only' | 'syncing' | 'synced' | 'conflict' | 'failed' | 'restricted';
export type PendingDraft = { reference: CacheWrite; state: SyncState; conflictId: string | null; error: string | null };
export type LearningReference = CacheReference & { scope: 'learning'; objectType: Projection['object_type']; ancestry: CacheAncestry };
export type ConflictDraft = { reference: CacheWrite; body: string; note: string };
export type ConflictDraftSource = { source: CacheWrite | null; reference?: CacheWrite; state: 'available' | 'restricted' };
type EditorEnvelope = { schema: 1; kind: 'editor_recovery'; source: CacheWrite; conflictId: string; sourceHash: string; body: string; note: string };
type WriteEnvelope = { schema: 1; kind: 'write'; operation: SyncOperation; requestHash: string; batchId: string; supersedes?: CacheWrite[] };
type ResolutionEnvelope = {
  schema: 1; kind: 'resolve'; operation: SyncOperation; requestHash: string;
  conflictId: string; resolution: SyncResolution; original: CacheWrite;
  supersedes?: CacheWrite[];
  recoveryReferences?: CacheWrite[];
};
type Envelope = WriteEnvelope | ResolutionEnvelope;
type ReceiptBinding = {
  operation: 'syncOperation' | 'resolveConflict'; operationId: string; requestHash: string;
  objectId: string; allowsNewObject: boolean;
};

/** Storage remains behind PR-08a's encryption, namespace, deadline and tombstone checks. */
export interface SyncCache extends EventTarget {
  lock(): void;
  close(): void;
  unlockScope(scope: 'drafts' | 'learning', password: string, mfaCode?: string): Promise<void>;
  reconcileLearning(projections: Projection[], options?: { preserveKeys?: boolean }): Promise<void>;
  pendingDraftReferences(): Promise<CacheWrite[]>;
  put(write: CacheWrite, plaintext: Uint8Array): Promise<void>;
  saveRecoveryDraft(write: CacheWrite, plaintext: Uint8Array): Promise<void>;
  retryRecoveryDrafts(): Promise<Array<{ objectId: string; error?: unknown }>>;
  read(reference: CacheReference): Promise<Uint8Array | null>;
  acknowledgeDraft(reference: CacheReference, operationId: string): Promise<void>;
  applyDeletion(notice: DeletionNotice, options: { retainDrafts: true }): Promise<void>;
}

export class SyncError extends Error {
  constructor(readonly code: string) { super(code); this.name = 'SyncError'; }
}

const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });
const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[\da-f]{8}-[\da-f]{4}-[1-8][\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i.test(value);
const positive = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0;
const errorCode = (error: unknown) => object(error) && typeof error.code === 'string' ? error.code : 'SYNC_FAILED';
const keyOf = (reference: CacheReference) => JSON.stringify([reference.objectId, reference.revision, reference.generation]);
const projectionType = (operation: SyncOperation) => operation.object_type === 'personal_revision' ? 'review_document' : operation.object_type;
const resolvesDraft = (reference: CacheWrite, original: CacheWrite) => reference.objectId === original.objectId
  && reference.resolutionOf !== undefined && reference.resolutionOf.operationId === original.operationId
  && reference.resolutionOf.revision === original.revision && reference.resolutionOf.generation === original.generation;

function validateOperation(value: unknown): asserts value is SyncOperation {
  if (!object(value) || !uuid(value.op_id) || !uuid(value.object_id) || !uuid(value.draft_id)
    || !['personal_revision', 'mark', 'preferences', 'course'].includes(String(value.object_type))
    || !positive(value.base_revision) || !object(value.payload) || value.payload.base_revision !== value.base_revision
    || (value.object_type === 'personal_revision' && (value.payload.draft_id !== value.draft_id
      || (value.payload.base_content_revision_id !== null && !uuid(value.payload.base_content_revision_id))))) throw new SyncError('INVALID_SYNC_OPERATION');
}

/** One runtime per authenticated cache. Durable operations remain immutable across retries and reloads. */
export class OfflineSyncRuntime extends EventTarget {
  private unlocked = false;
  private scopes = new Set<'drafts' | 'learning'>();
  private learningEpoch = 0;
  private epoch = 0;
  private closing = false;
  private refreshInProgress = false;
  private flushing: Promise<void> | null = null;
  private polling: Promise<void> | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private cursor: string | null = null;
  private identity: string | null | undefined;
  private projections = new Map<string, Projection>();
  private authority = new Map<string, Projection['object_type']>();
  /** Successful exact reads only; no body is retained here, and every lock clears these grants. */
  private exactAuthority = new Set<string>();
  private protectedDocuments = new Set<string>();
  private metadataDirty = false;
  private rows = new Map<string, PendingDraft>();
  private bindings = new Map<string, ReceiptBinding>();
  private editorSaves = new Set<Promise<void>>();
  private editorErrors = new Map<string, unknown>();
  private editorGenerations = new Map<string, number>();
  private readonly onLocked = () => {
    this.epoch++;
    this.unlocked = false;
    this.scopes.clear();
    this.learningEpoch++;
    this.exactAuthority.clear();
    this.protectedDocuments.clear();
    this.rows.clear();
    this.bindings.clear();
    this.dispatchEvent(new Event('locked'));
    this.changed();
  };
  private readonly onOnline = () => { this.cache.lock(); };

  constructor(
    private readonly cache: SyncCache,
    private readonly transport: SyncTransport,
    private readonly options: { enabled?: boolean; newId?: () => string; pollIntervalMs?: number; identity?: () => string | null } = {},
  ) {
    super();
    cache.addEventListener('locked', this.onLocked);
    if (options.enabled && typeof window !== 'undefined') {
      window.addEventListener('online', this.onOnline);
      this.timer = setInterval(() => { void this.pollChanges(); }, options.pollIntervalMs ?? 5000);
    }
  }

  snapshot(): { unlocked: boolean; unlockedScopes: Array<'drafts' | 'learning'>; cursor: string | null; pending: PendingDraft[] } {
    return { unlocked: this.unlocked, unlockedScopes: [...this.scopes], cursor: this.cursor, pending: structuredClone([...this.rows.values()]) };
  }

  private changed() { this.dispatchEvent(new Event('change')); }
  private enabled() { if (!this.options.enabled || this.closing) throw new SyncError('DISABLED'); }
  private sameEpoch(epoch: number) {
    if (epoch !== this.epoch || this.closing || (this.options.identity && this.options.identity() !== this.identity)) throw new SyncError('LOCKED');
  }
  private current(epoch: number) { this.sameEpoch(epoch); if (!this.unlocked) throw new SyncError('LOCKED'); }
  private scoped(epoch: number) { this.sameEpoch(epoch); if (!this.scopes.size) throw new SyncError('LOCKED'); }
  private newId() { return this.options.newId?.() ?? crypto.randomUUID(); }

  /** The caller has established its authenticated session before calling this entry point. */
  async unlock(password: string, mfaCode?: string): Promise<void> {
    this.enabled();
    if (this.refreshInProgress) throw new SyncError('SYNC_BUSY');
    this.refreshInProgress = true;
    this.identity = this.options.identity?.();
    this.cache.lock();
    try {
      if (this.flushing) await this.flushing.catch(() => undefined);
      if (this.polling) await this.polling;
      try { await this.refreshAuthority(); }
      catch (error) { if (errorCode(error) !== 'CURSOR_EXPIRED') throw error; await this.refreshAuthority(); }
      const epoch = this.epoch;
      await this.cache.unlockScope('drafts', password, mfaCode);
      this.sameEpoch(epoch);
      this.unlocked = true;
      this.scopes.add('drafts');
      await this.restore();
    } catch (error) { this.cache.lock(); throw error; }
    finally { this.refreshInProgress = false; }
  }

  /** Adds the learning scope after fresh online authority checks. Existing draft keys keep their original deadline. */
  async unlockLearning(password: string, mfaCode?: string): Promise<void> {
    this.enabled();
    if (this.refreshInProgress) throw new SyncError('SYNC_BUSY');
    this.refreshInProgress = true;
    if (!this.scopes.size) this.identity = this.options.identity?.();
    try {
      if (this.flushing) await this.flushing;
      if (this.polling) await this.polling;
      await this.refreshAuthority(true);
      const epoch = this.epoch;
      await this.cache.unlockScope('learning', password, mfaCode);
      this.sameEpoch(epoch);
      this.scopes.add('learning');
      this.changed();
    } catch (error) { this.cache.lock(); throw error; }
    finally { this.refreshInProgress = false; }
  }

  private async refreshAuthority(preserveKeys = false): Promise<void> {
    let epoch = this.epoch;
    const start = this.cursor;
    const bootstrap = await this.transport.bootstrap();
    this.sameEpoch(epoch);
    this.installBootstrap(bootstrap);
    await this.consumeChanges(start ?? bootstrap.watermark);
    const reconcile = this.cache.reconcileLearning([...this.projections.values()], { preserveKeys });
    epoch = this.epoch;
    await reconcile;
    this.sameEpoch(epoch);
  }

  private installBootstrap(bootstrap: S['SyncBootstrap']) {
    const projections = this.parseBootstrap(bootstrap);
    // A complete snapshot can establish revocation even when its event cursor has expired.
    // Keep this authority baseline separate from projections invalidated by ordinary changes.
    const lostAuthority = [...this.authority].some(([id, kind]) => projections.get(id)?.object_type !== kind);
    this.projections = projections;
    this.authority = new Map([...projections].map(([id, value]) => [id, value.object_type]));
    this.metadataDirty = false;
    if (lostAuthority && this.scopes.size) this.cache.lock();
  }

  private parseBootstrap(bootstrap: S['SyncBootstrap']): Map<string, Projection> {
    if (!bootstrap || bootstrap.projection_complete !== true || !Array.isArray(bootstrap.projections)
      || !positive(bootstrap.maximum_projection_count) || bootstrap.projections.length > bootstrap.maximum_projection_count
      || typeof bootstrap.watermark !== 'string' || !bootstrap.watermark) throw new SyncError('INCOMPATIBLE_CLIENT');
    const projections = new Map<string, Projection>();
    for (const projection of bootstrap.projections) {
      if (!uuid(projection.object_id) || !positive(projection.revision)
        || !['semester', 'course', 'class_session', 'review_document', 'mark', 'preferences'].includes(projection.object_type)
        || (projection.parent_id !== null && !uuid(projection.parent_id))
        || (projection.adopted_revision_id !== null && !uuid(projection.adopted_revision_id))
        || !Number.isFinite(Date.parse(projection.updated_at)) || projections.has(projection.object_id)) throw new SyncError('INCOMPATIBLE_CLIENT');
      projections.set(projection.object_id, structuredClone(projection));
    }
    return projections;
  }

  private async consumeChanges(start: string): Promise<void> {
    let epoch = this.epoch;
    let cursor = start;
    // Keep the bootstrap watermark until the pending changes can be applied.
    this.cursor ??= start;
    for (let pageNumber = 0; pageNumber < 100; pageNumber++) {
      let page: S['Changes'];
      try { page = await this.transport.changes(cursor); }
      catch (error) {
        this.sameEpoch(epoch);
        if (errorCode(error) === 'CURSOR_EXPIRED') this.cursor = null;
        throw error;
      }
      this.sameEpoch(epoch);
      if (!page || !Array.isArray(page.items) || typeof page.next_cursor !== 'string' || !page.next_cursor
        || typeof page.has_more !== 'boolean' || (page.has_more && page.next_cursor === cursor)) throw new SyncError('INCOMPATIBLE_CLIENT');
      if (this.scopes.size && page.items.some(change => change?.object_type === 'deletion')) {
        // The event announces revocation before its scope can be fetched.
        this.cache.lock();
        epoch = this.epoch;
      }
      for (const change of page.items) {
        if (!uuid(change.event_id) || !uuid(change.object_id) || !positive(change.revision) || typeof change.deleted !== 'boolean'
          || !['semester', 'course', 'class_session', 'transcript', 'slide', 'question', 'review_document', 'mark', 'preferences', 'task', 'deletion', 'notification'].includes(change.object_type)) throw new SyncError('INCOMPATIBLE_CLIENT');
        if (change.object_type === 'deletion') {
          const deletion = await this.transport.deletion(change.object_id);
          this.sameEpoch(epoch);
          if (deletion.id !== change.object_id || !uuid(deletion.scope_id)
            || !['account', 'semester', 'course', 'class_session', 'audio', 'media_asset'].includes(deletion.scope_type)
            || !['access_revoked', 'pending_delete', 'delete_failed', 'deleted'].includes(deletion.state)) throw new SyncError('INCOMPATIBLE_CLIENT');
          const applying = this.cache.applyDeletion({ object_type: deletion.scope_type, object_id: deletion.scope_id, deletion_id: deletion.id, state: deletion.state }, { retainDrafts: true });
          epoch = this.epoch;
          await applying;
          this.sameEpoch(epoch);
          this.removeProjection(deletion.scope_type, deletion.scope_id);
        } else if (change.deleted) {
          const applying = this.cache.applyDeletion({ object_type: change.object_type, object_id: change.object_id, deletion_id: change.event_id, state: 'access_revoked' }, { retainDrafts: true });
          epoch = this.epoch;
          await applying;
          this.sameEpoch(epoch);
          this.removeProjection(change.object_type, change.object_id);
        }
        else {
          // A retained document projection does not authorize every historical source-backed body.
          // Revoke delivered and in-flight plaintext before reauthorizing exact revisions on unlock.
          if (change.object_type === 'review_document' && this.scopes.size && this.protectedDocuments.has(change.object_id)) { this.cache.lock(); epoch = this.epoch; }
          // The old metadata cannot authorize a newly changed body or moved object. Hydrate on a new snapshot.
          const previous = this.projections.get(change.object_id);
          if (['semester', 'course', 'class_session', 'review_document', 'mark', 'preferences'].includes(change.object_type)) {
            if (!previous || previous.revision < change.revision) this.projections.delete(change.object_id);
            this.metadataDirty = true;
            this.learningEpoch++;
            const invalidating = this.cache.reconcileLearning([...this.projections.values()].filter(value => value.object_id !== change.object_id), { preserveKeys: true });
            epoch = this.epoch;
            await invalidating;
            this.sameEpoch(epoch);
          }
        }
      }
      cursor = page.next_cursor;
      if (!page.has_more) { this.cursor = cursor; return; }
    }
    throw new SyncError('CAPACITY_EXCEEDED');
  }

  private removeProjection(kind: string, id: string) {
    if (kind === 'account') { this.projections.clear(); return; }
    if (kind === 'audio' || kind === 'media_asset') return;
    const removed = new Set([id]);
    for (let pass = 0; pass < 4; pass++) {
      for (const [objectId, projection] of this.projections) {
        if (removed.has(objectId) || (projection.parent_id && removed.has(projection.parent_id))) {
          removed.add(objectId); this.projections.delete(objectId);
        }
      }
    }
  }

  private permitted(reference: CacheWrite, operation?: SyncOperation): boolean {
    const projection = this.projections.get(reference.objectId);
    const ancestry = reference.ancestry;
    return !!projection && (!operation || projection.object_type === projectionType(operation))
      && (!ancestry.semesterId || this.projections.get(ancestry.semesterId)?.object_type === 'semester')
      && (!ancestry.courseId || this.projections.get(ancestry.courseId)?.object_type === 'course')
      && (!ancestry.classSessionId || this.projections.get(ancestry.classSessionId)?.object_type === 'class_session');
  }

  private row(reference: CacheWrite, state: SyncState, error: string | null = null, conflictId: string | null = null) {
    this.rows.set(keyOf(reference), { reference: structuredClone(reference), state, error, conflictId });
    this.changed();
  }

  private async restore() {
    const epoch = this.epoch;
    const references = await this.cache.pendingDraftReferences();
    this.current(epoch);
    for (const reference of references) {
      if (reference.objectType === 'sync_editor_recovery') continue;
      if (!this.permitted(reference)) { this.row(reference, 'restricted', 'AUTHORITY_UNAVAILABLE'); continue; }
      try { await this.load(reference); this.current(epoch); this.row(reference, 'local_only'); }
      catch (error) { this.current(epoch); this.row(reference, ['GONE', 'AUTHORITY_UNAVAILABLE'].includes(errorCode(error)) ? 'restricted' : 'failed', errorCode(error)); }
    }
  }

  private async load(reference: CacheWrite): Promise<Envelope> {
    const epoch = this.epoch;
    if (!this.permitted(reference)) throw new SyncError('AUTHORITY_UNAVAILABLE');
    const bytes = await this.cache.read(reference);
    if (!bytes) throw new SyncError('DRAFT_UNAVAILABLE');
    let envelope: Envelope;
    try { envelope = JSON.parse(decoder.decode(bytes)) as Envelope; }
    catch { throw new SyncError('UNSUPPORTED_DRAFT'); }
    finally { bytes.fill(0); }
    if (!envelope || envelope.schema !== 1 || !['write', 'resolve'].includes(envelope.kind)) throw new SyncError('UNSUPPORTED_DRAFT');
    validateOperation(envelope.operation);
    if (envelope.operation.object_id !== reference.objectId || envelope.operation.op_id !== reference.operationId
      || envelope.operation.base_revision !== reference.revision || !this.permitted(reference, envelope.operation)) throw new SyncError('CORRUPT_DRAFT');
    if (reference.resolutionOf && (envelope.kind !== 'resolve' || !envelope.original
      || !resolvesDraft(reference, envelope.original))) throw new SyncError('CORRUPT_DRAFT');
    const value = envelope.kind === 'write' ? envelope.operation : { conflict_id: envelope.conflictId, body: envelope.resolution };
    if (await syncFingerprint(value) !== envelope.requestHash) throw new SyncError('CORRUPT_DRAFT');
    this.current(epoch);
    await this.authorizeOperation(envelope.operation, epoch);
    if (envelope.kind === 'resolve') {
      if (!this.exactAuthority.has(JSON.stringify(['conflict', reference.objectId, envelope.conflictId]))) await this.fetchConflict(envelope.conflictId, reference.objectId, epoch);
      if (envelope.resolution.merged_operation) await this.authorizeOperation(envelope.resolution.merged_operation, epoch);
    }
    this.current(epoch);
    this.bindings.set(keyOf(reference), this.receiptBinding(envelope));
    return envelope;
  }

  private authorizationFailure(error: unknown, key: string): never {
    if (['NOT_FOUND', 'FORBIDDEN', 'GONE'].includes(errorCode(error))) {
      if (this.exactAuthority.has(key)) this.cache.lock();
      throw new SyncError('AUTHORITY_UNAVAILABLE');
    }
    throw error;
  }

  /** Online exact hydration also primes a bounded, memory-only authority grant for offline edits. */
  async readReviewRevision(documentId: string, revisionId: string): Promise<S['ReviewRevision']> {
    this.enabled();
    const epoch = this.epoch;
    this.scoped(epoch);
    if (!uuid(revisionId) || this.projections.get(documentId)?.object_type !== 'review_document') throw new SyncError('AUTHORITY_UNAVAILABLE');
    const key = JSON.stringify(['revision', documentId, revisionId]);
    this.protectedDocuments.add(documentId);
    let revision: S['ReviewRevision'];
    try { revision = await this.transport.reviewRevision(revisionId); }
    catch (error) { this.scoped(epoch); this.authorizationFailure(error, key); }
    this.scoped(epoch);
    if (revision.id !== revisionId || revision.document_id !== documentId || !positive(revision.revision)) throw new SyncError('INCOMPATIBLE_CLIENT');
    this.exactAuthority.add(key);
    return structuredClone(revision);
  }

  private async authorizeRevision(documentId: string, revisionId: string, epoch: number) {
    this.scoped(epoch);
    if (!this.exactAuthority.has(JSON.stringify(['revision', documentId, revisionId]))) await this.readReviewRevision(documentId, revisionId);
    this.scoped(epoch);
  }

  private async authorizeOperation(operation: SyncOperation, epoch: number) {
    if (operation.object_type !== 'personal_revision') return;
    const revisionId = operation.payload.base_content_revision_id;
    if (revisionId !== null) { await this.authorizeRevision(operation.object_id, revisionId, epoch); return; }
    const key = JSON.stringify(['note', operation.object_id]);
    if (!this.exactAuthority.has(key)) {
      let document: S['ReviewDocument'];
      try { document = await this.transport.document(operation.object_id); }
      catch (error) { this.scoped(epoch); this.authorizationFailure(error, key); }
      this.scoped(epoch);
      if (document.id !== operation.object_id || document.kind !== 'note') throw new SyncError('AUTHORITY_UNAVAILABLE');
      this.exactAuthority.add(key);
    }
    this.scoped(epoch);
  }

  private async fetchConflict(id: string, documentId: string, epoch: number, original?: WriteEnvelope): Promise<SyncConflict> {
    this.current(epoch);
    const key = JSON.stringify(['conflict', documentId, id]);
    this.protectedDocuments.add(documentId);
    let conflict: SyncConflict;
    try { conflict = await this.transport.conflict(id); }
    catch (error) {
      this.current(epoch);
      if (!original || !object(error) || error.status !== 409 || error.code !== 'INCOMPATIBLE_CLIENT') {
        this.authorizationFailure(error, key);
      }
      // The original receipt can predate the authorized comparison. Replay its exact write once.
      const receipt = await this.pushWrite(original);
      this.current(epoch);
      this.validateReceipt(receipt, this.receiptBinding(original));
      if (receipt.status !== 'conflict' || receipt.conflict_id !== id || receipt.object_id !== documentId
        || !positive(receipt.revision)) {
        throw new SyncError('INVALID_RECEIPT');
      }
      const recovered = await this.fetchConflict(id, documentId, epoch);
      this.current(epoch);
      if (recovered.server_revision !== receipt.revision) throw new SyncError('INCOMPATIBLE_CLIENT');
      return recovered;
    }
    this.current(epoch);
    if (conflict.id !== id || conflict.object_id !== documentId) throw new SyncError('INCOMPATIBLE_CLIENT');
    this.exactAuthority.add(key);
    return conflict;
  }

  /** A persistence rejection leaves the caller responsible for keeping the current editor input. */
  async enqueue(operation: SyncOperation, ancestry: CacheAncestry, generation: number): Promise<CacheWrite> {
    this.enabled();
    const epoch = this.epoch;
    this.current(epoch);
    const frozen = structuredClone(operation);
    validateOperation(frozen);
    if (!positive(generation)) throw new SyncError('INVALID_SYNC_OPERATION');
    const reference: CacheWrite = { scope: 'drafts', objectId: frozen.object_id, revision: frozen.base_revision, generation,
      ancestry: structuredClone(ancestry), operationId: frozen.op_id };
    if (!this.permitted(reference, frozen)) throw new SyncError('AUTHORITY_UNAVAILABLE');
    const envelope: WriteEnvelope = { schema: 1, kind: 'write', operation: frozen, requestHash: await syncFingerprint(frozen), batchId: frozen.op_id };
    this.current(epoch);
    await this.authorizeOperation(frozen, epoch);
    this.current(epoch);
    await this.persist(reference, envelope);
    this.current(epoch);
    this.row(reference, 'local_only');
    return structuredClone(reference);
  }

  private async persist(reference: CacheWrite, envelope: Envelope) {
    const epoch = this.epoch;
    const bytes = encoder.encode(JSON.stringify(envelope));
    try {
      await this.cache.put(reference, bytes);
      this.current(epoch);
      this.bindings.set(keyOf(reference), this.receiptBinding(envelope));
    }
    finally { bytes.fill(0); }
  }

  async readDraft(reference: CacheWrite): Promise<SyncOperation> {
    const epoch = this.epoch;
    this.current(epoch);
    const envelope = await this.load(structuredClone(reference));
    this.current(epoch);
    return structuredClone(envelope.operation);
  }

  /** Accept input only while authorized; the cache immediately starts write-only encryption. */
  saveConflictDraft(source: CacheWrite, body: string, note: string, editorId: string, generation: number): Promise<CacheWrite> {
    try { return this.startConflictDraft(source, body, note, editorId, generation); }
    catch (error) { this.editorErrors.set(editorId, error); throw error; }
  }

  private startConflictDraft(source: CacheWrite, body: string, note: string, editorId: string, generation: number): Promise<CacheWrite> {
    this.current(this.epoch);
    const original = structuredClone(source);
    const row = this.rows.get(keyOf(original));
    const binding = this.bindings.get(keyOf(original));
    if (!uuid(editorId) || !positive(generation) || generation <= (this.editorGenerations.get(editorId) ?? 0)
      || !row?.conflictId || !binding || typeof body !== 'string' || typeof note !== 'string'
      || body.length > 120000 || note.length > 12000
      || !this.exactAuthority.has(JSON.stringify(['conflict', original.objectId, row.conflictId]))) throw new SyncError('INVALID_RESOLUTION');
    this.editorGenerations.set(editorId, generation);
    const reference: CacheWrite = { scope: 'drafts', objectType: 'sync_editor_recovery', objectId: editorId, revision: original.revision,
      generation, operationId: this.newId(), ancestry: structuredClone(original.ancestry), recoveryFor: original.operationId,
      recoveryConflictId: row.conflictId, recoverySourceObjectId: original.objectId };
    const bytes = encoder.encode(JSON.stringify({ schema: 1, kind: 'editor_recovery', source: original, conflictId: row.conflictId,
      sourceHash: binding.requestHash, body, note } satisfies EditorEnvelope));
    let writing: Promise<void>;
    try { writing = this.cache.saveRecoveryDraft(reference, bytes); }
    catch (error) { this.editorErrors.set(editorId, error); throw error; }
    finally { bytes.fill(0); }
    const tracked = writing.then(() => { if (this.editorGenerations.get(editorId) === generation) this.editorErrors.delete(editorId); }, error => {
      if (this.editorGenerations.get(editorId) === generation) this.editorErrors.set(editorId, error);
      throw error;
    });
    this.editorSaves.add(tracked);
    void tracked.finally(() => this.editorSaves.delete(tracked)).catch(() => undefined);
    // The result contains routing metadata only; ciphertext completion never reopens the view.
    return tracked.then(() => structuredClone(reference));
  }

  async awaitEditorDraftSaves(): Promise<void> {
    await Promise.all([...this.editorSaves]);
    if (this.editorErrors.size) throw this.editorErrors.values().next().value;
  }

  async retryEditorDraftSaves(): Promise<void> {
    await Promise.allSettled([...this.editorSaves]);
    for (const result of await this.cache.retryRecoveryDrafts()) {
      if (result.error) this.editorErrors.set(result.objectId, result.error);
      else this.editorErrors.delete(result.objectId);
    }
    if (this.editorErrors.size) throw this.editorErrors.values().next().value;
  }

  async readConflictDrafts(source: CacheWrite): Promise<ConflictDraft[]> {
    const epoch = this.epoch;
    this.current(epoch);
    const original = structuredClone(source);
    if (!this.permitted(original)) throw new SyncError('AUTHORITY_UNAVAILABLE');
    const references = await this.cache.pendingDraftReferences();
    this.current(epoch);
    const drafts: ConflictDraft[] = [];
    for (const reference of references.filter(value => value.objectType === 'sync_editor_recovery' && value.recoveryFor === original.operationId)) {
      const saved = await this.readEditorEnvelope(reference, epoch);
      if (!saved) continue;
      if (keyOf(saved.source) !== keyOf(original)
        || saved.source.operationId !== original.operationId || reference.recoveryConflictId !== saved.conflictId
        || typeof saved.body !== 'string' || typeof saved.note !== 'string') throw new SyncError('CORRUPT_DRAFT');
      const conflict = await this.fetchConflict(saved.conflictId, original.objectId, epoch);
      await this.authorizeOperation(conflict.local_operation, epoch);
      if (await syncFingerprint(conflict.local_operation) !== saved.sourceHash) throw new SyncError('CORRUPT_DRAFT');
      this.current(epoch);
      drafts.push({ reference: structuredClone(reference), body: saved.body, note: saved.note });
    }
    this.current(epoch);
    return drafts;
  }

  /** Find retained editor branches after reload without relying on an in-memory original outbox ref. */
  async listConflictDraftSources(): Promise<ConflictDraftSource[]> {
    const epoch = this.epoch;
    this.current(epoch);
    const references = await this.cache.pendingDraftReferences();
    this.current(epoch);
    const sources = new Map<string, ConflictDraftSource>();
    for (const reference of references.filter(value => value.objectType === 'sync_editor_recovery')) {
      let saved: EditorEnvelope | null;
      try { saved = await this.readEditorEnvelope(reference, epoch); }
      catch (error) {
        this.current(epoch);
        if (!['GONE', 'KEY_REQUIRED'].includes(errorCode(error))) throw error;
        sources.set(`restricted:${reference.objectId}`, { source: null, reference: structuredClone(reference), state: 'restricted' });
        continue;
      }
      if (!saved) continue;
      const key = JSON.stringify([keyOf(saved.source), saved.source.operationId]);
      if (sources.has(key)) continue;
      let state: 'available' | 'restricted' = 'available';
      try {
        if (!this.permitted(saved.source)) throw new SyncError('AUTHORITY_UNAVAILABLE');
        const conflict = await this.fetchConflict(saved.conflictId, saved.source.objectId, epoch);
        await this.authorizeOperation(conflict.local_operation, epoch);
        if (await syncFingerprint(conflict.local_operation) !== saved.sourceHash) throw new SyncError('CORRUPT_DRAFT');
      } catch (error) {
        this.current(epoch);
        if (!['AUTHORITY_UNAVAILABLE', 'GONE'].includes(errorCode(error))) throw error;
        state = 'restricted';
      }
      this.current(epoch);
      sources.set(key, { source: structuredClone(saved.source), state });
    }
    this.current(epoch);
    return [...sources.values()];
  }

  private async readEditorEnvelope(reference: CacheWrite, epoch: number): Promise<EditorEnvelope | null> {
    const bytes = await this.cache.read(reference);
    try {
      this.current(epoch);
      if (!bytes) return null;
      const saved = JSON.parse(decoder.decode(bytes)) as EditorEnvelope;
      if (saved.kind !== 'editor_recovery' || saved.schema !== 1 || !saved.source || !uuid(saved.source.operationId)
        || saved.source.objectId !== reference.recoverySourceObjectId
        || saved.source.operationId !== reference.recoveryFor || saved.conflictId !== reference.recoveryConflictId
        || typeof saved.sourceHash !== 'string' || typeof saved.body !== 'string' || typeof saved.note !== 'string') throw new SyncError('CORRUPT_DRAFT');
      return saved;
    } finally { bytes?.fill(0); }
  }

  private learningAllowed(reference: LearningReference, epoch: number, learningEpoch: number) {
    this.sameEpoch(epoch);
    const projection = this.projections.get(reference.objectId);
    if (!this.scopes.has('learning')) throw new SyncError('LOCKED');
    if (learningEpoch !== this.learningEpoch || reference.scope !== 'learning' || !positive(reference.generation)
      || !projection || projection.object_type !== reference.objectType || projection.revision !== reference.revision
      || (reference.objectType === 'review_document' && !uuid(reference.contentRevisionId))
      || !this.permitted(reference)) throw new SyncError('AUTHORITY_UNAVAILABLE');
  }

  async readLearning(reference: LearningReference): Promise<Uint8Array | null> {
    const frozen = structuredClone(reference);
    const epoch = this.epoch;
    const learningEpoch = this.learningEpoch;
    this.learningAllowed(frozen, epoch, learningEpoch);
    if (frozen.objectType === 'review_document') await this.authorizeRevision(frozen.objectId, frozen.contentRevisionId!, epoch);
    this.learningAllowed(frozen, epoch, learningEpoch);
    const bytes = await this.cache.read(frozen);
    try { this.learningAllowed(frozen, epoch, learningEpoch); return bytes; }
    catch (error) { bytes?.fill(0); throw error; }
  }

  /** The hydration port performs the existing authorized HTTP read for this exact cloud revision. */
  async cacheLearning(reference: LearningReference, hydrate: () => Promise<{ objectId: string; revision: number; contentRevisionId?: string; bytes: Uint8Array }>): Promise<void> {
    const frozen = structuredClone(reference);
    const epoch = this.epoch;
    const learningEpoch = this.learningEpoch;
    this.learningAllowed(frozen, epoch, learningEpoch);
    if (frozen.objectType === 'review_document') await this.authorizeRevision(frozen.objectId, frozen.contentRevisionId!, epoch);
    this.learningAllowed(frozen, epoch, learningEpoch);
    const hydrated = await hydrate();
    this.learningAllowed(frozen, epoch, learningEpoch);
    if (hydrated.objectId !== frozen.objectId || hydrated.revision !== frozen.revision || !(hydrated.bytes instanceof Uint8Array)
      || (frozen.objectType === 'review_document' && hydrated.contentRevisionId !== frozen.contentRevisionId)) throw new SyncError('INVALID_HYDRATION');
    const bytes = new Uint8Array(hydrated.bytes);
    try {
      await this.cache.put({ scope: 'learning', objectId: frozen.objectId, revision: frozen.revision, generation: frozen.generation,
        objectType: frozen.objectType, contentRevisionId: frozen.contentRevisionId, ancestry: frozen.ancestry, verifiedAt: Date.now() }, bytes);
      this.learningAllowed(frozen, epoch, learningEpoch);
    } finally { bytes.fill(0); }
  }

  flush(): Promise<void> {
    this.enabled();
    if (this.refreshInProgress) return Promise.reject(new SyncError('SYNC_BUSY'));
    if (!this.flushing) this.flushing = (async () => {
      if (this.polling) await this.polling;
      await this.flushPending();
    })().finally(() => { this.flushing = null; });
    return this.flushing;
  }

  /** Polling consumes the same cursor as replay and never extends the cache's bounded key window. */
  pollChanges(): Promise<void> {
    if (this.polling) return this.polling;
    if (!this.scopes.size || !this.cursor || this.flushing || this.refreshInProgress || this.closing) return Promise.resolve();
    this.polling = this.consumeChanges(this.cursor).then(async () => {
      if (this.scopes.size && this.metadataDirty) await this.refreshChangedMetadata();
    }).catch(error => {
      // A transport outage retains the existing deadline. Definitive failures require a fresh unlock.
      if (!['STORAGE_UNAVAILABLE', 'RESULT_UNKNOWN'].includes(errorCode(error))) this.cache.lock();
    }).finally(() => { this.polling = null; });
    return this.polling;
  }

  private async refreshChangedMetadata() {
    const epoch = this.epoch;
    const start = this.cursor;
    const bootstrap = await this.transport.bootstrap();
    this.sameEpoch(epoch);
    this.installBootstrap(bootstrap);
    await this.consumeChanges(start ?? bootstrap.watermark);
    this.sameEpoch(epoch);
  }

  private async flushPending() {
    const epoch = this.epoch;
    this.current(epoch);
    const start = this.cursor;
    // A fresh authority snapshot precedes each replay. It never replaces the local queue.
    const latest = await this.transport.bootstrap();
    this.current(epoch);
    this.installBootstrap(latest);
    try { await this.consumeChanges(start ?? latest.watermark); }
    catch (error) {
      // An expired feed cannot establish that an already visible body remains authorized.
      // Rebuild the snapshot only through the explicit unlock flow, with views locked first.
      if (errorCode(error) === 'CURSOR_EXPIRED') this.cache.lock();
      throw error;
    }
    this.current(epoch);
    if (this.metadataDirty) await this.refreshChangedMetadata();
    this.current(epoch);
    const known = [...this.rows.values()].map(value => structuredClone(value.reference));
    const references = (await this.cache.pendingDraftReferences()).filter(value => value.objectType !== 'sync_editor_recovery');
    this.current(epoch);
    const persisted = new Set(references.map(keyOf));
    for (const reference of known) {
      if (!persisted.has(keyOf(reference))) await this.reconcileMissing(reference, epoch);
    }
    for (const reference of references) {
      this.current(epoch);
      if (!this.permitted(reference)) { this.row(reference, 'restricted', 'AUTHORITY_UNAVAILABLE'); continue; }
      try {
        const envelope = await this.load(reference);
        this.current(epoch);
        this.row(reference, 'syncing');
        await this.send(reference, envelope, epoch);
      } catch (error) {
        this.current(epoch);
        if (errorCode(error) === 'DRAFT_UNAVAILABLE') { await this.reconcileMissing(reference, epoch); continue; }
        this.row(reference, ['GONE', 'AUTHORITY_UNAVAILABLE'].includes(errorCode(error)) ? 'restricted' : 'failed', errorCode(error));
      }
    }
  }

  private receiptBinding(envelope: Envelope): ReceiptBinding {
    return { operation: envelope.kind === 'write' ? 'syncOperation' : 'resolveConflict',
      operationId: envelope.operation.op_id, requestHash: envelope.requestHash, objectId: envelope.operation.object_id,
      allowsNewObject: envelope.kind === 'resolve' && envelope.resolution.resolution === 'copy_to_new_note' };
  }

  private validateReceipt(receipt: SyncReceipt, binding: ReceiptBinding) {
    if (receipt.op_id !== binding.operationId || receipt.operation !== binding.operation || receipt.request_sha256 !== binding.requestHash
      || !['applied', 'conflict', 'deleted', 'rejected', 'pending'].includes(receipt.status)
      || (receipt.status === 'applied' && (!positive(receipt.revision) || !uuid(receipt.object_id)
        || (!binding.allowsNewObject && receipt.object_id !== binding.objectId)))) throw new SyncError('INVALID_RECEIPT');
  }

  private removeRow(reference: CacheWrite) {
    this.rows.delete(keyOf(reference));
    this.bindings.delete(keyOf(reference));
  }

  /** Another tab can ACK the shared record. Missing local bytes alone never establish cloud persistence. */
  private async reconcileMissing(reference: CacheWrite, epoch: number) {
    const binding = this.bindings.get(keyOf(reference));
    if (!binding) { this.row(reference, 'failed', 'DRAFT_UNAVAILABLE'); return; }
    try {
      const receipt = await this.transport.receipt(binding.operationId, binding.operation);
      this.current(epoch);
      this.validateReceipt(receipt, binding);
      if (receipt.status === 'applied') { this.removeRow(reference); this.changed(); }
      else if (receipt.status === 'conflict' && binding.operation === 'syncOperation' && uuid(receipt.conflict_id)) {
        const conflict = await this.transport.conflict(receipt.conflict_id);
        this.current(epoch);
        const originalHash = await syncFingerprint(conflict.local_operation);
        this.current(epoch);
        if (conflict.id === receipt.conflict_id && conflict.object_id === binding.objectId && conflict.resolved === true
          && originalHash === binding.requestHash) { this.removeRow(reference); this.changed(); }
        else this.row(reference, 'failed', 'DRAFT_UNAVAILABLE');
      }
      else this.row(reference, receipt.status === 'deleted' ? 'restricted' : 'failed', 'DRAFT_UNAVAILABLE');
    } catch (error) {
      this.current(epoch);
      this.row(reference, 'failed', errorCode(error) === 'NOT_FOUND' ? 'DRAFT_UNAVAILABLE' : errorCode(error));
    }
  }

  private async pushWrite(envelope: WriteEnvelope): Promise<SyncReceipt> {
    const result = await this.transport.push(envelope.operation, envelope.batchId);
    if (!result || !Array.isArray(result.receipts) || result.receipts.length !== 1) throw new SyncError('INVALID_RECEIPT');
    // Only consumed changes can advance the local watermark.
    return result.receipts[0];
  }

  private async send(reference: CacheWrite, envelope: Envelope, epoch: number) {
    const operation = envelope.kind === 'write' ? 'syncOperation' : 'resolveConflict';
    let receipt: SyncReceipt;
    try { receipt = await this.transport.receipt(envelope.operation.op_id, operation); }
    catch (error) {
      this.current(epoch);
      if (errorCode(error) !== 'NOT_FOUND') throw error;
      if (envelope.kind === 'resolve') receipt = await this.transport.resolve(envelope.conflictId, envelope.resolution, envelope.operation.op_id);
      else receipt = await this.pushWrite(envelope);
    }
    this.current(epoch);
    this.validateReceipt(receipt, this.receiptBinding(envelope));
    if (receipt.status === 'applied') {
      if (envelope.kind === 'resolve') {
        for (const recovery of envelope.recoveryReferences ?? []) {
          await this.cache.acknowledgeDraft(recovery, recovery.operationId!);
          this.current(epoch);
        }
        for (const superseded of envelope.supersedes ?? []) {
          await this.cache.acknowledgeDraft(superseded, superseded.operationId!);
          this.current(epoch);
          this.removeRow(superseded);
        }
        await this.cache.acknowledgeDraft(envelope.original, envelope.original.operationId!);
        this.current(epoch);
        this.removeRow(envelope.original);
      }
      await this.cache.acknowledgeDraft(reference, envelope.operation.op_id);
      this.current(epoch);
      this.removeRow(reference);
      this.changed();
    } else if (receipt.status === 'conflict' && uuid(receipt.conflict_id)) this.row(reference, 'conflict', null, receipt.conflict_id);
    else this.row(reference, receipt.status === 'deleted' ? 'restricted' : 'failed', receipt.status.toUpperCase());
  }

  async inspectConflict(reference: CacheWrite): Promise<SyncConflict> {
    const epoch = this.epoch;
    this.current(epoch);
    const frozen = structuredClone(reference);
    const row = this.rows.get(keyOf(frozen));
    if (!row?.conflictId) throw new SyncError('CONFLICT_UNAVAILABLE');
    const envelope = await this.load(frozen);
    const original = envelope.kind === 'write' && envelope.operation.object_type === 'personal_revision' ? envelope : undefined;
    const conflict = await this.fetchConflict(row.conflictId, frozen.objectId, epoch, original);
    this.current(epoch);
    if (conflict.id !== row.conflictId || conflict.object_id !== frozen.objectId || conflict.resolved
      || conflict.base_revision !== envelope.operation.base_revision
      || await syncFingerprint(conflict.local_operation) !== await syncFingerprint(envelope.operation)) throw new SyncError('INCOMPATIBLE_CLIENT');
    this.current(epoch);
    return structuredClone(conflict);
  }

  /** Explicitly request a new comparison after another device changed the cloud version again. */
  async recheckConflict(reference: CacheWrite, generation: number): Promise<CacheWrite> {
    const epoch = this.epoch;
    this.current(epoch);
    const previous = structuredClone(reference);
    if (this.rows.get(keyOf(previous))?.state !== 'conflict') throw new SyncError('CONFLICT_UNAVAILABLE');
    // First recover every related unknown response using its original key.
    await this.flush();
    this.current(epoch);
    await this.inspectConflict(previous);
    this.current(epoch);
    const envelope = await this.load(previous);
    this.current(epoch);
    if (envelope.kind !== 'write' || !positive(generation) || generation <= previous.generation) throw new SyncError('INVALID_RESOLUTION');
    const operation = structuredClone(envelope.operation);
    const supersedes = [...(envelope.supersedes ?? []), previous];
    const references = await this.cache.pendingDraftReferences();
    this.current(epoch);
    let latestMergeGeneration = 0;
    for (const candidate of references) {
      if (candidate.objectId !== previous.objectId || keyOf(candidate) === keyOf(previous)) continue;
      const pending = await this.load(candidate);
      this.current(epoch);
      if (pending.kind !== 'resolve' || keyOf(pending.original) !== keyOf(previous)) continue;
      if (this.rows.get(keyOf(candidate))?.error !== 'REVISION_CONFLICT') throw new SyncError('RESULT_UNKNOWN');
      supersedes.push(candidate);
      if (pending.resolution.resolution === 'edited_merge' && pending.resolution.merged_operation && candidate.generation > latestMergeGeneration) {
        operation.payload = structuredClone(pending.resolution.merged_operation.payload);
        operation.payload.base_revision = operation.base_revision;
        latestMergeGeneration = candidate.generation;
      }
    }
    operation.op_id = this.newId();
    const next: CacheWrite = { ...previous, generation, operationId: operation.op_id };
    const replacement: WriteEnvelope = { ...envelope, operation, batchId: operation.op_id,
      requestHash: await syncFingerprint(operation), supersedes };
    this.current(epoch);
    await this.persist(next, replacement);
    this.current(epoch);
    this.row(next, 'local_only');
    await this.flush();
    return structuredClone(next);
  }

  private async retrySavedResolution(original: CacheWrite, body: SyncResolution, epoch: number): Promise<boolean> {
    const pending = (await this.cache.pendingDraftReferences()).find(reference => resolvesDraft(reference, original));
    this.current(epoch);
    if (!pending) return false;
    const envelope = await this.load(pending);
    const fingerprint = envelope.kind === 'resolve' ? await syncFingerprint({ conflict_id: envelope.conflictId, body }) : null;
    this.current(epoch);
    if (envelope.requestHash !== fingerprint) throw new SyncError('RESULT_UNKNOWN');
    await this.flush();
    return true;
  }

  /** Each explicit resolution is itself persisted before sending, so an unknown response is recoverable. */
  async resolve(reference: CacheWrite, resolution: SyncResolution, generation: number, recoveryReferences: CacheWrite[] = []): Promise<void> {
    const epoch = this.epoch;
    this.current(epoch);
    const original = structuredClone(reference);
    const body = structuredClone(resolution);
    if (!original.operationId || original.resolutionOf || !positive(generation) || generation <= original.generation
      || !['edited_merge', 'copy_to_new_note', 'use_server'].includes(body.resolution)) throw new SyncError('INVALID_RESOLUTION');
    if (await this.retrySavedResolution(original, body, epoch)) return;
    // Other resolutions do not submit the manually edited recovery body, so its ciphertext stays.
    const requestedRecoveries = body.resolution === 'edited_merge' ? structuredClone(recoveryReferences) : [];
    const recoveries: CacheWrite[] = [];
    const originalEnvelope = await this.load(original);
    this.current(epoch);
    const conflict = await this.inspectConflict(original);
    this.current(epoch);
    if (body.base_revision !== conflict.server_revision) throw new SyncError('INVALID_RESOLUTION');
    if (body.resolution === 'edited_merge') {
      validateOperation(body.merged_operation);
      if (body.merged_operation.object_type !== conflict.local_operation.object_type || body.merged_operation.object_id !== conflict.object_id
        || body.merged_operation.base_revision !== body.base_revision || body.merged_operation.op_id === conflict.local_operation.op_id) throw new SyncError('INVALID_RESOLUTION');
    }
    if (body.resolution === 'copy_to_new_note' && (!uuid(body.destination_class_session_id)
      || this.projections.get(body.destination_class_session_id)?.object_type !== 'class_session')) throw new SyncError('INVALID_RESOLUTION');
    const sourceReferences = new Map([original, ...(originalEnvelope.supersedes ?? [])].map(value => [value.operationId, value]));
    const sourceOperations = new Set(sourceReferences.keys());
    if (requestedRecoveries.some(value => value.objectType !== 'sync_editor_recovery' || !sourceOperations.has(value.recoveryFor)
      || !uuid(value.operationId) || !positive(value.generation))) throw new SyncError('INVALID_RESOLUTION');
    for (const recovery of requestedRecoveries) {
      const saved = await this.readEditorEnvelope(recovery, epoch);
      if (!saved) continue;
      const expectedSource = sourceReferences.get(saved.source.operationId);
      if (!expectedSource || keyOf(expectedSource) !== keyOf(saved.source)) throw new SyncError('INVALID_RESOLUTION');
      const frozenConflict = await this.fetchConflict(saved.conflictId, saved.source.objectId, epoch);
      await this.authorizeOperation(frozenConflict.local_operation, epoch);
      if (await syncFingerprint(frozenConflict.local_operation) !== saved.sourceHash) throw new SyncError('CORRUPT_DRAFT');
      const merged = body.merged_operation!;
      let included = false;
      if (merged.object_type === 'personal_revision') included = saved.body === merged.payload.body_markdown && saved.note === merged.payload.personal_note;
      else if (saved.note === '') {
        try { included = await syncFingerprint({ ...JSON.parse(saved.body), base_revision: body.base_revision }) === await syncFingerprint(merged.payload); }
        catch { /* Invalid or differing recovery text remains independently recoverable. */ }
      }
      this.current(epoch);
      if (included) recoveries.push(recovery);
    }
    const opId = this.newId();
    const operation = structuredClone(conflict.local_operation);
    operation.op_id = opId;
    operation.base_revision = body.base_revision;
    operation.payload.base_revision = body.base_revision;
    const pending: CacheWrite = { ...original, revision: body.base_revision, generation, operationId: opId,
      resolutionOf: { operationId: original.operationId, revision: original.revision, generation: original.generation } };
    const envelope: ResolutionEnvelope = { schema: 1, kind: 'resolve', operation, conflictId: conflict.id, resolution: body, original,
      requestHash: await syncFingerprint({ conflict_id: conflict.id, body }), supersedes: originalEnvelope.supersedes, recoveryReferences: recoveries };
    this.current(epoch);
    try { await this.persist(pending, envelope); }
    catch (error) {
      // Two tabs can both look up the original before either writes. Reuse only an identical durable request.
      if (errorCode(error) === 'REVISION_CONFLICT' && await this.retrySavedResolution(original, body, epoch)) return;
      throw error;
    }
    this.current(epoch);
    this.row(pending, 'local_only');
    await this.flush();
  }

  close() {
    this.closing = true;
    if (typeof window !== 'undefined') window.removeEventListener('online', this.onOnline);
    if (this.timer) clearInterval(this.timer);
    this.cache.removeEventListener('locked', this.onLocked);
    this.onLocked(); this.cache.close();
  }
}
