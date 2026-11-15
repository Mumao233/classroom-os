import {describe,it,expect,vi} from 'vitest';
import {AuthClient,AuthError,parseLogin} from '../src/api/auth-client';

const user={id:crypto.randomUUID(),revision:1,created_at:'2026-11-10T00:00:00Z',updated_at:'2026-11-10T00:00:00Z',email:'synthetic@example.test',nickname:'',school:'',major:'',role:'student',status:'active',mfa_enabled:false};
const authenticated={state:'authenticated',challenge_id:null,user,device_id:crypto.randomUUID(),csrf_token:'a'.repeat(43)};
describe('formal authentication consumer',()=>{
  it('accepts only the three disjoint complete login states',()=>{
    expect(parseLogin(authenticated).state).toBe('authenticated');
    expect(parseLogin({state:'mfa_required',challenge_id:crypto.randomUUID(),user:null,device_id:null,csrf_token:null,receiver_challenge:'b'.repeat(43)}).state).toBe('mfa_required');
    const recovery={flow_id:crypto.randomUUID(),scope:'mfa_rebind',state:'pending',expires_at:'2026-11-10T00:10:00Z',csrf_token:'a'.repeat(43),receiver_challenge:'b'.repeat(43)};
    expect(parseLogin({state:'recovery_required',challenge_id:null,user:null,device_id:null,csrf_token:null,recovery}).state).toBe('recovery_required');
    for(const bad of [{...authenticated,state:'future_state'},{...authenticated,user:{id:user.id,role:'student',status:'active'}},{...authenticated,extra:true},
      {state:'mfa_required',challenge_id:crypto.randomUUID(),user,device_id:null,csrf_token:null,receiver_challenge:'b'.repeat(43)}])expect(()=>parseLogin(bad)).toThrow(AuthError);
  });
  it('never accepts a delayed authenticated response after local account teardown',async()=>{
    let resolve!:(response:Response)=>void;
    const fetcher=vi.fn(()=>new Promise<Response>(done=>{resolve=done;})) as unknown as typeof fetch;
    const client=new AuthClient(fetcher);client.csrf='a'.repeat(43);
    const pending=client.login({email:user.email,password:'synthetic-password',remember_me:false,device_name:'test'});
    client.clear();resolve(Response.json(authenticated));
    await expect(pending).rejects.toMatchObject({code:'STALE_ACCOUNT'});expect(client.csrf).toBe('');
  });
  it('does not automatically retry a secret operation or an unknown commit',async()=>{
    const fetcher=vi.fn(async()=>Response.json({error:{code:'RESULT_UNKNOWN'}},{status:503}));
    const client=new AuthClient(fetcher);
    await expect(client.request('/auth/mfa/recovery-codes',{})).rejects.toMatchObject({code:'RESULT_UNKNOWN'});
    expect(fetcher).toHaveBeenCalledTimes(1);
    const options=fetcher.mock.calls[0]?.[1] as RequestInit|undefined;
    expect(options?.credentials).toBe('same-origin');expect(options?.cache).toBe('no-store');expect(options?.redirect).toBe('error');
  });
  it('only a 401 session response leads to anonymous protocol bootstrap',async()=>{
    const fetcher=vi.fn(async(path:RequestInfo|URL)=>String(path).endsWith('/auth/session')?Response.json({error:{code:'UNAUTHENTICATED'}},{status:401}):Response.json({csrf_token:'a'.repeat(43),expires_at:'2026-11-10T00:10:00Z'}));
    const client=new AuthClient(fetcher);expect(await client.bootstrap()).toBe(null);expect(client.csrf).toBe('a'.repeat(43));
    const unavailable=new AuthClient(vi.fn(async()=>new Response('',{status:503})));
    await expect(unavailable.bootstrap()).rejects.toBeInstanceOf(AuthError);
  });
  it('calls the browser fetch with the global receiver',async()=>{
    const client=new AuthClient(function(this:unknown){expect(this).toBe(globalThis);return Promise.resolve(Response.json({accepted:true}));} as typeof fetch);
    await expect(client.request('/auth/logout',{})).resolves.toEqual({accepted:true});
  });
});

describe('credential rejection keeps only a live authentication context',()=>{
  const csrf='c'.repeat(43), nextCsrf='e'.repeat(43);
  const invalid=()=>Response.json({error:{code:'UNAUTHENTICATED'}},{status:401});
  const bootstrap=(value=csrf,receiver_challenge?:string)=>Response.json({csrf_token:value,expires_at:'2026-11-10T23:59:00Z',...(receiver_challenge?{receiver_challenge}:{})});
  it.each(['password','totp','reauth'] as const)('allows explicit corrected %s input after one rejection',async(kind)=>{
    let posts=0;const seen:string[]=[];
    const fetcher=vi.fn(async(path:RequestInfo|URL,options?:RequestInit)=>{
      if(String(path).endsWith('/auth/csrf'))return bootstrap();
      seen.push(new Headers(options?.headers).get('X-CSRF-Token')??'');posts++;
      if(seen.at(-1)!==csrf)return Response.json({error:{code:'FORBIDDEN'}},{status:403});
      return posts===1?invalid():Response.json(kind==='reauth'?{reauth_token:'p'.repeat(43)}:authenticated);
    });
    const client=new AuthClient(fetcher);client.csrf=csrf;const challenge=crypto.randomUUID();
    const submit=(good:boolean)=>kind==='password'?client.login({email:user.email,password:good?'correct-password':'wrong-password',remember_me:false,device_name:'test'}):kind==='totp'?client.totp(challenge,good?'123457':'123456'):client.request('/auth/reauth',{password:'correct-password',mfa_code:good?'123457':'123456'});
    await expect(submit(false)).rejects.toMatchObject({code:'UNAUTHENTICATED',status:401});
    expect(posts).toBe(1);expect(client.csrf).toBe(csrf);
    await expect(submit(true)).resolves.toBeDefined();
    expect(posts).toBe(2);expect(seen).toEqual([csrf,csrf]);
    expect(fetcher.mock.calls.filter(([path])=>String(path).endsWith('/auth/csrf'))).toHaveLength(1);
  });
  it('preserves the bound recovery key and flow after an incorrect current password',async()=>{
    const flow=crypto.randomUUID(),challenge=crypto.randomUUID();let enrolls=0;
    const fetcher=vi.fn(async(path:RequestInfo|URL,options?:RequestInit)=>{
      const url=String(path);
      if(url.endsWith('/auth/csrf'))return bootstrap(csrf,'b'.repeat(43));
      if(url.endsWith('/auth/login/mfa'))return Response.json({state:'recovery_required',challenge_id:null,user:null,device_id:null,csrf_token:null,recovery:{flow_id:flow,scope:'mfa_rebind',state:'pending',expires_at:'2026-11-10T23:59:00Z',csrf_token:csrf,receiver_challenge:'b'.repeat(43)}});
      const body=JSON.parse(String(options?.body));expect(body.flow_id).toBe(flow);expect(body.receiver_proof.signature).toHaveLength(86);
      expect(new Headers(options?.headers).get('X-CSRF-Token')).toBe(csrf);enrolls++;
      return enrolls===1?invalid():Response.json({enrollment_id:crypto.randomUUID(),receiver_challenge:'d'.repeat(43),otpauth_uri:'synthetic-not-a-seed'});
    });
    const client=new AuthClient(fetcher);client.csrf=csrf;
    await client.recover({state:'mfa_required',challenge_id:challenge,user:null,device_id:null,csrf_token:null,receiver_challenge:'b'.repeat(43)},'r'.repeat(43));
    await expect(client.recoveryAction('enroll','wrong-password')).rejects.toMatchObject({code:'UNAUTHENTICATED'});
    expect(enrolls).toBe(1);await expect(client.recoveryAction('enroll','correct-password')).resolves.toHaveProperty('enrollment_id');expect(enrolls).toBe(2);
  });
  it.each(['/auth/login','/auth/login/mfa','/auth/reauth','/auth/recovery/enroll'])('invalidates an expired context on %s without replaying the write',async(path)=>{
    const fetcher=vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/auth/csrf')?bootstrap(nextCsrf):invalid());
    const client=new AuthClient(fetcher);client.csrf=csrf;
    await expect(client.request(path,{password:'synthetic'})).rejects.toMatchObject({code:'AUTH_CONTEXT_EXPIRED',status:401});
    expect(client.csrf).toBe(nextCsrf);expect(fetcher).toHaveBeenCalledTimes(2);
    await expect(client.recoveryAction('enroll','synthetic')).rejects.toMatchObject({code:'UNAUTHENTICATED'});
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('clears a genuinely invalid session read',async()=>{
    const client=new AuthClient(vi.fn(async()=>invalid()));client.csrf=csrf;
    await expect(client.request('/me/sessions')).rejects.toMatchObject({code:'AUTH_CONTEXT_EXPIRED'});expect(client.csrf).toBe('');
  });
  it.each(['/auth/reauth','/auth/recovery/enroll','/auth/login/mfa'])('probes a missing-cookie 403 on %s without replaying it',async(path)=>{
    const fetcher=vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/auth/csrf')?bootstrap(nextCsrf):Response.json({error:{code:'FORBIDDEN'}},{status:403}));
    const client=new AuthClient(fetcher);client.csrf=csrf;
    await expect(client.request(path,{})).rejects.toMatchObject({code:'AUTH_CONTEXT_EXPIRED'});
    expect(client.csrf).toBe(nextCsrf);expect(fetcher.mock.calls.map(([url])=>String(url))).toEqual(['/api/web/v1'+path,'/api/web/v1/auth/csrf']);
  });
  it('keeps a live context when a write is forbidden for another reason',async()=>{
    const fetcher=vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/auth/csrf')?bootstrap():Response.json({error:{code:'FORBIDDEN'}},{status:403}));
    const client=new AuthClient(fetcher);client.csrf=csrf;
    await expect(client.request('/auth/reauth',{})).rejects.toMatchObject({code:'FORBIDDEN',status:403});
    expect(client.csrf).toBe(csrf);expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('fails closed when a forbidden write cannot be checked',async()=>{
    const fetcher=vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/auth/csrf')?Response.json({error:{code:'SERVICE_DISABLED'}},{status:503}):Response.json({error:{code:'FORBIDDEN'}},{status:403}));
    const client=new AuthClient(fetcher);client.csrf=csrf;
    await expect(client.request('/auth/reauth',{})).rejects.toMatchObject({code:'AUTH_CONTEXT_EXPIRED'});expect(client.csrf).toBe('');
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('does not let a late 403 probe clear a newer scope',async()=>{
    let resolve!:(r:Response)=>void;
    const fetcher=vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('/auth/csrf')?new Promise<Response>(r=>{resolve=r;}):Response.json({error:{code:'FORBIDDEN'}},{status:403}));
    const client=new AuthClient(fetcher);client.csrf=csrf;const pending=client.request('/auth/reauth',{});
    await vi.waitFor(()=>expect(resolve).toBeTypeOf('function'));client.clear();client.csrf=nextCsrf;resolve(bootstrap());
    await expect(pending).rejects.toMatchObject({code:'STALE_ACCOUNT'});expect(client.csrf).toBe(nextCsrf);
  });
  it('does not tear down a newer context when the read-only probe completes late',async()=>{
    let resolve!:(r:Response)=>void;
    const fetcher=vi.fn(async(path:RequestInfo|URL)=>String(path).endsWith('/auth/csrf')?new Promise<Response>(r=>{resolve=r;}):invalid());
    const client=new AuthClient(fetcher);client.csrf=csrf;
    const pending=client.login({email:user.email,password:'wrong-password',remember_me:false,device_name:'test'});
    await vi.waitFor(()=>expect(resolve).toBeTypeOf('function'));
    client.clear();client.csrf=nextCsrf;resolve(bootstrap());
    await expect(pending).rejects.toMatchObject({code:'STALE_ACCOUNT'});expect(client.csrf).toBe(nextCsrf);
  });
});
