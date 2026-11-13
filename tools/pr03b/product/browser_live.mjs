// A real Chrome session against the test-owned HTTPS server. No trace, cookie
// values, passwords, seeds or recovery-code values are exported as evidence.
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
const cfg=JSON.parse(await new Promise(resolve=>{let s='';process.stdin.setEncoding('utf8');process.stdin.on('data',v=>s+=v);process.stdin.on('end',()=>resolve(s));}));
const {chromium}=await import(pathToFileURL(cfg.playwrightModule).href);
let stage='launch',browser,page,tlsDetails=null,loginPosts=0;const steps=[],responses=[],pageErrors=[];
const mark=name=>{stage=name;steps.push(name);};
try {
  // Two trust modes. Linux: a private NSS store holding only this run's CA, so the chain and
  // the hostname are really validated. Windows guest (pinnedKey): Chromium has no per-profile
  // trust store there and the OS store is left untouched, so exactly this run's server key
  // is allow-listed. That mode cannot check the hostname or the chain and never claims to;
  // its negative control is a second listener with a different key, which must be refused.
  const pinned=cfg.tlsMode==='pinned_key';
  if(!pinned&&!cfg.strictLinuxTLS)throw new Error('strict_isolated_Chromium_trust_required');
  if(pinned&&!/^[A-Za-z0-9+/]{43}=$/.test(cfg.spki??''))throw new Error('pinned_key_required');
  browser=await chromium.launch({...(cfg.chromium?{executablePath:cfg.chromium}:{}),headless:true,timeout:20000,
    args:['--disable-gpu','--disable-background-networking','--disable-sync','--no-first-run','--no-sandbox',...(pinned?['--ignore-certificate-errors-spki-list='+cfg.spki]:[])]});
  mark('browser_launched');
  const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block',acceptDownloads:false});
  const wrongHost=pinned?cfg.unpinnedOrigin:cfg.origin.replace('127.0.0.1','localhost');
  await context.route('**/*',route=>route.request().url().startsWith(cfg.origin+'/')||route.request().url().startsWith(wrongHost+'/')?route.continue():route.abort());
  page=await context.newPage();
  page.on('response',r=>{if(responses.length<32)responses.push({path:new URL(r.url()).pathname,status:r.status()});});
  page.on('pageerror',e=>pageErrors.push({kind:e.name}));
  let rejected=false;
  try{await page.goto(wrongHost+'/');}catch(error){rejected=error.message.includes(pinned?'ERR_CERT_AUTHORITY_INVALID':'ERR_CERT_COMMON_NAME_INVALID');}
  if(!rejected)throw new Error('certificate_hostname_must_be_rejected');
  mark(pinned?'TLS_unpinned_key_rejected':'TLS_invalid_hostname_rejected');
  // Chromium finishes its error-document navigation asynchronously. A fresh
  // page keeps that negative control from interrupting the positive test.
  await page.close();page=await context.newPage();
  page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/web/v1/auth/login')loginPosts++;});
  page.on('response',r=>{if(responses.length<32)responses.push({path:new URL(r.url()).pathname,status:r.status()});});
  page.on('pageerror',e=>pageErrors.push({kind:e.name}));
  const navigation=await page.goto(cfg.origin+'/');
  const details=await navigation.securityDetails();
  tlsDetails=details;
  if(!details?.issuer?.includes('PR03b isolated root '))throw new Error('unexpected_certificate_chain');
  mark(pinned?'HTTPS_served_by_run_certificate':'HTTPS_CA_chain_validated');
  await page.getByRole('heading',{name:'登录课间',exact:true}).waitFor();mark('real_https_login_form');
  await page.getByLabel('邮箱',{exact:true}).fill(cfg.email);
  if(cfg.retryCredentials) {
    await page.getByLabel('密码',{exact:true}).fill('intentionally-wrong-synthetic');
    const rejected=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/web/v1/auth/login'&&r.status()===401);
    await page.getByRole('button',{name:'继续',exact:true}).click();await rejected;
    await page.getByRole('status').filter({hasText:'凭据无效'}).waitFor();
    if(loginPosts!==1)throw new Error('credential_write_was_automatically_replayed');
  }
  await page.getByLabel('密码',{exact:true}).fill(cfg.password);
  await page.getByRole('button',{name:'继续',exact:true}).click();await page.getByRole('heading',{name:cfg.email,exact:true}).waitFor();mark('real_https_authenticated');
  if(cfg.retryCredentials) {
    if(loginPosts!==2)throw new Error('credential_retry_count');
    mark('wrong_password_then_correct_login_without_reload');
  }
  const cookie=(await context.cookies()).find(c=>c.name==='__Host-classroom_session');
  if(!cookie?.secure||!cookie.httpOnly||cookie.sameSite!=='Lax'||cookie.expires!==-1)throw new Error('cookie_policy');
  await page.screenshot({path:cfg.screenshot,fullPage:true});mark('secure_httponly_nonpersistent_cookie');
  const ticket=await page.evaluate(async()=>{
    const session=await (await fetch('/api/web/v1/auth/session',{cache:'no-store'})).json();
    const r=await fetch('/api/web/v1/ws-tickets',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf_token,'Idempotency-Key':crypto.randomUUID()},
      body:JSON.stringify({channel:'events',class_session_id:null,capture_epoch_id:null,fence_token:null})});
    if(r.status!==201)throw new Error('ticket');return r.json();
  });
  await page.evaluate(async({ticket})=>{
    await new Promise((resolve,reject)=>{
      const socket=new WebSocket(location.origin.replace('https:','wss:')+'/api/web/v1/ws/events','classroom.v1');
      window.__authTestSocket=socket;window.__authTestClosed=new Promise(done=>socket.addEventListener('close',e=>done(e.code),{once:true}));
      socket.addEventListener('error',()=>reject(new Error('TLS_WS_error')),{once:true});
      socket.addEventListener('open',()=>{socket.send(JSON.stringify({type:'authenticate',ticket}));setTimeout(()=>socket.readyState===WebSocket.OPEN?resolve(true):reject(new Error('WS_denied')),500);},{once:true});
    });
  },ticket);mark('real_WSS_single_ticket_authentication');
  if(cfg.retryCredentials) {
    const otherTab=await context.newPage();await otherTab.goto(cfg.origin+'/');
    await otherTab.getByRole('heading',{name:cfg.email,exact:true}).waitFor();
    await otherTab.getByRole('button',{name:'退出当前会话',exact:true}).click();
    await otherTab.getByRole('heading',{name:'登录课间',exact:true}).waitFor();
    if((await context.cookies()).some(c=>c.name==='__Host-classroom_session'))throw new Error('second_tab_cookie_not_removed');
    await otherTab.close();
    let sensitiveWrites=0;
    page.on('request',r=>{if(r.method()==='POST'&&['/api/web/v1/auth/reauth','/api/web/v1/auth/password/change'].includes(new URL(r.url()).pathname))sensitiveWrites++;});
    await page.getByLabel('当前密码',{exact:true}).fill(cfg.password);
    await page.getByLabel('新密码',{exact:true}).fill('unused-synthetic-new-password');
    const forbidden=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/web/v1/auth/reauth'&&r.status()===403);
    await page.getByRole('button',{name:'更改密码',exact:true}).click();await forbidden;
    await page.getByRole('heading',{name:'登录课间',exact:true}).waitFor();mark('expired_session_clears_UI_and_returns_to_login');
    if(sensitiveWrites!==1)throw new Error('forbidden_sensitive_write_was_replayed');
    mark('second_tab_logout_then_sensitive_403_restores_login_without_replay');
  } else {
    await page.getByRole('button',{name:'退出当前会话',exact:true}).click();await page.getByRole('heading',{name:'登录课间',exact:true}).waitFor();
  }
  const closeCode=await page.evaluate(()=>Promise.race([window.__authTestClosed,new Promise((_,reject)=>setTimeout(()=>reject(new Error('revocation_timeout')),3000))]));
  if(closeCode!==1008)throw new Error('WS_revocation_code');mark('real_WSS_revocation_after_logout');
  if((await context.cookies()).some(c=>c.name==='__Host-classroom_session'))throw new Error('cookie_not_cleared');
  await context.close();console.log(JSON.stringify({passed:true,steps,secret_values_exported:false,external_web_requests_allowed:false}));
}catch(error){if(page)await page.screenshot({path:cfg.screenshot.replace('.png','-failure.png'),fullPage:true}).catch(()=>{});console.log(JSON.stringify({passed:false,stage,steps,error_kind:error.name,error_code:['unexpected_certificate_chain','certificate_hostname_must_be_rejected'].includes(error.message)?error.message:null,tlsDetails,launch_diagnostics:['launch','browser_launched','TLS_invalid_hostname_rejected'].includes(stage)?error.message.slice(0,4000):null,responses,pageErrors,secret_values_exported:false}));process.exitCode=1;}
finally{if(browser)await browser.close();}
