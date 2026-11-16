"""Run PR-04a on owned Windows guest PG and SeaweedFS instances."""
from pathlib import Path
import ast
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
BASE = Path('J:/class-assistant/worktrees/web-pr03b-sessions')
WORK = ROOT / 'work/pr04a'
OUTPUT = ROOT / 'outputs/pr04a'
OUTPUT.mkdir(parents=True, exist_ok=True)
tag = uuid.uuid4().hex
run = WORK / tag
run.mkdir(parents=True)
env = {k:v for k,v in os.environ.items() if not k.upper().startswith(('PG','AWS'))
       and k.upper() not in ('PYTEST_ADDOPTS','PYTEST_PLUGINS','PYTHONPATH','PYTHONPYCACHEPREFIX')}
env.update(TEMP=str(run),TMP=str(run),PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',
           PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PR04A_OBJECT_INTEGRATION='1')
if len(sys.argv)>1 and sys.argv[1]=='full':
    env['PR03B_REAL_BROWSER']='1'
pgbin = BASE / 'work/pr03b/toolchains/pg17.11-win64-53b5364a5a04e8ec/runtime/Library/bin'
weed = WORK / 'seaweedfs/weed.exe'
flags = subprocess.CREATE_NO_WINDOW
commands = []


def call(args, **kwargs):
    commands.append([str(x) for x in args])
    with (run / f'command-{len(commands)}.log').open('wb') as out:
        return subprocess.run([str(x) for x in args],env=env,stdout=out,stderr=subprocess.STDOUT,
                              creationflags=flags,**kwargs)


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def object_ports():
    # Reserve both HTTP and gRPC ports together during selection.
    while True:
        first = 20000 + secrets.randbelow(20000)
        selected = [first + offset for offset in range(4)]
        sockets = []
        try:
            for value in selected + [value + 10000 for value in selected]:
                sock = socket.socket()
                sockets.append(sock)
                sock.bind(('127.0.0.1', value))
            return selected
        except OSError:
            pass
        finally:
            for sock in sockets:
                sock.close()


pg_port = port()
assert pg_port != 5432
admin = 'pr02a_admin_' + tag[:12]
pg_started = False
server = None
test = None
stop = None
suite = None
pg_log = ROOT / 'outputs/pr03a/third-round' / ('pr04a-'+tag+'.log')
pg_log.parent.mkdir(parents=True,exist_ok=True)
try:
    assert call([pgbin/'initdb.exe','-D',run/'pg','-U',admin,'-A','trust','--encoding=UTF8','--locale=C'],timeout=30).returncode == 0
    start = call([pgbin/'pg_ctl.exe','-D',run/'pg','-l',pg_log,'-o',
        f'-h 127.0.0.1 -p {pg_port} -c max_connections=100 -c log_statement=none -c log_min_error_statement=panic -c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0',
        '-w','-t','20','start'],timeout=30)
    pg_started = (run/'pg/postmaster.pid').exists()
    assert start.returncode == 0 and pg_started
    import psycopg
    with psycopg.connect(host='127.0.0.1',port=pg_port,user=admin,dbname='postgres',autocommit=True) as connection:
        connection.execute('CREATE DATABASE pr02a_control')
    env['PR02A_TEST_ADMIN_URL'] = f'postgresql+psycopg://{admin}@127.0.0.1:{pg_port}/pr02a_control'
    env['PR03A_THIRD_ROUND_PG_LOG'] = str(pg_log)
    ports = object_ports()
    master,volume,filer,s3 = ports
    (run/'objects').mkdir()
    config = run/'s3.json'
    config.write_text(json.dumps({'identities':[{'name':'synthetic-only','credentials':[
        {'accessKey':'pr04a-synthetic','secretKey':'pr04a-synthetic-secret'}],
        'actions':['Admin','Read','Write','List','Tagging']}]}),encoding='utf8')
    args = [str(weed),'server','-ip=127.0.0.1','-ip.bind=127.0.0.1',
        '-dir='+str(run/'objects'),'-master.port='+str(master),'-volume.port='+str(volume),
        '-filer','-filer.port='+str(filer),'-s3','-s3.port='+str(s3),'-s3.config='+str(config),
        '-volume.max=128','-master.volumeSizeLimitMB=64','-master.telemetry=false',
        '-s3.port.iceberg=0','-s3.port.lance=0','-s3.iam=false']
    commands.append(args)
    with (run/'seaweed.log').open('wb') as server_log:
        server = subprocess.Popen(args,cwd=run,env=env,stdout=server_log,stderr=subprocess.STDOUT,creationflags=flags)
        import httpx
        env['PR04A_S3_ENDPOINT'] = f'http://127.0.0.1:{s3}'
        for _ in range(90):
            if server.poll() is not None:
                raise RuntimeError('owned object service stopped before readiness')
            try:
                if httpx.get(env['PR04A_S3_ENDPOINT'],timeout=1).status_code in (200,401,403):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError('owned object service readiness timed out')
        import boto3
        from botocore.config import Config
        from botocore.exceptions import ClientError, BotoCoreError
        probe=boto3.client('s3',endpoint_url=env['PR04A_S3_ENDPOINT'],region_name='us-east-1',
            aws_access_key_id='pr04a-synthetic',aws_secret_access_key='pr04a-synthetic-secret',
            config=Config(connect_timeout=1,read_timeout=3,retries={'total_max_attempts':1},
                          request_checksum_calculation='when_required'))
        probe.create_bucket(Bucket='pr04a-readiness')
        for _ in range(30):
            try:
                probe.put_object(Bucket='pr04a-readiness',Key='ready',Body=b'ready')
                break
            except (ClientError, BotoCoreError):
                time.sleep(1)
        else:
            raise RuntimeError('object service write readiness timed out')
        selected = ['tests/unit/test_media_ranges.py','tests/integration/test_private_media.py']
        if len(sys.argv)>1 and sys.argv[1]=='full':
            selected = ['tests']
        if len(sys.argv)>1 and sys.argv[1]=='r1':
            selected = ['tests/unit/test_media_ranges.py','tests/integration/test_media_stream_lifecycle.py',
                'tests/integration/test_private_media.py::test_normal_http_upload_download_ranges_and_private_bucket',
                'tests/integration/test_private_media.py::test_stream_checks_revocation_between_blocks',
                'tests/integration/test_private_media.py::test_real_browser_media_over_https']
        test = call([sys.executable,'-B','-m','pytest',*selected,'-q','--basetemp='+str(run/'pytest'),
            '-o','cache_dir='+str(run/'cache'),'--junitxml='+str(OUTPUT/(tag+'.xml'))],cwd=ROOT/'web/backend',timeout=900)
        cases = list(ET.parse(OUTPUT/(tag+'.xml')).iter('testcase'))
        required_module = 'test_media_stream_lifecycle' if len(sys.argv)>1 and sys.argv[1]=='r1' else 'test_private_media'
        required = {node.name for node in ast.parse((ROOT/'web/backend/tests/integration'/f'{required_module}.py').read_text(encoding='utf8')).body
                    if isinstance(node,ast.FunctionDef) and node.name.startswith('test_')}
        observed = {case.get('name').split('[',1)[0] for case in cases if case.get('classname','').endswith(required_module)}
        bad = sum(case.find(label) is not None for case in cases for label in ('failure','error','skipped'))
        suite = dict(junit_cases=len(cases),bad=bad,required_media_cases=len(required),observed_media_cases=len(observed),
                     all_media_cases_executed=required.issubset(observed))
finally:
    if server is not None and server.poll() is None:
        server.terminate()
        server.wait(timeout=30)
    if pg_started:
        stop = call([pgbin/'pg_ctl.exe','-D',run/'pg','-m','fast','-w','-t','20','stop'],timeout=30)
    result = dict(run=tag,commands=commands,test_exit=test.returncode if test else None,
        pg_stop_exit=stop.returncode if stop else None,object_process_stopped=server is not None and server.poll() is not None,
        object_service='SeaweedFS 4.53',postgres='17.11',synthetic_only=True,work=str(run),pg_log=str(pg_log))
    result['suite'] = suite
    (OUTPUT/(tag+'.json')).write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    print(json.dumps(result))
    for path in sorted(run.glob('command-*.log')):
        print(path.name, path.read_text(encoding='utf8',errors='replace')[-18000:])
    if test is None and (run/'seaweed.log').exists():
        print((run/'seaweed.log').read_text(encoding='utf8',errors='replace')[-12000:])
sys.exit(0 if test and test.returncode==0 and stop and stop.returncode==0 and suite
         and suite['bad']==0 and suite['all_media_cases_executed'] else 1)
