import { CacheError } from './types';
import type { CacheMetadata, CipherRecord } from './types';

export const DATABASE_NAME = 'classroom-os-trusted-cache';
export const STORES = ['records', 'metadata', 'nonces', 'namespaces', 'tombstones'] as const;

export function metadataOf(record: CipherRecord): CacheMetadata {
  return { id: record.id, namespace: record.namespace, header: record.header,
    bytes: record.bytes, accessedAt: record.accessedAt };
}

export function putCacheRecord(tx: IDBTransaction, record: CipherRecord) {
  tx.objectStore('records').put(record);
  tx.objectStore('metadata').put(metadataOf(record));
}

export function deleteCacheRecord(tx: IDBTransaction, id: IDBValidKey) {
  tx.objectStore('records').delete(id);
  tx.objectStore('metadata').delete(id);
}

/** v3 adds transactionally maintained metadata without changing v1/v2 ciphertext or AAD. */
export function openCacheDatabase(name = DATABASE_NAME): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(name, 3);
    let abandoned = false;
    request.onupgradeneeded = event => {
      const db = request.result;
      for (const store of STORES) {
        if (!db.objectStoreNames.contains(store)) db.createObjectStore(store, { keyPath: 'id' });
      }
      if (event.oldVersion < 3) {
        const tx = request.transaction!;
        const metadata = tx.objectStore('metadata');
        metadata.createIndex('namespace', 'namespace');
        metadata.createIndex('pool', ['namespace', 'header.scope']);
        metadata.createIndex('objectVersion', ['namespace', 'header.scope', 'header.objectId', 'header.revision', 'header.generation']);
        // Upgrade handles one existing envelope at a time. A failed upgrade rolls back both schema and backfill.
        const records = tx.objectStore('records').openCursor();
        records.onsuccess = () => {
          const cursor = records.result;
          if (!cursor) return;
          metadata.put(metadataOf(cursor.value as CipherRecord));
          cursor.continue();
        };
      }
    };
    request.onblocked = () => { abandoned = true; reject(new CacheError('DATABASE_BLOCKED')); };
    request.onerror = () => reject(new CacheError('STORAGE_UNAVAILABLE'));
    request.onsuccess = () => {
      const db = request.result;
      db.onversionchange = () => db.close();
      if (abandoned) db.close();
      else resolve(db);
    };
  });
}

export function storageError(error: unknown): CacheError {
  if (error instanceof CacheError) return error;
  return new CacheError(error instanceof DOMException && error.name === 'QuotaExceededError' ? 'QUOTA_EXCEEDED' : 'STORAGE_UNAVAILABLE');
}

/** All callbacks enqueue IDB work synchronously. Crypto always runs outside transactions. */
export async function cacheTransaction<T>(
  name: string, mode: IDBTransactionMode,
  run: (tx: IDBTransaction, result: (value: T) => void, fail: (error: CacheError) => void) => void,
  track?: (tx: IDBTransaction) => (() => void),
): Promise<T> {
  const db = await openCacheDatabase(name);
  try {
    return await new Promise<T>((resolve, reject) => {
      const tx = db.transaction([...STORES], mode);
      let value: T;
      let failure: CacheError | null = null;
      const untrack = track?.(tx);
      const fail = (error: CacheError) => {
        failure = error;
        try { tx.abort(); } catch { /* Completion may already be queued. */ }
      };
      tx.oncomplete = () => { untrack?.(); resolve(value); };
      tx.onabort = () => { untrack?.(); reject(failure ?? storageError(tx.error)); };
      tx.onerror = () => { /* onabort owns the error so no partial success escapes. */ };
      try { run(tx, result => { value = result; }, fail); }
      catch (error) { fail(storageError(error)); }
    });
  } finally { db.close(); }
}
