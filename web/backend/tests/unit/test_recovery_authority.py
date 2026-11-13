"""Independent pinned trust and fresh-nonce controls, using synthetic installers."""
import copy,json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from uuid import uuid4

import pytest,rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat

from classroom_web.domain.auth import AuthRejected
from classroom_web.infrastructure.auth_crypto import b64,random_token
from classroom_web.infrastructure.recovery_authority import AuthorityVerifier,ControlSchema,InstalledAuthorityRoot,control_hash


def at(value):return value.isoformat(timespec='microseconds').replace('+00:00','Z')


@pytest.fixture
def installed():
    keys=[Ed25519PrivateKey.generate() for _ in range(3)]
    public=[dict(key_id=str(uuid4()),alg='Ed25519',public_key=b64(k.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw))) for k in keys]
    deployment=str(uuid4());peer=dict(provider='windows_sid',subject='S-1-5-21-SYNTHETIC-ONLY')
    body=dict(deployment_id=deployment,version=1,previous_manifest_hash='0'*64,root_key=public[0],change_key=public[1],maintainer_ref='synthetic-independent-maintainer',
              authority=dict(authority_id=str(uuid4()),project_user_mapping_ref='synthetic-external-install-approval',os_subject=peer,recover_key=public[2],enabled=True),verifier_version='1')
    def sign(index,domain,value):return dict(alg='Ed25519',key_id=public[index]['key_id'],signature=b64(keys[index].sign(domain.encode()+b'\0'+rfc8785.dumps(value))))
    class Reader:
        mutate=None
        def read_current(self,nonce):
            manifest=copy.deepcopy(body);now=datetime.now(timezone.utc)
            current=dict(deployment_id=deployment,request_nonce=nonce,initialization_complete=True,high_water_version=manifest['version'],high_water_hash=control_hash(manifest),
                         state='ready',transition_id=None,issued_at=at(now),expires_at=at(now+timedelta(seconds=30)))
            if self.mutate:self.mutate(manifest,current)
            return dict(protocol_version=1,kind='d0.manifest',body=manifest,signature=sign(0,'classroom.identity.manifest.v1',manifest)),dict(protocol_version=1,kind='d0.current.evidence',body=current,signature=sign(0,'classroom.identity.current.v1',current))
    path=Path(__file__).resolve().parents[4]/'docs/web-alpha/architecture/contracts/identity-control.schema.json'
    reader=Reader();verifier=AuthorityVerifier(installed_root=InstalledAuthorityRoot(deployment,public[0]['key_id'],public[0]['public_key'],'external-synthetic-approval'),reader=reader,schema=ControlSchema(json.loads(path.read_text(encoding='utf8'))))
    return verifier,reader,body,sign,peer


def test_fresh_current_evidence_matches_independent_installed_pin(installed):
    verifier,_,body,_,_=installed
    current=verifier.current()
    assert current.version==1 and current.manifest_hash==control_hash(body)


@pytest.mark.parametrize('fault',['nonce','revoked','rollback','transition','same_key'])
def test_valid_signatures_cannot_replace_currentness_or_purpose_separation(installed,fault):
    verifier,reader,body,_,_=installed
    def mutate(manifest,current):
        if fault=='nonce':current['request_nonce']=random_token()
        if fault=='revoked':manifest['authority']['enabled']=False;current['high_water_hash']=control_hash(manifest)
        if fault=='rollback':current['high_water_version']=2
        if fault=='transition':current['state']='transition_reserved';current['transition_id']=str(uuid4())
        if fault=='same_key':manifest['change_key']=manifest['root_key'];current['high_water_hash']=control_hash(manifest)
    reader.mutate=mutate
    with pytest.raises(AuthRejected):verifier.current()


def test_hello_signature_binds_exact_kernel_peer_and_domain(installed):
    verifier,_,body,sign,peer=installed;now=datetime.now(timezone.utc)
    hello=dict(protocol_version=1,kind='identity_control.hello.response',deployment_id=body['deployment_id'],authentication_challenge_id=str(uuid4()),challenge_nonce=random_token(),authentication_mode='authority',challenge_expiry=at(now+timedelta(seconds=30)))
    signature=sign(2,'classroom.identity.authenticate.v1',dict(hello=hello,peer=peer))
    assert verifier.authenticate(hello,peer,signature).authority['authority_id']==body['authority']['authority_id']
    with pytest.raises(AuthRejected):verifier.authenticate(hello,{**peer,'subject':'another-OS-subject'},signature)
    wrong=sign(2,'classroom.identity.recovery-proof.v1',dict(hello=hello,peer=peer))
    with pytest.raises(AuthRejected):verifier.authenticate(hello,peer,wrong)
