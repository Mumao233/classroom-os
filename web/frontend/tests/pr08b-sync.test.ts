import { describe, expect, it } from 'vitest';
import type { components } from '../src/generated/openapi';
import type { CacheAncestry, CacheReference, CacheWrite, DeletionNotice } from '../src/cache/types';
import { syncFingerprint } from '../src/sync/client';
import { AuthError } from '../src/api/auth-client';
import type { SyncOperation, SyncReceipt, SyncResolution, SyncTransport } from '../src/sync/client';
import { OfflineSyncRuntime, SyncError } from '../src/sync/runtime';
import type { SyncCache } from '../src/sync/runtime';
import type { LearningReference } from '../src/sync/runtime';

type S = components['schemas'];
const id = (value: number) => `10000000-0000-4000-8000-${String(value).padStart(12, '0')}`;
const ancestry = { semesterId: null, courseId: null, classSessionId: null };
const stamp = '2026-11-17T12:00:00Z';
const key = (reference: CacheReference) => JSON.stringify([reference.scope, reference.objectId, reference.revision, reference.generation]);
const operation = (number = 1): SyncOperation => ({ op_id: id(number), object_type: 'course', object_id: id(100), draft_id: id(200), base_revision: 1, payload: { base_revision: 1, name: `课程 ${number}` } });
const projection = (objectId = id(100), kind: S['BootstrapProjection']['object_type'] = 'course'): S['BootstrapProjection'] => ({ object_id: objectId, object_type: kind, revision: 1, parent_id: null, adopted_revision_id: null, updated_at: stamp });
const personalOperation = (contentRevisionId: string | null = id(800)): SyncOperation => ({ op_id: id(1), object_type: 'personal_revision', object_id: id(100), draft_id: id(200), base_revision: 1,
  payload: { base_revision: 1, base_content_revision_id: contentRevisionId, draft_id: id(200), body_markdown: 'SOURCE B PRIVATE DRAFT', personal_note: 'unique note' } });
const reviewRevision = (revisionId: string): S['ReviewRevision'] => ({ id: revisionId, document_id: id(100), revision: 1, kind: 'ai_candidate', created_at: stamp, updated_at: stamp,
  parent_revision_id: null, source_snapshot_id: id(810), raw_output_ref: null, body_markdown: 'SOURCE B PRIVATE BODY', structured_content: null, personal_note: '' });

class MemoryCache extends EventTarget implements SyncCache {
  records = new Map<string, { reference: CacheWrite; bytes: Uint8Array }>();
  events: string[] = [];
  denied = false;
  recoveryHeads = new Map<string, number>();
  lock() { this.events.push('lock'); this.dispatchEvent(new Event('locked')); }
  async lockForDeletion(ids: readonly string[]) { if (ids.length) { this.events.push('peer-lock'); this.lock(); } }
  async pendingDeletionIds() { return []; }
  close() { this.lock(); }
  async unlockScope() { this.events.push('unlock'); }
  async reconcileLearning(projections: S['BootstrapProjection'][], options: { preserveKeys?: boolean } = {}) {
    this.events.push('reconcile');
    if (!options.preserveKeys) this.lock();
    for (const [recordId, value] of this.records) {
      if (value.reference.scope === 'learning' && !projections.some(item => item.object_id === value.reference.objectId && item.revision === value.reference.revision)) this.records.delete(recordId);
    }
  }
  async pendingDraftReferences() { return [...this.records.values()].filter(value => value.reference.scope === 'drafts').map(value => structuredClone(value.reference)); }
  async put(reference: CacheWrite, bytes: Uint8Array) {
    if (this.denied) throw new SyncError('QUOTA_EXCEEDED');
    const previous = this.records.get(key(reference));
    if (previous && (JSON.stringify(previous.reference) !== JSON.stringify(reference) || Buffer.compare(previous.bytes, bytes))) throw new SyncError('REVISION_CONFLICT');
    this.records.set(key(reference), { reference: structuredClone(reference), bytes: new Uint8Array(bytes) });
  }
  async saveRecoveryDraft(reference: CacheWrite, bytes: Uint8Array) {
    if (this.denied) throw new SyncError('QUOTA_EXCEEDED');
    if ((this.recoveryHeads.get(reference.objectId) ?? 0) >= reference.generation) return;
    this.recoveryHeads.set(reference.objectId, reference.generation);
    for (const [id, record] of this.records) if (record.reference.objectType === 'sync_editor_recovery' && record.reference.objectId === reference.objectId) this.records.delete(id);
    this.records.set(key(reference), { reference: structuredClone(reference), bytes: new Uint8Array(bytes) });
  }
  async retryRecoveryDrafts() { return []; }
  async read(reference: CacheReference) { const value = this.records.get(key(reference)); return value ? new Uint8Array(value.bytes) : null; }
  async acknowledgeDraft(reference: CacheReference, opId: string) {
    const value = this.records.get(key(reference));
    if (value && value.reference.operationId !== opId) throw new SyncError('REVISION_CONFLICT');
    this.records.delete(key(reference));
  }
  async applyDeletion(_notice: DeletionNotice, options: { retainDrafts: true }) { if (!options.retainDrafts) throw new Error('Draft deletion'); this.events.push('delete'); this.lock(); }
}

async function receipt(op: SyncOperation, status: SyncReceipt['status'] = 'applied'): Promise<SyncReceipt> {
  return { op_id: op.op_id, operation: 'syncOperation', request_sha256: await syncFingerprint(op), status,
    object_id: op.object_id, revision: 2, conflict_id: status === 'conflict' ? id(300) : null, task_id: null, created_at: stamp };
}

function fixture() {
  const cache = new MemoryCache();
  const received: SyncOperation[] = [];
  const receipts = new Map<string, SyncReceipt>();
  let projections = [projection(), projection(id(101), 'class_session')];
  let sequence = 500;
  const transport: SyncTransport = {
    bootstrap: async () => ({ watermark: 'snapshot', generated_at: stamp, projection_complete: true, projections: structuredClone(projections), maximum_projection_count: 10000 }),
    changes: async () => ({ items: [], next_cursor: 'consumed', has_more: false }),
    deletion: async () => ({ id: id(400), revision: 1, created_at: stamp, updated_at: stamp, scope_type: 'course', scope_id: id(100), state: 'access_revoked', access_revoked_at: stamp, physical_completed_at: null, failure_code: null }),
    receipt: async (opId, name) => { const value = receipts.get(opId); if (!value || value.operation !== name) throw new SyncError('NOT_FOUND'); return structuredClone(value); },
    push: async op => { received.push(structuredClone(op)); const saved = await receipt(op); receipts.set(op.op_id, saved); return { receipts: [saved], cursor: 'unconsumed-server-hint' }; },
    conflict: async () => ({ id: id(300), object_id: id(100), base_revision: 1, server_revision: 2, local_operation: operation(), server_content: { id: id(100) } as S['Course'], resolved: false }),
    reviewRevision: async revisionId => reviewRevision(revisionId),
    document: async documentId => ({ id: documentId, revision: 1, created_at: stamp, updated_at: stamp, class_session_id: id(101), kind: 'note', title: 'Personal note',
      adopted_revision_id: null, latest_personal_revision_id: null, candidate_revision_ids: [], understood: false, source_status: 'unchanged' }),
    resolve: async (conflictId, body, opId) => {
      const saved: SyncReceipt = { op_id: opId, operation: 'resolveConflict', request_sha256: await syncFingerprint({ conflict_id: conflictId, body }), status: 'applied', object_id: body.resolution === 'copy_to_new_note' ? id(900) : id(100), revision: 3, conflict_id: null, task_id: null, created_at: stamp };
      receipts.set(opId, saved); return saved;
    },
  };
  const runtime = new OfflineSyncRuntime(cache, transport, { enabled: true, newId: () => id(sequence++) });
  return { cache, runtime, transport, received, receipts, setProjections: (value: S['BootstrapProjection'][]) => { projections = value; } };
}

async function editableConflict(op: SyncOperation = operation()) {
  const f = fixture();
  if (op.object_type === 'personal_revision') f.setProjections([projection(id(100), 'review_document')]);
  await f.runtime.unlock(''); const reference = await f.runtime.enqueue(op, ancestry, 1);
  f.receipts.set(op.op_id, await receipt(op, 'conflict'));
  f.transport.conflict = async () => ({ id: id(300), object_id: id(100), base_revision: 1, server_revision: 2, local_operation: op,
    server_content: { id: id(100) } as S['Course'], resolved: false });
  await f.runtime.flush(); await f.runtime.inspectConflict(reference);
  return { ...f, reference };
}

describe('PR-08b encrypted operation queue', () => {
  it('stays disabled unless explicitly enabled', async () => {
    const f = fixture();
    const disabled = new OfflineSyncRuntime(f.cache, f.transport);
    await expect(disabled.unlock('')).rejects.toMatchObject({ code: 'DISABLED' });
    expect(f.cache.events).toEqual([]);
  });

  it('completes authority and learning reconciliation before key release', async () => {
    const f = fixture();
    await f.runtime.unlock('');
    expect(f.cache.events).toEqual(['lock', 'reconcile', 'lock', 'unlock']);
  });

  it('freezes mutable payload and ancestry before hashing or writing', async () => {
    const f = fixture(); await f.runtime.unlock('');
    const op = operation(); const parents: CacheAncestry = { ...ancestry };
    const pending = f.runtime.enqueue(op, parents, 1);
    if (op.object_type === 'course') op.payload.name = 'mutated';
    parents.courseId = id(999);
    const reference = await pending;
    expect((await f.runtime.readDraft(reference)).payload).toMatchObject({ name: '课程 1' });
    expect(reference.ancestry.courseId).toBeNull();
  });

  it('only acknowledges the submitted generation and ignores the push cursor hint', async () => {
    const f = fixture(); await f.runtime.unlock('');
    const first = await f.runtime.enqueue(operation(), ancestry, 1);
    let release!: (value: S['SyncResult']) => void;
    let sent!: () => void;
    const started = new Promise<void>(resolve => { sent = resolve; });
    f.transport.push = async () => { sent(); return new Promise(resolve => { release = resolve; }); };
    const flushing = f.runtime.flush(); await started;
    const next = await f.runtime.enqueue(operation(2), ancestry, 2);
    release({ receipts: [await receipt(operation())], cursor: 'ahead' }); await flushing;
    expect(await f.cache.read(first)).toBeNull();
    expect(await f.cache.read(next)).not.toBeNull();
    expect(f.runtime.snapshot().pending.map(value => value.reference.operationId)).toEqual([id(2)]);
    expect(f.runtime.snapshot().cursor).toBe('consumed');
  });

  it('recovers a committed write after its HTTP response is lost', async () => {
    const f = fixture(); await f.runtime.unlock('');
    await f.runtime.enqueue(operation(), ancestry, 1);
    let sends = 0;
    f.transport.push = async op => { sends++; f.receipts.set(op.op_id, await receipt(op)); throw new SyncError('RESULT_UNKNOWN'); };
    await f.runtime.flush(); expect(f.cache.records.size).toBe(1);
    await f.runtime.flush(); expect(f.cache.records.size).toBe(0); expect(sends).toBe(1);
  });

  it('retries the original operation only after a definitive missing receipt', async () => {
    const f = fixture(); await f.runtime.unlock('');
    await f.runtime.enqueue(operation(), ancestry, 1);
    f.transport.receipt = async () => { throw new SyncError('STORAGE_UNAVAILABLE'); };
    await f.runtime.flush(); expect(f.received).toHaveLength(0); expect(f.cache.records.size).toBe(1);
  });

  it.each(['request_sha256', 'op_id', 'object_id', 'operation'] as const)('retains the draft when receipt %s mismatches', async field => {
    const f = fixture(); await f.runtime.unlock('');
    await f.runtime.enqueue(operation(), ancestry, 1);
    // Inject a malformed successful response directly: the normal fixture filters by operation
    // and correctly turns a differently scoped receipt into NOT_FOUND before validation.
    const malformed = { ...await receipt(operation()), [field]: 'mismatch' };
    f.transport.receipt = async () => structuredClone(malformed);
    await f.runtime.flush(); expect(f.cache.records.size).toBe(1);
    expect(f.runtime.snapshot().pending[0].error).toBe('INVALID_RECEIPT'); expect(f.received).toHaveLength(0);
  });

  it('retains conflict, rejected, pending and deleted operations', async () => {
    for (const status of ['conflict', 'rejected', 'pending', 'deleted'] as const) {
      const f = fixture(); await f.runtime.unlock(''); await f.runtime.enqueue(operation(), ancestry, 1);
      f.receipts.set(id(1), await receipt(operation(), status)); await f.runtime.flush();
      expect(f.cache.records.size).toBe(1);
      expect(f.runtime.snapshot().pending[0].state).toBe(status === 'conflict' ? 'conflict' : status === 'deleted' ? 'restricted' : 'failed');
    }
  });

  it('restores pending records after a new runtime opens the same encrypted store', async () => {
    const f = fixture(); await f.runtime.unlock(''); const reference = await f.runtime.enqueue(operation(), ancestry, 1); f.runtime.close();
    const next = new OfflineSyncRuntime(f.cache, f.transport, { enabled: true }); await next.unlock('');
    expect(await next.readDraft(reference)).toEqual(operation()); await next.flush(); expect(f.cache.records.size).toBe(0);
  });

  it('reconciles a different tab acknowledgement using the original receipt binding', async () => {
    const f = fixture(); await f.runtime.unlock(''); await f.runtime.enqueue(operation(), ancestry, 1);
    const otherCache = new MemoryCache(); otherCache.records = f.cache.records;
    const other = new OfflineSyncRuntime(otherCache, f.transport, { enabled: true }); await other.unlock('');
    expect(other.snapshot().pending).toHaveLength(1);
    await f.runtime.flush(); expect(other.snapshot().pending).toHaveLength(1);
    await other.flush(); expect(other.snapshot().pending).toHaveLength(0); expect(f.received).toHaveLength(1);
  });

  it('reports missing local storage when no matching applied receipt can be established', async () => {
    const f = fixture(); await f.runtime.unlock(''); await f.runtime.enqueue(operation(), ancestry, 1);
    f.cache.records.clear(); await f.runtime.flush();
    expect(f.runtime.snapshot().pending[0]).toMatchObject({ state: 'failed', error: 'DRAFT_UNAVAILABLE' });
    expect(f.received).toHaveLength(0);
    f.receipts.set(id(1), { ...await receipt(operation()), request_sha256: 'wrong' });
    await f.runtime.flush(); expect(f.runtime.snapshot().pending[0].error).toBe('INVALID_RECEIPT');
  });

  it('checks the authorization epoch after reconciling a missing shared record', async () => {
    const f = fixture(); await f.runtime.unlock(''); await f.runtime.enqueue(operation(), ancestry, 1); f.cache.records.clear();
    f.transport.receipt = async () => { f.cache.lock(); return receipt(operation()); };
    await expect(f.runtime.flush()).rejects.toMatchObject({ code: 'LOCKED' });
    expect(f.runtime.snapshot().unlocked).toBe(false); expect(f.runtime.snapshot().pending).toEqual([]);
  });

  it('confirms another tab resolved the same preserved server conflict before clearing its stale row', async () => {
    const f = fixture(); await f.runtime.unlock(''); await f.runtime.enqueue(operation(), ancestry, 1);
    f.receipts.set(id(1), await receipt(operation(), 'conflict')); await f.runtime.flush(); f.cache.records.clear();
    await f.runtime.flush(); expect(f.runtime.snapshot().pending[0].error).toBe('DRAFT_UNAVAILABLE');
    const conflict = f.transport.conflict;
    f.transport.conflict = async id => ({ ...await conflict(id), resolved: true });
    await f.runtime.flush(); expect(f.runtime.snapshot().pending).toEqual([]);
  });

  it('locks visible content and preserves unique ciphertext when the complete snapshot omits its object', async () => {
    const f = fixture(); await f.runtime.unlock(''); const reference = await f.runtime.enqueue(operation(), ancestry, 1);
    f.setProjections([]); await expect(f.runtime.flush()).rejects.toMatchObject({ code: 'LOCKED' });
    expect(f.runtime.snapshot().unlocked).toBe(false); expect(f.cache.records.size).toBe(1);
    await expect(f.runtime.readDraft(reference)).rejects.toMatchObject({ code: 'LOCKED' });
    await f.runtime.unlock('');
    expect(f.runtime.snapshot().pending[0].state).toBe('restricted'); expect(f.cache.records.size).toBe(1);
    await expect(f.runtime.readDraft(reference)).rejects.toMatchObject({ code: 'AUTHORITY_UNAVAILABLE' }); expect(f.received).toHaveLength(0);
  });

  it('consumes intervening deletion events from the old cursor when a new bootstrap advances', async () => {
    const f = fixture(); await f.runtime.unlock(''); await f.runtime.enqueue(operation(), ancestry, 1);
    const bootstrap = f.transport.bootstrap;
    f.transport.bootstrap = async () => ({ ...await bootstrap(), watermark: 'after-deletion', projections: [] });
    const cursors: string[] = [];
    f.transport.changes = async cursor => {
      cursors.push(cursor);
      return { items: cursor === 'consumed' ? [{ event_id: id(401), object_type: 'deletion' as const, object_id: id(400), revision: 1, deleted: true }] : [], next_cursor: 'after-deletion', has_more: false };
    };
    await expect(f.runtime.flush()).rejects.toMatchObject({ code: 'LOCKED' });
    expect(cursors).toEqual(['consumed']); expect(f.cache.events).toContain('delete');
    expect(f.runtime.snapshot().cursor).toBe('after-deletion'); expect(f.cache.records.size).toBe(1); expect(f.received).toHaveLength(0);
  });

  it('locks before deletion detail arrives and retries the same cursor after a detail outage', async () => {
    const f = fixture(); await f.runtime.unlock('');
    const reference = await f.runtime.enqueue(operation(), ancestry, 1);
    const saved = structuredClone(f.cache.records.get(key(reference)));
    const cursors: string[] = [];
    f.transport.changes = async cursor => {
      cursors.push(cursor);
      return { items: [{ event_id: id(401), object_type: 'deletion', object_id: id(400), revision: 1, deleted: true }],
        next_cursor: 'after-deletion', has_more: false };
    };
    const readDeletion = f.transport.deletion;
    let started!: () => void;
    let fail!: (error: AuthError) => void;
    const detailStarted = new Promise<void>(resolve => { started = resolve; });
    f.transport.deletion = () => new Promise((_, reject) => { fail = reject; started(); });

    const polling = f.runtime.pollChanges();
    await detailStarted;
    expect(f.runtime.snapshot()).toMatchObject({ unlocked: false, cursor: 'consumed' });
    await expect(f.runtime.readDraft(reference)).rejects.toMatchObject({ code: 'LOCKED' });
    expect(f.cache.events).not.toContain('delete');
    fail(new AuthError('STORAGE_UNAVAILABLE'));
    await polling;
    expect(f.runtime.snapshot().cursor).toBe('consumed');

    f.transport.deletion = readDeletion;
    await f.runtime.unlock('');
    expect(cursors).toEqual(['consumed', 'consumed']);
    expect(f.runtime.snapshot()).toMatchObject({ cursor: 'after-deletion', pending: [{ state: 'restricted', reference }] });
    expect(f.cache.records.get(key(reference))).toEqual(saved);
    expect(f.received).toHaveLength(0);
  });

  it('retains the initial watermark when a newer bootstrap follows an unread deletion', async () => {
    const f = fixture();
    const cursors: string[] = [];
    f.transport.changes = async cursor => {
      cursors.push(cursor);
      return { items: cursor === 'snapshot' ? [{ event_id: id(401), object_type: 'deletion', object_id: id(400), revision: 1, deleted: true }] : [],
        next_cursor: 'after-deletion', has_more: false };
    };
    const readDeletion = f.transport.deletion;
    f.transport.deletion = async () => { throw new AuthError('NOT_FOUND', 404); };
    await expect(f.runtime.unlock('')).rejects.toMatchObject({ code: 'NOT_FOUND' });
    expect(f.runtime.snapshot()).toMatchObject({ unlocked: false, cursor: 'snapshot' });
    const bootstrap = f.transport.bootstrap;
    f.transport.bootstrap = async () => ({ ...await bootstrap(), watermark: 'after-deletion' });
    f.transport.deletion = readDeletion;
    await f.runtime.unlock('');
    expect(cursors).toEqual(['snapshot', 'snapshot']);
    expect(f.cache.events.lastIndexOf('delete')).toBeLessThan(f.cache.events.lastIndexOf('unlock'));
    expect(f.runtime.snapshot().cursor).toBe('after-deletion');
  });

  it('locks the open view when local deletion fence storage cannot be read', async () => {
    const f = fixture(); await f.runtime.unlock('');
    const reference = await f.runtime.enqueue(operation(), ancestry, 1);
    f.cache.pendingDeletionIds = async () => { throw new SyncError('STORAGE_UNAVAILABLE'); };
    let requested = false;
    f.transport.changes = async () => { requested = true; throw new Error('Unexpected feed request'); };
    await f.runtime.pollChanges();
    expect(f.runtime.snapshot()).toMatchObject({ unlocked: false, cursor: 'consumed' });
    expect(requested).toBe(false);
    await expect(f.runtime.readDraft(reference)).rejects.toMatchObject({ code: 'LOCKED' });
    expect(f.cache.records.size).toBe(1);
  });

  it('does not persist a deletion ID from a malformed changes page', async () => {
    const f = fixture(); await f.runtime.unlock('');
    const changes = f.transport.changes;
    const fences: string[][] = [];
    f.cache.lockForDeletion = async ids => { fences.push([...ids]); };
    f.transport.changes = async () => ({ items: [{ event_id: 'invalid event ID', object_type: 'deletion',
      object_id: id(400), revision: 1, deleted: true }], next_cursor: 'invalid-page', has_more: false });
    await f.runtime.pollChanges();
    expect(f.runtime.snapshot()).toMatchObject({ unlocked: false, cursor: 'consumed' });
    expect(fences).toEqual([]);
    f.transport.changes = changes;
    await f.runtime.unlock('');
    expect(f.runtime.snapshot()).toMatchObject({ unlocked: true, cursor: 'consumed' });
  });

  it('fences a valid deletion before rejecting its malformed page neighbor', async () => {
    const f = fixture(); await f.runtime.unlock('');
    const pending: string[] = [];
    f.cache.lockForDeletion = async ids => { pending.push(...ids); f.cache.lock(); };
    const correct = { event_id: id(401), object_type: 'deletion' as const, object_id: id(400), revision: 1, deleted: true };
    f.transport.changes = async () => ({ items: [correct, { ...correct, event_id: 'invalid', object_id: id(402) }],
      next_cursor: 'bad-page', has_more: false });
    await f.runtime.pollChanges();
    expect(pending).toEqual([correct.object_id]);
    expect(f.runtime.snapshot()).toMatchObject({ unlocked: false, cursor: 'consumed' });
  });

  it('locks a malformed page even if writing a known deletion fence fails', async () => {
    const f = fixture(); await f.runtime.unlock('');
    f.cache.lockForDeletion = async () => { throw new SyncError('STORAGE_UNAVAILABLE'); };
    const correct = { event_id: id(401), object_type: 'deletion' as const, object_id: id(400), revision: 1, deleted: true };
    f.transport.changes = async () => ({ items: [correct, { ...correct, event_id: 'invalid' }],
      next_cursor: 'bad-page', has_more: false });
    await f.runtime.pollChanges();
    expect(f.runtime.snapshot()).toMatchObject({ unlocked: false, cursor: 'consumed' });
  });

  it('resumes from the last completed page after the bounded catch-up limit', async () => {
    const f = fixture();
    const cursors: string[] = [];
    f.transport.changes = async cursor => {
      cursors.push(cursor);
      const next = cursor === 'snapshot' ? 1 : Number(cursor.slice('page-'.length)) + 1;
      return { items: [], next_cursor: `page-${next}`, has_more: next < 102 };
    };
    await expect(f.runtime.unlock('')).rejects.toMatchObject({ code: 'CAPACITY_EXCEEDED' });
    expect(f.runtime.snapshot()).toMatchObject({ unlocked: false, cursor: 'page-100' });
    await f.runtime.unlock('');
    expect(cursors.slice(99)).toEqual(['page-99', 'page-100', 'page-101']);
    expect(f.runtime.snapshot()).toMatchObject({ unlocked: true, cursor: 'page-102' });
  });

  it('locks an open view after cursor expiry and rebuilds only during a fresh unlock', async () => {
    const f = fixture(); await f.runtime.unlock(''); await f.runtime.enqueue(operation(), ancestry, 1);
    f.transport.changes = async cursor => {
      if (cursor === 'consumed') throw new SyncError('CURSOR_EXPIRED');
      return { items: [], next_cursor: 'fresh', has_more: false };
    };
    await expect(f.runtime.flush()).rejects.toMatchObject({ code: 'CURSOR_EXPIRED' });
    expect(f.runtime.snapshot()).toMatchObject({ unlocked: false, cursor: null });
    expect(f.cache.records.size).toBe(1); expect(f.received).toHaveLength(0);
    f.setProjections([]); await f.runtime.unlock('');
    expect(f.runtime.snapshot().pending[0].state).toBe('restricted'); expect(f.cache.records.size).toBe(1);
  });

  it('applies deletion before unlocking and retains unique drafts', async () => {
    const f = fixture(); await f.runtime.unlock(''); await f.runtime.enqueue(operation(), ancestry, 1);
    f.transport.changes = async () => ({ items: [{ event_id: id(401), object_type: 'deletion', object_id: id(400), revision: 1, deleted: true }], next_cursor: 'deleted', has_more: false });
    await f.runtime.unlock(''); expect(f.cache.records.size).toBe(1); expect(f.runtime.snapshot().pending[0].state).toBe('restricted');
    expect(f.cache.events.lastIndexOf('delete')).toBeLessThan(f.cache.events.lastIndexOf('unlock'));
  });

  it('keeps the editor input responsibility explicit when persistence fails', async () => {
    const f = fixture(); await f.runtime.unlock(''); await f.runtime.enqueue(operation(), ancestry, 1); f.cache.denied = true;
    await expect(f.runtime.enqueue(operation(2), ancestry, 2)).rejects.toMatchObject({ code: 'QUOTA_EXCEEDED' });
    expect(f.cache.records.size).toBe(1); expect(f.runtime.snapshot().pending).toHaveLength(1);
  });

  it('refuses malformed bootstrap and unknown operation kinds', async () => {
    const f = fixture(); f.transport.bootstrap = async () => ({ projection_complete: false } as unknown as S['SyncBootstrap']);
    await expect(f.runtime.unlock('')).rejects.toMatchObject({ code: 'INCOMPATIBLE_CLIENT' });
    const other = fixture(); await other.runtime.unlock('');
    await expect(other.runtime.enqueue({ ...operation(), object_type: 'unknown' } as unknown as SyncOperation, ancestry, 1)).rejects.toMatchObject({ code: 'INVALID_SYNC_OPERATION' });
  });

  it('never reopens after an external lock arrives during authority fetch', async () => {
    const f = fixture(); const original = f.transport.bootstrap;
    f.transport.bootstrap = async () => { f.cache.lock(); return original(); };
    await expect(f.runtime.unlock('')).rejects.toMatchObject({ code: 'LOCKED' }); expect(f.cache.events).not.toContain('unlock');
  });

  it('does not discard a newer snapshot projection when an older event arrives', async () => {
    const f = fixture(); f.setProjections([{ ...projection(), revision: 3 }]);
    f.transport.changes = async () => ({ items: [{ event_id: id(600), object_type: 'course', object_id: id(100), revision: 2, deleted: false }], next_cursor: 'older-event', has_more: false });
    await f.runtime.unlock(''); await f.runtime.enqueue(operation(), ancestry, 1); expect(f.cache.records.size).toBe(1);
  });

  it.each(['use_server', 'edited_merge', 'copy_to_new_note'] as const)('persists and confirms explicit %s resolution', async resolution => {
    const f = fixture(); await f.runtime.unlock(''); const reference = await f.runtime.enqueue(operation(), ancestry, 1);
    f.receipts.set(id(1), await receipt(operation(), 'conflict')); await f.runtime.flush();
    const body: SyncResolution = { base_revision: 2, resolution };
    if (resolution === 'copy_to_new_note') body.destination_class_session_id = id(101);
    if (resolution === 'edited_merge') body.merged_operation = { ...operation(3), base_revision: 2, payload: { base_revision: 2, name: 'merged' } };
    await f.runtime.resolve(reference, body, 2); expect(f.cache.records.size).toBe(0);
  });

  it('recovers a lost resolution response without resending the resolution', async () => {
    const f = fixture(); await f.runtime.unlock(''); const reference = await f.runtime.enqueue(operation(), ancestry, 1);
    f.receipts.set(id(1), await receipt(operation(), 'conflict')); await f.runtime.flush();
    const resolve = f.transport.resolve; let calls = 0;
    f.transport.resolve = async (...args) => { calls++; await resolve(...args); throw new SyncError('RESULT_UNKNOWN'); };
    await f.runtime.resolve(reference, { base_revision: 2, resolution: 'use_server' }, 2); expect(f.cache.records.size).toBe(2);
    await f.runtime.flush(); expect(f.cache.records.size).toBe(0); expect(calls).toBe(1);
  });

  it('explicitly rechecks a second CAS conflict and retains the submitted merge until confirmation', async () => {
    const f = fixture(); await f.runtime.unlock(''); const reference = await f.runtime.enqueue(operation(), ancestry, 1);
    f.receipts.set(id(1), await receipt(operation(), 'conflict')); await f.runtime.flush();
    const resolve = f.transport.resolve;
    f.transport.resolve = async () => { throw new SyncError('REVISION_CONFLICT'); };
    await f.runtime.resolve(reference, { base_revision: 2, resolution: 'edited_merge', merged_operation: { ...operation(3), base_revision: 2, payload: { base_revision: 2, name: 'unique merged draft' } } }, 2);
    expect(f.cache.records.size).toBe(2);
    let newest = operation();
    f.transport.push = async op => { newest = structuredClone(op); const value = await receipt(op, 'conflict'); f.receipts.set(op.op_id, value); return { receipts: [value], cursor: 'hint' }; };
    const conflict = f.transport.conflict;
    f.transport.conflict = async () => ({ ...await conflict(id(300)), local_operation: newest, server_revision: newest.op_id === id(1) ? 2 : 3 });
    const next = await f.runtime.recheckConflict(reference, 3);
    expect((await f.runtime.readDraft(next)).payload).toMatchObject({ name: 'unique merged draft' });
    expect(f.cache.records.size).toBe(3);
    f.transport.resolve = resolve;
    await f.runtime.resolve(next, { base_revision: 3, resolution: 'use_server' }, 4);
    expect(f.cache.records.size).toBe(0);
  });

  it('does not create a new operation to recheck an unknown submission', async () => {
    const f = fixture(); await f.runtime.unlock(''); const reference = await f.runtime.enqueue(operation(), ancestry, 1);
    await expect(f.runtime.recheckConflict(reference, 2)).rejects.toMatchObject({ code: 'CONFLICT_UNAVAILABLE' });
    expect(f.cache.records.size).toBe(1); expect(f.received).toHaveLength(0);
  });

  it('locks on background deletion while retaining the encrypted queue', async () => {
    const f = fixture(); await f.runtime.unlock(''); await f.runtime.enqueue(operation(), ancestry, 1);
    f.transport.changes = async () => ({ items: [{ event_id: id(401), object_type: 'deletion', object_id: id(400), revision: 1, deleted: true }], next_cursor: 'deleted', has_more: false });
    await f.runtime.pollChanges(); expect(f.runtime.snapshot().unlocked).toBe(false); expect(f.cache.records.size).toBe(1);
  });

  it('retains the current key deadline on a network outage and locks on definitive rejection', async () => {
    const f = fixture(); await f.runtime.unlock('');
    f.transport.changes = async () => { throw new SyncError('STORAGE_UNAVAILABLE'); };
    await f.runtime.pollChanges(); expect(f.runtime.snapshot().unlocked).toBe(true);
    f.transport.changes = async () => { throw new SyncError('FORBIDDEN'); };
    await f.runtime.pollChanges(); expect(f.runtime.snapshot().unlocked).toBe(false);
  });

  it('refreshes ordinary changed metadata without rebasing or losing a newer local generation', async () => {
    const f = fixture(); await f.runtime.unlock(''); const reference = await f.runtime.enqueue(operation(2), ancestry, 2);
    f.setProjections([{ ...projection(), revision: 2 }, projection(id(101), 'class_session')]);
    f.transport.changes = async () => ({ items: [{ event_id: id(700), object_type: 'course', object_id: id(100), revision: 2, deleted: false }], next_cursor: 'newer-metadata', has_more: false });
    await f.runtime.pollChanges(); expect(f.runtime.snapshot().unlocked).toBe(true);
    const saved = await f.runtime.readDraft(reference); expect(saved.base_revision).toBe(1); expect(saved.payload).toMatchObject({ name: '课程 2' });
  });

  it('does not skip revocation that arrives while changed metadata is being refreshed', async () => {
    const f = fixture(); await f.runtime.unlock(''); await f.runtime.enqueue(operation(), ancestry, 1);
    const bootstrap = f.transport.bootstrap;
    f.transport.bootstrap = async () => ({ ...await bootstrap(), watermark: 'after-revocation', projections: [{ ...projection(id(101), 'class_session'), revision: 2 }] });
    const cursors: string[] = [];
    f.transport.changes = async cursor => {
      cursors.push(cursor);
      if (cursor === 'consumed') return { items: [{ event_id: id(705), object_type: 'class_session', object_id: id(101), revision: 2, deleted: false }], next_cursor: 'before-revocation', has_more: false };
      return { items: cursor === 'before-revocation' ? [{ event_id: id(706), object_type: 'course' as const, object_id: id(100), revision: 1, deleted: true }] : [], next_cursor: 'after-revocation', has_more: false };
    };
    await f.runtime.pollChanges();
    expect(cursors).toEqual(['consumed', 'before-revocation']); expect(f.cache.events).toContain('delete');
    expect(f.runtime.snapshot().unlocked).toBe(false); expect(f.cache.records.size).toBe(1); expect(f.received).toHaveLength(0);
  });

  it('tracks draft and learning authorization separately and preserves the draft grant when adding learning', async () => {
    const f = fixture(); await f.runtime.unlockLearning('');
    expect(f.runtime.snapshot().unlocked).toBe(false); expect(f.runtime.snapshot().unlockedScopes).toEqual(['learning']);
    await expect(f.runtime.enqueue(operation(), ancestry, 1)).rejects.toMatchObject({ code: 'LOCKED' });
    await f.runtime.unlock(''); const draft = await f.runtime.enqueue(operation(), ancestry, 1);
    await f.runtime.unlockLearning(''); expect(f.runtime.snapshot().unlockedScopes).toEqual(['drafts', 'learning']);
    expect(await f.runtime.readDraft(draft)).toEqual(operation());
  });

  it('requires exact authorized cloud hydration before persisting learning content', async () => {
    const f = fixture(); await f.runtime.unlockLearning('');
    const reference: LearningReference = { scope: 'learning', objectType: 'course', objectId: id(100), revision: 1, generation: 1, ancestry };
    await expect(f.runtime.cacheLearning(reference, async () => ({ objectId: id(100), revision: 2, bytes: new TextEncoder().encode('wrong') }))).rejects.toMatchObject({ code: 'INVALID_HYDRATION' });
    expect(f.cache.records.size).toBe(0);
    await f.runtime.cacheLearning(reference, async () => ({ objectId: id(100), revision: 1, bytes: new TextEncoder().encode('cloud') }));
    expect(new TextDecoder().decode((await f.runtime.readLearning(reference))!)).toBe('cloud');
    await expect(f.runtime.readLearning({ ...reference, objectType: 'review_document' })).rejects.toMatchObject({ code: 'AUTHORITY_UNAVAILABLE' });
  });

  it('invalidates learning on a normal update while retaining pending personal input', async () => {
    const f = fixture(); await f.runtime.unlock(''); const draft = await f.runtime.enqueue(operation(), ancestry, 1); await f.runtime.unlockLearning('');
    const reference: LearningReference = { scope: 'learning', objectType: 'course', objectId: id(100), revision: 1, generation: 1, ancestry };
    await f.runtime.cacheLearning(reference, async () => ({ objectId: id(100), revision: 1, bytes: new TextEncoder().encode('old cloud') }));
    f.setProjections([{ ...projection(), revision: 2 }, projection(id(101), 'class_session')]);
    f.transport.changes = async () => ({ items: [{ event_id: id(700), object_type: 'course', object_id: id(100), revision: 2, deleted: false }], next_cursor: 'newer', has_more: false });
    await f.runtime.pollChanges(); await expect(f.runtime.readLearning(reference)).rejects.toMatchObject({ code: 'AUTHORITY_UNAVAILABLE' });
    expect(await f.runtime.readDraft(draft)).toEqual(operation());
    expect(await f.runtime.readLearning({ ...reference, revision: 2 })).toBeNull();
  });

  it('treats a source-derived document deletion as persistent revocation and retains the draft ciphertext', async () => {
    const f = fixture(); await f.runtime.unlock(''); await f.runtime.enqueue(operation(), ancestry, 1);
    f.transport.changes = async () => ({ items: [{ event_id: id(702), object_type: 'review_document', object_id: id(100), revision: 1, deleted: true }], next_cursor: 'source-revoked', has_more: false });
    await f.runtime.pollChanges(); expect(f.runtime.snapshot().unlockedScopes).toEqual([]); expect(f.cache.records.size).toBe(1);
  });

  it('keeps a partially revoked source draft restricted after a fresh unlock and runtime reload', async () => {
    const f = fixture(); f.setProjections([projection(id(100), 'review_document'), projection(id(101), 'class_session')]);
    await f.runtime.unlock(''); await f.runtime.readReviewRevision(id(100), id(800));
    const reference = await f.runtime.enqueue(personalOperation(), ancestry, 1);
    f.receipts.set(id(1), await receipt(personalOperation(), 'conflict'));
    f.transport.conflict = async () => ({ id: id(300), object_id: id(100), base_revision: 1, server_revision: 2, local_operation: personalOperation(), server_content: reviewRevision(id(800)), resolved: false });
    await f.runtime.flush(); expect((await f.runtime.inspectConflict(reference)).server_content).toMatchObject({ body_markdown: 'SOURCE B PRIVATE BODY' });
    f.transport.reviewRevision = async () => { throw new SyncError('NOT_FOUND'); };
    f.transport.conflict = async () => { throw new SyncError('NOT_FOUND'); };
    f.transport.changes = async () => ({ items: [{ event_id: id(820), object_type: 'review_document', object_id: id(100), revision: 1, deleted: false }], next_cursor: 'partial-revocation', has_more: false });
    await f.runtime.pollChanges(); expect(f.runtime.snapshot().unlocked).toBe(false); expect(f.cache.events).not.toContain('delete');
    f.transport.changes = async () => ({ items: [], next_cursor: 'partial-revocation', has_more: false });
    await f.runtime.unlock(''); expect(f.runtime.snapshot().pending[0].state).toBe('restricted');
    await expect(f.runtime.readDraft(reference)).rejects.toMatchObject({ code: 'AUTHORITY_UNAVAILABLE' });
    f.runtime.close(); const next = new OfflineSyncRuntime(f.cache, f.transport, { enabled: true }); await next.unlock('');
    await expect(next.readDraft(reference)).rejects.toMatchObject({ code: 'AUTHORITY_UNAVAILABLE' });
    expect(next.snapshot().pending[0].state).toBe('restricted'); expect(f.cache.records.size).toBe(1); expect(f.received).toHaveLength(0);
  });

  it('replays only the exact personal write to recover a reauthorized comparison', async () => {
    const f = fixture(); f.setProjections([projection(id(100), 'review_document')]);
    const op = personalOperation();
    await f.runtime.unlock('');
    const reference = await f.runtime.enqueue(op, ancestry, 4);
    const saved = structuredClone(f.cache.records.get(key(reference)));
    const originalReceipt = await receipt(op, 'conflict');
    f.receipts.set(op.op_id, originalReceipt);
    let reauthorized = false;
    let recovered = false;
    let detailReads = 0;
    const pushes: [SyncOperation, string][] = [];
    f.transport.conflict = async () => {
      detailReads++;
      if (!reauthorized) throw new AuthError('NOT_FOUND', 404);
      if (!recovered) throw new AuthError('INCOMPATIBLE_CLIENT', 409);
      return { id: id(300), object_id: id(100), base_revision: 1, server_revision: 2,
        server_content: reviewRevision(id(801)), local_operation: op, resolved: false };
    };
    f.transport.push = async (value, batchId) => {
      pushes.push([structuredClone(value), batchId]);
      recovered = true;
      return { receipts: [originalReceipt], cursor: 'unconsumed-server-hint' };
    };

    await f.runtime.flush();
    await expect(f.runtime.inspectConflict(reference)).rejects.toMatchObject({ code: 'AUTHORITY_UNAVAILABLE' });
    expect(pushes).toEqual([]);
    expect(f.cache.records.get(key(reference))).toEqual(saved);
    reauthorized = true;
    await f.runtime.unlock(''); await f.runtime.flush();
    expect(pushes).toEqual([]);
    await expect(f.runtime.inspectConflict(reference)).resolves.toMatchObject({
      id: originalReceipt.conflict_id, local_operation: op, server_content: { id: id(801) }, server_revision: 2,
    });
    expect(pushes).toEqual([[op, op.op_id]]);
    expect(detailReads).toBe(3);
    expect(f.runtime.snapshot()).toMatchObject({ cursor: 'consumed', pending: [{ reference, state: 'conflict' }] });
    expect(f.cache.records.get(key(reference))).toEqual(saved);
  });

  it('does not fetch or acknowledge a repaired comparison if authorization changes during the replay', async () => {
    const op = personalOperation();
    const f = await editableConflict(op);
    let detailReads = 0;
    f.transport.conflict = async () => { detailReads++; throw new AuthError('INCOMPATIBLE_CLIENT', 409); };
    f.transport.push = async () => { f.cache.lock(); return { receipts: [await receipt(op, 'conflict')], cursor: 'late' }; };
    await expect(f.runtime.inspectConflict(f.reference)).rejects.toMatchObject({ code: 'LOCKED' });
    expect(detailReads).toBe(1);
    expect(f.runtime.snapshot()).toMatchObject({ unlocked: false, cursor: 'consumed' });
    expect(f.cache.records.get(key(f.reference))?.reference).toEqual(f.reference);
  });

  it('never acknowledges a legacy comparison replay with a mismatched conflict receipt', async () => {
    const op = personalOperation();
    const f = await editableConflict(op);
    let detailReads = 0;
    f.transport.conflict = async () => { detailReads++; throw new AuthError('INCOMPATIBLE_CLIENT', 409); };
    f.transport.push = async () => ({ receipts: [await receipt(op, 'applied')], cursor: 'unconsumed-server-hint' });
    await expect(f.runtime.inspectConflict(f.reference)).rejects.toMatchObject({ code: 'INVALID_RECEIPT' });
    expect(detailReads).toBe(1);
    expect(f.cache.records.get(key(f.reference))?.reference).toEqual(f.reference);
  });

  it('uses exact authority only for its current unlock epoch and permits previously authorized offline drafts', async () => {
    const f = fixture(); f.setProjections([projection(id(100), 'review_document')]); await f.runtime.unlock('');
    await f.runtime.readReviewRevision(id(100), id(800));
    f.transport.reviewRevision = async () => { throw new SyncError('STORAGE_UNAVAILABLE'); };
    const reference = await f.runtime.enqueue(personalOperation(), ancestry, 1);
    expect((await f.runtime.readDraft(reference)).payload).toMatchObject({ body_markdown: 'SOURCE B PRIVATE DRAFT' });
    f.cache.lock(); await f.runtime.unlock('');
    await expect(f.runtime.readDraft(reference)).rejects.toMatchObject({ code: 'STORAGE_UNAVAILABLE' }); expect(f.cache.records.size).toBe(1);
  });

  it('requires a verified note document for null-base personal drafts', async () => {
    const f = fixture(); f.setProjections([projection(id(100), 'review_document')]); await f.runtime.unlock('');
    const document = f.transport.document;
    f.transport.document = async id => ({ ...await document(id), kind: 'summary' });
    await expect(f.runtime.enqueue(personalOperation(null), ancestry, 1)).rejects.toMatchObject({ code: 'AUTHORITY_UNAVAILABLE' });
    expect(f.cache.records.size).toBe(0); f.transport.document = document;
    await f.runtime.enqueue(personalOperation(null), ancestry, 1); expect(f.cache.records.size).toBe(1);
  });

  it('rejects revision identity mismatch and a late exact-authorization response after locking', async () => {
    const f = fixture(); f.setProjections([projection(id(100), 'review_document')]); await f.runtime.unlock('');
    f.transport.reviewRevision = async revisionId => ({ ...reviewRevision(revisionId), document_id: id(999) });
    await expect(f.runtime.enqueue(personalOperation(), ancestry, 1)).rejects.toMatchObject({ code: 'INCOMPATIBLE_CLIENT' });
    f.transport.reviewRevision = async revisionId => { f.cache.lock(); return reviewRevision(revisionId); };
    await expect(f.runtime.enqueue(personalOperation(), ancestry, 1)).rejects.toMatchObject({ code: 'LOCKED' }); expect(f.cache.records.size).toBe(0);
  });

  it('invalidates an in-flight exact revision read before it can deliver a revoked body', async () => {
    const f = fixture(); f.setProjections([projection(id(100), 'review_document')]); await f.runtime.unlock('');
    let finish!: (revision: S['ReviewRevision']) => void;
    f.transport.reviewRevision = async () => new Promise(resolve => { finish = resolve; });
    const pending = f.runtime.readReviewRevision(id(100), id(800));
    f.transport.changes = async () => ({ items: [{ event_id: id(822), object_type: 'review_document', object_id: id(100), revision: 1, deleted: false }], next_cursor: 'partial-revocation', has_more: false });
    await f.runtime.pollChanges(); finish(reviewRevision(id(800)));
    await expect(pending).rejects.toMatchObject({ code: 'LOCKED' }); expect(f.runtime.snapshot().unlocked).toBe(false);
  });

  it('reauthorizes the exact frozen conflict before reopening an unconfirmed resolution envelope', async () => {
    const f = fixture(); await f.runtime.unlock(''); const original = await f.runtime.enqueue(operation(), ancestry, 1);
    f.receipts.set(id(1), await receipt(operation(), 'conflict')); await f.runtime.flush();
    f.transport.resolve = async () => { throw new SyncError('RESULT_UNKNOWN'); };
    await f.runtime.resolve(original, { base_revision: 2, resolution: 'use_server' }, 2);
    const resolution = f.runtime.snapshot().pending.find(value => value.reference.generation === 2)!.reference;
    f.cache.lock(); f.transport.conflict = async () => { throw new SyncError('NOT_FOUND'); };
    await f.runtime.unlock('');
    expect(f.runtime.snapshot().pending.find(value => value.reference.generation === 2)?.state).toBe('restricted');
    await expect(f.runtime.readDraft(resolution)).rejects.toMatchObject({ code: 'AUTHORITY_UNAVAILABLE' }); expect(f.cache.records.size).toBe(2);
  });

  it('requires exact review-body authority and never rehydrates revoked learning content', async () => {
    const f = fixture(); f.setProjections([projection(id(100), 'review_document')]); await f.runtime.unlockLearning('');
    const reference: LearningReference = { scope: 'learning', objectType: 'review_document', objectId: id(100), revision: 1, generation: 1, ancestry, contentRevisionId: id(800) };
    await expect(f.runtime.cacheLearning({ ...reference, contentRevisionId: undefined }, async () => ({ objectId: id(100), revision: 1, bytes: new Uint8Array() }))).rejects.toMatchObject({ code: 'AUTHORITY_UNAVAILABLE' });
    await expect(f.runtime.cacheLearning(reference, async () => ({ objectId: id(100), revision: 1, contentRevisionId: id(801), bytes: new Uint8Array() }))).rejects.toMatchObject({ code: 'INVALID_HYDRATION' });
    await f.runtime.cacheLearning(reference, async () => ({ objectId: id(100), revision: 1, contentRevisionId: id(800), bytes: new TextEncoder().encode('SOURCE B PRIVATE BODY') }));
    f.transport.reviewRevision = async () => { throw new SyncError('NOT_FOUND'); };
    f.transport.changes = async () => ({ items: [{ event_id: id(821), object_type: 'review_document', object_id: id(100), revision: 1, deleted: false }], next_cursor: 'partial-revocation', has_more: false });
    await f.runtime.pollChanges(); expect(f.runtime.snapshot().unlockedScopes).toEqual([]);
    f.transport.changes = async () => ({ items: [], next_cursor: 'partial-revocation', has_more: false });
    await f.runtime.unlockLearning(''); await expect(f.runtime.readLearning(reference)).rejects.toMatchObject({ code: 'AUTHORITY_UNAVAILABLE' });
    let hydrated = false;
    await expect(f.runtime.cacheLearning(reference, async () => { hydrated = true; return { objectId: id(100), revision: 1, bytes: new Uint8Array() }; })).rejects.toMatchObject({ code: 'AUTHORITY_UNAVAILABLE' });
    expect(hydrated).toBe(false);
  });

  it('stores only the latest editor snapshot without altering or sending the frozen original operation', async () => {
    const f = await editableConflict();
    await f.runtime.saveConflictDraft(f.reference, 'first', 'first note', id(900), 1);
    await f.runtime.saveConflictDraft(f.reference, 'unique latest body', 'unique latest note', id(900), 2);
    await f.runtime.awaitEditorDraftSaves(); await f.runtime.flush();
    expect(f.cache.records.size).toBe(2); expect(f.runtime.snapshot().pending).toHaveLength(1); expect(f.received).toHaveLength(0);
    expect(await f.runtime.readDraft(f.reference)).toEqual(operation());
    expect(await f.runtime.readConflictDrafts(f.reference)).toMatchObject([{ body: 'unique latest body', note: 'unique latest note' }]);
  });

  it('restores unsent merge input after an ordinary remote event locks the view', async () => {
    const f = await editableConflict(personalOperation());
    await f.runtime.saveConflictDraft(f.reference, 'UNSUBMITTED UNIQUE MERGE', 'UNSUBMITTED UNIQUE NOTE', id(900), 1);
    f.transport.changes = async () => ({ items: [{ event_id: id(822), object_type: 'review_document', object_id: id(100), revision: 1, deleted: false }], next_cursor: 'ordinary-update', has_more: false });
    await f.runtime.pollChanges();
    await expect(f.runtime.readConflictDrafts(f.reference)).rejects.toMatchObject({ code: 'LOCKED' });
    f.transport.changes = async () => ({ items: [], next_cursor: 'ordinary-update', has_more: false });
    await f.runtime.unlock(''); await f.runtime.flush();
    expect(await f.runtime.readConflictDrafts(f.reference)).toMatchObject([{ body: 'UNSUBMITTED UNIQUE MERGE', note: 'UNSUBMITTED UNIQUE NOTE' }]);
    expect(f.runtime.snapshot().pending).toHaveLength(1); expect(f.received).toHaveLength(0);
  });

  it('retains unsent merge ciphertext but refuses recovery after exact source revocation', async () => {
    const f = await editableConflict(personalOperation());
    await f.runtime.saveConflictDraft(f.reference, 'B SOURCE UNIQUE MERGE', 'B SOURCE UNIQUE NOTE', id(900), 1);
    f.cache.lock(); f.transport.reviewRevision = async () => { throw new SyncError('NOT_FOUND'); };
    await f.runtime.unlock('');
    await expect(f.runtime.readConflictDrafts(f.reference)).rejects.toMatchObject({ code: 'AUTHORITY_UNAVAILABLE' });
    expect(f.cache.records.size).toBe(2); expect(f.runtime.snapshot().pending[0].state).toBe('restricted');
  });

  it('keeps independent editor branches for explicit recovery and never automatically flushes them', async () => {
    const f = await editableConflict();
    await f.runtime.saveConflictDraft(f.reference, 'TAB A UNIQUE', 'note A', id(900), 1);
    await f.runtime.saveConflictDraft(f.reference, 'TAB B UNIQUE', 'note B', id(901), 1);
    await f.runtime.flush();
    expect((await f.runtime.readConflictDrafts(f.reference)).map(value => value.body).sort()).toEqual(['TAB A UNIQUE', 'TAB B UNIQUE']);
    expect(f.received).toHaveLength(0); expect(f.runtime.snapshot().pending).toHaveLength(1); expect(f.cache.records.size).toBe(3);
  });

  it('keeps a later editor generation when an old explicit resolution receipt arrives', async () => {
    const f = await editableConflict(personalOperation(null));
    const first = await f.runtime.saveConflictDraft(f.reference, 'submitted merge', 'submitted note', id(900), 1);
    const resolve = f.transport.resolve;
    let finish!: () => void; let started!: () => void;
    const sending = new Promise<void>(accept => { started = accept; });
    f.transport.resolve = async (...args) => { started(); await new Promise<void>(accept => { finish = accept; }); return resolve(...args); };
    const local = personalOperation(null);
    const resolving = f.runtime.resolve(f.reference, { base_revision: 2, resolution: 'edited_merge', merged_operation: { ...local, op_id: id(3), base_revision: 2,
      payload: { ...local.payload, base_revision: 2, body_markdown: 'submitted merge', personal_note: 'submitted note' } } as SyncOperation }, 2, [first]);
    await sending;
    const savedResolution = [...f.cache.records.values()].map(value => JSON.parse(new TextDecoder().decode(value.bytes))).find(value => value.kind === 'resolve');
    expect(savedResolution.recoveryReferences).toEqual([first]);
    const newer = await f.runtime.saveConflictDraft(f.reference, 'LATER UNIQUE MERGE', 'LATER UNIQUE NOTE', id(900), 2);
    finish(); await resolving;
    expect(f.cache.records.size).toBe(1); expect(await f.cache.read(newer)).not.toBeNull();
    expect(await f.runtime.readConflictDrafts(f.reference)).toMatchObject([{ body: 'LATER UNIQUE MERGE', note: 'LATER UNIQUE NOTE' }]);
    expect(f.runtime.snapshot().pending).toEqual([]);
  });

  it('discovers orphan recovery in a fresh runtime without a retained source reference', async () => {
    const f = await editableConflict();
    const saved = await f.runtime.saveConflictDraft(f.reference, 'UNSUBMITTED ORPHAN', 'ORPHAN NOTE', id(900), 1);
    await f.runtime.resolve(f.reference, { base_revision: 2, resolution: 'use_server' }, 2, [saved]);
    f.runtime.close(); const next = new OfflineSyncRuntime(f.cache, f.transport, { enabled: true }); await next.unlock('');
    expect(next.snapshot().pending).toEqual([]);
    const sources = await next.listConflictDraftSources(); expect(sources).toHaveLength(1); expect(sources[0].state).toBe('available');
    expect(await next.readConflictDrafts(sources[0].source!)).toMatchObject([{ body: 'UNSUBMITTED ORPHAN', note: 'ORPHAN NOTE' }]);
  });

  it('does not let one tombstoned recovery hide another authorized branch from discovery', async () => {
    const f = await editableConflict();
    const deleted = await f.runtime.saveConflictDraft(f.reference, 'RESTRICTED BRANCH', '', id(900), 1);
    await f.runtime.saveConflictDraft(f.reference, 'AVAILABLE BRANCH', '', id(901), 1);
    const read = f.cache.read.bind(f.cache);
    f.cache.read = async reference => { if (reference.objectId === deleted.objectId) throw new SyncError('GONE'); return read(reference); };
    f.runtime.close(); const next = new OfflineSyncRuntime(f.cache, f.transport, { enabled: true }); await next.unlock('');
    const sources = await next.listConflictDraftSources();
    expect(sources.map(value => value.state).sort()).toEqual(['available', 'restricted']);
    expect(sources.find(value => value.state === 'restricted')?.source).toBeNull();
    const available = sources.find(value => value.state === 'available')?.source;
    expect(await next.readConflictDrafts(available!)).toMatchObject([{ body: 'AVAILABLE BRANCH' }]);
  });

  it('reports failed editor persistence without acknowledging it as saved', async () => {
    const f = await editableConflict();
    await f.runtime.saveConflictDraft(f.reference, 'saved earlier', 'saved note', id(900), 1); f.cache.denied = true;
    await expect(f.runtime.saveConflictDraft(f.reference, 'unsaved later', 'unsaved note', id(900), 2)).rejects.toMatchObject({ code: 'QUOTA_EXCEEDED' });
    await expect(f.runtime.awaitEditorDraftSaves()).rejects.toMatchObject({ code: 'QUOTA_EXCEEDED' });
    expect(await f.runtime.readConflictDrafts(f.reference)).toMatchObject([{ body: 'saved earlier' }]);
  });

  it('tracks write-only encrypted recovery completion across a lock without reopening the view', async () => {
    const f = await editableConflict();
    const save = f.cache.saveRecoveryDraft.bind(f.cache);
    let finish!: () => void;
    f.cache.saveRecoveryDraft = (reference, bytes) => {
      const copy = new Uint8Array(bytes);
      return new Promise<void>(resolve => { finish = () => { void save(reference, copy).then(resolve); copy.fill(0); }; });
    };
    const saving = f.runtime.saveConflictDraft(f.reference, 'RACING UNIQUE INPUT', 'RACING UNIQUE NOTE', id(900), 1);
    f.cache.lock(); finish(); await saving; await f.runtime.awaitEditorDraftSaves();
    expect(f.runtime.snapshot().unlocked).toBe(false); expect(f.cache.records.size).toBe(2);
    await f.runtime.unlock(''); await f.runtime.flush();
    expect(await f.runtime.readConflictDrafts(f.reference)).toMatchObject([{ body: 'RACING UNIQUE INPUT', note: 'RACING UNIQUE NOTE' }]);
  });
});
