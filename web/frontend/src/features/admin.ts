import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { ApiClientError, type ApiClient } from '../api/client';
import { canonicalJson } from '../domain/admin';
import type { components } from '../generated/openapi';
import { isDefinitiveRejection } from './classroom';
import { isAuthoritativeDenial, isUnauthenticated } from './revocation';
import { useAppData } from './data';

type S = components['schemas'];

export type AdminOperationId = 'adminUpdateUser' | 'adminCreateInvitation' | 'adminRevokeInvitation' | 'adminReissueInvitation' | 'adminAdjustQuota' | 'adminUpdateSwitches';
export type AdminReauthBinding = { operationId: AdminOperationId; targetId: string | null; idempotencyKey: string; body: unknown };

/** Why a reauthentication proof could not be issued. */
export class AdminReauthError extends Error {
  constructor(readonly kind: 'unavailable' | 'failed' | 'forbidden' | 'mfa' | 'network' | 'disabled' | 'invalid' | 'recheck') { super(`admin reauth: ${kind}`); this.name = 'AdminReauthError'; }
}
/**
 * Issues a purpose-bound proof for one admin write (operation, target, Idempotency-Key, exact body).
 * Password and code are passed through once and never kept.
 */
export type AdminReauthPort = { issue(binding: AdminReauthBinding, password: string, code: string): Promise<{ token: string; expires_at: string }> };
/** Used where no signed-in administrator is known: every admin write stays disabled. */
export const unavailableAdminReauth: AdminReauthPort = { issue: () => Promise.reject(new AdminReauthError('unavailable')) };

/** Proof target per operation (HTTP 0.4.0; decision PR11B-CONTRACT-DECISION-20261124 §1). */
const ACCOUNT_TARGETED: ReadonlySet<AdminOperationId> = new Set(['adminAdjustQuota', 'adminUpdateSwitches', 'adminCreateInvitation']);
export function reauthOperation(binding: AdminReauthBinding, adminId: string): S['ReauthOperation'] {
  const target = ACCOUNT_TARGETED.has(binding.operationId) ? { kind: 'account', id: adminId } : { kind: binding.operationId === 'adminUpdateUser' ? 'user' : 'invitation', id: binding.targetId ?? '' };
  return { purpose: binding.operationId, target, candidate_parameters: { idempotency_key: binding.idempotencyKey, body: binding.body } } as S['ReauthOperation'];
}
/**
 * `POST /auth/reauth` for the six admin purposes (HTTP 0.4.0). The reauth request has its own key; the
 * returned proof is held by the write intent in memory only. A purpose whose backend write is not
 * implemented yet answers 503 SERVICE_DISABLED (decision §1), shown per operation.
 */
export function createHttpAdminReauth(api: ApiClient, adminId: () => string | null, newKey: () => string = () => crypto.randomUUID()): AdminReauthPort {
  return {
    async issue(binding, password, code) {
      const id = adminId();
      if (!id) throw new AdminReauthError('unavailable');
      try {
        const result = await api.reauthenticate({ password, mfa_code: code, operation: reauthOperation(binding, id) }, newKey());
        return { token: result.reauth_token, expires_at: result.expires_at };
      } catch (failure) {
        if (!(failure instanceof ApiClientError)) throw failure;
        if (failure.kind === 'network') throw new AdminReauthError('network');
        if (failure.kind !== 'http') throw failure;
        // 401 is both "wrong current password" and "session ended": the caller rechecks the session.
        if (failure.status === 401) throw new AdminReauthError('recheck');
        const kinds: Partial<Record<NonNullable<ApiClientError['code']>, AdminReauthError['kind']>> = { REAUTH_REQUIRED: 'failed', FORBIDDEN: 'forbidden', MFA_REQUIRED: 'mfa', SERVICE_DISABLED: 'disabled', VALIDATION_FAILED: 'invalid' };
        const kind = failure.code ? kinds[failure.code] : undefined;
        // Other refusals (an ended session, rate limits) keep their own handling.
        if (!kind) throw failure;
        throw new AdminReauthError(kind);
      }
    },
  };
}

const adminKey = (...parts: unknown[]) => ['admin', ...parts];
/**
 * Per account epoch: when an admin read was last refused (role, MFA, session). Data read before that
 * moment stays hidden even if a later refresh fails at the transport, until a successful read replaces it.
 */
const deniedAt = new WeakMap<QueryClient, Map<string, number>>();
const denials = (client: QueryClient) => { let map = deniedAt.get(client); if (!map) { map = new Map(); deniedAt.set(client, map); } return map; };
/**
 * Account-wide admin gate: a refusal of the admin role, MFA or the session on any admin read hides every
 * admin projection read before it (and re-reads `/me`), until each is read again successfully.
 * Resource-specific denials (NOT_FOUND, GONE) stay with their own query.
 */
type Gate = { at: number; listeners: Set<() => void> };
const gates = new WeakMap<QueryClient, Gate>();
const gateOf = (client: QueryClient) => { let gate = gates.get(client); if (!gate) { gate = { at: -1, listeners: new Set() }; gates.set(client, gate); } return gate; };
const accessDenial = (error: unknown) => isUnauthenticated(error) || (error instanceof ApiClientError && (error.code === 'FORBIDDEN' || error.code === 'MFA_REQUIRED'));
function useAdminGate(client: QueryClient) {
  const gate = gateOf(client);
  return useSyncExternalStore(listener => { gate.listeners.add(listener); return () => { gate.listeners.delete(listener); }; }, () => gate.at);
}
/** Records a refusal (after render) and returns the moment before which this query's data stays hidden. */
function useDenialMask(client: QueryClient, accountKey: string, key: readonly unknown[], query: { isError: boolean; error: unknown; errorUpdatedAt: number }) {
  const hash = JSON.stringify(key);
  const gateAt = useAdminGate(client);
  const { isError, error, errorUpdatedAt } = query;
  useEffect(() => {
    if (!isError || !isAdminDenial(error)) return;
    if ((denials(client).get(hash) ?? -1) < errorUpdatedAt) denials(client).set(hash, errorUpdatedAt);
    const gate = gateOf(client);
    if (accessDenial(error) && gate.at < errorUpdatedAt) {
      gate.at = errorUpdatedAt;
      for (const listener of gate.listeners) listener();
      void client.invalidateQueries({ queryKey: ['account', accountKey, 'me'] });
    }
  }, [client, accountKey, hash, isError, error, errorUpdatedAt]);
  const local = isError && isAdminDenial(error) ? errorUpdatedAt : (denials(client).get(hash) ?? -1);
  return Math.max(local, gateAt);
}
function useAdminQuery<T>(parts: unknown[], fn: (api: ApiClient, signal: AbortSignal) => Promise<T>, enabled = true) {
  const { api, accountKey } = useAppData();
  const client = useQueryClient();
  const key = ['account', accountKey, ...adminKey(...parts)];
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) => fn(api, signal), enabled });
  const refusedAt = useDenialMask(client, accountKey, key, query);
  return refusedAt >= 0 && query.dataUpdatedAt <= refusedAt ? { ...query, data: undefined } : query;
}
export const useAdminUsers = (enabled = true) => useAdminQuery(['users'], (api, signal) => api.adminUsers(signal), enabled);
export const useAdminUserQuota = (userId: string | null) => useAdminQuery(['user-quota', userId], (api, signal) => api.adminUserQuota(userId ?? '', signal), userId !== null);
export const useAdminInvitations = () => useAdminQuery(['invitations'], (api, signal) => api.adminInvitations(signal));
export const useAdminUsage = (periodStart: string | null) => useAdminQuery(['usage', periodStart], (api, signal) => api.adminUsage(periodStart, signal));
export const useAdminHealth = () => useAdminQuery(['health'], (api, signal) => api.adminHealth(signal));
export const useAdminSwitches = () => useAdminQuery(['switches'], (api, signal) => api.adminSwitches(signal));
export function useAdminAudit() {
  const { api, accountKey } = useAppData();
  const client = useQueryClient();
  const key = ['account', accountKey, 'admin', 'audit'];
  const query = useInfiniteQuery({
    queryKey: key,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => api.adminAudit(pageParam, signal),
    getNextPageParam: (last: S['AuditRecordPage']) => last.next_cursor,
  });
  const refusedAt = useDenialMask(client, accountKey, key, query);
  return refusedAt >= 0 && query.dataUpdatedAt <= refusedAt ? { ...query, data: undefined } : query;
}

/** The server refused an admin read itself (role, MFA, session): nothing of it, nor a secret shown beside it, may stay. */
export const isAdminDenial = (error: unknown) => isAuthoritativeDenial(error) || isUnauthenticated(error) || (error instanceof ApiClientError && error.code === 'MFA_REQUIRED');

export type AdminWriteStage = 'idle' | 'choose' | 'reauth' | 'sending' | 'unconfirmed' | 'failed';
type Pending<B> = { slot: string; body: B; key: string; token: string | null; expires: number; sent: boolean };
/**
 * Unconfirmed intents per account epoch (one QueryClient each), by operation and target. They outlive the
 * dialog: submitting the same body again later reuses the same Idempotency-Key, so a write whose reply was
 * lost is never applied twice (quota adjustments and invitations have no CAS to catch it).
 */
const unconfirmed = new WeakMap<QueryClient, Map<string, Pending<unknown>>>();
const intentsOf = (client: QueryClient) => { let map = unconfirmed.get(client); if (!map) { map = new Map(); unconfirmed.set(client, map); } return map; };

/**
 * One admin write intent: reason and body first, then a purpose-bound reauthentication, then the request.
 * The Idempotency-Key (and its proof, while valid) is kept until a definitive reply, so a lost reply is
 * recovered by sending the very same request again; a changed body is a new intent with a new key.
 */
export function useAdminWrite<B, R>(spec: {
  operationId: AdminOperationId;
  targetId: (body: B) => string | null;
  send: (api: ApiClient, body: B, key: string, token: string) => Promise<R>;
  /** Writes the confirmed reply into the affected projections before they are refetched (offline-safe). */
  apply?: (client: QueryClient, accountKey: string, result: R, body: B) => void;
  onSuccess?: (result: R, body: B) => void;
}) {
  const { api, accountKey, adminReauth = unavailableAdminReauth } = useAppData();
  const client = useQueryClient();
  const [stage, setStage] = useState<AdminWriteStage>('idle');
  const [error, setError] = useState<unknown>(null);
  const pending = useRef<Pending<B> | null>(null);
  // An intent not yet dispatched belongs to this dialog: leaving it abandons a verification still in flight,
  // so it can never be sent later beside a new intent with another key.
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; if (pending.current && !pending.current.sent) pending.current = null; }; }, []);
  const slotOf = (body: B) => `${spec.operationId}:${spec.targetId(body) ?? ''}:${canonicalJson(body)}`;
  const settle = (intent: Pending<B>) => { if (intentsOf(client).get(intent.slot) === intent) intentsOf(client).delete(intent.slot); if (pending.current === intent) pending.current = null; };
  // The reply may carry a one-time secret (an invitation token): it is handed to the page once and never
  // kept in the mutation cache. The mutation itself stays, so a 401 still retires the account session.
  const reply = useRef<{ value: R } | null>(null);
  // Only the operation name is the mutation's variable: body, key and proof stay in the intent ref, so the
  // proof never lands in the mutation cache.
  const mutation = useMutation<null, unknown, AdminOperationId>({
    mutationFn: async () => {
      const intent = pending.current;
      if (!intent?.token) throw new Error('admin write without proof');
      reply.current = { value: await spec.send(api, intent.body, intent.key, intent.token) };
      return null;
    },
  });
  // Admin projections and this account's own quota view (a switch or an own-quota change shows there too).
  const refresh = () => Promise.all([
    client.invalidateQueries({ queryKey: ['account', accountKey, 'admin'] }),
    client.invalidateQueries({ queryKey: ['account', accountKey, 'quota'] }),
  ]);

  async function send() {
    const intent = pending.current;
    if (!mounted.current) return;
    if (!intent?.token) { setStage('reauth'); return; }
    // Registered before dispatch: a dialog opened while this request is in flight joins the same key.
    intentsOf(client).set(intent.slot, intent as Pending<unknown>);
    intent.sent = true;
    setStage('sending'); setError(null);
    try {
      await mutation.mutateAsync(spec.operationId);
      const result = reply.current;
      reply.current = null;
      // The confirmed state is shown even if the refetch below cannot reach the server.
      if (result) spec.apply?.(client, accountKey, result.value, intent.body);
      settle(intent);
      setStage('idle');
      if (result) spec.onSuccess?.(result.value, intent.body);
    } catch (failure) {
      setError(failure);
      if (failure instanceof ApiClientError && failure.code === 'REAUTH_REQUIRED') {
        // Same key and body: only the proof is renewed.
        if (pending.current === intent) intent.token = null;
        setStage('reauth');
        return;
      }
      if (isDefinitiveRejection(failure)) { settle(intent); setStage('failed'); }
      // No definitive reply: the write may have been applied. Keep key and proof for a replay.
      else setStage('unconfirmed');
    } finally { void refresh(); }
  }

  return {
    stage, error, operationId: spec.operationId,
    pendingBody: () => pending.current?.body ?? null,
    /** Whether the current intent was already dispatched (its replay starts nothing new). */
    resuming: pending.current?.sent ?? false,
    /**
     * Starts an intent. One slot per operation, target and exact body: when an earlier identical request is
     * still unconfirmed, the administrator chooses between replaying it (never applied twice) and a new one.
     */
    start(body: B) {
      setError(null);
      const slot = slotOf(body);
      if (pending.current?.slot === slot) { pending.current.token = null; setStage('reauth'); return; }
      const earlier = intentsOf(client).get(slot) as Pending<B> | undefined;
      if (earlier) { pending.current = earlier; setStage('choose'); return; }
      pending.current = { slot, body, key: crypto.randomUUID(), token: null, expires: 0, sent: false };
      setStage('reauth');
    },
    /** After `choose`: replay the earlier request with its key, or submit a new one with a fresh key. */
    choose(replay: boolean) {
      const earlier = pending.current;
      if (!earlier) return;
      if (replay) earlier.token = null;
      else pending.current = { slot: earlier.slot, body: earlier.body, key: crypto.randomUUID(), token: null, expires: 0, sent: false };
      setStage('reauth');
    },
    async verify(password: string, code: string) {
      const intent = pending.current;
      if (!intent) return;
      setError(null);
      try {
        const proof = await adminReauth.issue({ operationId: spec.operationId, targetId: spec.targetId(intent.body), idempotencyKey: intent.key, body: intent.body }, password, code);
        if (!mounted.current || pending.current !== intent) return;
        intent.token = proof.token;
        intent.expires = Date.parse(proof.expires_at);
      } catch (failure) {
        if (failure instanceof AdminReauthError && failure.kind === 'recheck') {
          // Re-read the session: if it has ended, the 401 on this account query retires the account epoch
          // (revocation ledger) and nothing private stays on screen; otherwise it was a factor failure.
          try {
            await client.fetchQuery({ queryKey: ['account', accountKey, 'me'], queryFn: ({ signal }) => api.me(signal), staleTime: 0 });
            setError(new AdminReauthError('failed'));
          } catch (sessionFailure) { setError(sessionFailure); }
          return;
        }
        setError(failure);
        return;
      }
      await send();
    },
    /**
     * An explicit retry keeps the same key and body but always asks for fresh verification (HTTP 0.4.0
     * decision): a request that never reached the server is never sent on an earlier proof.
     */
    retry: () => { const intent = pending.current; if (!intent) return; intent.token = null; setError(null); setStage('reauth'); },
    /** Leaves the intent. An unconfirmed one is re-read from the server, never assumed to have failed. */
    cancel() { pending.current = null; setStage('idle'); setError(null); void refresh(); },
    /** Whether an earlier attempt of this operation with this body is still unconfirmed. */
    hasUnconfirmed: (body: B) => intentsOf(client).has(slotOf(body)),
  };
}
export type AdminWrite<B> = { stage: AdminWriteStage; error: unknown; operationId: AdminOperationId; resuming: boolean; pendingBody: () => B | null; start(body: B): void; choose(replay: boolean): void; verify(password: string, code: string): Promise<void>; retry(): void; cancel(): void; hasUnconfirmed(body: B): boolean };

const reauthMessages: Record<AdminReauthError['kind'], string> = {
  unavailable: '管理写操作暂不可用：没有确认当前管理员身份，请刷新页面。',
  disabled: '这项管理操作的服务端尚未开放，暂时不能执行。',
  invalid: '提交内容未通过校验，请检查理由和各项输入。验证码没有被使用。',
  failed: '密码或验证码不正确，请重新输入。',
  recheck: '验证没有完成，正在确认登录状态。',
  forbidden: '当前账号无权执行管理操作。',
  mfa: '管理员需要先启用双重验证。',
  network: '无法连接服务，验证没有完成。',
};
const adminMessages: Partial<Record<NonNullable<ApiClientError['code']>, string>> = {
  REAUTH_REQUIRED: '验证已失效，请重新输入密码和验证码。',
  MFA_REQUIRED: '管理员需要先启用双重验证，才能使用运营后台。',
  FORBIDDEN: '当前账号无权执行这项管理操作。',
  INVITATION_UNAVAILABLE: '这个邀请已被使用或撤销，不能执行该操作。',
  SECRET_ALREADY_ISSUED: '操作已经完成，但一次性令牌不会再次显示。请刷新列表，对该邀请执行“重新签发”获取新令牌。',
  SERVICE_DISABLED: '这项设置或操作暂未开放，没有任何改动。',
  REVISION_CONFLICT: '数据已被其他管理员更新。已重新读取最新状态，请核对后再操作。',
  GONE: '该账号正在注销，不能再修改。',
  CURSOR_EXPIRED: '分页位置已失效，请重新读取列表。',
};
export function describeAdminError(error: unknown, fallback: (error: unknown) => string): string {
  if (error instanceof AdminReauthError) return reauthMessages[error.kind];
  if (error instanceof ApiClientError && error.code && adminMessages[error.code]) return adminMessages[error.code]!;
  return fallback(error);
}
