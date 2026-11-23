import { useRef, useState } from 'react';
import { useInfiniteQuery, useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { ApiClientError, type ApiClient } from '../api/client';
import { canonicalJson } from '../domain/admin';
import type { components } from '../generated/openapi';
import { isDefinitiveRejection } from './classroom';
import { isAuthoritativeDenial, isUnauthenticated } from './revocation';
import { useAccountQuery, useAppData } from './data';

type S = components['schemas'];

export type AdminOperationId = 'adminUpdateUser' | 'adminCreateInvitation' | 'adminRevokeInvitation' | 'adminReissueInvitation' | 'adminAdjustQuota' | 'adminUpdateSwitches';
export type AdminReauthBinding = { operationId: AdminOperationId; targetId: string | null; idempotencyKey: string; body: unknown };

/** Why a reauthentication proof could not be issued. */
export class AdminReauthError extends Error {
  constructor(readonly kind: 'unavailable' | 'failed' | 'forbidden' | 'mfa' | 'network') { super(`admin reauth: ${kind}`); this.name = 'AdminReauthError'; }
}
/**
 * Issues a purpose-bound proof for one admin write (operation, target, Idempotency-Key, exact body).
 * The 0.3.0 contract has no admin reauth purpose (PR11B-SCOPE-GAPS.md §1): only the Mock supplies one.
 * Password and code are passed through once and never kept.
 */
export type AdminReauthPort = { issue(binding: AdminReauthBinding, password: string, code: string): Promise<{ token: string; expires_at: string }> };
/** The real-API port until the contract adds admin reauth purposes: every admin write stays disabled. */
export const unavailableAdminReauth: AdminReauthPort = { issue: () => Promise.reject(new AdminReauthError('unavailable')) };

const adminKey = (...parts: unknown[]) => ['admin', ...parts];
export const useAdminUsers = (enabled = true) => useAccountQuery(adminKey('users'), (api, signal) => api.adminUsers(signal), enabled);
export const useAdminUserQuota = (userId: string | null) => useAccountQuery(adminKey('user-quota', userId), (api, signal) => api.adminUserQuota(userId ?? '', signal), userId !== null);
export const useAdminInvitations = () => useAccountQuery(adminKey('invitations'), (api, signal) => api.adminInvitations(signal));
export const useAdminUsage = (periodStart: string | null) => useAccountQuery(adminKey('usage', periodStart), (api, signal) => api.adminUsage(periodStart, signal));
export const useAdminHealth = () => useAccountQuery(adminKey('health'), (api, signal) => api.adminHealth(signal));
export const useAdminSwitches = () => useAccountQuery(adminKey('switches'), (api, signal) => api.adminSwitches(signal));
export function useAdminAudit() {
  const { api, accountKey } = useAppData();
  return useInfiniteQuery({
    queryKey: ['account', accountKey, 'admin', 'audit'],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => api.adminAudit(pageParam, signal),
    getNextPageParam: (last: S['AuditRecordPage']) => last.next_cursor,
  });
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
  // Proof expiry is wall-clock time (the page's data clock may be a fixed synthetic one).
  const wall = () => Date.now();
  const client = useQueryClient();
  const [stage, setStage] = useState<AdminWriteStage>('idle');
  const [error, setError] = useState<unknown>(null);
  const pending = useRef<Pending<B> | null>(null);
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
      if (pending.current?.slot === slot) { if (pending.current.token && pending.current.expires <= wall()) pending.current.token = null; setStage('reauth'); return; }
      const earlier = intentsOf(client).get(slot) as Pending<B> | undefined;
      if (earlier) { pending.current = earlier; setStage('choose'); return; }
      pending.current = { slot, body, key: crypto.randomUUID(), token: null, expires: 0, sent: false };
      setStage('reauth');
    },
    /** After `choose`: replay the earlier request with its key, or submit a new one with a fresh key. */
    choose(replay: boolean) {
      const earlier = pending.current;
      if (!earlier) return;
      if (replay) { if (earlier.token && earlier.expires <= wall()) earlier.token = null; }
      else pending.current = { slot: earlier.slot, body: earlier.body, key: crypto.randomUUID(), token: null, expires: 0, sent: false };
      setStage('reauth');
    },
    async verify(password: string, code: string) {
      const intent = pending.current;
      if (!intent) return;
      setError(null);
      try {
        const proof = await adminReauth.issue({ operationId: spec.operationId, targetId: spec.targetId(intent.body), idempotencyKey: intent.key, body: intent.body }, password, code);
        if (pending.current !== intent) return;
        intent.token = proof.token;
        intent.expires = Date.parse(proof.expires_at);
      } catch (failure) { setError(failure); return; }
      await send();
    },
    // The server answers a replayed key from its receipt before it looks at the proof, so an expired proof
    // is still sent; only an unprocessed request comes back REAUTH_REQUIRED and asks for a new one.
    retry: () => { void send(); },
    /** Leaves the intent. An unconfirmed one is re-read from the server, never assumed to have failed. */
    cancel() { pending.current = null; setStage('idle'); setError(null); void refresh(); },
    /** Whether an earlier attempt of this operation with this body is still unconfirmed. */
    hasUnconfirmed: (body: B) => intentsOf(client).has(slotOf(body)),
  };
}
export type AdminWrite<B> = { stage: AdminWriteStage; error: unknown; operationId: AdminOperationId; resuming: boolean; pendingBody: () => B | null; start(body: B): void; choose(replay: boolean): void; verify(password: string, code: string): Promise<void>; retry(): void; cancel(): void; hasUnconfirmed(body: B): boolean };

const reauthMessages: Record<AdminReauthError['kind'], string> = {
  unavailable: '管理写操作暂不可用：接口契约还没有定义管理员再认证用途。',
  failed: '密码或验证码不正确，请重新输入。',
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
  REVISION_CONFLICT: '数据已被其他管理员更新。已重新读取最新状态，请核对后再操作。',
  GONE: '该账号正在注销，不能再修改。',
  CURSOR_EXPIRED: '分页位置已失效，请重新读取列表。',
};
export function describeAdminError(error: unknown, fallback: (error: unknown) => string): string {
  if (error instanceof AdminReauthError) return reauthMessages[error.kind];
  if (error instanceof ApiClientError && error.code && adminMessages[error.code]) return adminMessages[error.code]!;
  return fallback(error);
}
