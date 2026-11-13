import {useEffect,useRef,useState} from 'react';
import {AuthClient,AuthError} from '../api/auth-client';
import {RecoveryReceiverClient} from '../api/recovery-receiver';
import type {ReceiverRegisterResponse,ReceiverFactorStartResponse} from '../generated/identity-control';
import {Button,Input,Panel} from '../ui';
import styles from './AuthWorkbench.module.css';

export default function RecoveryReceiver(){
  const client=useRef<RecoveryReceiverClient|null>(null);
  if(!client.current)client.current=new RecoveryReceiverClient();
  const [registration,setRegistration]=useState<ReceiverRegisterResponse|null>(null),[factor,setFactor]=useState<ReceiverFactorStartResponse|null>(null);
  const [stage,setStage]=useState<'new'|'registered'|'waiting'|'delivered'|'confirmed'>('new');
  const [caseId,setCaseId]=useState(''),[generation,setGeneration]=useState('1'),[password,setPassword]=useState(''),[code,setCode]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
  useEffect(()=>()=>client.current?.clear(),[]);
  async function run(action:()=>Promise<void>){
    setBusy(true);setMessage('');
    try{await action();}
    catch(error){
      if(error instanceof AuthError&&['RESULT_UNKNOWN','STALE_ACCOUNT','OUTPUT_INVALID'].includes(error.code)){
        client.current?.clear();setFactor(null);setRegistration(null);setStage('new');setMessage('结果不明，页面不会重放秘密。请让原执行者查询事实，必要时重新批准接收。');
      }else setMessage('请求被拒绝或服务尚未启用。请核对本次恢复信息和验证码，再联系原执行者。');
    }finally{setBusy(false);setPassword('');setCode('');}
  }
  return <main className={styles.main} id="main-content"><header className={styles.brand}><span>课间</span><p>Classroom OS · 账户恢复</p></header><Panel className={styles.card}>
    <h1>接收新的验证器</h1><p>先完成独立身份核验，再由原执行者批准本页面。刷新或离开会丢失本次接收密钥。</p>
    {stage==='new'&&<Button disabled={busy} onClick={()=>void run(async()=>{const auth=new AuthClient();const csrf=await auth.refreshCsrf();const value=await client.current!.register(csrf.csrf_token);setRegistration(value);setStage('registered');})}>建立本次接收页</Button>}
    {registration&&<section className={styles.section}><h2>交给核验者核对</h2><p>接收端编号</p><code>{registration.receiver_id}</code><p>请通过独立核验渠道逐字核对完整指纹：</p><code className={styles.secret}>{registration.receiver_key_hash}</code></section>}
    {stage==='registered'&&<section className={styles.section}><Input label="本次恢复编号" value={caseId} onChange={e=>setCaseId(e.target.value)} autoComplete="off"/>
      <Input label="本次接收代次" value={generation} onChange={e=>setGeneration(e.target.value)} inputMode="numeric" autoComplete="off"/>
      <Button disabled={busy||!caseId||!Number.isSafeInteger(Number(generation))||Number(generation)<1} onClick={()=>void run(async()=>{setStage('waiting');try{await client.current!.wait(caseId,Number(generation));setStage('delivered');}catch(error){setStage('registered');throw error;}})}>绑定页面并等待批准</Button></section>}
    {stage==='waiting'&&<p role="status">正在等待原执行者批准。请保持此页面打开。</p>}
    {stage==='delivered'&&!factor&&<section className={styles.section}><p>学生请输入当前密码。管理员恢复请留空；服务端会核对实际账户类型。</p><Input label="当前密码（学生必填）" type="password" value={password} onChange={e=>setPassword(e.target.value)} autoComplete="current-password"/>
      <Button disabled={busy} onClick={()=>void run(async()=>setFactor(await client.current!.start(password||null)))}>接收一次性验证器密钥</Button></section>}
    {factor&&<section className={styles.section}><h2>添加到你自己的验证器</h2><p>密钥仅展示本次，请勿交给恢复执行者。</p><code className={styles.secret}>{factor.totp_seed}</code>
      <Input label="新验证器验证码" value={code} onChange={e=>setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={6}/>
      <Button disabled={busy||code.length!==6} onClick={()=>void run(async()=>{await client.current!.confirm(code);setFactor(null);setStage('confirmed');})}>确认新验证器</Button></section>}
    {stage==='confirmed'&&<p role="status">新验证器已确认。请等待原执行者手动完成；完成后使用密码和新验证码重新登录，再明确生成新的恢复码。</p>}
    {(stage==='delivered'||stage==='confirmed')&&<Button disabled={busy} variant="ghost" onClick={()=>void run(async()=>{await client.current!.abandon();setFactor(null);setRegistration(null);setStage('new');setMessage('本次接收已放弃，原验证器保持原状。');})}>放弃本次接收</Button>}
    {message&&<p role="status">{message}</p>}<p><a href="/">返回登录</a></p>
  </Panel></main>;
}
