"""Synthetic pinned D0 and proof verifier; no real enrollment/recovery entry."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import hashlib,json,secrets,threading,time
from pathlib import Path
import common as b
import crypto
CURRENT_FIXTURE=None
class Gate:
    def __init__(self):self.cv=threading.Condition();self.readers=0;self.writer=False;self.waiters=0
    @contextmanager
    def hold(self,exclusive=False):
        with self.cv:
            if exclusive:self.waiters+=1
            try:
                end=time.monotonic()+5
                while self.writer or (self.readers if exclusive else self.waiters):
                    remain=end-time.monotonic();b.require(remain>0,'admission_timeout');self.cv.wait(remain)
                if exclusive:self.writer=True
                else:self.readers+=1
            finally:
                if exclusive:self.waiters-=1
        try:yield
        finally:
            with self.cv:
                if exclusive:self.writer=False
                else:self.readers-=1
                self.cv.notify_all()
class D0:
    def __init__(self,root,approval):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        self.root=Path(root);self.root.mkdir()
        import windows
        windows.protect_d0(self.root)
        self.gate=Gate();self.signer=Ed25519PrivateKey.generate();self.change=Ed25519PrivateKey.generate()
        self.recover=Ed25519PrivateKey.generate();self.pin=self.signer.public_key();self.change_pin=self.change.public_key();self.approval=approval
        from cryptography.hazmat.primitives import serialization
        def public(key):return crypto.b64(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw))
        self.manifest={'deployment':'s3-synthetic-deployment','authority':'s3-synthetic-person','os_subject':approval['os_subject'],'version':1,'enabled':True,'binding':approval['binding'],'recover_public':public(self.recover)}
        self.highwater={'version':1,'hash':self.digest(self.manifest),'phase':'ready','transition':None,'nonces':[]};self.published=dict(self.manifest)
        b.write(self.root/'installed-root.json',{'deployment':self.manifest['deployment'],'root_public':public(self.signer),'change_public':public(self.change),'os_subject':approval['os_subject'],'approval_reference':approval['binding']})
        b.write(self.root/'ledger.json',self.highwater);b.write(self.root/'installed.json',self.published)
    @staticmethod
    def digest(v):return hashlib.sha256(crypto.jcs(v)).hexdigest()
    @classmethod
    def reopen(cls,previous):
        # Synthetic restart fixture: pins/keys are supplied by the independent
        # installer, not recovered from the writable PG mirror or new manifest.
        from cryptography.hazmat.primitives import serialization
        value=cls.__new__(cls)
        for name in ('root','signer','change','recover','pin','change_pin','approval'):setattr(value,name,getattr(previous,name))
        installed=b.strict(b.read(value.root/'installed-root.json'))
        public=lambda key:crypto.b64(key.public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw))
        b.require(installed['root_public']==public(value.pin) and installed['change_public']==public(value.change_pin) and installed['os_subject']==value.approval['os_subject'],'D0_restart_external_pins')
        value.manifest=b.strict(b.read(value.root/'installed.json'));value.published=dict(value.manifest)
        value.highwater=b.strict(b.read(value.root/'ledger.json'));value.gate=Gate()
        b.require(value.highwater['version']>=value.manifest['version'] and value.highwater['phase'] in ('ready','reserved'),'D0_restart_no_reset')
        return value
    def current(self,nonce):
        b.require(self.pin is not None and self.highwater is not None,'D0_missing_root_or_ledger')
        body={'deployment':self.manifest['deployment'],'nonce':nonce,'version':self.highwater['version'],'hash':self.highwater['hash'],'phase':self.highwater['phase'],'enabled':self.manifest['enabled']}
        return body,self.signer.sign(crypto.signed('classroom.identity.current.v1',body))
    def ready(self,pg,nonce=None,response=None):
        nonce=nonce or crypto.b64(secrets.token_bytes(32));body,sig=response or self.current(nonce)
        b.require(self.pin is not None,'D0_missing_root')
        try:self.pin.verify(sig,crypto.signed('classroom.identity.current.v1',body))
        except Exception:raise b.Stop('D0_current_signature') from None
        b.require(body.get('enabled') is True and self.published.get('enabled') is True,'D0_revoked')
        b.require(body['deployment']==self.published['deployment'],'D0_deployment')
        b.require(body['nonce']==nonce and body['phase']=='ready' and (body['version'],body['hash'])==pg== (self.published['version'],self.digest(self.published)),'D0_mismatch_or_stale')
        return True
    def change_body(self,new):
        return {'deployment':self.manifest['deployment'],'authority':self.manifest['authority'],'action':'revoke' if not new['enabled'] else 'key_rotate',
            'old_version':self.highwater['version'],'old_hash':self.highwater['hash'],'new_version':new['version'],'new_hash':self.digest(new),
            'old_binding':self.manifest['binding'],'new_binding':new['binding'],'approval_nonce':crypto.b64(secrets.token_bytes(32)),
            'expires':(datetime.now(timezone.utc)+timedelta(seconds=120)).isoformat(),'maintainer':'s3-independent-maintainer'}
    def reserve(self,body,signature,new,pop=None):
        try:self.change_pin.verify(signature,crypto.signed('classroom.identity.change.v1',body))
        except Exception:raise b.Stop('D0_change_signature') from None
        b.require(body['deployment']==self.manifest['deployment'] and body['authority']==self.manifest['authority'] and body['maintainer']=='s3-independent-maintainer'
            and body['old_version']==self.highwater['version'] and body['old_hash']==self.highwater['hash']
            and body['new_version']==new['version']>body['old_version'] and body['new_hash']==self.digest(new)
            and body['old_binding']==self.manifest['binding'] and body['new_binding']==new['binding']
            and datetime.now(timezone.utc)<datetime.fromisoformat(body['expires']) and body['approval_nonce'] not in self.highwater['nonces'],'D0_change_binding')
        b.require(body['action']==('revoke' if not new['enabled'] else 'key_rotate'),'D0_change_action')
        if new['recover_public']!=self.manifest['recover_public']:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            material={'deployment_id':body['deployment'],'key_purpose':'recover','key_id':'synthetic-new-key','new_binding_hash':body['new_binding'],'approval_nonce':body['approval_nonce']}
            try:Ed25519PublicKey.from_public_bytes(crypto.unb64(new['recover_public'],32)).verify(pop,crypto.signed('classroom.identity.key-pop.v1',material))
            except Exception:raise b.Stop('D0_new_key_PoP') from None
        self.highwater.update(version=new['version'],hash=self.digest(new),phase='reserved',transition=body['approval_nonce'])
        self.highwater['nonces'].append(body['approval_nonce']);self._persist('ledger.json',self.highwater)
    def _persist(self,name,value):
        import os
        p=self.root/(name+'.new')
        with p.open('x',encoding='utf-8') as f:json.dump(value,f);f.flush();os.fsync(f.fileno())
        p.replace(self.root/name)
    def publish(self,new):
        b.require((new['version'],self.digest(new))==(self.highwater['version'],self.highwater['hash']),'D0_forward_only')
        self.manifest=dict(new);self.published=dict(new);self._persist('installed.json',new)
        self.highwater['phase']='ready';self._persist('ledger.json',self.highwater)
def proof_times(verified,challenge_expiry,qualification_expiry,pending_expiry=None):
    b.require(verified<challenge_expiry,'challenge_expired')
    b.require(verified<qualification_expiry and (pending_expiry is None or verified<pending_expiry),'qualification_or_pending_expired')
    return min(verified+timedelta(seconds=120),qualification_expiry,*([pending_expiry] if pending_expiry else []))
class Verifier:
    def __init__(self,public,subject):self.public=public;self.subject=subject;self.used=set()
    def verify(self,challenge,signature,peer,expected,db_now):
        b.require(peer==self.subject and challenge['plan']==expected,'proof_peer_or_plan')
        try:self.public.verify(signature,crypto.signed('classroom.identity.recovery-proof.v1',challenge))
        except Exception:raise b.Stop('proof_signature') from None
        b.require(challenge['nonce'] not in self.used,'proof_challenge_used')
        verified=db_now();deadline=proof_times(verified,datetime.fromisoformat(challenge['expires']),datetime.fromisoformat(challenge['qualification']))
        self.used.add(challenge['nonce']);return verified,deadline
def experiments(lease,check,private_root):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import windows
    approval={'os_subject':windows.sid_string(),'binding':'independent-synthetic-install-approval'}
    d0=D0(private_root,approval)
    # Trusted synthetic installer initializes PG's mirror, never its trust pin.
    with lease.connect('bootstrap') as c:
        c.execute('BEGIN READ WRITE');c.execute('UPDATE identity_control.lab_guard SET manifest_hash=%s WHERE id=1 AND version=1 AND manifest_hash=repeat(\'0\',64)',(d0.highwater['hash'],));c.execute('COMMIT')
    def pg_state():
        with lease.connect('bootstrap') as c:return c.execute('SELECT version,manifest_hash FROM identity_control.lab_guard WHERE id=1').fetchone()
    old=pg_state();check('D01_independent_install_control',d0.ready(old))
    def reject(label,body):
        try:body()
        except b.Stop:check(label,True)
        else:check(label,False)
    old_nonce=crypto.b64(secrets.token_bytes(32));response=d0.current(old_nonce)
    reject('D01_old_signed_current_nonce',lambda:d0.ready(old,response=response))
    from cryptography.hazmat.primitives import serialization
    new_operator=Ed25519PrivateKey.generate();new_public=crypto.b64(new_operator.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw))
    new=dict(d0.manifest,version=2,binding=hashlib.sha256(new_public.encode()).hexdigest(),recover_public=new_public)
    body=d0.change_body(new);signature=d0.change.sign(crypto.signed('classroom.identity.change.v1',body))
    pop=new_operator.sign(crypto.signed('classroom.identity.key-pop.v1',{'deployment_id':body['deployment'],'key_purpose':'recover','key_id':'synthetic-new-key','new_binding_hash':body['new_binding'],'approval_nonce':body['approval_nonce']}))
    reject('D01_new_key_PoP_not_approval',lambda:d0.reserve(body,d0.recover.sign(crypto.signed('classroom.identity.change.v1',body)),new))
    for field in body:
        changed=dict(body);changed[field]=body[field]+1 if type(body[field]) is int else 'tampered'
        reject('D01_change_tamper:'+field,lambda changed=changed:d0.reserve(changed,signature,new))
    with d0.gate.hold(exclusive=True):
        d0.reserve(body,signature,new,pop=pop)
        reject('D01_reserved_with_old_PG_closed',lambda:d0.ready(old))
        with lease.connect('maintenance') as c:
            c.execute('BEGIN READ WRITE');c.execute('SELECT identity_api.lab3_registration(%s,%s,%s,%s,%s,%s,%s)',(s_ticket('maintenance',5),old[0],old[1],new['version'],d0.highwater['hash'],body['approval_nonce'],new['enabled']));c.execute('COMMIT')
        reject('D01_PG_new_manifest_old_closed',lambda:d0.ready(pg_state()))
        d0.publish(new);d0.recover=new_operator;check('D01_same_transition_forward_ready',d0.ready(pg_state()))
    reject('D01_old_PG_and_manifest_not_highwater',lambda:d0.ready(old))
    reject('D01_approval_nonce_cannot_repeat',lambda:d0.reserve(body,signature,new))
    pin=d0.pin;d0.pin=None;reject('D01_root_missing_safe_closed',lambda:d0.ready(pg_state()));d0.pin=pin
    # Actual mature signature validation and all plan fields, no string roles.
    now=datetime.now(timezone.utc);key=Ed25519PrivateKey.generate();verify=Verifier(key.public_key(),'kernel-peer')
    plan={k:v for k,v in (('deployment','synthetic'),('executor','authority-5'),('case',1),('op',1),('target',3),('epoch',1),('action','complete'),('flow',1),('receiver','receiver-a'),('evidence','verified-independent'),('confirmation','new-factor-confirmed'))}
    challenge={'plan':plan,'nonce':crypto.b64(secrets.token_bytes(32)),'expires':(now+timedelta(seconds=300)).isoformat(),'qualification':(now+timedelta(seconds=600)).isoformat()}
    sig=key.sign(crypto.signed('classroom.identity.recovery-proof.v1',challenge))
    def db_now():
        with lease.connect('bootstrap') as c:return c.execute('SELECT clock_timestamp()').fetchone()[0]
    for field in plan:
        altered=dict(plan);altered[field]='changed'
        reject('D01_proof_scope:'+field,lambda altered=altered:verify.verify(challenge,sig,'kernel-peer',altered,db_now))
    reject('D01_proof_wrong_OS_peer',lambda:verify.verify(challenge,sig,'self-reported-peer',plan,db_now))
    verified,deadline=verify.verify(challenge,sig,'kernel-peer',plan,db_now)
    check('D02_proof_actual_DB_verified_at',0<(deadline-verified).total_seconds()<=120)
    reject('D02_proof_challenge_replay',lambda:verify.verify(challenge,sig,'kernel-peer',plan,db_now))
    check('D02_4m_old_proof_refuses',verified+timedelta(seconds=240)>=deadline)
    for seconds,ok in ((299,True),(300,False),(301,False)):
        try:end=proof_times(now+timedelta(seconds=seconds),now+timedelta(seconds=300),now+timedelta(seconds=900));result=True
        except b.Stop:result=False
        check('D02_challenge_4_59_5_00:'+str(seconds),result==ok)
        if ok:check('D02_no_retruncate_consumed_challenge',end==now+timedelta(seconds=419))
    # TLS receiver P-256/JWK/P1363, nonce/action/request hash binding.
    receiver=ec.generate_private_key(ec.SECP256R1());jwk=crypto.jwk(receiver.public_key())
    message=crypto.signed('classroom.identity.receiver.v1',{'action':'factor_confirm','receiver_id':'synthetic','case_id':'case-1','flow_generation':1,'server_nonce':crypto.b64(secrets.token_bytes(32)),'request_hash':'a'*64})
    signature=crypto.receiver_sign(receiver,message);crypto.receiver_verify(jwk,message,signature);check('receiver_P256_positive',True)
    reject('receiver_signed_action_tamper',lambda:crypto.receiver_verify(jwk,message+b'x',signature))
    reject('receiver_private_JWK_rejected',lambda:crypto.receiver_key(dict(jwk,d='private')))
    reject('receiver_invalid_curve_point',lambda:crypto.receiver_key(dict(jwk,x=crypto.b64(b'\0'*32),y=crypto.b64(b'\0'*32))))
    # AEAD KEK failures never produce a seed or fall back to password.
    kek=AESGCM.generate_key(bit_length=256);nonce=secrets.token_bytes(12);secret=secrets.token_bytes(32);sealed=AESGCM(kek).encrypt(nonce,secret,b'factor-v1')
    check('KEK_positive',AESGCM(kek).decrypt(nonce,sealed,b'factor-v1')==secret)
    for label,other,aad in (('wrong_key',AESGCM.generate_key(bit_length=256),b'factor-v1'),('wrong_version',kek,b'factor-v2')):
        try:AESGCM(other).decrypt(nonce,sealed,aad)
        except Exception:check('KEK_'+label+'_denied',True)
        else:check('KEK_'+label+'_denied',False)
    global CURRENT_FIXTURE
    CURRENT_FIXTURE=d0
    return {'synthetic_only':True,'D0_root':str(private_root),'D0_highwater':d0.highwater['version'],'real_identity_enrolled':False}
def s_ticket(role,actor):
    import schema
    return schema.base.ticket(role,actor)
