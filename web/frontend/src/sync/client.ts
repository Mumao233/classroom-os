import canonicalize from 'canonicalize';
import type { AuthClient } from '../api/auth-client';
import type { components } from '../generated/openapi';

type S = components['schemas'];
export type SyncOperation = S['SyncOperation'];
export type SyncReceipt = S['OperationReceipt'];
export type SyncConflict = S['Conflict'];
export type SyncResolution = S['ResolveConflict'];

export interface SyncTransport {
  bootstrap(): Promise<S['SyncBootstrap']>;
  changes(after: string): Promise<S['Changes']>;
  deletion(id: string): Promise<S['Deletion']>;
  receipt(id: string, operation: 'syncOperation' | 'resolveConflict'): Promise<SyncReceipt>;
  push(operation: SyncOperation, batchId: string): Promise<S['SyncResult']>;
  conflict(id: string): Promise<SyncConflict>;
  resolve(id: string, body: SyncResolution, operationId: string): Promise<SyncReceipt>;
}

/** The same canonical field set is hashed by the server before writing its durable receipt. */
export async function syncFingerprint(value: unknown): Promise<string> {
  const serialized = canonicalize(value);
  if (!serialized) throw new Error('INVALID_SYNC_OPERATION');
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(serialized));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}

/** Uses AuthClient's same-origin Cookie/CSRF, account epoch and no-store request boundary. */
export class AuthenticatedSyncClient implements SyncTransport {
  constructor(private readonly auth: AuthClient) {}
  bootstrap() { return this.auth.request<S['SyncBootstrap']>('/sync/bootstrap'); }
  changes(after: string) { return this.auth.request<S['Changes']>(`/sync/changes?after=${encodeURIComponent(after)}&limit=1000`); }
  deletion(id: string) { return this.auth.request<S['Deletion']>(`/deletions/${encodeURIComponent(id)}`); }
  receipt(id: string, operation: 'syncOperation' | 'resolveConflict') {
    return this.auth.request<SyncReceipt>(`/operations/${encodeURIComponent(id)}?operation=${operation}`);
  }
  push(operation: SyncOperation, batchId: string) {
    return this.auth.request<S['SyncResult']>('/sync/operations', { operations: [structuredClone(operation)] }, { key: batchId });
  }
  conflict(id: string) { return this.auth.request<SyncConflict>(`/sync/conflicts/${encodeURIComponent(id)}`); }
  resolve(id: string, body: SyncResolution, operationId: string) {
    return this.auth.request<SyncReceipt>(`/sync/conflicts/${encodeURIComponent(id)}/resolve`, structuredClone(body), { key: operationId });
  }
}
