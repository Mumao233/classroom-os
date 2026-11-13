"""Encrypted identity mail spool; SMTP is never on the public request path."""
import base64,json,os,secrets,smtplib,ssl
from email.message import EmailMessage
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import sqlalchemy as sa
from collections import deque
from sqlalchemy.engine import make_url
from ..domain.auth import AuthRejected
from .auth_crypto import digest,random_token,unb64


class AuthMailCipher:
    def __init__(self,key):
        if not isinstance(key,bytes) or len(key)!=32:raise ValueError('explicit independent mail encryption key required')
        self.cipher=AESGCM(key)

    def seal(self,identifier,email,token,purpose):
        unb64(token,32);nonce=secrets.token_bytes(12)
        body=json.dumps(dict(email=email,token=token,purpose=purpose),ensure_ascii=True,separators=(',',':')).encode('ascii')
        return base64.b64encode(nonce+self.cipher.encrypt(nonce,body,('ClassroomOS.AuthMail.v1:'+identifier).encode('ascii'))).decode('ascii')

    def open(self,identifier,envelope):
        try:
            raw=base64.b64decode(envelope,validate=True)
            value=json.loads(self.cipher.decrypt(raw[:12],raw[12:],('ClassroomOS.AuthMail.v1:'+identifier).encode('ascii')))
            if set(value)!={'email','token','purpose'} or value['purpose'] not in ('reset','change_email'):raise ValueError()
            unb64(value['token'],32)
            return value
        except Exception:raise AuthRejected('STORAGE_UNAVAILABLE',503) from None


class PgAuthMailRepository:
    def __init__(self,url):
        u=make_url(url)
        if u.drivername!='postgresql+psycopg' or u.query or not all((u.host,u.port,u.database,u.username,u.password)) or any(k.upper().startswith('PG') for k in os.environ):raise ValueError('explicit separate mail-only login required')
        self.engine=sa.create_engine(u,pool_size=1,max_overflow=0,pool_timeout=1,hide_parameters=True,connect_args={'connect_timeout':1,'options':'-c statement_timeout=2000 -c lock_timeout=1000'})
        self.faults=deque(maxlen=16)

    def call(self,name,p):
        if name not in ('claim_auth_mail','read_auth_mail_delivery','finish_auth_mail'):raise ValueError('fixed mail functions only')
        try:
            with self.engine.begin() as connection:
                if connection.scalar(sa.text("SELECT NOT(rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolinherit) AND NOT EXISTS(SELECT FROM pg_auth_members WHERE member=r.oid) FROM pg_roles r WHERE rolname=current_user")) is not True:raise AuthRejected('SERVICE_DISABLED',503)
                value=connection.scalar(sa.text(f'SELECT identity_api.{name}(CAST(:p AS jsonb))'),dict(p=json.dumps(p,separators=(',',':'))))
            return value
        except (sa.exc.DBAPIError,sa.exc.TimeoutError) as error:
            self.faults.append(dict(kind=type(error).__name__,sqlstate=getattr(getattr(error,'orig',None),'sqlstate',None)))
            raise AuthRejected('STORAGE_UNAVAILABLE',503) from None

    def dispose(self):self.engine.dispose()


class AuthMailDispatcher:
    def __init__(self,repository,cipher,transport):self.repository=repository;self.cipher=cipher;self.transport=transport;self.faults=deque(maxlen=16)

    def dispatch_once(self):
        claim=digest('auth_mail_lease',random_token());items=self.repository.call('claim_auth_mail',dict(claim_hash=claim)) or []
        result=dict(claimed=len(items),sent=0,discarded=0,failed=0)
        for item in items:
            p=dict(id=item,claim_hash=claim)
            stage='read'
            try:
                delivery=self.repository.call('read_auth_mail_delivery',p)
                if delivery is None:
                    self.repository.call('finish_auth_mail',{**p,'result':'discarded'});result['discarded']+=1;continue
                stage='decrypt';value=self.cipher.open(item,delivery['encrypted_payload'])
                # No PG transaction or user/job lock survives this network call.
                stage='send';self.transport.send_action(value['email'],value['token'],value['purpose'],item)
                stage='finish'
                if self.repository.call('finish_auth_mail',{**p,'result':'sent'}):result['sent']+=1
                else:result['failed']+=1
            except Exception as error:self.faults.append(dict(stage=stage,kind=type(error).__name__));result['failed']+=1
        return result


class SMTPActionTransport:
    def __init__(self,*,host,port,sender,links,tls_context=None,credential=None,synthetic_loopback=False):
        from urllib.parse import urlsplit
        if set(links) not in ({'reset','change_email'},{'verify'},{'reset','change_email','verify'}) or any(urlsplit(v).scheme!='https' or not urlsplit(v).netloc or urlsplit(v).username or urlsplit(v).query or urlsplit(v).fragment for v in links.values()):raise ValueError('explicit HTTPS action-link bases required')
        if synthetic_loopback:
            if host!='127.0.0.1' or port in (25,465,587):raise ValueError('synthetic SMTP uses literal private loopback and nonstandard port')
        elif not isinstance(tls_context,ssl.SSLContext) or tls_context.verify_mode!=ssl.CERT_REQUIRED or not tls_context.check_hostname:raise ValueError('verified SMTP TLS required')
        if any('\r' in v or '\n' in v for v in (sender,*links.values())):raise ValueError('invalid mail configuration')
        self.host,self.port,self.sender,self.links=host,port,sender,dict(links);self.context=tls_context;self.credential=credential;self.synthetic=synthetic_loopback

    def send_action(self,email,token,purpose,identifier):
        from urllib.parse import quote
        if purpose not in self.links or '\r' in email or '\n' in email or (self.synthetic and not email.endswith('.test')):raise AuthRejected('FORBIDDEN',403)
        unb64(token,32);message=EmailMessage();message['From']=self.sender;message['To']=email;message['Subject']='Classroom OS account action'
        message['Message-ID']='<'+identifier+'@classroom.invalid>'
        # The capability remains in a fragment, outside HTTP request targets,
        # proxy access logs and Referer headers.
        message.set_content(self.links[purpose]+'#token='+quote(token,safe=''))
        factory=smtplib.SMTP if self.synthetic else smtplib.SMTP_SSL
        options={} if self.synthetic else {'context':self.context}
        with factory(self.host,self.port,timeout=5,local_hostname='localhost' if self.synthetic else None,**options) as client:
            if self.credential:client.login(*self.credential())
            if client.send_message(message):raise AuthRejected('STORAGE_UNAVAILABLE',503)


class SMTPVerificationTransport:
    """Reuse PR-03a's durable invitation mail claim/commit protocol unchanged."""
    def __init__(self,transport):
        if 'verify' not in transport.links:raise ValueError('explicit verification link required')
        self.transport=transport
    def send_verification(self,email,token):
        from uuid import uuid4
        self.transport.send_action(email,token,'verify',str(uuid4()))
