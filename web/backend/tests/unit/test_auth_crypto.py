"""Real crypto/encoding checks using synthetic keys and explicit local lists."""
import hashlib,json,secrets
from datetime import datetime,timedelta,timezone
from uuid import uuid4

import pytest,pyotp,rfc8785
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from classroom_web.domain.auth import AuthRejected,AuthPolicy,BrowserContext
from classroom_web.infrastructure.auth_crypto import (
    Credentials,ListApproval,LocalPasswordPolicy,b64,unb64,random_token,
    scalar_password,receiver_key,verify_receiver,
)


@pytest.fixture(scope='module')
def credentials():return Credentials(encryption_key=secrets.token_bytes(32),commitment_key=secrets.token_bytes(32))


def policy(tmp_path):
    p=tmp_path/'approved-password-digests.txt'
    p.write_text(hashlib.sha256(b'weak12').hexdigest()+'\n',encoding='ascii')
    a=ListApproval(hashlib.sha256(p.read_bytes()).hexdigest(),datetime.now(timezone.utc)+timedelta(hours=1))
    return LocalPasswordPolicy(p,a)


@pytest.mark.parametrize('value',[None,False,42,'x'*1025,'\ud800','x\udfff'])
def test_password_rejects_invalid_scalar_input_without_echo(value):
    with pytest.raises(AuthRejected) as e:scalar_password(value)
    assert str(e.value)=='VALIDATION_FAILED'


def test_password_list_is_explicit_fresh_and_unchanged_at_commit(tmp_path):
    p=policy(tmp_path)
    with pytest.raises(AuthRejected):LocalPasswordPolicy().validate('secret12','student')
    with pytest.raises(AuthRejected):p.validate('weak12','student')
    a=p.validate('new12👍','student')
    with pytest.raises(AuthRejected):p.validate('new12👍','admin')
    p.current(a)
    p.path.write_text('0'*64+'\n',encoding='ascii')
    with pytest.raises(AuthRejected) as e:p.current(a)
    assert e.value.code=='SERVICE_DISABLED'


def test_unicode_is_not_normalized_trimmed_or_truncated(credentials,tmp_path):
    p=policy(tmp_path)
    for password in ('  abcd ','é'*6,'e\u0301'*6,'👍'*1024):p.validate(password,'student')
    phc=credentials.hash('  abcd ')
    assert credentials.verify(phc,'  abcd ')
    assert not credentials.verify(phc,'abcd')
    assert not credentials.verify(phc.replace('m=65536','m=999999999'),'  abcd ')
    assert not credentials.verify(None,'  abcd ')


def test_secret_envelopes_bind_owner_and_factor(credentials):
    uid,fid=str(uuid4()),str(uuid4());seed=credentials.new_seed()
    first=credentials.seal(uid,fid,seed);second=credentials.seal(uid,fid,seed)
    assert first!=second and credentials.open(uid,fid,first)==seed
    with pytest.raises(AuthRejected):credentials.open(str(uuid4()),fid,first)
    with pytest.raises(AuthRejected):credentials.open(uid,str(uuid4()),first)


def test_totp_uses_mature_component_and_explicit_time(credentials):
    seed=credentials.new_seed();now=datetime.now(timezone.utc)
    code=pyotp.TOTP(seed).at(now)
    assert credentials.totp_step(seed,code,now)==int(now.timestamp())//30
    with pytest.raises(AuthRejected):credentials.totp_step(seed,'bad',now)


@pytest.mark.parametrize('value',['a','abc=','A'*43+'=','+/'*22,'A'*257,'',None])
def test_noncanonical_tokens_are_rejected(value):
    with pytest.raises(AuthRejected):unb64(value,32)


def test_receiver_signature_is_bound_to_exact_action_body_key_and_context():
    private=ec.generate_private_key(ec.SECP256R1());point=private.public_key().public_numbers()
    jwk={'kty':'EC','crv':'P-256','x':b64(point.x.to_bytes(32)),'y':b64(point.y.to_bytes(32))}
    _,thumb=receiver_key(jwk);nonce=random_token();key=str(uuid4());context=str(uuid4())
    body={'factor_type':'recovery_code','challenge_id':context,'code':'synthetic-recovery','receiver_public_key':jwk}
    path='/api/web/v1/auth/login/mfa'
    message='\n'.join(('ClassroomOS.Web.ReceiverPoP.v1','POST',path,key,'login_challenge',context,thumb,nonce,hashlib.sha256(rfc8785.dumps(body)).hexdigest())).encode()
    r,s=decode_dss_signature(private.sign(message,ec.ECDSA(hashes.SHA256())))
    proof={'challenge':nonce,'signature':b64(r.to_bytes(32)+s.to_bytes(32))};body['receiver_proof']=proof
    kwargs=dict(method='POST',path=path,key=key,kind='login_challenge',context=context,challenge=nonce,body=body)
    assert verify_receiver(jwk,proof,**kwargs)==thumb
    with pytest.raises(AuthRejected):verify_receiver(jwk,proof,**dict(kwargs,path=path+'/wrong'))
    with pytest.raises(AuthRejected):verify_receiver(jwk,proof,**dict(kwargs,key=str(uuid4())))
    with pytest.raises(AuthRejected):verify_receiver(dict(jwk,d='private'),proof,**kwargs)


def test_unsafe_policy_and_diagnostic_repr():
    with pytest.raises(ValueError):AuthPolicy(admin_idle=timedelta(days=14))
    # Lifetimes the database fixes cannot be configured shorter and then silently ignored.
    for name,value in (('challenge',timedelta(minutes=1)),('recovery_flow',timedelta(minutes=5)),('ws_ticket',timedelta(seconds=5))):
        with pytest.raises(ValueError):AuthPolicy(**{name:value})
    assert AuthPolicy().ws_ticket==timedelta(seconds=30) and AuthPolicy(ws_first_frame=2.0).ws_first_frame==2.0
    c=BrowserContext(session='private-session',csrf='private-csrf',source='private-IP')
    assert 'private' not in repr(c)


def test_legacy_bounded_argon2_phc_remains_verifiable(credentials):
    from argon2 import PasswordHasher
    from argon2.low_level import Type
    old=PasswordHasher(time_cost=2,memory_cost=32768,parallelism=2,type=Type.ID).hash('synthetic-old-password')
    assert credentials.verify(old,'synthetic-old-password')
    assert not credentials.verify(old,'wrong')
    assert not credentials.verify(old.replace('p=2','p=8'),'synthetic-old-password')
