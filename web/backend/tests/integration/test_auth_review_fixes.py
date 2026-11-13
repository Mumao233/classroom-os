"""Regressions for the 2026-11-12 Astra review of PR-03b, on real disposable PostgreSQL."""
import hashlib,threading
from uuid import uuid4

import sqlalchemy as sa
from fastapi.testclient import TestClient

from test_authentication import auth,post,login,install_factor,PASSWORD,BASE

WRONG='wrong-synthetic-password'
HEADERS={'Origin':'https://testserver','Sec-Fetch-Site':'same-origin'}


def _attempt(a,password):
    a.client.cookies.clear();csrf=a.client.get(BASE+'/auth/csrf').json()['csrf_token']
    return post(a,'/auth/login',dict(email=a.email,password=password,remember_me=False,device_name='budget'),csrf).status_code


def test_concurrent_wrong_passwords_cannot_exceed_the_account_failure_budget(auth,monkeypatch):
    a=auth;calls=[];verify=a.crypto.verify;guard=threading.Lock()
    def counted(*args):
        with guard:calls.append(True)
        return verify(*args)
    monkeypatch.setattr(a.crypto,'verify',counted)
    burst=8;barrier=threading.Barrier(burst);statuses=[None]*burst
    def worker(index):
        # Separate cookie jars: every request has its own valid pre-authentication context.
        client=TestClient(a.client.app,base_url='https://testserver')
        csrf=client.get(BASE+'/auth/csrf').json()['csrf_token'];barrier.wait(15)
        statuses[index]=client.post(BASE+'/auth/login',json=dict(email=a.email,password=WRONG,remember_me=False,device_name='burst'),
            headers={**HEADERS,'X-CSRF-Token':csrf,'Idempotency-Key':str(uuid4())}).status_code
    threads=[threading.Thread(target=worker,args=(i,)) for i in range(burst)]
    for t in threads:t.start()
    for t in threads:t.join(30)
    # The budget is three failures. Checking before and counting after verification let all
    # eight through; reserving first admits exactly three password verifications.
    assert sorted(statuses)==[401]*3+[429]*(burst-3),statuses
    assert len(calls)==3
    assert _attempt(a,PASSWORD)==429


def test_a_successful_login_returns_only_its_own_reservation(auth):
    a=auth
    assert _attempt(a,WRONG)==401 and _attempt(a,WRONG)==401
    assert _attempt(a,PASSWORD)==200          # allowed with one slot left, and does not consume it
    assert _attempt(a,WRONG)==401             # the third failure is still available
    assert _attempt(a,PASSWORD)==429          # and success never reset the two earlier failures


def test_session_and_device_lists_stay_within_the_contract_cap(auth):
    a=auth;current=login(a)
    with a.pg.admin.begin() as c:
        # History that is newer than the current login, so a plain "newest 100" would drop it.
        c.execute(sa.text("INSERT INTO devices(id,owner_id,device_name,revoked_at,created_at) SELECT gen_random_uuid(),:u,'old device '||g,clock_timestamp(),"
                          "clock_timestamp()+make_interval(secs=>g) FROM generate_series(1,120) g"),dict(u=a.uid))
        c.execute(sa.text("INSERT INTO auth_sessions(id,owner_id,device_id,token_hash,logical_session_id,security_epoch,role,mfa_level,idle_deadline,absolute_deadline,revoked_at,created_at) "
                          "SELECT gen_random_uuid(),d.owner_id,d.id,encode(sha256(convert_to(d.id::text,'UTF8')),'hex'),gen_random_uuid(),1,'student','none',"
                          "clock_timestamp()+interval '1 hour',clock_timestamp()+interval '2 hours',clock_timestamp(),d.created_at FROM devices d WHERE d.owner_id=:u AND d.device_name LIKE 'old device %'"),dict(u=a.uid))
    sessions=a.client.get(BASE+'/me/sessions');assert sessions.status_code==200,sessions.text
    items=sessions.json()['items'];assert len(items)==100
    assert [item['is_current'] for item in items].count(True)==1
    devices=a.client.get(BASE+'/me/devices');assert devices.status_code==200,devices.text
    items=devices.json()['items'];assert len(items)==100
    assert current['device_id'] in {item['id'] for item in items}


def test_pending_automatic_recovery_can_be_abandoned_so_login_can_restart(auth):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    import rfc8785
    from classroom_web.infrastructure.auth_crypto import b64,random_token,receiver_key
    a=auth;recovery_code=random_token();install_factor(a,code=recovery_code)
    private=ec.generate_private_key(ec.SECP256R1());numbers=private.public_key().public_numbers()
    jwk=dict(kty='EC',crv='P-256',x=b64(numbers.x.to_bytes(32)),y=b64(numbers.y.to_bytes(32)));_,thumb=receiver_key(jwk)
    csrf=a.client.get(BASE+'/auth/csrf').json()['csrf_token']
    credentials=dict(email=a.email,password=PASSWORD,remember_me=False,device_name='Recovery device')
    challenge=post(a,'/auth/login',credentials,csrf).json();key=str(uuid4());path='/auth/login/mfa'
    body=dict(factor_type='recovery_code',challenge_id=challenge['challenge_id'],code=recovery_code,receiver_public_key=jwk)
    message='\n'.join(('ClassroomOS.Web.ReceiverPoP.v1','POST',BASE+path,key,'login_challenge',challenge['challenge_id'],thumb,challenge['receiver_challenge'],
                       hashlib.sha256(rfc8785.dumps(body)).hexdigest())).encode('utf8')
    r,s=decode_dss_signature(private.sign(message,ec.ECDSA(hashes.SHA256())))
    body['receiver_proof']=dict(challenge=challenge['receiver_challenge'],signature=b64(r.to_bytes(32)+s.to_bytes(32)))
    r=post(a,path,body,csrf,**{'Idempotency-Key':key});assert r.status_code==200 and r.json()['state']=='recovery_required',r.text
    flow=r.json()['recovery']

    # The stuck state: the browser has dropped its receiver key, but the HttpOnly recovery
    # cookie keeps every bootstrap in recovery scope, where a normal login is refused.
    assert a.client.get(BASE+'/auth/csrf').json()['csrf_token']==flow['csrf_token']
    assert post(a,'/auth/login',credentials,flow['csrf_token']).status_code==403
    # Abandoning needs the recovery-scoped CSRF token, not merely the cookie.
    assert post(a,'/auth/logout',{},csrf).status_code==403
    assert a.client.get(BASE+'/auth/csrf').json()['csrf_token']==flow['csrf_token']

    r=post(a,'/auth/logout',{},flow['csrf_token']);assert r.status_code==200,r.text
    assert not a.client.cookies.get('__Host-classroom_recovery')
    fresh=a.client.get(BASE+'/auth/csrf').json()['csrf_token'];assert fresh!=flow['csrf_token']
    r=post(a,'/auth/login',credentials,fresh);assert r.status_code==200 and r.json()['state']=='mfa_required',r.text
    with a.pg.admin.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM auth_challenges WHERE kind='auto_recovery' AND consumed_at IS NULL"))==0
