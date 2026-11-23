import { useState } from 'react';
import type { components } from '../../generated/openapi';
import { invitationKindLabel, invitationStateLabel } from '../../domain/admin';
import { formatDateTime } from '../../domain/dashboard';
import { useAdminInvitations, useAdminWrite } from '../../features/admin';
import { ReadOnlyWhile, readableCache, useAppData } from '../../features/data';
import { Badge, Empty } from '../common';
import { AdminActionDialog, AdminData, AdminPage } from './AdminShell';
import styles from '../pages.module.css';

type S = components['schemas'];
const stateTone: Record<S['Invitation']['state'], string> = { issued: 'info', consumed: 'success', revoked: 'neutral', expired: 'warning' };
type Issued = { token: string; invitation: S['Invitation']; reissued: boolean };

/** AUTH-07 invitations page. A one-time token lives only in this page's memory until it is dismissed. */
export function AdminInvites() {
  return <AdminPage title="邀请" detail="创建、撤销或重新签发注册邀请。邀请只能注册学生账号。"><InvitesBody /></AdminPage>;
}

function InvitesBody() {
  const invitations = useAdminInvitations();
  const { online } = useAppData();
  const [creating, setCreating] = useState(false);
  const [action, setAction] = useState<{ kind: 'revoke' | 'reissue'; invitation: S['Invitation'] } | null>(null);
  const [issued, setIssued] = useState<Issued | null>(null);
  // A token is offered only while its invitation is still the issued revision the token belongs to.
  const listed = readableCache(invitations)?.items.find(item => item.id === issued?.invitation.id);
  const tokenCurrent = issued !== null && (listed === undefined || (listed.state === 'issued' && listed.revision === issued.invitation.revision));
  return <ReadOnlyWhile stale={invitations.isError}>
    {issued && tokenCurrent && <TokenPanel issued={issued} onDismiss={() => setIssued(null)} />}
    <section className={styles.panel} aria-labelledby="admin-invites-title">
      <div className={styles.sectionHead}><h2 id="admin-invites-title">邀请</h2><button type="button" className={styles.button} disabled={!online} onClick={() => setCreating(true)}>创建邀请</button></div>
      <AdminData query={invitations} label="正在读取邀请…">{page => page.items.length === 0 ? <Empty title="还没有邀请" description="创建邀请后，把一次性令牌通过可信渠道交给受邀人。" /> :
        <div className={styles.tableWrap}><table className={styles.table} data-testid="admin-invitations">
          <thead><tr><th>类型</th><th>邮箱</th><th>状态</th><th>创建时间</th><th>有效至</th><th>操作</th></tr></thead>
          <tbody>{page.items.map(item => <tr key={item.id} data-testid={`admin-invitation-${item.id}`}>
            <td>{invitationKindLabel[item.kind]}</td><td>{item.email_hint ?? '—'}</td>
            <td><Badge tone={stateTone[item.state]}>{invitationStateLabel[item.state]}</Badge></td>
            <td>{formatDateTime(item.created_at)}</td><td>{formatDateTime(item.expires_at)}</td>
            <td><div className={styles.row}>
              {(item.state === 'issued' || item.state === 'expired') && <button type="button" className={styles.buttonSecondary} disabled={!online} onClick={() => setAction({ kind: 'reissue', invitation: item })}>重新签发</button>}
              {item.state === 'issued' && <button type="button" className={styles.buttonDanger} disabled={!online} onClick={() => setAction({ kind: 'revoke', invitation: item })}>撤销</button>}
            </div></td>
          </tr>)}</tbody>
        </table></div>}
      </AdminData>
    </section>
    {creating && <CreateDialog onClose={() => setCreating(false)} onIssued={created => { setCreating(false); setIssued({ ...created, reissued: false }); }} />}
    {action?.kind === 'revoke' && <RevokeDialog key={action.invitation.id} invitation={action.invitation} onClose={() => setAction(null)} onRevoked={id => { setAction(null); setIssued(current => current?.invitation.id === id ? null : current); }} />}
    {action?.kind === 'reissue' && <ReissueDialog key={action.invitation.id} invitation={action.invitation} onClose={() => setAction(null)} onIssued={created => { setAction(null); setIssued({ ...created, reissued: true }); }} />}
  </ReadOnlyWhile>;
}

type Created = { token: string; invitation: S['Invitation'] };
const created = (reply: S['InvitationCreated']): Created => ({ token: reply.one_time_token, invitation: reply.invitation });

function CreateDialog({ onClose, onIssued }: { onClose: () => void; onIssued: (created: Created) => void }) {
  const [kind, setKind] = useState<S['InvitationCreate']['kind']>('email_bound');
  const [email, setEmail] = useState('');
  const write = useAdminWrite<S['InvitationCreate'], S['InvitationCreated']>({
    operationId: 'adminCreateInvitation', targetId: () => null,
    send: (api, body, key, token) => api.adminCreateInvitation(body, key, token),
    onSuccess: reply => onIssued(created(reply)),
  });
  const emailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim()) && email.trim().length <= 254;
  return <AdminActionDialog open onClose={onClose} write={write} title="创建邀请" description="邮箱邀请只能由该邮箱注册；单次邀请任何人都可使用一次。邀请默认 7 天内有效。"
    prepare={<>
      <fieldset className={styles.stack} style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className={styles.fieldLegend}>邀请类型</legend>
        <label className={styles.choice}><input type="radio" name="invite-kind" checked={kind === 'email_bound'} onChange={() => setKind('email_bound')} /> <span>邮箱邀请</span></label>
        <label className={styles.choice}><input type="radio" name="invite-kind" checked={kind === 'single_use'} onChange={() => setKind('single_use')} /> <span>单次邀请</span></label>
      </fieldset>
      {kind === 'email_bound' && <div className={styles.field}><label htmlFor="invite-email">受邀邮箱</label>
        <input id="invite-email" type="email" autoComplete="off" value={email} onChange={event => setEmail(event.target.value)} aria-invalid={email !== '' && !emailValid ? true : undefined} />
        {email !== '' && !emailValid && <span className={styles.error}>请输入有效的邮箱地址。</span>}</div>}
    </>}
    prepareValid={kind === 'single_use' || emailValid}
    build={() => kind === 'email_bound' ? { kind, email: email.trim() } : { kind }} />;
}

function RevokeDialog({ invitation, onClose, onRevoked }: { invitation: S['Invitation']; onClose: () => void; onRevoked: (id: string) => void }) {
  const write = useAdminWrite<Record<string, never>, S['Invitation']>({
    operationId: 'adminRevokeInvitation', targetId: () => invitation.id,
    send: (api, _body, key, token) => api.adminRevokeInvitation(invitation.id, key, token),
    onSuccess: () => onRevoked(invitation.id),
  });
  return <AdminActionDialog open onClose={onClose} write={write} danger title="撤销邀请"
    description={`撤销后这个${invitationKindLabel[invitation.kind]}${invitation.email_hint ? `（${invitation.email_hint}）` : ''}立即失效，已注册的账号不受影响。`}
    prepareValid build={() => ({})} confirmLabel="下一步：验证身份后撤销" />;
}

function ReissueDialog({ invitation, onClose, onIssued }: { invitation: S['Invitation']; onClose: () => void; onIssued: (created: Created) => void }) {
  const write = useAdminWrite<Record<string, never>, S['InvitationCreated']>({
    operationId: 'adminReissueInvitation', targetId: () => invitation.id,
    send: (api, _body, key, token) => api.adminReissueInvitation(invitation.id, key, token),
    onSuccess: reply => onIssued(created(reply)),
  });
  return <AdminActionDialog open onClose={onClose} write={write} title="重新签发邀请"
    description={`将为这个${invitationKindLabel[invitation.kind]}${invitation.email_hint ? `（${invitation.email_hint}）` : ''}生成新的一次性令牌，旧令牌立即失效，有效期重新计算。`}
    prepareValid build={() => ({})} />;
}

/** Shown once: the token is not cached, not persisted and cannot be shown again after dismissal. */
function TokenPanel({ issued, onDismiss }: { issued: Issued; onDismiss: () => void }) {
  const [copied, setCopied] = useState<'yes' | 'failed' | null>(null);
  const copy = () => { navigator.clipboard.writeText(issued.token).then(() => setCopied('yes'), () => setCopied('failed')); };
  return <section className={`${styles.banner} ${styles.bannerWarning}`} role="status" aria-labelledby="invite-token-title" data-testid="invite-token">
    <div className={styles.stack}>
      <strong id="invite-token-title">{issued.reissued ? '新的一次性令牌' : '邀请已创建'}：只显示这一次</strong>
      <span>{invitationKindLabel[issued.invitation.kind]}{issued.invitation.email_hint ? `（${issued.invitation.email_hint}）` : ''}，有效至 {formatDateTime(issued.invitation.expires_at)}。请通过可信渠道交给受邀人；关闭后无法再次查看，只能重新签发。</span>
      <div className={styles.field}><label htmlFor="invite-token-value">一次性令牌</label><input id="invite-token-value" readOnly value={issued.token} onFocus={event => event.target.select()} /></div>
      <div className={styles.row}>
        <button type="button" className={styles.buttonSecondary} onClick={copy}>复制令牌</button>
        <button type="button" className={styles.button} onClick={onDismiss}>我已保存，关闭</button>
        {copied === 'yes' && <span className={styles.success}>已复制</span>}
        {copied === 'failed' && <span className={styles.error}>复制失败，请手动选中复制。</span>}
      </div>
    </div>
  </section>;
}
