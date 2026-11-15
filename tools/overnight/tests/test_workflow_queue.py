"""Offline v2 queue boundaries, request persistence and explicit execution."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch
import zipfile

from tools.overnight import workflow_queue as queue
from tools.overnight.state import StateError, read_json, atomic_json
from tools.overnight.task_config import plan_hash


ROOT = Path(__file__).resolve().parents[3]
CONTROL = 'a' * 40


def make_request(plan, **changes):
    value = dict(version=2, repository=plan['repository'], control_sha=CONTROL,
                 request_id='123', plan_path=queue.PLAN_PATH, config_sha=plan_hash(plan),
                 authorization_id=plan['authorization_id'])
    value.update(changes)
    return value


def zipped(value, *, name='request.json', extra=False, raw=None, symlink=False):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        info = zipfile.ZipInfo(name)
        if symlink:
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, json.dumps(value) if raw is None else raw)
        if extra:
            archive.writestr('command.sh', 'untrusted')
    return output.getvalue()


class FakeClient:
    def __init__(self, plan):
        self.calls = []
        self.runs = [dict(id=123, head_sha=CONTROL, event='workflow_dispatch', path=queue.WORKFLOW_PATH,
                          status='completed', conclusion='success', actor={'login': 'Mumao233'},
                          triggering_actor={'login': 'Mumao233'},
                          repository={'full_name': 'Mumao233/classroom-os'})]
        self.artifacts = [dict(id=456, name='overnight-request', expired=False)]
        self.data = zipped(make_request(plan))

    def get(self, path):
        self.calls.append(('GET', path))
        if path.endswith('runs?status=success&per_page=30'):
            return {'workflow_runs': self.runs}
        if path.endswith('/actions/runs/123/artifacts'):
            return {'artifacts': self.artifacts}
        raise AssertionError('unexpected GET ' + path)

    def get_bytes(self, path, *, max_bytes):
        self.calls.append(('GET_BYTES', path, max_bytes))
        if path != '/repos/Mumao233/classroom-os/actions/artifacts/456/zip':
            raise AssertionError('unexpected artifact GET ' + path)
        return self.data


class WorkflowQueueTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / queue.PLAN_PATH).read_text('utf-8'))
        self.client = FakeClient(self.plan)

    def temporary(self):
        folder = ROOT / 'work/overnight/tests'
        folder.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix='workflow-queue-', dir=folder)
        self.addCleanup(temp.cleanup)
        return Path(temp.name)

    def test_request_requires_exact_v2_identity_and_no_commands(self):
        request = make_request(self.plan)
        self.assertEqual(queue.validate_request(request, self.plan, CONTROL), request)
        changes = [('version', 1), ('version', 2.0), ('version', True), ('request_id', 123),
                   ('request_id', '0123'), ('request_id', '../123'), ('request_id', '9' * 31),
                   ('repository', 'other/repo'), ('control_sha', 'b' * 40),
                   ('config_sha', 'b' * 64), ('authorization_id', 'new-auth'),
                   ('plan_path', 'work/untrusted.json'), ('command', 'execute arbitrary')]
        for key, value in changes:
            with self.subTest(key=key, value=value), self.assertRaises(StateError):
                queue.validate_request(dict(request, **{key: value}), self.plan, CONTROL)
        missing = deepcopy(request)
        del missing['config_sha']
        with self.assertRaises(StateError):
            queue.validate_request(missing, self.plan, CONTROL)

    def test_archive_structure_size_and_json_are_bounded(self):
        request = make_request(self.plan)
        self.assertEqual(queue.decode_request(zipped(request), self.plan, CONTROL), request)
        compressed = io.BytesIO()
        with zipfile.ZipFile(compressed, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('request.json', json.dumps(request))
        corrupted = bytearray(compressed.getvalue())
        corrupted[30 + len('request.json')] = 7  # invalid DEFLATE block type
        cases = [zipped(request, name='../request.json'), zipped(request, extra=True),
                 zipped(request, symlink=True), b'bad zip', b'x' * 32769,
                 bytes(corrupted),
                 zipped(request, raw=' ' * 4097), zipped(request, raw=b'\xff'),
                 zipped(request, raw='{"version":2,"version":2}'),
                 zipped(request, raw='{"version":NaN}'), zipped(request, raw='[]')]
        for value in cases:
            with self.subTest(size=len(value)), self.assertRaises(StateError):
                queue.decode_request(value, self.plan, CONTROL)

    def test_only_successful_owner_manual_runs_match(self):
        request = queue.find_request(self.client, self.plan, CONTROL)
        self.assertEqual(request['request_id'], '123')
        self.assertEqual(self.client.calls[-1][-1], 32768)
        for key, value in [('event', 'push'), ('event', 'schedule'), ('head_sha', 'b' * 40),
                           ('actor', {'login': 'attacker'}), ('triggering_actor', {'login': 'attacker'}),
                           ('actor', None), ('repository', {'full_name': 'other/repo'}),
                           ('path', '.github/workflows/other.yml'), ('conclusion', 'failure'),
                           ('status', 'in_progress'), ('id', True)]:
            client = FakeClient(self.plan)
            client.runs[0][key] = value
            with self.subTest(key=key, value=value):
                self.assertIsNone(queue.find_request(client, self.plan, CONTROL))
                self.assertEqual(len(client.calls), 1)

    def test_artifact_ambiguity_and_run_identity_fail_closed(self):
        original = deepcopy(self.client.artifacts[0])
        for artifacts in ([], [original, original], [dict(original, expired=True)],
                          [dict(original, expired=None)], [dict(original, id=True)]):
            self.client.artifacts = artifacts
            with self.subTest(artifacts=artifacts), self.assertRaises(StateError):
                queue.find_request(self.client, self.plan, CONTROL)
        self.client.artifacts = [original]
        self.client.data = zipped(make_request(self.plan, request_id='999'))
        with self.assertRaises(StateError):
            queue.find_request(self.client, self.plan, CONTROL)

    def test_bad_metadata_is_not_treated_as_a_valid_request(self):
        for reply in (None, [], {}, {'workflow_runs': 'not-list'}, {'workflow_runs': [None]}):
            with patch.object(self.client, 'get', return_value=reply), self.assertRaises(StateError):
                queue.find_request(self.client, self.plan, CONTROL)

    def test_validation_only_has_no_persistence(self):
        source = self.temporary()
        queue.bind_request(source, make_request(self.plan), self.plan, CONTROL, persist=False)
        self.assertEqual(list(source.iterdir()), [])

    def test_same_request_and_new_same_plan_id_preserve_state_and_ledger(self):
        source = self.temporary()
        root = source / 'work/overnight/workflows' / self.plan['authorization_id']
        root.mkdir(parents=True)
        for name in ('state.json', 'subscription.json'):
            (root / name).write_text('existing checkpoint: do not touch', 'utf-8')
        request = make_request(self.plan)
        first = queue.bind_request(source, request, self.plan, CONTROL)
        destination = source / 'work/overnight/workflow-requests' / (self.plan['authorization_id'] + '.json')
        before = destination.stat().st_mtime_ns
        self.assertEqual(queue.bind_request(source, request, self.plan, CONTROL), first)
        self.assertEqual(destination.stat().st_mtime_ns, before)
        result = queue.bind_request(source, dict(request, request_id='124'), self.plan, CONTROL)
        self.assertEqual(set(result['requests']), {'123', '124'})
        for name in ('state.json', 'subscription.json'):
            self.assertEqual((root / name).read_text('utf-8'), 'existing checkpoint: do not touch')

    def test_new_request_id_cannot_change_existing_authorization_identity(self):
        for drift in ('control', 'config'):
            source = self.temporary()
            queue.bind_request(source, make_request(self.plan), self.plan, CONTROL)
            changed = deepcopy(self.plan)
            control = CONTROL
            if drift == 'control':
                control = 'b' * 40
            else:
                changed['tasks']['06a']['goal'] += ' modified'
            request = make_request(changed, control_sha=control, request_id='124')
            with self.subTest(drift=drift), self.assertRaises(StateError):
                queue.bind_request(source, request, changed, control)

    def test_mutated_persisted_request_or_authorization_fails_closed(self):
        for drift in ('request', 'authorization', 'unknown-field', 'version'):
            source = self.temporary()
            request = make_request(self.plan)
            queue.bind_request(source, request, self.plan, CONTROL)
            path = source / 'work/overnight/workflow-requests' / (self.plan['authorization_id'] + '.json')
            record = read_json(path)
            if drift == 'request':
                record['requests']['123'] = '0' * 64
            elif drift == 'authorization':
                record['authorization_id'] = 'changed'
            elif drift == 'version':
                record['version'] = 2.0
            else:
                record['reset'] = True
            atomic_json(path, record)
            with self.subTest(drift=drift), self.assertRaises(StateError):
                queue.bind_request(source, request, self.plan, CONTROL)

    def test_cli_loads_control_before_credentials_and_defaults_to_validation(self):
        events = []
        def loader(*args):
            events.append('load')
            return self.plan, {}, plan_hash(self.plan)
        def client(*args):
            self.assertEqual(events, ['load'])
            events.append('client')
            return self.client
        with patch.object(queue, 'load_plan', side_effect=loader), \
                patch.object(queue.GitHubClient, 'from_git_credentials', side_effect=client), \
                patch.object(queue, 'bind_request') as binder, \
                patch('tools.overnight.workflow_adapter.ProductionAdapter') as adapter, \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(queue.main(['--control-sha', CONTROL]), 0)
            binder.assert_called_once_with(ROOT, make_request(self.plan), self.plan, CONTROL, persist=False)
            adapter.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())['status'], 'validated_not_executed')
        with patch.object(queue, 'load_plan', side_effect=StateError('invalid pinned plan')), \
                patch.object(queue.GitHubClient, 'from_git_credentials') as credentials:
            with self.assertRaises(StateError):
                queue.main(['--control-sha', CONTROL])
            credentials.assert_not_called()

    def test_cli_no_match_stops_without_adapter_or_binding(self):
        self.client.runs = []
        with patch.object(queue, 'load_plan', return_value=(self.plan, {}, plan_hash(self.plan))), \
                patch.object(queue.GitHubClient, 'from_git_credentials', return_value=self.client), \
                patch.object(queue, 'bind_request') as binder, \
                patch('tools.overnight.workflow_adapter.ProductionAdapter') as adapter, \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(queue.main(['--control-sha', CONTROL]), 0)
            binder.assert_not_called()
            adapter.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())['status'], 'no_matching_pinned_request')

    def test_cli_rejects_source_spoofing_publish_without_execute_and_missing_deps(self):
        for flags in (['--source', str(self.temporary())], ['--publish'], ['--execute']):
            with patch.object(queue, 'load_plan') as loader, self.assertRaises(StateError):
                queue.main(['--control-sha', CONTROL, *flags])
            loader.assert_not_called()

    def test_cli_explicit_execute_reuses_authorization_and_forwards_draft_option(self):
        with patch.object(queue, 'load_plan', return_value=(self.plan, {}, plan_hash(self.plan))), \
                patch.object(queue.GitHubClient, 'from_git_credentials', return_value=self.client), \
                patch.object(queue, 'bind_request') as binder, \
                patch('tools.overnight.workflow_adapter.ProductionAdapter') as adapter, \
                patch('tools.overnight.workflow.Workflow') as workflow, \
                patch('sys.stdout', new_callable=io.StringIO):
            workflow.return_value.run.return_value = {'status': 'STOPPED_NO_READY_TASKS',
                                                       'tasks': {'06a': {'effective_state': 'SKIPPED'}}}
            args = ['--control-sha', CONTROL, '--execute', '--deps', 'fixed-deps', '--publish', '--codex', 'fake-codex']
            for _ in range(2):
                self.assertEqual(queue.main(args), 0)
            self.assertEqual(workflow.call_count, 2)
            self.assertEqual(workflow.call_args_list[0], workflow.call_args_list[1])
            self.assertEqual(adapter.call_args.args[2], ROOT / 'work/overnight/workflows' / self.plan['authorization_id'])
            self.assertTrue(adapter.call_args.args[-1])
            self.assertEqual(binder.call_args.kwargs, {'persist': True})
            workflow.return_value.run.return_value = {'status': 'GLOBAL_STOP'}
            self.assertEqual(queue.main(args), 1)

    def test_manual_workflow_emitter_uses_canonical_plan_and_pinned_checkout(self):
        text = (ROOT / queue.WORKFLOW_PATH).read_text('utf-8')
        trigger = text.split('on:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertEqual(trigger.strip(), 'workflow_dispatch:')
        self.assertIn('persist-credentials: false', text)
        self.assertIn('ref: ${{ github.sha }}', text)
        script = text.split("python -I -B - <<'PY'\n", 1)[1].split('\n          PY', 1)[0]
        source = self.temporary()
        (source / 'automation').mkdir()
        plan = deepcopy(self.plan)
        plan['tasks']['06a']['goal'] += ' 中文测试'
        (source / queue.PLAN_PATH).write_text(json.dumps(plan, ensure_ascii=False), 'utf-8')
        environment = dict(os.environ, REQUEST_SHA=CONTROL, REQUEST_RUN_ID='123')
        completed = subprocess.run([sys.executable, '-I', '-B', '-'], input=textwrap.dedent(script).encode('utf-8'),
                                   cwd=source, env=environment, capture_output=True, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode('utf-8', errors='replace'))
        actual = json.loads((source / 'request.json').read_text('utf-8'))
        self.assertEqual(actual, make_request(plan))


if __name__ == '__main__':
    unittest.main()
