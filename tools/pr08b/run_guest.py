"""PR-08b staged verification on owned, synthetic VMware guest services."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from uuid import uuid4
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'outputs/pr08b'
WORK = ROOT / 'work/pr08b'


def main():
    if os.name != 'nt' or os.environ.get('PR08B_VMWARE_GUEST') != '1':
        raise SystemExit('VMware guest entrypoint required')
    # Isolated Python ignores PYTHONUTF8. Preserve Unicode test diagnostics on Windows pipes.
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')
    mode = sys.argv[1]
    if mode not in {'targeted', 'frontend', 'full', 'legacy'}:
        raise SystemExit('Unknown verification stage')
    dependency = Path(os.environ['PR08B_DEPENDENCY_ROOT'])
    node = dependency / 'work/pr03b/vmware-broker/payload/node/node.exe'
    modules = dependency / 'web/frontend/node_modules'
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(('PG', 'AWS'))
           and key.upper() not in {'PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'PYTHONPATH', 'PYTHONPYCACHEPREFIX', 'NODE_OPTIONS'}}
    env.update(
        PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
        PYTHONPATH=os.environ.get('PR08B_VALIDATION_DEPS', ''),
        PR08B_SYNC_INTEGRATION='1', PR08B_OUTPUT=str(OUTPUT),
        PR08B_FRONTEND_MODULES=str(modules),
        PR08B_PLAYWRIGHT_MODULE=str(modules / '@playwright/test/index.mjs'),
        PR08A_OUTPUT=str(OUTPUT / 'cache'), PR08A_FRONTEND_MODULES=str(modules),
        PR08A_PLAYWRIGHT_MODULE=str(modules / '@playwright/test/index.mjs'),
        PR08A_COMBINED='1', PR08A_VMWARE_GUEST='1', PR05A_VMWARE_GUEST='1',
        PR05A_DEPENDENCY_ROOT=str(dependency), PR05A_REVIEW_OUTPUT=str(OUTPUT),
        PR04B_DELETION_INTEGRATION='1', PR05B_CAPTURE_INTEGRATION='1',
        PR04B_OBJECT_ROOT=str(WORK / 'objects'),
        PLAYWRIGHT_BROWSERS_PATH=str(dependency / 'work/pr03b/vmware-broker/payload/ms-playwright'),
    )
    commands = []
    results = {}
    junit = None
    failure = None
    started_at = datetime.now(timezone.utc).isoformat()

    def execute(command, name, cwd=ROOT, timeout=300, child_env=None):
        command = [str(value) for value in command]
        began = time.monotonic()
        with (OUTPUT / f'{name}.log').open('wb') as log:
            process = subprocess.Popen(command, cwd=cwd, env=child_env or env, stdout=log,
                                       stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, check=False)
                process.wait(timeout=20)
                code = 124
        results[name] = code
        commands.append({'name': name, 'command': command, 'cwd': str(cwd), 'exit_code': code,
                         'elapsed_seconds': round(time.monotonic() - began, 3)})
        print(json.dumps(commands[-1]), flush=True)
        if code:
            print((OUTPUT / f'{name}.log').read_text(encoding='utf-8', errors='replace')[-12000:], flush=True)
        return code

    pg_started = False
    media = None
    remaining = []
    pg_bin = Path(os.environ['PR04B_PG_BIN'])
    pg_data = WORK / 'pg-data'
    pg_log = ROOT / 'outputs/pr03a/third-round/postgres.log'
    try:
        if mode in {'frontend', 'full'}:
            env['PR03B_REAL_BROWSER'] = '1'
            execute([sys.executable, '-B', 'tools/overnight/check_contracts.py', '--output', OUTPUT / 'contracts.json'], 'contracts')
        if mode == 'full':
            execute([node, modules / 'vite/bin/vite.js', 'build', '--mode', 'production'], 'auth-frontend-build', ROOT / 'web/frontend')
            execute([node, 'scripts/verify-build.mjs'], 'auth-frontend-verify-build', ROOT / 'web/frontend')
        if mode == 'frontend':
            frontend = ROOT / 'web/frontend'
            execute([node, modules / 'typescript/bin/tsc', '-b', '--pretty', 'false'], 'typecheck', frontend)
            execute([node, modules / 'eslint/bin/eslint.js', '.', '--max-warnings', '0'], 'lint', frontend)
            execute([node, 'scripts/gen-contracts.mjs', '--check'], 'generated', frontend)
            execute([node, modules / 'vitest/vitest.mjs', 'run', '--config', 'tests/vitest.config.ts'], 'frontend-unit', frontend)
            execute([node, modules / 'vite/bin/vite.js', 'build', '--mode', 'production'], 'build', frontend)
            execute([node, 'scripts/verify-build.mjs'], 'verify-build', frontend)
            if execute([node, ROOT / 'tools/pr08a/build.mjs'], 'cache-bundle') == 0:
                execute([node, ROOT / 'tools/pr08a/browser.mjs'], 'cache-browser')
                execute([node, ROOT / 'tools/pr08a/combined-browser.mjs'], 'capture-cache-browser')
            if execute([node, ROOT / 'tools/pr08b/build.mjs'], 'sync-bundle') == 0:
                execute([node, ROOT / 'tools/pr08b/browser.mjs'], 'sync-browser')
        if mode == 'legacy':
            sys.path.insert(0, str(ROOT / 'tools/pr00'))
            from run_checks import NODE_TESTS, junit_result
            # Package fixtures append SHA-256 asset names. Keep their paths below Windows MAX_PATH.
            legacy_temp = ROOT / 'work/p'
            guarded = dict(env, CLASSROOM_PR00_TEST_GUARD='1', CLASSROOM_PR00_REPO_ROOT=str(ROOT),
                           PYTHONPATH=str(ROOT / 'tools/pr00/runtime_guard'), HF_HUB_OFFLINE='1',
                           TRANSFORMERS_OFFLINE='1', HF_HOME=str(WORK / 'hf'), PYTHONNOUSERSITE='1')
            python_guard = execute([sys.executable, '-B', 'tools/pr00/selftest_guards.py'], 'legacy-python-guard', child_env=guarded)
            node_guard = ROOT / 'tools/pr00/node_guard.cjs'
            node_check = execute([node, '--require', node_guard, 'tools/pr00/selftest_guards.cjs'], 'legacy-node-guard', child_env=guarded)
            execute([sys.executable, '-B', 'tools/pr00/selftest_junit.py'], 'legacy-junit-gate', child_env=guarded)
            if python_guard or node_check:
                raise RuntimeError('Legacy network/data guards failed')
            execute([sys.executable, '-B', '-m', 'pytest', 'tests', '-q', '--basetemp=' + str(legacy_temp),
                     '--junitxml=' + str(OUTPUT / 'legacy-junit.xml'), '-o', 'cache_dir=' + str(WORK / 'legacy-cache')],
                    'legacy-pytest', timeout=900, child_env=guarded)
            for name in NODE_TESTS:
                execute([node, '--require', node_guard, 'tests/' + name + '.cjs'], 'legacy-node-' + name, child_env=guarded)
            junit, valid = junit_result(OUTPUT / 'legacy-junit.xml')
            results['legacy-junit-required-baseline'] = 0 if valid else 1
        else:
            sys.path.insert(0, str(ROOT / 'web/backend'))
            sys.path.insert(0, str(ROOT / 'web/backend/tests/integration'))
            from classroom_web.test_support import validate_test_database_target
            import psycopg
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            if port == 5432:
                raise RuntimeError('Unexpected production-default test port')
            admin = 'pr02a_admin_' + uuid4().hex[:12]
            pg_log.parent.mkdir(parents=True, exist_ok=True)
            if execute([pg_bin / 'initdb.exe', '-D', pg_data, '-U', admin, '-A', 'trust', '--encoding=UTF8', '--locale=C'], 'pg-init'):
                raise RuntimeError('Owned PostgreSQL initialization failed')
            options = f'-h 127.0.0.1 -p {port} -c max_connections=100 -c log_statement=none -c log_min_error_statement=panic -c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0'
            pg_start_code = execute([pg_bin / 'pg_ctl.exe', '-D', pg_data, '-l', pg_log, '-o', options, '-w', '-t', '30', 'start'], 'pg-start')
            pg_started = (pg_data / 'postmaster.pid').exists()
            if pg_start_code or not pg_started:
                raise RuntimeError('Owned PostgreSQL start failed')
            with psycopg.connect(host='127.0.0.1', port=port, user=admin, dbname='postgres', autocommit=True) as connection:
                connection.execute('CREATE DATABASE pr02a_control')
            url = validate_test_database_target(f'postgresql+psycopg://{admin}@127.0.0.1:{port}/pr02a_control', environ=env)
            env['PR02A_TEST_ADMIN_URL'] = url.render_as_string(hide_password=False)
            env['PR03A_THIRD_ROUND_PG_LOG'] = str(pg_log)
            required = sorted((ROOT / 'web/backend/tests').rglob('test_pr08b*.py'))
            if not required:
                raise RuntimeError('PR-08b tests are required for every backend stage')
            selected = [str(path.relative_to(ROOT / 'web/backend')) for path in required]
            if mode == 'targeted':
                selected += [
                    'tests/unit/test_pr02b_runner.py', 'tests/integration/test_persistent_tasks.py',
                    'tests/integration/test_pr07b_versions.py', 'tests/integration/test_pr07b_http.py',
                    'tests/integration/test_deletion_lifecycle.py', 'tests/integration/test_deletion_restore.py',
                    'tests/integration/test_cache_keys.py', 'tests/integration/test_trusted_device_guard.py',
                ]
            if mode == 'frontend':
                selected += ['tests/integration/test_trusted_cache_browser.py', 'tests/integration/test_auth_browser.py']
            if mode == 'full':
                from object_service import ObjectService
                media = ObjectService(WORK / 'objects', 'pr04a-media', access='pr04a-synthetic',
                                      secret='pr04a-synthetic-secret', volumes=512).start()
                env.update(PR04A_OBJECT_INTEGRATION='1', PR04A_S3_ENDPOINT=media.endpoint)
                selected = ['tests']
            execute([sys.executable, '-B', '-m', 'pytest', *selected, '-q', '-rA', '-p', 'no:cacheprovider',
                     '--basetemp=' + str(WORK / 'pytest'), '--junitxml=' + str(OUTPUT / 'junit.xml')],
                    'backend-pytest', ROOT / 'web/backend', 2100)
            tree = ET.parse(OUTPUT / 'junit.xml')
            cases = list(tree.iter('testcase'))
            expected = {(path.stem, item.name) for path in required
                        for item in ast.parse(path.read_text(encoding='utf-8')).body
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith('test_')}
            observed = {(case.get('classname', '').split('.')[-1], case.get('name', '').split('[', 1)[0]) for case in cases}
            suites = list(tree.iter('testsuite'))
            junit = {name: sum(int(suite.get(name, '0')) for suite in suites)
                     for name in ('tests', 'failures', 'errors', 'skipped')}
            junit.update(parent_testcases=len(cases), subtests=junit['tests'] - len(cases),
                         required_pr08b=len(expected), observed_pr08b=len(expected & observed))
            failures = [{'test': case.get('classname') + '.' + case.get('name'),
                         'type': label, 'message': item.get('message', '')[:2500], 'traceback': item.text}
                        for case in cases for label in ('failure', 'error', 'skipped')
                        if (item := case.find(label)) is not None]
            (OUTPUT / 'failures.json').write_text(json.dumps(failures, indent=2), encoding='utf-8')
            for case_failure in failures:
                print(json.dumps({'test': case_failure['test'], 'type': case_failure['type'],
                                  'trace_tail': (case_failure['traceback'] or case_failure['message'])[-2500:]}), flush=True)
            results['junit-required-pr08b'] = 0 if expected and expected <= observed and not any(junit[name] for name in ('failures', 'errors', 'skipped')) else 1
            if mode in {'frontend', 'full'}:
                auth_cases = [case for case in cases if case.get('classname', '').endswith('test_auth_browser')]
                junit['pr03b_auth_browser_cases'] = len(auth_cases)
                results['junit-required-auth-browser'] = 0 if auth_cases else 1
    except Exception as error:
        failure = {'type': type(error).__name__, 'message': str(error)}
        results['runner'] = 1
        print(json.dumps(failure), flush=True)
    finally:
        if media is not None:
            media.stop()
        for pid_file in (WORK / 'objects').rglob('pid'):
            pid = pid_file.read_text(encoding='ascii').strip()
            probe = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if 'weed' in probe.stdout.lower():
                remaining.append(pid)
                subprocess.run(['taskkill', '/PID', pid, '/T', '/F'], capture_output=True, check=False)
        if pg_started:
            execute([pg_bin / 'pg_ctl.exe', '-D', pg_data, '-m', 'fast', '-w', '-t', '30', 'stop'], 'pg-stop')
        if pg_log.is_file():
            shutil.copyfile(pg_log, OUTPUT / 'postgres.log')
        for path in pg_log.parent.rglob('*.json'):
            shutil.copyfile(path, OUTPUT / ('third-round-' + path.name))
        for path in (WORK / 'objects').rglob('seaweed-*.log'):
            target = OUTPUT / ('object-' + '-'.join(path.relative_to(WORK / 'objects').parts))
            shutil.copyfile(path, target)
        report = {'package': 'PR-08b', 'stage': mode, 'started_at': started_at,
                  'finished_at': datetime.now(timezone.utc).isoformat(), 'python': sys.version,
                  'commands': commands, 'results': results, 'junit': junit, 'failure': failure,
                  'real_auth_browser_enabled': env.get('PR03B_REAL_BROWSER') == '1',
                  'object_processes_left': remaining, 'synthetic_only': True,
                  'passed': bool(results) and all(code == 0 for code in results.values()) and not remaining}
        (OUTPUT / 'run.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
