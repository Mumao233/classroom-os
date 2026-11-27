import { describe, expect, it } from 'vitest';
import { additionalUnits, formatBasisPoints, formatUnits, formatYuan, healthWarnings, parseMicro, reasonError, recentMonths, usageView } from '../src/domain/admin';
import { beijingMonthStart } from '../src/mock/admin-backend';
import type { components } from '../src/generated/openapi';

type S = components['schemas'];
const usage = (over: Partial<S['AdminUsage']> = {}): S['AdminUsage'] => ({
  period_start: '2026-09-30T16:00:00Z', period_end: '2026-10-31T16:00:00Z', budget_microyuan: '1000000000', fixed_and_accrued_microyuan: '300000000',
  settled_microyuan: '200000000', reserved_microyuan: '50000000', unknown_microyuan: '0', safety_reserve_microyuan: '100000000',
  price_version: 'p', ledger_reconciled_at: null, ...over,
});

describe('PR-11b admin domain', () => {
  it('a reason is required, not only whitespace, at most 500 code points', () => {
    expect(reasonError('')).not.toBeNull();
    expect(reasonError('   \n')).not.toBeNull();
    expect(reasonError('学生申请')).toBeNull();
    expect(reasonError('字'.repeat(500))).toBeNull();
    expect(reasonError('字'.repeat(501))).not.toBeNull();
    // Astral characters count once (JSON Schema maxLength counts code points).
    expect(reasonError('😀'.repeat(500))).toBeNull();
    // AdminWriteReason (0.4.0): U+FEFF / U+0085 alone are white space; U+0000 is never allowed.
    expect(reasonError('\uFEFF')).not.toBeNull();
    expect(reasonError('\u0085\u3000')).not.toBeNull();
    expect(reasonError('理由\u0000')).not.toBeNull();
  });

  it('money stays in integer microyuan and is rounded half up only for display', () => {
    expect(parseMicro('0')).toBe(0n);
    expect(parseMicro('012')).toBeNull();
    expect(parseMicro('1.5')).toBeNull();
    expect(parseMicro(12)).toBeNull();
    expect(parseMicro('-1')).toBeNull();
    expect(formatYuan(0n)).toBe('¥0.00');
    expect(formatYuan(1_234_565_000n)).toBe('¥1,234.57');
    expect(formatYuan(4_999n)).toBe('¥0.00');
    expect(formatYuan(5_000n)).toBe('¥0.01');
    // Far beyond Number precision.
    expect(formatYuan(90_071_992_547_409_930_000_000n)).toBe('¥90,071,992,547,409,930.00');
    expect(formatYuan(-1_000_000n)).toBe('-¥1.00');
  });

  it('exposure is fixed + settled + reserved + unknown; levels at 60/80/100 %', () => {
    const view = usageView(usage())!;
    expect(view.exposure).toBe(550_000_000n);
    expect(view.basisPoints).toBe(5_500);
    expect(view.level).toBe('normal');
    expect(view.admissible).toBe(350_000_000n);
    expect(usageView(usage({ unknown_microyuan: '50000000' }))!.level).toBe('warning60');
    expect(usageView(usage({ unknown_microyuan: '250000000' }))!.level).toBe('warning80');
    expect(usageView(usage({ unknown_microyuan: '450000000' }))!.level).toBe('blocked100');
    expect(usageView(usage({ unknown_microyuan: '449999999' }))!.level).toBe('warning80');
    expect(usageView(usage({ budget_microyuan: '0' }))!.level).toBe('blocked100');
    // billing_state(0, 0): a zero budget is blocked even with no exposure at all.
    expect(usageView(usage({ budget_microyuan: '0', fixed_and_accrued_microyuan: '0', settled_microyuan: '0', reserved_microyuan: '0', unknown_microyuan: '0', safety_reserve_microyuan: '0' }))!.level).toBe('blocked100');
    expect(usageView(usage({ settled_microyuan: '1e9' }))).toBeNull();
    expect(formatBasisPoints(5_507)).toBe('55.07%');
  });

  it('months follow Beijing time, also across the UTC day boundary', () => {
    // 2026-10-31T17:00Z is already 1 November in Beijing.
    // The usage query takes the calendar date of the month's first day (`format: date`, YYYY-MM-01).
    expect(recentMonths(Date.parse('2026-10-31T17:00:00Z'), 2)).toEqual([
      { date: '2026-11-01', label: '2026 年 11 月' }, { date: '2026-10-01', label: '2026 年 10 月' },
    ]);
    expect(recentMonths(Date.parse('2026-10-31T15:59:00Z'), 1)).toEqual([{ date: '2026-10-01', label: '2026 年 10 月' }]);
    expect(recentMonths(Date.parse('2026-01-15T00:00:00Z'), 2)[1]).toEqual({ date: '2025-12-01', label: '2025 年 12 月' });
    expect(beijingMonthStart('2026-10-28T08:00:00Z')).toBe('2026-09-30T16:00:00Z');
    expect(beijingMonthStart('2026-10-28T08:00:00Z', -1)).toBe('2026-10-31T16:00:00Z');
  });

  it('quota units: whole minutes for time buckets, whole times for counted ones', () => {
    expect(additionalUnits('asr_month_ms', '30')).toBe(1_800_000);
    expect(additionalUnits('summary_month_count', '5')).toBe(5);
    for (const bad of ['', '0', '-3', '1.5', '1e3', '12345678', 'abc']) expect(additionalUnits('asr_month_ms', bad)).toBeNull();
    expect(formatUnits('asr_month_ms', 5_400_000)).toBe('1 小时 30 分钟');
    expect(formatUnits('asr_day_ms', 59_999)).toBe('0 分钟');
    expect(formatUnits('interactive_month_count', 3)).toBe('3 次');
  });

  it('health readings that need attention are named', () => {
    const fine: S['AdminHealth'] = { checked_at: '2026-10-28T08:00:00Z', database: 'healthy', objects: 'healthy', worker_heartbeat_at: '2026-10-28T07:59:50Z', oldest_queued_seconds: 3, unknown_task_count: 0, pending_delete_count: 2, failed_delete_count: 0, active_asr_streams: 1, last_backup_at: '2026-10-28T02:00:00Z' };
    expect(healthWarnings(fine)).toEqual([]);
    const bad = healthWarnings({ ...fine, database: 'unavailable', worker_heartbeat_at: null, oldest_queued_seconds: 900, unknown_task_count: 2, failed_delete_count: 1, last_backup_at: null });
    expect(bad).toEqual(['数据库不可用', '后台处理进程没有心跳记录', '最早的排队任务已等待 15 分钟', '2 个任务结果未知，需要核对', '1 项删除失败，需要处理', '超过 48 小时没有成功备份']);
    expect(healthWarnings({ ...fine, worker_heartbeat_at: '2026-10-28T07:50:00Z' })).toEqual(['后台处理进程已 10 分钟没有心跳']);
  });
});

describe('PR-11b reauthentication operation (HTTP 0.4.0)', () => {
  it('binds account-targeted writes to the administrator and path writes to their resource', async () => {
    const { reauthOperation } = await import('../src/features/admin');
    const admin = 'c9000000-0000-4000-8000-000000000001';
    const key = '0d000000-0000-4000-8000-000000000001';
    expect(reauthOperation({ operationId: 'adminAdjustQuota', targetId: 'a0000000-0000-4000-8000-000000000001', idempotencyKey: key, body: { reason: 'r' } }, admin))
      .toEqual({ purpose: 'adminAdjustQuota', target: { kind: 'account', id: admin }, candidate_parameters: { idempotency_key: key, body: { reason: 'r' } } });
    expect(reauthOperation({ operationId: 'adminUpdateUser', targetId: 'b0000000-0000-4000-8000-000000000001', idempotencyKey: key, body: {} }, admin).target).toEqual({ kind: 'user', id: 'b0000000-0000-4000-8000-000000000001' });
    expect(reauthOperation({ operationId: 'adminReissueInvitation', targetId: 'd0000000-0000-4000-8000-000000000104', idempotencyKey: key, body: {} }, admin).target).toEqual({ kind: 'invitation', id: 'd0000000-0000-4000-8000-000000000104' });
    expect(reauthOperation({ operationId: 'adminCreateInvitation', targetId: null, idempotencyKey: key, body: {} }, admin).target).toEqual({ kind: 'account', id: admin });
  });
});

describe('PR-11b mock: exact integers in the raw admin JSON', () => {
  it('accepts exact integer spellings and rejects fractional ones before parsing rounds them', async () => {
    const { rawIntegersOk } = await import('../src/mock/admin-backend');
    for (const ok of ['{"additional_units":1}', '{"additional_units":1.0}', '{"base_revision":1e0}', '{"base_revision":10e-1}', '{"reason":"1.5"}']) expect(rawIntegersOk(ok)).toBe(true);
    for (const bad of ['{"additional_units":1.0000000000000001}', '{"base_revision":1.5}', '{"base_revision":1e-1}', '{"operation":{"candidate_parameters":{"body":{"additional_units":2.25}}}}']) expect(rawIntegersOk(bad)).toBe(false);
  });
});

describe('PR-11b lost invitation-create reply', () => {
  it('names the invitation the server reports for SECRET_ALREADY_ISSUED, never a token', async () => {
    const { describeAdminError } = await import('../src/features/admin');
    const { ApiClientError } = await import('../src/api/client');
    const body = (conflict_id: unknown) => ({ error: { code: 'SECRET_ALREADY_ISSUED', message: 'm', request_id: '00000000-0000-4000-8000-000000000001', retryable: false, retry_after_seconds: null, current_revision: null, conflict_id, task_id: null } });
    const fallback = () => 'fallback';
    const named = describeAdminError(new ApiClientError('http', 409, body('D0000000-0000-4000-8000-0000000AB123') as never), fallback);
    expect(named).toContain('#0ab123');
    // Without (or with a malformed) reference the general guidance is kept.
    for (const missing of [null, 'not-a-uuid']) expect(describeAdminError(new ApiClientError('http', 409, body(missing) as never), fallback)).toContain('请刷新列表');
  });
});
