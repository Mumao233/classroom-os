import type { components } from '../generated/openapi';

export type Notification = components['schemas']['Notification'];
export type NotificationPage = components['schemas']['NotificationPage'];

function fractionalSeconds(value: string): string {
  return (/\.(\d+)(?=Z$|[+-]\d{2}:\d{2}$)/i.exec(value)?.[1] ?? '').replace(/0+$/, '');
}

export function orderNotifications(items: readonly Notification[]): Notification[] {
  const unique = new Map<string, Notification>();
  for (const item of items) {
    const prior = unique.get(item.id);
    unique.set(item.id, prior ? { ...item, read_at: item.read_at ?? prior.read_at,
      resolved_at: item.resolved_at ?? prior.resolved_at } : item);
  }
  return [...unique.values()].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)
    || fractionalSeconds(b.created_at).localeCompare(fractionalSeconds(a.created_at)) || b.id.localeCompare(a.id));
}

export function needsAttention(item: Notification): boolean {
  const persistent = item.kind === 'conflict' || item.kind === 'capture_failure' || item.kind === 'save_failure'
    || (item.kind === 'security' && item.severity !== 'info');
  return item.resolved_at === null && (persistent || item.read_at === null);
}

export const notificationSeverity = {
  info: { label: '消息', tone: 'info' },
  warning: { label: '提醒', tone: 'warning' },
  critical: { label: '重要', tone: 'danger' },
} as const;

export const notificationMessages: Record<string, string> = {
  TASK_COMPLETED: '任务已完成',
  SYNC_CONFLICT: '有一项同步冲突待处理',
  AUDIO_EXPIRED: '课堂音频已到保留期限',
  CAPTURE_LEASE_LOST: '采集已中断',
  SAVE_FAILED: '保存失败',
  PERMISSION_REVOKED: '访问权限已失效',
};
