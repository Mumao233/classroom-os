import { useState, type ReactNode } from 'react';
import { Link, NavLink } from 'react-router';
import { describeAdminError, type AdminWrite } from '../../features/admin';
import { describeError, isTransportFailure, readableCache, useAppData, useMe } from '../../features/data';
import { Dialog } from '../../ui';
import { DataSourceNote, Loading, OfflineBanner, PageHeading, QueryError } from '../common';
import shell from '../../app/Shell.module.css';
import styles from '../pages.module.css';

const tabs = [
  { to: '/admin/users', label: '用户' }, { to: '/admin/invites', label: '邀请' },
  { to: '/admin/usage', label: '用量' }, { to: '/admin/runtime', label: '运行状态' },
] as const;

/**
 * AUTH-07 / FE-13: the four operations pages exist only for an administrator with MFA. The role comes
 * from the server's `/me`; until it is known (or for a student) no `/admin/*` request is ever made.
 */
export function AdminPage({ title, detail, children }: { title: string; detail: string; children: ReactNode }) {
  const me = useMe();
  const user = readableCache(me);
  let body: ReactNode;
  if (!user) body = me.isError ? <QueryError error={me.error} retry={() => void me.refetch()} /> : <Loading label="正在确认账号权限…" />;
  else if (user.role !== 'admin') body = <section className={styles.panel} data-testid="admin-denied"><h2>无权访问运营后台</h2><p className={styles.muted}>运营后台只对管理员开放。当前账号是学生账号。</p><Link className={styles.button} to="/dashboard">返回首页</Link></section>;
  else if (!user.mfa_enabled) body = <section className={styles.panel} data-testid="admin-mfa-required"><h2>需要先启用双重验证</h2><p className={styles.muted}>管理员必须完成双重验证才能使用运营后台。</p><Link className={styles.button} to="/settings/security">前往账号安全</Link></section>;
  else body = <>
    <nav className={styles.tabs} aria-label="运营后台分区">{tabs.map(tab => <NavLink key={tab.to} className={styles.tab} to={tab.to}>{tab.label}</NavLink>)}</nav>
    <div className={styles.stack}>
      <p className={`${styles.small} ${styles.muted}`} data-testid="admin-boundary">后台只显示账号、额度和运行元数据，不提供查看学生课堂、笔记或录音的入口，也不能以其他用户身份登录。</p>
      <OfflineBanner scope="后台只读显示已加载的内容，管理操作已停用；" />
      {children}
    </div>
  </>;
  return <main id="main-content" className={shell.page} tabIndex={-1} data-testid="admin-page">
    <PageHeading eyebrow="运营后台" title={title} detail={detail} focusKey={title} />
    <DataSourceNote />
    {body}
  </main>;
}

/** Data of an admin read: shown only while its latest failure (if any) was the transport. */
export function AdminData<T>({ query, label, children }: { query: { data: T | undefined; isError: boolean; isPending: boolean; error: unknown; refetch: () => unknown }; label: string; children: (data: T) => ReactNode }) {
  const data = readableCache(query);
  if (data === undefined) return query.isError ? <QueryError error={query.error} retry={() => void query.refetch()} /> : <Loading label={label} />;
  return <>
    {query.isError && isTransportFailure(query.error) && <div className={`${styles.banner} ${styles.bannerWarning}`} role="status"><div>刷新失败：{describeError(query.error)} 以下为上次读取的内容。</div></div>}
    {children(data)}
  </>;
}

/**
 * One admin write in a dialog: the operation's own fields first (reason included where the contract has
 * one), then password + current verification code, then the request. A reply that never arrived keeps
 * the same request for an explicit retry; leaving re-reads the server state instead of guessing.
 */
export function AdminActionDialog<B>({ open, onClose, title, description, write, prepare, prepareValid, build, confirmLabel = '下一步：验证身份', danger = false }: {
  open: boolean; onClose: () => void; title: string; description: string; write: AdminWrite<B>;
  prepare?: ReactNode; prepareValid: boolean; build: () => B; confirmLabel?: string; danger?: boolean;
}) {
  const { online } = useAppData();
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [verifying, setVerifying] = useState(false);
  const close = () => { setPassword(''); setCode(''); write.cancel(); onClose(); };
  const codeValid = /^[0-9]{6}$/.test(code);
  const verify = async () => {
    setVerifying(true);
    // The factors are used for this one proof and cleared at once, whatever the outcome.
    const [typedPassword, typedCode] = [password, code];
    setPassword(''); setCode('');
    try { await write.verify(typedPassword, typedCode); } finally { setVerifying(false); }
  };
  const message = write.error ? describeAdminError(write.error, describeError) : null;
  return <Dialog open={open} onOpenChange={value => { if (!value && write.stage !== 'sending' && !verifying) close(); }} title={title} description={description}>
    <div className={styles.stack} data-testid="admin-action">
      {write.stage === 'idle' && <>
        {prepare}
        {!online && <p className={styles.error}>离线时不能执行管理操作。</p>}
        <div className={styles.row}>
          <button type="button" className={danger ? styles.buttonDanger : styles.button} disabled={!prepareValid || !online} onClick={() => write.start(build())}>{confirmLabel}</button>
          <button type="button" className={styles.buttonSecondary} onClick={close}>取消</button>
        </div>
      </>}
      {write.stage === 'reauth' && <form className={styles.stack} onSubmit={event => { event.preventDefault(); if (password && codeValid && online && !verifying) void verify(); }}>
        <p className={`${styles.small} ${styles.muted}`}>管理操作需要重新验证。验证结果只用于这一次操作，短时间内有效。</p>
        <div className={styles.field}><label htmlFor="admin-reauth-password">当前密码</label>
          <input id="admin-reauth-password" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} /></div>
        <div className={styles.field}><label htmlFor="admin-reauth-code">验证器中的 6 位验证码</label>
          <input id="admin-reauth-code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={event => setCode(event.target.value.replace(/\D/g, ''))} aria-invalid={code !== '' && !codeValid ? true : undefined} /></div>
        {message && <p role="alert" className={styles.error}>{message}</p>}
        {!online && <p className={styles.error}>离线时不能验证。</p>}
        <div className={styles.row}>
          <button type="submit" className={danger ? styles.buttonDanger : styles.button} disabled={!password || !codeValid || !online || verifying}>{verifying ? '正在验证…' : '验证并执行'}</button>
          <button type="button" className={styles.buttonSecondary} disabled={verifying} onClick={close}>取消</button>
        </div>
      </form>}
      {write.stage === 'sending' && <p role="status" className={styles.muted}>正在提交…</p>}
      {write.stage === 'unconfirmed' && <>
        <div className={`${styles.banner} ${styles.bannerWarning}`} role="alert" data-testid="admin-unconfirmed"><div><strong>没有收到服务端确认</strong>：操作可能已经生效。重试会发送同一个请求，不会重复执行。{message ? ` ${message}` : ''}</div></div>
        <div className={styles.row}>
          <button type="button" className={styles.button} disabled={!online} onClick={write.retry}>重试同一操作</button>
          <button type="button" className={styles.buttonSecondary} onClick={close}>放弃并刷新</button>
        </div>
      </>}
      {write.stage === 'failed' && <>
        <p role="alert" className={styles.error} data-testid="admin-failed">{message}</p>
        <div className={styles.row}><button type="button" className={styles.buttonSecondary} onClick={close}>关闭</button></div>
      </>}
    </div>
  </Dialog>;
}

/** Reason textarea shared by every write that carries one (it is stored in the audit trail). */
export function ReasonField({ id, value, onChange, error }: { id: string; value: string; onChange: (value: string) => void; error: string | null }) {
  const [touched, setTouched] = useState(false);
  return <div className={styles.field}><label htmlFor={id}>操作理由</label>
    <textarea id={id} value={value} onBlur={() => setTouched(true)} onChange={event => onChange(event.target.value)} aria-invalid={touched && error ? true : undefined} aria-describedby={`${id}-hint`} />
    <span id={`${id}-hint`} className={touched && error ? styles.error : styles.hint}>{touched && error ? error : '理由会写入审计记录，请不要填写密码、验证码或学生的学习内容。'}</span></div>;
}
