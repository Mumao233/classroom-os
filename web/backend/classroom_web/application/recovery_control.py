"""Stateful local control; receiver messages never acquire an executor context."""
from contextlib import contextmanager
from dataclasses import dataclass,field
from datetime import datetime,timedelta,timezone
import copy,threading,time
from functools import wraps
from typing import Protocol
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.exceptions import InvalidSignature
import rfc8785

from ..domain.auth import AuthRejected,BrowserContext
from ..infrastructure.auth_crypto import digest,random_token,receiver_key,unb64
from ..infrastructure.recovery_authority import control_hash,instant
from ..infrastructure.recovery_peer_windows import NativePipePeer


def utc():return datetime.now(timezone.utc)
def wire_time(value):return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
def wire(value):
    if isinstance(value,dict):return {k:wire(v) for k,v in value.items()}
    if isinstance(value,list):return [wire(v) for v in value]
    if isinstance(value,datetime):return wire_time(value)
    if isinstance(value,str) and value.endswith('+00:00') and 'T' in value:return wire_time(instant(value))
    return value


def invalidate_on_failure(method):
    @wraps(method)
    def call(self,connection,*args,**kwargs):
        try:return method(self,connection,*args,**kwargs)
        except BaseException:
            connection.executor=None;connection.session_token=None;connection.challenges.clear();connection.proofs.clear()
            raise
    return call


@dataclass(frozen=True)
class VerifiedRecoveryRecord:
    target_user_id:str
    role:str
    security_epoch:int
    record_hash:str
    expires_at:datetime


class VerificationRegistry(Protocol):
    """An independently approved verifier, not request-supplied evidence JSON."""
    def resolve(self,reference:str,target_user_id:str)->VerifiedRecoveryRecord: ...
    def current(self,record_hash:str,target_user_id:str)->VerifiedRecoveryRecord: ...


class AdmissionGate:
    def __init__(self):self.condition=threading.Condition();self.readers=0;self.writer=False;self.waiting_writers=0
    @contextmanager
    def shared(self):
        until=time.monotonic()+5
        with self.condition:
            while self.writer or self.waiting_writers:
                left=until-time.monotonic()
                if left<=0:raise AuthRejected('RATE_LIMITED',429)
                self.condition.wait(left)
            self.readers+=1
        try:yield
        finally:
            with self.condition:self.readers-=1;self.condition.notify_all()
    @contextmanager
    def exclusive(self):
        until=time.monotonic()+5
        with self.condition:
            self.waiting_writers+=1
            try:
                while self.writer or self.readers:
                    left=until-time.monotonic()
                    if left<=0:raise AuthRejected('RATE_LIMITED',429)
                    self.condition.wait(left)
                self.writer=True
            finally:self.waiting_writers-=1;self.condition.notify_all()
        try:yield
        finally:
            with self.condition:self.writer=False;self.condition.notify_all()


@dataclass(repr=False)
class ControlConnection:
    peer:NativePipePeer
    connection_id:str=field(default_factory=lambda:str(uuid4()))
    hello:dict|None=None
    executor:dict|None=None
    deadline:datetime|None=None
    session_token:str|None=None
    challenges:dict=field(default_factory=dict)
    proofs:dict=field(default_factory=dict)
    closed:bool=False


@dataclass(repr=False)
class ReceiverWait:
    key:tuple
    request:dict
    expires_at:datetime
    event:threading.Event=field(default_factory=threading.Event)
    cap:str|None=None
    closed:bool=False


class RecoveryBroker:
    def __init__(self,*,repository,authority_verifier,instance_lease,credentials,hash_gate,auth_service,verification_registry,maintenance_start=False):
        self.repository=repository;self.verifier=authority_verifier;self.lease=instance_lease;self.crypto=credentials;self.hash_gate=hash_gate
        self.auth=auth_service;self.registry=verification_registry;self.schema=authority_verifier.schema;self.gate=AdmissionGate();self.lock=threading.RLock()
        self.instance_id=str(uuid4());self.connections={};self.receivers={};self.delivery={};self.closed=True
        self.lease.check()
        if self.lease.deployment_id!=self.verifier.root.deployment_id:raise AuthRejected('SERVICE_DISABLED',503)
        # A supervised restart during M1-M3 may expose only the separate
        # maintenance adapter. All ordinary control/receiver admission stays shut.
        if maintenance_start:return
        current=self.verifier.current(require_enabled=False);p=self._base(current);p['service_logins']=list(repository.service_logins)
        self._need(repository.call('bind_control_broker',p));self.closed=False

    @staticmethod
    def _need(value):
        if value is None:raise AuthRejected('FORBIDDEN',403)
        return value

    def _base(self,current):
        return dict(deployment_id=current.deployment_id,version=current.version,manifest_hash=current.manifest_hash,broker_instance_id=self.instance_id,
                    os_mapping_hash=control_hash(current.authority))

    def _context(self,connection,current):
        self.lease.check()
        with self.lock:
            if self.closed or connection.closed or self.connections.get(connection.connection_id) is not connection or not connection.executor or utc()>=connection.deadline:
                raise AuthRejected('FORBIDDEN',403)
        peer=connection.peer.subject()
        if connection.executor['executor_type']=='authority' and (not current.authority['enabled'] or connection.executor['executor_id']!=current.authority['authority_id'] or peer!=current.authority['os_subject']):raise AuthRejected('FORBIDDEN',403)
        result={**self._base(current),**connection.executor,'auth_context_deadline':wire_time(connection.deadline)}
        if connection.session_token:result['actor_session_hash']=digest('session',connection.session_token)
        return result

    def _emit(self,name,body):
        value=wire({'protocol_version':1,'kind':self.schema.schema['$defs'][name]['properties']['kind']['const'],**body})
        self.schema.check(name,value);return value

    def connect(self,peer):
        if not isinstance(peer,NativePipePeer):raise AuthRejected('FORBIDDEN',403)
        peer.subject();self.lease.check()
        with self.lock:
            if self.closed or len(self.connections)>=16:raise AuthRejected('RATE_LIMITED',429)
            connection=ControlConnection(peer);self.connections[connection.connection_id]=connection
        return connection

    def disconnect(self,connection):
        with self.lock:
            connection.closed=True;connection.executor=None;connection.session_token=None;connection.challenges.clear();connection.proofs.clear()
            self.connections.pop(connection.connection_id,None)
        connection.peer.close()

    def hello(self,connection,request):
        self.schema.check('ControlHelloRequest',request);self.lease.check();connection.peer.subject()
        if connection.closed or request['deployment_id']!=self.verifier.root.deployment_id:raise AuthRejected('FORBIDDEN',403)
        connection.executor=None;connection.session_token=None;connection.proofs.clear()
        connection.challenges={k:v for k,v in connection.challenges.items() if utc()<instant(v['challenge_expiry'])}
        connection.hello=self._emit('ControlHelloResponse',dict(deployment_id=request['deployment_id'],authentication_challenge_id=str(uuid4()),challenge_nonce=random_token(),
            authentication_mode=request['authentication_mode'],challenge_expiry=wire_time(utc()+timedelta(seconds=120))))
        return copy.deepcopy(connection.hello)

    def authenticate(self,connection,request):
        self.schema.check('ControlAuthenticateRequest',request)
        try:
            hello=connection.hello
            if not hello or request['authentication_challenge_id']!=hello['authentication_challenge_id'] or utc()>=instant(hello['challenge_expiry']):raise AuthRejected('FORBIDDEN',403)
            evidence=request['evidence'];connection.hello=None
            with self.gate.shared():
                if hello['authentication_mode']=='authority' and evidence['evidence_type']=='authority_signature':
                    current=self.verifier.authenticate(hello,connection.peer.subject(),evidence['signature'])
                    executor=dict(executor_type='authority',executor_id=current.authority['authority_id'])
                elif hello['authentication_mode']=='actor' and evidence['evidence_type']=='actor_session':
                    current=self.verifier.current(require_enabled=False);s=self.auth.context(BrowserContext(session=evidence['session_token']),touch=False)
                    if s['role']!='admin' or s['mfa_level']!='totp':raise AuthRejected('FORBIDDEN',403)
                    executor=dict(executor_type='actor',executor_id=s['owner_id']);connection.session_token=evidence['session_token']
                else:raise AuthRejected('FORBIDDEN',403)
                connection.executor=executor;connection.deadline=min(utc()+timedelta(seconds=120),instant(hello['challenge_expiry']))
                self._need(self.repository.call('control_executor',self._context(connection,current)))
            return self._emit('ControlAuthenticateResponse',dict(auth_context_id=connection.connection_id,executor=executor,expires_at=wire_time(connection.deadline)))
        except BaseException:
            connection.executor=None;connection.session_token=None;connection.challenges.clear();connection.proofs.clear();raise

    def _record(self,record,*,target,role=None,epoch=None,record_hash=None):
        if (not isinstance(record,VerifiedRecoveryRecord) or record.target_user_id!=target or record.expires_at.tzinfo is None or utc()>=record.expires_at
                or (role is not None and record.role!=role) or (epoch is not None and record.security_epoch!=epoch) or (record_hash is not None and record.record_hash!=record_hash)):
            raise AuthRejected('FORBIDDEN',403)
        return record

    def open_case(self,connection,request):
        self.schema.check('CaseOpenRequest',request)
        if self.registry is None:raise AuthRejected('SERVICE_DISABLED',503)
        with self.gate.shared():
            current=self.verifier.current(require_enabled=False);p=self._context(connection,current)
            if request['deployment_id']!=current.deployment_id:raise AuthRejected('FORBIDDEN',403)
            record=self._record(self.registry.resolve(request['verification_record_ref'],request['target_user_id']),target=request['target_user_id'],role=request['recovery_type'])
            value=self._need(self.repository.call('open_manual_case',{**p,'case_id':str(uuid4()),'target_user_id':record.target_user_id,'verification_record_hash':record.record_hash,
                'verified_target_role':record.role,'verified_target_epoch':record.security_epoch,'verification_deadline':wire_time(record.expires_at)}))
            return self._emit('CaseOpenResponse',dict(deployment_id=current.deployment_id,case_id=value['case_id'],executor=connection.executor,stage='verified_case',verification_record_hash=record.record_hash))

    def challenge(self,connection,request):
        self.schema.check('ProofChallengeRequest',request)
        with self.gate.shared():
            current=self.verifier.current(require_enabled=False);p=self._context(connection,current)
            if request['deployment_id']!=current.deployment_id:raise AuthRejected('FORBIDDEN',403)
            if len(connection.challenges)>=16:raise AuthRejected('RATE_LIMITED',429)
            if request['action']=='status':plan=dict(deployment_id=current.deployment_id,case_id=request['case_id'],op_id=request['op_id'],action='status',executor=connection.executor)
            else:
                plan=self._need(self.repository.call('manual_case_plan',{**p,'case_id':request['case_id'],'op_id':request['op_id'],'action':request['action'],'receiver_id':request['receiver_id']}))
                if plan['flow_generation']!=request['expected_flow_generation']:raise AuthRejected('FORBIDDEN',403)
                if self.registry is None:raise AuthRejected('SERVICE_DISABLED',503)
                self._record(self.registry.current(plan['verification_record_hash'],plan['target_user_id']),target=plan['target_user_id'],role=plan['target_role'],epoch=plan['target_security_epoch'],record_hash=plan['verification_record_hash'])
            now=utc();body=dict(challenge_id=str(uuid4()),challenge_nonce=random_token(),plan=plan,plan_hash=control_hash(plan),issued_at=wire_time(now),challenge_expiry=wire_time(now+timedelta(minutes=5)))
            self.schema.check('ChallengeBody',body);connection.challenges[body['challenge_id']]=copy.deepcopy(body)
            return self._emit('ProofChallengeResponse',dict(challenge=body))

    @invalidate_on_failure
    def verify_proof(self,connection,request):
        self.schema.check('ProofVerifyRequest',request)
        challenge=connection.challenges.pop(request['challenge_id'],None)
        if challenge is None or utc()>=instant(challenge['challenge_expiry']):raise AuthRejected('FORBIDDEN',403)
        with self.gate.shared():
            plan=challenge['plan'];evidence=request['evidence'];bridge=None
            if plan['executor']!=connection.executor:raise AuthRejected('FORBIDDEN',403)
            if connection.executor and connection.executor['executor_type']=='authority' and evidence['evidence_type']=='authority_signature':
                current=self.verifier.approve(challenge,evidence['signature'],peer=connection.peer.subject());verified=utc();deadline=verified+timedelta(seconds=120)
            elif connection.executor and connection.executor['executor_type']=='actor' and evidence['evidence_type']=='student_admin_reauth':
                current=self.verifier.current(require_enabled=False);bridge=self.auth.control_bridge(session_token=connection.session_token,proof_token=evidence['reauth_token'],plan=plan)
                if bridge['executor_id']!=connection.executor['executor_id']:raise AuthRejected('FORBIDDEN',403)
                verified=instant(bridge['verified_at']);deadline=instant(bridge['operation_deadline'])
            else:raise AuthRejected('FORBIDDEN',403)
            p=self._context(connection,current);deadline=min(deadline,connection.deadline)
            if verified<instant(challenge['issued_at']) or verified>=instant(challenge['challenge_expiry']) or utc()>=deadline:raise AuthRejected('FORBIDDEN',403)
            proof_id=str(uuid4())
            if plan['action']!='status':
                record=self._record(self.registry.current(plan['verification_record_hash'],plan['target_user_id']),target=plan['target_user_id'],role=plan['target_role'],epoch=plan['target_security_epoch'],record_hash=plan['verification_record_hash'])
                payload={**p,**{k:plan[k] for k in ('case_id','op_id','action','receiver_id')},'plan':plan,'plan_hash':challenge['plan_hash'],'nonce_hash':digest('operation_nonce',challenge['challenge_nonce']),
                         'proof_id':proof_id,'verified_at':wire_time(verified),'challenge_issued_at':challenge['issued_at'],'challenge_expiry':challenge['challenge_expiry']}
                if bridge:payload['actor_bridge_hash']=digest('reauth',evidence['reauth_token'])
                value=self._need(self.repository.call('prepare_manual_proof',payload));deadline=instant(value['operation_deadline'])
            connection.proofs[proof_id]=dict(plan=plan,verified_at=wire_time(verified),operation_deadline=wire_time(deadline),plan_hash=challenge['plan_hash'])
            return self._emit('ProofVerifyResponse',dict(proof_id=proof_id,case_id=plan['case_id'],op_id=plan['op_id'],action=plan['action'],plan_hash=challenge['plan_hash'],verified_at=wire_time(verified),operation_deadline=wire_time(deadline),single_use=plan['action']!='status'))

    def execute(self,connection,request,*,status=False):
        name='RecoveryStatusRequest' if status else 'RecoveryCompleteRequest' if request.get('kind')=='recovery.complete.request' else 'RecoveryIssueRequest'
        self.schema.check(name,request);proof=connection.proofs.get(request['proof_id'])
        if proof is None or utc()>=instant(proof['operation_deadline']):raise AuthRejected('FORBIDDEN',403)
        plan=proof['plan'];action=plan['action']
        if (request['case_id']!=plan['case_id'] or request['op_id']!=plan['op_id'] or status!=(action=='status')
                or (not status and ((name=='RecoveryCompleteRequest')!=(action=='complete')))):raise AuthRejected('FORBIDDEN',403)
        with self.gate.shared():
            current=self.verifier.current(require_enabled=False);p=self._context(connection,current)
            payload={**p,'case_id':plan['case_id'],'op_id':plan['op_id'],'proof_id':request['proof_id'],'plan_hash':proof['plan_hash'],
                     'verified_at':proof['verified_at'],'operation_deadline':proof['operation_deadline']}
            if status:return self._emit('RecoveryStatusResponse',{k:v for k,v in self._need(self.repository.call('read_manual_status',payload)).items() if k not in ('protocol_version','kind')})
            # A write proof cannot be blindly retried after any unknown result.
            connection.proofs.pop(request['proof_id'])
            self._record(self.registry.current(plan['verification_record_hash'],plan['target_user_id']),target=plan['target_user_id'],role=plan['target_role'],epoch=plan['target_security_epoch'],record_hash=plan['verification_record_hash'])
            if action=='complete':
                payload.update(pending_factor_id=plan['pending_factor_id'],confirmation_hash=plan['confirmation_hash'],audit_id=str(uuid4()),notice_id=str(uuid4()))
                value=self._need(self.repository.call('complete_manual',payload));return self._emit('RecoveryCompleteResponse',value)
            key=(plan['case_id'],plan['flow_generation'],plan['receiver_id'])
            with self.lock:
                waiter=self.delivery.get(key)
                if waiter is None or waiter.closed or utc()>=waiter.expires_at:raise AuthRejected('FORBIDDEN',403)
            cap=random_token();payload.update(receiver_id=plan['receiver_id'],cap_id=str(uuid4()),cap_hash=digest('manual_cap',cap),audit_id=str(uuid4()))
            value=self._need(self.repository.call('issue_manual',payload))
            with self.lock:
                if not waiter.closed:
                    self.receivers[plan['receiver_id']]['expires_at']=instant(value['pending_flow_expiry'])
                    self.receivers[plan['receiver_id']].update(target_user_id=plan['target_user_id'],actor_id=plan['executor']['executor_id'] if plan['executor']['executor_type']=='actor' else None)
                    waiter.cap=cap;waiter.event.set()
            return self._emit('RecoveryIssueResponse',value)

    def register_receiver(self,request):
        self.schema.check('ReceiverRegisterRequest',request);self.lease.check()
        public,_=receiver_key(request['public_key']);unb64(request['client_nonce'],32)
        with self.gate.shared():
            current=self.verifier.current(require_enabled=False);rid=str(uuid4());nonce=random_token()
            with self.lock:
                self.receivers={k:v for k,v in self.receivers.items() if utc()<v['expires_at']}
                if self.closed or len(self.receivers)>=200:raise AuthRejected('RATE_LIMITED',429)
                value=self._need(self.repository.call('register_receiver',{**self._base(current),'receiver_id':rid,'public_key':request['public_key'],
                    'key_hash':control_hash(request['public_key']),'nonce_hash':digest('manual_nonce',nonce)}))
                self.receivers[rid]=dict(public=public,expires_at=instant(value['expires_at']))
        return self._emit('ReceiverRegisterResponse',dict(receiver_id=rid,receiver_key_hash=control_hash(request['public_key']),server_nonce=nonce,registration_expiry=value['expires_at']))

    def _receiver_request(self,request,action):
        self.schema.check({'await':'ReceiverAwaitRequest','factor_start':'ReceiverFactorStartRequest','factor_confirm':'ReceiverFactorConfirmRequest','abandon':'ReceiverAbandonRequest'}[action],request)
        a=request['receiver_auth'];unb64(a['server_nonce'],32)
        with self.lock:
            r=self.receivers.get(a['receiver_id'])
            if self.closed or not r or utc()>=r['expires_at']:raise AuthRejected('FORBIDDEN',403)
        unsigned=copy.deepcopy(request);del unsigned['receiver_auth']['signature']
        signed=dict(protocol_version=1,action=action,**{k:a[k] for k in ('receiver_id','case_id','flow_generation','server_nonce')},request_hash=control_hash(unsigned))
        raw=unb64(a['signature'],64)
        try:r['public'].verify(encode_dss_signature(int.from_bytes(raw[:32]),int.from_bytes(raw[32:])),b'classroom.identity.receiver.v1\0'+rfc8785.dumps(signed),ec.ECDSA(hashes.SHA256()))
        except (ValueError,InvalidSignature):raise AuthRejected('FORBIDDEN',403) from None
        return a

    def begin_wait(self,request):
        a=self._receiver_request(request,'await');self.lease.check()
        key=(a['case_id'],a['flow_generation'],a['receiver_id'])
        with self.lock:
            if key in self.delivery or len(self.delivery)>=16:raise AuthRejected('FORBIDDEN',403)
            waiter=ReceiverWait(key,copy.deepcopy(request),min(self.receivers[a['receiver_id']]['expires_at'],utc()+timedelta(seconds=120)))
            self.delivery[key]=waiter
            return waiter

    def finish_wait(self,waiter):
        """Only the already bound TLS request can receive this one delivery."""
        with self.lock:
            if self.delivery.get(waiter.key) is not waiter or waiter.closed or utc()>=waiter.expires_at or not waiter.event.is_set() or waiter.cap is None:
                raise AuthRejected('FORBIDDEN',403)
            cap=waiter.cap;waiter.cap=None
        try:
            result=self.receiver(waiter.request,'await')
            return self._emit('ReceiverDelivery',{**result,'cap':cap})
        finally:self.cancel_wait(waiter)

    def cancel_wait(self,waiter):
        with self.lock:
            waiter.closed=True;waiter.cap=None;waiter.event.set()
            if self.delivery.get(waiter.key) is waiter:self.delivery.pop(waiter.key)

    def receiver(self,request,action,*,source='local-receiver'):
        a=self._receiver_request(request,action);self.lease.check()
        with self.gate.shared():
            current=self.verifier.current(require_enabled=False);p={**self._base(current),**{k:a[k] for k in ('receiver_id','case_id','flow_generation')},'action':action,
                'nonce_hash':digest('manual_nonce',a['server_nonce']),'next_nonce_hash':None}
            if action!='await':p['cap_hash']=digest('manual_cap',request['cap'])
            row=self._need(self.repository.call('receiver_peek',p));nonce=random_token();p['next_nonce_hash']=digest('manual_nonce',nonce)
            seed=None
            if action=='factor_start':
                if row['target_role']=='student':
                    if request['target_password'] is None:raise AuthRejected('FORBIDDEN',403)
                    self.auth.check_current_password(row,request['target_password'],source)
                    p['password_hash']=row['password_hash']
                elif request['target_password'] is not None:raise AuthRejected('FORBIDDEN',403)
                if row['factor_id'] is not None:raise AuthRejected('FORBIDDEN',403)
                fid=str(uuid4());seed=self.crypto.new_seed();p.update(factor_id=fid,encrypted_seed=self.crypto.seal(row['owner_id'],fid,seed))
            elif action=='factor_confirm':
                if row['factor_id'] is None or row['factor_id']!=request['pending_factor_id']:raise AuthRejected('FORBIDDEN',403)
                factor_seed=self.crypto.open(row['owner_id'],row['factor_id'],row['encrypted_seed'])
                p.update(factor_id=row['factor_id'],encrypted_seed=row['encrypted_seed'],step=self.crypto.totp_step(factor_seed,request['code'],instant(row['now'])),
                    confirmation_hash=self.crypto.commitment('manual_confirmation',dict(case_id=a['case_id'],flow_generation=a['flow_generation'],factor_id=row['factor_id'],nonce=random_token())))
            value=self._need(self.repository.call('confirm_manual_receiver',p))
            with self.lock:self.receivers[a['receiver_id']]['expires_at']=instant(value['pending_flow_expiry'])
            body={k:a[k] for k in ('case_id','flow_generation')}
            if action=='await':return {**body,'pending_flow_expiry':value['pending_flow_expiry'],'server_nonce':nonce}
            if action=='abandon':return self._emit('ReceiverAbandonResponse',{**body,'stage':'abandoned'})
            body.update(pending_factor_id=value['pending_factor_id'],server_nonce=nonce)
            if action=='factor_start':return self._emit('ReceiverFactorStartResponse',{**body,'totp_seed':seed,'pending_flow_expiry':value['pending_flow_expiry']})
            return self._emit('ReceiverFactorConfirmResponse',{**body,'stage':'pending_confirmed','confirmation_hash':value['confirmation_hash']})

    def close(self):
        with self.gate.exclusive():
            self.closed=True
            for connection in list(self.connections.values()):self.disconnect(connection)
            for waiter in list(self.delivery.values()):self.cancel_wait(waiter)
            self.receivers.clear();self.repository.dispose();self.lease.close()

    def identity_notice(self,event):
        """A trusted dispatcher calls after its claim transaction commits."""
        with self.gate.shared():
            if event.get('scope')=='registry':
                if event.get('deployment_id')!=self.verifier.root.deployment_id:return True
                affected=set(self.receivers)
                for connection in list(self.connections.values()):self.disconnect(connection)
            elif event.get('scope')=='user':
                subject=event.get('subject_id')
                affected={rid for rid,r in self.receivers.items() if subject in (r.get('target_user_id'),r.get('actor_id'))}
                for connection in list(self.connections.values()):
                    if connection.executor and connection.executor['executor_type']=='actor' and connection.executor['executor_id']==subject:self.disconnect(connection)
            else:return False
            with self.lock:
                for waiter in list(self.delivery.values()):
                    if waiter.key[2] in affected:self.cancel_wait(waiter)
                for rid in affected:self.receivers.pop(rid,None)
        return True
