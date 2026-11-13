// @vitest-environment jsdom
import {createElement} from 'react';
import {webcrypto} from 'node:crypto';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {render,screen,fireEvent,waitFor,cleanup} from '@testing-library/react';
import AuthWorkbench from '../src/app/AuthWorkbench';

const csrf='c'.repeat(43), anonymous='e'.repeat(43);
const user={id:crypto.randomUUID(),revision:1,created_at:'2026-11-10T00:00:00Z',updated_at:'2026-11-10T00:00:00Z',email:'synthetic@example.test',nickname:'',school:'',major:'',role:'student',status:'active',mfa_enabled:false};
const session={user,session_id:crypto.randomUUID(),device_id:crypto.randomUUID(),csrf_token:csrf};
const unauthenticated=()=>Response.json({error:{code:'UNAUTHENTICATED'}},{status:401});
const bootstrap=(value=anonymous)=>Response.json({csrf_token:value,expires_at:'2026-11-12T00:00:00Z'});
const enter=(label:string,value:string)=>fireEvent.change(screen.getByLabelText(label,{exact:true}),{target:{value}});
afterEach(()=>{cleanup();vi.unstubAllGlobals();history.replaceState(null,'','/');});

describe('authentication UI context recovery',()=>{
  it.each([false,true])('checks a sensitive 403, cookie missing=%s',async(missing)=>{
    let sessionReads=0,writes=0,probes=0;
    vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>{
      const path=String(url);
      if(path.endsWith('/auth/session'))return ++sessionReads===1||!missing?Response.json(session):unauthenticated();
      if(path.endsWith('/auth/csrf')){probes++;return bootstrap(missing?anonymous:csrf);}
      writes++;return Response.json({error:{code:'FORBIDDEN'}},{status:403});
    }));
    render(createElement(AuthWorkbench));await screen.findByRole('heading',{name:user.email});
    enter('当前密码','synthetic-password');enter('新密码','synthetic-new-password');
    fireEvent.click(screen.getByRole('button',{name:'更改密码',exact:true}));
    if(missing){await screen.findByRole('heading',{name:'登录课间'});expect(screen.queryByRole('heading',{name:user.email})).toBeNull();}
    else {await screen.findByText('当前操作未获授权，请刷新页面后重试。');expect(screen.getByRole('heading',{name:user.email})).toBeDefined();}
    expect(writes).toBe(1);expect(probes).toBe(missing?2:1);
  });
  it('offers to abandon a recovery flow whose receiver key was lost on reload',async()=>{
    const recoveryCsrf='r'.repeat(43);const abandoned:string[]=[];let logins=0;
    vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL,options?:RequestInit)=>{
      const path=String(url);
      if(path.endsWith('/auth/session'))return unauthenticated();
      // A fresh page: the HttpOnly recovery cookie survives, the memory-only key does not.
      if(path.endsWith('/auth/csrf'))return abandoned.length?bootstrap():Response.json({csrf_token:recoveryCsrf,expires_at:'2026-11-12T00:00:00Z',receiver_challenge:'b'.repeat(43)});
      if(path.endsWith('/auth/logout')){abandoned.push(new Headers(options?.headers).get('X-CSRF-Token')??'');return Response.json({});}
      if(path.endsWith('/auth/login')){logins++;return Response.json({error:{code:'FORBIDDEN'}},{status:403});}
      throw new Error('unexpected route');
    }));
    render(createElement(AuthWorkbench));await screen.findByRole('heading',{name:'有未完成的验证器更换'});
    // No login form is offered while every login would be refused, and nothing is abandoned unasked.
    expect(screen.queryByLabelText('密码')).toBeNull();expect(abandoned).toEqual([]);
    fireEvent.click(screen.getByRole('button',{name:'放弃并重新登录'}));
    await screen.findByRole('heading',{name:'登录课间'});
    expect(abandoned).toEqual([recoveryCsrf]);expect(logins).toBe(0);expect(screen.getByLabelText('密码')).toBeDefined();
  });
  it.each(['mfa','recovery'] as const)('can explicitly restart a rejected %s flow even when preauth CSRF stays valid',async(mode)=>{
    vi.stubGlobal('crypto',webcrypto);
    let logins=0,mfaWrites=0;const first=crypto.randomUUID(),second=crypto.randomUUID();
    const challenges:string[]=[];const abandoned:string[]=[];const recoveryCsrf='r'.repeat(43);
    vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL,options?:RequestInit)=>{
      const path=String(url);
      if(path.endsWith('/auth/session'))return unauthenticated();
      // Until the server abandons the flow, the HttpOnly recovery cookie keeps bootstrap in recovery scope.
      if(path.endsWith('/auth/csrf'))return bootstrap(mode==='recovery'&&mfaWrites>0&&!abandoned.length?recoveryCsrf:anonymous);
      if(path.endsWith('/auth/logout')){abandoned.push(new Headers(options?.headers).get('X-CSRF-Token')??'');return Response.json({});}
      if(path.endsWith('/auth/login')){logins++;return Response.json({state:'mfa_required',challenge_id:logins===1?first:second,user:null,device_id:null,csrf_token:null,receiver_challenge:'b'.repeat(43)});}
      if(path.endsWith('/auth/login/mfa')){
        mfaWrites++;challenges.push(JSON.parse(String(options?.body)).challenge_id);
        if(mode==='mfa')return unauthenticated();
        return Response.json({state:'recovery_required',challenge_id:null,user:null,device_id:null,csrf_token:null,recovery:{flow_id:crypto.randomUUID(),scope:'mfa_rebind',state:'pending',expires_at:'2026-11-12T00:00:00Z',csrf_token:recoveryCsrf,receiver_challenge:'b'.repeat(43)}});
      }
      if(path.endsWith('/auth/recovery/enroll'))return unauthenticated();
      throw new Error('unexpected route');
    }));
    render(createElement(AuthWorkbench));await screen.findByRole('heading',{name:'登录课间'});
    const login=async()=>{enter('邮箱',user.email);enter('密码','synthetic-password');fireEvent.click(screen.getByRole('button',{name:'继续',exact:true}));await screen.findByRole('heading',{name:'验证登录'});};
    await login();
    if(mode==='recovery')fireEvent.click(screen.getByLabelText('使用恢复码'));
    enter(mode==='recovery'?'恢复码':'验证器验证码',mode==='recovery'?'synthetic-recovery':'123456');
    fireEvent.click(screen.getByRole('button',{name:'验证',exact:true}));
    if(mode==='recovery'){
      await screen.findByRole('heading',{name:'更换验证器'});enter('再次验证当前密码','synthetic-password');
      fireEvent.click(screen.getByRole('button',{name:'生成新验证器'}));
    }
    await screen.findByText(/凭据无效或已过期/);
    expect(mfaWrites).toBe(1);expect(challenges).toEqual([first]);
    fireEvent.click(screen.getByRole('button',{name:'重新开始登录'}));
    await screen.findByRole('heading',{name:'登录课间'});expect((screen.getByLabelText('密码') as HTMLInputElement).value).toBe('');
    // Leaving recovery must end the server-side flow with the recovery-scoped CSRF; plain MFA has nothing to abandon.
    expect(abandoned).toEqual(mode==='recovery'?[recoveryCsrf]:[]);
    await login();expect(logins).toBe(2);expect(mfaWrites).toBe(1);
    expect(screen.queryByRole('heading',{name:'更换验证器'})).toBeNull();
    // A second explicit submission must use the newly issued challenge.
    enter('验证器验证码','234567');fireEvent.click(screen.getByRole('button',{name:'验证',exact:true}));
    await waitFor(()=>expect(challenges).toEqual([first,second]));
  });
});

describe('authentication UI outcomes that must not be lost or misreported',()=>{
  const reauth=()=>Response.json({reauth_token:'p'.repeat(43),expires_at:'2026-11-12T00:00:00Z'});
  it('keeps once-only recovery codes on screen when the follow-up bootstrap fails',async()=>{
    const codes=['k'.repeat(43),'m'.repeat(43)];const enrollment=crypto.randomUUID();
    vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>{
      const path=String(url);
      if(path.endsWith('/auth/session'))return Response.json(session);
      if(path.endsWith('/auth/reauth'))return reauth();
      if(path.endsWith('/auth/mfa/enroll'))return Response.json({enrollment_id:enrollment,expires_at:'2026-11-12T00:00:00Z',otpauth_uri:'otpauth://totp/synthetic'});
      if(path.endsWith('/auth/mfa/confirm'))return Response.json({codes,shown_once:true});
      // The server has committed and cleared the session; the next request never arrives.
      if(path.endsWith('/auth/csrf'))throw new TypeError('synthetic network failure');
      throw new Error('unexpected route');
    }));
    render(createElement(AuthWorkbench));await screen.findByRole('heading',{name:user.email});
    enter('当前密码','synthetic-password');fireEvent.click(screen.getByRole('button',{name:'启用验证器'}));
    await screen.findByLabelText('新验证器验证码');
    enter('当前密码','synthetic-password');enter('新验证器验证码','123456');
    fireEvent.click(screen.getByRole('button',{name:'确认新验证器'}));
    await screen.findByRole('heading',{name:'服务暂不可用'});
    for(const value of codes)expect(screen.getByText(value)).toBeDefined();
    expect(screen.getByRole('heading',{name:'恢复码只展示这一次'})).toBeDefined();
    expect(screen.getByRole('button',{name:'重新连接'})).toBeDefined();
  });
  it.each(['lost','truncated'] as const)('reports a %s write response as an unknown outcome, not a retry',async(kind)=>{
    let changes=0;
    vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>{
      const path=String(url);
      if(path.endsWith('/auth/session'))return Response.json(session);
      if(path.endsWith('/auth/reauth'))return reauth();
      if(path.endsWith('/auth/password/change')){changes++;if(kind==='lost')throw new TypeError('synthetic network failure');return new Response('{"accep',{status:200});}
      throw new Error('unexpected route');
    }));
    render(createElement(AuthWorkbench));await screen.findByRole('heading',{name:user.email});
    enter('当前密码','synthetic-password');enter('新密码','synthetic-new-password');
    fireEvent.click(screen.getByRole('button',{name:'更改密码',exact:true}));
    await screen.findByText(/提交结果暂不确定/);
    expect(screen.queryByText(/服务暂不可用/)).toBeNull();expect(changes).toBe(1);
  });
  it('keeps the email-link token after a rejected new password so it can be corrected',async()=>{
    const link='t'.repeat(43);const sent:{token:string;new_password:string}[]=[];
    history.replaceState(null,'','/reset-password#token='+link);
    vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL,options?:RequestInit)=>{
      const path=String(url);
      if(path.endsWith('/auth/session'))return unauthenticated();
      if(path.endsWith('/auth/csrf'))return bootstrap();
      if(path.endsWith('/auth/password/reset')){
        sent.push(JSON.parse(String(options?.body)));
        return sent.length===1?Response.json({error:{code:'VALIDATION_FAILED'}},{status:422}):Response.json({accepted:true,request_id:crypto.randomUUID()},{status:202});
      }
      throw new Error('unexpected route');
    }));
    render(createElement(AuthWorkbench));await screen.findByRole('heading',{name:'重置密码'});
    expect(location.hash).toBe('');
    enter('新密码','weak');fireEvent.click(screen.getByRole('button',{name:'继续',exact:true}));
    await screen.findByText(/请检查输入内容/);
    expect((screen.getByLabelText('邮件令牌') as HTMLInputElement).value).toBe(link);
    expect((screen.getByLabelText('新密码') as HTMLInputElement).value).toBe('');
    enter('新密码','synthetic-new-password');fireEvent.click(screen.getByRole('button',{name:'继续',exact:true}));
    await screen.findByRole('heading',{name:'登录课间'});
    expect(sent).toEqual([{token:link,new_password:'weak'},{token:link,new_password:'synthetic-new-password'}]);
  });
});
