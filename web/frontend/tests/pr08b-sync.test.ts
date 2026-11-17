import { describe, expect, it } from 'vitest';
import type { components } from '../src/generated/openapi';
import type { CacheAncestry, CacheReference, CacheWrite, DeletionNotice } from '../src/cache/types';
import { syncFingerprint } from '../src/sync/client';
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

class MemoryCache extends EventTarget implements SyncCache {
  records = new Map<string, { reference: CacheWrite; bytes: Uint8Array }>();
  events: string[] = [];
  denied = false;
  lock() { this.events.push('lock'); this.dispatchEvent(new Event('locked')); }
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
    resolve: async (conflictId, body, opId) => {
      const saved: SyncReceipt = { op_id: opId, operation: 'resolveConflict', request_sha256: await syncFingerprint({ conflict_id: conflictId, body }), status: 'applied', object_id: body.resolution === 'copy_to_new_note' ? id(900) : id(100), revision: 3, conflict_id: null, task_id: null, created_at: stamp };
      receipts.set(opId, saved); return saved;
    },
  };
  const runtime = new OfflineSyncRuntime(cache, transport, { enabled: true, newId: () => id(sequence++) });
  return { cache, runtime, transport, received, receipts, setProjections: (value: S['BootstrapProjection'][]) => { projections = value; } };
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
});
