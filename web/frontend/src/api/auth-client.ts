import type { components } from '../generated/openapi';
import canonicalize from 'canonicalize';

type S = components['schemas'];
type Login = S['LoginResult'];
type Recovery = S['AutomaticRecoveryContext'];
export class AuthError extends Error {
  constructor(readonly code: string, readonly status = 0) { super(code); this.name = 'AuthError'; }
}
const object = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(v);
const token = (v: unknown): v is string => typeof v === 'string' && /^[A-Za-z0-9_-]{43}$/.test(v);
const exact = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).length === keys.length && keys.every(k => k in v);
const user = (v: unknown) => object(v) && exact(v,['id','revision','created_at','updated_at','email','nickname','school','major','role','status','mfa_enabled'])
  && uuid(v.id) && Number.isInteger(v.revision) && Number(v.revision)>=1 && ['created_at','updated_at'].every(k=>typeof v[k]==='string'&&Number.isFinite(Date.parse(v[k] as string)))
  && ['email','nickname','school','major'].every(k=>typeof v[k]==='string') && ['student','admin'].includes(String(v.role)) && v.status==='active' && typeof v.mfa_enabled==='boolean';

export function parseLogin(value: unknown): Login {
  if (!object(value)) throw new AuthError('INCOMPATIBLE_CLIENT');
  const base = ['state', 'challenge_id', 'user', 'device_id', 'csrf_token'];
  if (value.state === 'authenticated' && exact(value, base) && value.challenge_id === null && uuid(value.device_id) && token(value.csrf_token)
    && user(value.user)) return value as Login;
  if (value.state === 'mfa_required' && exact(value, [...base, 'receiver_challenge']) && uuid(value.challenge_id) && token(value.receiver_challenge)
    && value.user === null && value.device_id === null && value.csrf_token === null) return value as Login;
  if (value.state === 'recovery_required' && exact(value, [...base, 'recovery']) && value.challenge_id === null && value.user === null && value.device_id === null
    && value.csrf_token === null && object(value.recovery) && exact(value.recovery, ['flow_id','scope','state','expires_at','csrf_token','receiver_challenge'])
    && uuid(value.recovery.flow_id) && value.recovery.scope === 'mfa_rebind' && value.recovery.state === 'pending' && token(value.recovery.csrf_token)
    && token(value.recovery.receiver_challenge) && typeof value.recovery.expires_at === 'string' && Number.isFinite(Date.parse(value.recovery.expires_at))) return value as Login;
  throw new AuthError('INCOMPATIBLE_CLIENT');
}

const bytes = (s: string) => new TextEncoder().encode(s);
const encode = (raw: ArrayBuffer) => btoa(String.fromCharCode(...new Uint8Array(raw))).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
function canonical(value: unknown): string {
  const result=canonicalize(value);
  if (typeof result!=='string') throw new AuthError('VALIDATION_FAILED');
  return result;
}
async function sha(value: string) { return crypto.subtle.digest('SHA-256', bytes(value)); }

/** Cookies remain HttpOnly. CSRF, pending seeds, codes and private keys are memory-only. */
export class AuthClient {
  csrf = '';
  /** The server is still in automatic-recovery scope, but this page no longer holds the
   * memory-only receiver key (for example after a reload), so the flow cannot continue here. */
  orphanedRecovery = false;
  private generation = 0;
  private receiver?: CryptoKeyPair;
  private recovery?: Recovery;
  constructor(private transport: typeof fetch = fetch) {}
  clear() { this.generation++; this.csrf = ''; this.receiver = undefined; this.recovery = undefined; this.orphanedRecovery = false; }
  private async checkRejectedContext(generation: number, sentCsrf: string) {
    // Probe the cookie/CSRF context only; matching CSRF does not establish that
    // an MFA challenge is still valid. Never replay the rejected write.
    try {
      const current=await this.request<S['CsrfBootstrap']>('/auth/csrf');
      if(generation!==this.generation || this.csrf!==sentCsrf)throw new AuthError('STALE_ACCOUNT');
      if(!token(current.csrf_token))throw new AuthError('OUTPUT_INVALID');
      if(current.csrf_token!==sentCsrf) {
        this.clear(); this.csrf=current.csrf_token;
        throw new AuthError('AUTH_CONTEXT_EXPIRED',401);
      }
      if(this.recovery && current.receiver_challenge)this.recovery={...this.recovery,receiver_challenge:current.receiver_challenge};
    } catch(error) {
      if(error instanceof AuthError && ['STALE_ACCOUNT','AUTH_CONTEXT_EXPIRED'].includes(error.code))throw error;
      if(generation!==this.generation || this.csrf!==sentCsrf)throw new AuthError('STALE_ACCOUNT');
      this.clear(); throw new AuthError('AUTH_CONTEXT_EXPIRED',401);
    }
  }
  async request<T>(path: string, body?: unknown, options: { key?: string; reauth?: string } = {}): Promise<T> {
    const generation = this.generation;
    const sentCsrf = this.csrf;
    let response: Response;
    try {
      response = await this.transport.call(globalThis, '/api/web/v1' + path, { method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-CSRF-Token': sentCsrf, 'Idempotency-Key': options.key ?? crypto.randomUUID(), ...(options.reauth ? {'X-Reauth-Token': options.reauth} : {}) },
        body: body === undefined ? undefined : JSON.stringify(body) });
    } catch { throw new AuthError('STORAGE_UNAVAILABLE'); }
    if (generation !== this.generation) throw new AuthError('STALE_ACCOUNT');
    let value: unknown;
    try { value = await response.json(); } catch { throw new AuthError('OUTPUT_INVALID', response.status); }
    if (generation !== this.generation) throw new AuthError('STALE_ACCOUNT');
    if (!response.ok) {
      const code = object(value) && object(value.error) && typeof value.error.code === 'string' ? value.error.code : 'STORAGE_UNAVAILABLE';
      if (body!==undefined && [401,403].includes(response.status) && path!=='/auth/csrf') {
        await this.checkRejectedContext(generation,sentCsrf);
      } else if (response.status === 401) {
          this.clear();
          if(path!=='/auth/session')throw new AuthError('AUTH_CONTEXT_EXPIRED',401);
      }
      throw new AuthError(code, response.status);
    }
    return value as T;
  }
  async bootstrap() {
    try {
      const session = await this.request<S['SessionContext']>('/auth/session');
      if (!object(session) || !exact(session,['user','device_id','session_id','csrf_token']) || !user(session.user) || !uuid(session.device_id) || !uuid(session.session_id) || !token(session.csrf_token)) throw new AuthError('OUTPUT_INVALID');
      this.csrf = session.csrf_token; return session;
    } catch (e) { if (!(e instanceof AuthError) || e.status !== 401) throw e; }
    await this.refreshCsrf(); return null;
  }
  async refreshCsrf() {
    const value = await this.request<S['CsrfBootstrap']>('/auth/csrf');
    if (!token(value.csrf_token)) throw new AuthError('OUTPUT_INVALID');
    this.csrf = value.csrf_token;
    // Only a recovery-scoped context carries a receiver challenge.
    this.orphanedRecovery = !this.recovery && !!value.receiver_challenge;
    if (this.recovery && value.receiver_challenge) this.recovery = {...this.recovery, receiver_challenge: value.receiver_challenge};
    return value;
  }
  private accept(result: Login) {
    if (result.state === 'authenticated') this.csrf = result.csrf_token;
    if (result.state === 'recovery_required') { this.recovery = result.recovery; this.csrf = result.recovery.csrf_token; }
    return result;
  }
  async login(body: S['LoginRequest']) { return this.accept(parseLogin(await this.request('/auth/login', body))); }
  async totp(challenge_id: string, code: string) { return this.accept(parseLogin(await this.request('/auth/login/mfa', {factor_type: 'totp', challenge_id, code}))); }
  private async proof(body: Record<string, unknown>, path: string, key: string, kind: string, context: string, challenge: string, jwk: S['ReceiverPublicKey']) {
    if (!this.receiver || !token(challenge)) throw new AuthError('UNAUTHENTICATED');
    const thumb = encode(await sha(canonical(jwk)));
    const bodyHash = [...new Uint8Array(await sha(canonical(body)))].map(x => x.toString(16).padStart(2,'0')).join('');
    const message = ['ClassroomOS.Web.ReceiverPoP.v1','POST','/api/web/v1'+path,key,kind,context,thumb,challenge,bodyHash].join('\n');
    const signature = await crypto.subtle.sign({name:'ECDSA',hash:'SHA-256'}, this.receiver.privateKey, bytes(message));
    if (signature.byteLength !== 64) throw new AuthError('INCOMPATIBLE_CLIENT');
    return {challenge, signature:encode(signature)};
  }
  private async publicKey(): Promise<S['ReceiverPublicKey']> {
    if (!this.receiver) throw new AuthError('UNAUTHENTICATED');
    const jwk = await crypto.subtle.exportKey('jwk', this.receiver.publicKey);
    if (!jwk.x || !jwk.y) throw new AuthError('INCOMPATIBLE_CLIENT');
    return {kty:'EC', crv:'P-256', x:jwk.x, y:jwk.y};
  }
  async recover(challenge: Extract<Login,{state:'mfa_required'}>, code: string) {
    const generation=this.generation;
    const receiver = await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'}, false, ['sign','verify']);
    if(generation!==this.generation)throw new AuthError('STALE_ACCOUNT');
    this.receiver=receiver;
    const jwk = await this.publicKey(), key = crypto.randomUUID(), path = '/auth/login/mfa';
    const body = {factor_type:'recovery_code', challenge_id:challenge.challenge_id, code, receiver_public_key:jwk};
    const receiver_proof = await this.proof(body,path,key,'login_challenge',challenge.challenge_id,challenge.receiver_challenge,jwk);
    if(generation!==this.generation)throw new AuthError('STALE_ACCOUNT');
    return this.accept(parseLogin(await this.request(path,{...body,receiver_proof},{key})));
  }
  async recoveryAction(action:'enroll'|'confirm', password:string, enrollment_id?:string, code?:string) {
    if (!this.recovery) throw new AuthError('UNAUTHENTICATED');
    const generation=this.generation;
    const key = crypto.randomUUID(), path = '/auth/recovery/'+action;
    const body: Record<string,unknown> = {flow_id:this.recovery.flow_id,password,...(action==='confirm'?{enrollment_id,code}:{})};
    const receiver_proof = await this.proof(body,path,key,'automatic_recovery',this.recovery.flow_id,this.recovery.receiver_challenge,await this.publicKey());
    if(generation!==this.generation)throw new AuthError('STALE_ACCOUNT');
    const value = await this.request<S['RecoveryReceiverEnrollment']|S['RecoveryCompleted']>(path,{...body,receiver_proof},{key});
    if ('receiver_challenge' in value) this.recovery = {...this.recovery,receiver_challenge:value.receiver_challenge};
    else this.clear();
    return value;
  }
  async change(purpose:S['ReauthOperation']['purpose'], target:S['ReauthOperation']['target'], body:Record<string,unknown>, password:string, mfa_code?:string, path?:string) {
    const key=crypto.randomUUID();
    const operation={purpose,target,candidate_parameters:{idempotency_key:key,body}};
    const proof=await this.request<S['ReauthResult']>('/auth/reauth',{password,...(mfa_code?{mfa_code}:{}),operation});
    if (!path) throw new AuthError('VALIDATION_FAILED');
    // No automatic retries: secret responses and unknown commits are not replayable.
    return this.request<unknown>(path,body,{key,reauth:proof.reauth_token});
  }
  async logout() { try { await this.request('/auth/logout',{}); } finally { this.clear(); } }
}
