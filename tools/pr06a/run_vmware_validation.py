"""Run offline PR-06a acceptance in a manifest-verified Windows VMware snapshot."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--deps', type=Path, required=True)
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--legacy-python', type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    output = root / 'outputs/pr06a'
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(root))
    from tools.overnight.asr_gate import parse_summary
    from tools.overnight.ci_backend import clean_environment

    manifest = json.loads((root / 'SOURCE.json').read_text(encoding='utf-8'))
    driver = Path(__file__).resolve(strict=True)
    driver_hash = hashlib.sha256(driver.read_bytes()).hexdigest()
    if driver_hash != manifest['runner_sha256']:
        raise RuntimeError('Executed validation runner differs from SOURCE.json')
    mismatches = [name for name, digest in manifest['files_sha256'].items()
                  if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest]
    if mismatches:
        raise RuntimeError('Snapshot files differ from SOURCE.json')
    from tools.pr02b.run_tests import NETWORK_GUARD

    environment = clean_environment(os.environ)
    environment['PYTHONPATH'] = os.pathsep.join((str(args.deps), str(root)))
    report = dict(source_sha256=manifest['source_zip_sha256'],
                  base_sha=manifest['base_sha'], source_file_count=len(manifest['files_sha256']),
                  executed_runner_sha256=driver_hash,
                  source_mismatches=mismatches,
                  execution_scope='Offline simulation and synthetic loopback test commands only',
                  provider_validation='not authorized or executed', steps=[])

    def preserve_evidence():
        (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        archive = output / 'evidence.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as package:
            package.write(root / 'SOURCE.json', 'SOURCE.json')
            package.write(driver, 'driver/run_vmware_validation.py')
            for directory in ('outputs/pr06a', 'outputs/pr07b', 'outputs/pr11a', 'outputs/pr03a/third-round',
                              'work/pr00/pr06a-validation'):
                for path in (root / directory).rglob('*'):
                    if path.is_file() and path.suffix in ('.json', '.log', '.xml', '.md'):
                        package.write(path, path.relative_to(root).as_posix())
        print('evidence_sha256', hashlib.sha256(archive.read_bytes()).hexdigest(), flush=True)

    def run(name, command, timeout=300, child_environment=None):
        command = [str(value) for value in command]
        try:
            result = subprocess.run(command, cwd=root, env=child_environment or environment,
                                    capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=timeout,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as error:
            def decoded(value):
                return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or ''
            result = subprocess.CompletedProcess(command, 124, decoded(error.stdout),
                decoded(error.stderr) + '\nTimed out. Descendant state requires inspection before retry.\n')
        except OSError as error:
            result = subprocess.CompletedProcess(command, 1, '', type(error).__name__)
        (output / (name + '.stdout.log')).write_text(result.stdout, encoding='utf-8')
        (output / (name + '.stderr.log')).write_text(result.stderr, encoding='utf-8')
        report['steps'].append(dict(name=name, command=command,
                                   process_exit_code=result.returncode, exit_code=result.returncode))
        (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(name, result.returncode, flush=True)
        if result.returncode == 124:
            report.update(passed=False, unresolved_descendants=True)
            preserve_evidence()
            raise SystemExit(124)
        return result

    backend = root / 'web/backend'
    suite = backend / 'tests/asr_dashscope'
    scratch = root / 'work/pr06a/dashscope-tests'
    guard = scratch / 'guard'
    guard.mkdir(parents=True, exist_ok=False)
    (guard / 'sitecustomize.py').write_text(NETWORK_GUARD, encoding='utf8')
    guarded = dict(environment, PYTHONPATH=os.pathsep.join((str(guard), environment['PYTHONPATH'])))
    probe = run('dashscope-network-guard', [sys.executable, '-B', '-c',
        "import sys; sys.audit('socket.connect', None, ('198.51.100.1', 443))"],
        child_environment=guarded)
    report['steps'][-1]['exit_code'] = int(
        probe.returncode != 1 or 'PR-02b tests deny non-loopback' not in probe.stderr)

    def dashscope_pytest(name, target, env):
        junit = output / (name + '.xml')
        result = run(name, [sys.executable, '-B', '-m', 'pytest', target, '-q',
            '-c', backend / 'pyproject.toml', '-o', 'pythonpath=', '-p', 'no:cacheprovider',
            '--basetemp=' + str(scratch / name), '--junitxml=' + str(junit)], child_environment=env)
        step = report['steps'][-1]
        try:
            cases = list(ET.parse(junit).iter('testcase'))
        except (OSError, ET.ParseError) as error:
            step.update(exit_code=1, validation_error=type(error).__name__)
            return result, step, []
        step['summary'] = dict(tests_run=len(cases),
            failures=sum(case.find('failure') is not None for case in cases),
            errors=sum(case.find('error') is not None for case in cases),
            skipped=sum(case.find('skipped') is not None for case in cases))
        return result, step, cases

    suite_environment = dict(guarded, PYTHONPATH=os.pathsep.join((guarded['PYTHONPATH'], str(backend))))
    _, suite_step, suite_cases = dashscope_pytest('dashscope', suite, suite_environment)
    if not suite_cases or any(suite_step['summary'][part] for part in ('failures', 'errors', 'skipped')):
        suite_step.update(exit_code=1, validation_error='Protocol suite must complete without failures, errors or skips')

    def negative_control(name, source, needle, replacement, node, expected_failure):
        shadow = scratch / (name + '-source')
        shutil.copytree(backend / 'classroom_web', shadow / 'classroom_web')
        target = shadow / 'classroom_web/asr_dashscope' / source
        original = target.read_bytes()
        before, after = needle.encode(), replacement.encode()
        if original.count(before) != 1:
            report['steps'].append(dict(name=name, exit_code=1,
                validation_error='Expected exactly one matching mutation point in ' + source))
            return
        mutant = original.replace(before, after)
        target.write_bytes(mutant)
        env = dict(guarded, PYTHONPATH=os.pathsep.join((guarded['PYTHONPATH'], str(shadow), str(backend))))
        result, step, cases = dashscope_pytest(name, str(suite / node), env)
        step['mutation'] = dict(file=target.relative_to(shadow).as_posix(), node=node,
            original_sha256=hashlib.sha256(original).hexdigest(),
            mutant_sha256=hashlib.sha256(mutant).hexdigest(), before=needle, after=replacement,
            expected_failure=expected_failure)
        observed = cases[0].find('failure') if len(cases) == 1 else None
        step['exit_code'] = int(result.returncode != 1 or observed is None
            or step['summary'] != dict(tests_run=1, failures=1, errors=0, skipped=0)
            or expected_failure not in (observed.get('message', '') + (observed.text or '')))
        if step['exit_code']:
            step['validation_error'] = 'Selected test must fail against the mutation for the expected reason'

    negative_control('dashscope-mutant-task-id', 'wire.py',
        'if uid(header.get("task_id")) != uid(provider_task_id):',
        'if not uid(header.get("task_id")):',
        'test_wire.py::test_task_identity_must_be_the_one_canonical_uuid_for_this_connection'
        '[fb1b224a-c3bb-4504-9518-72a77b1190dc]', 'DID NOT RAISE')
    negative_control('dashscope-mutant-real-mode', 'adapter.py',
        "if config.get('mode', 'synthetic') != 'synthetic':", 'if False:',
        'test_adapter.py::test_real_mode_rejected_before_connect_even_with_permit', 'DID NOT RAISE')

    binding = dict(head_sha=manifest['base_sha'], patch_sha256=manifest['source_zip_sha256'])
    for stage in ('candidate', 'trusted'):
        nonce = uuid.uuid4().hex
        result = run('asr-' + stage, [sys.executable, '-I', '-X', 'utf8', '-B', 'tools/overnight/asr_gate.py',
            '--stage', stage, '--workspace', root, '--deps', args.deps,
            '--nonce', nonce, '--head', binding['head_sha'], '--patch', binding['patch_sha256']])
        try:
            summary = parse_summary(result.stdout, stage=stage, nonce=nonce, binding=binding)
            report['steps'][-1]['summary'] = summary
        except ValueError as error:
            report['steps'][-1]['validation_error'] = str(error)
            report['steps'][-1]['exit_code'] = 1
    run('contracts', [sys.executable, '-I', '-X', 'utf8', '-B', 'tools/overnight/check_contracts.py',
        '--deps', args.deps, '--output', output / 'contracts.json'])
    controller = run('controller', [sys.executable, '-X', 'utf8', '-B', '-m', 'unittest', 'discover',
                                   '-s', 'tools/overnight/tests', '-p', 'test_*.py', '-v'], timeout=900)
    count = re.search(r'^Ran (\d+) tests? in ', controller.stderr, re.MULTILINE)
    outcome = next((line for line in reversed(controller.stderr.splitlines())
                    if line == 'OK' or line.startswith(('OK (', 'FAILED ('))), None)
    if count is None or outcome is None:
        report['steps'][-1].update(exit_code=1, validation_error='Missing controller unittest completion summary')
    else:
        counts = dict(tests_run=int(count.group(1)), failures=0, errors=0, skipped=0)
        counts.update({name: int(value) for name, value in
                       re.findall(r'(failures|errors|skipped)=(\d+)', outcome)})
        report['steps'][-1]['summary'] = counts
    if not args.full:
        pg = Path('J:/class-assistant/worktrees/web-pr03b-sessions/work/pr03b/toolchains')
        pg = pg / 'pg17.11-win64-53b5364a5a04e8ec/runtime/Library/bin'
        run('billing-defaults', [sys.executable, '-I', '-X', 'utf8', '-B', 'tools/pr11a/run_tests.py',
            '--pg-bin', pg, '--suite', 'billing', '--test-target',
            'tests/integration/test_pr06a_billing_gate.py', '--test-target',
            'tests/integration/test_pr06a_dashscope_billing.py'], timeout=300)
    if args.full:
        run('backend', [sys.executable, '-I', '-X', 'utf8', '-B', 'tools/pr07b/run_combined.py',
                       '--root', root, '--auth-browser', '--deps', args.deps], timeout=2400)
        if args.legacy_python is None:
            raise RuntimeError('Full validation requires a prepared Python 3.10 interpreter')
    if args.legacy_python is not None:
        base = Path('J:/class-assistant/worktrees/web-pr03b-sessions')
        node = base / 'work/pr03b/vmware-broker/payload/node/node.exe'
        run('legacy', [args.legacy_python, '-X', 'utf8', '-B', 'tools/pr00/run_checks.py',
            '--node', node, '--powershell', 'powershell.exe',
            '--output-dir', root / 'work/pr00/pr06a-validation'], timeout=1500)
    report['source_mismatches_after'] = [name for name, digest in manifest['files_sha256'].items()
        if not (root / name).is_file() or hashlib.sha256((root / name).read_bytes()).hexdigest() != digest]
    report['passed'] = (all(step['exit_code'] == 0 for step in report['steps'])
                        and not report['source_mismatches_after'])
    has_skips = any(step.get('summary', {}).get('skipped', 0) for step in report['steps'])
    report['complete_without_skips'] = report['passed'] and not has_skips
    report['status'] = 'failed'
    if report['passed']:
        report['status'] = 'passed_with_skips' if has_skips else 'passed'
    preserve_evidence()
    print(json.dumps(report), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
