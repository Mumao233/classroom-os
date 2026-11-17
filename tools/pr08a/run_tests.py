"""Owned VMware verification for PR-08a. Every service and dataset is synthetic and loopback-only."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dependency', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt' or os.environ.get('PR08A_VMWARE_GUEST') != '1':
        raise SystemExit('VMware guest entrypoint required')
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / 'web/backend'))
    from classroom_web.test_support import SYNTHETIC_PASSWORD, validate_test_database_target
    import psycopg
    args.output.mkdir(parents=True, exist_ok=False)
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('PG') and key.upper() not in ('PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'PYTHONPATH', 'PYTHONPYCACHEPREFIX')}
    node = args.dependency / 'work/pr03b/vmware-broker/payload/node/node.exe'
    modules = args.dependency / 'web/frontend/node_modules'
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
               PR08A_OUTPUT=str(args.output), PR08A_FRONTEND_MODULES=str(modules),
               PR08A_PLAYWRIGHT_MODULE=str(modules / '@playwright/test/index.mjs'),
               PR05A_DEPENDENCY_ROOT=str(args.dependency), PR05A_REVIEW_OUTPUT=str(args.output),
               PLAYWRIGHT_BROWSERS_PATH=str(args.dependency / 'work/pr03b/vmware-broker/payload/ms-playwright'))
    env['PYTHONPATH'] = os.environ['PR08A_VALIDATION_DEPS']
    commands = []
    results = {}

    def execute(command, name, cwd=None, timeout=180):
        command = [str(value) for value in command]
        commands.append({'name': name, 'command': command, 'cwd': str(cwd or root)})
        with (args.output / (name + '.log')).open('wb') as log:
            process = subprocess.Popen(command, cwd=cwd or root, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, check=False)
                process.wait(timeout=20)
                code = 124
        results[name] = code
        print(json.dumps({'check': name, 'exit': code}), flush=True)
        return code

    execute([node, root / 'tools/pr08a/build.mjs'], 'bundle')
    if results['bundle'] == 0:
        execute([node, root / 'tools/pr08a/browser.mjs'], 'browser')
    if os.environ.get('PR08A_BROWSER_ONLY') == '1':
        report = {'package': 'PR-08a', 'mode': 'native-browser-only', 'results': results, 'commands': commands,
                  'passed': results == {'bundle': 0, 'browser': 0}, 'synthetic_only': True}
        (args.output / 'run.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)
        return 0 if report['passed'] else 1
    frontend = root / 'web/frontend'
    execute([node, modules / 'typescript/bin/tsc', '-b', '--pretty', 'false'], 'typecheck', frontend)
    execute([node, modules / 'eslint/bin/eslint.js', '.', '--max-warnings', '0'], 'lint', frontend)
    execute([node, 'scripts/gen-contracts.mjs', '--check'], 'generated', frontend)
    execute([node, modules / 'vitest/vitest.mjs', 'run', '--config', 'tests/vitest.config.ts'], 'frontend-unit', frontend)
    execute([node, modules / 'vite/bin/vite.js', 'build', '--mode', 'production'], 'build', frontend)
    execute([node, 'scripts/verify-build.mjs'], 'verify-build', frontend)

    marker = uuid4().hex[:12]
    admin = 'pr02a_admin_' + marker
    data = args.output / 'pg-data'
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    started = False
    try:
        if execute([args.pg_bin / 'initdb.exe', '-D', data, '-U', admin, '-A', 'trust', '--encoding=UTF8', '--locale=C'], 'initdb'):
            raise RuntimeError('owned PostgreSQL init failed')
        options = f'-h 127.0.0.1 -p {port} -c log_statement=none -c log_min_error_statement=panic -c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0'
        if execute([args.pg_bin / 'pg_ctl.exe', '-D', data, '-l', args.output / 'postgres.log', '-o', options, '-w', '-t', '20', 'start'], 'pg-start'):
            raise RuntimeError('owned PostgreSQL start failed')
        started = True
        with psycopg.connect(host='127.0.0.1', port=port, user=admin, password=SYNTHETIC_PASSWORD, dbname='postgres', autocommit=True) as connection:
            connection.execute('CREATE DATABASE pr02a_control')
        url = validate_test_database_target(f'postgresql+psycopg://{admin}@127.0.0.1:{port}/pr02a_control', environ=env)
        env['PR02A_TEST_ADMIN_URL'] = url.render_as_string(hide_password=False)
        selected = ['tests/integration/test_trusted_cache_browser.py', 'tests/integration/test_cache_keys.py',
                    'tests/integration/test_capture_migration.py', 'tests/unit/test_pr07b_contract.py',
                    'tests/integration/test_authentication.py::test_real_login_cookie_context_logout_and_revoked_cookie',
                    'tests/integration/test_authentication.py::test_bound_reauth_changes_password_and_revokes_existing_session']
        execute([sys.executable, '-B', '-m', 'pytest', *selected, '-q', '-rA', '--basetemp=' + str(args.output / 'pytest'),
                 '-o', 'cache_dir=' + str(args.output / 'pytest-cache'), '--junitxml=' + str(args.output / 'junit.xml')], 'pytest', root / 'web/backend', 300)
    finally:
        if started:
            execute([args.pg_bin / 'pg_ctl.exe', '-D', data, '-m', 'fast', '-w', '-t', '20', 'stop'], 'pg-stop')
        report = {'package': 'PR-08a', 'results': results, 'commands': commands, 'passed': all(code == 0 for code in results.values()), 'synthetic_only': True}
        (args.output / 'run.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
