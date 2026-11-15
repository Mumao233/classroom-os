"""Loopback HTTPS/WSS mechanism prototype; /lab/* is not a product API."""
from __future__ import annotations
import asyncio,hashlib,http.client,http.server,json,secrets,socket,ssl,threading,time,uuid,collections,sys,traceback
from service_pool import FixedPool
from datetime import datetime,timedelta,timezone
from pathlib import Path
import common as b,crypto,schema
import admission
import request_diagnostics as rd
import itertools
_SESSION_IDS=itertools.count(1000001)
_SESSION_ID_LOCK=threading.Lock()
class Capacity(b.Stop):pass
class TLSHTTPServer(http.server.ThreadingHTTPServer):
    """Bounded fixture transport; TLS handshakes never hold the accept loop."""
    daemon_threads=True
    def __init__(self,address,handler,context):
        self.tls_context=context;self.transport_slots=threading.BoundedSemaphore(64)
        self.diagnostic_lock=threading.RLock();self.transport_cv=threading.Condition(self.diagnostic_lock);self.active_workers=0
        self.error_counts=collections.Counter();self.error_examples=collections.deque(maxlen=64)
        super().__init__(address,handler)
    def get_request(self):
        raw,address=super().get_request();raw.settimeout(5)
        try:return self.tls_context.wrap_socket(raw,server_side=True,do_handshake_on_connect=False),address
        except BaseException:raw.close();raise
    def process_request(self,request,address):
        if not self.transport_slots.acquire(False):
            with self.diagnostic_lock:self.error_counts['transport_capacity']+=1
            self.shutdown_request(request);return
        with self.diagnostic_lock:self.active_workers+=1
        try:return super().process_request(request,address)
        except BaseException:
            self.transport_slots.release();self.shutdown_request(request)
            with self.transport_cv:self.active_workers-=1;self.transport_cv.notify_all()
            raise
    def process_request_thread(self,request,address):
        try:
            request.do_handshake()
            super().process_request_thread(request,address)
        except BaseException:
            self.handle_error(request,address);self.shutdown_request(request)
        finally:
            self.transport_slots.release()
            with self.transport_cv:self.active_workers-=1;self.transport_cv.notify_all()
    def handle_error(self,request,address):
        # All failures remain counted. Do not amplify repeated closed-peer
        # exceptions into unbounded stderr or serialize exception messages,
        # requests, cookies, credentials or SQL parameters.
        error=sys.exc_info()[1];kind=type(error).__name__
        state=getattr(error,'sqlstate',None)
        if type(state) is not str or len(state)!=5 or not state.isascii() or not state.isalnum():state=None
        frames=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(error.__traceback__)][-8:] if error else []
        with self.diagnostic_lock:
            self.error_counts[kind]+=1;self.error_examples.append({'error_type':kind,'sqlstate':state,'frames':frames})
    def diagnostics(self):
        with self.diagnostic_lock:return {'error_counts':dict(self.error_counts),'last_error_examples':list(self.error_examples),'example_capacity':64,'all_error_occurrences_counted':True,'exception_messages_or_request_values_recorded':False,'TLS_worker_limit':64,'socket_timeout_seconds':5,'active_workers':self.active_workers}
    def wait_drained(self):
        until=time.monotonic()+5
        with self.transport_cv:
            while self.active_workers:
                remaining=until-time.monotonic();b.require(remaining>0,'HTTP_transport_workers_not_drained');self.transport_cv.wait(remaining)
def login_password(value):
    if type(value) is not str or len(value)>1024:return False
    try:value.encode('utf-8','strict');return True
    except UnicodeError:return False
def digest(raw):return hashlib.sha256(raw.encode()).hexdigest()
def cookie_values(header):
    pairs=[x.strip().split('=',1) for x in (header or '').split(';') if '=' in x]
    b.require(len({p[0] for p in pairs})==len(pairs),'ambiguous_duplicate_cookie')
    return dict(pairs)
def certificates(root):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import ipaddress
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048);now=datetime.now(timezone.utc)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'PR03b S3 Synthetic CA '+secrets.token_hex(8))])
    ca=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True).add_extension(x509.KeyUsage(False,False,False,False,False,True,True,False,False),critical=True).sign(key,hashes.SHA256())
    server_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    leaf=x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'app.test')])).issuer_name(name).public_key(server_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName('app.test'),x509.DNSName('evil.test'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False).add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),critical=False).sign(key,hashes.SHA256())
    (root/'ca.cer').write_bytes(ca.public_bytes(serialization.Encoding.DER));(root/'ca.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (root/'tls.pem').write_bytes(leaf.public_bytes(serialization.Encoding.PEM));(root/'tls-key.pem').write_bytes(server_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    # Fixed guest compatibility profile. The reviewed TLS1.2 floor, normal
    # certificate validation and five-second WSS budget remain unchanged.
    # The separate 500/500 comparison records TLS1.3 failures; it does not
    # establish the internal cause of old HOME24/28/30 or the TLS stack bug.
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.minimum_version=ssl.TLSVersion.TLSv1_2;ctx.maximum_version=ssl.TLSVersion.TLSv1_2;ctx.load_cert_chain(str(root/'tls.pem'),str(root/'tls-key.pem'))
    client=ssl.create_default_context(cafile=str(root/'ca.pem'));client.minimum_version=ssl.TLSVersion.TLSv1_2;client.maximum_version=ssl.TLSVersion.TLSv1_2
    return ctx,client,ca.fingerprint(hashes.SHA1()).hex().upper()
class Lab(FixedPool):
    def __init__(self,lease,root,*,business=False):
        from argon2 import PasswordHasher
        import windows,os
        b.require(not os.environ.get('SSLKEYLOGFILE'),'TLS_keylog_environment_refused')
        windows.protect_private(root)
        self.lease=lease;self.root=root;self.lock=threading.Lock();self.preauth={};self.sessions={};self.pending_mfa={};self.next_id=1000000;self.namespace=uuid.uuid4().hex
        self.request_log=rd.Recorder()
        super().__init__(lease,('auth','session','runtime') if business else ('auth','session'))
        self.hash_gate=admission.Bounded(shared_slots=admission.shared());self.admission_rows=[];self.no_hash_baseline=False
        self.password=secrets.token_urlsafe(24);self.phc=PasswordHasher(memory_cost=65536,time_cost=3,parallelism=1).hash(self.password)
        self.dummy_phc=PasswordHasher(memory_cost=65536,time_cost=3,parallelism=1).hash(secrets.token_urlsafe(24))
        # Trusted fixture installation, not a password-change endpoint. HTTP
        # authentication itself always resolves the actual stored PG PHC.
        self.aliases={};self.allowed_web_actors={1,2,3}
        with lease.connect('bootstrap') as c:
            c.execute('BEGIN READ WRITE')
            for actor,names in ((1,('synthetic-student-mfa',)),(2,('synthetic-student',)),(3,('synthetic-admin',))):
                existing=c.execute('SELECT email FROM identity_control.lab3_credentials WHERE owner=%s',(actor,)).fetchone()
                email=existing[0] if existing else 'synthetic-'+str(actor)+'@example.invalid'
                c.execute('INSERT INTO identity_control.lab3_credentials VALUES(%s,%s,1,%s,1,decode(\'010203\',\'hex\')) ON CONFLICT(owner) DO UPDATE SET phc=EXCLUDED.phc',(actor,self.phc,email))
                for name in names:self.aliases[name]=email
            c.execute('COMMIT')
        self.mfa_seed=secrets.token_bytes(20);self.hmac_key=secrets.token_bytes(32);self.markers={self.password,self.phc,self.dummy_phc,crypto.b64(self.mfa_seed),self.mfa_seed.hex()};self.issued_codes=[];self.receiver_seen=set();self.fault_pg=False;self.fault_sql_timeout=False;self.drop_next_codes=False;self.dropped_code_commits=0;self.expensive_calls=0
        import native_process,os
        self.worker_pid=os.getpid();self.worker_creation=str(native_process.observe_process_details(self.worker_pid).identity.creation_token)
        self.context,self.client_context,self.ca_thumb=certificates(root)
        # Publish the listener only after its finite role pools have completed
        # full native/backend/role handshakes. Startup is recorded separately;
        # it does not renew any request, proof or challenge deadline.
        for service in self.channels:
            for _ in range(self.pool_size(service)):
                b.require(self.database(service,'SELECT 1',read_only=True)==1,'prepublish_pool_identity')
        self.http=TLSHTTPServer(('127.0.0.1',0),self.handler(),self.context)
        self.port=self.http.server_address[1];self.origin='https://app.test:'+str(self.port)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.ws_server=None;self.ws_thread=None;self.ws_counts=[0,0]
    def sql(self,statement,args=()):
        b.require(not self.fault_pg,'lab_PG_unavailable')
        if self.fault_sql_timeout:return self.database('session','SELECT pg_sleep(4)')
        return self.database('session',statement,args)
    def session(self,token,mode='validate'):
        with self.lock:row=self.sessions.get(digest(token))
        b.require(row is not None,'session_invalid')
        result=self.sql('SELECT identity_api.lab3_session(%s,%s,%s,%s,%s)',(schema.ticket('session',row['actor']),mode,row['id'],'',digest(token)))
        return dict(row,**result)
    def business(self,token):
        with self.lock:row=self.sessions.get(digest(token))
        b.require(row is not None,'session_invalid')
        now=time.monotonic();existing=admission.CURRENT.get()
        deadline=admission.CURRENT.set(min(now+5,existing) if existing is not None else now+5)
        args=(schema.ticket('session',row['actor']),'validate',row['id'],'',digest(token))
        def read(c):
            # Keep the session/user locks until the separate runtime RLS read
            # is complete. Revocation cannot commit in a cross-role gap.
            identity=c.execute('SELECT identity_api.lab3_session(%s,%s,%s,%s,%s)',args).fetchone()[0]
            data=self.database('runtime','SELECT identity_api.lab_runtime(%s)',(schema.ticket('runtime',identity['owner']),),read_only=True)
            c.execute('SELECT identity_api.lab3_session(%s,%s,%s,%s,%s)',args).fetchone()
            b.require(identity['owner']==row['actor'] and isinstance(data,list) and len(data)>0,'owned_business_read')
            return {'owner':identity['owner'],'documents':len(data)}
        try:return self.transaction('session',read)
        finally:admission.CURRENT.reset(deadline)
    def budget(self,key,limit,seconds,increment=False):
        return self.database('auth','SELECT identity_api.lab3_fail_budget(%s,%s,%s,%s,%s)',(schema.ticket('auth',2),self.namespace+':'+key,limit,seconds,increment))['allowed']
    def install_load_accounts(self):
        # Exactly thirty active WEB subjects in this disposable trial. The
        # non-Web synthetic D0 authority is not a learner account.
        with self.lease.connect('bootstrap') as c:
            c.execute('BEGIN READ WRITE')
            c.execute("UPDATE identity_control.lab_principals SET active=false WHERE kind<>'synthetic_authority'")
            for actor in schema.LOAD_ACTORS:
                email='load'+str(actor)+'@example.invalid'
                c.execute("INSERT INTO identity_control.lab_principals VALUES(%s,'student',true,1) ON CONFLICT(id) DO UPDATE SET active=true",(actor,))
                c.execute("INSERT INTO identity_control.lab3_credentials VALUES(%s,%s,1,%s,1,decode('010203','hex')) ON CONFLICT(owner) DO UPDATE SET phc=EXCLUDED.phc,email=EXCLUDED.email",(actor,self.phc,email))
                if actor>5:
                    for service in ('session','runtime'):c.execute('INSERT INTO identity_control.lab_contexts VALUES(md5(%s),%s,%s,%s)',(schema.ticket(service,actor),schema.logins(self.lease.plan.run_id)[service],actor,service))
                c.execute('INSERT INTO identity_control.lab_documents VALUES(%s,%s,\'S3-LOAD-OWNED-DOC\')',(actor,100000+actor))
                self.aliases['load'+str(actor)]=email
            c.execute('COMMIT')
            b.require(c.execute("SELECT count(*) FROM identity_control.lab_principals WHERE active AND kind<>'synthetic_authority'").fetchone()[0]==30,'exact_30_active_web_accounts')
        self.allowed_web_actors=set(schema.LOAD_ACTORS)
    def lookup(self,email):
        return self.database('auth','SELECT identity_api.lab3_lookup(%s,%s)',(schema.ticket('auth',2),email),read_only=True)
    def verify(self,phc,value):
        if admission.GRANTED.get() is None:
            remaining=admission.remaining()
            deadline=None if remaining is None else time.monotonic()+remaining-4
            try:return self.hash_gate.call(lambda:self.verify(phc,value),deadline=deadline)
            except admission.Expired as ex:raise Capacity(str(ex)) from None
        crypto.phc_bounded(phc);jid=secrets.randbits(62)
        deadline=admission.CURRENT.get()
        until=datetime.now(timezone.utc)+timedelta(seconds=admission.live())
        def admit(mode):
            return self.database('auth','SELECT identity_api.lab3_hash_admission(%s,%s,%s,%s,%s,%s)',(schema.ticket('auth',2),mode,jid,self.worker_pid,self.worker_creation,until))['state']
        state='none';unresolved=False;expensive=0
        try:
            unresolved=True;state=admit('enter');unresolved=False
            while state=='queued':
                time.sleep(min(.01,admission.live()));unresolved=True;state=admit('poll');unresolved=False
            if state!='active':raise Capacity('global_hash_capacity')
            admission.live()
            admission.granted()
            with self.lock:self.expensive_calls+=1
            expensive=1
            with rd.stage('hash.verify'):return crypto.verify(phc,value)
        finally:
            if unresolved:admission.reclaim_unstarted(admit,expensive_calls=expensive);state='none'
            if state=='active':
                token=admission.CURRENT.set(time.monotonic()+5)
                try:admit('finish')
                finally:admission.CURRENT.reset(token)
            elif state=='queued':
                token=admission.CURRENT.set(time.monotonic()+5)
                try:admit('cancel')
                finally:admission.CURRENT.reset(token)
    def authenticate(self,username,password,entered):
        def work():
            account='login-'+digest(username)
            jid=secrets.randbits(62);until=datetime.now(timezone.utc)+timedelta(seconds=admission.live());state='none';failed=False;unresolved=False
            metrics={'entered':entered,'no_hash_baseline':self.no_hash_baseline,'actual_expensive_calls':0,'hash_ms':None,'queue_ms':None}
            try:
                unresolved=True
                packet=self.database('auth','SELECT identity_api.lab3_login_admit(%s,%s,%s,%s,%s,%s,%s,%s)',(schema.ticket('auth',2),account,self.namespace,self.aliases.get(username,username),jid,self.worker_pid,self.worker_creation,until))
                unresolved=False
                if not packet['budget_allowed']:return None,False,'failure_budget'
                credential=packet['credential'];state=packet['admission']['state']
                while state=='queued':
                    time.sleep(min(.01,admission.live()))
                    unresolved=True
                    state=self.database('auth','SELECT identity_api.lab3_hash_admission(%s,%s,%s,%s,%s,%s)',(schema.ticket('auth',2),'poll',jid,self.worker_pid,self.worker_creation,until))['state']
                    unresolved=False
                if state!='active':raise Capacity('global_hash_capacity')
                admission.live()
                metrics['queue_ms']=(time.monotonic()-entered)*1000
                admission.granted()
                if self.no_hash_baseline:valid=credential is not None and secrets.compare_digest(password,self.password)
                else:
                    with self.lock:self.expensive_calls+=1
                    before=time.monotonic();metrics['actual_expensive_calls']=1
                    with rd.stage('hash.verify'):valid=crypto.verify(credential['phc'] if credential else self.dummy_phc,password)
                    metrics['hash_ms']=(time.monotonic()-before)*1000
                failed=credential is None or not valid
                return credential,valid,'verified'
            finally:
                if unresolved:
                    admission.reclaim_unstarted(lambda mode:self.database('auth','SELECT identity_api.lab3_hash_admission(%s,%s,%s,%s,%s,%s)',(schema.ticket('auth',2),mode,jid,self.worker_pid,self.worker_creation,until)),expensive_calls=metrics['actual_expensive_calls'])
                    state='none'
                if state=='active':
                    token=admission.CURRENT.set(time.monotonic()+5)
                    try:self.database('auth','SELECT identity_api.lab3_login_finish(%s,%s,%s,%s,%s,%s,%s)',(schema.ticket('auth',2),account,self.namespace,jid,self.worker_pid,self.worker_creation,failed))
                    finally:admission.CURRENT.reset(token)
                elif state=='queued':
                    token=admission.CURRENT.set(time.monotonic()+5)
                    try:self.database('auth','SELECT identity_api.lab3_hash_admission(%s,%s,%s,%s,%s,%s)',(schema.ticket('auth',2),'cancel',jid,self.worker_pid,self.worker_creation,until))
                    finally:admission.CURRENT.reset(token)
                metrics['worker_ms']=(time.monotonic()-entered)*1000
                with self.lock:self.admission_rows.append(metrics)
        try:
            with rd.stage('admission.call'):return self.hash_gate.call(work,deadline=entered+1)
        except admission.Expired as ex:raise Capacity(str(ex)) from None
    def login(self,actor=1,mfa_complete=False,password_version=None,mfa_verified_at=None):
        token=secrets.token_urlsafe(32);self.markers.add(token)
        with _SESSION_ID_LOCK:self.next_id=next(_SESSION_IDS);sid=self.next_id
        if password_version is None:
            with self.lease.connect('bootstrap') as c:password_version=c.execute('SELECT version FROM identity_control.lab3_credentials WHERE owner=%s',(actor,)).fetchone()[0]
        row=self.sql('SELECT identity_api.lab3_establish(%s,%s,%s,%s)',(schema.ticket('session',actor),sid,digest(token),password_version))
        record=dict(id=sid,actor=actor,csrf=secrets.token_urlsafe(32),mfa_complete=mfa_complete,mfa_verified_at=mfa_verified_at,**row)
        with self.lock:self.sessions[digest(token)]=record
        return token,record
    def ticket(self,token):
        record=self.session(token);ticket=secrets.token_urlsafe(32);self.markers.add(ticket)
        self.sql('SELECT identity_api.lab3_ticket(%s,\'issue\',%s,%s,%s)',(schema.ticket('session',record['actor']),digest(ticket),record['id'],record['epoch']))
        return ticket
    def handler(self):
        lab=self
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*a):pass
            def reply(self,code,value,cookie=None,html=False):
                data=value.encode() if html else json.dumps(value,separators=(',',':')).encode()
                self.send_response(code);self.send_header('Cache-Control','no-store');self.send_header('Pragma','no-cache');self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json')
                self.send_header('Content-Length',str(len(data)));self.send_header('Connection','close')
                if cookie:self.send_header('Set-Cookie',cookie)
                trace=rd.CURRENT.get()
                if trace is not None:trace.response_status_attempted=code
                try:self.end_headers();self.wfile.write(data)
                except BaseException as ex:rd.failure(ex,'HTTP.response_write',secondary=True);raise
                if trace is not None:trace.response_write_returned=True
            def do_GET(self):
                with lab.request_log.request('GET',self.path,self.headers.get('X-Lab-Request-ID')):
                    token=admission.CURRENT.set(time.monotonic()+5)
                    try:return self.get_body()
                    finally:admission.CURRENT.reset(token)
            def get_body(self):
                try:
                    if self.path=='/lab/page':self.reply(200,'<!doctype html><meta charset=utf-8><title>PR03b synthetic lab</title><p>Isolated mechanism prototype</p>',html=True);return
                    if self.path=='/lab/preauth':
                        previous=cookie_values(self.headers.get('Cookie')).get('__Host-classroom_preauth','')
                        with lab.lock:existing=lab.preauth.get(digest(previous));lab.preauth={k:v for k,v in lab.preauth.items() if time.monotonic()<v['expires']}
                        if existing and time.monotonic()<existing['expires']:self.reply(200,{'csrf':existing['csrf']});return
                        if not lab.database('auth','SELECT identity_api.lab3_preauth(%s,%s)',(schema.ticket('auth',2),lab.namespace)):self.reply(429,{'code':'RATE_LIMITED'});return
                        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
                        with lab.lock:
                            b.require(len(lab.preauth)<200,'preauth_capacity');lab.preauth[digest(token)]={'csrf':csrf,'expires':time.monotonic()+300}
                        self.reply(200,{'csrf':csrf},'__Host-classroom_preauth='+token+'; Secure; HttpOnly; Path=/; SameSite=Lax');return
                    token=cookie_values(self.headers.get('Cookie')).get('__Host-classroom_session','')
                    if self.path=='/lab/business':self.reply(200,lab.business(token));return
                    record=lab.session(token)
                    if self.path=='/lab/me':self.reply(200,{'authenticated':True,'owner':record['owner']});return
                    self.reply(403,{'code':'FORBIDDEN'})
                except BaseException as ex:
                    rd.failure(ex,'GET.dispatch');self.reply(401,{'code':'UNAUTHENTICATED'})
            def do_POST(self):
                with lab.request_log.request('POST',self.path,self.headers.get('X-Lab-Request-ID')):
                    entered=time.monotonic()
                    token=admission.CURRENT.set(entered+5)
                    try:return self.post_body(entered)
                    finally:admission.CURRENT.reset(token)
            def post_body(self,entered):
                try:
                    n=int(self.headers.get('Content-Length','0'));limit=32768 if self.path=='/lab/reauth' else 16384
                    if not 0<n<=limit:self.reply(422,{'code':'VALIDATION_FAILED'});return
                    data=b.strict(self.rfile.read(n));origin=self.headers.get('Origin');cookies=cookie_values(self.headers.get('Cookie'))
                    b.require(origin==lab.origin and self.headers.get('Content-Type')=='application/json','origin_or_type')
                    if self.path=='/lab/login':
                        token=cookies.get('__Host-classroom_preauth','')
                        with lab.lock:pre=lab.preauth.get(digest(token))
                        b.require(pre is not None and time.monotonic()<pre['expires'] and self.headers.get('X-CSRF-Token')==pre['csrf'],'preauth_csrf')
                        b.require(set(data)=={'username','password','remember_me'} and type(data['remember_me']) is bool,'login_shape')
                        if type(data['username']) is not str or not login_password(data['password']):self.reply(422,{'code':'VALIDATION_FAILED'});return
                        credential,valid,reason=lab.authenticate(data['username'],data['password'],entered)
                        if reason=='failure_budget':self.reply(429,{'code':'RATE_LIMITED'});return
                        known=credential is not None
                        if not known or not valid:
                            self.reply(401,{'code':'UNAUTHENTICATED'});return
                        with lab.lock:b.require(lab.preauth.pop(digest(token),None) is not None,'preauth_used')
                        actor=credential['owner'];b.require(actor in lab.allowed_web_actors,'only_fixed_HTTP_fixture_accounts')
                        if actor in (1,3):
                            challenge=secrets.token_urlsafe(32);lab.markers.add(challenge)
                            expiry=lab.sql("SELECT clock_timestamp()+interval '300 seconds'")
                            with lab.lock:lab.pending_mfa[digest(challenge)]={'actor':actor,'password_version':credential['version'],'csrf':pre['csrf'],'expires':time.monotonic()+300,'db_expires':expiry,'used':False}
                            self.reply(200,{'state':'mfa_required','challenge':challenge});return
                        session,record=lab.login(actor,password_version=credential['version']);suffix='; Max-Age=2592000' if data['remember_me'] else ''
                        self.reply(200,{'state':'authenticated','csrf':record['csrf']},'__Host-classroom_session='+session+'; Secure; HttpOnly; Path=/; SameSite=Lax'+suffix);return
                    if self.path=='/lab/mfa':
                        b.require(set(data)=={'challenge','code'},'mfa_shape')
                        with lab.lock:p=lab.pending_mfa.get(digest(data['challenge']))
                        b.require(p is not None and not p['used'] and time.monotonic()<p['expires'] and self.headers.get('X-CSRF-Token')==p['csrf'],'mfa_context')
                        b.require(lab.budget('mfa-'+str(p['actor']),5,900),'mfa_budget')
                        step=int(time.time()//30)
                        if not secrets.compare_digest(crypto.totp(lab.mfa_seed,step).decode(),str(data['code'])):
                            lab.budget('mfa-'+str(p['actor']),5,900,True);self.reply(401,{'code':'UNAUTHENTICATED'});return
                        verified_at=lab.sql('SELECT identity_api.lab3_mfa_step(%s,%s,%s)',(schema.ticket('session',p['actor']),step,p['db_expires']))
                        with lab.lock:b.require(not p['used'],'mfa_already_used');p['used']=True
                        token,record=lab.login(p['actor'],mfa_complete=True,password_version=p['password_version'],mfa_verified_at=verified_at)
                        self.reply(200,{'state':'authenticated','csrf':record['csrf']},'__Host-classroom_session='+token+'; Secure; HttpOnly; Path=/; SameSite=Lax');return
                    session=cookies.get('__Host-classroom_session','');record=lab.session(session)
                    b.require(self.headers.get('X-CSRF-Token')==record['csrf'],'session_csrf')
                    if self.path=='/lab/ws-ticket':
                        b.require(data=={},'ticket_shape');self.reply(200,{'ticket':lab.ticket(session)});return
                    if self.path=='/lab/reauth':
                        if not login_password(data.get('password')):self.reply(422,{'code':'VALIDATION_FAILED'});return
                        credential=lab.lookup(lab.aliases[{1:'synthetic-student-mfa',2:'synthetic-student',3:'synthetic-admin'}[record['actor']]])
                        b.require(credential['owner']==record['actor'],'reauth_current_password_owner')
                        b.require(record['mfa_complete'] and set(data)=={'password','operation'} and lab.verify(credential['phc'],data['password']),'reauth_factor')
                        mfa_until=record['mfa_verified_at']+timedelta(seconds=120)
                        b.require(lab.sql('SELECT clock_timestamp()')<mfa_until,'R3_MFA_freshness')
                        operation=data['operation'];b.require(set(operation)=={'purpose','key','body'} and operation['purpose']=='rotateRecoveryCodes' and operation['body']=={},'reauth_scope')
                        token=secrets.token_urlsafe(32);lab.markers.add(token);binding=crypto.binding(operation,lab.hmac_key).hex()
                        lab.sql('SELECT identity_api.lab3_web_proof(%s,%s,%s,%s,%s)',(schema.ticket('session',record['actor']),digest(token),binding,record['epoch'],mfa_until))
                        self.reply(200,{'token':token});return
                    if self.path=='/lab/codes':
                        b.require(record['mfa_complete'],'codes_full_factor_login')
                        b.require(data=={},'codes_shape');key=self.headers.get('Idempotency-Key');uuid.UUID(key)
                        binding=crypto.binding({'purpose':'rotateRecoveryCodes','key':key,'body':{}},lab.hmac_key).hex()
                        codes=[secrets.token_hex(16) for _ in range(10)];lab.markers.update(codes)
                        outcome=lab.sql('SELECT identity_api.lab3_codes(%s,%s,%s,%s,%s)',(schema.ticket('session',record['actor']),digest(self.headers.get('X-Reauth-Token','')),binding,key,[digest(v) for v in codes]))
                        if outcome['secret_already_issued']:self.reply(409,{'code':'SECRET_ALREADY_ISSUED'});return
                        lab.issued_codes.append(outcome['generation'])
                        with lab.lock:drop=lab.drop_next_codes;lab.drop_next_codes=False
                        if drop:
                            lab.dropped_code_commits+=1;self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
                        self.reply(200,{'codes':codes});return
                    self.reply(403,{'code':'FORBIDDEN'})
                except (BrokenPipeError,ConnectionResetError) as ex:rd.failure(ex,'POST.client_disconnected',secondary=True)
                except Capacity as ex:
                    rd.failure(ex,'POST.capacity');self.reply(429,{'code':'RATE_LIMITED'})
                except BaseException as ex:
                    rd.failure(ex,'POST.dispatch');self.reply(403,{'code':'FORBIDDEN'})
        return Handler
    def ws_start(self):
        from websockets.sync.server import serve,ServerConnection
        from websockets.http11 import Response
        from websockets.datastructures import Headers
        self.ws_trace=[]
        def trace(stage,**values):self.ws_trace.append({'stage':stage,'seconds':round(time.monotonic(),6),**values})
        class ObservedServerConnection(ServerConnection):
            def __init__(conn,*args,**kwargs):
                conn.s3_counted=False;conn.s3_unauthenticated=False;conn.s3_closed=False
                conn.s3_diag=len(self.ws_trace);trace('server_TLS_complete_construct',connection=conn.s3_diag)
                super().__init__(*args,**kwargs)
            def handshake(conn,*args,**kwargs):
                trace('server_handshake_enter',connection=conn.s3_diag)
                try:
                    result=super().handshake(*args,**kwargs)
                    trace('server_handshake_complete',connection=conn.s3_diag);return result
                except BaseException as ex:
                    release(conn)
                    trace('server_handshake_error',connection=conn.s3_diag,error_type=type(ex).__name__);raise
            def close_socket(conn):
                with self.lock:conn.s3_closed=True
                try:return super().close_socket()
                finally:release(conn)
        def release(conn):
            # recv thread, handshake thread and handler may all arrive here.
            # One reservation has one idempotent release under the same lock.
            with self.lock:
                if conn.s3_counted:
                    self.ws_counts[0]-=1
                    if conn.s3_unauthenticated:self.ws_counts[1]-=1
                    conn.s3_counted=False;conn.s3_unauthenticated=False
        def before(conn,request):
            trace('process_request_enter',connection=conn.s3_diag)
            try:
                b.require(request.path=='/lab/ws' and request.headers.get('Origin')==self.origin,'ws_origin_path')
                trace('session_PG_enter',connection=conn.s3_diag)
                self.session(cookie_values(request.headers.get('Cookie')).get('__Host-classroom_session',''))
                trace('session_validated',connection=conn.s3_diag)
                with self.lock:
                    b.require(not conn.s3_closed and self.ws_counts[0]<64 and self.ws_counts[1]<8,'ws_capacity');self.ws_counts[0]+=1;self.ws_counts[1]+=1
                    conn.s3_counted=True;conn.s3_unauthenticated=True
                conn.s3_upgrade=time.monotonic()
            except BaseException as ex:
                trace('upgrade_denied',connection=conn.s3_diag,error_type=type(ex).__name__,sqlstate=getattr(ex,'sqlstate',None),PG_fault_injected=self.fault_pg or self.fault_sql_timeout)
                return Response(403,'Forbidden',Headers([('Content-Length','0'),('Cache-Control','no-store')]),b'')
        def connection(ws):
            trace('application_enter',connection=ws.s3_diag)
            authenticated=False
            try:
                b.require(ws.subprotocol=='classroom.v1','ws_subprotocol')
                trace('first_frame_wait',connection=ws.s3_diag)
                raw=ws.recv(timeout=max(.01,5-(time.monotonic()-ws.s3_upgrade)))
                trace('first_frame_received',connection=ws.s3_diag)
                b.require(type(raw) is str and len(raw.encode())<=4096,'ws_first_shape');message=b.strict(raw.encode())
                b.require(set(message)=={'type','ticket'} and message['type']=='authenticate','ws_first_auth')
                token=cookie_values(ws.request.headers.get('Cookie')).get('__Host-classroom_session','');record=self.session(token)
                ok=self.sql('SELECT identity_api.lab3_ticket(%s,\'consume\',%s,%s,%s)',(schema.ticket('session',record['actor']),digest(message['ticket']),record['id'],record['epoch']))
                b.require(ok,'ws_ticket_single_use');authenticated=True
                trace('ticket_authenticated',connection=ws.s3_diag)
                with self.lock:
                    if ws.s3_unauthenticated:self.ws_counts[1]-=1;ws.s3_unauthenticated=False
                ws.send('{"type":"authenticated"}')
                for raw in ws:
                    trace('sensitive_PG_enter',connection=ws.s3_diag)
                    self.session(token)
                    b.require(type(raw) is str,'ws_no_binary');msg=b.strict(raw.encode());b.require(msg=={'type':'sensitive_probe'},'ws_message')
                    ws.send('{"type":"accepted","provider_calls":0}')
            except BaseException as ex:
                trace('application_denied',connection=ws.s3_diag,error_type=type(ex).__name__,sqlstate=getattr(ex,'sqlstate',None),PG_fault_injected=self.fault_pg or self.fault_sql_timeout)
                try:ws.close(code=1008,reason='authorization_required')
                except Exception:pass
            finally:
                trace('application_exit',connection=ws.s3_diag)
                release(ws)
        self.ws_server=serve(connection,'127.0.0.1',0,ssl=self.context,subprotocols=['classroom.v1'],process_request=before,max_size=4096,open_timeout=5,close_timeout=2,create_connection=ObservedServerConnection)
        def serve_forever():
            trace('serve_loop_enter')
            try:self.ws_server.serve_forever()
            except BaseException as ex:trace('serve_loop_error',error_type=type(ex).__name__);raise
            finally:trace('serve_loop_exit')
        self.ws_port=self.ws_server.socket.getsockname()[1];self.ws_thread=threading.Thread(target=serve_forever,name='S3-WSS-accept',daemon=True);self.ws_thread.start()
    def close(self):
        if self.ws_server:self.ws_server.shutdown();self.ws_thread.join(5)
        self.http.shutdown();self.http.server_close();self.thread.join(5)
        self.http.wait_drained()
        try:
            self.hash_gate.close()
            self.closing=True
            self.close_pool()
        finally:
            b.write(self.lease.plan.run_root/('R02-HTTP-DIAGNOSTICS-'+self.namespace+'.json'),dict(self.http.diagnostics(),request_diagnostics=self.request_log.snapshot()))
        # Only synthetic marker comparisons leave this process. The legitimate
        # TLS private directory, PGDATA and browser profile are never exported.
        import os,sys
        marker_bytes={v.encode() for v in self.markers if len(v)>=16}
        for line in (self.root/'tls-key.pem').read_text().splitlines():
            if len(line)>=32 and not line.startswith('---'):marker_bytes.add(line.encode())
        paths=list(self.lease.plan.run_root.glob('*.log'))+list(b.HOME.glob('coordinator.*.log'));hits=0;files=[]
        for path in paths:
            raw=path.read_bytes();found=sum(marker in raw for marker in marker_bytes);hits+=found
            files.append({'file':path.name,'bytes':len(raw),'marker_hits':found,'sha256':hashlib.sha256(raw).hexdigest()})
        process_text=json.dumps([sys.argv,dict(os.environ)]).encode();process_hits=sum(marker in process_text for marker in marker_bytes);hits+=process_hits
        dumps=list(self.lease.plan.run_root.rglob('*.dmp'))+list(b.HOME.glob('*.dmp'))
        self.audit_result={'synthetic_high_entropy_markers':len(marker_bytes),'forbidden_log_files':files,'argv_env_marker_hits':process_hits,'marker_hits':hits,'owned_dump_files':len(dumps),'legitimate_secret_files_exported':False,'low_entropy_TOTP_not_exported_or_substring_scanned':True}
        b.write(self.lease.plan.run_root/('SECRET-AUDIT-'+self.namespace+'.json'),self.audit_result)
        b.require(hits==0 and not dumps,'synthetic_secret_or_dump_in_forbidden_sink')
def request(lab,path,body=None,session=None,csrf=None,origin=None,headers=None):
    c=http.client.HTTPSConnection('127.0.0.1',lab.port,context=lab.client_context,timeout=5)
    h={'Content-Type':'application/json','Origin':origin or lab.origin}
    if session:h['Cookie']='__Host-classroom_session='+session
    if csrf:h['X-CSRF-Token']=csrf
    if headers:h.update(headers)
    raw=None if body is None else json.dumps(body).encode()
    try:
        c.request('GET' if body is None else 'POST',path,raw,h);r=c.getresponse();payload=r.read();meta=dict(r.getheaders())
        return r.status,json.loads(payload),meta
    finally:c.close()

def diagnostic_connect(lab,uri,*,context_label,**kwargs):
    """Same five-second library budget; capture before connect's cleanup."""
    from websockets.sync.client import ClientConnection,connect
    class Client(ClientConnection):
        def __init__(client,*args,**kw):
            lab.ws_trace.append({'stage':'client_TCP_TLS_complete','client':context_label,'seconds':time.monotonic()})
            super().__init__(*args,**kw)
        def handshake(client,*args,**kw):
            lab.ws_trace.append({'stage':'client_upgrade_enter','client':context_label,'seconds':time.monotonic()})
            try:return super().handshake(*args,**kw)
            except TimeoutError:
                import sys,traceback,native_process,os
                from dataclasses import asdict
                frames=sys._current_frames()
                threads=[{'thread':t.name,'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_stack(frames[t.ident])[-16:]]} for t in threading.enumerate() if t.ident in frames]
                rows=[asdict(v) for v in native_process.tcp_rows({os.getpid()}) if v.local_port==lab.ws_port or v.remote_port==lab.ws_port]
                b.write(b.HOME/'evidence'/('WORKER-STOP-WSS-'+context_label+'.json'),{'kind':'same_connection_before_library_cleanup','client_label':context_label,'trace':list(lab.ws_trace),'threads':threads,'tcp_rows':rows,'ordinary_budget_seconds':5,'client':{'response_received':client.response_rcvd.is_set(),'recv_thread_alive':client.recv_events_thread.is_alive(),'recv_error_type':type(client.recv_exc).__name__ if client.recv_exc else None,'socket_fileno':client.socket.fileno()}})
                raise
    return connect(uri,create_connection=Client,**kwargs)
def experiments(lease,check,root,plan,env):
    from websockets.sync.client import connect
    from websockets.exceptions import ConnectionClosed,InvalidStatus
    def closed_as(ws,label,codes,timeout=5,started=None):
        try:ws.recv(timeout=timeout)
        except ConnectionClosed as ex:
            code=ex.rcvd.code if ex.rcvd is not None else None
            elapsed=None if started is None else time.monotonic()-started
            check(label,code in codes and (elapsed is None or 4.5<=elapsed<7),
                {'received_close_code':code,'elapsed_seconds':None if elapsed is None else round(elapsed,3)})
        else:check(label,False)
    lab=Lab(lease,root);lab.ws_start()
    try:
        token,record=lab.login()
        code,data,headers=request(lab,'/lab/me',session=token);check('HTTPS_actual_CA_verified_session',code==200 and headers['Cache-Control']=='no-store')
        expensive_before=lab.expensive_calls
        for path,limit in (('/lab/login',16384),('/lab/reauth',32768)):
            for offset,expected in ((0,403),(1,422)):
                value={'password':lab.password,'operation':{},'padding':''} if path=='/lab/reauth' else {'x':''}
                padding='padding' if path=='/lab/reauth' else 'x';size=len(json.dumps(value,separators=(',',':')).encode())
                value[padding]='x'*(limit+offset-size);raw=json.dumps(value,separators=(',',':')).encode()
                c=http.client.HTTPSConnection('127.0.0.1',lab.port,context=lab.client_context,timeout=5)
                try:
                    c.request('POST',path,raw,{'Origin':lab.origin,'Content-Type':'application/json','Cookie':'__Host-classroom_session='+token,'X-CSRF-Token':record['csrf']});response=c.getresponse();response.read()
                    check('HTTPS_raw_body_boundary:'+path+':'+str(offset),len(raw)==limit+offset and response.status==expected)
                finally:c.close()
        check('HTTPS_body_rejection_zero_expensive_calls',lab.expensive_calls==expensive_before)
        for origin in ('https://evil.test:'+str(lab.port),lab.origin+'.evil','null','https://app.test:'+str(lab.port+1)):
            code,_,_=request(lab,'/lab/ws-ticket',{},token,record['csrf'],origin=origin);check('HTTPS_wrong_exact_Origin:'+origin,code==403)
        code,_,_=request(lab,'/lab/ws-ticket',{},token,'wrong');check('HTTPS_wrong_CSRF',code==403)
        code,_,_=request(lab,'/lab/recovery/complete',{},token,record['csrf']);check('browser_no_manual_complete',code==403)
        # Exercise final -> no codes -> full password/new-MFA login -> explicit
        # generation. The fixed verifier installs only synthetic AEAD material.
        import cases
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        cases.fixture(lease.connect,610000,ready=True);cases.proof(lease.connect,610000,610000)
        kek=AESGCM.generate_key(bit_length=256);iv=secrets.token_bytes(12);cipher=AESGCM(kek).encrypt(iv,lab.mfa_seed,b'synthetic-new-factor')
        with lease.connect('bootstrap') as c:
            c.execute('BEGIN READ WRITE');c.execute('UPDATE identity_control.lab3_factors SET encrypted=%s WHERE owner=1 AND generation=610000',(iv+cipher,));c.execute('COMMIT')
            group_count=c.execute('SELECT count(*) FROM identity_control.lab3_groups WHERE owner=1').fetchone()[0]
        with lease.connect('recovery') as c:
            c.execute('BEGIN READ WRITE');outcome=cases.write(c,610000);c.execute('COMMIT')
            check('N02_final_zero_new_secrets',outcome['new_recovery_codes_issued'] is False)
        with lease.connect('bootstrap') as c:
            check('N02_final_only_revokes_old_groups',c.execute('SELECT count(*),count(*) FILTER(WHERE NOT revoked) FROM identity_control.lab3_groups WHERE owner=1').fetchone()==(group_count,0))
            stored=bytes(c.execute("SELECT encrypted FROM identity_control.lab3_factors WHERE owner=1 AND generation=610000 AND state='active'").fetchone()[0])
        lab.mfa_seed=AESGCM(kek).decrypt(stored[:12],stored[12:],b'synthetic-new-factor')
        check('N02_login_uses_committed_encrypted_new_factor',len(lab.mfa_seed)==20)
        code,_,_=request(lab,'/lab/me',session=token);check('N02_old_browser_revoked',code==401)
        code,pre,headers=request(lab,'/lab/preauth');pre_cookie=headers['Set-Cookie'].split(';',1)[0]
        code,pending,headers=request(lab,'/lab/login',{'username':'synthetic-student-mfa','password':lab.password,'remember_me':False},csrf=pre['csrf'],headers={'Cookie':pre_cookie})
        check('N02_password_alone_no_session',code==200 and pending['state']=='mfa_required' and 'Set-Cookie' not in headers)
        challenge=pending['challenge']
        code,_,_=request(lab,'/lab/mfa',{'challenge':challenge,'code':'invalid'},csrf=pre['csrf']);check('MFA_wrong_code_denied',code==401)
        code,full,headers=request(lab,'/lab/mfa',{'challenge':challenge,'code':crypto.totp(lab.mfa_seed,int(time.time()//30)).decode()},csrf=pre['csrf'])
        check('N02_actual_new_MFA_full_relogin',code==200 and full['state']=='authenticated')
        token=headers['Set-Cookie'].split(';',1)[0].split('=',1)[1];record=lab.sessions[digest(token)]
        code,_,_=request(lab,'/lab/mfa',{'challenge':challenge,'code':crypto.totp(lab.mfa_seed,int(time.time()//30)).decode()},csrf=pre['csrf']);check('MFA_challenge_replay_denied',code==403)
        key=str(uuid.uuid4());operation={'purpose':'rotateRecoveryCodes','key':key,'body':{}}
        code,response,_=request(lab,'/lab/reauth',{'password':lab.password,'operation':operation},token,record['csrf']);check('HTTPS_R3_bound_proof',code==200)
        reauth=response['token'];code,response,headers=request(lab,'/lab/codes',{},token,record['csrf'],headers={'Idempotency-Key':key,'X-Reauth-Token':reauth})
        check('N02_full_login_explicit_new_group',code==200 and len(response['codes'])==len(set(response['codes']))==10 and headers['Cache-Control']=='no-store')
        code,response,_=request(lab,'/lab/codes',{},token,record['csrf'],headers={'Idempotency-Key':key,'X-Reauth-Token':reauth})
        check('N02_successful_secret_response_not_replayed',code==409 and response=={'code':'SECRET_ALREADY_ISSUED'})
        lost_key=str(uuid.uuid4());lost_op={'purpose':'rotateRecoveryCodes','key':lost_key,'body':{}}
        code,response,_=request(lab,'/lab/reauth',{'password':lab.password,'operation':lost_op},token,record['csrf']);check('N02_lost_response_fresh_proof',code==200)
        lost_proof=response['token'];lab.drop_next_codes=True
        try:request(lab,'/lab/codes',{},token,record['csrf'],headers={'Idempotency-Key':lost_key,'X-Reauth-Token':lost_proof})
        except (http.client.RemoteDisconnected,ConnectionResetError,ssl.SSLError):check('N02_actual_postcommit_TLS_response_loss',lab.dropped_code_commits==1)
        else:check('N02_actual_postcommit_TLS_response_loss',False)
        code,response,_=request(lab,'/lab/codes',{},token,record['csrf'],headers={'Idempotency-Key':lost_key,'X-Reauth-Token':lost_proof})
        check('N02_dropped_secret_response_not_replayed',code==409 and response=={'code':'SECRET_ALREADY_ISSUED'})
        rotate_key=str(uuid.uuid4());rotate_op={'purpose':'rotateRecoveryCodes','key':rotate_key,'body':{}}
        code,response,_=request(lab,'/lab/reauth',{'password':lab.password,'operation':rotate_op},token,record['csrf']);check('N02_explicit_after_loss_new_proof',code==200)
        code,response,_=request(lab,'/lab/codes',{},token,record['csrf'],headers={'Idempotency-Key':rotate_key,'X-Reauth-Token':response['token']})
        check('N02_explicit_after_loss_rotates_whole_group',code==200 and len(response['codes'])==10)
        actual_mfa=record['mfa_verified_at'];record['mfa_verified_at']=actual_mfa-timedelta(seconds=300)
        code,_,_=request(lab,'/lab/reauth',{'password':lab.password,'operation':{'purpose':'rotateRecoveryCodes','key':str(uuid.uuid4()),'body':{}}},token,record['csrf'])
        check('R3_stale_MFA_not_refreshed_by_password',code==403);record['mfa_verified_at']=actual_mfa
        url='wss://127.0.0.1:'+str(lab.ws_port)+'/lab/ws'
        from websockets.sync.client import ClientConnection
        class ObservedClientConnection(ClientConnection):
            def handshake(client,*args,**kwargs):
                try:return super().handshake(*args,**kwargs)
                except TimeoutError:
                    # Capture while receive/server threads still exist. The
                    # library's connect() cleans them before ws_open sees it.
                    import sys,traceback,native_process,os
                    from dataclasses import asdict
                    frames=sys._current_frames()
                    threads=[{'thread':t.name,'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_stack(frames[t.ident])[-16:]]} for t in threading.enumerate() if t.ident in frames]
                    rows=[asdict(v) for v in native_process.tcp_rows({os.getpid()}) if v.local_port==lab.ws_port or v.remote_port==lab.ws_port]
                    b.write(b.HOME/'evidence/WORKER-STOP-WSS-PRECLEANUP.json',{'kind':'WSS_handshake_timeout_before_library_cleanup','trace':list(lab.ws_trace),'threads':threads,'tcp_rows':rows,'ordinary_budget_seconds':5,'client':{'response_received':client.response_rcvd.is_set(),'recv_thread_alive':client.recv_events_thread.is_alive(),'recv_error_type':type(client.recv_exc).__name__ if client.recv_exc else None,'socket_fileno':client.socket.fileno()}})
                    raise
        def ws_open(origin=None,path=None):
            try:return connect(path or url,ssl=lab.client_context,origin=origin or lab.origin,subprotocols=['classroom.v1'],additional_headers={'Cookie':'__Host-classroom_session='+token},open_timeout=5,close_timeout=2,max_size=4096,create_connection=ObservedClientConnection)
            except TimeoutError:
                import sys,traceback,native_process,os
                from dataclasses import asdict
                frames=sys._current_frames()
                threads=[{'thread':t.name,'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_stack(frames[t.ident])[-12:]]} for t in threading.enumerate() if t.ident in frames]
                rows=[asdict(v) for v in native_process.tcp_rows({os.getpid()}) if v.local_port==lab.ws_port or v.remote_port==lab.ws_port]
                b.write(b.HOME/'evidence/WORKER-STOP-WSS.json',{'kind':'WSS_handshake_timeout','trace':lab.ws_trace,'threads':threads,'tcp_rows':rows,'ws_thread_alive':lab.ws_thread.is_alive(),'ordinary_budget_seconds':5})
                raise
        ticket=lab.ticket(token)
        with ws_open() as ws:
            ws.send(json.dumps({'type':'authenticate','ticket':ticket}));check('WSS_real_upgrade_and_ticket',json.loads(ws.recv(timeout=5))=={'type':'authenticated'})
            ws.send('{"type":"sensitive_probe"}');check('WSS_sensitive_command_PG_check',json.loads(ws.recv(timeout=5))['provider_calls']==0)
        handshake_controls=[]
        for repeat in range(20):
            fresh_ticket=lab.ticket(token);started=time.monotonic()
            with ws_open() as ws:
                elapsed=time.monotonic()-started
                ws.send(json.dumps({'type':'authenticate','ticket':fresh_ticket}));authenticated=json.loads(ws.recv(timeout=5))=={'type':'authenticated'}
                check('WSS_repeated_fresh_upgrade_auth:'+str(repeat),elapsed<5 and authenticated,{'upgrade_seconds':round(elapsed,6),'ordinary_open_budget_seconds':5})
                handshake_controls.append(round(elapsed,6))
        with ws_open() as ws:
            ws.send(json.dumps({'type':'authenticate','ticket':ticket}))
            closed_as(ws,'WSS_ticket_replay_denied',{1008})
        for raw,label in ((b'not-auth','binary'),('{"type":"sensitive_probe"}','business_before_auth'),('x'*4097,'oversize')):
            with ws_open() as ws:
                ws.send(raw)
                closed_as(ws,'WSS_first_frame_'+label,{1009} if label=='oversize' else {1008},timeout=6)
        before=time.monotonic()
        with ws_open() as ws:
            closed_as(ws,'WSS_first_frame_5s_deadline',{1008},timeout=7,started=before)
        try:ws_open(origin='https://evil.test:'+str(lab.port))
        except InvalidStatus as ex:check('WSS_cross_origin_upgrade_denied',ex.response.status_code==403,{'handshake_status':ex.response.status_code})
        else:check('WSS_cross_origin_upgrade_denied',False)
        with ws_open() as ws:
            ws.send(json.dumps({'type':'authenticate','ticket':lab.ticket(token)}));ws.recv(timeout=5)
            lab.session(token,mode='revoke');ws.send('{"type":"sensitive_probe"}')
            closed_as(ws,'WSS_revocation_ignores_lost_notice',{1008})
        # Chromium cookie store/JS/cache and real WebCrypto receiver checks.
        import browser
        result=browser.experiments(lab,plan,env,check)
        return {'HTTPS_port':lab.port,'WSS_port':lab.ws_port,'CA_thumbprint':lab.ca_thumb,'browser':result,'product_endpoints_implemented':False,'repeated_upgrade_seconds':handshake_controls,'WSS_public_trace':lab.ws_trace}
    finally:
        lab.close();check('HTTPS_WSS_browser_secret_markers_zero',lab.audit_result['marker_hits']==0,{'markers':lab.audit_result['synthetic_high_entropy_markers'],'files':len(lab.audit_result['forbidden_log_files'])})
