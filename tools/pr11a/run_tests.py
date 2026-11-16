"""Create an owned VMware-guest PG cluster and run synthetic billing gates."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
from uuid import uuid4
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'web/backend'))

from classroom_web.test_support import SYNTHETIC_PASSWORD, validate_test_database_target
import psycopg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--suite', choices=('billing', 'backend', 'integration'), default='billing')
    parser.add_argument('--test-target', action='append', help='explicit pytest node ID; repeat for a targeted regression')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('this runner requires the authorized Windows VMware guest')
    tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8]
    work = ROOT / 'work/pr11a' / tag
    output = ROOT / 'outputs/pr11a' / tag
    for directory in (work / 'tmp', work / 'guard', output):
        directory.mkdir(parents=True, exist_ok=False)
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith('PG')
           and not any(part in key.upper() for part in ('TOKEN','SECRET','PASSWORD','KEY'))
           and key.upper() not in ('PYTEST_ADDOPTS','PYTEST_PLUGINS','PYTHONPATH','PYTHONPYCACHEPREFIX')}
    env.update(TEMP=str(work/'tmp'), TMP=str(work/'tmp'), PYTHONUTF8='1',
               PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONNOUSERSITE='1')
    guard_spec = importlib.util.spec_from_file_location('pr02b_runner', ROOT/'tools/pr02b/run_tests.py')
    guard_module = importlib.util.module_from_spec(guard_spec)
    guard_spec.loader.exec_module(guard_module)
    (work/'guard/sitecustomize.py').write_text(guard_module.NETWORK_GUARD, encoding='utf-8')
    env['PYTHONPATH'] = os.pathsep.join((str(work/'guard'),str(ROOT/'web/backend')))
    commands = []

    def run(name, arguments, timeout=120, cwd=ROOT):
        command = [str(argument) for argument in arguments]
        with (output/(name+'.log')).open('wb') as stream:
            result = subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=timeout)
        commands.append(dict(name=name, command=command, exit_code=result.returncode))
        return result.returncode

    probe = run('network-guard',[sys.executable,'-B','-c',
        "import socket;socket.create_connection(('198.51.100.1',443),timeout=1)"])
    assert probe != 0 and 'PR-02b tests deny' in (output/'network-guard.log').read_text(encoding='utf-8')
    login = 'pr02a_pr11a_' + uuid4().hex[:16]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    assert port != 5432
    data = work/'data'
    postgres_log = ROOT/'outputs/pr03a/third-round'/('pr11a-'+tag+'.log')
    postgres_log.parent.mkdir(parents=True,exist_ok=True)
    assert run('initdb',[args.pg_bin/'initdb.exe','-D',data,'-U',login,'-A','trust','--encoding=UTF8','--locale=C']) == 0
    started = False
    test_exit = stop_exit = None
    identity = {}
    try:
        code = run('start',[args.pg_bin/'pg_ctl.exe','-D',data,'-l',postgres_log,'-o',
            f'-h 127.0.0.1 -p {port} -c max_connections=100 -c log_statement=none -c log_min_error_statement=panic -c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0',
            '-w','-t','20','start'])
        assert code == 0
        started = True
        with psycopg.connect(host='127.0.0.1',port=port,user=login,password=SYNTHETIC_PASSWORD,
                             dbname='postgres',autocommit=True) as connection:
            actual = connection.execute("SELECT current_setting('data_directory'),current_setting('listen_addresses'),version()").fetchone()
            assert Path(actual[0]).resolve() == data.resolve() and actual[1] == '127.0.0.1'
            identity = dict(data_directory=actual[0],listen_addresses=actual[1],version=actual[2],port=port)
            connection.execute('CREATE DATABASE pr02a_pr11a_control')
        url = validate_test_database_target(f'postgresql+psycopg://{login}@127.0.0.1:{port}/pr02a_pr11a_control',environ=env)
        env['PR02A_TEST_ADMIN_URL'] = url.render_as_string(hide_password=False)
        env['PR03A_THIRD_ROUND_PG_LOG'] = str(postgres_log)
        env['PR03A_THIRD_ROUND_EVIDENCE'] = str(postgres_log.parent/('pr11a-'+tag+'-observations'))
        selected = ['tests/unit/test_billing_policy.py','tests/integration/test_billing.py'] if args.suite == 'billing' else [
            'tests','--ignore=tests/integration/test_auth_browser.py']
        if args.suite == 'integration':
            selected = ['tests/integration/test_billing_integration.py','tests/integration/test_task_worker.py',
                'tests/integration/test_authentication.py::test_real_login_cookie_context_logout_and_revoked_cookie',
                'tests/integration/test_authentication.py::test_bound_reauth_changes_password_and_revokes_existing_session',
                'tests/integration/test_authentication.py::test_real_totp_login_enforces_server_role_cookie_policy']
        if args.test_target:
            selected = args.test_target
        test_exit = run('pytest',[sys.executable,'-B','-m','pytest',*selected,'-q',
            '--basetemp='+str(work/'pytest'),'-o','cache_dir='+str(work/'cache'),
            '--junitxml='+str(output/'pytest.xml')],timeout=1200,cwd=ROOT/'web/backend')
    finally:
        if started:
            stop_exit = run('stop',[args.pg_bin/'pg_ctl.exe','-D',data,'-m','fast','-w','-t','20','stop'])
        if postgres_log.exists():
            shutil.copyfile(postgres_log,output/'postgres.log')
        counts = {}
        if (output/'pytest.xml').exists():
            suites = list(ET.parse(output/'pytest.xml').iter('testsuite'))
            counts = {key:sum(int(suite.get(key,'0')) for suite in suites) for key in ('tests','failures','errors','skipped')}
        passed = test_exit == 0 and stop_exit == 0 and counts.get('tests',0)>0 and not any(counts.get(k,0) for k in ('failures','errors','skipped'))
        report = dict(run=tag,suite=args.suite,commands=commands,cluster_identity=identity,
            test_exit=test_exit,stop_exit=stop_exit,junit=counts,passed=passed,synthetic_only=True,postgres_log=str(postgres_log),
            python=sys.version,source_manifest='SOURCE.json',real_provider_calls=0)
        (output/'results.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,indent=2))
    print((output/'pytest.log').read_text(encoding='utf-8',errors='replace')[-16000:])
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
