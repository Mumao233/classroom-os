import type { AuthClient } from '../api/auth-client';
import type { components } from '../generated/openapi';
import { TrustedCache } from './trusted-cache';
import { CacheError } from './types';
import type { CacheGrant, CacheOptions, CacheScope } from './types';

type S = components['schemas'];

/** AuthClient is the sole source of online identity and purpose-bound key release. */
export class AuthenticatedCache extends TrustedCache {
  private unsubscribe: () => void;
  private session: S['SessionContext'] | null = null;
  private epochs = new Map<string, number>();
  private checking = false;
  private generation = 0;
  private poll: ReturnType<typeof setInterval> | null = null;
  private readonly online = () => { void this.revalidate(); };

  constructor(private readonly auth: AuthClient, options: CacheOptions = {}) {
    super(options);
    this.unsubscribe = auth.subscribeSession(session => {
      const changed = !session || (this.session && (session.session_id !== this.session.session_id
        || session.user.id !== this.session.user.id || session.device_id !== this.session.device_id));
      if (changed) { this.generation++; this.epochs.clear(); void this.invalidate().catch(() => this.lock()); }
      this.session = session;
      this.bind(session ? { ownerId: session.user.id, deviceId: session.device_id, sessionId: session.session_id } : null);
    });
    if (options.enabled) {
      this.poll = setInterval(this.online, 5000);
      window.addEventListener('online', this.online);
    }
  }

  async unlockScope(scope: CacheScope, password: string, mfaCode?: string, classSessionId: string | null = null): Promise<void> {
    const session = await this.auth.bootstrap();
    if (!session) throw new CacheError('LOCKED');
    const devices = await this.auth.request<S['TrustedDevicePage']>('/me/devices');
    const device = devices.items.find(value => value.id === session.device_id);
    if (!device || device.revoked_at || (scope !== 'recording_recovery' && !device.trusted)) throw new CacheError('LOCKED');
    if (this.session?.session_id !== session.session_id) throw new CacheError('STALE_OPERATION');
    const generation = this.generation;
    const body: S['CacheUnlockRequest'] = { device_id: session.device_id, key_epoch: device.key_epoch, scope, class_session_id: classSessionId };
    await this.unlock(scope, classSessionId, async () => {
      const grant = await this.auth.change('unlockCache', { kind: 'device', id: session.device_id }, body, password, mfaCode, '/me/cache-unlock') as CacheGrant;
      if (grant.key_epoch !== device.key_epoch) { grant.key_material_b64 = ''; throw new CacheError('INVALID_GRANT'); }
      return grant;
    });
    if (generation !== this.generation || this.session?.session_id !== session.session_id) throw new CacheError('STALE_OPERATION');
    this.epochs.set(scope, device.key_epoch);
  }

  /** An unreachable server permits only the existing bounded key window. A definitive rejection locks immediately. */
  async revalidate(): Promise<void> {
    if (!this.session || !this.epochs.size || this.checking) return;
    this.checking = true;
    const expected = this.session;
    try {
      const session = await this.auth.bootstrap();
      if (!session || session.session_id !== expected.session_id || session.device_id !== expected.device_id || session.user.id !== expected.user.id) { await this.invalidate(); return; }
      const devices = await this.auth.request<S['TrustedDevicePage']>('/me/devices');
      const device = devices.items.find(value => value.id === session.device_id);
      if (!device || device.revoked_at || [...this.epochs].some(([scope, epoch]) => epoch !== device.key_epoch || (scope !== 'recording_recovery' && !device.trusted))) await this.invalidate();
    } catch (error) {
      const failure = error as { code?: string; status?: number };
      if (failure.status || failure.code !== 'STORAGE_UNAVAILABLE') await this.invalidate();
    } finally { this.checking = false; }
  }

  override close() {
    this.unsubscribe();
    if (this.poll) clearInterval(this.poll);
    window.removeEventListener('online', this.online);
    this.epochs.clear();
    super.close();
  }
}
