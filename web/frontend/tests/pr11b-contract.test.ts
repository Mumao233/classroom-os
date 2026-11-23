import { describe, expect, it } from 'vitest';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createMockBackend } from '../src/mock/backend';
import type { AdminOperationId } from '../src/mock/admin-backend';

const source = resolve(process.cwd(), '../../docs/web-alpha/architecture/contracts');
const openapi = JSON.parse(readFileSync(resolve(source, 'openapi.json'), 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema({ $id: 'https://classroom.invalid/openapi', components: openapi.components });
const check = (name: string, value: unknown) => { const validate = ajv.compile({ $ref: `https://classroom.invalid/openapi#/components/schemas/${name}` }); expect(validate(value), `${name}: ${JSON.stringify(validate.errors)}`).toBe(true); };
const A_ID = 'a0000000-0000-4000-8000-000000000001';
const B_ID = 'b0000000-0000-4000-8000-000000000001';

describe('PR-11b admin mock responses conform to contract 0.3.0', () => {
  it('every admin route is in the frozen contract, requires admin + MFA, and writes require reauth', () => {
    expect(openapi.info.version).toBe('0.3.0');
    const admin = Object.entries(openapi.paths as Record<string, Record<string, Record<string, unknown>>>).filter(([path]) => path.startsWith('/admin'));
    expect(admin.map(([path]) => path).sort()).toEqual(['/admin/audit', '/admin/health', '/admin/invitations', '/admin/invitations/{invitation_id}/reissue', '/admin/invitations/{invitation_id}/revoke', '/admin/quota-adjustments', '/admin/switches', '/admin/usage', '/admin/users', '/admin/users/{user_id}', '/admin/users/{user_id}/quota']);
    for (const [, operations] of admin) for (const [method, operation] of Object.entries(operations)) {
      expect(operation['x-required-role']).toBe('admin');
      expect(operation['x-requires-mfa']).toBe(true);
      if (method !== 'get') expect(operation['x-requires-reauth']).toBe(true);
    }
    // Contract gap §1: no admin purpose exists in ReauthOperation yet (this test fails once it is added).
    const purposes = (openapi.components.schemas.ReauthOperation.oneOf as Array<{ properties: { purpose: { const: string } } }>).map(item => item.properties.purpose.const);
    expect(purposes.some(purpose => purpose.startsWith('admin'))).toBe(false);
  });

  it('reads, writes and errors of every admin route', () => {
    const backend = createMockBackend({ clockMs: () => 0 });
    let n = 0;
    const newKey = () => `00000000-0000-4000-8000-${String(++n).padStart(12, '0')}`;
    const get = (path: string, query = '') => backend.handle('C', 'GET', path, new URLSearchParams(query), undefined, null)!;
    const write = (operationId: AdminOperationId, targetId: string | null, method: string, path: string, body: unknown) => {
      const key = newKey();
      const proof = backend.issueAdminProof('C', { operationId, targetId, idempotencyKey: key, body }, 'p', '123456') as { token: string };
      return backend.handle('C', method, path, new URLSearchParams(), body, key, proof.token)!;
    };
    const ok = (name: string, reply: { status: number; body: unknown }) => { expect(reply.status, JSON.stringify(reply.body)).toBeLessThan(300); check(name, reply.body); return reply.body as Record<string, unknown>; };
    const fail = (reply: { status: number; body: unknown }) => { expect(reply.status).toBeGreaterThanOrEqual(400); check('Error', reply.body); };

    ok('UserPage', get('/admin/users'));
    ok('UserPage', get('/admin/users', 'limit=2'));
    ok('QuotaView', get(`/admin/users/${A_ID}/quota`));
    ok('QuotaView', get('/admin/users/d0000000-0000-4000-8000-000000000001/quota'));
    ok('InvitationPage', get('/admin/invitations'));
    ok('AdminUsage', get('/admin/usage'));
    ok('AdminUsage', get('/admin/usage', 'period_start=2026-09-01'));
    for (const mode of ['healthy', 'degraded', 'stalled'] as const) { backend.setAdminHealth(mode); ok('AdminHealth', get('/admin/health')); }
    const switches = ok('AdminSwitches', get('/admin/switches'));
    ok('User', write('adminUpdateUser', B_ID, 'PATCH', `/admin/users/${B_ID}`, { base_revision: 1, status: 'disabled', reason: '合成' }));
    const created = ok('InvitationCreated', write('adminCreateInvitation', null, 'POST', '/admin/invitations', { kind: 'single_use' }));
    const invitationId = (created.invitation as { id: string }).id;
    ok('InvitationCreated', write('adminReissueInvitation', invitationId, 'POST', `/admin/invitations/${invitationId}/reissue`, {}));
    ok('Invitation', write('adminRevokeInvitation', invitationId, 'POST', `/admin/invitations/${invitationId}/revoke`, {}));
    const quota = get(`/admin/users/${A_ID}/quota`).body as { buckets: Array<{ kind: string; period_start: string }> };
    ok('QuotaView', write('adminAdjustQuota', A_ID, 'POST', '/admin/quota-adjustments', { user_id: A_ID, bucket_kind: 'summary_month_count', period_start: quota.buckets.find(item => item.kind === 'summary_month_count')!.period_start, additional_units: 10, reason: '合成' }));
    ok('AdminSwitches', write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: switches.revision, registration_enabled: false, max_active_users: '30', reason: '合成' }));
    ok('AuditRecordPage', get('/admin/audit'));
    ok('AuditRecordPage', get('/admin/audit', 'limit=1'));

    fail(backend.handle('A', 'GET', '/admin/users', new URLSearchParams(), undefined, null)!);
    fail(backend.handle('C', 'PATCH', `/admin/users/${A_ID}`, new URLSearchParams(), { base_revision: 1, status: 'disabled', reason: 'x' }, newKey(), null)!);
    fail(get('/admin/usage', 'period_start=2020-01-01T00:00:00Z'));
    fail(get('/admin/users', 'cursor=bad'));
    fail(write('adminUpdateSwitches', null, 'PATCH', '/admin/switches', { base_revision: 1, ai_enabled: false, reason: '旧版本' }));
    backend.setAdminMfa(false);
    fail(get('/admin/health'));
  });
});
