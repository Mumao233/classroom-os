import canonicalize from 'canonicalize';
import { cacheTransaction, DATABASE_NAME } from './indexed-db';
import { CacheError, LEARNING_LIMITS, namespaceOf, recordId } from './types';
import type {
  AudioDescriptor, AudioReceipt, CacheAncestry, CacheGrant, CacheHeader, CacheIdentity,
  CacheOptions, CacheReference, CacheScope, CacheWrite, CipherRecord, DeletionNotice,
  NamespaceState, Tombstone,
} from './types';

const encoder = new TextEncoder();
const scopes: CacheScope[] = ['learning', 'drafts', 'recording_recovery'];
const positive = (value: number) => Number.isSafeInteger(value) && value > 0;
const equalBytes = (left: Uint8Array, right: Uint8Array) => left.length === right.length && left.every((value, index) => value === right[index]);
const aad = (header: CacheHeader) => encoder.encode(canonicalize(header) ?? '');
const slot = (scope: CacheScope, classroom: string | null) => JSON.stringify([scope, scope === 'recording_recovery' ? classroom : null]);
const recordingId = (descriptor: AudioDescriptor) => JSON.stringify([descriptor.class_session_id, descriptor.capture_epoch_id, descriptor.seq]);
type MemoryKey = { key: CryptoKey; id: string; version: number; deadline: number; wallDeadline: number; generation: number };
type Authorized = { localGeneration: number; namespace: string; identity: CacheIdentity; key: MemoryKey };

/** Production adapter. Only ciphertext and minimal routing metadata enter IndexedDB. */
export class TrustedCache extends EventTarget {
  private identity: CacheIdentity | null = null;
  private localGeneration = 0;
  private keys = new Map<string, MemoryKey>();
  private unlockAttempts = new Map<string, number>();
  private transactions = new Set<IDBTransaction>();
  private channel: BroadcastChannel | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private barrier: Promise<void> = Promise.resolve();
  private readonly databaseName: string;
  private readonly hide = () => this.lock();

  constructor(private readonly options: CacheOptions = {}) {
    super();
    this.databaseName = options.databaseName ?? DATABASE_NAME;
    if (options.enabled) {
      this.channel = new BroadcastChannel(`${this.databaseName}:invalidation`);
      this.channel.onmessage = () => this.lock();
      window.addEventListener('pagehide', this.hide);
    }
  }

  bind(identity: CacheIdentity | null) {
    if (JSON.stringify(identity) === JSON.stringify(this.identity)) return;
    this.lock();
    this.identity = identity ? { ...identity } : null;
  }

  private enabled(scope?: CacheScope) {
    if (!this.options.enabled || (scope === 'recording_recovery' && !this.options.recordingEnabled)) throw new CacheError('DISABLED');
  }

  lock() {
    this.localGeneration++;
    this.keys.clear();
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    for (const tx of this.transactions) {
      try { tx.abort(); } catch { /* The transaction may already have completed. */ }
    }
    this.dispatchEvent(new Event('locked'));
  }

  /** Called immediately on logout, account change, or known revocation, including failed logout. */
  invalidate(): Promise<void> {
    const identity = this.identity;
    this.lock();
    this.identity = null;
    this.channel?.postMessage('lock');
    if (!identity || !this.options.enabled) return this.barrier;
    const namespace = namespaceOf(identity);
    this.barrier = this.barrier.then(() => cacheTransaction<void>(this.databaseName, 'readwrite', (tx, done) => {
      const store = tx.objectStore('namespaces');
      const request = store.get(namespace);
      request.onsuccess = () => {
        const state = request.result as NamespaceState | undefined;
        store.put({ id: namespace, generation: (state?.generation ?? 0) + 1 });
        done();
      };
    }));
    // Retain a failed barrier. Reopening this instance must not silently bypass failed invalidation.
    void this.barrier.catch(() => this.dispatchEvent(new Event('storage-failed')));
    return this.barrier;
  }

  close() { this.lock(); this.channel?.close(); this.channel = null; window.removeEventListener('pagehide', this.hide); }

  private track = (tx: IDBTransaction) => {
    this.transactions.add(tx);
    return () => this.transactions.delete(tx);
  };

  private async namespaceGeneration(namespace: string): Promise<number> {
    return cacheTransaction<number>(this.databaseName, 'readonly', (tx, done) => {
      const request = tx.objectStore('namespaces').get(namespace);
      request.onsuccess = () => done((request.result as NamespaceState | undefined)?.generation ?? 0);
    });
  }

  /** The issuer performs authenticated, purpose-bound online reauthorization. Its result is never persisted. */
  async unlock(scope: CacheScope, classroom: string | null, issue: () => Promise<CacheGrant>) {
    this.enabled(scope);
    if (!this.identity || !scopes.includes(scope) || (scope === 'recording_recovery') !== (classroom !== null)) throw new CacheError('INVALID_INPUT');
    await this.barrier;
    if (!this.identity) throw new CacheError('LOCKED');
    const identity = { ...this.identity };
    const generation = this.localGeneration;
    const namespace = namespaceOf(identity);
    const keySlot = slot(scope, classroom);
    const attempt = (this.unlockAttempts.get(keySlot) ?? 0) + 1;
    this.unlockAttempts.set(keySlot, attempt);
    const persistedGeneration = await this.namespaceGeneration(namespace);
    const startedWall = Date.now();
    const started = performance.now();
    const grant = await issue();
    let material: Uint8Array<ArrayBuffer>;
    try { material = Uint8Array.from(atob(grant.key_material_b64), value => value.charCodeAt(0)); }
    catch { throw new CacheError('INVALID_GRANT'); }
    // Do not retain the service's raw material alongside the imported nonextractable key.
    grant.key_material_b64 = '';
    let key: CryptoKey;
    try {
      if (material.length !== 32 || grant.scope !== scope || !positive(grant.key_epoch)
        || !grant.key_id || !grant.cache_policy_revision || !Number.isFinite(Date.parse(grant.expires_at))) throw new CacheError('INVALID_GRANT');
      key = await crypto.subtle.importKey('raw', material, 'AES-GCM', false, ['encrypt', 'decrypt']);
    } finally { material.fill(0); }
    const wallDeadline = Date.parse(grant.expires_at);
    const deadline = started + (wallDeadline - startedWall);
    if (generation !== this.localGeneration || JSON.stringify(this.identity) !== JSON.stringify(identity)) throw new CacheError('STALE_OPERATION');
    if (performance.now() >= deadline || Date.now() >= wallDeadline) throw new CacheError('LOCKED');
    if (await this.namespaceGeneration(namespace) !== persistedGeneration) throw new CacheError('STALE_OPERATION');
    if (generation !== this.localGeneration || this.unlockAttempts.get(keySlot) !== attempt) throw new CacheError('STALE_OPERATION');
    this.keys.set(keySlot, { key, id: grant.key_id, version: grant.key_epoch, deadline, wallDeadline, generation: persistedGeneration });
    this.scheduleExpiry();
    this.dispatchEvent(new Event('unlocked'));
  }

  private scheduleExpiry() {
    if (this.timer) clearTimeout(this.timer);
    const deadline = Math.min(...[...this.keys.values()].map(key => key.deadline));
    this.timer = setTimeout(() => this.lock(), Math.max(0, Math.min(deadline - performance.now(), 2_147_483_647)));
  }

  private authorized(scope: CacheScope, classroom: string | null): Authorized {
    this.enabled(scope);
    const key = this.keys.get(slot(scope, classroom));
    if (!this.identity || !key) throw new CacheError('LOCKED');
    if (performance.now() >= key.deadline || Date.now() >= key.wallDeadline) { this.lock(); throw new CacheError('LOCKED'); }
    return { localGeneration: this.localGeneration, namespace: namespaceOf(this.identity), identity: { ...this.identity }, key };
  }

  private check(auth: Authorized) {
    if (auth.localGeneration !== this.localGeneration || !this.identity || namespaceOf(this.identity) !== auth.namespace) throw new CacheError('STALE_OPERATION');
    if (performance.now() >= auth.key.deadline || Date.now() >= auth.key.wallDeadline) { this.lock(); throw new CacheError('LOCKED'); }
  }

  private guard(tx: IDBTransaction, auth: Authorized, header: CacheHeader, next: () => void, fail: (error: CacheError) => void) {
    const state = tx.objectStore('namespaces').get(auth.namespace);
    state.onsuccess = () => {
      try { this.check(auth); } catch (error) { fail(error as CacheError); return; }
      if (((state.result as NamespaceState | undefined)?.generation ?? 0) !== auth.key.generation) { fail(new CacheError('STALE_OPERATION')); return; }
      const request = tx.objectStore('tombstones').getAll();
      request.onsuccess = () => {
        const deleted = (request.result as Tombstone[]).some(value => value.namespace === auth.namespace && this.matchesDeletion(header, value));
        if (deleted) fail(new CacheError('GONE'));
        else next();
      };
    };
  }

  private matchesDeletion(header: CacheHeader, tombstone: Tombstone): boolean {
    const { kind, objectId } = tombstone;
    return (kind === 'account' && objectId === header.ownerId)
      || (kind === 'semester' && objectId === header.ancestry.semesterId)
      || (kind === 'course' && objectId === header.ancestry.courseId)
      || (kind === 'class_session' && objectId === header.ancestry.classSessionId)
      || (kind === 'audio' && header.scope === 'recording_recovery' && objectId === header.ancestry.classSessionId)
      || objectId === header.objectId;
  }

  private validate(write: CacheWrite) {
    if (!scopes.includes(write.scope) || !write.objectId || write.objectId.length > 512 || !positive(write.revision)
      || !positive(write.generation) || !write.ancestry || (write.scope === 'drafts' && !write.operationId)
      || (write.scope === 'learning' && (!Number.isFinite(write.verifiedAt) || write.verifiedAt! > Date.now()))
      || (write.scope === 'recording_recovery' && !write.ancestry.classSessionId)) throw new CacheError('INVALID_INPUT');
  }

  private async encrypt(header: CacheHeader, plaintext: Uint8Array, auth: Authorized): Promise<CipherRecord> {
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const copy = new Uint8Array(plaintext);
    let ciphertext: ArrayBuffer;
    try { ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce, additionalData: aad(header) }, auth.key.key, copy); }
    finally { copy.fill(0); }
    this.check(auth);
    return { id: recordId(auth.namespace, header), namespace: auth.namespace, header, nonce, ciphertext,
      bytes: ciphertext.byteLength + nonce.byteLength + aad(header).byteLength, accessedAt: Date.now() };
  }

  private async decrypt(record: CipherRecord, auth: Authorized): Promise<Uint8Array<ArrayBuffer>> {
    this.check(auth);
    if (record.header.schema !== 1 && record.header.schema !== 2) throw new CacheError('UNSUPPORTED_SCHEMA');
    if (record.namespace !== auth.namespace || record.id !== recordId(auth.namespace, record.header)
      || record.header.ownerId !== auth.identity.ownerId || record.header.deviceId !== auth.identity.deviceId) throw new CacheError('CORRUPT_RECORD');
    if (record.header.keyId !== auth.key.id || record.header.keyVersion !== auth.key.version) throw new CacheError('KEY_REQUIRED');
    let plaintext: ArrayBuffer;
    try { plaintext = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: record.nonce, additionalData: aad(record.header) }, auth.key.key, record.ciphertext); }
    catch { throw new CacheError('CORRUPT_RECORD'); }
    const result = new Uint8Array(plaintext);
    try { this.check(auth); } catch (error) { result.fill(0); throw error; }
    return result;
  }

  async put(write: CacheWrite, plaintext: Uint8Array): Promise<void> {
    return this.store(write, plaintext, false);
  }

  private async store(write: CacheWrite, plaintext: Uint8Array, ordered: boolean): Promise<void> {
    const frozen = new Uint8Array(plaintext);
    try { await this.storeSnapshot(write, frozen, ordered); }
    finally { frozen.fill(0); }
  }

  private async storeSnapshot(write: CacheWrite, plaintext: Uint8Array, ordered: boolean): Promise<void> {
    const snapshot = structuredClone(write);
    this.validate(snapshot);
    const auth = this.authorized(snapshot.scope, snapshot.ancestry.classSessionId);
    const header: CacheHeader = { ...snapshot, ownerId: auth.identity.ownerId, deviceId: auth.identity.deviceId,
      keyId: auth.key.id, keyVersion: auth.key.version, schema: 2 };
    const record = await this.encrypt(header, plaintext, auth);
    const existing = await cacheTransaction<CipherRecord | undefined>(this.databaseName, 'readwrite', (tx, done, fail) => {
      this.guard(tx, auth, header, () => {
        const records = tx.objectStore('records');
        const request = records.getAll();
        request.onsuccess = () => {
          const all = request.result as CipherRecord[];
          const previous = all.find(value => value.id === record.id);
          if (previous) { done(previous); return; }
          const own = all.filter(value => value.namespace === auth.namespace);
          const versions = ordered ? own.filter(value => value.header.scope === header.scope && value.header.objectId === header.objectId) : [];
          if (versions.some(value => value.header.revision > header.revision
            || (value.header.revision === header.revision && value.header.generation >= header.generation))) { fail(new CacheError('REVISION_CONFLICT')); return; }
          try { this.makeRoom(tx, own.filter(value => !versions.includes(value)), record); }
          catch (error) { fail(error as CacheError); return; }
          for (const version of versions) records.delete(version.id);
          tx.objectStore('nonces').add({ id: `${record.header.keyId}:${Array.from(record.nonce).join('.')}`, namespace: auth.namespace });
          records.add(record);
          done(undefined);
        };
      }, fail);
    }, this.track);
    this.check(auth);
    if (existing) {
      if (canonicalize(existing.header) !== canonicalize(header)) throw new CacheError('REVISION_CONFLICT');
      const prior = await this.decrypt(existing, auth);
      try { if (!equalBytes(prior, plaintext)) throw new CacheError('REVISION_CONFLICT'); }
      finally { prior.fill(0); }
    }
  }

  private makeRoom(tx: IDBTransaction, own: CipherRecord[], incoming: CipherRecord) {
    const samePool = own.filter(value => value.header.scope === incoming.header.scope);
    if (incoming.header.scope !== 'learning') {
      const limits = this.options.limits;
      const maxBytes = incoming.header.scope === 'drafts' ? limits?.draftBytes : limits?.recordingBytes;
      if (!maxBytes || !positive(maxBytes) || samePool.reduce((sum, record) => sum + record.bytes, incoming.bytes) > maxBytes
        || (incoming.header.scope === 'drafts' && (!positive(limits!.draftOperations) || samePool.length >= limits!.draftOperations))) throw new CacheError('QUOTA_EXCEEDED');
      return;
    }
    if (incoming.bytes > LEARNING_LIMITS.bytes || Date.now() - incoming.header.verifiedAt! >= LEARNING_LIMITS.ageMs) throw new CacheError('QUOTA_EXCEEDED');
    const records = tx.objectStore('records');
    const active = samePool.filter(value => {
      if (!Number.isFinite(value.header.verifiedAt) || Date.now() - value.header.verifiedAt! >= LEARNING_LIMITS.ageMs) { records.delete(value.id); return false; }
      return true;
    });
    const currentClass = incoming.header.ancestry.classSessionId;
    const recent = new Map<string, number>();
    for (const value of [...active, incoming]) {
      const classroom = value.header.ancestry.classSessionId;
      if (classroom) recent.set(classroom, Math.max(recent.get(classroom) ?? 0, value.accessedAt));
    }
    const excess = [...recent].filter(([id]) => id !== currentClass).sort((a, b) => a[1] - b[1]).slice(0, Math.max(0, recent.size - LEARNING_LIMITS.classes));
    const discarded = new Set(excess.map(([id]) => id));
    const retained = active.filter(value => {
      if (value.header.ancestry.classSessionId && discarded.has(value.header.ancestry.classSessionId)) { records.delete(value.id); return false; }
      return true;
    }).sort((a, b) => a.accessedAt - b.accessedAt);
    let bytes = retained.reduce((sum, value) => sum + value.bytes, incoming.bytes);
    for (const value of retained) {
      if (bytes <= LEARNING_LIMITS.bytes) break;
      records.delete(value.id);
      bytes -= value.bytes;
    }
  }

  private async load(reference: CacheReference, classroom: string | null): Promise<{ record: CipherRecord; auth: Authorized } | null> {
    const snapshot = structuredClone(reference);
    const auth = this.authorized(snapshot.scope, classroom);
    const record = await cacheTransaction<CipherRecord | null>(this.databaseName, 'readwrite', (tx, done, fail) => {
      const records = tx.objectStore('records');
      const request = records.get(recordId(auth.namespace, snapshot));
      request.onsuccess = () => {
        const value = request.result as CipherRecord | undefined;
        if (!value) { done(null); return; }
        this.guard(tx, auth, value.header, () => {
          if (value.header.scope === 'learning' && Date.now() - value.header.verifiedAt! >= LEARNING_LIMITS.ageMs) {
            records.delete(value.id); done(null); return;
          }
          value.accessedAt = Date.now(); records.put(value); done(value);
        }, fail);
      };
    }, this.track);
    this.check(auth);
    return record ? { record, auth } : null;
  }

  async read(reference: CacheReference, classroom: string | null = null): Promise<Uint8Array<ArrayBuffer> | null> {
    const loaded = await this.load(reference, classroom);
    if (!loaded) return null;
    const { record, auth } = loaded;
    const plain = await this.decrypt(record, auth);
    try {
      await cacheTransaction<void>(this.databaseName, 'readonly', (tx, done, fail) => this.guard(tx, auth, record.header, () => done(), fail), this.track);
      this.check(auth);
      return plain;
    } catch (error) { plain.fill(0); throw error; }
  }

  async listPending(scope: 'drafts' | 'recording_recovery', classroom: string | null = null): Promise<CacheWrite[]> {
    const auth = this.authorized(scope, classroom);
    const records = await cacheTransaction<CipherRecord[]>(this.databaseName, 'readonly', (tx, done) => {
      const request = tx.objectStore('records').getAll();
      request.onsuccess = () => done((request.result as CipherRecord[]).filter(value => value.namespace === auth.namespace && value.header.scope === scope
        && (scope !== 'recording_recovery' || value.header.ancestry.classSessionId === classroom)));
    }, this.track);
    const result: CacheWrite[] = [];
    for (const record of records) {
      const content = await this.read(record.header, classroom);
      if (content) { content.fill(0); result.push(structuredClone(record.header)); }
    }
    this.check(auth);
    return result;
  }

  /** An ACK names the exact frozen operation and generation. Newer edits remain pending. */
  async acknowledgeDraft(reference: CacheReference, operationId: string): Promise<void> {
    if (reference.scope !== 'drafts') throw new CacheError('INVALID_INPUT');
    await this.removeAcknowledged(reference, null, header => header.operationId === operationId);
  }

  private async removeAcknowledged(reference: CacheReference, classroom: string | null, matches: (header: CacheHeader) => boolean) {
    const snapshot = structuredClone(reference);
    const auth = this.authorized(snapshot.scope, classroom);
    await cacheTransaction<void>(this.databaseName, 'readwrite', (tx, done, fail) => {
      const records = tx.objectStore('records');
      const request = records.get(recordId(auth.namespace, snapshot));
      request.onsuccess = () => {
        const value = request.result as CipherRecord | undefined;
        if (!value) { done(); return; }
        this.guard(tx, auth, value.header, () => {
          if (!matches(value.header)) { fail(new CacheError('REVISION_CONFLICT')); return; }
          records.delete(value.id); done();
        }, fail);
      };
    }, this.track);
    this.check(auth);
  }

  async applyDeletion(notice: DeletionNotice): Promise<void> {
    this.enabled();
    const identity = this.identity;
    if (!identity || !notice.object_id || !notice.deletion_id) throw new CacheError('INVALID_INPUT');
    const namespace = namespaceOf(identity);
    this.lock();
    this.channel?.postMessage('lock');
    const tombstone: Tombstone = { id: JSON.stringify([namespace, notice.object_type, notice.object_id]), namespace, kind: notice.object_type, objectId: notice.object_id };
    this.barrier = this.barrier.then(() => cacheTransaction<void>(this.databaseName, 'readwrite', (tx, done) => {
      tx.objectStore('tombstones').put(tombstone);
      const states = tx.objectStore('namespaces');
      const state = states.get(namespace);
      state.onsuccess = () => states.put({ id: namespace, generation: ((state.result as NamespaceState | undefined)?.generation ?? 0) + 1 });
      const records = tx.objectStore('records');
      const request = records.getAll();
      request.onsuccess = () => {
        for (const record of request.result as CipherRecord[]) {
          if (record.namespace === namespace && this.matchesDeletion(record.header, tombstone)) records.delete(record.id);
        }
        done();
      };
    }));
    return this.barrier;
  }

  /** Explicit confirmed clearing affects only the bound account/device, retaining its invalidation barrier. */
  async purge(): Promise<void> {
    const identity = this.identity;
    this.enabled();
    if (!identity) throw new CacheError('LOCKED');
    const namespace = namespaceOf(identity);
    await this.invalidate();
    await cacheTransaction<void>(this.databaseName, 'readwrite', (tx, done) => {
      for (const name of ['records', 'nonces']) {
        const store = tx.objectStore(name);
        const request = store.openCursor();
        request.onsuccess = () => {
          const cursor = request.result;
          if (!cursor) return;
          if (cursor.value.namespace === namespace) cursor.delete();
          cursor.continue();
        };
      }
      done();
    });
  }

  async migrate(reference: CacheReference, classroom: string | null = null): Promise<void> {
    const loaded = await this.load(reference, classroom);
    if (!loaded || loaded.record.header.schema === 2) return;
    const { record, auth } = loaded;
    const plain = await this.decrypt(record, auth);
    let replacement: CipherRecord;
    try { replacement = await this.encrypt({ ...record.header, schema: 2 }, plain, auth); }
    finally { plain.fill(0); }
    await cacheTransaction<void>(this.databaseName, 'readwrite', (tx, done, fail) => {
      this.guard(tx, auth, record.header, () => {
        const store = tx.objectStore('records');
        const request = store.get(record.id);
        request.onsuccess = () => {
          const current = request.result as CipherRecord | undefined;
          if (!current || !equalBytes(new Uint8Array(current.ciphertext), new Uint8Array(record.ciphertext))) { fail(new CacheError('REVISION_CONFLICT')); return; }
          tx.objectStore('nonces').add({ id: `${replacement.header.keyId}:${Array.from(replacement.nonce).join('.')}`, namespace: auth.namespace });
          store.put(replacement); done();
        };
      }, fail);
    }, this.track);
    this.check(auth);
  }

  async putRecording(descriptor: AudioDescriptor, bytes: Uint8Array, ancestry: CacheAncestry, fenceToken: number): Promise<void> {
    const snapshot = structuredClone(descriptor);
    const parents = structuredClone(ancestry);
    const pcm = new Uint8Array(bytes);
    let payload: Uint8Array<ArrayBuffer> | undefined;
    try {
      const auth = this.authorized('recording_recovery', parents.classSessionId);
      if (parents.classSessionId !== snapshot.class_session_id || !positive(fenceToken) || snapshot.byte_length !== pcm.byteLength
        || snapshot.encoding !== 'pcm_s16le' || snapshot.sample_rate !== 16000 || snapshot.channels !== 1
        || !Number.isSafeInteger(snapshot.seq) || snapshot.seq < 0 || snapshot.end_sample <= snapshot.start_sample
        || (snapshot.end_sample - snapshot.start_sample) * 2 !== pcm.byteLength) throw new CacheError('INVALID_INPUT');
      const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', pcm))].map(value => value.toString(16).padStart(2, '0')).join('');
      this.check(auth);
      if (hash !== snapshot.sha256) throw new CacheError('INVALID_INPUT');
      // The immutable envelope binds the complete descriptor. A fencing change does not rename old chunks.
      payload = encoder.encode(JSON.stringify({ descriptor: snapshot, fenceToken, bytes: Array.from(pcm) }));
      await this.put({ scope: 'recording_recovery', objectId: recordingId(snapshot), revision: 1, generation: 1, ancestry: parents }, payload);
    } finally { pcm.fill(0); payload?.fill(0); }
  }

  async readRecording(descriptor: AudioDescriptor): Promise<Uint8Array<ArrayBuffer> | null> {
    const snapshot = structuredClone(descriptor);
    const bytes = await this.read({ scope: 'recording_recovery', objectId: recordingId(snapshot), revision: 1, generation: 1 }, snapshot.class_session_id);
    if (!bytes) return null;
    try {
      const record = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)) as { descriptor: AudioDescriptor; bytes: number[] };
      if (canonicalize(record.descriptor) !== canonicalize(snapshot)) throw new CacheError('REVISION_CONFLICT');
      return Uint8Array.from(record.bytes);
    } finally { bytes.fill(0); }
  }

  async acknowledgeRecording(receipt: AudioReceipt): Promise<void> {
    const snapshot = structuredClone(receipt);
    if (snapshot.state !== 'durable' || !snapshot.durable_at || !Number.isFinite(Date.parse(snapshot.durable_at)) || !snapshot.id || !snapshot.receipt_id) throw new CacheError('INVALID_INPUT');
    const descriptor = snapshot.descriptor;
    const objectId = recordingId(descriptor);
    const bytes = await this.readRecording(descriptor);
    if (!bytes) return;
    bytes.fill(0);
    await this.removeAcknowledged({ scope: 'recording_recovery', objectId, revision: 1, generation: 1 }, descriptor.class_session_id,
      header => header.scope === 'recording_recovery' && header.objectId === objectId && header.ancestry.classSessionId === descriptor.class_session_id);
  }

  async saveCaptureState(classSessionId: string, epochId: string, state: Uint8Array, ancestry: CacheAncestry, fenceToken: number, generation: number): Promise<void> {
    if (classSessionId !== ancestry.classSessionId || !epochId) throw new CacheError('INVALID_INPUT');
    await this.store({ scope: 'recording_recovery', objectId: JSON.stringify(['capture-state', classSessionId, epochId]),
      revision: fenceToken, generation, ancestry }, state, true);
  }

  async readCaptureState(classSessionId: string, epochId: string): Promise<Uint8Array<ArrayBuffer> | null> {
    const objectId = JSON.stringify(['capture-state', classSessionId, epochId]);
    const pending = await this.listPending('recording_recovery', classSessionId);
    const current = pending.filter(value => value.objectId === objectId).sort((a, b) => b.revision - a.revision || b.generation - a.generation)[0];
    return current ? this.read(current, classSessionId) : null;
  }
}
