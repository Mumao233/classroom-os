import { useState } from 'react';
import type { components } from '../../generated/openapi';
import { additionalUnits, bucketLabel, formatUnits, isDurationBucket, reasonError, roleLabel, statusLabel } from '../../domain/admin';
import { formatDate, formatDateTime } from '../../domain/dashboard';
import { useAdminUserQuota, useAdminUsers, useAdminWrite } from '../../features/admin';
import { ReadOnlyWhile, readableCache, useMe } from '../../features/data';
import { Badge, Empty } from '../common';
import { AdminActionDialog, AdminData, AdminPage, ReasonField } from './AdminShell';
import styles from '../pages.module.css';

type S = components['schemas'];
const statusTone: Record<S['User']['status'], string> = { active: 'success', pending: 'info', disabled: 'warning', deleting: 'danger' };

/** AUTH-07 users page: account metadata, status changes and per-user quota — no study content. */
export function AdminUsers() {
  return <AdminPage title="用户" detail="查看账号状态、停用或恢复账号，并按需追加个人额度。"><UsersBody /></AdminPage>;
}

function UsersBody() {
  const users = useAdminUsers();
  const me = readableCache(useMe());
  const [statusTarget, setStatusTarget] = useState<S['User'] | null>(null);
  const [quotaUser, setQuotaUser] = useState<S['User'] | null>(null);
  // A list kept after a failed refresh is read-only: no write is based on a projection marked stale.
  return <ReadOnlyWhile stale={users.isError}>
    <section className={styles.panel} aria-labelledby="admin-users-title"><h2 id="admin-users-title">账号</h2>
      <AdminData query={users} label="正在读取用户列表…">{page => page.items.length === 0 ? <Empty title="还没有用户" description="通过邀请注册的账号会出现在这里。" /> :
        <div className={styles.tableWrap}><table className={styles.table} data-testid="admin-users">
          <thead><tr><th>账号</th><th>角色</th><th>状态</th><th>双重验证</th><th>注册时间</th><th>操作</th></tr></thead>
          <tbody>{page.items.map(user => <tr key={user.id} data-testid={`admin-user-${user.id}`}>
            <td><strong>{user.email}</strong>{user.nickname && <div className={styles.muted}>{user.nickname}</div>}</td>
            <td>{roleLabel[user.role]}</td>
            <td><Badge tone={statusTone[user.status]}>{statusLabel[user.status]}</Badge></td>
            <td>{user.mfa_enabled ? '已启用' : '未启用'}</td>
            <td>{formatDate(user.created_at)}</td>
            <td><div className={styles.row}>
              {user.status === 'active' && <button type="button" className={styles.buttonGhost} onClick={() => setQuotaUser(user)} aria-label={`查看 ${user.email} 的额度`}>额度</button>}
              {user.id !== me?.id && (user.status === 'active' || user.status === 'disabled') &&
                <button type="button" className={user.status === 'active' ? styles.buttonDanger : styles.buttonSecondary} onClick={() => setStatusTarget(user)}>{user.status === 'active' ? '停用' : '恢复'}</button>}
            </div></td>
          </tr>)}</tbody>
        </table></div>}
      </AdminData>
    </section>
    {quotaUser && users.data?.items.find(user => user.id === quotaUser.id)?.status === 'active' && <QuotaPanel key={quotaUser.id} user={quotaUser} onClose={() => setQuotaUser(null)} />}
    {statusTarget && <StatusDialog key={`${statusTarget.id}:${statusTarget.revision}`} user={statusTarget} onClose={() => setStatusTarget(null)} />}
  </ReadOnlyWhile>;
}

function StatusDialog({ user, onClose }: { user: S['User']; onClose: () => void }) {
  const next = user.status === 'active' ? 'disabled' : 'active';
  const [reason, setReason] = useState('');
  const write = useAdminWrite<S['AdminUserPatch'], S['User']>({
    operationId: 'adminUpdateUser', targetId: () => user.id,
    send: (api, body, key, token) => api.adminUpdateUser(user.id, body, key, token),
    apply: (client, accountKey, updated) => {
      client.setQueryData<S['UserPage']>(['account', accountKey, 'admin', 'users'], page => page && ({ ...page, items: page.items.map(item => item.id === updated.id && item.revision <= updated.revision ? updated : item) }));
      // A disabled account has no quota projection any more.
      if (updated.status !== 'active') client.removeQueries({ queryKey: ['account', accountKey, 'admin', 'user-quota', updated.id] });
    },
    onSuccess: onClose,
  });
  const error = reasonError(reason);
  return <AdminActionDialog open onClose={onClose} write={write} danger={next === 'disabled'}
    title={next === 'disabled' ? `停用账号 ${user.email}` : `恢复账号 ${user.email}`}
    description={next === 'disabled' ? '停用后该账号的全部会话立即失效，不能再登录；数据保留，可以再恢复。' : '恢复后该账号可以重新登录，原有数据不变。'}
    prepare={<ReasonField id="user-status-reason" value={reason} onChange={setReason} error={error} />}
    prepareValid={error === null} build={() => ({ base_revision: user.revision, status: next, reason })} />;
}

function QuotaPanel({ user, onClose }: { user: S['User']; onClose: () => void }) {
  const quota = useAdminUserQuota(user.id);
  const [bucket, setBucket] = useState<S['QuotaBucket'] | null>(null);
  return <ReadOnlyWhile stale={quota.isError}><section className={styles.panel} aria-labelledby="admin-quota-title" data-testid="admin-quota">
    <div className={styles.sectionHead}><h2 id="admin-quota-title">{user.email} 的额度</h2><button type="button" className={styles.buttonGhost} onClick={onClose}>收起</button></div>
    <AdminData query={quota} label="正在读取额度…">{view => <>
      <p className={`${styles.small} ${styles.muted}`}>实时转写{view.asr_enabled ? '已开启' : '已关闭'}，AI {view.ai_enabled ? '已开启' : '已关闭'}。额度只记录用量数字，不包含任何学习内容。</p>
      <div className={styles.tableWrap}><table className={styles.table}>
        <thead><tr><th>额度</th><th>周期</th><th>上限</th><th>已结算</th><th>处理中</th><th>结果未知</th><th>剩余</th><th></th></tr></thead>
        <tbody>{view.buckets.map(item => <tr key={`${item.kind}:${item.period_start}`}>
          <td>{bucketLabel[item.kind]}</td><td>{formatDateTime(item.period_start)} 起</td>
          <td>{formatUnits(item.kind, item.limit)}</td><td>{formatUnits(item.kind, item.settled)}</td>
          <td>{formatUnits(item.kind, item.reserved + item.submitted)}</td><td>{formatUnits(item.kind, item.unknown)}</td>
          <td><strong>{formatUnits(item.kind, item.remaining)}</strong></td>
          <td><button type="button" className={styles.buttonGhost} onClick={() => setBucket(item)} aria-label={`追加${bucketLabel[item.kind]}`}>追加</button></td>
        </tr>)}</tbody>
      </table></div>
    </>}</AdminData>
    {bucket && <AdjustDialog key={`${bucket.kind}:${bucket.period_start}`} user={user} bucket={bucket} onClose={() => setBucket(null)} />}
  </section></ReadOnlyWhile>;
}

function AdjustDialog({ user, bucket, onClose }: { user: S['User']; bucket: S['QuotaBucket']; onClose: () => void }) {
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const write = useAdminWrite<S['QuotaAdjustment'], S['QuotaView']>({
    operationId: 'adminAdjustQuota', targetId: body => body.user_id,
    send: (api, body, key, token) => api.adminAdjustQuota(body, key, token),
    apply: (client, accountKey, quota, body) => client.setQueryData(['account', accountKey, 'admin', 'user-quota', body.user_id], quota),
    onSuccess: onClose,
  });
  const units = additionalUnits(bucket.kind, amount);
  const error = reasonError(reason);
  return <AdminActionDialog open onClose={onClose} write={write}
    title={`追加${bucketLabel[bucket.kind]}`} description={`为 ${user.email} 在 ${formatDateTime(bucket.period_start)} 起的周期追加额度。追加只增加上限，不会退还或改写已有用量。`}
    prepare={<>
      <div className={styles.field}><label htmlFor="quota-amount">追加数量（{isDurationBucket(bucket.kind) ? '分钟' : '次'}）</label>
        <input id="quota-amount" inputMode="numeric" value={amount} onChange={event => setAmount(event.target.value)} aria-invalid={amount !== '' && units === null ? true : undefined} />
        <span className={amount !== '' && units === null ? styles.error : styles.hint}>{amount !== '' && units === null ? '请输入正整数。' : `当前上限 ${formatUnits(bucket.kind, bucket.limit)}，剩余 ${formatUnits(bucket.kind, bucket.remaining)}。`}</span></div>
      <ReasonField id="quota-reason" value={reason} onChange={setReason} error={error} />
    </>}
    prepareValid={units !== null && error === null}
    build={() => ({ user_id: user.id, bucket_kind: bucket.kind, period_start: bucket.period_start, additional_units: units ?? 0, reason })} />;
}
