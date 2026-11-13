import { useEffect, useRef, useState, type FormEvent } from 'react';
import type { components } from '../generated/openapi';
import { AuthClient, AuthError } from '../api/auth-client';
import { Button, Input, Panel } from '../ui';
import styles from './AuthWorkbench.module.css';

type S = components['schemas'];
type Challenge = Extract<S['LoginResult'], {state:'mfa_required'}>;
type Screen = 'loading'|'unavailable'|'login'|'mfa'|'recovery'|'account';
const messages: Record<string,string> = {
  UNAUTHENTICATED:'凭据无效或已过期。验证码使用后不能再次使用，请等待下一组验证码。',
  FORBIDDEN:'当前操作未获授权，请刷新页面后重试。', RATE_LIMITED:'尝试过于频繁，请稍后再试。',
  SERVICE_DISABLED:'这项服务暂未启用，请联系项目负责人。', REAUTH_REQUIRED:'请重新验证当前密码和必要的验证码。',
  VALIDATION_FAILED:'请检查输入内容。学生新密码至少 6 个字符，管理员至少 12 个字符。',
  SECRET_ALREADY_ISSUED:'秘密已展示一次。若未保存，请重新验证身份并明确发起新操作。',
  RESULT_UNKNOWN:'提交结果暂不确定。请先重新登录核对状态，不要重复恢复操作。',
  INCOMPATIBLE_CLIENT:'客户端与服务端不兼容，请刷新或更新后重试。',
  AUTH_CONTEXT_EXPIRED:'登录或验证流程已过期，请重新登录。',
};

export default function AuthWorkbench() {
  const api=useRef<AuthClient|null>(null);
  const [reload,setReload]=useState(0),[screen,setScreen]=useState<Screen>('loading');
  const [session,setSession]=useState<S['SessionContext']|null>(null),[challenge,setChallenge]=useState<Challenge|null>(null);
  const [busy,setBusy]=useState(false),[message,setMessage]=useState('');
  const [email,setEmail]=useState(''),[password,setPassword]=useState(''),[code,setCode]=useState('');
  const [newPassword,setNewPassword]=useState(''),[remember,setRemember]=useState(false),[recoveryCode,setRecoveryCode]=useState(false);
  const [seed,setSeed]=useState<S['MfaEnrollment']|S['RecoveryReceiverEnrollment']|null>(null),[newCode,setNewCode]=useState('');
  const [codes,setCodes]=useState<string[]>([]),[sessions,setSessions]=useState<S['DeviceSession'][]>([]);
  const [devices,setDevices]=useState<S['TrustedDevice'][]>([]),[newEmail,setNewEmail]=useState('');
  const [mode,setMode]=useState<'login'|'register'|'forgot'|'reset'|'verify'|'confirm_email'>('login'),[linkToken,setLinkToken]=useState('');
  const [mailIntent,setMailIntent]=useState<{mode:'reset'|'verify'|'confirm_email';token:string}|null>(null);
  useEffect(()=>{
    const mailMode={'/reset-password':'reset','/verify-email':'verify','/confirm-email':'confirm_email'}[location.pathname] as 'reset'|'verify'|'confirm_email'|undefined;
    const token=new URLSearchParams(location.hash.slice(1)).get('token');
    if(mailMode&&token){history.replaceState(null,'',location.pathname);if(/^[A-Za-z0-9_-]{43}$/.test(token))setMailIntent({mode:mailMode,token});}
    const client=new AuthClient();api.current=client;let active=true;
    void client.bootstrap().then(value=>{if(active){setSession(value);setScreen(value?'account':'login');}})
      .catch(()=>{if(active)setScreen('unavailable');});
    return ()=>{active=false;client.clear();};
  },[reload]);
  useEffect(()=>{if(screen==='login'&&mailIntent){setMode(mailIntent.mode);setLinkToken(mailIntent.token);}},[screen,mailIntent]);
  const client=()=>{if(!api.current)throw new AuthError('STORAGE_UNAVAILABLE');return api.current;};
  async function run(action:()=>Promise<void>) {
    setBusy(true);setMessage('');
    try{await action();}catch(error){
      if(error instanceof AuthError && error.code==='AUTH_CONTEXT_EXPIRED') {
        setSession(null);setChallenge(null);setSeed(null);setCodes([]);setSessions([]);setDevices([]);
        setScreen('loading');setReload(value=>value+1);
      }
      setMessage(error instanceof AuthError ? messages[error.code]??'服务暂不可用，请稍后重试。':'服务暂不可用，请稍后重试。');
    }
    finally{setPassword('');setCode('');setNewPassword('');setNewCode('');setLinkToken('');setBusy(false);}
  }
  async function accept(value:S['LoginResult']) {
    setSeed(null);setCodes([]);
    if(value.state==='authenticated') {const current=await client().bootstrap();setSession(current);setScreen('account');setChallenge(null);}
    else if(value.state==='mfa_required'){setChallenge(value);setScreen('mfa');}
    else {setChallenge(null);setScreen('recovery');}
  }
  async function loggedOut() {
    client().clear();setSession(null);setChallenge(null);setSeed(null);setCodes([]);setSessions([]);setDevices([]);
    await client().refreshCsrf();setScreen('login');
  }
  function restartLogin() {
    client().clear();setSession(null);setChallenge(null);setSeed(null);setCodes([]);setSessions([]);setDevices([]);
    setPassword('');setCode('');setNewPassword('');setNewCode('');setLinkToken('');setRecoveryCode(false);
    setMailIntent(null);setMode('login');setMessage('请重新输入密码开始登录。');
    setScreen('loading');setReload(value=>value+1);
  }
  function submit(event:FormEvent) {
    event.preventDefault();
    void run(async()=>{
      if(screen==='login') {
        if(mode==='login')await accept(await client().login({email,password,remember_me:remember,device_name:'浏览器'}));
        else {
          const path={register:'/auth/register',forgot:'/auth/password/forgot',reset:'/auth/password/reset',verify:'/auth/verify-email',confirm_email:'/auth/email/confirm'}[mode];
          const body=mode==='register'?{email,password,invitation_token:linkToken}:mode==='forgot'?{email}:mode==='reset'?{token:linkToken,new_password:password}:{token:linkToken};
          await client().request(path,body);setMailIntent(null);setMode('login');setMessage('请求已接收。如需邮件验证，请查看对应邮箱后完成验证。');
        }
      } else if(screen==='mfa'&&challenge) {
        await accept(recoveryCode?await client().recover(challenge,code):await client().totp(challenge.challenge_id,code));
      } else if(screen==='recovery') {
        const value=await client().recoveryAction(seed?'confirm':'enroll',password,seed?.enrollment_id,newCode);
        if('otpauth_uri' in value)setSeed(value);
        else {await loggedOut();setMessage('验证器已更换。请使用密码和新的验证码完整登录，再明确生成新的恢复码。');}
      }
    });
  }
  async function sensitive(purpose:S['ReauthOperation']['purpose'],body:Record<string,unknown>,path:string,targetId?:string) {
    if(!session)throw new AuthError('UNAUTHENTICATED');
    const kind=purpose==='confirmMfa'?'mfa_enrollment':purpose==='revokeSession'?'session':purpose==='revokeDevice'?'device':'account';
    return client().change(purpose,{kind,id:targetId??session.user.id},body,password,code||undefined,path);
  }
  const controls=<><Input label="当前密码" type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} maxLength={2048}/>
    {session?.user.mfa_enabled&&<Input label="当前验证器验证码" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={e=>setCode(e.target.value)} maxLength={6}/>}</>;
  return <main className={styles.main} id="main-content"><header className={styles.brand}><span aria-hidden="true">课间</span><p>Classroom OS · Web Alpha</p></header>
    <Panel className={styles.card}>
      {screen==='loading'?<><h1>正在检查会话…</h1><p role="status">请稍候。</p></>:screen==='unavailable'?<><h1>服务暂不可用</h1><p>无法连接认证服务。请连接可用的服务后重试。</p><Button onClick={()=>{setScreen('loading');setReload(x=>x+1);}}>重新连接</Button></>:
      screen==='account'&&session?<>
        <p className={styles.eyebrow}>账户与安全</p><h1>{session.user.nickname||session.user.email}</h1><p>{session.user.role==='admin'?'管理员':'学生'} · {session.user.mfa_enabled?'已启用双重验证':'尚未启用双重验证'}</p>
        {mailIntent&&<p role="status">检测到邮件验证请求。请先退出当前会话，再继续验证。</p>}
        <div className={styles.actions}><Button disabled={busy} variant="secondary" onClick={()=>void run(async()=>{await client().logout();await loggedOut();})}>退出当前会话</Button>
          <Button disabled={busy} variant="ghost" onClick={()=>void run(async()=>{const page=await client().request<S['DeviceSessionPage']>('/me/sessions');setSessions(page.items);})}>查看登录会话</Button>
          <Button disabled={busy} variant="ghost" onClick={()=>void run(async()=>{const page=await client().request<S['TrustedDevicePage']>('/me/devices');setDevices(page.items);})}>查看设备</Button></div>
        {!!sessions.length&&<ul className={styles.list}>{sessions.map(s=><li key={s.id}><span>{s.device_name} · {s.is_current?'当前会话':s.revoked_at?'已撤销':'其他会话'}</span>{!s.is_current&&!s.revoked_at&&<Button disabled={busy} variant="ghost" onClick={()=>void run(async()=>{await sensitive('revokeSession',{},'/me/sessions/'+s.id+'/revoke',s.id);setSessions([]);setMessage('已撤销该会话。');})}>撤销</Button>}</li>)}</ul>}
        {!!devices.length&&<ul className={styles.list}>{devices.map(d=><li key={d.id}><span>{d.device_name} · {d.id===session.device_id?'当前设备':d.revoked_at?'已撤销':'其他设备'}</span>{!d.revoked_at&&<Button disabled={busy} variant="ghost" onClick={()=>void run(async()=>{await sensitive('revokeDevice',{},'/me/devices/'+d.id+'/revoke',d.id);setDevices([]);if(d.id===session.device_id)await loggedOut();else setMessage('已撤销该设备。');})}>撤销设备</Button>}</li>)}</ul>}
        <div className={styles.section}><h2>验证敏感操作</h2><p>每次操作都绑定本次提交内容。已使用的验证码不能重复用于下一项操作。</p>{controls}
          <Input label="新密码" type="password" autoComplete="new-password" value={newPassword} onChange={e=>setNewPassword(e.target.value)} maxLength={2048}/>
          <div className={styles.actions}><Button disabled={busy||!password||!newPassword} onClick={()=>void run(async()=>{await sensitive('changePassword',{new_password:newPassword},'/auth/password/change');await loggedOut();setMessage('密码已更改，请重新登录。');})}>更改密码</Button>
            <Button disabled={busy||!password} variant="secondary" onClick={()=>void run(async()=>{await sensitive('logoutAll',{},'/auth/logout-all');await loggedOut();})}>退出全部会话</Button></div>
          <Input label="新邮箱" type="email" autoComplete="email" value={newEmail} onChange={e=>setNewEmail(e.target.value)} maxLength={254}/>
          <Button disabled={busy||!password||!newEmail} variant="secondary" onClick={()=>void run(async()=>{await sensitive('requestEmailChange',{new_email:newEmail},'/auth/email/change');setNewEmail('');setMessage('更改请求已接收，请在新邮箱完成确认。');})}>请求更改邮箱</Button>
        </div>
        <div className={styles.section}><h2>验证器与恢复码</h2>{seed?<><p>将以下地址导入你自己的验证器，确认前原验证器仍有效。</p><code className={styles.secret}>{seed.otpauth_uri}</code>
          <Input label="新验证器验证码" autoComplete="one-time-code" inputMode="numeric" value={newCode} onChange={e=>setNewCode(e.target.value)} maxLength={6}/>
          <Button disabled={busy||!password||newCode.length!==6} onClick={()=>void run(async()=>{const value=await sensitive('confirmMfa',{enrollment_id:seed.enrollment_id,code:newCode},'/auth/mfa/confirm',seed.enrollment_id) as S['RecoveryCodes'];setSeed(null);await loggedOut();setCodes(value.codes);setMessage('验证器已启用。请保存下列恢复码，然后重新登录。');})}>确认新验证器</Button></>:
          <div className={styles.actions}><Button disabled={busy||!password} variant="secondary" onClick={()=>void run(async()=>setSeed(await sensitive('enrollMfa',{},'/auth/mfa/enroll') as S['MfaEnrollment']))}>{session.user.mfa_enabled?'更换验证器':'启用验证器'}</Button>
            {session.user.mfa_enabled&&<Button disabled={busy||!password} variant="secondary" onClick={()=>void run(async()=>{const value=await sensitive('rotateRecoveryCodes',{},'/auth/mfa/recovery-codes') as S['RecoveryCodes'];setCodes(value.codes);})}>生成或轮换恢复码</Button>}
            {session.user.mfa_enabled&&session.user.role==='student'&&<Button disabled={busy||!password} variant="danger" onClick={()=>void run(async()=>{await sensitive('disableMfa',{},'/auth/mfa/disable');await loggedOut();})}>关闭双重验证</Button>}</div>}
        </div>
      </>:<>
        <h1>{screen==='mfa'?'验证登录':screen==='recovery'?'更换验证器':mode==='register'?'使用邀请注册':mode==='forgot'?'找回密码':mode==='reset'?'重置密码':mode==='verify'?'验证邮箱':mode==='confirm_email'?'确认新邮箱':'登录课间'}</h1>
        <form className={styles.form} onSubmit={submit}>
          {screen==='login'&&<>
            {mode!=='reset'&&mode!=='verify'&&mode!=='confirm_email'&&<Input label="邮箱" type="email" autoComplete="username" required value={email} onChange={e=>setEmail(e.target.value)} maxLength={254}/>}
            {mode!=='forgot'&&mode!=='verify'&&mode!=='confirm_email'&&<Input label={mode==='reset'?'新密码':'密码'} type="password" autoComplete={mode==='login'?'current-password':'new-password'} required value={password} onChange={e=>setPassword(e.target.value)} maxLength={2048}/>}
            {['register','reset','verify','confirm_email'].includes(mode)&&<Input label={mode==='register'?'邀请码':'邮件令牌'} type="password" autoComplete="off" required value={linkToken} onChange={e=>setLinkToken(e.target.value)} maxLength={256}/>}
            {mode==='login'&&<label className={styles.check}><input type="checkbox" checked={remember} onChange={e=>setRemember(e.target.checked)}/>在此浏览器保留学生会话</label>}
          </>}
          {screen==='mfa'&&<><p>密码已验证。请输入当前验证码，或使用恢复码更换丢失的验证器。</p><label className={styles.check}><input type="checkbox" checked={recoveryCode} onChange={e=>{setRecoveryCode(e.target.checked);setCode('');}}/>使用恢复码</label>
            <Input label={recoveryCode?'恢复码':'验证器验证码'} type={recoveryCode?'password':'text'} autoComplete={recoveryCode?'off':'one-time-code'} inputMode={recoveryCode?'text':'numeric'} required value={code} onChange={e=>setCode(e.target.value)} maxLength={recoveryCode?128:6}/></>}
          {screen==='recovery'&&<><p>此流程只用于更换验证器。完成后需重新登录。</p><Input label="再次验证当前密码" type="password" autoComplete="current-password" required value={password} onChange={e=>setPassword(e.target.value)} maxLength={2048}/>
            {seed&&<><code className={styles.secret}>{seed.otpauth_uri}</code><Input label="新验证器验证码" autoComplete="one-time-code" required value={newCode} onChange={e=>setNewCode(e.target.value)} maxLength={6}/></>}</>}
          <Button type="submit" disabled={busy}>{busy?'正在处理…':screen==='recovery'?(seed?'确认更换':'生成新验证器'):screen==='mfa'?'验证':'继续'}</Button>
        </form>
        {(screen==='mfa'||screen==='recovery')&&<><p>验证码输错后可以重试。若流程已过期或账户发生更改，请重新开始登录；当前验证器和恢复码不会因此重置。</p>
          <Button variant="ghost" disabled={busy} onClick={restartLogin}>重新开始登录</Button></>}
        {screen==='login'&&<nav className={styles.links} aria-label="账户入口">{([['login','登录'],['register','邀请注册'],['forgot','忘记密码'],['reset','重置密码'],['verify','验证邮箱']] as const).filter(([value])=>value!==mode).map(([value,label])=><Button variant="ghost" key={value} disabled={busy} onClick={()=>{setMailIntent(null);setMode(value);setPassword('');setLinkToken('');setMessage('');}}>{label}</Button>)}</nav>}
      </>}
      {!!codes.length&&<aside className={styles.section}><h2>恢复码只展示这一次</h2><p>请保存在你自己的安全位置。关闭页面后无法重新查看，下一次轮换会废弃旧码。</p><div className={styles.codes}>{codes.map(value=><code key={value}>{value}</code>)}</div><Button variant="secondary" onClick={()=>setCodes([])}>我已妥善保存</Button></aside>}
      {message&&<p role="status" className={styles.message}>{message}</p>}
    </Panel>
  </main>;
}
