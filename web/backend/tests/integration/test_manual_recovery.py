"""Real PG, native Windows peer and synthetic independently installed D0 only."""
import copy,json,os,threading,time
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest,rfc8785,sqlalchemy as sa,pyotp
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat

from classroom_web.application.recovery_control import RecoveryBroker,VerifiedRecoveryRecord,wire_time
from classroom_web.infrastructure.auth_crypto import b64,random_token,digest
from classroom_web.infrastructure.recovery_authority import AuthorityVerifier,InstalledAuthorityRoot,ControlSchema,control_hash
from classroom_web.infrastructure.recovery_peer_windows import NativePipePeer
from classroom_web.infrastructure.recovery_singleton_windows import GlobalBrokerLease
from classroom_web.infrastructure.recovery_transport_windows import PipeListener,connect_pipe,process_sid
from classroom_web.infrastructure.recovery_repository import PgRecoveryRepository,CONTROL_FUNCTIONS
from classroom_web.domain.auth import AuthRejected
from classroom_web.test_support import SYNTHETIC_PASSWORD
from test_authentication import auth,install_factor,PASSWORD,post,BASE


@contextmanager
def real_peer():
    name='ClassroomOSIdentity-test-'+uuid4().hex;sid=process_sid();stop=threading.Event();errors=[]
    with PipeListener(name,allowed_sids=[sid]) as listener:
        def client():
            try:
                with connect_pipe(name,expected_server_pid=os.getpid(),expected_server_sid=sid) as channel:
                    channel.send(b'kernel-peer-probe');stop.wait(120)
            except BaseException as error:errors.append(type(error).__name__)
        t=threading.Thread(target=client);t.start();channel=listener.accept(5)
        try:
            assert channel is not None and channel.receive()==b'kernel-peer-probe'
            with NativePipePeer(channel.handle) as peer:
                assert peer.subject()==dict(provider='windows_sid',subject=sid)
                yield peer
        finally:
            stop.set();t.join(5)
            if channel:channel.close()
        assert not t.is_alive() and not errors


@pytest.fixture
def control(auth):
    a=auth;deployment=str(uuid4());sid=process_sid();keys=[Ed25519PrivateKey.generate() for _ in range(3)]
    public=[dict(alg='Ed25519',key_id=str(uuid4()),public_key=b64(k.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw))) for k in keys]
    body=dict(deployment_id=deployment,version=1,previous_manifest_hash='0'*64,root_key=public[0],change_key=public[1],maintainer_ref='synthetic-independent-maintainer',
        authority=dict(authority_id=str(uuid4()),project_user_mapping_ref='synthetic-test-approval-only',os_subject=dict(provider='windows_sid',subject=sid),recover_key=public[2],enabled=True),verifier_version='1')
    def sign(index,domain,value):return dict(alg='Ed25519',key_id=public[index]['key_id'],signature=b64(keys[index].sign(domain.encode()+b'\0'+rfc8785.dumps(value))))
    class Reader:
        state='ready';transition_id=None;high_water=None
        def read_current(self,nonce):
            now=datetime.now(timezone.utc);manifest=copy.deepcopy(body)
            high=self.high_water or manifest
            current=dict(deployment_id=deployment,request_nonce=nonce,initialization_complete=True,high_water_version=high['version'],high_water_hash=control_hash(high),state=self.state,transition_id=self.transition_id,issued_at=wire_time(now),expires_at=wire_time(now+timedelta(seconds=30)))
            return dict(protocol_version=1,kind='d0.manifest',body=manifest,signature=sign(0,'classroom.identity.manifest.v1',manifest)),dict(protocol_version=1,kind='d0.current.evidence',body=current,signature=sign(0,'classroom.identity.current.v1',current))
    schema=ControlSchema(json.loads((Path(__file__).resolve().parents[4]/'docs/web-alpha/architecture/contracts/identity-control.schema.json').read_text(encoding='utf8')))
    reader=Reader();verifier=AuthorityVerifier(installed_root=InstalledAuthorityRoot(deployment,public[0]['key_id'],public[0]['public_key'],'synthetic-independent-approval'),reader=reader,schema=schema)
    roles={k:'pr02a_control_'+uuid4().hex[:12] for k in CONTROL_FUNCTIONS}
    with a.pg.admin.begin() as c:
        c.execute(sa.text("INSERT INTO identity_control.identity_deployment_guards(deployment_id,version,manifest_hash,state) VALUES(:d,1,:h,'closed')"),dict(d=deployment,h=control_hash(body)))
        c.execute(sa.text("INSERT INTO identity_control.recovery_authorities(id,deployment_id,version,manifest_hash,os_mapping_hash,enabled,expires_at) VALUES(:a,:d,1,:h,:m,true,clock_timestamp()+interval '1 hour')"),dict(a=body['authority']['authority_id'],d=deployment,h=control_hash(body),m=control_hash(body['authority'])))
        for kind,role in roles.items():
            c.exec_driver_sql(f'CREATE ROLE "{role}" LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
            c.exec_driver_sql(f'GRANT USAGE ON SCHEMA identity_api TO "{role}"')
            for f in CONTROL_FUNCTIONS[kind]:c.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION identity_api.{f}(jsonb) TO "{role}"')
    urls={k+'_url':a.pg.admin_url.set(username=v,password=SYNTHETIC_PASSWORD).render_as_string(hide_password=False) for k,v in roles.items()}
    repo=PgRecoveryRepository(**urls)
    class Registry:
        def resolve(self,reference,target):
            if reference!='synthetic-external-verification':raise AuthRejected('FORBIDDEN',403)
            with a.pg.admin.connect() as c:row=c.execute(sa.text('SELECT role,security_epoch FROM users WHERE id=:id'),dict(id=target)).one()
            return VerifiedRecoveryRecord(target,row.role,row.security_epoch,'a'*64,datetime.now(timezone.utc)+timedelta(minutes=10))
        def current(self,record_hash,target):
            if record_hash!='a'*64:raise AuthRejected('FORBIDDEN',403)
            return self.resolve('synthetic-external-verification',target)
    lease=GlobalBrokerLease(deployment,allowed_sids=[sid])
    broker=RecoveryBroker(repository=repo,authority_verifier=verifier,instance_lease=lease,credentials=a.crypto,hash_gate=a.service.hash_gate,auth_service=a.service,verification_registry=Registry())
    state=SimpleNamespace(a=a,broker=broker,body=body,sign=sign,roles=roles,repo=repo,lease=lease,reader=reader,verifier=verifier,registry=Registry())
    try:yield state
    finally:
        state.broker.close()
        with a.pg.admin.begin() as c:
            for role in roles.values():c.exec_driver_sql(f'DROP OWNED BY "{role}"');c.exec_driver_sql(f'DROP ROLE "{role}"')


def message(kind,**kw):return dict(protocol_version=1,kind=kind,**kw)


def authenticated(c,peer):
    b=c.broker;connection=b.connect(peer)
    hello=b.hello(connection,message('identity_control.hello.request',deployment_id=c.body['deployment_id'],authentication_mode='authority'))
    signature=c.sign(2,'classroom.identity.authenticate.v1',dict(hello=hello,peer=peer.subject()))
    b.authenticate(connection,message('identity_control.authenticate.request',authentication_challenge_id=hello['authentication_challenge_id'],evidence=dict(evidence_type='authority_signature',signature=signature)))
    return connection


def new_receiver(c):
    private=ec.generate_private_key(ec.SECP256R1());n=private.public_key().public_numbers()
    jwk=dict(kty='EC',crv='P-256',x=b64(n.x.to_bytes(32)),y=b64(n.y.to_bytes(32)))
    value=c.broker.register_receiver(message('receiver.register.request',public_key=jwk,client_nonce=random_token()))
    return private,value


def signed_receiver(private,r,case,flow,action,**kw):
    kinds={'await':'await','factor_start':'factor.start','factor_confirm':'factor.confirm','abandon':'abandon'}
    request=message('receiver.'+kinds[action]+'.request',receiver_auth=dict(receiver_id=r['receiver_id'],case_id=case,flow_generation=flow,server_nonce=r['server_nonce']),**kw)
    signed=dict(protocol_version=1,action=action,**request['receiver_auth'],request_hash=control_hash(request))
    signature=private.sign(b'classroom.identity.receiver.v1\0'+rfc8785.dumps(signed),ec.ECDSA(hashes.SHA256()));x,y=decode_dss_signature(signature)
    request['receiver_auth']['signature']=b64(x.to_bytes(32)+y.to_bytes(32));return request


def proof(c,connection,case,r,flow,action,op=None):
    b=c.broker;op=op or str(uuid4())
    challenge=b.challenge(connection,message('recovery.proof.challenge.request',deployment_id=c.body['deployment_id'],case_id=case,op_id=op,action=action,receiver_id=r,expected_flow_generation=flow))['challenge']
    signature=c.sign(2,'classroom.identity.recovery-proof.v1',challenge)
    verified=b.verify_proof(connection,message('recovery.proof.verify.request',challenge_id=challenge['challenge_id'],evidence=dict(evidence_type='authority_signature',signature=signature)))
    return message('recovery.complete.request' if action=='complete' else 'recovery.status.request' if action=='status' else 'recovery.issue.request',case_id=case,op_id=op,proof_id=verified['proof_id'])


def pending(c,connection):
    b=c.broker
    case=b.open_case(connection,message('recovery.case.open.request',deployment_id=c.body['deployment_id'],target_user_id=str(c.a.uid),recovery_type='admin',verification_record_ref='synthetic-external-verification'))['case_id']
    private,r=new_receiver(c);wait=b.begin_wait(signed_receiver(private,r,case,1,'await'))
    issue=proof(c,connection,case,r['receiver_id'],1,'issue');value=b.execute(connection,issue)
    delivery=b.finish_wait(wait);r['server_nonce']=delivery['server_nonce']
    return case,private,r,delivery,value,issue


def test_native_pipe_kernel_subject_singleton_and_server_pin():
    with real_peer() as p:assert p.subject()['subject']==process_sid()
    deployment=str(uuid4())
    with GlobalBrokerLease(deployment,allowed_sids=[process_sid()]) as lease:
        lease.check()
        with pytest.raises(AuthRejected):GlobalBrokerLease(deployment,allowed_sids=[process_sid()])
    with PipeListener('ClassroomOSIdentity-pin-'+uuid4().hex,allowed_sids=[process_sid()]) as listener:
        with pytest.raises(AuthRejected):connect_pipe(listener.name.split('\\')[-1],expected_server_pid=os.getpid()+1,expected_server_sid=process_sid())


def test_manual_complete_requires_fresh_operator_and_revokes_without_codes(control):
    c=control;old,seed=install_factor(c.a,role='admin',code=random_token())
    with real_peer() as peer:
        connection=authenticated(c,peer);case,private,r,delivery,issued,issue=pending(c,connection)
        assert 'cap' not in issued and 'totp_seed' not in issued
        start=signed_receiver(private,r,case,1,'factor_start',cap=delivery['cap'],target_password=None)
        factor=c.broker.receiver(start,'factor_start');r['server_nonce']=factor['server_nonce']
        with pytest.raises(AuthRejected):c.broker.receiver(start,'factor_start')
        with pytest.raises(AuthRejected):c.broker.receiver(signed_receiver(private,r,case,1,'factor_start',cap=delivery['cap'],target_password=None),'factor_start')
        confirmed=c.broker.receiver(signed_receiver(private,r,case,1,'factor_confirm',cap=delivery['cap'],pending_factor_id=factor['pending_factor_id'],code=pyotp.TOTP(factor['totp_seed']).now()),'factor_confirm')
        with c.a.pg.admin.connect() as sql:
            assert sql.scalar(sa.text('SELECT security_epoch FROM users WHERE id=:id'),dict(id=c.a.uid))==1
            assert sql.scalar(sa.text('SELECT state FROM mfa_credentials WHERE id=:id'),dict(id=old))=='active'
        complete=proof(c,connection,case,r['receiver_id'],1,'complete')
        result=c.broker.execute(connection,complete)
        assert result['stage']=='completed' and result['full_relogin_required'] and not result['recovery_codes_issued']
        with pytest.raises(AuthRejected):c.broker.execute(connection,complete)
        with c.a.pg.admin.connect() as sql:
            assert sql.scalar(sa.text('SELECT security_epoch FROM users WHERE id=:id'),dict(id=c.a.uid))==2
            assert sql.scalar(sa.text('SELECT state FROM mfa_credentials WHERE id=:id'),dict(id=old))=='revoked'
            assert sql.scalar(sa.text('SELECT state FROM mfa_credentials WHERE id=:id'),dict(id=factor['pending_factor_id']))=='active'
            assert sql.scalar(sa.text('SELECT count(*) FROM recovery_code_groups WHERE owner_id=:id AND revoked_at IS NULL'),dict(id=c.a.uid))==0
            before=sql.scalar(sa.text('SELECT count(*) FROM identity_control.identity_operation_proofs'))
        status=proof(c,connection,case,None,None,'status',op=complete['op_id'])
        for _ in range(2):assert c.broker.execute(connection,status,status=True)['result']=='committed'
        with c.a.pg.admin.connect() as sql:assert sql.scalar(sa.text('SELECT count(*) FROM identity_control.identity_operation_proofs'))==before


def test_receiver_cannot_continue_after_issuer_revocation(control):
    c=control;install_factor(c.a,role='admin')
    with real_peer() as peer:
        connection=authenticated(c,peer);case,private,r,delivery,_,_=pending(c,connection)
        with c.a.pg.admin.begin() as sql:sql.execute(sa.text('UPDATE identity_control.recovery_authorities SET enabled=false WHERE id=:id'),dict(id=c.body['authority']['authority_id']))
        with pytest.raises(AuthRejected):c.broker.receiver(signed_receiver(private,r,case,1,'factor_start',cap=delivery['cap'],target_password=None),'factor_start')
        with c.a.pg.admin.connect() as sql:assert sql.scalar(sa.text('SELECT count(*) FROM mfa_credentials WHERE owner_id=:id AND state=\'pending\''),dict(id=c.a.uid))==0


def test_unbound_receiver_and_unknown_status_never_grant_writes(control):
    c=control;install_factor(c.a,role='admin')
    with real_peer() as peer:
        connection=authenticated(c,peer)
        status=proof(c,connection,str(uuid4()),None,None,'status')
        assert c.broker.execute(connection,status,status=True)['result']=='unknown'
        with c.a.pg.admin.connect() as sql:
            assert sql.scalar(sa.text('SELECT count(*) FROM identity_control.identity_operation_proofs WHERE deployment_id=:d'),dict(d=c.body['deployment_id']))==0
        with pytest.raises(AuthRejected):c.broker.connect(object())


def test_reissue_invalidates_old_cap_and_preserves_original_deadline(control):
    c=control;install_factor(c.a,role='admin')
    with real_peer() as peer:
        connection=authenticated(c,peer);case,private,old,delivery,_,_=pending(c,connection)
        factor=c.broker.receiver(signed_receiver(private,old,case,1,'factor_start',cap=delivery['cap'],target_password=None),'factor_start')
        new_private,new=new_receiver(c);wait=c.broker.begin_wait(signed_receiver(new_private,new,case,2,'await'))
        request=proof(c,connection,case,new['receiver_id'],2,'reissue');result=c.broker.execute(connection,request)
        second=c.broker.finish_wait(wait)
        assert result['pending_flow_expiry']==delivery['pending_flow_expiry']==second['pending_flow_expiry']
        old['server_nonce']=factor['server_nonce']
        with pytest.raises(AuthRejected):c.broker.receiver(signed_receiver(private,old,case,1,'abandon',cap=delivery['cap']),'abandon')
        with c.a.pg.admin.connect() as sql:assert sql.scalar(sa.text('SELECT state FROM mfa_credentials WHERE id=:id'),dict(id=factor['pending_factor_id']))=='revoked'


def test_audit_failure_rolls_back_complete_and_status_is_independent(control):
    c=control;install_factor(c.a,role='admin')
    with real_peer() as peer:
        connection=authenticated(c,peer);case,private,r,delivery,_,_=pending(c,connection)
        f=c.broker.receiver(signed_receiver(private,r,case,1,'factor_start',cap=delivery['cap'],target_password=None),'factor_start');r['server_nonce']=f['server_nonce']
        c.broker.receiver(signed_receiver(private,r,case,1,'factor_confirm',cap=delivery['cap'],pending_factor_id=f['pending_factor_id'],code=pyotp.TOTP(f['totp_seed']).now()),'factor_confirm')
        complete=proof(c,connection,case,r['receiver_id'],1,'complete')
        with c.a.pg.admin.begin() as sql:
            sql.exec_driver_sql("CREATE FUNCTION public.synthetic_reject_manual_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.action='complete' THEN RAISE EXCEPTION 'synthetic audit failure';END IF;RETURN NEW;END $$")
            sql.exec_driver_sql('CREATE TRIGGER synthetic_reject_manual_audit BEFORE INSERT ON identity_control.identity_control_audit FOR EACH ROW EXECUTE FUNCTION public.synthetic_reject_manual_audit()')
        try:
            with pytest.raises(AuthRejected):c.broker.execute(connection,complete)
        finally:
            with c.a.pg.admin.begin() as sql:
                sql.exec_driver_sql('DROP TRIGGER synthetic_reject_manual_audit ON identity_control.identity_control_audit');sql.exec_driver_sql('DROP FUNCTION public.synthetic_reject_manual_audit()')
        status=proof(c,connection,case,None,None,'status',op=complete['op_id'])
        assert c.broker.execute(connection,status,status=True)['result']=='not_committed'
        with c.a.pg.admin.connect() as sql:
            assert sql.scalar(sa.text('SELECT security_epoch FROM users WHERE id=:id'),dict(id=c.a.uid))==1
            assert sql.scalar(sa.text('SELECT state FROM mfa_credentials WHERE id=:id'),dict(id=f['pending_factor_id']))=='pending'
            assert sql.scalar(sa.text('SELECT consumed_at IS NULL FROM identity_control.identity_operation_proofs WHERE id=:id'),dict(id=complete['proof_id']))
        with c.repo.engines['status'].begin() as sql:
            with pytest.raises(sa.exc.DBAPIError):sql.exec_driver_sql('UPDATE identity_control.manual_recovery_cases SET state=\'completed\'')


def test_target_epoch_change_does_not_hide_original_status_fact(control):
    c=control;install_factor(c.a,role='admin')
    with real_peer() as peer:
        connection=authenticated(c,peer);case,_,_,_,_,issue=pending(c,connection)
        with c.a.pg.admin.begin() as sql:sql.execute(sa.text('UPDATE users SET security_epoch=security_epoch+1 WHERE id=:id'),dict(id=c.a.uid))
        status=proof(c,connection,case,None,None,'status',op=issue['op_id'])
        assert c.broker.execute(connection,status,status=True)['result']=='committed'


def test_registration_transition_is_monotonic_and_old_pin_cannot_reopen(control):
    from classroom_web.application.recovery_maintenance import AcceptedTransition,apply_change
    c=control;b=c.broker;old=copy.deepcopy(c.body);new=copy.deepcopy(old);new['version']=2;new['previous_manifest_hash']=control_hash(old);new['authority']['enabled']=False
    now=datetime.now(timezone.utc)
    body=dict(deployment_id=old['deployment_id'],authority_id=old['authority']['authority_id'],action='revoke',old_manifest_hash=control_hash(old),new_manifest_hash=control_hash(new),
        old_version=1,new_version=2,old_binding_hash=control_hash(old['authority']),new_binding_hash=control_hash(new['authority']),approval_nonce=random_token(),issued_at=wire_time(now),expires_at=wire_time(now+timedelta(seconds=120)),maintainer_ref=old['maintainer_ref'],external_approval_ref='synthetic-maintenance-only')
    request=message('d0.change.request',approval=message('d0.change_approval',body=body,signature=c.sign(1,'classroom.identity.change.v1',body)),
        new_manifest=message('d0.manifest',body=new,signature=c.sign(0,'classroom.identity.manifest.v1',new)),new_key_proofs=[])
    class External:
        transition=AcceptedTransition(str(uuid4()),old,request,str(uuid4()))
        def reserve(self,request,*,peer,deadline):
            assert peer==dict(provider='windows_sid',subject=process_sid()) and request==self.transition.request
            return self.transition
        def transition_current(self,tid,nonce):
            current=dict(deployment_id=old['deployment_id'],request_nonce=nonce,initialization_complete=True,high_water_version=2,high_water_hash=control_hash(new),state='transition_reserved',transition_id=tid,issued_at=wire_time(datetime.now(timezone.utc)),expires_at=wire_time(datetime.now(timezone.utc)+timedelta(seconds=30)))
            return message('d0.current.evidence',body=current,signature=c.sign(0,'classroom.identity.current.v1',current))
        def publish(self,transition):
            c.body.clear();c.body.update(copy.deepcopy(new));return b.verifier.root
    with real_peer() as peer:
        result=apply_change(b,peer,request,external=External())
        assert result['phase']=='published_ready' and result['version']==2
        with c.a.pg.admin.connect() as sql:
            assert sql.scalar(sa.text('SELECT version FROM identity_control.identity_deployment_guards WHERE deployment_id=:id'),dict(id=old['deployment_id']))==2
            assert not sql.scalar(sa.text('SELECT enabled FROM identity_control.recovery_authorities WHERE deployment_id=:id'),dict(id=old['deployment_id']))
        with pytest.raises(AuthRejected):b.verifier.current()
        with pytest.raises(AuthRejected):apply_change(b,peer,request,external=External())


def test_student_manual_path_requires_scoped_real_admin_reauthentication(control):
    c=control;a=c.a;b=c.broker;actor=uuid4();fid=uuid4();seed=a.crypto.new_seed();email=uuid4().hex+'@example.test'
    with a.pg.admin.begin() as sql:
        sql.execute(sa.text("INSERT INTO users(id,role,state,normalized_email,email_verified_at,password_hash) VALUES(:u,'admin','active',:e,clock_timestamp(),:h)"),dict(u=actor,e=email,h=a.crypto.hash(PASSWORD)))
        sql.execute(sa.text("INSERT INTO mfa_credentials(id,owner_id,state,encrypted_seed,confirmed_at) VALUES(:f,:u,'active',:s,clock_timestamp())"),dict(f=fid,u=actor,s=a.crypto.seal(str(actor),str(fid),seed)))
        sql.execute(sa.text("INSERT INTO identity_control.recovery_permissions(deployment_id,actor_id,enabled,version,expires_at) VALUES(:d,:u,true,1,clock_timestamp()+interval '1 hour')"),dict(d=c.body['deployment_id'],u=actor))
    # Keep the accepted previous/current/next counters in one real interval;
    # no factor counter or database clock is reset to manufacture fresh proofs.
    while time.time()%30>25:time.sleep(.05)
    counter=int(time.time())//30
    csrf=a.client.get(BASE+'/auth/csrf').json()['csrf_token']
    challenge=post(a,'/auth/login',dict(email=email,password=PASSWORD,remember_me=False,device_name='Synthetic recovery Admin'),csrf).json()
    result=post(a,'/auth/login/mfa',dict(factor_type='totp',challenge_id=challenge['challenge_id'],code=pyotp.TOTP(seed).at((counter-1)*30)),csrf)
    assert result.status_code==200 and result.json()['state']=='authenticated'
    csrf=result.json()['csrf_token'];session=a.client.cookies.get('__Host-classroom_session');a.service.control_deployment_id=c.body['deployment_id']
    with real_peer() as peer:
        connection=b.connect(peer);hello=b.hello(connection,message('identity_control.hello.request',deployment_id=c.body['deployment_id'],authentication_mode='actor'))
        b.authenticate(connection,message('identity_control.authenticate.request',authentication_challenge_id=hello['authentication_challenge_id'],evidence=dict(evidence_type='actor_session',session_token=session)))
        case=b.open_case(connection,message('recovery.case.open.request',deployment_id=c.body['deployment_id'],target_user_id=str(a.uid),recovery_type='student',verification_record_ref='synthetic-external-verification'))['case_id']
        private,r=new_receiver(c);wait=b.begin_wait(signed_receiver(private,r,case,1,'await'))
        def actor_proof(action,step,op=None):
            op=op or str(uuid4());ch=b.challenge(connection,message('recovery.proof.challenge.request',deployment_id=c.body['deployment_id'],case_id=case,op_id=op,action=action,receiver_id=None if action=='status' else r['receiver_id'],expected_flow_generation=None if action=='status' else 1))['challenge']
            operation=dict(purpose='student_recovery_'+action,target=dict(kind='recovery_case',id=case),candidate_parameters=dict(op_id=op,plan_hash=ch['plan_hash']))
            response=post(a,'/auth/reauth',dict(password=PASSWORD,mfa_code=pyotp.TOTP(seed).at(step*30),operation=operation),csrf)
            assert response.status_code==200,(response.status_code,list(a.repository.faults))
            token=response.json()['reauth_token']
            verified=b.verify_proof(connection,message('recovery.proof.verify.request',challenge_id=ch['challenge_id'],evidence=dict(evidence_type='student_admin_reauth',reauth_token=token)))
            return message('recovery.status.request' if action=='status' else 'recovery.complete.request' if action=='complete' else 'recovery.issue.request',proof_id=verified['proof_id'],case_id=case,op_id=op)
        issue=actor_proof('issue',counter);b.execute(connection,issue);delivery=b.finish_wait(wait);r['server_nonce']=delivery['server_nonce']
        start=b.receiver(signed_receiver(private,r,case,1,'factor_start',cap=delivery['cap'],target_password=PASSWORD),'factor_start');r['server_nonce']=start['server_nonce']
        b.receiver(signed_receiver(private,r,case,1,'factor_confirm',cap=delivery['cap'],pending_factor_id=start['pending_factor_id'],code=pyotp.TOTP(start['totp_seed']).now()),'factor_confirm')
        complete=actor_proof('complete',counter+1);assert b.execute(connection,complete)['stage']=='completed'
        while int(time.time())//30<=counter:time.sleep(.05)
        status=actor_proof('status',counter+2,op=complete['op_id'])
        for _ in range(2):assert b.execute(connection,status,status=True)['result']=='committed'
        with a.pg.admin.begin() as sql:sql.execute(sa.text('UPDATE identity_control.recovery_permissions SET enabled=false WHERE deployment_id=:d AND actor_id=:u'),dict(d=c.body['deployment_id'],u=actor))
        with pytest.raises(AuthRejected):b.execute(connection,status,status=True)


@pytest.mark.parametrize('fault',['reserved','pg_committed','published'])
def test_restart_rolls_only_forward_through_the_original_registration_transition(control,fault):
    from classroom_web.application.recovery_maintenance import AcceptedTransition,apply_change
    c=control;old=copy.deepcopy(c.body);new=copy.deepcopy(old);new['version']=2;new['previous_manifest_hash']=control_hash(old);new['authority']['enabled']=False
    now=datetime.now(timezone.utc);expires=now+timedelta(seconds=5 if fault=='pg_committed' else 120)
    body=dict(deployment_id=old['deployment_id'],authority_id=old['authority']['authority_id'],action='revoke',old_manifest_hash=control_hash(old),new_manifest_hash=control_hash(new),old_version=1,new_version=2,
        old_binding_hash=control_hash(old['authority']),new_binding_hash=control_hash(new['authority']),approval_nonce=random_token(),issued_at=wire_time(now),expires_at=wire_time(expires),maintainer_ref=old['maintainer_ref'],external_approval_ref='synthetic-forward-only')
    request=message('d0.change.request',approval=message('d0.change_approval',body=body,signature=c.sign(1,'classroom.identity.change.v1',body)),new_manifest=message('d0.manifest',body=new,signature=c.sign(0,'classroom.identity.manifest.v1',new)),new_key_proofs=[])
    class External:
        transition=AcceptedTransition(str(uuid4()),old,request,str(uuid4()),c.verifier.root);failed=False
        def reserve(self,request,*,peer,deadline):
            assert request==self.transition.request and peer['subject']==process_sid()
            c.reader.state='transition_reserved';c.reader.transition_id=self.transition.transition_id;c.reader.high_water=new
            if fault=='reserved' and not self.failed:self.failed=True;raise RuntimeError('synthetic reservation reply lost')
            return self.transition
        def resume(self,request,*,peer,deadline):
            assert request==self.transition.request and peer['subject']==process_sid()
            return self.transition
        def transition_current(self,tid,nonce):
            assert tid==self.transition.transition_id
            return c.reader.read_current(nonce)[1]
        def publish(self,transition):
            assert transition==self.transition
            if fault=='pg_committed' and not self.failed:self.failed=True;raise RuntimeError('synthetic publication unavailable')
            c.body.clear();c.body.update(copy.deepcopy(new));c.reader.state='ready';c.reader.transition_id=None
            if fault=='published' and not self.failed:self.failed=True;raise RuntimeError('synthetic publication reply lost')
            return c.verifier.root
    external=External()
    with real_peer() as peer:
        with pytest.raises(RuntimeError):apply_change(c.broker,peer,request,external=external)
        assert c.broker.closed
        with c.a.pg.admin.connect() as sql:
            count=sql.scalar(sa.text('SELECT count(*) FROM identity_control.registration_transitions WHERE deployment_id=:d'),dict(d=old['deployment_id']))
            assert count==(0 if fault=='reserved' else 1)
        c.broker.close()
        if fault=='pg_committed':
            while datetime.now(timezone.utc)<=expires:time.sleep(.05)
        lease=GlobalBrokerLease(old['deployment_id'],allowed_sids=[process_sid()])
        c.broker=RecoveryBroker(repository=c.repo,authority_verifier=c.verifier,instance_lease=lease,credentials=c.a.crypto,hash_gate=c.a.service.hash_gate,auth_service=c.a.service,verification_registry=c.registry,maintenance_start=True)
        assert c.broker.closed
        result=apply_change(c.broker,peer,request,external=external,resume=True)
        assert result['phase']=='published_ready'
        with c.a.pg.admin.connect() as sql:
            assert sql.scalar(sa.text('SELECT version FROM identity_control.identity_deployment_guards WHERE deployment_id=:d'),dict(d=old['deployment_id']))==2
            assert sql.scalar(sa.text('SELECT count(*) FROM identity_control.registration_transitions WHERE deployment_id=:d'),dict(d=old['deployment_id']))==1


def test_real_private_pipe_dispatch_authenticates_kernel_peer_and_read_only_status(control):
    from classroom_web.entrypoints.recovery_control import LocalControlServer,LocalControlClient
    c=control;name='ClassroomOSIdentity-dispatch-'+uuid4().hex;errors=[];results=[]
    server=LocalControlServer(broker=c.broker,name=name,allowed_sids=[process_sid()])
    def tool():
        try:
            with LocalControlClient(name=name,server_pid=os.getpid(),server_sid=process_sid(),schema=c.broker.schema) as client:
                hello=client.exchange(message('identity_control.hello.request',deployment_id=c.body['deployment_id'],authentication_mode='authority'))
                signature=c.sign(2,'classroom.identity.authenticate.v1',dict(hello=hello,peer=dict(provider='windows_sid',subject=process_sid())))
                authenticated=client.exchange(message('identity_control.authenticate.request',authentication_challenge_id=hello['authentication_challenge_id'],evidence=dict(evidence_type='authority_signature',signature=signature)))
                assert authenticated['kind']=='identity_control.authenticate.response'
                case,op=str(uuid4()),str(uuid4())
                challenge=client.exchange(message('recovery.proof.challenge.request',deployment_id=c.body['deployment_id'],case_id=case,op_id=op,action='status',receiver_id=None,expected_flow_generation=None))['challenge']
                verified=client.approve_authority(challenge,confirm_plan=lambda plan:plan['action']=='status',signer=lambda domain,body:c.sign(2,domain,body))
                result=client.exchange(message('recovery.status.request',case_id=case,op_id=op,proof_id=verified['proof_id']));results.append(result['result'])
        except Exception as error:errors.append(type(error).__name__)
    thread=threading.Thread(target=tool);thread.start()
    try:server.serve_once(5);thread.join(10)
    finally:server.close()
    assert not thread.is_alive() and not errors and results==['unknown']


def test_receiver_gateway_rejects_control_messages_and_cross_origin_before_database_write(control):
    from fastapi.testclient import TestClient
    from classroom_web.entrypoints.recovery_receiver import create_receiver_app
    c=control;_,registration_key=new_receiver(c);private=ec.generate_private_key(ec.SECP256R1());point=private.public_key().public_numbers()
    jwk=dict(kty='EC',crv='P-256',x=b64(point.x.to_bytes(32)),y=b64(point.y.to_bytes(32)))
    csrf=c.a.client.get(BASE+'/auth/csrf').json()['csrf_token'];app=create_receiver_app(broker=c.broker,origin='https://testserver')
    with TestClient(app,base_url='https://testserver') as receiver:
        receiver.cookies.set('__Host-classroom_preauth',c.a.client.cookies.get('__Host-classroom_preauth'))
        body=message('receiver.register.request',public_key=jwk,client_nonce=random_token())
        headers={'Origin':'https://testserver','Sec-Fetch-Site':'same-origin','X-CSRF-Token':csrf}
        before=len(c.broker.receivers)
        assert receiver.post('/identity-receiver',json=body,headers={**headers,'Origin':'https://evil.test'}).status_code==403
        assert len(c.broker.receivers)==before
        result=receiver.post('/identity-receiver',json=body,headers=headers);assert result.status_code==200
        assert result.json()['kind']=='receiver.register.response' and 'cap' not in result.json()
        cookie=result.headers['set-cookie'];assert 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=strict' in cookie and 'Domain' not in cookie
        forged=message('recovery.complete.request',proof_id=str(uuid4()),case_id=str(uuid4()),op_id=str(uuid4()))
        result=receiver.post('/identity-receiver',json=forged,headers={**headers,'X-CSRF-Token':result.headers['x-receiver-csrf']})
        assert result.status_code==403 and result.json()['code']=='denied'

