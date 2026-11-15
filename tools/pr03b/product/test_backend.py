"""A new owned synthetic PG cluster per invocation; no existing database use."""
import json,os,pathlib,socket,subprocess,sys,time,uuid

R=pathlib.Path(__file__).resolve().parents[3];W=R/'work/pr03b/product-completion-20261110-01'
O=R/'outputs/pr03b/product-completion-20261110-01'
sys.path.insert(0,str(R/'web/backend'))
from classroom_web.test_support import validate_test_database_target,SYNTHETIC_PASSWORD
import psycopg

assert len(sys.argv)==2 and sys.argv[1] in ('auth','full','unit','browser','parity','review')
mode=sys.argv[1];tag=uuid.uuid4().hex;folder=W/('pg-'+tag);folder.mkdir()
pgbin=R/'work/pr03b/toolchains/pg17.11-win64-53b5364a5a04e8ec/runtime/Library/bin'
assert (pgbin/'postgres.exe').is_file() and not (folder/'data').exists()
env={k:v for k,v in os.environ.items() if not k.upper().startswith('PG') and k.upper() not in ('PYTEST_ADDOPTS','PYTEST_PLUGINS','PYTHONPATH','PYTHONPYCACHEPREFIX')}
env.update(TEMP=str(W/'tmp'),TMP=str(W/'tmp'),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
log=[];commands=[]
pg_log=R/'outputs/pr03a/third-round'/('pr03b-product-'+tag+'.log');pg_log.parent.mkdir(parents=True,exist_ok=True)
def call(args,**kwargs):
    commands.append([str(a) for a in args])
    capture=folder/('command-'+str(len(commands))+'.log')
    # On Windows a daemon may retain inherited pipe handles after pg_ctl has
    # exited. Real files preserve output without waiting for daemon pipe EOF.
    with capture.open('wb') as stream:
        p=subprocess.run([str(a) for a in args],env=env,creationflags=flags,stdout=stream,stderr=subprocess.STDOUT,**kwargs)
    p.stdout=capture.read_text(encoding='utf8',errors='replace');p.stderr=''
    log.append(p.stdout)
    return p
admin='pr02a_admin_'+tag[:12]
with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
assert port!=5432
p=call([pgbin/'initdb.exe','-D',folder/'data','-U',admin,'-A','trust','--encoding=UTF8','--locale=C'],timeout=30)
if p.returncode:
    (O/('initdb-'+tag+'.log')).write_text(log[-1],encoding='utf8');raise SystemExit(p.returncode)
started=False;test=None;stop=None
try:
    p=call([pgbin/'pg_ctl.exe','-D',folder/'data','-l',pg_log,'-o',f'-h 127.0.0.1 -p {port} -c max_connections=100 -c log_statement=none -c log_min_error_statement=panic -c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0','-w','-t','20','start'],timeout=30)
    assert p.returncode==0,'owned cluster startup failed';started=True
    with psycopg.connect(host='127.0.0.1',port=port,user=admin,password=SYNTHETIC_PASSWORD,dbname='postgres',autocommit=True) as c:
        c.execute('CREATE DATABASE pr02a_control')
    url=validate_test_database_target(f'postgresql+psycopg://{admin}@127.0.0.1:{port}/pr02a_control',environ=env)
    env['PR02A_TEST_ADMIN_URL']=url.render_as_string(hide_password=False)
    env['PR03A_THIRD_ROUND_PG_LOG']=str(pg_log)
    env['PR03B_REAL_BROWSER']='1'  # this runner is the prepared environment for test_auth_browser.py
    selected=['tests/integration/test_authentication.py','tests/integration/test_manual_recovery.py','tests/integration/test_auth_mail.py'] if mode=='auth' else ['tests/unit'] if mode=='unit' else ['tests/integration/test_auth_browser.py'] if mode=='browser' else ['tests/integration/test_enumeration_parity.py','-rP'] if mode=='parity' else ['tests/integration/test_auth_review_fixes.py','tests/integration/test_auth_mail.py'] if mode=='review' else ['tests']
    test=call([sys.executable,'-B','-m','pytest',*selected,'-q','--basetemp='+str(folder/'pytest'),'-o','cache_dir='+str(W/'cache/pytest'),
               '--junitxml='+str(O/('backend-'+tag+'.xml'))],cwd=R/'web/backend',timeout=900)
finally:
    if started:
        stop=call([pgbin/'pg_ctl.exe','-D',folder/'data','-m','fast','-w','-t','20','stop'],timeout=30)
    (O/('backend-'+tag+'.log')).write_text('\n'.join(log),encoding='utf8')
    value={'mode':mode,'run':tag,'commands':commands,'port':port,'database':'pr02a_control','user':admin,'synthetic_only':True,
           'test_exit_code':test.returncode if test else None,'owned_cluster_stop_exit_code':stop.returncode if stop else None,
           'pressure_test':False,'guest_operations':0}
    (O/('backend-'+tag+'.json')).write_text(json.dumps(value,indent=2)+'\n',encoding='utf8')
    print(json.dumps(value))
if test:print(test.stdout[-12000:]+test.stderr[-2000:])
raise SystemExit(test.returncode if test and stop and stop.returncode==0 else 1)
