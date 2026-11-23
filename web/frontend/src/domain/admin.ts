import type { components } from '../generated/openapi';

type S = components['schemas'];

/** Reasons are stored in the audit trail: 1–500 Unicode code points, not only whitespace. */
export function reasonError(text: string): string | null {
  if (!text.trim()) return '请填写操作理由，它会记入审计记录。';
  if ([...text].length > 500) return '理由不能超过 500 个字。';
  return null;
}

const MICRO = /^(0|[1-9][0-9]*)$/;
/** Exact integer microyuan from the contract's decimal string; null for anything malformed (never a float). */
export function parseMicro(value: unknown): bigint | null { return typeof value === 'string' && MICRO.test(value) ? BigInt(value) : null; }

/** ¥ with two decimals, rounded half up from integer microyuan. */
export function formatYuan(micro: bigint): string {
  const negative = micro < 0n;
  const cents = ((negative ? -micro : micro) + 5_000n) / 10_000n;
  const yuan = (cents / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${negative ? '-' : ''}¥${yuan}.${(cents % 100n).toString().padStart(2, '0')}`;
}

export type UsageView = {
  budget: bigint; fixed: bigint; settled: bigint; reserved: bigint; unknown: bigint; safetyReserve: bigint;
  /** fixed + settled + reserved + unknown (11a: placeholder, known actual cost, active holds and unknown risk). */
  exposure: bigint;
  /** Exposure as basis points (1/100 %) of the budget, floored. */
  basisPoints: number;
  /** Budget left for new admissions after the safety reserve; negative when already over. */
  admissible: bigint;
  level: 'normal' | 'warning60' | 'warning80' | 'blocked100';
};

/** Summary of one period; null when any amount is malformed (the page then shows no partial numbers). */
export function usageView(usage: S['AdminUsage']): UsageView | null {
  const values = [usage.budget_microyuan, usage.fixed_and_accrued_microyuan, usage.settled_microyuan, usage.reserved_microyuan, usage.unknown_microyuan, usage.safety_reserve_microyuan].map(parseMicro);
  if (values.some(value => value === null)) return null;
  const [budget, fixed, settled, reserved, unknown, safetyReserve] = values as bigint[];
  const exposure = fixed! + settled! + reserved! + unknown!;
  const basisPoints = budget! > 0n ? Number((exposure * 10_000n) / budget!) : exposure > 0n ? 10_000 : 0;
  const level = basisPoints >= 10_000 ? 'blocked100' : basisPoints >= 8_000 ? 'warning80' : basisPoints >= 6_000 ? 'warning60' : 'normal';
  return { budget: budget!, fixed: fixed!, settled: settled!, reserved: reserved!, unknown: unknown!, safetyReserve: safetyReserve!, exposure, basisPoints, admissible: budget! - safetyReserve! - exposure, level };
}
export const formatBasisPoints = (value: number) => `${Math.floor(value / 100)}.${String(value % 100).padStart(2, '0')}%`;
export const levelLabel: Record<UsageView['level'], string> = { normal: '正常', warning60: '已过 60% 提醒线', warning80: '已过 80% 提醒线', blocked100: '已到 100%，新收费任务暂停' };

const BEIJING = 8 * 3600_000;
/**
 * The last `count` Beijing calendar months (newest first). `date` is the `period_start` query value of
 * `GET /admin/usage` (`format: date`, the first day of the month: YYYY-MM-01).
 */
export function recentMonths(nowMs: number, count = 6): Array<{ date: string; label: string }> {
  const local = new Date(nowMs + BEIJING);
  return Array.from({ length: count }, (_, back) => {
    const first = new Date(Date.UTC(local.getUTCFullYear(), local.getUTCMonth() - back, 1));
    return { date: first.toISOString().slice(0, 10), label: `${first.getUTCFullYear()} 年 ${first.getUTCMonth() + 1} 月` };
  });
}

export const statusLabel: Record<S['User']['status'], string> = { pending: '待完成注册', active: '正常', disabled: '已停用', deleting: '注销中' };
export const roleLabel: Record<S['User']['role'], string> = { student: '学生', admin: '管理员' };
export const invitationStateLabel: Record<S['Invitation']['state'], string> = { issued: '未使用', consumed: '已使用', revoked: '已撤销', expired: '已过期' };
export const invitationKindLabel: Record<S['Invitation']['kind'], string> = { email_bound: '邮箱邀请', single_use: '单次邀请' };
export const healthLabel: Record<S['AdminHealth']['database'], string> = { healthy: '正常', degraded: '降级', unavailable: '不可用' };
export const billingStateLabel: Record<S['QuotaView']['platform_billing_state'], string> = { normal: '正常', warning60: '60% 提醒', warning80: '80% 提醒', blocked100: '已达上限', paused: '已暂停' };

export const bucketLabel: Record<S['QuotaBucket']['kind'], string> = {
  asr_month_ms: '实时转写（本月）', asr_day_ms: '实时转写（今日）', retranscribe_month_ms: '重新转写（本月）',
  interactive_month_count: '问答与讲解（本月）', summary_month_count: '总结与卡片（本月）',
};
export const isDurationBucket = (kind: S['QuotaBucket']['kind']) => kind.endsWith('_ms');
/** Units in the bucket's own measure: minutes for time buckets, times for counted ones. */
export function formatUnits(kind: S['QuotaBucket']['kind'], value: number) {
  if (!isDurationBucket(kind)) return `${value} 次`;
  const minutes = Math.floor(value / 60_000);
  return minutes >= 60 ? `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟` : `${minutes} 分钟`;
}
/** Additional units typed by an administrator: whole minutes for time buckets, whole times otherwise. */
export function additionalUnits(kind: S['QuotaBucket']['kind'], typed: string): number | null {
  if (!/^[1-9][0-9]{0,6}$/.test(typed.trim())) return null;
  const amount = Number(typed.trim());
  return isDurationBucket(kind) ? amount * 60_000 : amount;
}

export const operationLabel: Record<string, string> = {
  adminUpdateUser: '修改账号状态', adminCreateInvitation: '创建邀请', adminRevokeInvitation: '撤销邀请', adminReissueInvitation: '重新签发邀请',
  adminAdjustQuota: '追加额度', adminUpdateSwitches: '修改平台开关',
};
export const targetTypeLabel: Record<string, string> = { user: '用户', invitation: '邀请', platform: '平台' };

/** Seconds between the worker's last heartbeat and the health check; null when it never reported. */
export function heartbeatAge(health: Pick<S['AdminHealth'], 'checked_at' | 'worker_heartbeat_at'>): number | null {
  if (!health.worker_heartbeat_at) return null;
  return Math.max(0, Math.round((Date.parse(health.checked_at) - Date.parse(health.worker_heartbeat_at)) / 1000));
}
/** Readings that need an operator's attention, in display order. */
export function healthWarnings(health: S['AdminHealth']): string[] {
  const warnings: string[] = [];
  if (health.database !== 'healthy') warnings.push(`数据库${healthLabel[health.database]}`);
  if (health.objects !== 'healthy') warnings.push(`对象存储${healthLabel[health.objects]}`);
  const age = heartbeatAge(health);
  if (age === null) warnings.push('后台处理进程没有心跳记录');
  else if (age > 60) warnings.push(`后台处理进程已 ${Math.round(age / 60)} 分钟没有心跳`);
  if (health.oldest_queued_seconds > 300) warnings.push(`最早的排队任务已等待 ${Math.round(health.oldest_queued_seconds / 60)} 分钟`);
  if (health.unknown_task_count > 0) warnings.push(`${health.unknown_task_count} 个任务结果未知，需要核对`);
  if (health.failed_delete_count > 0) warnings.push(`${health.failed_delete_count} 项删除失败，需要处理`);
  if (!health.last_backup_at || Date.parse(health.checked_at) - Date.parse(health.last_backup_at) > 48 * 3600_000) warnings.push('超过 48 小时没有成功备份');
  return warnings;
}

/** Canonical JSON (sorted keys) so a retried write is recognised as the same body. */
export const canonicalJson = (value: unknown): string => Array.isArray(value) ? `[${value.map(canonicalJson).join(',')}]`
  : typeof value === 'object' && value !== null ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonicalJson((value as Record<string, unknown>)[key])}`).join(',')}}` : JSON.stringify(value);
