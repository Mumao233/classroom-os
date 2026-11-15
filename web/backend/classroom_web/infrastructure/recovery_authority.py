"""Independently installed D0 pins; recovery input can never install its own root.

The deployment owns the installation and external-current-reader adapters.
Neither a database row, a matching new key, nor a schema-valid install record
constitutes that external authorization. No installer or bootstrap API lives here.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
import hashlib
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from jsonschema import Draft202012Validator,FormatChecker
import rfc8785

from ..domain.auth import AuthRejected
from .auth_crypto import unb64,random_token


def control_hash(value):
    try:return hashlib.sha256(rfc8785.dumps(value)).hexdigest()
    except (ValueError,TypeError):raise AuthRejected('FORBIDDEN',403) from None


def instant(value):
    try:return datetime.fromisoformat(value.replace('Z','+00:00'))
    except (TypeError,ValueError,AttributeError):raise AuthRejected('FORBIDDEN',403) from None


def verify_control_signature(key,envelope,domain,body):
    if type(envelope) is not dict or set(envelope)!={'alg','key_id','signature'} or envelope['alg']!='Ed25519' or envelope['key_id']!=key['key_id']:
        raise AuthRejected('FORBIDDEN',403)
    try:
        public=Ed25519PublicKey.from_public_bytes(unb64(key['public_key'],32))
        public.verify(unb64(envelope['signature'],64),domain.encode('ascii')+b'\0'+rfc8785.dumps(body))
    except (InvalidSignature,ValueError,TypeError):raise AuthRejected('FORBIDDEN',403) from None


class ControlSchema:
    def __init__(self,schema):
        Draft202012Validator.check_schema(schema);self.schema=schema
    def check(self,name,value):
        root={'$ref':'#/$defs/'+name,'$defs':self.schema['$defs']}
        try:valid=Draft202012Validator(root,format_checker=FormatChecker()).is_valid(value)
        except (ValueError,TypeError):valid=False
        if not valid:raise AuthRejected('FORBIDDEN',403)
        # Also enforce the mature canonicalizer's exact numeric/string domain.
        control_hash(value)
        return value


@dataclass(frozen=True)
class InstalledAuthorityRoot:
    deployment_id: str
    root_key_id: str
    root_public_key: str
    installation_approval_reference: str

    def __post_init__(self):
        from uuid import UUID
        if any(str(UUID(v))!=v for v in (self.deployment_id,self.root_key_id)) or not self.installation_approval_reference:
            raise ValueError('external installation identity and approval reference required')
        unb64(self.root_public_key,32)

    @property
    def key(self):return {'key_id':self.root_key_id,'alg':'Ed25519','public_key':self.root_public_key}


class ExternalD0Reader(Protocol):
    def read_current(self,request_nonce:str)->tuple[dict,dict]:
        """Return signed installed manifest and signed live nonce response."""
        ...


@dataclass(frozen=True)
class CurrentAuthority:
    deployment_id:str
    version:int
    manifest_hash:str
    authority:dict
    expires_at:datetime
    manifest:dict


class AuthorityVerifier:
    def __init__(self,*,installed_root:InstalledAuthorityRoot,reader:ExternalD0Reader,schema:ControlSchema):
        self.root,self.reader,self.schema=installed_root,reader,schema

    def current(self,*,require_enabled=True)->CurrentAuthority:
        nonce=random_token()
        try:manifest,evidence=self.reader.read_current(nonce)
        except Exception:raise AuthRejected('SERVICE_DISABLED',503) from None
        self.schema.check('D0Manifest',manifest);self.schema.check('D0CurrentEvidence',evidence)
        body=manifest['body'];current=evidence['body'];now=datetime.now(timezone.utc)
        verify_control_signature(self.root.key,manifest['signature'],'classroom.identity.manifest.v1',body)
        verify_control_signature(self.root.key,evidence['signature'],'classroom.identity.current.v1',current)
        recover=body['authority']['recover_key'];change=body['change_key']
        keys=(self.root.key,change,recover)
        if (body['deployment_id']!=self.root.deployment_id or current['deployment_id']!=self.root.deployment_id
                or body['root_key']!=self.root.key or current['request_nonce']!=nonce or current['state']!='ready'
                or current['transition_id'] is not None or not current['initialization_complete']
                or current['high_water_version']!=body['version'] or current['high_water_hash']!=control_hash(body)
                or not instant(current['issued_at'])<=now<instant(current['expires_at'])
                or len({k['key_id'] for k in keys})!=3 or len({k['public_key'] for k in keys})!=3
                or (require_enabled and not body['authority']['enabled'])):
            raise AuthRejected('FORBIDDEN',403)
        return CurrentAuthority(self.root.deployment_id,body['version'],control_hash(body),body['authority'],instant(current['expires_at']),body)

    def authenticate(self,hello,peer,signature):
        self.schema.check('ControlHelloResponse',hello);self.schema.check('OsSubject',peer)
        current=self.current()
        if (hello['deployment_id']!=current.deployment_id or hello['authentication_mode']!='authority'
                or datetime.now(timezone.utc)>=instant(hello['challenge_expiry']) or peer!=current.authority['os_subject']):raise AuthRejected('FORBIDDEN',403)
        verify_control_signature(current.authority['recover_key'],signature,'classroom.identity.authenticate.v1',{'hello':hello,'peer':peer})
        return current

    def approve(self,challenge,signature,*,peer):
        self.schema.check('ChallengeBody',challenge);current=self.current();plan=challenge['plan'];now=datetime.now(timezone.utc)
        if (peer!=current.authority['os_subject'] or plan['deployment_id']!=current.deployment_id
                or plan['executor']!={'executor_type':'authority','executor_id':current.authority['authority_id']}
                or challenge['plan_hash']!=control_hash(plan) or not instant(challenge['issued_at'])<=now<instant(challenge['challenge_expiry'])
                or ('authority_version' in plan and plan['authority_version']!=current.version)):
            raise AuthRejected('FORBIDDEN',403)
        verify_control_signature(current.authority['recover_key'],signature,'classroom.identity.recovery-proof.v1',challenge)
        return current
