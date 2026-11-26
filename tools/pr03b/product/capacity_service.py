"""Owned Linux SUT bootstrap/API; secrets arrive only on anonymous stdin."""
import asyncio,base64,contextvars,hashlib,json,os,pathlib,secrets,socket,subprocess,sys,threading,time
from uuid import uuid4

cfg=json.loads(sys.stdin.readline());R=pathlib.Path(cfg['source']);W=pathlib.Path(cfg['work']);RAM=pathlib.Path(cfg['ram']);OUT=pathlib.Path(cfg['output'])
sys.path.insert(0,str(R/'backend'))
import sqlalchemy as sa,psycopg
from classroom_web.test_support import validate_test_database_target,SYNTHETIC_PASSWORD
from classroom_web.infrastructure.auth_crypto import Credentials,LocalPasswordPolicy,ListApproval
from classroom_web.infrastructure.auth_admission import HashAdmission
from classroom_web.infrastructure.auth_repository import PgAuthRepository,VERIFIER,SESSION
from classroom_web.infrastructure.ws_admission import WebSocketAdmission
from classroom_web.infrastructure.database import Database
from classroom_web.application.auth import AuthService
from classroom_web.application.course_read import CourseReader
from classroom_web.domain.data import OwnerContext
from classroom_web.entrypoints.http import create_app
from datetime import datetime,timedelta,timezone

mode=sys.argv[1];PG=W/'pg17-linux-installed/bin';admin=cfg['admin'];pg_port=cfg['pg_port'];data=RAM/'pgdata'
password='Synthetic-capacity-only-20261110'
url=f'postgresql+psycopg://{admin}@127.0.0.1:{pg_port}/pr02a_capacity'
validate_test_database_target(url,environ=os.environ)

if mode=='bootstrap':
    commands=[]
    def call(args,name,timeout=30):
        with (OUT/name).open('wb') as stream:p=subprocess.run(list(map(str,args)),stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
        commands.append(dict(argv=list(map(str,args)),exit_code=p.returncode));return p.returncode
    started=False;children=[]
    try:
        assert not data.exists()
        assert call([PG/'initdb','-D',data,'-U',admin,'-A','trust','--encoding=UTF8','--locale=C'],'initdb.log')==0
        assert call([PG/'pg_ctl','-D',data,'-l',OUT/'postgres.log','-o',f"-h 127.0.0.1 -p {pg_port} -c unix_socket_directories='' -c timezone=UTC -c log_timezone=UTC -c max_connections=100 -c shared_buffers=128MB -c max_wal_size=128MB -c min_wal_size=80MB -c checkpoint_timeout=60s -c log_statement=none -c log_min_error_statement=panic -c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0",'-w','-t','20','start'],'pg-start.log')==0
        started=True
        with psycopg.connect(host='127.0.0.1',port=pg_port,user=admin,dbname='postgres',autocommit=True) as c:
            facts=c.execute("SELECT current_user,host(inet_server_addr()),inet_server_port(),current_setting('data_directory'),version()").fetchone()
            assert facts[0]==admin and facts[1]=='127.0.0.1' and facts[2]==pg_port and pathlib.Path(facts[3]).resolve()==data.resolve()
            c.execute('CREATE DATABASE pr02a_capacity')
        engine=sa.create_engine(url,hide_parameters=True,poolclass=sa.pool.NullPool)
        from alembic.config import Config
        from alembic import command
        ac=Config(str(R/'backend/alembic.ini'));ac.set_main_option('script_location',str(R/'backend/migrations'))
        with engine.connect() as c:
            ac.attributes['connection']=c;command.upgrade(ac,'head');ac.attributes.pop('connection')
        crypto=Credentials(encryption_key=base64.b64decode(cfg['encryption_key']),commitment_key=base64.b64decode(cfg['commitment_key']))
        v,s,r=[cfg[k] for k in ('verifier','session','runtime')]
        with engine.begin() as c:
            for role,functions in ((v,VERIFIER),(s,SESSION)):
                c.exec_driver_sql(f'CREATE ROLE "{role}" LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
                c.exec_driver_sql(f'GRANT USAGE ON SCHEMA identity_api TO "{role}"')
                for f in functions:c.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION identity_api.{f}(jsonb) TO "{role}"')
            c.exec_driver_sql(f'CREATE ROLE "{r}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
            c.exec_driver_sql(f'GRANT classroom_web_runtime TO "{r}"')
            users=[]
            gate=HashAdmission(RAM/'hash-slots')
            for i in range(30):
                uid=str(uuid4());email=f'capacity-{i:02d}@example.test';phc=gate(lambda:crypto.hash(password))
                c.execute(sa.text("INSERT INTO users(id,role,state,normalized_email,email_verified_at,password_hash) VALUES(:id,'student','active',:email,clock_timestamp(),:phc)"),dict(id=uid,email=email,phc=phc))
                users.append(dict(id=uid,email=email))
        runtime=Database(f'postgresql+psycopg://{r}:{SYNTHETIC_PASSWORD}@127.0.0.1:{pg_port}/pr02a_capacity')
        from uuid import UUID
        for user in users:
            with runtime.transaction(OwnerContext(UUID(user['id']))) as tx:
                semester=tx.catalog.create_semester('Synthetic capacity semester');course=tx.catalog.create_course(semester.id,'Synthetic capacity course')
                user['course_id']=str(course.id)
        runtime.dispose();engine.dispose()
        (RAM/'approved-list.txt').write_text(hashlib.sha256(b'synthetic-blocked-password').hexdigest()+'\n')
        for index,port in enumerate(cfg['api_ports']):
            stream=(OUT/f'api-{index}.log').open('wb')
            child=subprocess.Popen([sys.executable,'-I','-B',__file__,'api',str(index)],stdin=subprocess.PIPE,stdout=stream,stderr=subprocess.STDOUT)
            child.stdin.write((json.dumps(cfg)+'\n').encode());child.stdin.flush();children.append((child,stream))
        until=time.monotonic()+30
        while time.monotonic()<until:
            if all((OUT/f'api-{i}-ready.json').exists() for i in range(len(children))):break
            if any(p.poll() is not None for p,_ in children):raise RuntimeError('owned API startup failed')
            time.sleep(.1)
        else:raise RuntimeError('owned API startup timed out')
        print(json.dumps(dict(ready=True,users=users,api_pids=[p.pid for p,_ in children],pg_facts=facts)),flush=True)
        assert sys.stdin.readline().strip()=='stop'
    except BaseException as error:
        print(json.dumps(dict(ready=False,error_kind=type(error).__name__)),flush=True)
        raise SystemExit(1) from None
    finally:
        for p,stream in children:
            try:p.stdin.write(b'stop\n');p.stdin.flush();p.wait(15)
            except (BrokenPipeError,subprocess.TimeoutExpired):p.terminate();p.wait(10)
            stream.close()
        if started:call([PG/'pg_ctl','-D',data,'-m','fast','-w','-t','20','stop'],'pg-stop.log')
        (OUT/'service-commands.json').write_text(json.dumps(commands,indent=2)+'\n')
else:
    from starlette.staticfiles import StaticFiles
    import uvicorn
    index=int(sys.argv[2]);port=cfg['api_ports'][index];origin=f'https://127.0.0.1:{port}'
    operation=contextvars.ContextVar('capacity_operation',default=None);file_lock=threading.RLock()
    metric_file=(OUT/f'service-metrics-{index}.jsonl').open('a',buffering=1)
    def metric(stage,seconds):
        with file_lock:metric_file.write(json.dumps(dict(stage=stage,seconds=seconds,op=operation.get(),time=time.monotonic(),pid=os.getpid()))+'\n')
    class TimedCredentials(Credentials):
        def verify(self,*args):
            start=time.monotonic()
            try:return super().verify(*args)
            finally:metric('hash_compute',time.monotonic()-start)
    from contextlib import contextmanager
    class MeasuredAdmission(HashAdmission):
        @contextmanager
        def _claim(self,indices):
            indices=tuple(indices);kind='active' if indices==tuple(range(2)) else 'wait'
            with super()._claim(indices) as held:
                if held:metric(kind+'_enter',0)
                try:yield held
                finally:
                    if held:metric(kind+'_leave',0)
    class MeasuredAuth(AuthService):
        def reauthenticate(self,ctx,body,**options):
            token=operation.set(body['operation']['candidate_parameters']['idempotency_key'])
            try:return super().reauthenticate(ctx,body,**options)
            finally:operation.reset(token)
        def login(self,ctx,body):
            if cfg['mode'] in ('r05_probe','r04_probe'):return super().login(ctx,body)
            # Only a synthetic ordinal; never log the email or password.
            arm='dummy' if body['email'].startswith('missing-') else 'wrong' if body['password']!=password else 'correct'
            token=operation.set('login_'+arm)
            try:return super().login(ctx,body)
            finally:operation.reset(token)
    crypto=TimedCredentials(encryption_key=base64.b64decode(cfg['encryption_key']),commitment_key=base64.b64decode(cfg['commitment_key']))
    list_path=RAM/'approved-list.txt';policy=LocalPasswordPolicy(list_path,ListApproval(hashlib.sha256(list_path.read_bytes()).hexdigest(),datetime.now(timezone.utc)+timedelta(hours=4)))
    def service_url(name):return f'postgresql+psycopg://{name}:{SYNTHETIC_PASSWORD}@127.0.0.1:{pg_port}/pr02a_capacity'
    repository=PgAuthRepository(verifier_url=service_url(cfg['verifier']),session_url=service_url(cfg['session']))
    auth=MeasuredAuth(repository,crypto,policy,hash_gate=MeasuredAdmission(RAM/'hash-slots',observer=metric))
    database=Database(service_url(cfg['runtime']))
    app=create_app(auth=auth,contract=json.loads((R/'openapi.json').read_text()),origin=origin,
        business=CourseReader(auth=auth,database=database),websocket_admission=WebSocketAdmission(RAM,crypto))
    app.mount('/',StaticFiles(directory=R/'dist',html=True))

    class TimingASGI:
        def __init__(self,app):self.app=app
        async def __call__(self,scope,receive,send):
            if scope['type']!='http':return await self.app(scope,receive,send)
            marker=None
            if cfg['mode'] in ('r05_probe','r04_probe'):
                key=dict(scope.get('headers',[])).get(b'idempotency-key',b'').decode('ascii',errors='ignore')
                try:marker=operation.set(('r05_' if cfg['mode']=='r05_probe' else 'r04_')+str(__import__('uuid').UUID(key)))
                except ValueError:pass
            start=time.monotonic()
            async def timed_send(message):
                if message['type']=='http.response.start':
                    message={**message,'headers':[*message.get('headers',[]),(b'x-pr03b-diagnostic-seconds',str(time.monotonic()-start).encode('ascii'))]}
                await send(message)
            try:return await self.app(scope,receive,timed_send)
            finally:
                if marker is not None:operation.reset(marker)
    server=uvicorn.Server(uvicorn.Config(TimingASGI(app),host='127.0.0.1',port=port,ssl_certfile=str(RAM/'leaf.pem'),ssl_keyfile=str(RAM/'leaf-key.pem'),
        proxy_headers=False,access_log=False,log_level='warning',ws='websockets-sansio',ws_max_size=4096,timeout_graceful_shutdown=3))
    thread=threading.Thread(target=server.run);thread.start()
    until=time.monotonic()+15
    while not server.started and thread.is_alive() and time.monotonic()<until:time.sleep(.02)
    if not server.started:raise SystemExit(2)
    (OUT/f'api-{index}-ready.json').write_text(json.dumps(dict(pid=os.getpid(),origin=origin)))
    try:sys.stdin.readline()
    finally:
        server.should_exit=True;thread.join(10);repository.dispose();database.dispose();metric_file.close()
    if thread.is_alive():raise SystemExit(3)
