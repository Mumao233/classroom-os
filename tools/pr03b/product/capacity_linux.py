"""Disposable network/mount namespaces + explicit cgroup v2 resource groups.

Invoke as a root-owned namespace supervisor, never as a normal app entrypoint.
Only this run's children/data/cgroups are managed. User files/host CA unchanged.
"""
import base64,hashlib,ipaddress,json,os,pathlib,shutil,socket,subprocess,sys,time,uuid,resource,ctypes
from datetime import datetime,timedelta,timezone
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

H=pathlib.Path(__file__).resolve().parent;R=H.parents[2];W=R/'work/pr03b/product-completion-20261110-01';tag=uuid.uuid4().hex;mode=sys.argv[1]
assert os.geteuid()==0 and mode in ('smoke','full','browser','timing','r05_probe','r04_probe') and W.as_posix().startswith('/mnt/j/class-assistant/worktrees/web-pr03b-sessions/work/')
api_count=int(sys.argv[2]) if mode=='r04_probe' and len(sys.argv)==3 else 2
assert api_count in (1,2,4)
RUN=W/('capacity-'+tag);RUN.mkdir();OUT=R/'outputs/pr03b/product-completion-20261110-01'/('capacity-'+tag);OUT.mkdir()
RAM=RUN/'ram';RAM.mkdir();SOURCE=RUN/'source';SOURCE.mkdir();uid=1000;gid=1000
groups=[];bootstrap=None;load=None;mounted=False;status={'run':tag,'mode':mode,'started_at':datetime.now(timezone.utc).isoformat(),'synthetic_only':True,'true_identity_registration':False}
original_net=os.readlink('/proc/1/ns/net');actual_net=os.readlink('/proc/self/ns/net')
resource.setrlimit(resource.RLIMIT_CORE,(0,0));ctypes.CDLL(None).prctl(4,0,0,0,0)
assert original_net!=actual_net,'dedicated network namespace required'

def call(args,**kwargs):return subprocess.run(list(map(str,args)),check=True,**kwargs)
def group(name,cpu,memory):
    p=pathlib.Path('/sys/fs/cgroup')/('pr03b-'+tag+'-'+name)
    assert not p.exists();p.mkdir();groups.append(p)
    for field,value in [('cpu.max',f'{cpu} 100000'),('memory.max',str(memory)),('memory.swap.max','0'),('pids.max','512')]:
        (p/field).write_text(value)
    return p
def child(group_path):
    def prepare():
        (group_path/'cgroup.procs').write_text(str(os.getpid()))
        os.setgroups([]);os.setresgid(gid,gid,gid);os.setresuid(uid,uid,uid)
    return prepare
def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

try:
    call(['mount','--make-rprivate','/']);call(['ip','link','set','lo','up'])
    call(['mount','-t','tmpfs','-o','size=2G,mode=0700','pr03b-'+tag,RAM]);mounted=True;os.chown(RAM,uid,gid)
    for p in (RUN,OUT,SOURCE):os.chown(p,uid,gid)
    sut=group('sut',200000,4*1024**3);client=group('client',400000,2*1024**3)
    shutil.copytree(R/'web/backend',SOURCE/'backend',ignore=shutil.ignore_patterns('v','__pycache__','.pytest_cache'))
    shutil.copytree(R/'work/pr09a/dist',SOURCE/'dist')
    shutil.copy2(R/'docs/web-alpha/architecture/contracts/openapi.json',SOURCE/'openapi.json')
    (SOURCE/'harness').mkdir()
    for name in ('capacity_linux.py','capacity_service.py','capacity_client.py','browser_namespace.py','browser_live.mjs','business_client.py'):
        shutil.copy2(H/name,SOURCE/'harness'/name)
    if mode=='r05_probe':
        for name in ('enumeration_trial.py','enumeration_client.py'):shutil.copy2(H/name,SOURCE/'harness'/name)
        for name in ('enumeration_renewal.py','enumeration_plan.py','evidence_stats.py'):shutil.copy2(H.parent/'mechanisms'/name,SOURCE/'harness'/name)
    if mode=='r04_probe':shutil.copy2(H/'admission_client.py',SOURCE/'harness/admission_client.py')
    manifest={p.relative_to(SOURCE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCE.rglob('*') if p.is_file()}
    (OUT/'SOURCE-MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
    now=datetime.now(timezone.utc);root_key=ec.generate_private_key(ec.SECP256R1());leaf_key=ec.generate_private_key(ec.SECP256R1())
    issuer=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'PR03b isolated root '+tag)])
    root_cert=(x509.CertificateBuilder().subject_name(issuer).issuer_name(issuer).public_key(root_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=4)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
        .add_extension(x509.KeyUsage(False,False,False,False,False,True,True,False,False),critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()),critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),critical=False).sign(root_key,hashes.SHA256()))
    leaf=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'127.0.0.1')])).issuer_name(issuer).public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=4)).add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
        .add_extension(x509.KeyUsage(True,False,False,False,False,False,False,False,False),critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()),critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),critical=False)
        .add_extension(x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]),critical=False).sign(root_key,hashes.SHA256()))
    (RAM/'leaf.pem').write_bytes(leaf.public_bytes(serialization.Encoding.PEM));(OUT/'root-ca.pem').write_bytes(root_cert.public_bytes(serialization.Encoding.PEM))
    (RAM/'leaf-key.pem').write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    for p in RAM.iterdir():os.chown(p,uid,gid);p.chmod(0o600)
    ports=[port() for _ in range(1+api_count)];assert len(set(ports))==1+api_count and 5432 not in ports
    cfg=dict(work=str(W),ram=str(RAM),output=str(OUT),source=str(SOURCE),pg_port=ports[0],api_ports=ports[1:],admin='pr02a_admin_'+tag[:12],
        verifier='pr02a_auth_'+tag[:12],session='pr02a_session_'+tag[:12],runtime='pr02a_runtime_'+tag[:12],mode=mode,
        encryption_key=base64.b64encode(os.urandom(32)).decode(),commitment_key=base64.b64encode(os.urandom(32)).decode(),harness=str(SOURCE/'harness'))
    env={k:v for k,v in os.environ.items() if not k.upper().startswith('PG') and k not in ('PYTHONPATH','PYTEST_ADDOPTS','PYTEST_PLUGINS')}
    env.update(LD_LIBRARY_PATH=str(W/'pg17-linux-installed/lib')+':'+str(W/'linux-libs/usr/lib/x86_64-linux-gnu'),PYTHONDONTWRITEBYTECODE='1',TMPDIR=str(RAM),TZ='UTC')
    python=W/'linux-python312/bin/python3'
    assert sys.version_info[:2]==(3,12),'capacity acceptance requires supported Python 3.12'
    status['python_version']=sys.version
    # Root supervises only; the complete PG/API process tree starts as uid1000
    # inside the SUT cgroup before initdb, imports or allocation of hash memory.
    bootstrap=subprocess.Popen([python,'-I','-B',SOURCE/'harness/capacity_service.py','bootstrap'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=(OUT/'bootstrap.log').open('wb'),env=env,preexec_fn=child(sut),text=True)
    bootstrap.stdin.write(json.dumps(cfg)+'\n');bootstrap.stdin.flush()
    ready_line=bootstrap.stdout.readline();ready=json.loads(ready_line)
    assert ready.get('ready') is True,'isolated SUT bootstrap failed'
    cfg['users']=ready['users'];status['ready']=dict(api_pids=ready['api_pids'],pg_facts=ready['pg_facts'])
    status.update(network_namespace=actual_net,host_network_namespace=original_net,interfaces=json.loads(subprocess.check_output(['ip','-j','link'])),routes=json.loads(subprocess.check_output(['ip','-j','route'])),
        limits={p.name:{f:(p/f).read_text().strip() for f in ('cpu.max','memory.max','memory.swap.max','pids.max')} for p in groups},
        load_generator='independent business process; same client cgroup; original rates, budgets and sessions',storage='run-private volatile tmpfs; real PG WAL/fsync enabled; not persistent-storage crash acceptance',
        topology='WSL2 Linux namespace, two API processes, PG17.11; same-host isolated client cgroup; not a separately provisioned deployment VM')
    if mode in ('r05_probe','r04_probe'):
        status['load_generator']='same-target renewal functional proof' if mode=='r05_probe' else 'bounded 16/32-request bursts and recovery probes'
        status['topology']=f'WSL2 Linux namespace, {api_count} API processes, PG17.11; same-host isolated client cgroup; not a deployment VM'
    assert all(i['ifname']=='lo' for i in status['interfaces']) and not status['routes']
    (OUT/'RUN.json').write_text(json.dumps(status,indent=2)+'\n')
    print(json.dumps({'run':tag,'output':str(OUT),'ready':True,'mode':mode}),flush=True)
    cfg['sut_cgroup']=str(sut);cfg['client_cgroup']=str(client)
    client_script={'r05_probe':'enumeration_client.py','r04_probe':'admission_client.py'}.get(mode,'capacity_client.py')
    load=subprocess.Popen([python,'-I','-B',SOURCE/'harness'/client_script],stdin=subprocess.PIPE,stdout=(OUT/'client.log').open('wb'),stderr=subprocess.STDOUT,env=env,preexec_fn=child(client),text=True)
    load.stdin.write(json.dumps(cfg)+'\n');load.stdin.close()
    start=time.monotonic();last_print=0
    with (OUT/'RESOURCES.jsonl').open('w',buffering=1) as stream:
        while load.poll() is None:
            sample=dict(seconds=time.monotonic()-start)
            for p in groups:
                sample[p.name]={f:(p/f).read_text().strip() for f in ('memory.current','memory.peak','memory.events','cpu.stat','pids.current')}
            stream.write(json.dumps(sample)+'\n')
            if time.monotonic()-last_print>=30:
                print(json.dumps({'run':tag,'seconds':round(sample['seconds']),'phase':(OUT/'PHASE.json').read_text() if (OUT/'PHASE.json').exists() else 'starting'}),flush=True);last_print=time.monotonic()
            if time.monotonic()-start>2700:load.terminate();load.wait(15);raise TimeoutError('owned load watchdog')
            time.sleep(1)
    status['client_exit_code']=load.returncode
except BaseException as error:
    status.update(error_kind=type(error).__name__,passed=False);print(json.dumps({'run':tag,'error_kind':type(error).__name__}),flush=True)
finally:
    if mode in ('r05_probe','r04_probe') and load and load.poll() is None:
        load.terminate()
        try:load.wait(15)
        except subprocess.TimeoutExpired:load.kill();load.wait(5)
    if bootstrap:
        try:bootstrap.stdin.write('stop\n');bootstrap.stdin.flush();bootstrap.wait(45)
        except (BrokenPipeError,subprocess.TimeoutExpired):bootstrap.terminate();bootstrap.wait(15)
        status['bootstrap_exit_code']=bootstrap.returncode
    status['finished_at']=datetime.now(timezone.utc).isoformat()
    status['cgroups_empty']={p.name:not (p/'cgroup.procs').read_text().strip() for p in groups}
    for p in groups:
        if not (p/'cgroup.procs').read_text().strip():p.rmdir()
    if mounted:
        try:call(['umount',RAM]);status['ram_unmounted']=True
        except subprocess.CalledProcessError:status['ram_unmounted']=False
    (OUT/'FINAL.json').write_text(json.dumps(status,indent=2)+'\n')
raise SystemExit(0 if status.get('client_exit_code')==0 and status.get('bootstrap_exit_code')==0 and all(status['cgroups_empty'].values()) and status.get('ram_unmounted') else 1)
