import { useMemo, useState } from 'react';
import type { components } from '../../generated/openapi';
import { healthLabel, healthWarnings, heartbeatAge, invitationRef, operationLabel, reasonError, targetTypeLabel } from '../../domain/admin';
import { formatDateTime } from '../../domain/dashboard';
import { describeAdminError, useAdminAudit, useAdminHealth, useAdminSwitches, useAdminUsers, useAdminWrite } from '../../features/admin';
import { describeError, ReadOnlyWhile, readableCache, useAppData } from '../../features/data';
import { Badge, Empty, Loading, QueryError } from '../common';
import { AdminActionDialog, AdminData, AdminPage, ReasonField } from './AdminShell';
import styles from '../pages.module.css';

type S = components['schemas'];
const healthTone = { healthy: 'success', degraded: 'warning', unavailable: 'danger' } as const;

/** AUTH-07 runtime page: health readings, platform switches (CAS + reason + reauth) and the audit trail. */
export function AdminRuntime() {
  return <AdminPage title="运行状态" detail="查看服务健康、平台开关和管理操作记录。"><Health /><Switches /><Audit /></AdminPage>;
}

function Health() {
  const health = useAdminHealth();
  return <section className={styles.panel} aria-labelledby="admin-health-title">
    <div className={styles.sectionHead}><h2 id="admin-health-title">服务健康</h2><button type="button" className={styles.buttonGhost} onClick={() => void health.refetch()} disabled={health.isFetching}>{health.isFetching ? '正在检查…' : '重新检查'}</button></div>
    <AdminData query={health} label="正在读取运行状态…">{reading => {
      const warnings = healthWarnings(reading);
      const age = heartbeatAge(reading);
      return <div className={styles.stack} data-testid="admin-health">
        {warnings.length > 0 ? <div className={`${styles.banner} ${styles.bannerWarning}`} role="status" data-testid="admin-health-warnings"><div><strong>需要关注</strong><ul>{warnings.map(item => <li key={item}>{item}</li>)}</ul></div></div>
          : <p className={styles.success}>各项读数正常。</p>}
        <dl className={styles.dl}>
          <dt>数据库</dt><dd><Badge tone={healthTone[reading.database]}>{healthLabel[reading.database]}</Badge></dd>
          <dt>对象存储</dt><dd><Badge tone={healthTone[reading.objects]}>{healthLabel[reading.objects]}</Badge></dd>
          <dt>后台处理心跳</dt><dd>{age === null ? '没有记录' : `${age} 秒前`}</dd>
          <dt>最早排队任务</dt><dd>已等待 {reading.oldest_queued_seconds} 秒</dd>
          <dt>结果未知的任务</dt><dd>{reading.unknown_task_count} 个</dd>
          <dt>待删除 / 删除失败</dt><dd>{reading.pending_delete_count} 项 / {reading.failed_delete_count} 项</dd>
          <dt>进行中的实时转写</dt><dd>{reading.active_asr_streams} 路</dd>
          <dt>最近成功备份</dt><dd>{reading.last_backup_at ? formatDateTime(reading.last_backup_at) : '没有记录'}</dd>
          <dt>检查时间</dt><dd>{formatDateTime(reading.checked_at)}</dd>
        </dl>
      </div>;
    }}</AdminData>
  </section>;
}

type SwitchDraft = Pick<S['AdminSwitches'], 'registration_enabled' | 'asr_enabled' | 'ai_enabled' | 'max_active_users'>;
const switchKeys = ['registration_enabled', 'asr_enabled', 'ai_enabled', 'max_active_users'] as const;
/** Only the editable fields: the server revision is tracked apart from the draft and never edited. */
const draftOf = (value: S['AdminSwitches']): SwitchDraft => ({ registration_enabled: value.registration_enabled, asr_enabled: value.asr_enabled, ai_enabled: value.ai_enabled, max_active_users: value.max_active_users });
const switchFields = [
  { key: 'registration_enabled', label: '开放邀请注册', hint: '关闭后已发出的邀请也不能注册新账号，已有账号不受影响。' },
  { key: 'asr_enabled', label: '实时转写', hint: '关闭后不再开始新的转写；进行中的会话按服务端规则结束，已有字幕保留。' },
  { key: 'ai_enabled', label: 'AI 问答、讲解与总结', hint: '关闭后不再接收新的 AI 请求；已生成的内容仍可阅读。' },
] as const;

function Switches() {
  const switches = useAdminSwitches();
  const { online } = useAppData();
  const current = readableCache(switches);
  // Only explicitly edited fields, with the revision they were edited against. A refetch never turns an
  // untouched field into a requested change; a newer server revision makes the save a CAS conflict.
  const [draft, setDraft] = useState<{ base: S['AdminSwitches']; edits: Partial<SwitchDraft> } | null>(null);
  const [confirming, setConfirming] = useState<{ base: S['AdminSwitches']; edits: Partial<SwitchDraft> } | null>(null);
  const shown = current ? { ...current, ...draft?.edits } : null;
  const edit = (change: Partial<SwitchDraft>) => current && setDraft({ base: draft?.base ?? current, edits: { ...draft?.edits, ...change } });
  const changed = draft ? switchKeys.filter(key => key in draft.edits && draft.edits[key] !== draft.base[key]) : [];
  const rebased = draft !== null && current !== undefined && current.revision !== draft.base.revision;
  return <ReadOnlyWhile stale={switches.isError}><section className={styles.panel} aria-labelledby="admin-switches-title">
    <h2 id="admin-switches-title">平台开关</h2>
    {!shown ? (switches.isError ? <QueryError error={switches.error} retry={() => void switches.refetch()} /> : <Loading label="正在读取平台开关…" />) : <div className={styles.stack} data-testid="admin-switches">
      {switches.isError && <div className={`${styles.banner} ${styles.bannerWarning}`} role="status"><div>刷新失败：{describeError(switches.error)} 以下为上次读取的开关。</div></div>}
      {switchFields.map(field => <label key={field.key} className={styles.choice}>
        <input type="checkbox" checked={shown[field.key]} disabled={!online || !current} onChange={event => edit({ [field.key]: event.target.checked })} />
        <span><strong>{field.label}</strong><br /><span className={styles.hint}>{field.hint}</span></span>
      </label>)}
      <dl className={styles.dl}><dt>活跃用户阶段</dt><dd data-testid="active-user-stage">{shown.max_active_users} 人</dd></dl>
      <p className={`${styles.small} ${styles.muted}`}>阶段切换（10 人 / 30 人）要等阶段切换规则确定后才开放，这里只显示当前阶段。</p>
      {rebased && <div className={`${styles.banner} ${styles.bannerWarning}`} role="status" data-testid="switches-rebased"><div>开关已被其他管理员更新。保存会被拒绝，请放弃修改后按最新状态重新编辑。</div></div>}
      <div className={styles.row}>
        <button type="button" className={styles.button} disabled={!online || !current || changed.length === 0} onClick={() => draft && setConfirming({ base: draft.base, edits: draft.edits })}>保存变更</button>
        {draft && <button type="button" className={styles.buttonSecondary} onClick={() => setDraft(null)}>放弃修改</button>}
      </div>
    </div>}
    {confirming && <SwitchDialog key={confirming.base.revision} base={confirming.base} edits={confirming.edits} onClose={() => setConfirming(null)} onSaved={() => { setConfirming(null); setDraft(null); }} />}
  </section></ReadOnlyWhile>;
}

function SwitchDialog({ base, edits, onClose, onSaved }: { base: S['AdminSwitches']; edits: Partial<SwitchDraft>; onClose: () => void; onSaved: () => void }) {
  const draft = { ...draftOf(base), ...edits };
  const [reason, setReason] = useState('');
  const write = useAdminWrite<S['AdminSwitchesPatch'], S['AdminSwitches']>({
    operationId: 'adminUpdateSwitches', targetId: () => null,
    send: (api, body, key, token) => api.adminUpdateSwitches(body, key, token),
    onSuccess: onSaved,
  });
  const changes = switchKeys.filter(key => key in edits && draft[key] !== base[key]);
  const describe = (key: keyof SwitchDraft) => key === 'max_active_users' ? `活跃用户上限：${base.max_active_users} 人 → ${draft.max_active_users} 人` : `${switchFields.find(field => field.key === key)!.label}：${base[key] ? '开' : '关'} → ${draft[key] ? '开' : '关'}`;
  const error = reasonError(reason);
  return <AdminActionDialog open onClose={onClose} write={write} danger={changes.some(key => key !== 'max_active_users' && draft[key] === false)}
    title="修改平台开关" description="开关对所有用户立即生效，并记入审计记录。"
    prepare={<><ul data-testid="switch-changes">{changes.map(key => <li key={key}>{describe(key)}</li>)}</ul><ReasonField id="switch-reason" value={reason} onChange={setReason} error={error} /></>}
    prepareValid={error === null && changes.length > 0}
    build={() => ({ base_revision: base.revision, reason, ...Object.fromEntries(changes.map(key => [key, draft[key]])) })} />;
}

function Audit() {
  const audit = useAdminAudit();
  const users = useAdminUsers();
  const emails = useMemo(() => new Map((readableCache(users)?.items ?? []).map(user => [user.id, user.email])), [users]);
  const who = (id: string | null) => id === null ? '—' : emails.get(id) ?? '已不在列表中的账号';
  // Rows stay visible only while the latest failure (if any) was the transport; a denial hides them all.
  const items = readableCache(audit)?.pages.flatMap(page => page.items) ?? [];
  return <section className={styles.panel} aria-labelledby="admin-audit-title">
    <h2 id="admin-audit-title">管理操作记录</h2>
    <p className={`${styles.small} ${styles.muted}`}>记录操作人、对象和理由，不记录密码、验证码、邀请令牌或任何学习内容。</p>
    {audit.isPending ? <Loading label="正在读取操作记录…" /> : audit.isError && !items.length ? <QueryError error={audit.error} retry={() => void audit.refetch()} /> :
      items.length === 0 ? <Empty title="还没有管理操作" description="停用账号、邀请、额度和开关的变更会记录在这里。" /> : <>
        {(audit.isError || audit.isFetchNextPageError) && <p role="alert" className={styles.error}>{describeAdminError(audit.error, describeError)}</p>}
        <div className={styles.tableWrap}><table className={styles.table} data-testid="admin-audit">
          <thead><tr><th>时间</th><th>操作</th><th>对象</th><th>理由</th><th>操作人</th></tr></thead>
          <tbody>{items.map(item => <tr key={item.id}>
            <td>{formatDateTime(item.created_at)}</td>
            <td>{operationLabel[item.operation] ?? item.operation}</td>
            <td>{targetTypeLabel[item.target_type] ?? item.target_type}{item.target_type === 'user' && item.target_id ? `：${who(item.target_id)}` : item.target_type === 'invitation' && item.target_id ? ` ${invitationRef(item.target_id)}` : ''}</td>
            <td className={styles.wrap}>{item.reason}</td>
            <td>{who(item.actor_id)}</td>
          </tr>)}</tbody>
        </table></div>
        {audit.hasNextPage && <button type="button" className={styles.buttonSecondary} disabled={audit.isFetchingNextPage} onClick={() => void audit.fetchNextPage()}>{audit.isFetchingNextPage ? '正在读取…' : '加载更早的记录'}</button>}
      </>}
  </section>;
}
