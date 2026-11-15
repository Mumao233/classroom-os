"""Real isolated PG + bounded loopback SMTP; mail secrets never leave memory."""
import email.policy,email.parser,re,secrets,socketserver,threading
from contextlib import contextmanager
from uuid import uuid4
import pytest,sqlalchemy as sa
from classroom_web.infrastructure.auth_mail import AuthMailCipher,PgAuthMailRepository,AuthMailDispatcher,SMTPActionTransport
from classroom_web.infrastructure.auth_crypto import LocalPasswordPolicy
from classroom_web.test_support import SYNTHETIC_PASSWORD
from test_authentication import auth,post,BASE,install_factor


@pytest.fixture
def mail(auth):
    a=auth;role='pr02a_mail_'+uuid4().hex[:12];cipher=AuthMailCipher(secrets.token_bytes(32));a.service.mail=cipher
    with a.pg.admin.begin() as sql:
        sql.exec_driver_sql(f'CREATE ROLE "{role}" LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
        sql.exec_driver_sql(f'GRANT USAGE ON SCHEMA identity_api TO "{role}"')
        for name in ('claim_auth_mail','read_auth_mail_delivery','finish_auth_mail'):sql.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION identity_api.{name}(jsonb) TO "{role}"')
    repo=PgAuthMailRepository(a.pg.admin_url.set(username=role,password=SYNTHETIC_PASSWORD).render_as_string(hide_password=False))
    try:yield a,cipher,repo
    finally:
        repo.dispose()
        with a.pg.admin.begin() as sql:
            sql.execute(sa.text("UPDATE identity_control.authentication_mail_jobs SET state='discarded',encrypted_payload=NULL,finished_at=clock_timestamp(),claim_until=NULL,claim_hash=NULL WHERE owner_id=:u AND state IN('queued','sending')"),dict(u=a.uid))
            sql.exec_driver_sql(f'DROP OWNED BY "{role}"');sql.exec_driver_sql(f'DROP ROLE "{role}"')


@contextmanager
def smtp_capture():
    messages=[];errors=[]
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(3);self.wfile.write(b'220 synthetic capture\r\n')
            while True:
                line=self.rfile.readline(16385)
                if not line:return
                if len(line)>16384:raise ValueError('bounded SMTP command')
                command=line.split(b' ',1)[0].strip().upper()
                if command in (b'EHLO',b'HELO'):self.wfile.write(b'250-capture\r\n250 SIZE 16384\r\n')
                elif command in (b'MAIL',b'RCPT',b'RSET'):self.wfile.write(b'250 ok\r\n')
                elif command==b'DATA':
                    self.wfile.write(b'354 continue\r\n');body=bytearray()
                    while True:
                        part=self.rfile.readline(16385)
                        if part==b'.\r\n':break
                        if not part or len(body)+len(part)>16384:raise ValueError('bounded SMTP message')
                        body.extend(part)
                    messages.append(bytes(body));self.wfile.write(b'250 captured\r\n')
                elif command==b'QUIT':self.wfile.write(b'221 bye\r\n');return
                else:self.wfile.write(b'500 rejected\r\n')
    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address=False;daemon_threads=True
        def handle_error(self,*args):errors.append(True)
    with Server(('127.0.0.1',0),Handler) as server:
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:yield server.server_address[1],messages
        finally:server.shutdown();thread.join(3)
    assert not errors


def test_forgot_acknowledgement_does_not_depend_on_recipient_or_SMTP_failure(mail):
    a,cipher,repo=mail;csrf=a.client.get(BASE+'/auth/csrf').json()['csrf_token']
    known=post(a,'/auth/password/forgot',dict(email=a.email),csrf)
    unknown=post(a,'/auth/password/forgot',dict(email=uuid4().hex+'@example.test'),csrf)
    assert known.status_code==unknown.status_code==202 and known.json()['accepted'] and unknown.json()['accepted']
    assert len(known.content)==len(unknown.content)
    class Down:
        calls=0
        def send_action(self,*args):self.calls+=1;raise OSError('synthetic SMTP unavailable')
    down=Down();result=AuthMailDispatcher(repo,cipher,down).dispatch_once()
    assert result==dict(claimed=2,sent=0,discarded=1,failed=1)
    assert down.calls==1
    with a.pg.admin.connect() as sql:
        assert sql.scalar(sa.text("SELECT count(*) FROM identity_control.authentication_mail_jobs WHERE owner_id=:u AND state='sending'"),dict(u=a.uid))==1
    again=post(a,'/auth/password/forgot',dict(email=a.email),csrf)
    assert again.status_code==202 and again.json()['accepted']


def test_real_SMTP_reset_is_once_only_policy_guarded_and_preserves_MFA(mail):
    a,cipher,repo=mail;factor,_=install_factor(a);csrf=a.client.get(BASE+'/auth/csrf').json()['csrf_token']
    assert post(a,'/auth/password/forgot',dict(email=a.email),csrf).status_code==202
    with smtp_capture() as (port,messages):
        transport=SMTPActionTransport(host='127.0.0.1',port=port,sender='classroom@example.test',links={'reset':'https://testserver/reset-password','change_email':'https://testserver/confirm-email'},synthetic_loopback=True)
        dispatcher=AuthMailDispatcher(repo,cipher,transport);result=dispatcher.dispatch_once();assert result==dict(claimed=1,sent=1,discarded=0,failed=0),(list(repo.faults),list(dispatcher.faults))
    assert len(messages)==1
    message=email.parser.BytesParser(policy=email.policy.default).parsebytes(messages[0]);body=message.get_content()
    match=re.search(r'#token=([A-Za-z0-9_-]{43})',body);assert match is not None
    token=match.group(1);new_password='Synthetic-new-student-20261110';original=a.service.password_policy
    a.service.password_policy=LocalPasswordPolicy()
    assert post(a,'/auth/password/reset',dict(token=token,new_password=new_password),csrf).status_code==503
    a.service.password_policy=original
    assert post(a,'/auth/password/reset',dict(token=token,new_password=new_password),csrf).status_code==200
    assert post(a,'/auth/password/reset',dict(token=token,new_password=new_password),csrf).status_code==401
    with a.pg.admin.connect() as sql:
        assert sql.scalar(sa.text('SELECT state FROM mfa_credentials WHERE id=:id'),dict(id=factor))=='active'
        assert sql.scalar(sa.text('SELECT security_epoch FROM users WHERE id=:id'),dict(id=a.uid))==2
        assert sql.scalar(sa.text('SELECT encrypted_payload IS NULL FROM identity_control.authentication_mail_jobs WHERE owner_id=:id'),dict(id=a.uid))
        password_hash=sql.scalar(sa.text('SELECT password_hash FROM users WHERE id=:id'),dict(id=a.uid))
    verified=a.crypto.verify(password_hash,new_password);assert verified
    login=post(a,'/auth/login',dict(email=a.email,password=new_password,remember_me=False,device_name='After reset'),csrf)
    assert login.status_code==200 and login.json()['state']=='mfa_required' and not a.client.cookies.get('__Host-classroom_session')


def test_email_change_commits_only_after_real_mail_token_and_revokes_old_session(mail):
    from test_authentication import login,reauth
    a,cipher,repo=mail;session=login(a);new_email=uuid4().hex+'@example.test';body=dict(new_email=new_email)
    proof=reauth(a,session,'requestEmailChange',body)
    result=post(a,'/auth/email/change',body,session['csrf_token'],**proof)
    assert result.status_code==202,result.text
    with a.pg.admin.connect() as sql:
        assert sql.scalar(sa.text('SELECT normalized_email FROM users WHERE id=:id'),dict(id=a.uid))==a.email
    with smtp_capture() as (port,messages):
        transport=SMTPActionTransport(host='127.0.0.1',port=port,sender='classroom@example.test',links={'reset':'https://testserver/reset-password','change_email':'https://testserver/confirm-email'},synthetic_loopback=True)
        assert AuthMailDispatcher(repo,cipher,transport).dispatch_once()==dict(claimed=1,sent=1,discarded=0,failed=0)
    assert len(messages)==1
    message=email.parser.BytesParser(policy=email.policy.default).parsebytes(messages[0])
    assert message['To']==new_email
    token=re.search(r'#token=([A-Za-z0-9_-]{43})',message.get_content()).group(1)
    assert post(a,'/auth/logout',{},session['csrf_token']).status_code==200
    csrf=a.client.get(BASE+'/auth/csrf').json()['csrf_token']
    assert post(a,'/auth/email/confirm',dict(token=token),csrf).status_code==200
    assert post(a,'/auth/email/confirm',dict(token=token),csrf).status_code==401
    with a.pg.admin.connect() as sql:
        assert sql.scalar(sa.text('SELECT normalized_email FROM users WHERE id=:id'),dict(id=a.uid))==new_email
        assert sql.scalar(sa.text('SELECT security_epoch FROM users WHERE id=:id'),dict(id=a.uid))==2
        assert sql.scalar(sa.text('SELECT count(*) FROM auth_sessions WHERE owner_id=:id AND revoked_at IS NULL'),dict(id=a.uid))==0


def test_invitation_mail_adapter_uses_existing_transport_with_fragment_only():
    from classroom_web.infrastructure.auth_mail import SMTPVerificationTransport
    from classroom_web.infrastructure.auth_crypto import random_token
    token=random_token()
    with smtp_capture() as (port,messages):
        transport=SMTPActionTransport(host='127.0.0.1',port=port,sender='classroom@example.test',links={'verify':'https://testserver/verify-email'},synthetic_loopback=True)
        SMTPVerificationTransport(transport).send_verification('student@example.test',token)
    assert len(messages)==1
    message=email.parser.BytesParser(policy=email.policy.default).parsebytes(messages[0])
    assert message.get_content().strip()=='https://testserver/verify-email#token='+token
