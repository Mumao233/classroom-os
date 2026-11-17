import { CacheError } from './types';

export const DATABASE_NAME = 'classroom-os-trusted-cache';
export const STORES = ['records', 'nonces', 'namespaces', 'tombstones'] as const;

/** v1 used the same immutable ciphertext envelopes. v2 adds persistent invalidation barriers. */
export function openCacheDatabase(name = DATABASE_NAME): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(name, 2);
    let abandoned = false;
    request.onupgradeneeded = () => {
      const db = request.result;
      for (const store of STORES) {
        if (!db.objectStoreNames.contains(store)) db.createObjectStore(store, { keyPath: 'id' });
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
