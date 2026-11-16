"""Formal PR-03b on a real disposable PG database and independent logins."""
import hashlib,json,secrets
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest,sqlalchemy as sa,pyotp
from fastapi.testclient import TestClient

from classroom_web.application.auth import AuthService
from classroom_web.infrastructure.auth_repository import PgAuthRepository,VERIFIER,SESSION
from classroom_web.infrastructure.auth_crypto import Credentials,LocalPasswordPolicy,ListApproval
from classroom_web.infrastructure.auth_admission import HashAdmission
from classroom_web.infrastructure.ws_admission import WebSocketAdmission
from classroom_web.entrypoints.http import create_app
from classroom_web.application.course_read import CourseReader
from classroom_web.domain.auth import AuthRejected
from classroom_web.test_support import SYNTHETIC_PASSWORD

PASSWORD='Synthetic-auth-only-20261110'
BASE='/api/web/v1'


@pytest.fixture
def auth(pg,tmp_path):
    marker=uuid4().hex[:12];vlogin='pr02a_auth_'+marker;slogin='pr02a_session_'+marker
    uid=uuid4();crypto=Credentials(encryption_key=secrets.token_bytes(32),commitment_key=secrets.token_bytes(32))
    with pg.admin.begin() as c:
        for login,functions in ((vlogin,VERIFIER),(slogin,SESSION)):
            c.exec_driver_sql(f'CREATE ROLE "{login}" LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
            c.exec_driver_sql(f'GRANT USAGE ON SCHEMA identity_api TO "{login}"')
            for f in functions:c.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION identity_api.{f}(jsonb) TO "{login}"')
        c.execute(sa.text("INSERT INTO users(id,normalized_email,password_hash,role,state,email_verified_at) VALUES(:id,:email,:phc,'student','active',clock_timestamp())"),dict(id=uid,email=marker+'@example.test',phc=crypto.hash(PASSWORD)))
    repository=PgAuthRepository(verifier_url=pg.admin_url.set(username=vlogin,password=SYNTHETIC_PASSWORD).render_as_string(hide_password=False),
                                session_url=pg.admin_url.set(username=slogin,password=SYNTHETIC_PASSWORD).render_as_string(hide_password=False))
    p=tmp_path/'list.txt';p.write_text(hashlib.sha256(b'weak12').hexdigest()+'\n',encoding='ascii')
    policy=LocalPasswordPolicy(p,ListApproval(hashlib.sha256(p.read_bytes()).hexdigest(),datetime.now(timezone.utc)+timedelta(hours=1)))
    service=AuthService(repository,crypto,policy,hash_gate=HashAdmission(tmp_path/'hash-slots'))
    contract=json.loads((Path(__file__).resolve().parents[4]/'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf8'))
    ws=WebSocketAdmission(tmp_path,crypto)
    app=create_app(auth=service,contract=contract,origin='https://testserver',websocket_admission=ws,business=CourseReader(auth=service,database=pg.db))
    with TestClient(app,base_url='https://testserver') as client:
        yield SimpleNamespace(client=client,service=service,repository=repository,uid=uid,email=marker+'@example.test',crypto=crypto,pg=pg,policy=policy,ws_admission=ws)
    repository.dispose()
    with pg.admin.begin() as c:
        for login in (vlogin,slogin):c.exec_driver_sql(f'DROP OWNED BY "{login}"');c.exec_driver_sql(f'DROP ROLE "{login}"')


def post(a,path,body,csrf,**headers):
    return a.client.post(BASE+path,json=body,headers={'Origin':'https://testserver','Sec-Fetch-Site':'same-origin','X-CSRF-Token':csrf,'Idempotency-Key':str(uuid4()),**headers})


def login(a):
    csrf=a.client.get(BASE+'/auth/csrf');assert csrf.status_code==200,csrf.text
    r=post(a,'/auth/login',dict(email=a.email,password=PASSWORD,remember_me=False,device_name='Synthetic device'),csrf.json()['csrf_token'])
    assert r.status_code==200,r.text
    assert r.json()['state']=='authenticated'
    return r.json()


def test_real_login_cookie_context_logout_and_revoked_cookie(auth):
    a=auth;result=login(a);cookie=a.client.cookies.get('__Host-classroom_session')
    r=a.client.get(BASE+'/auth/session');assert r.status_code==200,r.text
    assert r.json()['user']['id']==str(a.uid) and r.json()['csrf_token']==result['csrf_token']
    r=post(a,'/auth/logout',{},result['csrf_token']);assert r.status_code==200,r.text
    a.client.cookies.set('__Host-classroom_session',cookie)
    assert a.client.get(BASE+'/auth/session').status_code==401


def test_protocol_defenses_reject_before_hash_and_never_echo_secrets(auth):
    a=auth;r=a.client.get(BASE+'/auth/csrf');csrf=r.json()['csrf_token']
    for headers in ({},{'Origin':'https://evil.test','Sec-Fetch-Site':'cross-site'}):
        r=a.client.post(BASE+'/auth/login',json={'password':'PRIVATE_SENTINEL'},headers=headers)
        assert r.status_code==403 and 'PRIVATE' not in r.text
    r=a.client.post(BASE+'/auth/login',content=b'{"password":"a","password":"b"}',headers={'Origin':'https://testserver','Sec-Fetch-Site':'same-origin','Content-Type':'application/json','Idempotency-Key':str(uuid4()),'X-CSRF-Token':csrf})
    assert r.status_code==422


def test_approved_list_missing_blocks_new_password_but_not_existing_login(auth):
    a=auth;a.policy.path.unlink();login(a)
    with pytest.raises(AuthRejected) as error:a.policy.validate('new-synthetic-password','student')
    assert error.value.code=='SERVICE_DISABLED'


def test_epoch_revocation_and_deadline_are_current_database_facts(auth):
    a=auth;login(a)
    with a.pg.admin.begin() as c:c.execute(sa.text('UPDATE users SET security_epoch=security_epoch+1 WHERE id=:id'),dict(id=a.uid))
    assert a.client.get(BASE+'/auth/session').status_code==401


def test_runtime_and_auth_logins_cannot_read_secret_tables_or_call_other_service(auth):
    a=auth
    for engine in (a.pg.db.engine,a.repository.engines['verifier'],a.repository.engines['session']):
        with engine.connect() as c:
            with pytest.raises(sa.exc.DBAPIError):c.execute(sa.text('SELECT * FROM public.auth_sessions'))
    with a.repository.engines['session'].connect() as c:
        with pytest.raises(sa.exc.DBAPIError):c.execute(sa.text("SELECT identity_api.locate_auth('{}'::jsonb)"))


def test_bound_reauth_changes_password_and_revokes_existing_session(auth):
    a=auth;l=login(a);uid=str(a.uid);key=str(uuid4());new='New-synthetic-password-123'
    operation=dict(purpose='changePassword',target=dict(kind='account',id=uid),candidate_parameters=dict(idempotency_key=key,body=dict(new_password=new)))
    r=post(a,'/auth/reauth',dict(password=PASSWORD,operation=operation),l['csrf_token']);assert r.status_code==200,r.text
    proof=r.json()['reauth_token']
    r=post(a,'/auth/password/change',dict(new_password=new+'x'),l['csrf_token'],**{'Idempotency-Key':key,'X-Reauth-Token':proof})
    assert r.status_code==403,r.text
    r=post(a,'/auth/password/change',dict(new_password=new),l['csrf_token'],**{'Idempotency-Key':key,'X-Reauth-Token':proof})
    assert r.status_code==200,r.text
    assert a.client.get(BASE+'/auth/session').status_code==401


def test_lists_match_contract_and_no_capture_or_trust_is_enabled(auth):
    a=auth;l=login(a)
    for path in ('/me/sessions','/me/devices'):
        r=a.client.get(BASE+path);assert r.status_code==200,r.text
        assert len(r.json()['items'])==1 and r.json()['next_cursor'] is None
    r=post(a,'/ws-tickets',dict(channel='capture',class_session_id=None,capture_epoch_id=None,fence_token=None),l['csrf_token'])
    assert r.status_code==503,r.text


def reauth(a,session,purpose,body,target=None,code=None):
    key=str(uuid4());kind='mfa_enrollment' if purpose=='confirmMfa' else 'account'
    value=dict(password=PASSWORD,operation=dict(purpose=purpose,target=dict(kind=kind,id=target or str(a.uid)),candidate_parameters=dict(idempotency_key=key,body=body)))
    if code:value['mfa_code']=code
    r=post(a,'/auth/reauth',value,session['csrf_token']);assert r.status_code==200,r.text
    return {'Idempotency-Key':key,'X-Reauth-Token':r.json()['reauth_token']}


def test_real_mfa_enrollment_is_once_only_and_requires_full_new_login(auth):
    a=auth;l=login(a);headers=reauth(a,l,'enrollMfa',{})
    r=post(a,'/auth/mfa/enroll',{},l['csrf_token'],**headers);assert r.status_code==200,r.text
    enrollment=r.json();seed=pyotp.parse_uri(enrollment['otpauth_uri']).secret
    replay=post(a,'/auth/mfa/enroll',{},l['csrf_token'],**headers);assert replay.status_code==409,replay.text
    body=dict(enrollment_id=enrollment['enrollment_id'],code=pyotp.TOTP(seed).now())
    headers=reauth(a,l,'confirmMfa',body,target=enrollment['enrollment_id'])
    r=post(a,'/auth/mfa/confirm',body,l['csrf_token'],**headers);assert r.status_code==200,r.text
    assert len(r.json()['codes'])==10 and len(set(r.json()['codes']))==10
    assert a.client.get(BASE+'/auth/session').status_code==401
    csrf=a.client.get(BASE+'/auth/csrf').json()['csrf_token']
    r=post(a,'/auth/login',dict(email=a.email,password=PASSWORD,remember_me=True,device_name='MFA login'),csrf)
    assert r.status_code==200 and r.json()['state']=='mfa_required',r.text
    assert r.json()['user'] is None and r.json()['device_id'] is None
    # The confirmation step has already consumed this TOTP counter. A replay
    # under a fresh challenge must not become an ordinary session.
    r=post(a,'/auth/login/mfa',dict(factor_type='totp',challenge_id=r.json()['challenge_id'],code=body['code']),csrf)
    assert r.status_code==401,(r.text,list(a.repository.faults),list(a.client.app.state.errors))


def test_admin_without_registered_factor_cannot_obtain_a_session(auth):
    a=auth
    with a.pg.admin.begin() as c:c.execute(sa.text("UPDATE users SET role='admin' WHERE id=:id"),dict(id=a.uid))
    csrf=a.client.get(BASE+'/auth/csrf').json()['csrf_token']
    r=post(a,'/auth/login',dict(email=a.email,password=PASSWORD,remember_me=True,device_name='Admin'),csrf)
    assert r.status_code==403 and r.json()['error']['code']=='MFA_REQUIRED'
    assert not a.client.cookies.get('__Host-classroom_session')


def test_ticket_is_single_use_and_intersects_both_deadlines(auth):
    from classroom_web.domain.auth import BrowserContext
    a=auth;l=login(a);r=post(a,'/ws-tickets',dict(channel='events',class_session_id=None,capture_epoch_id=None,fence_token=None),l['csrf_token'])
    assert r.status_code==201,r.text
    ctx=BrowserContext(session=a.client.cookies.get('__Host-classroom_session'))
    past=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    with pytest.raises(AuthRejected):a.service.ws_authenticate(ctx,r.json()['ticket'],'events',past)
    future=(datetime.now(timezone.utc)+timedelta(seconds=5)).isoformat()
    assert a.service.ws_authenticate(ctx,r.json()['ticket'],'events',future)['owner_id']==str(a.uid)
    with pytest.raises(AuthRejected):a.service.ws_authenticate(ctx,r.json()['ticket'],'events',future)


def install_factor(a,*,role='student',code=None):
    from classroom_web.infrastructure.auth_crypto import digest
    fid=uuid4();seed=a.crypto.new_seed()
    with a.pg.admin.begin() as c:
        c.execute(sa.text('UPDATE users SET role=:role WHERE id=:id'),dict(role=role,id=a.uid))
        c.execute(sa.text("INSERT INTO mfa_credentials(id,owner_id,state,encrypted_seed,confirmed_at) VALUES(:fid,:uid,'active',:seed,clock_timestamp())"),
                  dict(fid=fid,uid=a.uid,seed=a.crypto.seal(str(a.uid),str(fid),seed)))
        if code:
            gid=uuid4()
            c.execute(sa.text('INSERT INTO recovery_code_groups(id,owner_id,credential_id,generation,security_epoch) VALUES(:g,:u,:f,1,1)'),dict(g=gid,u=a.uid,f=fid))
            c.execute(sa.text('INSERT INTO mfa_recovery_codes(id,owner_id,group_id,code_hash) VALUES(:id,:u,:g,:h)'),dict(id=uuid4(),u=a.uid,g=gid,h=digest('recovery_code',code)))
    return fid,seed


@pytest.mark.parametrize('role',['student','admin'])
def test_real_totp_login_enforces_server_role_cookie_policy(auth,role):
    a=auth;_,seed=install_factor(a,role=role)
    csrf=a.client.get(BASE+'/auth/csrf').json()['csrf_token']
    r=post(a,'/auth/login',dict(email=a.email,password=PASSWORD,remember_me=True,device_name='MFA device'),csrf)
    assert r.status_code==200 and r.json()['state']=='mfa_required',r.text
    assert not a.client.cookies.get('__Host-classroom_session')
    assert post(a,'/auth/login/mfa',dict(factor_type='totp',challenge_id=r.json()['challenge_id'],code=wrong_totp(seed)),csrf).status_code==401
    r=post(a,'/auth/login/mfa',dict(factor_type='totp',challenge_id=r.json()['challenge_id'],code=pyotp.TOTP(seed).now()),csrf)
    assert r.status_code==200 and r.json()['state']=='authenticated',r.text
    cookie=r.headers['set-cookie']
    assert ('Max-Age' in cookie)==(role=='student')
    assert 'Secure' in cookie and 'HttpOnly' in cookie and 'Domain' not in cookie


def test_real_automatic_recovery_requires_bound_key_each_action_and_never_logs_in(auth):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    import rfc8785
    from classroom_web.infrastructure.auth_crypto import b64,random_token,receiver_key
    a=auth;recovery_code=random_token();old,_=install_factor(a,code=recovery_code)
    private=ec.generate_private_key(ec.SECP256R1());numbers=private.public_key().public_numbers()
    jwk=dict(kty='EC',crv='P-256',x=b64(numbers.x.to_bytes(32)),y=b64(numbers.y.to_bytes(32)));_,thumb=receiver_key(jwk)
    def signed(body,*,path,kind,context,nonce,key):
        message='\n'.join(('ClassroomOS.Web.ReceiverPoP.v1','POST',BASE+path,key,kind,context,thumb,nonce,hashlib.sha256(rfc8785.dumps(body)).hexdigest())).encode('utf8')
        r,s=decode_dss_signature(private.sign(message,ec.ECDSA(hashes.SHA256())))
        return {**body,'receiver_proof':dict(challenge=nonce,signature=b64(r.to_bytes(32)+s.to_bytes(32)))}
    csrf=a.client.get(BASE+'/auth/csrf').json()['csrf_token']
    r=post(a,'/auth/login',dict(email=a.email,password=PASSWORD,remember_me=False,device_name='Recovery device'),csrf)
    challenge=r.json();key=str(uuid4());path='/auth/login/mfa'
    body=signed(dict(factor_type='recovery_code',challenge_id=challenge['challenge_id'],code=recovery_code,receiver_public_key=jwk),path=path,kind='login_challenge',context=challenge['challenge_id'],nonce=challenge['receiver_challenge'],key=key)
    r=post(a,path,body,csrf,**{'Idempotency-Key':key});assert r.status_code==200,r.text
    assert r.json()['state']=='recovery_required' and r.json()['user'] is None
    flow=r.json()['recovery'];assert a.client.get(BASE+'/auth/session').status_code==401
    key=str(uuid4());path='/auth/recovery/enroll'
    body=signed(dict(flow_id=flow['flow_id'],password=PASSWORD),path=path,kind='automatic_recovery',context=flow['flow_id'],nonce=flow['receiver_challenge'],key=key)
    r=post(a,path,body,flow['csrf_token'],**{'Idempotency-Key':key});assert r.status_code==200,r.text
    pending=r.json();seed=pyotp.parse_uri(pending['otpauth_uri']).secret
    with a.pg.admin.connect() as c:assert c.scalar(sa.text('SELECT state FROM mfa_credentials WHERE id=:id'),dict(id=old))=='active'
    key=str(uuid4());path='/auth/recovery/confirm'
    body=signed(dict(flow_id=flow['flow_id'],enrollment_id=pending['enrollment_id'],password=PASSWORD,code=pyotp.TOTP(seed).now()),
                path=path,kind='automatic_recovery',context=flow['flow_id'],nonce=pending['receiver_challenge'],key=key)
    r=post(a,path,body,flow['csrf_token'],**{'Idempotency-Key':key});assert r.status_code==200,r.text
    assert r.json()==dict(state='completed',flow_id=flow['flow_id'],requires_full_login=True,new_recovery_codes_issued=False)
    assert not a.client.cookies.get('__Host-classroom_session') and not a.client.cookies.get('__Host-classroom_recovery')
    with a.pg.admin.connect() as c:
        assert c.scalar(sa.text('SELECT security_epoch FROM users WHERE id=:id'),dict(id=a.uid))==2
        assert c.scalar(sa.text('SELECT count(*) FROM recovery_code_groups WHERE owner_id=:id AND revoked_at IS NULL'),dict(id=a.uid))==0


def test_runtime_transaction_rechecks_real_session_after_principal_was_issued(auth):
    from dataclasses import replace
    from classroom_web.domain.auth import BrowserContext
    from classroom_web.domain.identity import AuthorizationDenied
    a=auth;value=login(a);ctx=BrowserContext(session=a.client.cookies.get('__Host-classroom_session'))
    principal=a.service.principal(ctx)
    with a.pg.db.authorized_transaction(principal) as tx:semester=tx.catalog.create_semester('Real auth owner bridge')
    with pytest.raises(AuthorizationDenied):
        with a.pg.db.authorized_transaction(replace(principal,session_id=uuid4())):pass
    assert post(a,'/auth/logout',{},value['csrf_token']).status_code==200
    with pytest.raises(AuthorizationDenied):
        with a.pg.db.authorized_transaction(principal):pass


def test_session_deadline_is_rechecked_after_real_database_lock_wait(auth):
    from concurrent.futures import ThreadPoolExecutor
    import time
    from classroom_web.domain.auth import BrowserContext
    a=auth;login(a);ctx=BrowserContext(session=a.client.cookies.get('__Host-classroom_session'))
    with a.pg.admin.begin() as c:c.execute(sa.text("UPDATE auth_sessions SET idle_deadline=clock_timestamp()+interval '100 milliseconds' WHERE owner_id=:u"),dict(u=a.uid))
    with a.pg.admin.connect() as c:
        tx=c.begin();c.execute(sa.text('SELECT id FROM auth_sessions WHERE owner_id=:u FOR UPDATE'),dict(u=a.uid))
        with ThreadPoolExecutor(max_workers=1) as worker:
            result=worker.submit(a.service.context,ctx);time.sleep(.2);tx.commit()
            with pytest.raises(AuthRejected):result.result(timeout=2)


def test_three_failure_budget_is_shared_across_fresh_protocol_contexts(auth,monkeypatch):
    a=auth;calls=[];verify=a.crypto.verify
    def counted(*args):calls.append(True);return verify(*args)
    monkeypatch.setattr(a.crypto,'verify',counted)
    for index in range(4):
        a.client.cookies.clear();csrf=a.client.get(BASE+'/auth/csrf').json()['csrf_token']
        r=post(a,'/auth/login',dict(email=a.email,password='wrong-synthetic-password',remember_me=False,device_name='failure control'),csrf)
        assert r.status_code==(401 if index<3 else 429),r.text
    assert len(calls)==3


def test_additive_auth_migration_has_one_head_repeat_and_forced_RLS(auth):
    from alembic import command
    from alembic.script import ScriptDirectory
    a=auth
    scripts=ScriptDirectory.from_config(a.pg.config)
    assert scripts.get_heads()==['pr07a_001']
    assert scripts.get_revision('pr07a_001').down_revision=='0009_private_media'
    assert scripts.get_revision('0009_private_media').down_revision=='0008_manual_recovery'
    for _ in range(2):command.upgrade(a.pg.config,'head')
    with a.pg.admin.connect() as c:
        assert c.scalar(sa.text('SELECT version_num FROM alembic_version'))=='pr07a_001'
        rows=c.execute(sa.text("SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname IN ('devices','auth_sessions','auth_challenges','reauth_proofs','mfa_credentials','recovery_code_groups','mfa_recovery_codes','email_actions','preauth_receipts')")).all()
        assert len(rows)==9 and all(enabled and forced for _,enabled,forced in rows)
        assert c.scalar(sa.text("SELECT count(*) FROM pg_roles WHERE rolname IN ('classroom_web_auth_verifier','classroom_web_session_writer','classroom_web_identity_table_owner') AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolinherit)"))==0


def test_idle_websocket_checks_never_extend_idle_or_absolute_deadlines(auth):
    from classroom_web.domain.auth import BrowserContext
    a=auth;login(a);ctx=BrowserContext(session=a.client.cookies.get('__Host-classroom_session'))
    def deadlines():
        with a.pg.admin.connect() as c:return c.execute(sa.text('SELECT idle_deadline,absolute_deadline FROM auth_sessions WHERE owner_id=:u'),dict(u=a.uid)).one()
    before=deadlines()
    for _ in range(2):a.service.ws_current(ctx)
    assert deadlines()==before
    a.service.context(ctx,touch=True);after=deadlines()
    assert after[0]>before[0] and after[1]==before[1]


def test_expired_ingress_rolls_back_real_SQL_before_commit(auth):
    import time
    from classroom_web.infrastructure.auth_deadlines import DEADLINE
    from classroom_web.infrastructure.auth_crypto import digest,random_token
    a=auth;identifier=uuid4();token=DEADLINE.set(time.monotonic()+.05)
    try:
        with pytest.raises(AuthRejected) as error:
            a.repository.call('protocol_context',dict(action='create',id=str(identifier),token_hash=digest('preauth',random_token())),before_commit=lambda:time.sleep(.075))
        assert error.value.code=='RATE_LIMITED'
    finally:DEADLINE.reset(token)
    with a.pg.admin.connect() as c:assert c.scalar(sa.text('SELECT count(*) FROM auth_challenges WHERE id=:id'),dict(id=identifier))==0


def test_course_read_uses_current_session_and_touches_only_success(auth):
    from classroom_web.domain.data import OwnerContext
    a=auth;login(a)
    with a.pg.db.transaction(OwnerContext(a.uid)) as tx:
        semester=tx.catalog.create_semester('Synthetic owner only');course=tx.catalog.create_course(semester.id,'Synthetic protected read')
    def idle():
        with a.pg.admin.connect() as sql:return sql.scalar(sa.text('SELECT idle_deadline FROM auth_sessions WHERE owner_id=:u'),dict(u=a.uid))
    before=idle()
    for _ in range(2):assert a.client.get(BASE+'/auth/session').status_code==200
    assert idle()==before
    assert a.client.get(BASE+'/courses/'+str(uuid4())).status_code==404
    assert a.client.get(BASE+'/courses/'+str(course.id)+'?revision=2').status_code==409
    assert idle()==before
    r=a.client.get(BASE+'/courses/'+str(course.id));assert r.status_code==200,r.text
    assert r.json()['id']==str(course.id) and r.json()['class_session_count']==0 and idle()>before
    assert a.client.get(BASE+'/courses/'+str(course.id)+'?revision=1&revision=1').status_code==422
    with a.pg.admin.begin() as sql:sql.execute(sa.text('UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE owner_id=:u'),dict(u=a.uid))
    assert a.client.get(BASE+'/courses/'+str(course.id)).status_code==401


def test_expired_cookie_can_bootstrap_anonymous_without_reviving_authority(auth):
    a=auth;login(a)
    with a.pg.admin.begin() as sql:sql.execute(sa.text('UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE owner_id=:u'),dict(u=a.uid))
    assert a.client.get(BASE+'/auth/session').status_code==401
    result=a.client.get(BASE+'/auth/csrf');assert result.status_code==200
    assert not a.client.cookies.get('__Host-classroom_session') and a.client.cookies.get('__Host-classroom_preauth')
    assert a.client.get(BASE+'/auth/session').status_code==401


def test_identity_notice_claim_never_follows_user_lock_and_delivery_is_after_commit(auth):
    from concurrent.futures import ThreadPoolExecutor
    from classroom_web.infrastructure.identity_notices import PgIdentityNoticeRepository,IdentityNoticeDispatcher
    a=auth;role='pr02a_notice_'+uuid4().hex[:12];identifier=uuid4()
    with a.pg.admin.begin() as sql:
        sql.exec_driver_sql(f'CREATE ROLE "{role}" LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
        sql.exec_driver_sql(f'GRANT USAGE ON SCHEMA identity_api TO "{role}"')
        sql.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION identity_api.claim_identity_notices(jsonb),identity_api.finish_identity_notice(jsonb) TO "{role}"')
        sql.execute(sa.text('INSERT INTO identity_control.identity_revocation_notices(id,subject_id,security_epoch) VALUES(:i,:u,1)'),dict(i=identifier,u=a.uid))
    repo=PgIdentityNoticeRepository(a.pg.admin_url.set(username=role,password=SYNTHETIC_PASSWORD).render_as_string(hide_password=False));observed=[]
    try:
        with a.pg.admin.connect() as sql:
            transaction=sql.begin();sql.execute(sa.text('SELECT id FROM users WHERE id=:u FOR UPDATE'),dict(u=a.uid))
            with ThreadPoolExecutor(max_workers=1) as executor:
                future=executor.submit(repo.call,'claim_identity_notices',dict(limit=32,claim_hash='a'*64))
                batch=future.result(timeout=2)
                assert any(event['id']==str(identifier) for event in batch['items'])
            transaction.rollback()
        with a.pg.admin.begin() as sql:sql.execute(sa.text('UPDATE identity_control.identity_revocation_notices SET claim_until=clock_timestamp()-interval \'1 second\' WHERE id=:i'),dict(i=identifier))
        def deliver(event):
            if event['id']==str(identifier):
                # Independent transaction can take event UPDATE and user SHARE
                # simultaneously: dispatcher no longer holds its claim lock.
                with a.pg.admin.begin() as sql:
                    sql.exec_driver_sql("SET LOCAL lock_timeout='1s'")
                    sql.execute(sa.text('SELECT id FROM identity_control.identity_revocation_notices WHERE id=:i FOR UPDATE'),dict(i=identifier))
                    sql.execute(sa.text('SELECT id FROM users WHERE id=:u FOR SHARE'),dict(u=a.uid))
                observed.append(True)
            return True
        result=IdentityNoticeDispatcher(repo,deliver).dispatch_once();assert result['failed']==0 and observed==[True]
        with a.pg.admin.connect() as sql:assert sql.scalar(sa.text('SELECT delivered_at IS NOT NULL FROM identity_control.identity_revocation_notices WHERE id=:i'),dict(i=identifier))
    finally:
        repo.dispose()
        with a.pg.admin.begin() as sql:sql.exec_driver_sql(f'DROP OWNED BY "{role}"');sql.exec_driver_sql(f'DROP ROLE "{role}"')


def test_malformed_preauth_cookie_bootstraps_fresh_anonymous_context(auth):
    a=auth
    response=a.client.get(BASE+'/auth/csrf',headers={'Cookie':'__Host-classroom_preauth=malformed'})
    assert response.status_code==200 and response.json()['csrf_token']
    assert 'Set-Cookie' in response.headers
    assert a.client.get(BASE+'/auth/session').status_code==401


def mfa_login(a,code):
    csrf=a.client.get(BASE+'/auth/csrf').json()['csrf_token']
    response=post(a,'/auth/login',dict(email=a.email,password=PASSWORD,remember_me=False,device_name='MFA regression'),csrf)
    assert response.status_code==200 and response.json()['state']=='mfa_required'
    challenge=response.json()['challenge_id']
    response=post(a,'/auth/login/mfa',dict(factor_type='totp',challenge_id=challenge,code=code),csrf)
    assert response.status_code==200 and response.json()['state']=='authenticated'
    return response.json(),a.client.cookies.get('__Host-classroom_session')


def wrong_totp(seed):
    now=datetime.now(timezone.utc);step=int(now.timestamp())//30
    used={pyotp.TOTP(seed).at(n*30) for n in range(step-1,step+2)}
    return next(f'{n:06d}' for n in range(10) if f'{n:06d}' not in used)


def mfa_reauth_body(a,code=None):
    body=dict(password=PASSWORD,operation=dict(purpose='changePassword',target=dict(kind='account',id=str(a.uid)),candidate_parameters=dict(idempotency_key=str(uuid4()),body=dict(new_password='Synthetic-candidate-not-committed'))))
    if code is not None:body['mfa_code']=code
    return body


@pytest.mark.parametrize('role',['student','admin'])
def test_mfa_failures_share_one_real_PG_budget_across_login_sessions_and_service_pools(auth,role,monkeypatch):
    a=auth;_,seed=install_factor(a,role=role);step=int(datetime.now(timezone.utc).timestamp())//30
    used_code=pyotp.TOTP(seed).at(step*30);first,cookie1=mfa_login(a,used_code)
    a.client.cookies.clear();second,cookie2=mfa_login(a,pyotp.TOTP(seed).at((step+1)*30))
    a.client.cookies.clear();csrf=a.client.get(BASE+'/auth/csrf').json()['csrf_token']
    challenge=post(a,'/auth/login',dict(email=a.email,password=PASSWORD,remember_me=False,device_name='Pending challenge'),csrf).json()['challenge_id']
    bad=wrong_totp(seed)
    for _ in range(2):assert post(a,'/auth/login/mfa',dict(factor_type='totp',challenge_id=challenge,code=bad),csrf).status_code==401
    repo=PgAuthRepository(verifier_url=a.repository.engines['verifier'].url.render_as_string(hide_password=False),session_url=a.repository.engines['session'].url.render_as_string(hide_password=False))
    service=AuthService(repo,a.crypto,a.policy,hash_gate=a.service.hash_gate)
    contract=json.loads((Path(__file__).resolve().parents[4]/'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf8'))
    try:
        with TestClient(create_app(auth=service,contract=contract,origin='https://testserver',websocket_admission=a.ws_admission),base_url='https://testserver') as client:
            client.cookies.set('__Host-classroom_session',cookie2)
            for code in (None,bad,used_code):
                result=client.post(BASE+'/auth/reauth',json=mfa_reauth_body(a,code),headers={'Origin':'https://testserver','Sec-Fetch-Site':'same-origin','X-CSRF-Token':second['csrf_token'],'Idempotency-Key':str(uuid4())})
                assert result.status_code==401
            key=a.crypto.commitment('budget:mfa_fail',str(a.uid))
            with a.pg.admin.connect() as sql:
                assert sql.scalar(sa.text('SELECT n FROM identity_control.authentication_budgets WHERE budget_key=:k'),dict(k=key))==5
            called=[];original=a.crypto.totp_step
            monkeypatch.setattr(a.crypto,'totp_step',lambda *args:(called.append(True),original(*args))[1])
            for session,cookie in ((first,cookie1),(second,cookie2)):
                client.cookies.set('__Host-classroom_session',cookie)
                result=client.post(BASE+'/auth/reauth',json=mfa_reauth_body(a,used_code),headers={'Origin':'https://testserver','Sec-Fetch-Site':'same-origin','X-CSRF-Token':session['csrf_token'],'Idempotency-Key':str(uuid4())})
                assert result.status_code==429
            for _ in range(2):
                pending=post(a,'/auth/login',dict(email=a.email,password=PASSWORD,remember_me=False,device_name='Fresh limited challenge'),csrf)
                assert pending.status_code==200 and pending.json()['state']=='mfa_required'
                assert post(a,'/auth/login/mfa',dict(factor_type='totp',challenge_id=pending.json()['challenge_id'],code=used_code),csrf).status_code==429
            assert not called
            with a.pg.admin.connect() as sql:
                assert sql.scalar(sa.text('SELECT n FROM identity_control.authentication_budgets WHERE budget_key=:k'),dict(k=key))==5
    finally:repo.dispose()


@pytest.mark.parametrize('role',['student','admin'])
def test_mfa_reauth_wrong_then_valid_next_code_keeps_session_and_does_not_reset_budget(auth,role):
    a=auth;fid,seed=install_factor(a,role=role);step=int(datetime.now(timezone.utc).timestamp())//30
    session,_=mfa_login(a,pyotp.TOTP(seed).at(step*30))
    assert post(a,'/auth/reauth',mfa_reauth_body(a,wrong_totp(seed)),session['csrf_token']).status_code==401
    assert a.client.get(BASE+'/auth/session').status_code==200
    with a.pg.admin.connect() as sql:
        now,last=sql.execute(sa.text('SELECT floor(extract(epoch FROM clock_timestamp())/30)::bigint,last_accepted_step FROM mfa_credentials WHERE id=:f'),dict(f=fid)).one()
    code=pyotp.TOTP(seed).at(max(now,last+1)*30)
    response=post(a,'/auth/reauth',mfa_reauth_body(a,code),session['csrf_token'])
    assert response.status_code==200 and response.json()['reauth_token']
    key=a.crypto.commitment('budget:mfa_fail',str(a.uid))
    with a.pg.admin.connect() as sql:
        assert sql.scalar(sa.text('SELECT n FROM identity_control.authentication_budgets WHERE budget_key=:k'),dict(k=key))==1


def test_missing_session_cookie_write_is_403_and_readonly_probe_changes_context(auth):
    a=auth;session=login(a);a.client.cookies.clear()
    response=post(a,'/auth/reauth',mfa_reauth_body(a),session['csrf_token'])
    assert response.status_code==403 and response.json()['error']['code']=='FORBIDDEN'
    probe=a.client.get(BASE+'/auth/csrf')
    assert probe.status_code==200 and probe.json()['csrf_token']!=session['csrf_token']
    assert a.client.get(BASE+'/auth/session').status_code==401


def test_forbidden_write_can_leave_session_and_csrf_valid(auth):
    a=auth;session=login(a)
    response=post(a,'/auth/reauth',mfa_reauth_body(a),session['csrf_token'],Origin='https://forbidden.example.test')
    assert response.status_code==403
    assert a.client.get(BASE+'/auth/csrf').json()['csrf_token']==session['csrf_token']
    assert a.client.get(BASE+'/auth/session').status_code==200


def test_consumed_mfa_challenge_can_restart_login_with_same_preauth_without_resetting_budget(auth):
    a=auth;_,seed=install_factor(a);csrf=a.client.get(BASE+'/auth/csrf').json()['csrf_token']
    body=dict(email=a.email,password=PASSWORD,remember_me=False,device_name='Restart regression')
    pending=post(a,'/auth/login',body,csrf).json()
    assert pending['state']=='mfa_required'
    assert post(a,'/auth/login/mfa',dict(factor_type='totp',challenge_id=pending['challenge_id'],code=wrong_totp(seed)),csrf).status_code==401
    with a.pg.admin.begin() as sql:
        sql.execute(sa.text('UPDATE users SET security_epoch=security_epoch+1 WHERE id=:u'),dict(u=a.uid))
        sql.execute(sa.text('UPDATE auth_challenges SET consumed_at=clock_timestamp() WHERE owner_id=:u'),dict(u=a.uid))
    response=post(a,'/auth/login/mfa',dict(factor_type='totp',challenge_id=pending['challenge_id'],code=pyotp.TOTP(seed).now()),csrf)
    assert response.status_code==401 and a.client.get(BASE+'/auth/csrf').json()['csrf_token']==csrf
    fresh=post(a,'/auth/login',body,csrf)
    assert fresh.status_code==200 and fresh.json()['state']=='mfa_required'
    assert fresh.json()['challenge_id']!=pending['challenge_id']
    key=a.crypto.commitment('budget:mfa_fail',str(a.uid))
    with a.pg.admin.connect() as sql:
        assert sql.scalar(sa.text('SELECT n FROM identity_control.authentication_budgets WHERE budget_key=:k'),dict(k=key))==1
