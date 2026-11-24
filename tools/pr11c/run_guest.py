"""Verify PR-11c on an owned Windows TEST guest PostgreSQL cluster."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from uuid import uuid4
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'web/backend'
FRONTEND = ROOT / 'web/frontend'
WORK = ROOT / 'work/pr11c'
OUTPUT = ROOT / 'outputs/pr11c'
CREATE_SUSPENDED = 0x00000004


def junit(path):
    if not path.is_file():
        return None
    root = ET.parse(path)
    cases = list(root.iter('testcase'))
    return {
        'tests': len(cases),
        **{name: sum(case.find(tag) is not None for case in cases)
           for name, tag in [('failures', 'failure'), ('errors', 'error'), ('skips', 'skipped')]},
        'pr11c_tests': sum('pr11c' in case.get('classname', '') or 'pr11c' in case.get('file', '') for case in cases),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=('targeted', 'full'), required=True)
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--weed', type=Path, required=True)
    parser.add_argument('--node', type=Path, required=True)
    parser.add_argument('--dependency-root', type=Path, required=True)
    parser.add_argument('--deps', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt' or os.environ.get('PR11C_VMWARE_GUEST') != '1':
        parser.error('use the Windows TEST guest entrypoint')
    for name in tuple(os.environ):
        if name.upper().startswith('PG'):
            del os.environ[name]
    os.environ['PR04B_WEED'] = str(args.weed)
    sys.path[:0] = [str(ROOT), str(BACKEND), str(BACKEND / 'tests/integration')]
    from classroom_web.test_support import synthetic_connect_args, validate_test_database_target
    from object_service import ObjectService
    from tools.overnight.executor import _WindowsJob
    import psycopg

    modules = FRONTEND / 'node_modules'
    standalone = args.dependency_root / 'work/pr03b/vmware-broker/payload'
    data = WORK / 'pg'
    # The PR-03a third-round suites require their PostgreSQL log under outputs/pr03a/third-round.
    pg_log = ROOT / 'outputs/pr03a/third-round' / ('pr11c-' + uuid4().hex[:12] + '.log')
    pg_log.parent.mkdir(parents=True, exist_ok=True)
    tmp = WORK / 'tmp'
    tmp.mkdir(parents=True, exist_ok=False)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('PG', 'AWS', 'CLASSROOM_WEB_'))
           and not re.match(r'PR[0-9]', k.upper())
           and not any(part in k.upper() for part in ('TOKEN', 'SECRET', 'PASSWORD', 'KEY'))
           and k.upper() not in {'PYTHONPATH', 'PYTHONPYCACHEPREFIX', 'PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'NODE_OPTIONS'}}
    env.update(PATH=os.pathsep.join((str(args.node.parent), env.get('PATH', ''))),
               TEMP=str(tmp), TMP=str(tmp), TMPDIR=str(tmp), PYTHONUTF8='1',
               PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
               PYTHONPATH=os.pathsep.join((str(BACKEND), str(args.deps))), PLAYWRIGHT_BROWSERS_PATH=str(standalone / 'ms-playwright'),
               PR04B_WEED=str(args.weed), PR04B_PG_BIN=str(args.pg_bin),
               PR04B_OBJECT_ROOT=str(WORK / 'objects'), PR04B_DELETION_INTEGRATION='1',
               PR05B_CAPTURE_INTEGRATION='1', PR05A_DEPENDENCY_ROOT=str(args.dependency_root),
               PR05A_REVIEW_OUTPUT=str(OUTPUT / 'pr05a'), PR05A_VMWARE_GUEST='1', CI='1',
               AWS_EC2_METADATA_DISABLED='true',
               PR11C_OPERATIONS_INTEGRATION='1', PR11C_POSTGRES_LOG=str(pg_log),
               PR03A_THIRD_ROUND_PG_LOG=str(pg_log))
    commands = []
    results = {}
    identity = None
    failure = None
    media = None

    def run_in_job(command, stream, cwd, timeout):
        job = _WindowsJob()
        process = None
        try:
            # Hold the initial thread until the job owns the process, so no
            # children can start outside the job. pg_ctl start stays separate.
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stream,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW | CREATE_SUSPENDED)
            job.attach_and_resume(process)
            return process.wait(timeout=timeout)
        finally:
            try:
                job.close()
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)

    def run(name, *argv, cwd=ROOT, timeout=300, job_owned=False):
        command = [str(arg) for arg in argv]
        with (OUTPUT / (name + '.log')).open('wb') as stream:
            try:
                if job_owned:
                    code = run_in_job(command, stream, cwd, timeout)
                else:
                    code = subprocess.run(command, cwd=cwd, env=env, stdout=stream,
                                          stderr=subprocess.STDOUT, timeout=timeout,
                                          creationflags=subprocess.CREATE_NO_WINDOW).returncode
            except subprocess.TimeoutExpired:
                code = 124
        commands.append({'name': name, 'argv': command, 'cwd': str(cwd), 'exit_code': code})
        results[name] = code
        return code

    try:
        run('python-version', sys.executable, '--version')
        run('node-version', args.node, '--version')
        run('dependencies', sys.executable, '-I', '-B', '-m', 'pip', 'check')
        frontend_args = sorted(str(path.relative_to(FRONTEND)) for path in (FRONTEND / 'tests').glob('pr11c-*.test.ts'))
        run('frontend-vitest', args.node, modules / 'vitest/vitest.mjs', 'run', '--config', 'tests/vitest.config.ts',
            '--reporter=default', '--reporter=junit', '--outputFile.junit=' + str(OUTPUT / 'vitest.xml'),
            *(frontend_args if args.suite == 'targeted' else []), cwd=FRONTEND, job_owned=True)

        login = 'pr02a_pr11c_' + uuid4().hex[:16]
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        if port == 5432:
            raise RuntimeError('Unexpected default PostgreSQL port')
        database = 'pr02a_pr11c_' + uuid4().hex[:16]
        url = validate_test_database_target(f'postgresql+psycopg://{login}@127.0.0.1:{port}/{database}', environ=env)
        if run('pg-init', args.pg_bin / 'initdb.exe', '-D', data, '-U', login, '-A', 'trust',
               '--encoding=UTF8', '--locale=C'):
            raise RuntimeError('Owned PostgreSQL initialization failed')
        options = (f'-h 127.0.0.1 -p {port} -c max_connections=100 -c log_statement=none '
                   '-c log_min_error_statement=panic -c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0')
        if run('pg-start', args.pg_bin / 'pg_ctl.exe', '-D', data, '-l', pg_log, '-o', options, '-w', '-t', '30', 'start'):
            raise RuntimeError('Owned PostgreSQL start failed')
        with psycopg.connect(host=url.host, port=url.port, user=url.username, dbname='postgres',
                             password=url.password, autocommit=True, **synthetic_connect_args()) as conn:
            directory, address, pg_version = conn.execute(
                "SELECT current_setting('data_directory'),current_setting('listen_addresses'),version()").fetchone()
            if Path(directory).resolve() != data.resolve() or address != '127.0.0.1':
                raise RuntimeError('PostgreSQL target is not the owned cluster')
            identity = {'directory': directory, 'address': address, 'port': port, 'version': pg_version}
            conn.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(database)))
        env['PR02A_TEST_ADMIN_URL'] = url.render_as_string(hide_password=False)

        selected = ['tests/integration/test_pr11c_operations.py', 'tests/integration/test_pr11c_migrations.py']
        if args.suite == 'full':
            run('frontend-build', args.node, modules / 'vite/bin/vite.js', 'build', '--mode', 'production',
                cwd=FRONTEND, job_owned=True)
            # Keep the handle before start(): a failed readiness probe can leave the process
            # running, and the finally block below must still be able to stop it.
            media = ObjectService(WORK / 'objects', 'pr04a-media', access='pr04a-synthetic',
                                  secret='pr04a-synthetic-secret', volumes=512)
            media.start()
            env.update(PR03B_REAL_BROWSER='1', PR08B_SYNC_INTEGRATION='1',
                       PR04A_OBJECT_INTEGRATION='1', PR04A_S3_ENDPOINT=media.endpoint)
            selected = ['tests']
        run('backend-pytest', sys.executable, '-B', '-m', 'pytest', *selected, '-q', '-ra', '-p', 'no:cacheprovider',
            '--basetemp=' + str(tmp / 'pytest'), '--junitxml=' + str(OUTPUT / 'pytest.xml'),
            cwd=BACKEND, timeout=2400, job_owned=True)

        markers = ('PR11C_EXCERPT_PRIVATE_SENTINEL', 'PR11C_DESCRIPTION_PRIVATE_SENTINEL')
        results['postgres-log-privacy'] = int(any(marker in pg_log.read_text(encoding='utf8', errors='replace')
                                                  for marker in markers))
    except Exception as error:
        failure = {'kind': type(error).__name__, 'message': str(error)}
    finally:
        try:
            if media is not None:
                media.stop()
        finally:
            if (data / 'postmaster.pid').is_file():
                run('pg-stop', args.pg_bin / 'pg_ctl.exe', '-D', data, '-m', 'fast', '-w', '-t', '30', 'stop')
            if pg_log.is_file():
                (OUTPUT / 'postgres.log').write_bytes(pg_log.read_bytes())

    counts = {label: junit(OUTPUT / (label + '.xml')) for label in ('pytest', 'vitest')}
    passed = not failure and all(code == 0 for code in results.values()) and all(
        count and count['pr11c_tests'] and not any(count[name] for name in ('failures', 'errors', 'skips'))
        for count in counts.values())
    report = {'suite': args.suite, 'python': sys.version, 'cluster': identity, 'commands': commands,
              'results': results, 'junit': counts, 'setup_error': failure, 'passed': bool(passed)}
    (OUTPUT / 'results.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
    print(json.dumps(report, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
