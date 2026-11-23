import { useState } from 'react';
import { Link } from 'react-router';
import { ApiClientError } from '../../api/client';
import { formatBasisPoints, formatYuan, levelLabel, recentMonths, usageView } from '../../domain/admin';
import { formatDateTime } from '../../domain/dashboard';
import { useAdminUsage } from '../../features/admin';
import { readableCache, useAppData } from '../../features/data';
import { Badge, Empty, QueryError, Loading } from '../common';
import { AdminPage } from './AdminShell';
import styles from '../pages.module.css';

const levelTone = { normal: 'success', warning60: 'info', warning80: 'warning', blocked100: 'danger' } as const;

/** AUTH-07 usage page: the platform's monthly cost ledger in exact integer microyuan (11a). */
export function AdminUsage() {
  return <AdminPage title="用量" detail="按北京时间自然月查看平台费用、预留和结果未知的风险。"><UsageBody /></AdminPage>;
}

function UsageBody() {
  const { now } = useAppData();
  const months = recentMonths(now());
  const [period, setPeriod] = useState(months[0]!.start);
  const usage = useAdminUsage(period);
  const data = readableCache(usage);
  const missing = usage.isError && usage.error instanceof ApiClientError && usage.error.code === 'NOT_FOUND';
  const view = data ? usageView(data) : null;
  return <section className={styles.panel} aria-labelledby="admin-usage-title">
    <div className={styles.sectionHead}><h2 id="admin-usage-title">平台费用</h2>
      <div className={styles.field}><label htmlFor="usage-period">月份</label>
        <select id="usage-period" value={period} onChange={event => setPeriod(event.target.value)}>{months.map(month => <option key={month.start} value={month.start}>{month.label}</option>)}</select></div>
    </div>
    {missing ? <Empty title="这个月没有用量记录" description="平台在该月尚未开始计费，或记录已超出保留期。" />
      : !data ? (usage.isError ? <QueryError error={usage.error} retry={() => void usage.refetch()} /> : <Loading label="正在读取用量…" />)
      : !view ? <p role="alert" className={styles.error}>服务返回的金额格式无法识别，没有显示任何数字。请刷新后重试。</p>
      : <div className={styles.stack} data-testid="admin-usage">
        {usage.isError && <div className={`${styles.banner} ${styles.bannerWarning}`} role="status"><div>刷新失败，以下为上次读取的内容。</div></div>}
        <div className={styles.row}><Badge tone={levelTone[view.level]}>{levelLabel[view.level]}</Badge><span className={styles.muted}>风险合计占预算 {formatBasisPoints(view.basisPoints)}</span></div>
        <div className={styles.meter} role="meter" aria-label="风险合计占预算比例" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, Math.floor(view.basisPoints / 100))}><span style={{ width: `${Math.min(100, view.basisPoints / 100)}%` }} /></div>
        <p className={`${styles.small} ${styles.muted}`}>提醒线：60%、80%；到 100% 时停止接收新的收费任务，已有记录仍可核对。</p>
        <dl className={styles.dl}>
          <dt>月度预算</dt><dd>{formatYuan(view.budget)}</dd>
          <dt>固定与已计提</dt><dd>{formatYuan(view.fixed)}</dd>
          <dt>已结算</dt><dd>{formatYuan(view.settled)}</dd>
          <dt>进行中预留</dt><dd>{formatYuan(view.reserved)}</dd>
          <dt>结果未知</dt><dd>{formatYuan(view.unknown)}{view.unknown > 0n && <span className={styles.warning}>（按可能已收费计入，待核对）</span>}</dd>
          <dt>风险合计</dt><dd><strong>{formatYuan(view.exposure)}</strong></dd>
          <dt>安全预留</dt><dd>{formatYuan(view.safetyReserve)}</dd>
          <dt>新任务可用</dt><dd className={view.admissible < 0n ? styles.danger : undefined}>{formatYuan(view.admissible)}</dd>
          <dt>价格版本</dt><dd>{data.price_version}</dd>
          <dt>周期</dt><dd>{formatDateTime(data.period_start)} 至 {formatDateTime(data.period_end)}</dd>
          <dt>账本核对</dt><dd>{data.ledger_reconciled_at ? formatDateTime(data.ledger_reconciled_at) : '尚未完成核对'}</dd>
        </dl>
        <p className={`${styles.small} ${styles.muted}`}>这里只有平台汇总。单个用户的用量请在 <Link to="/admin/users">用户</Link> 页查看其额度。</p>
      </div>}
  </section>;
}
