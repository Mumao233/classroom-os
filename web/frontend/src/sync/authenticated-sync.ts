import type { AuthClient } from '../api/auth-client';
import { AuthenticatedCache } from '../cache/auth-cache';
import type { CacheOptions } from '../cache/types';
import { AuthenticatedSyncClient } from './client';
import { OfflineSyncRuntime, SyncError } from './runtime';

export type OfflineSyncOptions = Omit<CacheOptions, 'recordingEnabled'>;

/** Default-off normal entry point for the authenticated application and downstream editors. */
export class AuthenticatedOfflineSync extends OfflineSyncRuntime {
  private readonly unsubscribe: () => void;
  constructor(private readonly auth: AuthClient, private readonly configuration: OfflineSyncOptions = {}) {
    const options = configuration;
    let identity: string | null = null;
    super(new AuthenticatedCache(auth, { ...options, recordingEnabled: false }), new AuthenticatedSyncClient(auth), { enabled: options.enabled, identity: () => identity });
    this.unsubscribe = auth.subscribeSession(session => { identity = session ? JSON.stringify([session.user.id, session.device_id, session.session_id]) : null; });
  }

  override async unlock(password: string, mfaCode?: string) {
    if (!this.configuration.enabled) throw new SyncError('DISABLED');
    const session = await this.auth.bootstrap();
    if (!session) throw new SyncError('LOCKED');
    await super.unlock(password, mfaCode);
  }

  override async unlockLearning(password: string, mfaCode?: string) {
    if (!this.configuration.enabled) throw new SyncError('DISABLED');
    if (!await this.auth.bootstrap()) throw new SyncError('LOCKED');
    await super.unlockLearning(password, mfaCode);
  }

  override close() { this.unsubscribe(); super.close(); }
}
