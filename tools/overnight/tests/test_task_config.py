"""Strict plans and exact tracked-object loading; only synthetic repositories."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.overnight.state import StateError, load_dag
from tools.overnight.task_config import load_plan, plan_hash, validate_evidence, validate_plan


ROOT = Path(__file__).resolve().parents[3]
DAG_PATH = 'docs/web-alpha/architecture/contracts/pr-dependencies.json'
EVIDENCE = 'docs/web-alpha/overnight/evidence/workflow-baselines.json'


class TaskConfigTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / 'automation/workflow.json').read_text('utf-8'))
        self.dag = load_dag(ROOT / DAG_PATH)

    def reject(self, mutate):
        plan = deepcopy(self.plan)
        mutate(plan)
        with self.assertRaises(StateError):
            validate_plan(plan, self.dag)

    def test_checked_plan_only_audits_existing_simulation(self):
        result = validate_plan(self.plan, self.dag)
        self.assertEqual(len(self.dag), 28)
        self.assertEqual(result['enabled_tasks'], ['06a'])
        self.assertEqual(result['tasks']['06a']['audit']['status'], 'COMPLETE')
        self.assertEqual(result['tasks']['05a']['audit']['status'], 'NOT_STARTED')
        self.assertNotIn('05a', result['enabled_tasks'])
        self.assertEqual(set(result['baselines']), {'00', '01'})
        self.assertTrue(all(b['approved'] for b in result['baselines'].values()))
        self.assertEqual(result['limits']['extra_api_spend_usd'], 0)
        self.assertNotIn('control_sha', result)

    def test_top_level_fields_types_and_authorization_are_strict(self):
        for field, value in (('version', True), ('version', 2.0), ('repository', 'other/repo'),
                             ('author_login', 'Codex'), ('authorization_id', '../escape'),
                             ('dag_path', 'other.json'), ('extra', 'unknown')):
            with self.subTest(field=field, value=value):
                self.reject(lambda p: p.update({field: value}))
        self.reject(lambda p: p.pop('baselines'))
        for enabled in (['unknown'], ['06a', '06a'], ['00'], '06a'):
            self.reject(lambda p: p.update(enabled_tasks=enabled))

    def test_all_28_dag_nodes_and_all_edge_references_are_validated(self):
        missing = deepcopy(self.dag)
        del missing['12c']
        with self.assertRaises(StateError):
            validate_plan(self.plan, missing)
        for kind in ('depends_on', 'integration_depends_on', 'paid_depends_on'):
            invalid = deepcopy(self.dag)
            invalid['00'][kind] = ['missing']
            with self.subTest(kind=kind), self.assertRaises(StateError):
                validate_plan(self.plan, invalid)
        invalid = deepcopy(self.dag)
        invalid['00']['depends_on'] = ['01']
        with self.assertRaises(StateError):
            validate_plan(self.plan, invalid)

    def test_edges_and_gate_partition_cannot_change_the_contract(self):
        for key in ('depends_on', 'integration_depends_on', 'paid_depends_on'):
            self.reject(lambda p: p['tasks']['06a'][key].append('12c'))
        self.reject(lambda p: p['tasks']['06a']['gates']['paid'].append('not-a-gate'))
        self.reject(lambda p: p['tasks']['06a']['gates']['paid'].clear())
        self.reject(lambda p: p['tasks']['06a']['gates']['development'].extend(
            p['tasks']['06a']['gates']['paid']))

    def test_explicit_branch_base_and_dependency_heads_are_required(self):
        for branch in ('main', 'feature/elsewhere', 'feature/workflow-../escape', 'feature/workflow-X'):
            self.reject(lambda p: p['tasks']['06a'].update(branch=branch))
        for ref in ('HEAD', 'main~1', 'refs/heads/main', '../main', 'main:other'):
            self.reject(lambda p: p['tasks']['06a']['base'].update(ref=ref))
        self.reject(lambda p: p['tasks']['06a']['base'].update(sha='abc'))
        self.reject(lambda p: p['tasks']['06a']['base'].update(confirmed=1))
        self.reject(lambda p: p['tasks']['06a']['base'].update(sha=None))
        self.reject(lambda p: p['tasks']['06a']['base']['dependency_heads'].clear())
        self.reject(lambda p: p['tasks']['06a']['base']['dependency_heads'].update({'12c': 'a' * 40}))

    def test_scope_cannot_cover_controls_frozen_contracts_or_old_product(self):
        for prefix in ('../outside/', '/absolute/', 'C:/outside/', 'web/', 'automation/',
                       'tools/overnight/', 'docs/web-alpha/architecture/contracts/',
                       'docs/web-alpha/overnight/', 'static/', 'templates/'):
            self.reject(lambda p: p['tasks']['06a']['scope'].update(add_prefixes=[prefix]))
        for path in ('AGENTS.md', 'nested/AGENTS.md', 'app.py', '.git/config',
                     'web/backend/migrations/0001.sql', 'requirements-dev.txt'):
            self.reject(lambda p: p['tasks']['06a']['scope'].update(modify_files=[path]))

    def test_model_effort_limits_and_scalar_types_fail_closed(self):
        self.reject(lambda p: p['tasks']['06a']['model'].update(name='other-provider'))
        self.reject(lambda p: p['tasks']['06a']['review_model'].update(reasoning='auto'))
        for field, value in (('max_repairs', 4), ('max_calls', True), ('wall_seconds', -1),
                             ('call_timeout_seconds', 1201)):
            self.reject(lambda p: p['tasks']['06a']['limits'].update({field: value}))
        self.reject(lambda p: p['limits'].update(extra_api_spend_usd=1))
        self.reject(lambda p: p['limits'].update(unknown=1))
        self.reject(lambda p: p['limits'].update(task_max_calls=p['limits']['batch_max_calls'] + 1))
        self.reject(lambda p: p['limits'].update(task_wall_seconds=p['limits']['batch_wall_seconds'] + 1))

    def test_known_codex_models_configurable_without_changing_default(self):
        for name in ('gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-6-astra', 'gpt-6-sol', 'gpt-6-luna'):
            plan = deepcopy(self.plan)
            plan['tasks']['06a']['model']['name'] = name
            plan['tasks']['06a']['review_model']['name'] = name
            self.assertEqual(validate_plan(plan, self.dag)['tasks']['06a']['model']['name'], name)
        self.assertEqual(self.plan['tasks']['06a']['model'], {'name': 'gpt-6.1-sol', 'reasoning': 'high'})
        self.reject(lambda p: p['tasks']['06a']['model'].update(name='provider/arbitrary'))

    def test_tests_require_trusted_control_entrypoint_and_known_placeholders(self):
        self.reject(lambda p: p['tasks']['06a'].update(tests=[p['tasks']['06a']['tests'][0]]))
        self.reject(lambda p: p['tasks']['06a']['tests'].append(deepcopy(p['tasks']['06a']['tests'][0])))
        for command in (['cmd', '/c', 'echo OK'], ['{python}', '-c', 'pass', '{control}/tools/fake.py'],
                        ['{python}', '{workspace}/tools/fake.py'],
                        ['{python}', '{control}/tools/../outside.py'],
                        ['{python}', '{control}/tools/fake.py', '{secret}']):
            self.reject(lambda p: p['tasks']['06a']['tests'][1].update(command=command))
        self.reject(lambda p: p['tasks']['06a']['tests'][1]['completion'].update(min_tests=True))
        self.reject(lambda p: p['tasks']['06a']['tests'][1]['completion'].update(min_tests=0))

    def test_audit_receipts_require_complete_identity_and_evidence(self):
        self.reject(lambda p: p['tasks']['06a']['audit'].update(evidence=[]))
        self.reject(lambda p: p['tasks']['06a']['audit'].update(tree_sha=None))
        self.reject(lambda p: p['tasks']['05a']['audit'].update(head_sha='a' * 40))
        self.reject(lambda p: p['baselines']['00'].update(status='PARTIAL'))
        self.reject(lambda p: p['baselines']['00'].update(approved=1))
        for path in ('../receipt.json', '/receipt.json', 'C:/receipt.json', 'docs\\receipt.json'):
            self.reject(lambda p: p['tasks']['06a']['audit']['evidence'][0].update(path=path))
        self.reject(lambda p: p['tasks']['06a']['audit']['evidence'][0].update(sha256='short'))

    def test_plan_hash_is_canonical_and_input_is_not_mutated(self):
        before = deepcopy(self.plan)
        result = validate_plan(self.plan, self.dag)
        self.assertEqual(self.plan, before)
        reversed_plan = dict(reversed(list(self.plan.items())))
        self.assertEqual(plan_hash(self.plan), plan_hash(reversed_plan))
        result['tasks']['06a']['goal'] = 'different goal'
        self.assertNotEqual(plan_hash(result), plan_hash(self.plan))
        with self.assertRaises(StateError):
            plan_hash({'bad': float('nan')})

    def repository(self):
        folder = ROOT / 'work/overnight/tests'
        folder.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix='task-config-', dir=folder)
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        def git(*args):
            return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE).decode().strip()
        git('init', '-q', '--initial-branch=main')
        git('config', 'user.name', 'Synthetic Config Test')
        git('config', 'user.email', 'synthetic@example.invalid')
        git('config', 'core.autocrlf', 'false')
        for name, body in ((DAG_PATH, (ROOT / DAG_PATH).read_text('utf-8')),
                           (EVIDENCE, '{"synthetic":true}\n'),
                           ('docs/web-alpha/evidence/baseline-manifest.json',
                            '{"tracked_files":{"archive/legacy.txt":"synthetic"}}'),
                           ('archive/legacy.txt', 'protected old product fixture'),
                           ('tools/overnight/asr_gate.py', '# synthetic trusted entrypoint\n'),
                           ('tools/overnight/workflow_gate.py', '# synthetic trusted entrypoint\n')):
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, 'utf-8', newline='\n')
        git('add', '--all'); git('commit', '-q', '-m', 'synthetic baseline')
        base, tree = git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')
        git('branch', 'integration/synthetic-base')
        plan = deepcopy(self.plan)
        evidence = [{'path': EVIDENCE, 'sha256': hashlib.sha256((repo / EVIDENCE).read_bytes()).hexdigest()}]
        for audit in [*plan['baselines'].values(), plan['tasks']['06a']['audit']]:
            audit.update(head_sha=base, tree_sha=tree, evidence=deepcopy(evidence))
        plan['tasks']['06a']['base'].update(ref='integration/synthetic-base', sha=base,
                                           dependency_heads={'01': base})
        plan['tasks']['05a']['base']['ref'] = 'integration/synthetic-base'
        (repo / 'automation').mkdir()
        (repo / 'automation/workflow.json').write_text(json.dumps(plan), 'utf-8')
        (repo / 'automation/workflow.schema.json').write_bytes((ROOT / 'automation/workflow.schema.json').read_bytes())
        git('add', '--all'); git('commit', '-q', '-m', 'synthetic pinned plan')
        return repo, git, plan

    def test_load_plan_uses_clean_exact_tracked_control_objects(self):
        repo, git, expected = self.repository()
        control = git('rev-parse', 'HEAD')
        plan, dag, config_sha = load_plan(repo, 'automation/workflow.json', control)
        self.assertEqual(plan, expected)
        self.assertEqual(len(dag), 28)
        self.assertEqual(config_sha, plan_hash(expected))
        validate_evidence(repo, plan['tasks']['06a']['audit']['evidence'], control)
        self.assertEqual(load_plan(repo, Path('automation/workflow.json'), control)[2], config_sha)
        self.assertEqual(git('status', '--porcelain'), '')

    def test_load_rejects_dirty_wrong_pin_and_outside_paths(self):
        repo, git, _ = self.repository()
        control = git('rev-parse', 'HEAD')
        with self.assertRaises(StateError):
            load_plan(repo, 'automation/workflow.json', git('rev-parse', 'HEAD^'))
        with self.assertRaises(StateError):
            load_plan(repo, '../other.json', control)
        (repo / 'automation/workflow.json').write_text('{}', 'utf-8')
        with self.assertRaises(StateError):
            load_plan(repo, 'automation/workflow.json', control)

    def test_detached_checkout_accepts_only_exact_offline_origin_base(self):
        repo, git, expected = self.repository()
        base = expected['tasks']['06a']['base']
        base['ref'] = 'main'
        (repo / 'automation/workflow.json').write_text(json.dumps(expected), 'utf-8')
        git('add', 'automation/workflow.json'); git('commit', '-q', '-m', 'synthetic main base plan')
        control = git('rev-parse', 'HEAD')
        git('checkout', '--detach', '-q', control)
        git('update-ref', 'refs/heads/main', base['sha'])
        git('update-ref', 'refs/remotes/origin/' + base['ref'], base['sha'])
        self.assertEqual(load_plan(repo, 'automation/workflow.json', control)[0], expected)
        git('branch', '-D', base['ref'])
        self.assertEqual(load_plan(repo, 'automation/workflow.json', control)[0], expected)
        git('update-ref', 'refs/remotes/origin/' + base['ref'], control)
        with self.assertRaises(StateError):
            load_plan(repo, 'automation/workflow.json', control)
        git('update-ref', 'refs/heads/' + base['ref'], base['sha'])
        with self.assertRaises(StateError):
            load_plan(repo, 'automation/workflow.json', control)
        git('update-ref', '-d', 'refs/remotes/origin/' + base['ref'])
        git('update-ref', '-d', 'refs/heads/' + base['ref'])
        with self.assertRaises(StateError):
            load_plan(repo, 'automation/workflow.json', control)

    def test_load_rejects_tampered_persisted_evidence(self):
        repo, git, _ = self.repository()
        (repo / EVIDENCE).write_text('{"synthetic":"changed"}', 'utf-8')
        git('add', EVIDENCE); git('commit', '-q', '-m', 'synthetic evidence drift')
        with self.assertRaises(StateError):
            load_plan(repo, 'automation/workflow.json', git('rev-parse', 'HEAD'))

    def test_evidence_requires_regular_tracked_blob_not_ignored_file_or_symlink(self):
        repo, git, _ = self.repository()
        ignored = repo / 'work/fake.json'
        ignored.parent.mkdir()
        ignored.write_text('not tracked', 'utf-8')
        digest = hashlib.sha256(ignored.read_bytes()).hexdigest()
        control = git('rev-parse', 'HEAD')
        with self.assertRaises(StateError):
            validate_evidence(repo, [{'path': 'work/fake.json', 'sha256': digest}], control)
        # Create a symlink-mode Git object without requiring Windows symlink
        # privileges or following a link on the filesystem.
        blob = git('hash-object', '-w', 'archive/legacy.txt')
        git('update-index', '--add', '--cacheinfo', '120000,' + blob + ',docs/linked-receipt.json')
        git('commit', '-q', '-m', 'synthetic symlink object')
        with self.assertRaises(StateError):
            validate_evidence(repo, [{'path': 'docs/linked-receipt.json', 'sha256': digest}], git('rev-parse', 'HEAD'))
        with self.assertRaises(StateError):
            validate_evidence(repo, [], git('rev-parse', 'HEAD^{tree}'))

    def test_load_rejects_moved_base_and_nonancestor_audit(self):
        for change in ('base', 'audit'):
            with self.subTest(change=change):
                repo, git, plan = self.repository()
                if change == 'base':
                    git('update-ref', 'refs/heads/integration/synthetic-base', git('rev-parse', 'HEAD'))
                else:
                    tree = git('rev-parse', 'HEAD^{tree}')
                    other = git('commit-tree', tree, '-m', 'synthetic unrelated history')
                    plan['tasks']['06a']['audit'].update(head_sha=other, tree_sha=tree)
                    (repo / 'automation/workflow.json').write_text(json.dumps(plan), 'utf-8')
                    git('add', 'automation/workflow.json'); git('commit', '-q', '-m', 'synthetic mismatched audit')
                with self.assertRaises(StateError):
                    load_plan(repo, 'automation/workflow.json', git('rev-parse', 'HEAD'))

    def test_load_rejects_missing_trusted_entrypoint_and_legacy_scope(self):
        for change in ('entrypoint', 'legacy'):
            with self.subTest(change=change):
                repo, git, plan = self.repository()
                if change == 'entrypoint':
                    git('rm', 'tools/overnight/asr_gate.py')
                else:
                    plan['tasks']['06a']['scope']['modify_files'] = ['archive/legacy.txt']
                    (repo / 'automation/workflow.json').write_text(json.dumps(plan), 'utf-8')
                    git('add', 'automation/workflow.json')
                git('commit', '-q', '-m', 'synthetic invalid input')
                with self.assertRaises(StateError):
                    load_plan(repo, 'automation/workflow.json', git('rev-parse', 'HEAD'))

    def test_load_rejects_duplicate_json_and_wrong_audit_tree(self):
        for change in ('duplicate', 'tree'):
            with self.subTest(change=change):
                repo, git, plan = self.repository()
                if change == 'duplicate':
                    raw = json.dumps(plan).replace('"version": 2', '"version": 2, "version": 2', 1)
                else:
                    plan['tasks']['06a']['audit']['tree_sha'] = '0' * 40
                    raw = json.dumps(plan)
                (repo / 'automation/workflow.json').write_text(raw, 'utf-8')
                git('add', 'automation/workflow.json'); git('commit', '-q', '-m', 'synthetic invalid plan')
                with self.assertRaises(StateError):
                    load_plan(repo, 'automation/workflow.json', git('rev-parse', 'HEAD'))


if __name__ == '__main__':
    unittest.main()
