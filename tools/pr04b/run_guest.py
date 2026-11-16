"""Run PR-04b inside the Windows guest on owned PostgreSQL and SeaweedFS instances."""
from pathlib import Path
import ast
import json
import os
import socket
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'work/pr04b'
OUTPUT = ROOT / 'outputs/pr04b'
OUTPUT.mkdir(parents=True, exist_ok=True)
mode = sys.argv[1] if len(sys.argv) > 1 else 'targeted'
tag = uuid.uuid4().hex
run = WORK / tag
run.mkdir(parents=True)
env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('PG', 'AWS'))
       and k.upper() not in ('PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'PYTHONPATH', 'PYTHONPYCACHEPREFIX')}
env.update(TEMP=str(run), TMP=str(run), PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
           PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PR04B_DELETION_INTEGRATION='1',
           PR04B_OBJECT_ROOT=str(run / 'objects'), PR04B_PG_BIN=os.environ['PR04B_PG_BIN'])
pgbin = Path(os.environ['PR04B_PG_BIN'])
flags = subprocess.CREATE_NO_WINDOW
commands = []
REQUIRED = ['test_deletion_lifecycle', 'test_deletion_restore']


def call(args, **kwargs):
    commands.append([str(x) for x in args])
    with (run / f'command-{len(commands)}.log').open('wb') as out:
        return subprocess.run([str(x) for x in args], env=env, stdout=out, stderr=subprocess.STDOUT,
                              creationflags=flags, **kwargs)


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


if mode == 'probe':
    done = call([sys.executable, '-B', ROOT / 'tools/pr04b/probe_store.py', run], timeout=300)
    print((run / 'command-1.log').read_text(encoding='utf8', errors='replace')[-12000:])
    (OUTPUT / (tag + '-probe.txt')).write_bytes((run / 'command-1.log').read_bytes())
    sys.exit(done.returncode)

sys.path.insert(0, str(ROOT / 'web/backend/tests/integration'))
pg_port = port()
assert pg_port != 5432
admin = 'pr02a_admin_' + tag[:12]
pg_started = False
media = test = stop = suite = None
left = []
pg_log = ROOT / 'outputs/pr03a/third-round' / ('pr04b-' + tag + '.log')
pg_log.parent.mkdir(parents=True, exist_ok=True)
(run / 'objects').mkdir()
try:
    assert call([pgbin / 'initdb.exe', '-D', run / 'pg', '-U', admin, '-A', 'trust', '--encoding=UTF8', '--locale=C'], timeout=60).returncode == 0
    start = call([pgbin / 'pg_ctl.exe', '-D', run / 'pg', '-l', pg_log, '-o',
        f'-h 127.0.0.1 -p {pg_port} -c max_connections=100 -c log_statement=none -c log_min_error_statement=panic -c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0',
        '-w', '-t', '30', 'start'], timeout=60)
    pg_started = (run / 'pg/postmaster.pid').exists()
    assert start.returncode == 0 and pg_started
    import psycopg
    with psycopg.connect(host='127.0.0.1', port=pg_port, user=admin, dbname='postgres', autocommit=True) as connection:
        connection.execute('CREATE DATABASE pr02a_control')
    env['PR02A_TEST_ADMIN_URL'] = f'postgresql+psycopg://{admin}@127.0.0.1:{pg_port}/pr02a_control'
    env['PR03A_THIRD_ROUND_PG_LOG'] = str(pg_log)
    selected = ['tests/unit/test_deletion_values.py'] + [f'tests/integration/{name}.py' for name in REQUIRED]
    if mode == 'full':
        # The complete backend suite, including PR-04a's object tests on their own store.
        from object_service import ObjectService
        media = ObjectService(run / 'objects', 'pr04a-media', access='pr04a-synthetic', secret='pr04a-synthetic-secret',
                              volumes=512).start()  # one bucket per PR-04a test, as in the merged-main runner
        env.update(PR04A_OBJECT_INTEGRATION='1', PR04A_S3_ENDPOINT=media.endpoint)
        selected = ['tests']
    test = call([sys.executable, '-B', '-m', 'pytest', *selected, '-q', '-p', 'no:cacheprovider',
                 '--basetemp=' + str(run / 'pytest'), '--junitxml=' + str(OUTPUT / (tag + '.xml'))],
                cwd=ROOT / 'web/backend', timeout=1500)
    cases = list(ET.parse(OUTPUT / (tag + '.xml')).iter('testcase'))
    required, observed = set(), set()
    for module in REQUIRED:
        source = (ROOT / 'web/backend/tests/integration' / f'{module}.py').read_text(encoding='utf8')
        required |= {(module, node.name) for node in ast.parse(source).body
                     if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')}
        observed |= {(module, case.get('name').split('[', 1)[0]) for case in cases
                     if case.get('classname', '').endswith(module)}
    bad = sum(case.find(label) is not None for case in cases for label in ('failure', 'error', 'skipped'))
    suite = dict(junit_cases=len(cases), bad=bad, required_deletion_cases=len(required),
                 observed_deletion_cases=len(observed & required), all_deletion_cases_executed=required <= observed)
finally:
    if media is not None:
        media.stop()
    # Object services owned by the tests record their process ids; none may outlive the run.
    for pid_file in (run / 'objects').rglob('pid'):
        pid = pid_file.read_text(encoding='ascii').strip()
        probe = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
                               capture_output=True, text=True, creationflags=flags)
        if 'weed' in probe.stdout.lower():
            left.append(pid)
            subprocess.run(['taskkill', '/PID', pid, '/T', '/F'], capture_output=True, creationflags=flags)
    if pg_started:
        stop = call([pgbin / 'pg_ctl.exe', '-D', run / 'pg', '-m', 'fast', '-w', '-t', '30', 'stop'], timeout=60)
    result = dict(run=tag, mode=mode, commands=commands, test_exit=test.returncode if test else None,
                  pg_stop_exit=stop.returncode if stop else None, object_processes_left=left,
                  object_service='SeaweedFS 4.53', postgres='17.11', synthetic_only=True, suite=suite)
    (OUTPUT / (tag + '.json')).write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    print(json.dumps(result))
    for path in sorted(run.glob('command-*.log')):
        print(path.name, path.read_text(encoding='utf8', errors='replace')[-20000:])
    # Keep every object-service log with the evidence; show the PR-04a store's tail on failure.
    for path in sorted((run / 'objects').rglob('seaweed-*.log')):
        name = '-'.join(path.relative_to(run / 'objects').parts)
        (OUTPUT / f'{tag}-{name}').write_bytes(path.read_bytes()[-200000:])
        if (test is None or test.returncode != 0) and 'pr04a-media' in name:
            print(name, path.read_text(encoding='utf8', errors='replace')[-6000:])
sys.exit(0 if test and test.returncode == 0 and stop and stop.returncode == 0 and suite
         and suite['bad'] == 0 and suite['all_deletion_cases_executed'] and not left else 1)
