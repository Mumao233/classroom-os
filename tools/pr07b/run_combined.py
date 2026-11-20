"""Run the frozen PR-07b backend with owned VMware guest PostgreSQL and S3.

Adapted from integration-ci-20261116-01/run_combined.py. Invoke this script in
the Windows VMware guest using its prepared PR-04a Python environment. ROOT is
always supplied explicitly. The default suite excludes the real auth browser.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import traceback
import uuid
import xml.etree.ElementTree as ET


BASE = Path('J:/class-assistant/worktrees/web-pr03b-sessions')
MEDIA = Path('J:/class-assistant/worktrees/web-pr04a-storage')
GUARD_VERIFIED = 'Combined pytest loopback guard verified'
GUARDED_PYTEST = f"""
from pathlib import Path
import sys

import sitecustomize

expected = Path(sys.argv.pop(1)).resolve(strict=True)
if Path(sitecustomize.__file__).resolve() != expected:
    raise SystemExit('Combined pytest loaded a different loopback guard')
try:
    sys.audit('socket.connect', None, ('198.51.100.1', 443))
except PermissionError as error:
    if str(error) != 'PR-02b tests deny non-loopback network connections':
        raise
else:
    raise SystemExit('Combined pytest did not deny a non-loopback audit event')
sys.audit('socket.connect', None, ('127.0.0.1', 443))
print({GUARD_VERIFIED!r}, flush=True)

import pytest

raise SystemExit(pytest.main(sys.argv[1:]))
"""


def ephemeral_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def object_ports():
    # SeaweedFS also binds corresponding +10000 gRPC ports. Check both sets.
    for _ in range(100):
        first = 20000 + secrets.randbelow(20000)
        selected = [first + offset for offset in range(4)]
        listeners = []
        try:
            for value in selected + [value + 10000 for value in selected]:
                listener = socket.socket()
                listeners.append(listener)
                listener.bind(('127.0.0.1', value))
            return selected
        except OSError:
            continue
        finally:
            for listener in listeners:
                listener.close()
    raise RuntimeError('no available owned object-service ports')


def suite_evidence(root, junit, auth_browser):
    cases = list(ET.parse(junit).iter('testcase'))
    modules = [root / 'web/backend/tests/integration/test_private_media.py',
               root / 'web/backend/tests/integration/test_media_stream_lifecycle.py']
    modules.extend(sorted((root / 'web/backend/tests').rglob('test_pr07a_*.py')))
    modules.extend(sorted((root / 'web/backend/tests').rglob('test_pr07b_*.py')))
    if auth_browser:
        modules.append(root / 'web/backend/tests/integration/test_auth_browser.py')
    coverage = {}
    for module in modules:
        expected = {node.name for node in ast.parse(module.read_text(encoding='utf8')).body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith('test_')}
        observed = {case.get('name', '').split('[', 1)[0] for case in cases
                    if case.get('classname', '').endswith(module.stem)}
        coverage[module.stem] = dict(required=len(expected), observed=len(observed),
                                     missing=sorted(expected - observed))
    return dict(junit_cases=len(cases),
                failures=sum(case.find('failure') is not None for case in cases),
                errors=sum(case.find('error') is not None for case in cases),
                skipped=sum(case.find('skipped') is not None for case in cases),
                required_modules=coverage,
                all_required_executed=all(not item['missing'] for item in coverage.values()))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default=os.environ.get('PR07B_ROOT'))
    parser.add_argument('--pg-bin', default=str(BASE / 'work/pr03b/toolchains/pg17.11-win64-53b5364a5a04e8ec/runtime/Library/bin'))
    parser.add_argument('--weed', default=str(MEDIA / 'work/pr04a/seaweedfs/weed.exe'))
    parser.add_argument('--payload', default=str(BASE / 'work/pr03b/vmware-broker/payload'))
    parser.add_argument('--auth-browser', action='store_true',
                        help='build this snapshot frontend and include real HTTPS/WSS auth browser validation')
    parser.add_argument('--deps', default=os.environ.get('PR07B_VALIDATION_DEPS'),
                        help='explicit pip --target directory used only by this validation process')
    parser.add_argument('--pytest-loopback-guard', type=Path,
                        help='run pytest only after verifying this sitecustomize.py in the same process')
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    if not args.root:
        parser.error('--root or PR07B_ROOT must name the frozen source snapshot')
    root = Path(args.root)
    if not root.is_absolute() or not (root / 'web/backend/tests').is_dir():
        parser.error('ROOT must be an absolute prepared Classroom OS source snapshot')
    if os.name != 'nt':
        parser.error('this evidence runner requires the prepared Windows VMware guest')
    if not 60 <= args.timeout <= 7200:
        parser.error('--timeout must be between 60 and 7200 seconds')
    guard = args.pytest_loopback_guard
    if guard is not None and (not guard.is_absolute() or guard.name != 'sitecustomize.py'
                              or not guard.is_file()):
        parser.error('--pytest-loopback-guard must name an existing absolute sitecustomize.py')

    tag = uuid.uuid4().hex
    run = root / 'work/pr07b/combined' / tag
    output = root / 'outputs/pr07b/combined' / tag
    run.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=False)
    pg_bin, weed, payload = Path(args.pg_bin), Path(args.weed), Path(args.payload)
    pg_log = root / 'outputs/pr03a/third-round' / ('pr07b-' + tag + '.log')
    pg_log.parent.mkdir(parents=True, exist_ok=True)
    junit = output / 'pytest.xml'
    commands = []
    flags = subprocess.CREATE_NO_WINDOW
    excluded = ('PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'PYTHONPATH', 'PYTHONPYCACHEPREFIX',
                'PR03B_REAL_BROWSER', 'PR02A_TEST_ADMIN_URL', 'PR04A_S3_ENDPOINT')
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(('PG', 'AWS', 'OPENAI_', 'ANTHROPIC_', 'DASHSCOPE_'))
                   and key.upper() not in excluded}
    environment.update(TEMP=str(run), TMP=str(run), PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
                       PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PR04A_OBJECT_INTEGRATION='1',
                       AWS_EC2_METADATA_DISABLED='true',
                       PLAYWRIGHT_BROWSERS_PATH=str(payload / 'ms-playwright'),
                       PR05A_DEPENDENCY_ROOT=str(BASE), PR05A_REVIEW_OUTPUT=str(output),
                       PR05A_VMWARE_GUEST='1', CI='1')
    environment['PATH'] = str(payload / 'node') + os.pathsep + environment.get('PATH', '')
    dependencies = Path(args.deps) if args.deps else root / 'work/pr07b/validation-deps'
    if args.deps or dependencies.is_dir():
        if not dependencies.is_absolute() or not dependencies.is_dir():
            parser.error('--deps must name an existing absolute validation package directory')
        environment['PYTHONPATH'] = str(dependencies)
    if args.auth_browser:
        environment['PR03B_REAL_BROWSER'] = '1'

    def call(arguments, *, timeout, cwd=None, env=None):
        command = [str(value) for value in arguments]
        commands.append(command)
        capture = output / f'command-{len(commands):02d}.log'
        with capture.open('wb') as stream:
            return subprocess.run(command, cwd=cwd, env=environment if env is None else env, stdout=stream,
                                  stderr=subprocess.STDOUT, creationflags=flags, timeout=timeout)

    pg_started = False
    server = None
    test = stop = None
    suite = None
    failure = None
    cleanup_errors = []
    frontend_build = []
    pytest_guard = None
    pg_port = ephemeral_port()
    admin = 'pr02a_admin_' + tag[:12]
    try:
        if args.auth_browser:
            frontend = root / 'web/frontend'
            node = payload / 'node/node.exe'
            if not all(path.is_file() for path in (node, frontend / 'node_modules/@playwright/test/index.mjs',
                    frontend / 'node_modules/typescript/bin/tsc', frontend / 'node_modules/vite/bin/vite.js')):
                raise RuntimeError('prepared frontend dependency junction or node is missing')
            original_lock = (BASE / 'web/frontend/pnpm-lock.yaml').read_bytes().replace(b'\r\n', b'\n')
            if (frontend / 'pnpm-lock.yaml').read_bytes().replace(b'\r\n', b'\n') != original_lock:
                raise RuntimeError('snapshot frontend lock differs from the prepared dependency installation')
            steps = [('contracts', ['scripts/gen-contracts.mjs', '--check']),
                     ('typescript', ['node_modules/typescript/bin/tsc', '-b', '--pretty', 'false']),
                     ('vite', ['node_modules/vite/bin/vite.js', 'build', '--mode', 'production']),
                     ('verify-build', ['scripts/verify-build.mjs'])]
            for label, arguments in steps:
                outcome = call([node, *arguments], cwd=frontend.resolve(), timeout=180)
                frontend_build.append(dict(step=label, exit_code=outcome.returncode))
                if outcome.returncode:
                    raise RuntimeError('fresh snapshot frontend build failed')
        if not all(path.is_file() for path in (pg_bin / 'initdb.exe', pg_bin / 'pg_ctl.exe', weed)):
            raise RuntimeError('prepared guest PostgreSQL/SeaweedFS executable is missing')
        if pg_port == 5432:
            raise RuntimeError('refusing the production PostgreSQL default port')
        initial = call([pg_bin / 'initdb.exe', '-D', run / 'pg', '-U', admin, '-A', 'trust',
                        '--encoding=UTF8', '--locale=C'], timeout=30)
        if initial.returncode:
            raise RuntimeError('owned PostgreSQL initialization failed')
        try:
            started = call([pg_bin / 'pg_ctl.exe', '-D', run / 'pg', '-l', pg_log, '-o',
                f'-h 127.0.0.1 -p {pg_port} -c max_connections=100 -c log_statement=none '
                '-c log_min_error_statement=panic -c log_parameter_max_length=0 '
                '-c log_parameter_max_length_on_error=0', '-w', '-t', '20', 'start'], timeout=30)
        finally:
            pg_started = (run / 'pg/postmaster.pid').is_file()
        if started.returncode or not pg_started:
            raise RuntimeError('owned PostgreSQL startup failed')
        import psycopg
        with psycopg.connect(host='127.0.0.1', port=pg_port, user=admin, dbname='postgres',
                             autocommit=True) as connection:
            connection.execute('CREATE DATABASE pr02a_control')
        environment['PR02A_TEST_ADMIN_URL'] = f'postgresql+psycopg://{admin}@127.0.0.1:{pg_port}/pr02a_control'
        environment['PR03A_THIRD_ROUND_PG_LOG'] = str(pg_log)
        master, volume, filer, s3 = object_ports()
        (run / 'objects').mkdir()
        configuration = run / 's3.json'
        configuration.write_text(json.dumps({'identities': [{'name': 'synthetic-only', 'credentials': [
            {'accessKey': 'pr04a-synthetic', 'secretKey': 'pr04a-synthetic-secret'}],
            'actions': ['Admin', 'Read', 'Write', 'List', 'Tagging']}]}), encoding='utf8')
        arguments = [str(weed), 'server', '-ip=127.0.0.1', '-ip.bind=127.0.0.1',
            '-dir=' + str(run / 'objects'), '-master.port=' + str(master), '-volume.port=' + str(volume),
            '-filer', '-filer.port=' + str(filer), '-s3', '-s3.port=' + str(s3), '-s3.config=' + str(configuration),
            '-volume.max=512', '-master.volumeSizeLimitMB=64', '-master.telemetry=false',
            '-s3.port.iceberg=0', '-s3.port.lance=0', '-s3.iam=false']
        commands.append(arguments)
        with (output / 'seaweed.log').open('wb') as server_log:
            server = subprocess.Popen(arguments, cwd=run, env=environment, stdout=server_log,
                                      stderr=subprocess.STDOUT, creationflags=flags)
            import httpx
            environment['PR04A_S3_ENDPOINT'] = f'http://127.0.0.1:{s3}'
            for _ in range(90):
                if server.poll() is not None:
                    raise RuntimeError('owned object service exited before readiness')
                try:
                    if httpx.get(environment['PR04A_S3_ENDPOINT'], timeout=1).status_code in (200, 401, 403):
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError('owned object service readiness timed out')
            import boto3
            from botocore.config import Config
            from botocore.exceptions import BotoCoreError, ClientError
            probe = boto3.client('s3', endpoint_url=environment['PR04A_S3_ENDPOINT'], region_name='us-east-1',
                aws_access_key_id='pr04a-synthetic', aws_secret_access_key='pr04a-synthetic-secret',
                config=Config(connect_timeout=1, read_timeout=3, retries={'total_max_attempts': 1},
                              request_checksum_calculation='when_required'))
            probe.create_bucket(Bucket='pr04a-readiness')
            for _ in range(30):
                try:
                    probe.put_object(Bucket='pr04a-readiness', Key='ready', Body=b'ready')
                    break
                except (ClientError, BotoCoreError):
                    time.sleep(1)
            else:
                raise RuntimeError('owned object service write readiness timed out')
            test_entry = ['-m', 'pytest']
            test_environment = None
            if guard is not None:
                test_entry = ['-c', GUARDED_PYTEST, guard]
                test_environment = {key: value for key, value in environment.items()
                                    if not key.upper().endswith('_PROXY')}
                test_paths = [str(guard.parent)]
                if 'PYTHONPATH' in environment:
                    test_paths.append(environment['PYTHONPATH'])
                test_environment['PYTHONPATH'] = os.pathsep.join(test_paths)
                pytest_guard = dict(path=str(guard), sha256=hashlib.sha256(guard.read_bytes()).hexdigest(),
                                   verified=False)
            test = call([sys.executable, '-B', *test_entry, 'tests', '-q',
                         '--basetemp=' + str(run / 'pytest'), '-o', 'cache_dir=' + str(run / 'cache'),
                         '--junitxml=' + str(junit)], cwd=root / 'web/backend', env=test_environment,
                         timeout=args.timeout)
            if pytest_guard is not None:
                log = output / f'command-{len(commands):02d}.log'
                pytest_guard['verified'] = GUARD_VERIFIED in log.read_text(encoding='utf8', errors='replace').splitlines()
                if not pytest_guard['verified']:
                    raise RuntimeError('Combined pytest loopback guard verification failed')
            suite = suite_evidence(root, junit, args.auth_browser)
    except Exception as error:
        failure = type(error).__name__
        (output / 'runner-error.log').write_text(traceback.format_exc(), encoding='utf8')
    finally:
        # Both cleanup branches run even when one owned service cannot stop.
        if server is not None and server.poll() is None:
            try:
                server.terminate()
                try:
                    server.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=10)
            except Exception as error:
                cleanup_errors.append('object:' + type(error).__name__)
        if pg_started:
            try:
                stop = call([pg_bin / 'pg_ctl.exe', '-D', run / 'pg', '-m', 'fast', '-w',
                             '-t', '20', 'stop'], timeout=30)
            except Exception as error:
                cleanup_errors.append('postgres:' + type(error).__name__)
        result = dict(run=tag, root=str(root), work=str(run), output=str(output), commands=commands,
            test_exit=test.returncode if test else None, pg_stop_exit=stop.returncode if stop else None,
            object_process_stopped=server is not None and server.poll() is not None,
            postgres='17.11', postgres_log=str(pg_log), object_service='SeaweedFS 4.53', synthetic_only=True,
            auth_browser_included=args.auth_browser, frontend_build=frontend_build,
            validation_dependencies=str(dependencies) if dependencies.is_dir() else None,
            pytest_loopback_guard=pytest_guard, suite=suite, runner_failure=failure, cleanup_errors=cleanup_errors)
        (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
        print(json.dumps(result))
        for path in sorted(output.glob('command-*.log')):
            print(path.name, path.read_text(encoding='utf8', errors='replace')[-12000:])
    success = (test is not None and test.returncode == 0 and stop is not None and stop.returncode == 0
               and server is not None and server.poll() is not None and not failure and not cleanup_errors
               and suite and suite['junit_cases'] > 0 and suite['all_required_executed']
               and not (suite['failures'] or suite['errors'] or suite['skipped']))
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
