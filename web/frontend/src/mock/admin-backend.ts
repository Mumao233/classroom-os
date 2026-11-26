import type { components } from '../generated/openapi';

/**
 * PR-11b development-only operations backend (PR11B-SCOPE-GAPS.md). It returns only the metadata
 * projections of the 0.3.0 `/admin/*` routes: no study content, no impersonation, no secrets in
 * the audit trail. One instance is shared by every Mock account (it is the platform, not a tenant).
 */
type S = components['schemas'];
type ErrorCode = S['Error']['error']['code'];
export type AdminReply = { status: number; body: unknown };

/** Admin write operations that need a purpose-bound reauthentication proof (HTTP 0.4.0 ReauthOperation). */
export type AdminOperationId = 'adminUpdateUser' | 'adminCreateInvitation' | 'adminRevokeInvitation' | 'adminReissueInvitation' | 'adminAdjustQuota' | 'adminUpdateSwitches';
/** What a proof is bound to: operation, path target (if any), the future Idempotency-Key and the exact body. */
export type AdminReauthBinding = { operationId: AdminOperationId; targetId: string | null; idempotencyKey: string; body: unknown };
/** Fine-grained platform configuration (HTTP 0.4.0); the public view adds the ASR/AI group summaries. */
type SwitchConfig = Omit<S['AdminSwitches'], 'asr_enabled' | 'ai_enabled'>;
export type ServiceSwitch = 'asr_streaming_enabled' | 'asr_filetrans_enabled' | 'ai_interaction_enabled' | 'ai_summary_enabled';
/** Proof target per operation (decision PR11B-CONTRACT-DECISION-20261124 §1). */
const ACCOUNT_TARGETED: ReadonlySet<AdminOperationId> = new Set(['adminAdjustQuota', 'adminUpdateSwitches', 'adminCreateInvitation']);
const BILLING_WRITES: ReadonlySet<AdminOperationId> = new Set(['adminAdjustQuota', 'adminUpdateSwitches']);
export const proofTarget = (operationId: AdminOperationId, actorId: string, pathId: string | null) =>
  ACCOUNT_TARGETED.has(operationId) ? { kind: 'account', id: actorId } : { kind: operationId === 'adminUpdateUser' ? 'user' : 'invitation', id: pathId ?? '' };
const CANONICAL_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export type AdminHealthDev = 'healthy' | 'degraded' | 'stalled';

/** The account-side hooks the admin backend needs; it never reads any account's study records. */
export type AdminHost = {
  now: () => string;
  /** Wall clock (ms) for proof expiry; independent of the fixed synthetic data clock. */
  clockMs: () => number;
  newId: () => string;
  /** The tenant user record when the id belongs to a Mock account (A/B/C), so its status takes effect there. */
  accountUser: (userId: string) => S['User'] | null;
  setAccountUser: (user: S['User']) => void;
  /** A Mock account's own quota view (its buckets carry its real usage); other users get a synthetic one. */
  accountQuota: (userId: string) => S['QuotaView'] | null;
  setAccountQuota: (userId: string, quota: S['QuotaView']) => void;
  /** Platform switches also gate every account's own quota view. */
  applySwitches: (switches: S['AdminSwitches']) => void;
  /** Current session generation of a user's Mock session; a proof is valid only within the one that issued it. */
  sessionGeneration: (userId: string) => number;
  /** Disabling a Mock account ends every existing session of it (restoring never revives them). */
  revokeSessions: (userId: string) => void;
};

const BEIJING = 8 * 3600_000;
/** Start of the Beijing calendar month containing `iso`, as a UTC instant (11a: billing months are Beijing time). */
export function beijingMonthStart(iso: string, monthsBack = 0) {
  const local = new Date(Date.parse(iso) + BEIJING);
  return new Date(Date.UTC(local.getUTCFullYear(), local.getUTCMonth() - monthsBack, 1) - BEIJING).toISOString().replace('.000Z', 'Z');
}

/** `YYYY-MM-01` of the Beijing month starting at `start` (the contract's `period_start` query is a calendar date). */
export const beijingMonthDate = (start: string) => new Date(Date.parse(start) + BEIJING).toISOString().slice(0, 10);

const base = (id: string, created: string, revision = 1) => ({ id, revision, created_at: created, updated_at: created });
const synthetic = (n: number) => `d0000000-0000-4000-8000-${n.toString(16).padStart(12, '0')}`;
const token = () => { const bytes = crypto.getRandomValues(new Uint8Array(32)); return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); };
const canonical = (value: unknown): string => Array.isArray(value) ? `[${value.map(canonical).join(',')}]`
  : typeof value === 'object' && value !== null ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical((value as Record<string, unknown>)[key])}`).join(',')}}` : JSON.stringify(value);
const maskEmail = (email: string) => { const [name, domain] = email.split('@'); return `${name!.slice(0, 1)}***@${domain}`; };
const isEmail = (value: unknown) => typeof value === 'string' && value.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
const isUuid = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
/** AdminWriteReason (0.4.0): 1–500 code points, one character outside the listed white space, no U+0000. */
// The contract pattern names these control characters as white space on purpose.
// eslint-disable-next-line no-control-regex
const REASON_VISIBLE = /[^\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000\uFEFF]/u;
const reasonOk = (value: unknown) => typeof value === 'string' && [...value].length >= 1 && [...value].length <= 500 && REASON_VISIBLE.test(value) && !value.includes('\u0000');
const MAX_SAFE = Number.MAX_SAFE_INTEGER;
const only = (body: Record<string, unknown>, keys: string[]) => Object.keys(body).every(key => keys.includes(key));
/** identity_api.verify_auth: the verified factor is good for 120 s, and an admin proof never outlives it. */
const PROOF_MS = 120_000;

/** Stage-10 personal limits (domain/billing.py quota_limits(10)); a larger allowance is an explicit adjustment. */
const STAGE10 = { asr_month_ms: 12 * 3_600_000, asr_day_ms: 3 * 3_600_000, retranscribe_month_ms: 2_400 * 3_600, interactive_month_count: 20, summary_month_count: 8 } as const;
export function syntheticQuota(now: string, settledAsrMs = 0): S['QuotaView'] {
  const month = { period_start: beijingMonthStart(now), period_end: beijingMonthStart(now, -1) };
  const dayStart = new Date(Math.floor((Date.parse(now) + BEIJING) / 86_400_000) * 86_400_000 - BEIJING).toISOString().replace('.000Z', 'Z');
  const day = { period_start: dayStart, period_end: new Date(Date.parse(dayStart) + 86_400_000).toISOString().replace('.000Z', 'Z') };
  const bucket = (kind: S['QuotaBucket']['kind'], period: typeof month, limit: number, settled = 0): S['QuotaBucket'] => ({ kind, ...period, limit, reserved: 0, submitted: 0, settled, unknown: 0, remaining: Math.max(0, limit - settled) });
  return { platform_billing_state: 'normal', asr_enabled: true, ai_enabled: true, buckets: [
    bucket('asr_month_ms', month, STAGE10.asr_month_ms, settledAsrMs), bucket('asr_day_ms', day, STAGE10.asr_day_ms), bucket('interactive_month_count', month, STAGE10.interactive_month_count),
    bucket('summary_month_count', month, STAGE10.summary_month_count), bucket('retranscribe_month_ms', month, STAGE10.retranscribe_month_ms),
  ] };
}

export function createAdminBackend(host: AdminHost, accountUsers: S['User'][]) {
  const extra: S['User'][] = [
    { ...base(synthetic(1), '2026-09-12T02:00:00Z'), email: 'student-c@example.invalid', nickname: '周同学', school: '', major: '', role: 'student', status: 'active', mfa_enabled: false },
    { ...base(synthetic(2), '2026-10-02T09:30:00Z', 3), email: 'student-d@example.invalid', nickname: '吴同学', school: '', major: '', role: 'student', status: 'disabled', mfa_enabled: false },
    { ...base(synthetic(3), '2026-10-25T11:00:00Z'), email: 'student-e@example.invalid', nickname: '', school: '', major: '', role: 'student', status: 'pending', mfa_enabled: false },
    { ...base(synthetic(4), '2026-10-05T01:00:00Z', 2), email: 'student-f@example.invalid', nickname: '郑同学', school: '', major: '', role: 'student', status: 'deleting', mfa_enabled: true },
  ];
  // Platform-held users that are not Mock accounts; Mock account users always come from their own store.
  const others = new Map(extra.map(user => [user.id, user]));
  const accountIds = accountUsers.map(user => user.id);
  const otherQuotas = new Map<string, S['QuotaView']>();
  const user = (id: string) => host.accountUser(id) ?? others.get(id) ?? null;
  const users = () => [...accountIds.map(id => host.accountUser(id)).filter((item): item is S['User'] => item !== null), ...others.values()]
    .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
  const saveUser = (next: S['User']) => { if (host.accountUser(next.id)) host.setAccountUser(next); else others.set(next.id, next); };
  const quotaOf = (id: string) => host.accountQuota(id) ?? otherQuotas.get(id) ?? (() => { const quota = applyGates(syntheticQuota(host.now())); otherQuotas.set(id, quota); return quota; })();
  const saveQuota = (id: string, quota: S['QuotaView']) => { if (host.accountQuota(id)) host.setAccountQuota(id, quota); else otherQuotas.set(id, quota); };
  /** Admin-owned quota changes per user, so a reseeded account fixture gets them back. */
  const adjustments = new Map<string, Array<{ kind: S['QuotaBucket']['kind']; period_start: string; units: number }>>();
  const withAdjustments = (userId: string, quota: S['QuotaView']): S['QuotaView'] => {
    let next = applyGates(quota);
    for (const change of adjustments.get(userId) ?? []) next = { ...next, buckets: next.buckets.map(item => item.kind === change.kind && Date.parse(item.period_start) === Date.parse(change.period_start) ? { ...item, limit: item.limit + change.units, remaining: item.remaining + change.units } : item) };
    return next;
  };

  const expiresIn = (days: number) => new Date(Date.parse(host.now()) + days * 86_400_000).toISOString().replace('.000Z', 'Z');
  let invitations: S['Invitation'][] = [
    { ...base(synthetic(0x101), '2026-10-27T03:00:00Z'), kind: 'email_bound', email_hint: 's***@example.invalid', state: 'issued', expires_at: '2026-11-03T03:00:00Z' },
    { ...base(synthetic(0x102), '2026-10-10T03:00:00Z', 2), kind: 'single_use', email_hint: null, state: 'consumed', expires_at: '2026-10-17T03:00:00Z' },
    { ...base(synthetic(0x103), '2026-10-12T03:00:00Z', 2), kind: 'email_bound', email_hint: 'w***@example.invalid', state: 'revoked', expires_at: '2026-10-19T03:00:00Z' },
    { ...base(synthetic(0x104), '2026-10-01T03:00:00Z'), kind: 'single_use', email_hint: null, state: 'expired', expires_at: '2026-10-08T03:00:00Z' },
  ];
  let switches: SwitchConfig = { revision: 1, registration_enabled: true, max_active_users: '10', billing_enabled: true, new_capture_enabled: true, asr_streaming_enabled: true, asr_filetrans_enabled: true, ai_interaction_enabled: true, ai_summary_enabled: true };
  /** Public view: a group summary is on when either member is configured on (independent of billing). */
  const switchView = (): S['AdminSwitches'] => ({ ...switches, asr_enabled: switches.asr_streaming_enabled || switches.asr_filetrans_enabled, ai_enabled: switches.ai_interaction_enabled || switches.ai_summary_enabled });
  /** Whether a service may admit new billable work: the billing master and its own switch. */
  const serviceEnabled = (service: ServiceSwitch) => switches.billing_enabled && switches[service];
  const adminId = accountUsers.find(item => item.role === 'admin')?.id ?? synthetic(0xfff);
  const audit: S['AuditRecord'][] = [
    { id: synthetic(0x201), actor_id: adminId, operation: 'adminUpdateUser', target_type: 'user', target_id: synthetic(2), reason: '学生本人申请暂停使用（合成记录）', created_at: '2026-10-20T06:00:00Z' },
    { id: synthetic(0x202), actor_id: adminId, operation: 'adminRevokeInvitation', target_type: 'invitation', target_id: synthetic(0x103), reason: '邮箱填写错误（合成记录）', created_at: '2026-10-12T05:00:00Z' },
  ];
  const usage = new Map<string, S['AdminUsage']>();
  const period = (start: string, values: Omit<S['AdminUsage'], 'period_start' | 'period_end' | 'price_version' | 'ledger_reconciled_at'>, reconciled: string | null) => {
    const end = beijingMonthStart(new Date(Date.parse(start) + 20 * 86_400_000).toISOString(), -1);
    usage.set(start, { period_start: start, period_end: end, ...values, price_version: 'synthetic-2026-10', ledger_reconciled_at: reconciled });
  };
  const current = beijingMonthStart(host.now());
  // Stage-10 platform pool (platform_limits(10)): ¥500 in total, ¥100 safety reserve, ¥100 fixed.
  period(current, { budget_microyuan: '500000000', fixed_and_accrued_microyuan: '100000000', settled_microyuan: '180000000', reserved_microyuan: '20600000', unknown_microyuan: '3800000', safety_reserve_microyuan: '100000000' }, '2026-10-28T07:30:00Z');
  period(beijingMonthStart(host.now(), 1), { budget_microyuan: '500000000', fixed_and_accrued_microyuan: '100000000', settled_microyuan: '168000000', reserved_microyuan: '0', unknown_microyuan: '0', safety_reserve_microyuan: '100000000' }, '2026-10-01T02:00:00Z');
  let healthDev: AdminHealthDev = 'healthy';
  const proofs = new Map<string, { binding: string; actor: string; generation: number; expires: number; used: boolean }>();
  const receipts = new Map<string, { body: string; reply: AdminReply }>();
  const SECRET_ISSUED: AdminReply = { status: 0, body: null };

  const fail = (status: number, code: ErrorCode, message: string, currentRevision: number | null = null): AdminReply =>
    ({ status, body: { error: { code, message, request_id: host.newId(), retryable: false, retry_after_seconds: null, current_revision: currentRevision, conflict_id: null, task_id: null } } });
  const ok = (body: unknown, status = 200): AdminReply => ({ status, body });
  const touch = <T extends { revision: number; updated_at: string }>(value: T): T => ({ ...value, revision: value.revision + 1, updated_at: host.now() });
  /** billing_state() over the current month of the platform ledger (the same numbers the usage page shows). */
  function platformState(): S['QuotaView']['platform_billing_state'] {
    const month = usage.get(current);
    if (!month) return 'normal';
    const budget = BigInt(month.budget_microyuan);
    const risk = BigInt(month.fixed_and_accrued_microyuan) + BigInt(month.settled_microyuan) + BigInt(month.reserved_microyuan) + BigInt(month.unknown_microyuan);
    if (budget === 0n || risk >= budget) return 'blocked100';
    // billing_state(risk, budget, enabled): the master switch pauses billable work below 100 %.
    if (!switches.billing_enabled) return 'paused';
    if (risk * 100n >= budget * 80n) return 'warning80';
    return risk * 100n >= budget * 60n ? 'warning60' : 'normal';
  }
  // QuotaView (0.4.0): a group is enabled when the billing master and at least one member are on.
  function applyGates(quota: S['QuotaView']): S['QuotaView'] { return { ...quota, asr_enabled: serviceEnabled('asr_streaming_enabled') || serviceEnabled('asr_filetrans_enabled'), ai_enabled: serviceEnabled('ai_interaction_enabled') || serviceEnabled('ai_summary_enabled'), platform_billing_state: platformState() }; }

  /**
   * Cursor = the id of the last record returned, scoped to the list. Records added before it (newer audit
   * entries) never shift a continuation; an unknown key is an expired cursor.
   */
  function paged<T extends { id: string }>(scope: string, list: T[], query: URLSearchParams): AdminReply {
    const limitText = query.get('limit');
    const limit = limitText === null ? 50 : Number(limitText);
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) return fail(422, 'VALIDATION_FAILED', '分页大小无效');
    const cursor = query.get('cursor');
    let offset = 0;
    if (cursor !== null) {
      const match = new RegExp(`^${scope}:([0-9a-f-]{36})$`).exec(cursor);
      const index = match ? list.findIndex(item => item.id === match[1]) : -1;
      if (index < 0) return fail(400, 'CURSOR_EXPIRED', '分页位置已失效，请从头读取');
      offset = index + 1;
    }
    const items = list.slice(offset, offset + limit);
    return ok({ items, next_cursor: offset + limit < list.length ? `${scope}:${items[items.length - 1]!.id}` : null });
  }

  /** The exact operation a proof commits to: purpose, target and future write (key + body). */
  const operationOf = (operationId: AdminOperationId, actorId: string, pathId: string | null, key: string, body: unknown) =>
    canonical({ purpose: operationId, target: proofTarget(operationId, actorId, pathId), candidate_parameters: { idempotency_key: key, body } });
  function grant(actorId: string, operation: string) {
    const value = token();
    const expires = host.clockMs() + PROOF_MS;
    proofs.set(value, { binding: operation, actor: actorId, generation: host.sessionGeneration(actorId), expires, used: false });
    return { reauth_token: value, expires_at: new Date(expires).toISOString() } satisfies S['ReauthResult'];
  }
  /** Test helper: the same proof the HTTP route issues, without the transport. */
  function issueProof(actorId: string, binding: AdminReauthBinding, password: string, code: string): { token: string; expires_at: string } | { error: 'FORBIDDEN' | 'MFA_REQUIRED' | 'REAUTH_FAILED' } {
    const actor = user(actorId);
    if (!actor || actor.role !== 'admin' || actor.status !== 'active') return { error: 'FORBIDDEN' };
    if (!actor.mfa_enabled) return { error: 'MFA_REQUIRED' };
    // Synthetic factors: any non-empty password; any six digits except 000000 (the Mock's "wrong code").
    if (!password || !/^[0-9]{6}$/.test(code) || code === '000000') return { error: 'REAUTH_FAILED' };
    const granted = grant(actorId, operationOf(binding.operationId, actorId, binding.targetId, binding.idempotencyKey, binding.body));
    return { token: granted.reauth_token, expires_at: granted.expires_at };
  }
  /** Per-operation body validation, shared by proof issuance (before any factor) and the write. */
  function bodyError(operationId: AdminOperationId, body: Record<string, unknown>): AdminReply | null {
    const revision = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 1 && (value as number) <= MAX_SAFE;
    switch (operationId) {
      case 'adminUpdateUser':
        return !only(body, ['base_revision', 'status', 'reason']) || !revision(body.base_revision) || !['active', 'disabled'].includes(body.status as string) || !reasonOk(body.reason)
          ? fail(422, 'VALIDATION_FAILED', '状态变更无效：需要基础版本、目标状态和理由') : null;
      case 'adminCreateInvitation': {
        const kind = body.kind;
        const shape = kind === 'email_bound' ? only(body, ['kind', 'email', 'reason']) && isEmail(body.email) : kind === 'single_use' && only(body, ['kind', 'reason']);
        return !shape || !reasonOk(body.reason) ? fail(422, 'VALIDATION_FAILED', '邮箱邀请需要有效邮箱，单次邀请不填写邮箱；两者都需要理由') : null;
      }
      case 'adminRevokeInvitation':
      case 'adminReissueInvitation':
        return !only(body, ['reason']) || !reasonOk(body.reason) ? fail(422, 'VALIDATION_FAILED', '需要填写理由') : null;
      case 'adminAdjustQuota': {
        const kinds = ['asr_month_ms', 'asr_day_ms', 'retranscribe_month_ms', 'interactive_month_count', 'summary_month_count'];
        const dateTime = typeof body.period_start === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/.test(body.period_start) && !Number.isNaN(Date.parse(body.period_start));
        return !only(body, ['user_id', 'bucket_kind', 'period_start', 'additional_units', 'reason']) || !isUuid(body.user_id) || !kinds.includes(body.bucket_kind as string) || !dateTime
          || !revision(body.additional_units) || !reasonOk(body.reason) ? fail(422, 'VALIDATION_FAILED', '额度调整无效：需要用户、额度类型、周期、追加数量和理由') : null;
      }
      case 'adminUpdateSwitches': {
        const booleans = ['registration_enabled', 'asr_enabled', 'ai_enabled', 'billing_enabled', 'new_capture_enabled', 'asr_streaming_enabled', 'asr_filetrans_enabled', 'ai_interaction_enabled', 'ai_summary_enabled'];
        const groups: Array<[string, string[]]> = [['asr_enabled', ['asr_streaming_enabled', 'asr_filetrans_enabled']], ['ai_enabled', ['ai_interaction_enabled', 'ai_summary_enabled']]];
        return !only(body, ['base_revision', 'reason', 'max_active_users', ...booleans]) || !revision(body.base_revision) || !reasonOk(body.reason)
          || ![...booleans, 'max_active_users'].some(field => field in body) || booleans.some(field => field in body && typeof body[field] !== 'boolean')
          || ('max_active_users' in body && !['10', '30'].includes(body.max_active_users as string))
          // A group field and one of its members cannot appear in the same PATCH.
          || groups.some(([group, members]) => group in body && members.some(member => member in body))
          ? fail(422, 'VALIDATION_FAILED', '开关变更无效：需要基础版本、至少一项变更和理由；同组的汇总项和分项不能同时修改') : null;
      }
    }
  }
  /**
   * POST /auth/reauth for the six admin purposes (HTTP 0.4.0). The operation is validated before the
   * factors are checked, so an unusable request never consumes a TOTP step.
   */
  function reauthenticate(actorId: string, rawBody: unknown): AdminReply {
    const request = (typeof rawBody === 'object' && rawBody !== null && !Array.isArray(rawBody) ? rawBody : {}) as Record<string, unknown>;
    const operation = (typeof request.operation === 'object' && request.operation !== null ? request.operation : {}) as Record<string, unknown>;
    const purpose = operation.purpose as AdminOperationId;
    const operations: AdminOperationId[] = ['adminUpdateUser', 'adminCreateInvitation', 'adminRevokeInvitation', 'adminReissueInvitation', 'adminAdjustQuota', 'adminUpdateSwitches'];
    if (!only(request, ['password', 'mfa_code', 'operation']) || typeof request.password !== 'string' || !operations.includes(purpose)) return fail(422, 'VALIDATION_FAILED', 'Mock 只提供管理员再认证用途');
    const actor = user(actorId);
    if (!actor || actor.role !== 'admin' || actor.status !== 'active') return fail(403, 'FORBIDDEN', '当前账号无权执行管理操作');
    if (!actor.mfa_enabled) return fail(403, 'MFA_REQUIRED', '管理员需要先启用双重验证');
    const target = (typeof operation.target === 'object' && operation.target !== null ? operation.target : {}) as Record<string, unknown>;
    const candidate = (typeof operation.candidate_parameters === 'object' && operation.candidate_parameters !== null ? operation.candidate_parameters : {}) as Record<string, unknown>;
    const body = candidate.body;
    if (!only(operation, ['purpose', 'target', 'candidate_parameters']) || !only(target, ['kind', 'id']) || !only(candidate, ['idempotency_key', 'body'])
      || typeof candidate.idempotency_key !== 'string' || !CANONICAL_UUID.test(candidate.idempotency_key) || typeof target.id !== 'string' || !isUuid(target.id)
      || typeof body !== 'object' || body === null || Array.isArray(body)) return fail(422, 'VALIDATION_FAILED', '再认证操作格式无效');
    const expected = proofTarget(purpose, actorId, target.id);
    if (target.kind !== expected.kind) return fail(422, 'VALIDATION_FAILED', '再认证目标类型与用途不符');
    // An account target is the authenticated administrator, in canonical lowercase.
    if (expected.kind === 'account' && !CANONICAL_UUID.test(target.id)) return fail(422, 'VALIDATION_FAILED', '账号目标必须是规范小写 UUID');
    if (expected.kind === 'account' && target.id !== actorId) return fail(403, 'FORBIDDEN', '账号目标必须是当前管理员');
    const invalid = bodyError(purpose, body as Record<string, unknown>);
    if (invalid) return invalid;
    if (typeof request.mfa_code !== 'string' || !/^[0-9]{6}$/.test(request.mfa_code)) return fail(422, 'VALIDATION_FAILED', '需要 6 位验证码');
    // Synthetic factors: any non-empty password; any six digits except 000000 (the Mock's "wrong code").
    if (!request.password || request.mfa_code === '000000') return fail(403, 'REAUTH_REQUIRED', '密码或验证码不正确');
    return ok(grant(actorId, operationOf(purpose, actorId, expected.kind === 'account' ? null : target.id, candidate.idempotency_key, body)));
  }

  /** Replay first (a lost reply is recovered with the same key), then the single-use proof, then the write. */
  function write(actorId: string, operationId: AdminOperationId, targetId: string | null, key: string | null, reauth: string | null, body: Record<string, unknown>, secret: boolean, run: () => AdminReply, replayed?: (stored: AdminReply) => AdminReply): AdminReply {
    if (!isUuid(key)) return fail(422, 'VALIDATION_FAILED', '缺少幂等键');
    // The header is required on every write, a replay included (it is only not re-consumed then).
    if (!reauth || !/^[A-Za-z0-9_-]{43,256}$/.test(reauth)) return fail(403, 'REAUTH_REQUIRED', '请重新验证密码和验证码');
    const scope = `${actorId}:${operationId}:${key}`;
    // Path target and body together, as the contract's receipt fingerprint does.
    const serialized = canonical({ targetId, body });
    const prior = receipts.get(scope);
    const binding = operationOf(operationId, actorId, ACCOUNT_TARGETED.has(operationId) ? null : targetId, key as string, body);
    const proof = reauth ? proofs.get(reauth) : undefined;
    const proofValid = (allowUsed: boolean) => proof !== undefined && (allowUsed || !proof.used) && proof.actor === actorId && proof.generation === host.sessionGeneration(actorId) && proof.expires > host.clockMs() && proof.binding === binding;
    if (prior) {
      if (prior.body !== serialized) return fail(409, 'IDEMPOTENCY_MISMATCH', '同一操作键对应了不同的对象或内容');
      // A replay is authorised like the write: a current-session proof bound to this exact write (the
      // original one or a fresh one). It is answered from the receipt and never applied again.
      if (!proofValid(true)) return fail(403, 'REAUTH_REQUIRED', '请重新验证密码和验证码');
      if (proof && !proof.used) proof.used = true;
      // A secret-bearing success is never replayed: only its non-secret status was kept.
      if (prior.reply === SECRET_ISSUED) return fail(409, 'SECRET_ALREADY_ISSUED', '该操作已完成，一次性令牌不会再次显示');
      // Applied once; what is returned now is rechecked against current access (as BillingAdmin.apply does).
      return replayed && prior.reply.status < 300 ? replayed(prior.reply) : prior.reply;
    }
    if (!proofValid(false) || !proof) return fail(403, 'REAUTH_REQUIRED', '请重新验证密码和验证码');
    const reply = run();
    // A rejected billing write is rolled back by the producer: the proof stays unused and no receipt exists.
    if (BILLING_WRITES.has(operationId) && reply.status >= 400) return reply;
    proof.used = true;
    // The one-time token of an invitation is never kept in a receipt (frozen endpoint rule).
    receipts.set(scope, { body: serialized, reply: secret && reply.status < 300 ? SECRET_ISSUED : reply });
    return reply;
  }
  function record(actorId: string, operation: AdminOperationId, targetType: string, targetId: string | null, reason: string) {
    audit.unshift({ id: host.newId(), actor_id: actorId, operation, target_type: targetType, target_id: targetId, reason, created_at: host.now() });
  }

  function health(): S['AdminHealth'] {
    const at = host.now();
    const minus = (seconds: number) => new Date(Date.parse(at) - seconds * 1000).toISOString().replace('.000Z', 'Z');
    if (healthDev === 'stalled') return { checked_at: at, database: 'healthy', objects: 'degraded', worker_heartbeat_at: minus(900), oldest_queued_seconds: 1260, unknown_task_count: 3, pending_delete_count: 14, failed_delete_count: 2, active_asr_streams: 0, last_backup_at: minus(3 * 86_400) };
    if (healthDev === 'degraded') return { checked_at: at, database: 'degraded', objects: 'healthy', worker_heartbeat_at: minus(40), oldest_queued_seconds: 180, unknown_task_count: 1, pending_delete_count: 3, failed_delete_count: 0, active_asr_streams: 2, last_backup_at: minus(26 * 3600) };
    return { checked_at: at, database: 'healthy', objects: 'healthy', worker_heartbeat_at: minus(8), oldest_queued_seconds: 4, unknown_task_count: 0, pending_delete_count: 0, failed_delete_count: 0, active_asr_streams: 1, last_backup_at: minus(5 * 3600) };
  }

  /** `/admin/*` for the authenticated caller. Role and factor come from the server-side user, never the request. */
  function handle(actorId: string, method: string, parts: string[], query: URLSearchParams, rawBody: unknown, key: string | null, reauth: string | null): AdminReply {
    const actor = user(actorId);
    if (!actor || actor.role !== 'admin') return fail(403, 'FORBIDDEN', '当前账号无权访问运营后台');
    if (!actor.mfa_enabled) return fail(403, 'MFA_REQUIRED', '管理员需要先启用双重验证');
    const isObject = typeof rawBody === 'object' && rawBody !== null && !Array.isArray(rawBody);
    // Every admin write carries a JSON object; a missing or malformed body is never treated as {}.
    if (method !== 'GET' && !isObject) return fail(422, 'VALIDATION_FAILED', '请求体必须是 JSON 对象');
    const body = (isObject ? rawBody : {}) as Record<string, unknown>;
    const route = `${method} /${parts.map(part => (isUuid(part) ? ':id' : part)).join('/')}`;
    const id = parts.find(isUuid) ?? '';
    switch (route) {
      case 'GET /admin/users': return paged('u', users(), query);
      case 'GET /admin/users/:id/quota': {
        // Like the billing backend: only an active owner has a quota projection.
        const target = user(id);
        return target?.status === 'active' ? ok(quotaOf(target.id)) : fail(404, 'NOT_FOUND', '用户不存在或未启用');
      }
      case 'PATCH /admin/users/:id': {
        const invalid = bodyError('adminUpdateUser', body); if (invalid) return invalid;
        return write(actorId, 'adminUpdateUser', id, key, reauth, body, false, () => {
          const target = user(id);
          if (!target) return fail(404, 'NOT_FOUND', '用户不存在');
          if (target.id === actorId) return fail(403, 'FORBIDDEN', '不能修改自己的账号状态');
          if (target.status === 'deleting') return fail(410, 'GONE', '该账号正在注销');
          if (target.revision !== body.base_revision) return fail(409, 'REVISION_CONFLICT', '账号状态已被更新', target.revision);
          if (target.status === 'pending') return fail(422, 'VALIDATION_FAILED', '账号尚未完成注册，不能修改状态');
          const next = touch({ ...target, status: body.status as 'active' | 'disabled' });
          saveUser(next);
          if (next.status === 'disabled') host.revokeSessions(next.id);
          record(actorId, 'adminUpdateUser', 'user', next.id, body.reason as string);
          return ok(next);
        });
      }
      case 'GET /admin/invitations': return paged('i', [...invitations].sort((a, b) => b.created_at.localeCompare(a.created_at) || a.id.localeCompare(b.id)), query);
      case 'POST /admin/invitations': {
        const kind = body.kind;
        const invalid = bodyError('adminCreateInvitation', body); if (invalid) return invalid;
        return write(actorId, 'adminCreateInvitation', null, key, reauth, body, true, () => {
          const created: S['Invitation'] = { ...base(host.newId(), host.now()), kind: kind as S['Invitation']['kind'], email_hint: kind === 'email_bound' ? maskEmail(body.email as string) : null, state: 'issued', expires_at: expiresIn(7) };
          invitations = [created, ...invitations];
          record(actorId, 'adminCreateInvitation', 'invitation', created.id, body.reason as string);
          return ok({ invitation: created, one_time_token: token() } satisfies S['InvitationCreated'], 201);
        });
      }
      case 'POST /admin/invitations/:id/revoke':
      case 'POST /admin/invitations/:id/reissue': {
        const reissue = parts[3] === 'reissue';
        const invalid = bodyError(reissue ? 'adminReissueInvitation' : 'adminRevokeInvitation', body); if (invalid) return invalid;
        return write(actorId, reissue ? 'adminReissueInvitation' : 'adminRevokeInvitation', id, key, reauth, body, reissue, () => {
          const target = invitations.find(item => item.id === id);
          if (!target) return fail(404, 'NOT_FOUND', '邀请不存在');
          if (reissue ? !['issued', 'expired'].includes(target.state) : target.state !== 'issued') return fail(409, 'INVITATION_UNAVAILABLE', reissue ? '已使用或已撤销的邀请不能重新签发' : '只有未使用的邀请可以撤销');
          const next = touch({ ...target, state: reissue ? 'issued' as const : 'revoked' as const, ...(reissue ? { expires_at: expiresIn(7) } : {}) });
          invitations = invitations.map(item => item.id === id ? next : item);
          record(actorId, reissue ? 'adminReissueInvitation' : 'adminRevokeInvitation', 'invitation', id, body.reason as string);
          return reissue ? ok({ invitation: next, one_time_token: token() } satisfies S['InvitationCreated']) : ok(next);
        });
      }
      case 'POST /admin/quota-adjustments': {
        const invalid = bodyError('adminAdjustQuota', body); if (invalid) return invalid;
        return write(actorId, 'adminAdjustQuota', body.user_id as string, key, reauth, body, false, () => {
          const target = user(body.user_id as string);
          if (target?.status !== 'active') return fail(404, 'NOT_FOUND', '用户不存在或未启用');
          const quota = quotaOf(target.id);
          const bucket = quota.buckets.find(item => item.kind === body.bucket_kind && Date.parse(item.period_start) === Date.parse(body.period_start as string));
          if (!bucket) return fail(422, 'VALIDATION_FAILED', '该周期没有对应的额度桶');
          const units = body.additional_units as number;
          // The resulting limit stays within the safe integer range of the public DTO.
          if (bucket.limit + units > MAX_SAFE) return fail(422, 'VALIDATION_FAILED', '调整后的额度上限超出范围');
          const next = { ...quota, buckets: quota.buckets.map(item => item === bucket ? { ...item, limit: item.limit + units, remaining: item.remaining + units } : item) };
          saveQuota(target.id, next);
          adjustments.set(target.id, [...(adjustments.get(target.id) ?? []), { kind: bucket.kind, period_start: bucket.period_start, units }]);
          record(actorId, 'adminAdjustQuota', 'user', target.id, body.reason as string);
          return ok(next, 201);
        }, () => {
          const target = user(body.user_id as string);
          return target?.status === 'active' ? ok(quotaOf(target.id), 201) : fail(404, 'NOT_FOUND', '用户不存在或未启用');
        });
      }
      case 'GET /admin/usage': {
        // `format: date`: the first day of a Beijing month, as the HTTP service accepts it (YYYY-MM-01).
        const start = query.get('period_start');
        if (start !== null && !/^\d{4}-(0[1-9]|1[0-2])-01$/.test(start)) return fail(422, 'VALIDATION_FAILED', '周期起点必须是某月 1 日（YYYY-MM-01）');
        const found = start === null ? usage.get(current) : [...usage.values()].find(item => beijingMonthDate(item.period_start) === start);
        return found ? ok(found) : fail(404, 'NOT_FOUND', '该周期没有用量记录');
      }
      case 'GET /admin/health': return ok(health());
      case 'GET /admin/switches': return ok(switchView());
      case 'PATCH /admin/switches': {
        const invalid = bodyError('adminUpdateSwitches', body); if (invalid) return invalid;
        return write(actorId, 'adminUpdateSwitches', null, key, reauth, body, false, () => {
          if (switches.revision !== body.base_revision) return fail(409, 'REVISION_CONFLICT', '平台开关已被其他管理员更新', switches.revision);
          if ('max_active_users' in body && body.max_active_users !== switches.max_active_users) return fail(503, 'SERVICE_DISABLED', '活跃用户阶段切换尚未开放');
          // Capture lease admission does not enforce this setting yet (0.4.0): changing it is refused.
          if ('new_capture_enabled' in body && body.new_capture_enabled !== switches.new_capture_enabled) return fail(503, 'SERVICE_DISABLED', '新采集开关暂未接入采集准入');
          const change: Partial<SwitchConfig> = {};
          for (const field of ['registration_enabled', 'billing_enabled', 'new_capture_enabled', 'asr_streaming_enabled', 'asr_filetrans_enabled', 'ai_interaction_enabled', 'ai_summary_enabled'] as const) if (field in body) change[field] = body[field] as boolean;
          if ('max_active_users' in body) change.max_active_users = body.max_active_users as SwitchConfig['max_active_users'];
          // A group field sets both of its members.
          if ('asr_enabled' in body) { change.asr_streaming_enabled = body.asr_enabled as boolean; change.asr_filetrans_enabled = body.asr_enabled as boolean; }
          if ('ai_enabled' in body) { change.ai_interaction_enabled = body.ai_enabled as boolean; change.ai_summary_enabled = body.ai_enabled as boolean; }
          switches = { ...switches, ...change, revision: switches.revision + 1 };
          host.applySwitches(switchView());
          for (const [userId, quota] of otherQuotas) otherQuotas.set(userId, applyGates(quota));
          record(actorId, 'adminUpdateSwitches', 'platform', null, body.reason as string);
          return ok(switchView());
        });
      }
      case 'GET /admin/audit': return paged('a', audit, query);
      default: return fail(404, 'NOT_FOUND', '接口不存在');
    }
  }

  return {
    handle,
    issueProof,
    reauthenticate,
    /** Whether a service may admit new billable work (billing master and its own switch). */
    serviceEnabled,
    setHealthDev: (value: AdminHealthDev) => { healthDev = value; },
    switches: () => switchView(),
    /** A reseeded account fixture keeps the platform switches and the admin's quota adjustments. */
    reapply: (userId: string, quota: S['QuotaView']) => withAdjustments(userId, quota),
    /** Platform switches and billing state applied to a quota view. */
    gate: (quota: S['QuotaView']) => applyGates(quota),
    /** Test helper: the audit trail as stored. */
    audit: () => audit,
  };
}
