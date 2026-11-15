import {describe,it,expect,vi} from 'vitest';
import canonicalize from 'canonicalize';
import {RecoveryReceiverClient} from '../src/api/recovery-receiver';

const encode=(value:ArrayBuffer)=>Buffer.from(value).toString('base64url');
const sha=async(value:unknown)=>Buffer.from(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonicalize(value)!))).toString('hex');

describe('manual receiver isolation',()=>{
  it('uses exact domain-separated WebCrypto proof and never exposes a complete action',async()=>{
    const id=crypto.randomUUID(),caseId=crypto.randomUUID();let key:CryptoKey|undefined;
    const transport=vi.fn(async(_path:RequestInfo|URL,options?:RequestInit)=>{
      const request=JSON.parse(String(options?.body));
      expect(options?.credentials).toBe('same-origin');expect(options?.cache).toBe('no-store');expect(options?.redirect).toBe('error');
      if(request.kind==='receiver.register.request'){
        key=await crypto.subtle.importKey('jwk',request.public_key,{name:'ECDSA',namedCurve:'P-256'},false,['verify']);
        return Response.json({protocol_version:1,kind:'receiver.register.response',receiver_id:id,receiver_key_hash:'a'.repeat(64),server_nonce:'b'.repeat(43),registration_expiry:'2026-11-10T23:00:00Z'}, {headers:{'X-Receiver-CSRF':'c'.repeat(43)}});
      }
      const signature=Buffer.from(request.receiver_auth.signature,'base64url');delete request.receiver_auth.signature;
      expect(signature.byteLength).toBe(64);
      const body={protocol_version:1,action:'await',...request.receiver_auth,request_hash:await sha(request)};
      expect(await crypto.subtle.verify({name:'ECDSA',hash:'SHA-256'},key!,signature,new TextEncoder().encode('classroom.identity.receiver.v1\0'+canonicalize(body)))).toBe(true);
      return Response.json({protocol_version:1,kind:'receiver.delivery',case_id:caseId,flow_generation:1,cap:encode(crypto.getRandomValues(new Uint8Array(32)).buffer),pending_flow_expiry:'2026-11-10T23:00:00Z',server_nonce:'d'.repeat(43)});
    });
    const client=new RecoveryReceiverClient(transport);await client.register('e'.repeat(43));
    const value=await client.wait(caseId,1);expect(Object.keys(value)).toEqual(['pending_flow_expiry']);
    expect('complete' in client).toBe(false);expect('status' in client).toBe(false);client.clear();
  });
  it('rejects undocumented fields and does not retry malformed secret responses',async()=>{
    const transport=vi.fn(async()=>Response.json({protocol_version:1,kind:'receiver.register.response',receiver_id:crypto.randomUUID(),receiver_key_hash:'a'.repeat(64),server_nonce:'b'.repeat(43),registration_expiry:'2026-11-10T23:00:00Z',cap:'forbidden-extra'},{headers:{'X-Receiver-CSRF':'c'.repeat(43)}}));
    await expect(new RecoveryReceiverClient(transport).register('e'.repeat(43))).rejects.toMatchObject({code:'OUTPUT_INVALID'});expect(transport).toHaveBeenCalledTimes(1);
  });
  it('preserves unknown-result semantics without automatically retrying',async()=>{
    const transport=vi.fn(async()=>Response.json({protocol_version:1,kind:'identity_control.error',code:'result_unknown',retryable:false,correlation_id:crypto.randomUUID()},{status:503}));
    await expect(new RecoveryReceiverClient(transport).register('e'.repeat(43))).rejects.toMatchObject({code:'RESULT_UNKNOWN'});expect(transport).toHaveBeenCalledTimes(1);
  });
});
