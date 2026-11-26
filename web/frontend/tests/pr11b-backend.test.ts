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
    // Same key and body: the stored reply, never a second write. An expired proof no longer authorises the
    // replay; a fresh proof bound to the same write does.
    advance(600_000);
    expect(err(call('C', 'PATCH', `/admin/users/${B_ID}`, patch, '', first.key, first.token))).toBe('REAUTH_REQUIRED');
    const fresh = backend.issueAdminProof('C', { operationId: 'adminUpdateUser', targetId: B_ID, idempotencyKey: first.key, body: patch }, 'p', '123456') as { token: string };
    expect(call('C', 'PATCH', `/admin/users/${B_ID}`, patch, '', first.key, fresh.token)).toEqual(first.reply);
    expect(backend.adminAudit().filter(item => item.target_id === B_ID && item.created_at === '2026-10-28T08:00:00Z')).toHaveLength(1);
    expect(err(call('C', 'PATCH', `/admin/users/${B_ID}`, { ...patch, reason: '改过' }, '', first.key, first.token))).toBe('IDEMPOTENCY_MISMATCH');
    // A used proof cannot authorise a new request.
    expect(err(call('C', 'PATCH', `/admin/users/${B_ID}`, { ...patch, base_revision: 2, status: 'active' }, '', newKey(), first.token))).toBe('REAUTH_REQUIRED');
  });
});

describe('PR-11b user status, quota and switches', () => {
  it('disabling ends the account\'s sessions at once; restoring lets it back; self and deleting accounts are refused', () => {
    const { backend, call, write } = harness();
    expect(call('A', 'GET', '/me').status).toBe(200);
    expect(write('adminUpdateUser', A_ID, 'PATCH', `/admin/users/${A_ID}`, { base_revision: 1, status: 'disabled', reason: '合成：停用检查' }).reply.status).toBe(200);
    expect(err(call('A', 'GET', '/me'))).toBe('UNAUTHENTICATED');
    expect(err(call('A', 'GET', '/courses'))).toBe('UNAUTHENTICATED');
    const stale = write('adminUpdateUser', A_ID, 'PATCH', `/admin/users/${A_ID}`, { base_revision: 1, status: 'active', reason: '合成：恢复' }).reply;
    expect(stale.status).toBe(409);
    expect((stale.body as S['Error']).error.current_revision).toBe(2);
    expect(write('adminUpdateUser', A_ID, 'PATCH', `/admin/users/${A_ID}`, { base_revision: 2, status: 'active', reason: '合成：恢复' }).reply.status).toBe(200);
    // Restoring does not revive the sessions that disabling ended: only a fresh sign-in is admitted.
    expect(err(call('A', 'GET', '/me'))).toBe('UNAUTHENTICATED');
    backend.beginSession('A');
    expect(call('A', 'GET', '/me').status).toBe(200);
    // The ended sessions stay revoked; the fresh sign-in is the only current, unrevoked one.
    expect(items<S['DeviceSession']>(call('A', 'GET', '/me/sessions')).filter(item => item.revoked_at === null).map(item => item.is_current)).toEqual([true]);
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
    // An active platform user who is not a Mock account (inactive owners have no quota projection).
    expect((call('C', 'GET', '/admin/users/d0000000-0000-4000-8000-000000000001/quota').body as S['QuotaView']).asr_enabled).toBe(false);
    const stale = write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: switches.revision, ai_enabled: false, reason: '旧版本' }).reply;
    expect(err(stale)).toBe('REVISION_CONFLICT');
    expect((stale.body as S['Error']).error.current_revision).toBe(switches.revision + 1);
    expect(err(call('C', 'PATCH', '/admin/switches', { base_revision: 2, reason: '没有变更' }))).toBe('VALIDATION_FAILED');
    expect(err(call('C', 'PATCH', '/admin/switches', { base_revision: 2, reason: 'x', max_active_users: '20' }))).toBe('VALIDATION_FAILED');
    // Stage transitions wait for the approved stage rule (11a): refused, nothing changes.
    expect(err(write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: switches.revision + 1, max_active_users: '30', reason: '合成' }).reply)).toBe('SERVICE_DISABLED');
    expect((call('C', 'GET', '/admin/switches').body as S['AdminSwitches']).max_active_users).toBe('10');
  });
});

describe('PR-11b invitations', () => {
  it('creation shows the one-time token once; a replay never shows it again; the audit has no token', () => {
    const { backend, call, write } = harness();
    const created = write('adminCreateInvitation', null, 'POST', '/admin/invitations', { kind: 'email_bound', email: 'new-student@example.invalid', reason: '合成：新同学' });
    expect(created.reply.status).toBe(201);
    const body = created.reply.body as S['InvitationCreated'];
    expect(body.one_time_token).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(body.invitation).toMatchObject({ kind: 'email_bound', email_hint: 'n***@example.invalid', state: 'issued' });
    const replay = call('C', 'POST', '/admin/invitations', { kind: 'email_bound', email: 'new-student@example.invalid', reason: '合成：新同学' }, '', created.key, created.token);
    expect(err(replay)).toBe('SECRET_ALREADY_ISSUED');
    expect(JSON.stringify(replay.body)).not.toContain(body.one_time_token);
    expect(JSON.stringify(backend.adminAudit())).not.toContain(body.one_time_token);
    expect(JSON.stringify(call('C', 'GET', '/admin/invitations').body)).not.toContain('new-student');
    for (const invalid of [{ kind: 'email_bound', reason: 'r' }, { kind: 'single_use', email: 'x@example.invalid', reason: 'r' }, { kind: 'admin', reason: 'r' }, { kind: 'email_bound', email: 'not-an-email', reason: 'r' }, { kind: 'single_use' }, { kind: 'single_use', reason: '\uFEFF' }]) expect(err(call('C', 'POST', '/admin/invitations', invalid))).toBe('VALIDATION_FAILED');
  });

  it('revoke only an unused invitation; reissue an unused or expired one with a new token', () => {
    const { write } = harness();
    expect(err(write('adminRevokeInvitation', CONSUMED, 'POST', `/admin/invitations/${CONSUMED}/revoke`, { reason: '合成' }).reply)).toBe('INVITATION_UNAVAILABLE');
    expect(err(write('adminReissueInvitation', CONSUMED, 'POST', `/admin/invitations/${CONSUMED}/reissue`, { reason: '合成' }).reply)).toBe('INVITATION_UNAVAILABLE');
    const reissued = write('adminReissueInvitation', EXPIRED, 'POST', `/admin/invitations/${EXPIRED}/reissue`, { reason: '合成' }).reply.body as S['InvitationCreated'];
    expect(reissued.invitation).toMatchObject({ state: 'issued', revision: 2 });
    expect(Date.parse(reissued.invitation.expires_at)).toBeGreaterThan(Date.parse('2026-10-28T08:00:00Z'));
    const revoked = write('adminRevokeInvitation', ISSUED, 'POST', `/admin/invitations/${ISSUED}/revoke`, { reason: '合成' }).reply.body as S['Invitation'];
    expect(revoked.state).toBe('revoked');
    expect(err(write('adminReissueInvitation', ISSUED, 'POST', `/admin/invitations/${ISSUED}/reissue`, { reason: '合成' }).reply)).toBe('INVITATION_UNAVAILABLE');
  });
});

describe('PR-11b usage and health', () => {
  it('usage defaults to the current Beijing month; an unknown period is NOT_FOUND', () => {
    const { call } = harness();
    const current = call('C', 'GET', '/admin/usage').body as S['AdminUsage'];
    expect(current.period_start).toBe('2026-09-30T16:00:00Z');
    // `format: date`, the first day of the Beijing month, as the HTTP service accepts it.
    expect(call('C', 'GET', '/admin/usage', undefined, 'period_start=2026-10-01').body).toEqual(current);
    expect((call('C', 'GET', '/admin/usage', undefined, 'period_start=2026-09-01').body as S['AdminUsage']).ledger_reconciled_at).not.toBeNull();
    expect(err(call('C', 'GET', '/admin/usage', undefined, 'period_start=2025-01-01'))).toBe('NOT_FOUND');
    for (const bad of ['yesterday', '2026-09-30T16:00:00Z', '2026-10-02', '2026-13-01']) expect(err(call('C', 'GET', '/admin/usage', undefined, `period_start=${bad}`))).toBe('VALIDATION_FAILED');
  });
  it('health readings follow the development panel', () => {
    const { backend, call } = harness();
    expect((call('C', 'GET', '/admin/health').body as S['AdminHealth']).database).toBe('healthy');
    backend.setAdminHealth('stalled');
    expect(call('C', 'GET', '/admin/health').body).toMatchObject({ objects: 'degraded', unknown_task_count: 3, failed_delete_count: 2 });
  });
});

describe('PR-11b review round 0 regressions (mock)', () => {
  it('a replayed key with the same body but another target is a mismatch, not the first target\'s success', () => {
    const { backend, call, write } = harness();
    const first = write('adminRevokeInvitation', ISSUED, 'POST', `/admin/invitations/${ISSUED}/revoke`, { reason: '合成' });
    expect(first.reply.status).toBe(200);
    const proof = backend.issueAdminProof('C', { operationId: 'adminRevokeInvitation', targetId: EXPIRED, idempotencyKey: first.key, body: { reason: '合成' } }, 'p', '123456') as { token: string };
    expect(err(call('C', 'POST', `/admin/invitations/${EXPIRED}/revoke`, { reason: '合成' }, '', first.key, proof.token))).toBe('IDEMPOTENCY_MISMATCH');
    expect((items<S['Invitation']>(call('C', 'GET', '/admin/invitations')).find(item => item.id === EXPIRED))!.state).toBe('expired');
  });

  it('a proof dies with the Mock session that obtained it', () => {
    const { backend, call, newKey } = harness();
    const key = newKey();
    const body = { base_revision: 1, status: 'disabled', reason: '合成：会话检查' };
    const proof = backend.issueAdminProof('C', { operationId: 'adminUpdateUser', targetId: B_ID, idempotencyKey: key, body }, 'p', '123456') as { token: string };
    backend.retireSession('C');
    expect(err(call('C', 'PATCH', `/admin/users/${B_ID}`, body, '', key, proof.token))).toBe('REAUTH_REQUIRED');
    expect(backend.accountUser('B').status).toBe('active');
  });

  it('only an active owner has a quota projection or adjustment', () => {
    const { call, write } = harness();
    write('adminUpdateUser', B_ID, 'PATCH', `/admin/users/${B_ID}`, { base_revision: 1, status: 'disabled', reason: '合成' });
    expect(err(call('C', 'GET', `/admin/users/${B_ID}/quota`))).toBe('NOT_FOUND');
    expect(err(call('C', 'GET', `/admin/users/${PENDING}/quota`))).toBe('NOT_FOUND');
    expect(err(write('adminAdjustQuota', B_ID, 'POST', '/admin/quota-adjustments', { user_id: B_ID, bucket_kind: 'summary_month_count', period_start: '2026-09-30T16:00:00Z', additional_units: 1, reason: '合成' }).reply)).toBe('NOT_FOUND');
  });

  it('reseeding a student dataset keeps the account status, platform switches and quota adjustments', () => {
    const { backend, call, write } = harness();
    const before = (call('A', 'GET', '/me/quota').body as S['QuotaView']).buckets.find(item => item.kind === 'summary_month_count')!;
    write('adminAdjustQuota', A_ID, 'POST', '/admin/quota-adjustments', { user_id: A_ID, bucket_kind: 'summary_month_count', period_start: before.period_start, additional_units: 7, reason: '合成' });
    write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: 1, ai_enabled: false, reason: '合成' });
    backend.reset('A', 'empty');
    const after = call('A', 'GET', '/me/quota').body as S['QuotaView'];
    expect(after.ai_enabled).toBe(false);
    expect(after.buckets.find(item => item.kind === 'summary_month_count')!.limit).toBe(before.limit + 7);
    expect(items(call('A', 'GET', '/courses'))).toHaveLength(0);
  });

  it('the switches are enforced at new task admission, not only at preparation', () => {
    const { call, write } = harness();
    const ENDED = 'c0000000-0000-4000-8000-000000000001';
    const request = (over: Record<string, unknown>) => ({ scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: false, ...over });
    const execute = (plan: S['GenerationPlan']) => ({ plan_sha256: plan.plan_sha256, confirmed_destination_revision: plan.destination.configuration_revision, confirm_send: true, confirm_images: false, confirm_original_snapshot: false, confirm_partial_scope: false });
    const question = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, request({ kind: 'ai_question', scope: 'recent120', question: '为什么？' })).body as S['GenerationPlan'];
    const retranscribe = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, request({ kind: 'asr_retranscribe' })).body as S['GenerationPlan'];
    write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: 1, ai_enabled: false, asr_enabled: false, reason: '合成：维护' });
    expect(err(call('A', 'POST', `/generation-plans/${question.id}/execute`, execute(question)))).toBe('BUDGET_BLOCKED');
    expect(err(call('A', 'POST', `/generation-plans/${retranscribe.id}/execute`, execute(retranscribe)))).toBe('SERVICE_DISABLED');
    expect(items<S['Task']>(call('A', 'GET', '/tasks', undefined, `class_session_id=${ENDED}`)).filter(item => item.kind === 'ai_question' || item.kind === 'asr_retranscribe')).toHaveLength(0);
  });

  it('a reason is limited in code points, not UTF-16 units', () => {
    const { write } = harness();
    expect(write('adminUpdateUser', B_ID, 'PATCH', `/admin/users/${B_ID}`, { base_revision: 1, status: 'disabled', reason: '😀'.repeat(500) }).reply.status).toBe(200);
    expect(err(write('adminUpdateUser', B_ID, 'PATCH', `/admin/users/${B_ID}`, { base_revision: 2, status: 'active', reason: '😀'.repeat(501) }).reply)).toBe('VALIDATION_FAILED');
  });
});

describe('PR-11b review round 1 regressions (mock)', () => {
  it('an audit continuation is stable when newer records are added before it', () => {
    const { call, write } = harness();
    const first = call('C', 'GET', '/admin/audit', undefined, 'limit=1').body as S['AuditRecordPage'];
    write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: 1, registration_enabled: false, reason: '合成：新记录' });
    const second = call('C', 'GET', '/admin/audit', undefined, `limit=1&cursor=${encodeURIComponent(first.next_cursor!)}`).body as S['AuditRecordPage'];
    expect(second.items[0]!.id).not.toBe(first.items[0]!.id);
    expect(second.next_cursor).toBeNull();
    expect(err(call('C', 'GET', '/admin/audit', undefined, 'cursor=a:00000000-0000-4000-8000-00000000ffff'))).toBe('CURSOR_EXPIRED');
  });

  it('platform-only users start from the stage-10 limits', () => {
    const { call } = harness();
    const quota = call('C', 'GET', '/admin/users/d0000000-0000-4000-8000-000000000001/quota').body as S['QuotaView'];
    expect(Object.fromEntries(quota.buckets.map(item => [item.kind, item.limit]))).toEqual({ asr_month_ms: 43_200_000, asr_day_ms: 10_800_000, retranscribe_month_ms: 8_640_000, interactive_month_count: 20, summary_month_count: 8 });
  });

  it('the ASR switch refuses new live streams but keeps an admitted one', () => {
    const { backend, write } = harness();
    const ENDED = 'c0000000-0000-4000-8000-000000000001';
    const final = (streamId: string, n: number) => ({ classId: ENDED, streamId, segmentId: `5e000000-0000-4000-8000-00000000000${n}`, segmentRevisionId: `5f000000-0000-4000-8000-00000000000${n}`, versionId: '5d000000-0000-4000-8000-000000000001', text: '合成', startMs: 0, endMs: 1, revision: 1 });
    backend.persistFinal('A', final('5c000000-0000-4000-8000-000000000001', 1));
    write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: 1, asr_enabled: false, reason: '合成：维护' });
    expect(backend.asrAdmits('A', 'c0000000-0000-4000-8000-000000000001', '5c000000-0000-4000-8000-000000000001')).toBe(true);
    expect(backend.asrAdmits('A', 'c0000000-0000-4000-8000-000000000001', '5c000000-0000-4000-8000-000000000002')).toBe(false);
    backend.persistFinal('A', final('5c000000-0000-4000-8000-000000000002', 2));
    expect(backend.classroomStore('A').segments.some(item => item.stream_id === '5c000000-0000-4000-8000-000000000002')).toBe(false);
  });
});

describe('PR-11b extra review regressions (mock)', () => {
  it('a replay still needs the reauthentication header and is rechecked against current access', () => {
    const { call, write } = harness();
    const quota = (call('C', 'GET', `/admin/users/${A_ID}/quota`).body as S['QuotaView']).buckets.find(item => item.kind === 'summary_month_count')!;
    const body = { user_id: A_ID, bucket_kind: 'summary_month_count', period_start: quota.period_start, additional_units: 3, reason: '合成：重放复核' };
    const first = write('adminAdjustQuota', A_ID, 'POST', '/admin/quota-adjustments', body);
    expect(first.reply.status).toBe(201);
    expect(err(call('C', 'POST', '/admin/quota-adjustments', body, '', first.key, null))).toBe('REAUTH_REQUIRED');
    // Same key and proof: applied once, current projection returned.
    const replay = call('C', 'POST', '/admin/quota-adjustments', body, '', first.key, first.token);
    expect((replay.body as S['QuotaView']).buckets.find(item => item.kind === 'summary_month_count')!.limit).toBe(quota.limit + 3);
    write('adminUpdateUser', A_ID, 'PATCH', `/admin/users/${A_ID}`, { base_revision: 1, status: 'disabled', reason: '合成' });
    expect(err(call('C', 'POST', '/admin/quota-adjustments', body, '', first.key, first.token))).toBe('NOT_FOUND');
  });

  it('a missing or malformed write body is refused, never treated as {}', () => {
    const { backend, call, newKey } = harness();
    for (const raw of [undefined, null, [], 'x', 3]) {
      const key = newKey();
      const proof = backend.issueAdminProof('C', { operationId: 'adminRevokeInvitation', targetId: ISSUED, idempotencyKey: key, body: { reason: '合成' } }, 'p', '123456') as { token: string };
      expect(err(call('C', 'POST', `/admin/invitations/${ISSUED}/revoke`, raw, '', key, proof.token))).toBe('VALIDATION_FAILED');
    }
    expect((items<S['Invitation']>(call('C', 'GET', '/admin/invitations')).find(item => item.id === ISSUED))!.state).toBe('issued');
  });

  it('a request dispatched by an ended session is refused even after the account signs in again', () => {
    const { backend } = harness();
    const stale = backend.sessionGeneration('A');
    backend.retireSession('A');
    backend.beginSession('A');
    expect(err(backend.handle('A', 'GET', '/me', new URLSearchParams(), undefined, null, null, stale)!)).toBe('UNAUTHENTICATED');
    expect(backend.handle('A', 'GET', '/me', new URLSearchParams(), undefined, null, null, backend.sessionGeneration('A'))!.status).toBe(200);
  });

  it('a fresh sign-in after a restore gets a new current session; the revoked rows stay, also across a reseed', () => {
    const { backend, call, write } = harness();
    write('adminUpdateUser', A_ID, 'PATCH', `/admin/users/${A_ID}`, { base_revision: 1, status: 'disabled', reason: '合成' });
    write('adminUpdateUser', A_ID, 'PATCH', `/admin/users/${A_ID}`, { base_revision: 2, status: 'active', reason: '合成' });
    backend.beginSession('A');
    const sessions = () => items<S['DeviceSession']>(call('A', 'GET', '/me/sessions'));
    expect(sessions().filter(item => item.is_current)).toHaveLength(1);
    expect(sessions().find(item => item.is_current)!.revoked_at).toBeNull();
    expect(sessions().filter(item => item.revoked_at !== null)).toHaveLength(2);
    backend.reset('A', 'empty');
    expect(sessions().filter(item => item.revoked_at !== null)).toHaveLength(2);
  });

  it('a stream admitted by its first partial keeps going after ASR is turned off', () => {
    const { backend, write } = harness();
    const STREAM = '5c000000-0000-4000-8000-000000000003';
    expect(backend.asrAdmits('A', 'c0000000-0000-4000-8000-000000000001', STREAM)).toBe(true);
    write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: 1, asr_enabled: false, reason: '合成' });
    expect(backend.asrAdmits('A', 'c0000000-0000-4000-8000-000000000001', STREAM)).toBe(true);
    backend.persistFinal('A', { classId: 'c0000000-0000-4000-8000-000000000001', streamId: STREAM, segmentId: '5e000000-0000-4000-8000-000000000009', segmentRevisionId: '5f000000-0000-4000-8000-000000000009', versionId: '5d000000-0000-4000-8000-000000000002', text: '合成', startMs: 0, endMs: 1, revision: 1 });
    expect(backend.classroomStore('A').segments.some(item => item.stream_id === STREAM)).toBe(true);
  });

  it('every quota view reports the platform state of the same ledger the usage page shows', () => {
    const { call } = harness();
    expect((call('C', 'GET', '/admin/usage').body as S['AdminUsage']).settled_microyuan).toBe('180000000');
    expect((call('A', 'GET', '/me/quota').body as S['QuotaView']).platform_billing_state).toBe('warning60');
    expect((call('C', 'GET', '/admin/users/d0000000-0000-4000-8000-000000000001/quota').body as S['QuotaView']).platform_billing_state).toBe('warning60');
  });
});

describe('PR-11b on HTTP 0.4.0 (mock)', () => {
  const ADMIN = C_ID;
  const reauth = (call: ReturnType<typeof harness>['call'], operation: Record<string, unknown>, code = '123456', key = '0a000000-0000-4000-8000-000000000001') =>
    call('C', 'POST', '/auth/reauth', { password: 'synthetic-password', mfa_code: code, operation }, '', key);
  const op = (purpose: string, target: Record<string, unknown>, key: string, body: unknown) => ({ purpose, target, candidate_parameters: { idempotency_key: key, body } });

  it('POST /auth/reauth issues a proof bound to purpose, target, key and body; the write consumes it once', () => {
    const { call } = harness();
    const key = '0b000000-0000-4000-8000-000000000001';
    const body = { base_revision: 1, status: 'disabled', reason: '合成：通过再认证接口' };
    const issued = reauth(call, op('adminUpdateUser', { kind: 'user', id: B_ID }, key, body));
    expect(issued.status).toBe(200);
    const token = (issued.body as S['ReauthResult']).reauth_token;
    expect(token).toMatch(/^[A-Za-z0-9_-]{43}$/);
    // Bound to the user in the path: another target is refused.
    expect(err(call('C', 'PATCH', `/admin/users/${A_ID}`, body, '', key, token))).toBe('REAUTH_REQUIRED');
    expect(call('C', 'PATCH', `/admin/users/${B_ID}`, body, '', key, token).status).toBe(200);
    const quotaKey = '0b000000-0000-4000-8000-000000000002';
    const quota = (call('C', 'GET', `/admin/users/${A_ID}/quota`).body as S['QuotaView']).buckets[0]!;
    const quotaBody = { user_id: A_ID, bucket_kind: quota.kind, period_start: quota.period_start, additional_units: 1, reason: '合成' };
    const quotaProof = reauth(call, op('adminAdjustQuota', { kind: 'account', id: ADMIN }, quotaKey, quotaBody), '123456', '0a000000-0000-4000-8000-000000000002');
    expect(call('C', 'POST', '/admin/quota-adjustments', quotaBody, '', quotaKey, (quotaProof.body as S['ReauthResult']).reauth_token).status).toBe(201);
  });

  it('an unusable operation is refused before the factors; a student or a non-admin purpose gets no admin proof', () => {
    const { call } = harness();
    const key = '0c000000-0000-4000-8000-000000000001';
    const switches = { base_revision: 1, ai_summary_enabled: false, reason: '合成' };
    // Wrong code is the factor failure; the shape checks below come first and return 422 even with a valid code.
    expect(err(reauth(call, op('adminUpdateSwitches', { kind: 'account', id: ADMIN }, key, switches), '000000'))).toBe('REAUTH_REQUIRED');
    expect(err(reauth(call, op('adminUpdateSwitches', { kind: 'account', id: ADMIN }, key, { ...switches, reason: '\u0020\u3000' })))).toBe('VALIDATION_FAILED');
    expect(err(reauth(call, op('adminUpdateSwitches', { kind: 'account', id: ADMIN }, key.toUpperCase(), switches)))).toBe('VALIDATION_FAILED');
    expect(err(reauth(call, op('adminUpdateSwitches', { kind: 'account', id: ADMIN.toUpperCase() }, key, switches)))).toBe('VALIDATION_FAILED');
    expect(err(reauth(call, op('adminUpdateSwitches', { kind: 'account', id: A_ID }, key, switches)))).toBe('FORBIDDEN');
    expect(err(reauth(call, op('adminRevokeInvitation', { kind: 'account', id: ADMIN }, key, { reason: 'r' })))).toBe('VALIDATION_FAILED');
    expect(err(reauth(call, op('adminCreateInvitation', { kind: 'account', id: ADMIN }, key, { kind: 'single_use', email: 'x@example.invalid', reason: 'r' })))).toBe('VALIDATION_FAILED');
    expect(err(reauth(call, op('logoutAll', { kind: 'account', id: ADMIN }, key, {})))).toBe('VALIDATION_FAILED');
    expect(err(call('A', 'POST', '/auth/reauth', { password: 'p', mfa_code: '123456', operation: op('adminUpdateSwitches', { kind: 'account', id: A_ID }, key, switches) }, '', '0a000000-0000-4000-8000-0000000000aa'))).toBe('FORBIDDEN');
  });

  it('switches are fine-grained: groups summarise with OR, a group field sets both members, mixing a group and its member is refused', () => {
    const { call, write } = harness();
    const saved = write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: 1, asr_streaming_enabled: false, reason: '合成：实时转写维护' }).reply.body as S['AdminSwitches'];
    expect(saved).toMatchObject({ asr_streaming_enabled: false, asr_filetrans_enabled: true, asr_enabled: true });
    // The student's quota view: ASR is still available as a group (filetrans), billing is on.
    expect((call('A', 'GET', '/me/quota').body as S['QuotaView']).asr_enabled).toBe(true);
    expect(err(call('C', 'PATCH', '/admin/switches', { base_revision: 2, asr_enabled: false, asr_filetrans_enabled: false, reason: 'x' }))).toBe('VALIDATION_FAILED');
    const group = write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: 2, ai_enabled: false, reason: '合成' }).reply.body as S['AdminSwitches'];
    expect(group).toMatchObject({ ai_interaction_enabled: false, ai_summary_enabled: false, ai_enabled: false });
    // The new-capture switch is stored but not enforced by capture admission yet: changing it is refused.
    expect(err(write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: 3, new_capture_enabled: false, reason: '合成' }).reply)).toBe('SERVICE_DISABLED');
    expect(write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: 3, new_capture_enabled: true, registration_enabled: false, reason: '合成' }).reply.status).toBe(200);
    // The billing master closes every group in the quota view.
    write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: 4, billing_enabled: false, reason: '合成：总闸' });
    expect(call('A', 'GET', '/me/quota').body).toMatchObject({ asr_enabled: false, ai_enabled: false });
  });

  it('each service is admitted by its own switch, not by the group summary', () => {
    const { backend, call, write } = harness();
    const ENDED = 'c0000000-0000-4000-8000-000000000001';
    const request = (over: Record<string, unknown>) => ({ scope: 'all', seen_sources: [], include_images: false, accept_partial_scope: false, ...over });
    const execute = (plan: S['GenerationPlan']) => ({ plan_sha256: plan.plan_sha256, confirmed_destination_revision: plan.destination.configuration_revision, confirm_send: true, confirm_images: false, confirm_original_snapshot: false, confirm_partial_scope: false });
    const retranscribe = call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, request({ kind: 'asr_retranscribe' })).body as S['GenerationPlan'];
    write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: 1, asr_filetrans_enabled: false, ai_summary_enabled: false, reason: '合成' });
    // Retranscription is closed while live ASR stays open; summaries are closed while questions stay open.
    expect(err(call('A', 'POST', `/generation-plans/${retranscribe.id}/execute`, execute(retranscribe)))).toBe('SERVICE_DISABLED');
    expect(backend.asrAdmits('A', 'c0000000-0000-4000-8000-000000000001', '5c000000-0000-4000-8000-0000000000a1')).toBe(true);
    expect(err(call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, request({ kind: 'review_summary' })))).toBe('BUDGET_BLOCKED');
    expect(call('A', 'POST', `/class-sessions/${ENDED}/generation-plans`, request({ kind: 'ai_question', scope: 'recent120', question: '为什么？' })).status).toBe(201);
  });

  it('an adjustment whose resulting limit would leave the safe integer range is refused', () => {
    const { call, write } = harness();
    const bucket = (call('C', 'GET', `/admin/users/${A_ID}/quota`).body as S['QuotaView']).buckets[0]!;
    expect(err(write('adminAdjustQuota', A_ID, 'POST', '/admin/quota-adjustments', { user_id: A_ID, bucket_kind: bucket.kind, period_start: bucket.period_start, additional_units: Number.MAX_SAFE_INTEGER, reason: '合成' }).reply)).toBe('VALIDATION_FAILED');
  });
});

describe('PR-11b #38 review round 0 regressions (mock)', () => {
  it('a replay is authorised like the write: an old-session proof is refused, a fresh proof gets the original receipt', () => {
    const { backend, call, write } = harness();
    const body = { base_revision: 1, ai_summary_enabled: false, reason: '合成：回执重放' };
    const first = write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', body);
    write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: 2, registration_enabled: false, reason: '合成：后续变更' });
    backend.retireSession('C');
    backend.beginSession('C');
    expect(err(call('C', 'PATCH', '/admin/switches', body, '', first.key, first.token))).toBe('REAUTH_REQUIRED');
    const fresh = backend.issueAdminProof('C', { operationId: 'adminUpdateSwitches', targetId: null, idempotencyKey: first.key, body }, 'p', '123456') as { token: string };
    const replay = call('C', 'PATCH', '/admin/switches', body, '', first.key, fresh.token);
    expect(replay.body).toEqual(first.reply.body);
    expect((call('C', 'GET', '/admin/switches').body as S['AdminSwitches']).revision).toBe(3);
  });

  it('a sign-in attempt of a disabled account creates no session; the first real sign-in after restoring does', () => {
    const { backend, call, write } = harness();
    write('adminUpdateUser', A_ID, 'PATCH', `/admin/users/${A_ID}`, { base_revision: 1, status: 'disabled', reason: '合成' });
    backend.beginSession('A');
    write('adminUpdateUser', A_ID, 'PATCH', `/admin/users/${A_ID}`, { base_revision: 2, status: 'active', reason: '合成' });
    expect(err(call('A', 'GET', '/me'))).toBe('UNAUTHENTICATED');
    backend.beginSession('A');
    expect(items<S['DeviceSession']>(call('A', 'GET', '/me/sessions')).filter(item => item.revoked_at === null)).toHaveLength(1);
  });

  it('the billing master pauses the platform state below 100 %', () => {
    const { call, write } = harness();
    write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: 1, billing_enabled: false, reason: '合成' });
    expect((call('A', 'GET', '/me/quota').body as S['QuotaView']).platform_billing_state).toBe('paused');
  });

  it('a quota period must be a date-time, at proof issuance and at the write', () => {
    const { call, write } = harness();
    const body = { user_id: A_ID, bucket_kind: 'summary_month_count', period_start: '2026-10-01', additional_units: 1, reason: '合成' };
    expect(err(call('C', 'POST', '/auth/reauth', { password: 'p', mfa_code: '123456', operation: { purpose: 'adminAdjustQuota', target: { kind: 'account', id: C_ID }, candidate_parameters: { idempotency_key: '0e000000-0000-4000-8000-000000000001', body } } }, '', '0e000000-0000-4000-8000-000000000002'))).toBe('VALIDATION_FAILED');
    expect(err(call('C', 'POST', '/admin/quota-adjustments', body))).toBe('VALIDATION_FAILED');
    // The same adjustment with the bucket's own date-time period is accepted.
    const bucket = (call('C', 'GET', `/admin/users/${A_ID}/quota`).body as S['QuotaView']).buckets.find(item => item.kind === 'summary_month_count')!;
    expect(write('adminAdjustQuota', A_ID, 'POST', '/admin/quota-adjustments', { ...body, period_start: bucket.period_start }).reply.status).toBe(201);
  });

  it('a live stream admission belongs to its classroom', () => {
    const { backend, write } = harness();
    const STREAM = '5c000000-0000-4000-8000-0000000000b1';
    expect(backend.asrAdmits('A', 'c0000000-0000-4000-8000-000000000001', STREAM)).toBe(true);
    write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: 1, asr_streaming_enabled: false, reason: '合成' });
    expect(backend.asrAdmits('A', 'c0000000-0000-4000-8000-000000000001', STREAM)).toBe(true);
    expect(backend.asrAdmits('A', 'c0000000-0000-4000-8000-000000000004', STREAM)).toBe(false);
  });
});
