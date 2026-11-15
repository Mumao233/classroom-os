"""Mature crypto adapters and bounded protocol encoding for synthetic inputs."""
from __future__ import annotations
import base64,hashlib,hmac,json,secrets,struct,time
from common import require,Stop
def b64(raw):return base64.urlsafe_b64encode(raw).decode().rstrip('=')
def unb64(s,n):
    require(type(s) is str and '=' not in s,'base64_canonical')
    try:raw=base64.urlsafe_b64decode(s+'='*((-len(s))%4))
    except Exception:raise Stop('base64_invalid') from None
    require(len(raw)==n and b64(raw)==s,'base64_shape');return raw
def jcs(value):
    # RFC8785 implementation, pinned as a separate dependency; no custom JCS.
    import rfc8785
    return rfc8785.dumps(value)
def signed(domain,body):return domain.encode('ascii')+b'\0'+jcs(body)
def binding(value,key):return hmac.digest(key,typed(value),'sha256')
def typed(v):
    if v is None:return b'n'
    if type(v) is bool:return b't' if v else b'f'
    if type(v) is int:return b'i'+str(v).encode()+b';'
    if type(v) is str:
        raw=v.encode('utf-8','strict');return b's'+str(len(raw)).encode()+b':'+raw
    if type(v) is list:return b'a'+str(len(v)).encode()+b':'+b''.join(map(typed,v))
    if type(v) is dict:
        require(all(type(k) is str for k in v),'typed_keys')
        return b'o'+str(len(v)).encode()+b':'+b''.join(typed(k)+typed(v[k]) for k in sorted(v))
    raise Stop('typed_value')
def password(value,role):
    require(type(value) is str and role in ('student','admin'),'password_type')
    try:value.encode('utf-8','strict')
    except UnicodeError:raise Stop('password_unicode') from None
    require((6 if role=='student' else 12)<=len(value)<=1024,'password_length');return value
def phc_bounded(value):
    import re
    require(type(value) is str and len(value)<=512 and value.isascii(),'phc_size')
    m=re.fullmatch(r'\$argon2id\$v=19\$m=([0-9]+),t=([0-9]+),p=([0-9]+)\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+',value)
    require(m is not None,'phc_format');memory,steps,lanes=map(int,m.groups())
    require(1<=lanes<=4 and 1<=steps<=4 and 8*lanes<=memory<=131072,'phc_budget')
    return memory,steps,lanes
def verify(phc,value):
    from argon2 import PasswordHasher
    from argon2.exceptions import VerificationError
    phc_bounded(phc)
    try:return PasswordHasher().verify(phc,value)
    except VerificationError:return False
def totp(seed,step):
    from cryptography.hazmat.primitives.twofactor.totp import TOTP
    from cryptography.hazmat.primitives import hashes
    return TOTP(seed,6,hashes.SHA1(),30).generate(step*30)
def jwk(public):
    n=public.public_numbers();return {'kty':'EC','crv':'P-256','x':b64(n.x.to_bytes(32,'big')),'y':b64(n.y.to_bytes(32,'big'))}
def receiver_key(value):
    from cryptography.hazmat.primitives.asymmetric import ec
    require(type(value) is dict and set(value)=={'kty','crv','x','y'} and value['kty']=='EC' and value['crv']=='P-256','receiver_jwk')
    try:return ec.EllipticCurvePublicNumbers(int.from_bytes(unb64(value['x'],32),'big'),int.from_bytes(unb64(value['y'],32),'big'),ec.SECP256R1()).public_key()
    except ValueError:raise Stop('receiver_curve') from None
def thumb(value):receiver_key(value);return b64(hashlib.sha256(jcs(value)).digest())
def receiver_verify(key,body,signature):
    from cryptography.hazmat.primitives.asymmetric import ec,utils
    from cryptography.hazmat.primitives import hashes
    from cryptography.exceptions import InvalidSignature
    raw=unb64(signature,64);r=int.from_bytes(raw[:32],'big');s=int.from_bytes(raw[32:],'big')
    require(r>0 and s>0,'receiver_scalar')
    try:receiver_key(key).verify(utils.encode_dss_signature(r,s),body,ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature,ValueError):raise Stop('receiver_signature') from None
def receiver_sign(key,body):
    from cryptography.hazmat.primitives.asymmetric import ec,utils
    from cryptography.hazmat.primitives import hashes
    r,s=utils.decode_dss_signature(key.sign(body,ec.ECDSA(hashes.SHA256())))
    return b64(r.to_bytes(32,'big')+s.to_bytes(32,'big'))
