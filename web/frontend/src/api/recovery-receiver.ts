import canonicalize from 'canonicalize';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import schema from '../../../../docs/web-alpha/architecture/contracts/identity-control.schema.json';
import type { ReceiverRegisterResponse,ReceiverDelivery,ReceiverFactorStartResponse,ReceiverFactorConfirmResponse,ReceiverAbandonResponse,ReceiverAuthentication } from '../generated/identity-control';
import { AuthError } from './auth-client';

const ajv=new Ajv2020({strict:false});addFormats(ajv);ajv.addSchema(schema);
const check=(name:string,value:unknown)=>{
  const validator=ajv.getSchema(schema.$id+'#/$defs/'+name);
  if(!validator?.(value))throw new AuthError('OUTPUT_INVALID');
};
const encode=(value:ArrayBuffer)=>btoa(String.fromCharCode(...new Uint8Array(value))).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
const text=(value:unknown)=>{const result=canonicalize(value);if(typeof result!=='string')throw new AuthError('VALIDATION_FAILED');return result;};
const hash=async(value:unknown)=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text(value))))).map(v=>v.toString(16).padStart(2,'0')).join('');

/** A receiver can bind, receive once, confirm or abandon. It has no final/status operation. */
export class RecoveryReceiverClient {
  private keys?:CryptoKeyPair;
  private registration?:ReceiverRegisterResponse;
  private flow?:ReceiverDelivery;
  private factorId?:string;
  private csrf='';
  private generation=0;
  private abort=new AbortController();
  constructor(private transport:typeof fetch=fetch){}
  clear(){this.generation++;this.abort.abort();this.abort=new AbortController();this.keys=undefined;this.registration=undefined;this.flow=undefined;this.factorId=undefined;this.csrf='';}
  private async post<T>(body:unknown,responseName:string):Promise<T>{
    const generation=this.generation;
    let response:Response;
    try{response=await this.transport.call(globalThis,'/identity-receiver',{method:'POST',credentials:'same-origin',cache:'no-store',redirect:'error',
      headers:{'Content-Type':'application/json','X-CSRF-Token':this.csrf},body:JSON.stringify(body),signal:this.abort.signal});}
    catch{throw new AuthError('RESULT_UNKNOWN');}
    if(generation!==this.generation)throw new AuthError('STALE_ACCOUNT');
    let value:unknown;
    try{value=await response.json();}catch{throw new AuthError('OUTPUT_INVALID');}
    if(generation!==this.generation)throw new AuthError('STALE_ACCOUNT');
    if(!response.ok){check('ControlError',value);const code=(value as {code:string}).code;throw new AuthError(code==='result_unknown'?'RESULT_UNKNOWN':code==='secret_already_issued'?'OUTPUT_INVALID':'RECOVERY_DENIED',response.status);}
    check(responseName,value);
    const csrf=response.headers.get('X-Receiver-CSRF');
    if(responseName==='ReceiverRegisterResponse'&&csrf===null)throw new AuthError('OUTPUT_INVALID');
    if(csrf!==null){if(!/^[A-Za-z0-9_-]{43}$/.test(csrf))throw new AuthError('OUTPUT_INVALID');this.csrf=csrf;}
    return value as T;
  }
  async register(preauthCsrf:string){
    this.clear();this.csrf=preauthCsrf;const generation=this.generation;
    const keys=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},false,['sign','verify']);
    const publicKey=await crypto.subtle.exportKey('jwk',keys.publicKey);
    if(generation!==this.generation)throw new AuthError('STALE_ACCOUNT');
    this.keys=keys;
    const nonce=crypto.getRandomValues(new Uint8Array(32));
    const value=await this.post<ReceiverRegisterResponse>({protocol_version:1,kind:'receiver.register.request',public_key:{kty:'EC',crv:'P-256',x:publicKey.x,y:publicKey.y},client_nonce:encode(nonce.buffer)},'ReceiverRegisterResponse');
    this.registration=value;return value;
  }
  private async signed(action:'await'|'factor_start'|'factor_confirm'|'abandon',kind:string,caseId:string,generation:number,extra:Record<string,unknown>={}){
    if(!this.keys||!this.registration)throw new AuthError('UNAUTHENTICATED');
    const localGeneration=this.generation;const keys=this.keys;
    const auth:Omit<ReceiverAuthentication,'signature'>={receiver_id:this.registration.receiver_id,case_id:caseId,flow_generation:generation,server_nonce:this.flow?.server_nonce??this.registration.server_nonce};
    const request={protocol_version:1,kind,receiver_auth:auth,...extra};
    const body={protocol_version:1,action,...auth,request_hash:await hash(request)};
    const signature=await crypto.subtle.sign({name:'ECDSA',hash:'SHA-256'},keys.privateKey,new TextEncoder().encode('classroom.identity.receiver.v1\0'+text(body)));
    if(localGeneration!==this.generation)throw new AuthError('STALE_ACCOUNT');
    if(signature.byteLength!==64)throw new AuthError('INCOMPATIBLE_CLIENT');
    return {...request,receiver_auth:{...auth,signature:encode(signature)}};
  }
  async wait(caseId:string,generation:number){
    const request=await this.signed('await','receiver.await.request',caseId,generation);
    const value=await this.post<ReceiverDelivery>(request,'ReceiverDelivery');
    if(value.case_id!==caseId||value.flow_generation!==generation)throw new AuthError('OUTPUT_INVALID');
    this.flow=value;return {pending_flow_expiry:value.pending_flow_expiry};
  }
  async start(password:string|null){
    if(!this.flow||this.factorId)throw new AuthError('UNAUTHENTICATED');
    const request=await this.signed('factor_start','receiver.factor.start.request',this.flow.case_id,this.flow.flow_generation,{cap:this.flow.cap,target_password:password});
    const value=await this.post<ReceiverFactorStartResponse>(request,'ReceiverFactorStartResponse');
    if(value.case_id!==this.flow.case_id||value.flow_generation!==this.flow.flow_generation)throw new AuthError('OUTPUT_INVALID');
    this.factorId=value.pending_factor_id;this.flow={...this.flow,server_nonce:value.server_nonce};return value;
  }
  async confirm(code:string){
    if(!this.flow||!this.factorId)throw new AuthError('UNAUTHENTICATED');
    const request=await this.signed('factor_confirm','receiver.factor.confirm.request',this.flow.case_id,this.flow.flow_generation,{cap:this.flow.cap,pending_factor_id:this.factorId,code});
    const value=await this.post<ReceiverFactorConfirmResponse>(request,'ReceiverFactorConfirmResponse');
    if(value.case_id!==this.flow.case_id||value.flow_generation!==this.flow.flow_generation||value.pending_factor_id!==this.factorId)throw new AuthError('OUTPUT_INVALID');
    this.flow={...this.flow,server_nonce:value.server_nonce};return value;
  }
  async abandon(){
    if(!this.flow)throw new AuthError('UNAUTHENTICATED');
    const request=await this.signed('abandon','receiver.abandon.request',this.flow.case_id,this.flow.flow_generation,{cap:this.flow.cap});
    try{return await this.post<ReceiverAbandonResponse>(request,'ReceiverAbandonResponse');}finally{this.clear();}
  }
}
