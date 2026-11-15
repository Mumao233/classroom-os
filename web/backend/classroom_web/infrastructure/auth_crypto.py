"""Mature cryptography with bounded encodings and explicit key injection."""
import base64,hashlib,hmac,json,re,secrets,threading
from dataclasses import dataclass
from datetime import datetime,timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError,InvalidHashError
from argon2.low_level import Type
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidSignature
import pyotp,rfc8785

from ..domain.auth import AuthRejected


def scalar_password(value,minimum=0):
    if type(value) is not str or not minimum<=len(value)<=1024 or any(0xD800<=ord(c)<=0xDFFF for c in value):
        raise AuthRejected('VALIDATION_FAILED',422)
    return value


def b64(raw):return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def unb64(value,size=None):
    if type(value) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,256}',value):raise AuthRejected()
    try:raw=base64.urlsafe_b64decode(value+'='*((-len(value))%4))
    except (ValueError,TypeError):raise AuthRejected() from None
    if b64(raw)!=value or (size is not None and len(raw)!=size):raise AuthRejected()
    return raw


def random_token():return b64(secrets.token_bytes(32))


def digest(purpose,value):
    unb64(value,32)
    return hashlib.sha256(purpose.encode('ascii')+b'\0'+value.encode('ascii')).hexdigest()


@dataclass(frozen=True)
class ListApproval:
    digest: str
    expires_at: datetime


class LocalPasswordPolicy:
    """The deployment supplies an independently approved exact list digest.

    Files contain SHA-256 of UTF-8 passwords, one lowercase hex digest per line.
    No network lookup, normalization, automatic approval or empty-list fallback.
    """
    def __init__(self,path:Path|None=None,approval:ListApproval|None=None):
        self.path,self.approval=path,approval
        self._lock=threading.Lock();self._cached_digest=None;self._cached_entries=None

    def _load(self):
        from .auth_deadlines import remaining
        left=remaining();timeout=1 if left is None else min(1,max(0,left))
        if not self._lock.acquire(timeout=timeout):raise AuthRejected('RATE_LIMITED',429)
        try:return self._load_locked()
        finally:self._lock.release()

    def _load_locked(self):
        if (self.path is None or self.approval is None or self.approval.expires_at.tzinfo is None
                or datetime.now(timezone.utc)>=self.approval.expires_at):
            raise AuthRejected('SERVICE_DISABLED',503)
        try:
            if self.path.stat().st_size>64*1024*1024:raise ValueError()
            with self.path.open('rb') as stream:raw=stream.read(64*1024*1024+1)
            if len(raw)>64*1024*1024:raise ValueError()
            if hashlib.sha256(raw).hexdigest()!=self.approval.digest:raise ValueError()
            # Still hash the actual bounded file at each commit check. Cache
            # only its parsed exact set so parallel validations cannot each
            # allocate another million-entry deny list.
            if self._cached_digest==self.approval.digest:return self._cached_entries
            lines=raw.decode('ascii').splitlines()
            if not lines or any(not re.fullmatch('[0-9a-f]{64}',s) for s in lines):raise ValueError()
        except (OSError,UnicodeError,ValueError):raise AuthRejected('SERVICE_DISABLED',503) from None
        self._cached_entries=frozenset(lines);self._cached_digest=self.approval.digest
        return self._cached_entries

    def validate(self,password,role):
        if role not in ('student','admin'):raise AuthRejected()
        scalar_password(password,12 if role=='admin' else 6)
        if hashlib.sha256(password.encode('utf8')).hexdigest() in self._load():
            raise AuthRejected('VALIDATION_FAILED',422)
        return self.approval

    def current(self,approval):
        self._load()
        if approval!=self.approval:raise AuthRejected('SERVICE_DISABLED',503)


class Credentials:
    def __init__(self,*,encryption_key:bytes,commitment_key:bytes):
        if len(encryption_key)!=32 or len(commitment_key)<32 or encryption_key==commitment_key:
            raise ValueError('separate explicit authentication keys required')
        self.aead=AESGCM(encryption_key);self.key=commitment_key
        self.argon=PasswordHasher(time_cost=3,memory_cost=65536,parallelism=1,hash_len=32,salt_len=16,type=Type.ID)
        # Same bounded PHC and work for a missing account; never use a cheap
        # dummy, untrusted PHC parameters or attacker-selected memory costs.
        # Public dummy PHC contains no account secret. Verification still does
        # the full identical Argon2 work; startup does not run an unadmitted hash.
        self.dummy='$argon2id$v=19$m=65536,t=3,p=1$'+'A'*22+'$'+'A'*43

    def hash(self,password):return self.argon.hash(scalar_password(password,6))

    def verify(self,phc,password):
        scalar_password(password)
        match=re.fullmatch(r'\$argon2id\$v=19\$m=([0-9]{1,6}),t=([0-9]),p=([0-9])\$([A-Za-z0-9+/]{11,86})\$([A-Za-z0-9+/]{22,86})',phc) if type(phc) is str and phc.isascii() and len(phc)<=512 else None
        valid=False
        if match:
            memory,iterations,parallelism=map(int,match.group(1,2,3))
            try:
                salt,hashed=(base64.b64decode(v+'='*((-len(v))%4),validate=True) for v in match.group(4,5))
                valid=1<=parallelism<=4 and 8*parallelism<=memory<=131072 and 1<=iterations<=4 and 8<=len(salt)<=64 and 16<=len(hashed)<=64
            except ValueError:pass
        try:ok=self.argon.verify(phc if valid else self.dummy,password)
        except (VerificationError,InvalidHashError):ok=False
        return bool(valid and ok)

    def commitment(self,purpose,value):
        return hmac.digest(self.key,purpose.encode('ascii')+b'\0'+rfc8785.dumps(value),'sha256').hex()

    def csrf(self,kind,token):
        unb64(token,32)
        return b64(hmac.digest(self.key,('csrf:'+kind+'\0').encode('ascii')+token.encode('ascii'),'sha256'))

    def seal(self,owner,factor,seed):
        nonce=secrets.token_bytes(12)
        return base64.b64encode(nonce+self.aead.encrypt(nonce,seed.encode('ascii'),f'{owner}:{factor}'.encode('ascii'))).decode('ascii')

    def open(self,owner,factor,envelope):
        try:
            raw=base64.b64decode(envelope,validate=True)
            return self.aead.decrypt(raw[:12],raw[12:],f'{owner}:{factor}'.encode('ascii')).decode('ascii')
        except Exception:raise AuthRejected('STORAGE_UNAVAILABLE',503) from None

    @staticmethod
    def new_seed():return pyotp.random_base32(length=32)

    @staticmethod
    def totp_step(seed,code,now):
        if type(code) is not str or re.fullmatch('[0-9]{6}',code) is None:raise AuthRejected()
        otp=pyotp.TOTP(seed);current=int(now.timestamp())//30
        matches=[s for s in (current-1,current,current+1) if hmac.compare_digest(otp.at(s*30),code)]
        if not matches:raise AuthRejected()
        return max(matches)


def receiver_key(jwk):
    try:
        if type(jwk) is not dict or set(jwk)!={'kty','crv','x','y'} or jwk['kty']!='EC' or jwk['crv']!='P-256':raise ValueError()
        point=ec.EllipticCurvePublicNumbers(int.from_bytes(unb64(jwk['x'],32)),int.from_bytes(unb64(jwk['y'],32)),ec.SECP256R1()).public_key()
        return point,b64(hashlib.sha256(rfc8785.dumps(jwk)).digest())
    except (ValueError,TypeError,KeyError):raise AuthRejected() from None


def verify_receiver(jwk,proof,*,method,path,key,kind,context,challenge,body):
    public,thumb=receiver_key(jwk)
    if type(proof) is not dict or set(proof)!={'challenge','signature'} or proof['challenge']!=challenge:raise AuthRejected()
    unb64(challenge,32)
    unsigned={k:v for k,v in body.items() if k!='receiver_proof'}
    message='\n'.join(('ClassroomOS.Web.ReceiverPoP.v1',method,path,key,kind,context,thumb,challenge,hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest())).encode('utf8')
    signature=unb64(proof['signature'],64)
    try:public.verify(encode_dss_signature(int.from_bytes(signature[:32]),int.from_bytes(signature[32:])),message,ec.ECDSA(hashes.SHA256()))
    except (ValueError,InvalidSignature):raise AuthRejected() from None
    return thumb
