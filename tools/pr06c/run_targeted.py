"""Run focused PR-06c tests with a separate loopback PostgreSQL cluster."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
import xml.etree.ElementTree as ET

import psycopg


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/pr07b'))
from run_combined import GUARDED_PYTEST, GUARD_VERIFIED, ephemeral_port
sys.path.pop(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deps', type=Path, required=True)
    parser.add_argument('--guard', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'win32' or os.environ.get('PR06C_VMWARE_GUEST') != '1':
        parser.error('the VMware guest launcher is required')
    tag = uuid4().hex
    work = ROOT / 'work/pr06c' / tag
    (work / 'objects').mkdir(parents=True)
    args.output.mkdir(parents=True, exist_ok=False)
    pg = Path(os.environ['PR04B_PG_BIN'])
    port = ephemeral_port()
    if port == 5432:
        raise RuntimeError('refusing the default PostgreSQL port')
    admin = 'pr02a_admin_' + tag[:12]
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(('PG', 'AWS', 'OPENAI_', 'ANTHROPIC_', 'DASHSCOPE_'))
           and not key.upper().endswith('_PROXY')
           and key.upper() not in ('PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'PYTHONPYCACHEPREFIX')}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
        TMP=str(work), TEMP=str(work), PR04B_OBJECT_ROOT=str(work / 'objects'),
        PYTHONPATH=os.pathsep.join((str(args.guard.parent), str(args.deps))),
        AWS_EC2_METADATA_DISABLED='true', PR02A_TEST_ADMIN_URL=f'postgresql+psycopg://{admin}@127.0.0.1:{port}/pr02a_control')
    evidence = {'commands': [], 'test_exit': None, 'pg_stop_exit': None, 'synthetic_only': True}

    def call(name, command, *, cwd=ROOT, timeout=60):
        command = [str(item) for item in command]
        with (args.output / (name + '.log')).open('wb') as log:
            code = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW).returncode
        evidence['commands'].append(dict(command=command, exit_code=code))
        return code

    cluster = work / 'pg'
    try:
        if call('initdb', [pg/'initdb.exe','-D',cluster,'-U',admin,'-A','trust','--encoding=UTF8','--locale=C']):
            return 1
        if call('pg-start', [pg/'pg_ctl.exe','-D',cluster,'-l',args.output/'postgres.log','-o',
                f'-h 127.0.0.1 -p {port} -c log_statement=none -c log_min_error_statement=panic',
                '-w','-t','30','start']):
            return 1
        with psycopg.connect(host='127.0.0.1', port=port, user=admin, dbname='postgres', autocommit=True) as c:
            c.execute('CREATE DATABASE pr02a_control')
        junit = args.output / 'pytest.xml'
        tests = ['tests/integration/test_filetrans.py', 'tests/integration/test_trusted_device_guard.py']
        evidence['test_exit'] = call('pytest', [sys.executable, '-B','-c', GUARDED_PYTEST,args.guard,
            *tests,'-x','-q','-p','no:cacheprovider','--basetemp='+str(work/'pytest'),'--junitxml='+str(junit)],
            cwd=ROOT/'web/backend', timeout=900)
        cases = list(ET.parse(junit).iter('testcase'))
        evidence['cases'] = len(cases)
        evidence['bad'] = sum(case.find(label) is not None for case in cases for label in ('failure','error','skipped'))
        evidence['guard_verified'] = GUARD_VERIFIED in (args.output/'pytest.log').read_text(encoding='utf8')
    finally:
        if (cluster/'postmaster.pid').is_file():
            evidence['pg_stop_exit'] = call('pg-stop', [pg/'pg_ctl.exe','-D',cluster,'-m','fast','-w','-t','30','stop'])
        (args.output/'result.json').write_text(json.dumps(evidence, indent=2)+'\n', encoding='utf8')
    return 0 if evidence['test_exit'] == evidence['pg_stop_exit'] == evidence['bad'] == 0 and evidence['guard_verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
