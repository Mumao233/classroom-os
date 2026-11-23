import { describe, expect, it } from 'vitest';
import type { components } from '../src/generated/openapi';
import { createMockBackend, type MockAccountKey } from '../src/mock/backend';
import type { AdminOperationId } from '../src/mock/admin-backend';

type S = components['schemas'];
const A_ID = 'a0000000-0000-4000-8000-000000000001';
const B_ID = 'b0000000-0000-4000-8000-000000000001';
const C_ID = 'c9000000-0000-4000-8000-000000000001';
const PENDING = 'd0000000-0000-4000-8000-000000000003';
const DELETING = 'd0000000-0000-4000-8000-000000000004';
const ISSUED = 'd0000000-0000-4000-8000-000000000101';
const CONSUMED = 'd0000000-0000-4000-8000-000000000102';
const EXPIRED = 'd0000000-0000-4000-8000-000000000104';

function harness() {
  let clock = 1_000_000;
  const backend = createMockBackend({ clockMs: () => clock });
  let n = 0;
  const newKey = () => `00000000-0000-4000-8000-${String(++n).padStart(12, '0')}`;
  const call = (account: MockAccountKey, method: string, path: string, body?: unknown, query = '', key?: string | null, reauth?: string | null) =>
    backend.handle(account, method, path, new URLSearchParams(query), body, key === undefined ? (method === 'GET' ? null : newKey()) : key, reauth ?? null)!;
  /** An admin write with a fresh, correctly bound proof (the purpose the contract still lacks). */
  const write = (operationId: AdminOperationId, targetId: string | null, method: string, path: string, body: unknown, key = newKey()) => {
    const proof = backend.issueAdminProof('C', { operationId, targetId, idempotencyKey: key, body }, 'synthetic-password', '123456');
    if ('error' in proof) throw new Error(proof.error);
    return { reply: call('C', method, path, body, '', key, proof.token), key, token: proof.token };
  };
  return { backend, call, write, newKey, advance: (ms: number) => { clock += ms; } };
}
const err = (reply: { body: unknown }) => (reply.body as S['Error']).error.code;
const items = <T>(reply: { body: unknown }) => (reply.body as { items: T[] }).items;

describe('PR-11b admin access', () => {
  it('students are refused on every admin route; an admin without MFA is refused too', () => {
    const { backend, call } = harness();
    for (const [method, path] of [['GET', '/admin/users'], ['GET', '/admin/invitations'], ['GET', '/admin/usage'], ['GET', '/admin/health'], ['GET', '/admin/switches'], ['GET', '/admin/audit'], ['GET', `/admin/users/${A_ID}/quota`], ['PATCH', `/admin/users/${B_ID}`], ['POST', '/admin/invitations'], ['PATCH', '/admin/switches']] as const) {
      for (const account of ['A', 'B'] as const) {
        const reply = call(account, method, path, {});
        expect(reply.status).toBe(403);
        expect(err(reply)).toBe('FORBIDDEN');
      }
    }
    expect(backend.issueAdminProof('A', { operationId: 'adminUpdateUser', targetId: B_ID, idempotencyKey: '00000000-0000-4000-8000-0000000000aa', body: {} }, 'p', '123456')).toEqual({ error: 'FORBIDDEN' });
    backend.setAdminMfa(false);
    expect(err(call('C', 'GET', '/admin/users'))).toBe('MFA_REQUIRED');
    expect(backend.issueAdminProof('C', { operationId: 'adminUpdateUser', targetId: B_ID, idempotencyKey: '00000000-0000-4000-8000-0000000000aa', body: {} }, 'p', '123456')).toEqual({ error: 'MFA_REQUIRED' });
  });

  it('lists carry account metadata only: no study content, no credentials, paged by cursor', () => {
    const { call } = harness();
    const all = items<S['User']>(call('C', 'GET', '/admin/users'));
    expect(all.map(user => user.id)).toEqual(expect.arrayContaining([A_ID, B_ID, C_ID, PENDING, DELETING]));
    const first = call('C', 'GET', '/admin/users', undefined, 'limit=3').body as S['UserPage'];
    expect(first.items).toHaveLength(3);
    const second = call('C', 'GET', '/admin/users', undefined, `limit=3&cursor=${encodeURIComponent(first.next_cursor!)}`).body as S['UserPage'];
    expect([...first.items, ...second.items].map(user => user.id)).toEqual(all.slice(0, first.items.length + second.items.length).map(user => user.id));
    expect(err(call('C', 'GET', '/admin/users', undefined, 'cursor=bogus'))).toBe('CURSOR_EXPIRED');
    expect(err(call('C', 'GET', '/admin/users', undefined, 'limit=101'))).toBe('VALIDATION_FAILED');
    const everything = JSON.stringify([call('C', 'GET', '/admin/users').body, call('C', 'GET', '/admin/invitations').body, call('C', 'GET', '/admin/audit').body, call('C', 'GET', `/admin/users/${A_ID}/quota`).body, call('C', 'GET', '/admin/usage').body, call('C', 'GET', '/admin/health').body]);
    for (const forbidden of ['body_markdown', 'transcript', 'question', 'password', 'one_time_token', 'reauth', '线性代数', '特征']) expect(everything).not.toContain(forbidden);
  });
});

describe('PR-11b admin writes need a purpose-bound, single-use proof', () => {
  const patch = { base_revision: 1, status: 'disabled', reason: '合成：学生申请暂停' };

  it('no proof, a wrong factor, or a proof bound to anything else is refused without effect', () => {
    const { backend, call, newKey } = harness();
    expect(err(call('C', 'PATCH', `/admin/users/${B_ID}`, patch))).toBe('REAUTH_REQUIRED');
    expect(backend.issueAdminProof('C', { operationId: 'adminUpdateUser', targetId: B_ID, idempotencyKey: newKey(), body: patch }, 'p', '000000')).toEqual({ error: 'REAUTH_FAILED' });
    expect(backend.issueAdminProof('C', { operationId: 'adminUpdateUser', targetId: B_ID, idempotencyKey: newKey(), body: patch }, '', '123456')).toEqual({ error: 'REAUTH_FAILED' });
    const key = newKey();
    const proof = backend.issueAdminProof('C', { operationId: 'adminUpdateUser', targetId: B_ID, idempotencyKey: key, body: patch }, 'p', '123456') as { token: string };
    // Another key, another body, another target, another operation: each is refused.
    expect(err(call('C', 'PATCH', `/admin/users/${B_ID}`, patch, '', newKey(), proof.token))).toBe('REAUTH_REQUIRED');
    expect(err(call('C', 'PATCH', `/admin/users/${B_ID}`, { ...patch, reason: '另一个理由' }, '', key, proof.token))).toBe('REAUTH_REQUIRED');
    expect(err(call('C', 'PATCH', `/admin/users/${A_ID}`, patch, '', key, proof.token))).toBe('REAUTH_REQUIRED');
    expect(err(call('C', 'PATCH', '/admin/switches', { base_revision: 1, reason: 'x', ai_enabled: false }, '', key, proof.token))).toBe('REAUTH_REQUIRED');
    expect(backend.accountUser('B').status).toBe('active');
    // The proof was not consumed by refusals: the bound request still succeeds once.
    expect(call('C', 'PATCH', `/admin/users/${B_ID}`, patch, '', key, proof.token).status).toBe(200);
    expect(backend.accountUser('B').status).toBe('disabled');
  });

  it('a proof expires after 300 s and is used once; a lost reply is recovered by replaying the same key', () => {
    const { backend, call, write, newKey, advance } = harness();
    const key = newKey();
    const late = backend.issueAdminProof('C', { operationId: 'adminUpdateUser', targetId: B_ID, idempotencyKey: key, body: patch }, 'p', '123456') as { token: string };
    advance(300_000);
    expect(err(call('C', 'PATCH', `/admin/users/${B_ID}`, patch, '', key, late.token))).toBe('REAUTH_REQUIRED');
    const first = write('adminUpdateUser', B_ID, 'PATCH', `/admin/users/${B_ID}`, patch);
    expect(first.reply.status).toBe(200);
    // Same key and body: the stored reply, even with the consumed (or an expired) proof — no second audit entry.
    advance(600_000);
    expect(call('C', 'PATCH', `/admin/users/${B_ID}`, patch, '', first.key, first.token)).toEqual(first.reply);
    expect(backend.adminAudit().filter(item => item.target_id === B_ID && item.created_at === '2026-10-28T08:00:00Z')).toHaveLength(1);
    expect(err(call('C', 'PATCH', `/admin/users/${B_ID}`, { ...patch, reason: '改过' }, '', first.key, first.token))).toBe('IDEMPOTENCY_MISMATCH');
    // A used proof cannot authorise a new request.
    expect(err(call('C', 'PATCH', `/admin/users/${B_ID}`, { ...patch, base_revision: 2, status: 'active' }, '', newKey(), first.token))).toBe('REAUTH_REQUIRED');
  });
});

describe('PR-11b user status, quota and switches', () => {
  it('disabling ends the account\'s sessions at once; restoring lets it back; self and deleting accounts are refused', () => {
    const { call, write } = harness();
    expect(call('A', 'GET', '/me').status).toBe(200);
    expect(write('adminUpdateUser', A_ID, 'PATCH', `/admin/users/${A_ID}`, { base_revision: 1, status: 'disabled', reason: '合成：停用检查' }).reply.status).toBe(200);
    expect(err(call('A', 'GET', '/me'))).toBe('UNAUTHENTICATED');
    expect(err(call('A', 'GET', '/courses'))).toBe('UNAUTHENTICATED');
    const stale = write('adminUpdateUser', A_ID, 'PATCH', `/admin/users/${A_ID}`, { base_revision: 1, status: 'active', reason: '合成：恢复' }).reply;
    expect(stale.status).toBe(409);
    expect((stale.body as S['Error']).error.current_revision).toBe(2);
    expect(write('adminUpdateUser', A_ID, 'PATCH', `/admin/users/${A_ID}`, { base_revision: 2, status: 'active', reason: '合成：恢复' }).reply.status).toBe(200);
    expect(call('A', 'GET', '/me').status).toBe(200);
    expect(err(write('adminUpdateUser', C_ID, 'PATCH', `/admin/users/${C_ID}`, { base_revision: 1, status: 'disabled', reason: '自己' }).reply)).toBe('FORBIDDEN');
    expect(err(write('adminUpdateUser', DELETING, 'PATCH', `/admin/users/${DELETING}`, { base_revision: 2, status: 'disabled', reason: 'x' }).reply)).toBe('GONE');
    expect(err(write('adminUpdateUser', PENDING, 'PATCH', `/admin/users/${PENDING}`, { base_revision: 1, status: 'disabled', reason: 'x' }).reply)).toBe('VALIDATION_FAILED');
    expect(err(call('C', 'PATCH', `/admin/users/${B_ID}`, { base_revision: 1, status: 'disabled', reason: '   ' }))).toBe('VALIDATION_FAILED');
    expect(err(call('C', 'PATCH', `/admin/users/${B_ID}`, { base_revision: 1, status: 'deleting', reason: 'x' }))).toBe('VALIDATION_FAILED');
  });

  it('a quota adjustment raises that bucket for the user (seen in their own /me/quota) and is audited with its reason', () => {
    const { call, write } = harness();
    const before = (call('A', 'GET', '/me/quota').body as S['QuotaView']).buckets.find(item => item.kind === 'asr_month_ms')!;
    const body = { user_id: A_ID, bucket_kind: 'asr_month_ms', period_start: before.period_start, additional_units: 1_800_000, reason: '合成：期中复习追加' };
    const reply = write('adminAdjustQuota', A_ID, 'POST', '/admin/quota-adjustments', body).reply;
    expect(reply.status).toBe(201);
    const after = (call('A', 'GET', '/me/quota').body as S['QuotaView']).buckets.find(item => item.kind === 'asr_month_ms')!;
    expect(after.limit - before.limit).toBe(1_800_000);
    expect(after.remaining - before.remaining).toBe(1_800_000);
    expect(after.settled).toBe(before.settled);
    expect(err(write('adminAdjustQuota', A_ID, 'POST', '/admin/quota-adjustments', { ...body, period_start: '2020-01-01T00:00:00Z' }).reply)).toBe('VALIDATION_FAILED');
    expect(err(call('C', 'POST', '/admin/quota-adjustments', { ...body, additional_units: 0 }))).toBe('VALIDATION_FAILED');
    const audit = items<S['AuditRecord']>(call('C', 'GET', '/admin/audit'));
    expect(audit[0]).toMatchObject({ actor_id: C_ID, operation: 'adminAdjustQuota', target_type: 'user', target_id: A_ID, reason: '合成：期中复习追加' });
  });

  it('switches use CAS and gate every account\'s quota view', () => {
    const { call, write } = harness();
    const switches = call('C', 'GET', '/admin/switches').body as S['AdminSwitches'];
    const saved = write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: switches.revision, asr_enabled: false, reason: '合成：供应商维护' }).reply;
    expect(saved.body).toMatchObject({ revision: switches.revision + 1, asr_enabled: false, ai_enabled: true });
    expect((call('A', 'GET', '/me/quota').body as S['QuotaView']).asr_enabled).toBe(false);
    expect((call('C', 'GET', `/admin/users/${PENDING}/quota`).body as S['QuotaView']).asr_enabled).toBe(false);
    const stale = write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: switches.revision, ai_enabled: false, reason: '旧版本' }).reply;
    expect(err(stale)).toBe('REVISION_CONFLICT');
    expect((stale.body as S['Error']).error.current_revision).toBe(switches.revision + 1);
    expect(err(call('C', 'PATCH', '/admin/switches', { base_revision: 2, reason: '没有变更' }))).toBe('VALIDATION_FAILED');
    expect(err(call('C', 'PATCH', '/admin/switches', { base_revision: 2, reason: 'x', max_active_users: '20' }))).toBe('VALIDATION_FAILED');
  });
});

describe('PR-11b invitations', () => {
  it('creation shows the one-time token once; a replay never shows it again; the audit has no token', () => {
    const { backend, call, write } = harness();
    const created = write('adminCreateInvitation', null, 'POST', '/admin/invitations', { kind: 'email_bound', email: 'new-student@example.invalid' });
    expect(created.reply.status).toBe(201);
    const body = created.reply.body as S['InvitationCreated'];
    expect(body.one_time_token).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(body.invitation).toMatchObject({ kind: 'email_bound', email_hint: 'n***@example.invalid', state: 'issued' });
    const replay = call('C', 'POST', '/admin/invitations', { kind: 'email_bound', email: 'new-student@example.invalid' }, '', created.key, created.token);
    expect(err(replay)).toBe('SECRET_ALREADY_ISSUED');
    expect(JSON.stringify(replay.body)).not.toContain(body.one_time_token);
    expect(JSON.stringify(backend.adminAudit())).not.toContain(body.one_time_token);
    expect(JSON.stringify(call('C', 'GET', '/admin/invitations').body)).not.toContain('new-student');
    for (const invalid of [{ kind: 'email_bound' }, { kind: 'single_use', email: 'x@example.invalid' }, { kind: 'admin' }, { kind: 'email_bound', email: 'not-an-email' }]) expect(err(call('C', 'POST', '/admin/invitations', invalid))).toBe('VALIDATION_FAILED');
  });

  it('revoke only an unused invitation; reissue an unused or expired one with a new token', () => {
    const { write } = harness();
    expect(err(write('adminRevokeInvitation', CONSUMED, 'POST', `/admin/invitations/${CONSUMED}/revoke`, {}).reply)).toBe('INVITATION_UNAVAILABLE');
    expect(err(write('adminReissueInvitation', CONSUMED, 'POST', `/admin/invitations/${CONSUMED}/reissue`, {}).reply)).toBe('INVITATION_UNAVAILABLE');
    const reissued = write('adminReissueInvitation', EXPIRED, 'POST', `/admin/invitations/${EXPIRED}/reissue`, {}).reply.body as S['InvitationCreated'];
    expect(reissued.invitation).toMatchObject({ state: 'issued', revision: 2 });
    expect(Date.parse(reissued.invitation.expires_at)).toBeGreaterThan(Date.parse('2026-10-28T08:00:00Z'));
    const revoked = write('adminRevokeInvitation', ISSUED, 'POST', `/admin/invitations/${ISSUED}/revoke`, {}).reply.body as S['Invitation'];
    expect(revoked.state).toBe('revoked');
    expect(err(write('adminReissueInvitation', ISSUED, 'POST', `/admin/invitations/${ISSUED}/reissue`, {}).reply)).toBe('INVITATION_UNAVAILABLE');
  });
});

describe('PR-11b usage and health', () => {
  it('usage defaults to the current Beijing month; an unknown period is NOT_FOUND', () => {
    const { call } = harness();
    const current = call('C', 'GET', '/admin/usage').body as S['AdminUsage'];
    expect(current.period_start).toBe('2026-09-30T16:00:00Z');
    expect(call('C', 'GET', '/admin/usage', undefined, 'period_start=2026-09-30T16:00:00Z').body).toEqual(current);
    expect((call('C', 'GET', '/admin/usage', undefined, 'period_start=2026-08-31T16:00:00Z').body as S['AdminUsage']).ledger_reconciled_at).not.toBeNull();
    expect(err(call('C', 'GET', '/admin/usage', undefined, 'period_start=2025-01-01T00:00:00Z'))).toBe('NOT_FOUND');
    expect(err(call('C', 'GET', '/admin/usage', undefined, 'period_start=yesterday'))).toBe('VALIDATION_FAILED');
  });
  it('health readings follow the development panel', () => {
    const { backend, call } = harness();
    expect((call('C', 'GET', '/admin/health').body as S['AdminHealth']).database).toBe('healthy');
    backend.setAdminHealth('stalled');
    expect(call('C', 'GET', '/admin/health').body).toMatchObject({ objects: 'degraded', unknown_task_count: 3, failed_delete_count: 2 });
  });
});
