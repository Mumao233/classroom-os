"""Strict operator-owned workflow plans, loaded from exact tracked Git objects.

The portable JSON Schema documents the shape; this stdlib validator additionally
enforces repository policy, real DAG equality, Git identity and evidence hashes.
No function in this module writes state, invokes a model, or approves a gate.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .budget import DEFAULT_LIMITS, _validate_limits
from .state import StateError, require_keys, require_text, strict_int, _sha
from . import validation


DAG_PATH = 'docs/web-alpha/architecture/contracts/pr-dependencies.json'
SCHEMA_PATH = 'automation/workflow.schema.json'
LEGACY_MANIFEST = 'docs/web-alpha/evidence/baseline-manifest.json'
TASK_IDS = frozenset('00 01 02a 02b 03a 03b 04a 04b 05a 05b 06a 06b 06c 07a 07b 08a 08b 08c 09a 09b 10a 10b 11a 11b 11c 12a 12b 12c'.split())
EDGES = ('depends_on', 'integration_depends_on', 'paid_depends_on')
PHASES = {'poc', 'implementation', 'integration', 'paid', 'activation'}
EFFORTS = {'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}
MODELS = {'gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-6-astra', 'gpt-6-sol', 'gpt-6-luna'}
PLACEHOLDERS = {'python', 'node', 'control', 'workspace', 'deps', 'nonce', 'head_sha', 'patch_sha256'}
AUDIT_FIELDS = {'status', 'head_sha', 'tree_sha', 'evidence', 'reason'}
TASK_FIELDS = {'goal', 'acceptance', 'review_criteria', 'phase', 'base', 'branch', 'title',
               *EDGES, 'gates', 'scope', 'model', 'review_model', 'limits', 'tests', 'audit'}
TOP_FIELDS = {'version', 'authorization_id', 'repository', 'author_login', 'dag_path',
              'enabled_tasks', 'limits', 'baselines', 'tasks'}


def plan_hash(plan):
    """SHA-256 of UTF-8 canonical JSON, with no self-referential control SHA."""
    if type(plan) is not dict:
        raise StateError('plan must be an object')
    try:
        body = json.dumps(plan, ensure_ascii=False, allow_nan=False, sort_keys=True,
                          separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError) as error:
        raise StateError('plan is not finite JSON') from error
    return hashlib.sha256(body).hexdigest()


def _strings(value, label, *, nonempty=False):
    if type(value) is not list or (nonempty and not value) or len(value) > 256:
        raise StateError('invalid ' + label)
    for item in value:
        require_text(item, label, 6000)
    if len(set(value)) != len(value):
        raise StateError('duplicate ' + label)
    return value


def _path(value):
    if type(value) is not str or not validation.safe_path(value):
        raise StateError('unsafe repository-relative path')
    return value


def _branch(value):
    require_text(value, 'base ref', 200)
    if value != 'main' and not re.fullmatch(r'(feature|integration)/[A-Za-z0-9][A-Za-z0-9._/-]*', value):
        raise StateError('base must be an explicit allowed branch')
    if any(part in ('', '.', '..') or '..' in part or part.endswith(('.', '.lock'))
           for part in value.split('/')):
        raise StateError('invalid base branch')


def _dag(dag):
    if type(dag) is not dict or set(dag) != TASK_IDS:
        raise StateError('the complete 28-task DAG is required')
    for task in dag.values():
        require_keys(task, {*EDGES, 'gates'})
        for key in (*EDGES, 'gates'):
            _strings(task[key], key)
        if any(set(task[key]) - TASK_IDS for key in EDGES):
            raise StateError('unknown dependency reference')
    visiting, visited = set(), set()
    def visit(task_id):
        if task_id in visiting:
            raise StateError('dependency cycle')
        if task_id in visited:
            return
        visiting.add(task_id)
        for key in EDGES:
            for dependency in dag[task_id][key]:
                visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)
    for task_id in dag:
        visit(task_id)


def _evidence_shape(evidence):
    if type(evidence) is not list or len(evidence) > 32:
        raise StateError('invalid evidence list')
    paths = set()
    for item in evidence:
        require_keys(item, {'path', 'sha256'})
        path = _path(item['path'])
        _sha(item['sha256'], 'evidence sha256', 64)
        if path.casefold() in paths:
            raise StateError('duplicate evidence path')
        paths.add(path.casefold())


def _audit(audit, *, baseline=False):
    require_keys(audit, AUDIT_FIELDS | ({'approved'} if baseline else set()))
    if audit['status'] not in {'COMPLETE', 'IMPLEMENTED_UNVERIFIED', 'PARTIAL', 'NOT_STARTED', 'BLOCKED'}:
        raise StateError('unknown audit status')
    require_text(audit['reason'], 'audit reason', 6000)
    _evidence_shape(audit['evidence'])
    for key in ('head_sha', 'tree_sha'):
        if audit[key] is not None:
            _sha(audit[key], key)
    if (audit['head_sha'] is None) != (audit['tree_sha'] is None):
        raise StateError('audit head and tree must be paired')
    if audit['status'] == 'COMPLETE' and (audit['head_sha'] is None or not audit['evidence']):
        raise StateError('complete audit needs source identity and evidence')
    if audit['status'] == 'NOT_STARTED' and (audit['head_sha'] is not None or audit['evidence']):
        raise StateError('not-started audit cannot claim implementation evidence')
    if baseline and (type(audit['approved']) is not bool or audit['status'] != 'COMPLETE'):
        raise StateError('baseline must have an explicit approval flag and complete audit')


def _entrypoint(test):
    command = test['command']
    if type(command) is not list or not 2 <= len(command) <= 128 or command[0] not in ('{python}', '{node}'):
        raise StateError('test command must use a pinned interpreter and script')
    for arg in command:
        require_text(arg, 'test argument', 4096)
        fields = re.findall(r'\{([^{}]*)\}', arg)
        if set(fields) - PLACEHOLDERS or any(c in re.sub(r'\{[^{}]*\}', '', arg) for c in '{}'):
            raise StateError('unknown or malformed command placeholder')
    index = 1
    while index < len(command) and command[index] in ('-I', '-B') and command[0] == '{python}':
        index += 1
    if index == len(command):
        raise StateError('test command lacks a script')
    match = re.fullmatch(r'\{(control|workspace)\}/([^{}]+)', command[index])
    if not match:
        raise StateError('test entrypoint must be an exact script, without shell or eval')
    owner, path = match.groups()
    _path(path)
    suffixes = ('.py',) if command[0] == '{python}' else ('.js', '.mjs', '.cjs')
    if not path.endswith(suffixes):
        raise StateError('test entrypoint does not match interpreter')
    if test['kind'] == 'trusted' and (owner != 'control' or not path.startswith('tools/')):
        raise StateError('trusted tests must use a pinned control/tools entrypoint')
    return owner, path


def _tests(tests, wall_seconds):
    if type(tests) is not list or not 1 <= len(tests) <= 32:
        raise StateError('tests must be a nonempty bounded list')
    ids, trusted = set(), False
    for test in tests:
        require_keys(test, {'id', 'kind', 'command', 'timeout_seconds', 'completion'})
        require_text(test['id'], 'test id', 80)
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,79}', test['id']) or test['id'] in ids:
            raise StateError('invalid or duplicate test id')
        ids.add(test['id'])
        if test['kind'] not in {'candidate', 'trusted'}:
            raise StateError('invalid test kind')
        trusted |= test['kind'] == 'trusted'
        strict_int(test['timeout_seconds'], 'test timeout', 1, min(wall_seconds, 3600))
        completion = test['completion']
        require_keys(completion, {'marker', 'stage', 'min_tests'})
        require_text(completion['marker'], 'completion marker', 128)
        require_text(completion['stage'], 'completion stage', 80)
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,79}', completion['stage']):
            raise StateError('invalid completion stage')
        strict_int(completion['min_tests'], 'minimum tests', 1, 100000)
        _entrypoint(test)
    if not trusted:
        raise StateError('at least one independent trusted check is required')


def validate_plan(plan, dag):
    """Validate trusted operator configuration; return a detached equal copy."""
    try:
        require_keys(plan, TOP_FIELDS)
        plan_hash(plan)
        strict_int(plan['version'], 'version', 2, 2)
        if plan['repository'] != 'Mumao233/classroom-os' or plan['author_login'] != 'Mumao233':
            raise StateError('repository or author exceeds authorization')
        if type(plan['authorization_id']) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,79}', plan['authorization_id']):
            raise StateError('invalid authorization identity')
        if plan['dag_path'] != DAG_PATH:
            raise StateError('the frozen repository DAG is required')
        _dag(dag)
        enabled = _strings(plan['enabled_tasks'], 'enabled task')
        require_keys(plan['limits'], set(), DEFAULT_LIMITS.keys())
        limits = dict(DEFAULT_LIMITS, **plan['limits'])
        _validate_limits(limits)
        for suffix in ('wall_seconds', 'max_calls', 'max_turns', 'max_input_tokens', 'max_output_tokens'):
            task_cap, batch_cap = limits['task_' + suffix], limits['batch_' + suffix]
            if task_cap is not None and batch_cap is not None and task_cap > batch_cap:
                raise StateError('task envelope exceeds batch limit')
        if type(plan['tasks']) is not dict or not plan['tasks'] or set(plan['tasks']) - TASK_IDS:
            raise StateError('invalid task specifications')
        if set(enabled) - plan['tasks'].keys():
            raise StateError('enabled task has no authorized specification')
        if type(plan['baselines']) is not dict or set(plan['baselines']) - TASK_IDS or set(plan['baselines']) & set(plan['tasks']):
            raise StateError('invalid or overlapping baselines')
        for baseline in plan['baselines'].values():
            _audit(baseline, baseline=True)
        branches = set()
        for task_id, task in plan['tasks'].items():
            require_keys(task, TASK_FIELDS)
            for field in ('goal', 'title'):
                require_text(task[field], field, 6000)
            for field in ('acceptance', 'review_criteria'):
                _strings(task[field], field, nonempty=True)
            if task['phase'] not in PHASES:
                raise StateError('invalid task phase')
            for edge in EDGES:
                if task[edge] != dag[task_id][edge]:
                    raise StateError('task changes frozen dependencies: ' + task_id)
            require_keys(task['gates'], {'development', 'enablement', 'paid'})
            gates = []
            for gate_list in task['gates'].values():
                gates.extend(_strings(gate_list, 'gate partition'))
            if len(gates) != len(set(gates)) or set(gates) != set(dag[task_id]['gates']):
                raise StateError('gates must partition the exact frozen gate list')
            require_keys(task['base'], {'ref', 'sha', 'confirmed', 'dependency_heads'})
            base = task['base']
            _branch(base['ref'])
            if type(base['confirmed']) is not bool or (base['confirmed'] and base['sha'] is None):
                raise StateError('confirmed base requires an explicit SHA')
            if base['sha'] is not None:
                _sha(base['sha'], 'base sha')
            needed = set(task['depends_on'])
            if task['phase'] in {'integration', 'paid', 'activation'}:
                needed.update(task['integration_depends_on'])
            if task['phase'] in {'paid', 'activation'}:
                needed.update(task['paid_depends_on'])
            if (type(base['dependency_heads']) is not dict or set(base['dependency_heads']) - needed
                    or base['confirmed'] and set(base['dependency_heads']) != needed):
                raise StateError('base dependency heads do not match its phase')
            for head in base['dependency_heads'].values():
                _sha(head, 'dependency head')
            if (type(task['branch']) is not str or not re.fullmatch(r'feature/workflow-[a-z0-9][a-z0-9-]{0,99}', task['branch'])
                    or task['branch'] == base['ref'] or task['branch'] in branches):
                raise StateError('task destination must be a distinct explicit workflow branch')
            branches.add(task['branch'])
            validation.validate_scope(task['scope'])
            for field in ('model', 'review_model'):
                require_keys(task[field], {'name', 'reasoning'})
                if task[field]['name'] not in MODELS or task[field]['reasoning'] not in EFFORTS:
                    raise StateError('unsupported pinned model or effort')
            task_limits = task['limits']
            require_keys(task_limits, {'wall_seconds', 'max_calls', 'max_repairs', 'call_timeout_seconds'})
            strict_int(task_limits['wall_seconds'], 'task wall time', 1, min(limits['task_wall_seconds'], limits['batch_wall_seconds']))
            strict_int(task_limits['max_calls'], 'task calls', 1, min(limits['task_max_calls'], limits['batch_max_calls']))
            strict_int(task_limits['max_repairs'], 'task repairs', 0, limits['max_repairs'])
            strict_int(task_limits['call_timeout_seconds'], 'call timeout', 1, min(1200, task_limits['wall_seconds']))
            _tests(task['tests'], task_limits['wall_seconds'])
            _audit(task['audit'])
    except (TypeError, KeyError, ValueError) as error:
        if isinstance(error, StateError):
            raise
        raise StateError('invalid workflow plan: ' + str(error)) from error
    return deepcopy(plan)


def _git(source, *args):
    try:
        return validation.git(Path(source), *args)
    except validation.ValidationError as error:
        raise StateError(str(error)) from error


def _blob(source, path, control_sha):
    _path(path)
    entries = _git(source, 'ls-tree', '-z', control_sha, '--', path).split(b'\0')
    entries = [entry for entry in entries if entry]
    if len(entries) != 1:
        raise StateError('required tracked object is missing: ' + path)
    header, name = entries[0].split(b'\t', 1)
    mode, kind, oid = header.split()
    if name.decode('utf-8') != path or mode not in (b'100644', b'100755') or kind != b'blob':
        raise StateError('evidence/configuration must be a regular tracked file')
    body = _git(source, 'cat-file', 'blob', oid.decode())
    if len(body) > 2_000_000:
        raise StateError('configuration/evidence object exceeds bound')
    return body


def _json(body):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise StateError('duplicate JSON field')
            result[key] = value
        return result
    def constant(_):
        raise StateError('nonfinite JSON number')
    try:
        return json.loads(body.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise StateError('invalid UTF-8 JSON') from error


def validate_evidence(source, evidence, control_sha):
    """Verify [{path, sha256}] against regular Git blobs at control_sha.

    Paths are repository-relative, never workspace/ignored output paths. This
    read-only helper does not require a clean working tree; load_plan does.
    """
    _sha(control_sha, 'control sha')
    if _git(source, 'cat-file', '-t', control_sha).strip() != b'commit':
        raise StateError('control SHA must identify a commit')
    _evidence_shape(evidence)
    for item in evidence:
        if hashlib.sha256(_blob(source, item['path'], control_sha)).hexdigest() != item['sha256']:
            raise StateError('evidence hash mismatch: ' + item['path'])


def _base_head(source, ref):
    """Resolve local or origin-tracking branch offline; disagreement is unsafe."""
    names = {'refs/heads/' + ref, 'refs/remotes/origin/' + ref}
    raw = _git(source, 'for-each-ref', '--format=%(refname) %(objectname)', *sorted(names))
    heads = {line.split()[1] for line in raw.decode('utf-8').splitlines()
             if line.split()[0] in names}
    if len(heads) != 1:
        raise StateError('configured base branch is missing or local/origin refs disagree')
    return heads.pop()


def load_plan(source, path, control_sha):
    """Read a clean pinned control commit; return (plan, full_dag, plan_hash)."""
    source = Path(source).resolve(strict=True)
    _sha(control_sha, 'control sha')
    if Path(_git(source, 'rev-parse', '--show-toplevel').decode().strip()).resolve() != source:
        raise StateError('source must be the repository root')
    if _git(source, 'rev-parse', 'HEAD').decode().strip() != control_sha:
        raise StateError('source HEAD differs from required control SHA')
    if _git(source, 'status', '--porcelain=v1', '--untracked-files=all').strip():
        raise StateError('control source must be clean')
    candidate_path = Path(path)
    if candidate_path.is_absolute():
        try:
            relative = candidate_path.resolve().relative_to(source).as_posix()
        except ValueError as error:
            raise StateError('plan path is outside source') from error
    else:
        relative = candidate_path.as_posix()
    plan = _json(_blob(source, relative, control_sha))
    schema = _json(_blob(source, SCHEMA_PATH, control_sha))
    if (type(schema) is not dict or schema.get('$schema') != 'https://json-schema.org/draft/2020-12/schema'
            or type(schema.get('properties')) is not dict or type(schema['properties'].get('version')) is not dict
            or schema['properties']['version'].get('const') != 2):
        raise StateError('unexpected workflow schema version')
    document = _json(_blob(source, DAG_PATH, control_sha))
    require_keys(document, {'version', 'status', 'prs'})
    require_text(document['status'], 'DAG status')
    if document['version'] != '1.0.0' or type(document['prs']) is not list:
        raise StateError('unexpected frozen DAG version or shape')
    dag = {}
    for item in document['prs']:
        require_keys(item, {'id', 'name', 'owner', 'depends_on'}, {'integration_depends_on', 'paid_depends_on', 'gates'})
        for field in ('id', 'name', 'owner'):
            require_text(item[field], 'DAG ' + field)
        if item['id'] in dag:
            raise StateError('duplicate DAG task')
        dag[item['id']] = {key: item.get(key, []) for key in (*EDGES, 'gates')}
    plan = validate_plan(plan, dag)
    for audit in [*plan['baselines'].values(), *(task['audit'] for task in plan['tasks'].values())]:
        validate_evidence(source, audit['evidence'], control_sha)
        if audit['head_sha'] is not None:
            if (_git(source, 'cat-file', '-t', audit['head_sha']).strip() != b'commit'
                    or _git(source, 'rev-parse', audit['head_sha'] + '^{tree}').decode().strip() != audit['tree_sha']):
                raise StateError('audit source head/tree mismatch')
            _git(source, 'merge-base', '--is-ancestor', audit['head_sha'], control_sha)
    legacy_document = _json(_blob(source, LEGACY_MANIFEST, control_sha))
    legacy = legacy_document.get('tracked_files') if type(legacy_document) is dict else None
    if type(legacy) is not dict:
        raise StateError('protected local-product manifest is unavailable')
    for task in plan['tasks'].values():
        base = task['base']
        if base['sha'] is not None:
            if _base_head(source, base['ref']) != base['sha']:
                raise StateError('configured base branch moved')
            _git(source, 'merge-base', '--is-ancestor', base['sha'], control_sha)
            for head in base['dependency_heads'].values():
                _git(source, 'merge-base', '--is-ancestor', head, base['sha'])
            if task['audit']['head_sha'] is not None:
                _git(source, 'merge-base', '--is-ancestor', task['audit']['head_sha'], base['sha'])
        scope = task['scope']
        modifications = {p.casefold() for p in scope['modify_files'] + scope['delete_files']}
        additions = [p.casefold() for p in scope['add_prefixes']]
        for old_path in legacy:
            if (old_path.casefold() in modifications
                    or any(old_path.casefold().startswith(prefix) for prefix in additions)):
                raise StateError('scope overlaps protected local-product manifest')
        for test in task['tests']:
            owner, entrypoint = _entrypoint(test)
            if owner == 'control':
                _blob(source, entrypoint, control_sha)
    return plan, dag, plan_hash(plan)
