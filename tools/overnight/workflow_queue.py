"""Consume one manual, data-only v2 request for a trusted pinned workflow.

The queue performs bounded GitHub GETs, never dispatches work remotely and has
no polling daemon. Without --execute it only validates; a request is not a new
authorization and cannot reset the workflow state or subscription ledger.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import io
import json
from pathlib import Path
import re
import shutil
import stat
import zipfile
import zlib

from .publish import GitHubClient, REPO_API
from .state import StateError, atomic_json, process_lock, read_json, require_keys, _sha
from .task_config import load_plan, plan_hash


PLAN_PATH = 'automation/workflow.json'
WORKFLOW_PATH = '.github/workflows/overnight-queue.yml'
REQUEST_FIELDS = {'version', 'repository', 'control_sha', 'request_id',
                  'plan_path', 'config_sha', 'authorization_id'}
IDENTITY_FIELDS = REQUEST_FIELDS - {'request_id'}
MAX_ARCHIVE = 32768
MAX_REQUEST = 4096


def validate_request(request, plan, control_sha):
    """Validate only against a plan already returned by trusted load_plan."""
    require_keys(request, REQUEST_FIELDS)
    _sha(control_sha, 'control sha')
    if type(request['version']) is not int or request['version'] != 2:
        raise StateError('unsupported queue request version')
    if (type(request['request_id']) is not str
            or not re.fullmatch(r'[1-9][0-9]{0,29}', request['request_id'])):
        raise StateError('invalid GitHub run request identity')
    if (request['repository'] != 'Mumao233/classroom-os'
            or request['repository'] != plan['repository']
            or request['control_sha'] != control_sha
            or request['plan_path'] != PLAN_PATH
            or request['config_sha'] != plan_hash(plan)
            or request['authorization_id'] != plan['authorization_id']):
        raise StateError('request differs from trusted workflow identity')
    return deepcopy(request)


def decode_request(data, plan, control_sha):
    if type(data) is not bytes or len(data) > MAX_ARCHIVE:
        raise StateError('request artifact exceeds limit or is not bytes')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) != 1:
                raise StateError('unexpected queue artifact contents')
            item = members[0]
            kind = stat.S_IFMT(item.external_attr >> 16)
            if (item.filename != 'request.json' or item.file_size > MAX_REQUEST
                    or item.is_dir() or item.flag_bits & 1
                    or kind not in (0, stat.S_IFREG)
                    or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                raise StateError('unexpected queue artifact contents')
            with archive.open(item) as handle:
                raw = handle.read(MAX_REQUEST + 1)
            if len(raw) > MAX_REQUEST:
                raise StateError('request JSON exceeds limit')
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise StateError('duplicate request fields')
                value[key] = item
            return value
        def invalid_constant(_):
            raise StateError('nonfinite request number')
        request = json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                             parse_constant=invalid_constant)
    except (zipfile.BadZipFile, UnicodeError, json.JSONDecodeError, RuntimeError, OSError,
            EOFError, zlib.error) as error:
        raise StateError('invalid request artifact') from error
    return validate_request(request, plan, control_sha)


def find_request(client, plan, control_sha):
    runs = client.get(REPO_API + '/actions/workflows/overnight-queue.yml/runs?status=success&per_page=30')
    if type(runs) is not dict or type(runs.get('workflow_runs')) is not list:
        raise StateError('invalid workflow run metadata')
    for run in runs['workflow_runs']:
        if type(run) is not dict:
            raise StateError('invalid workflow run metadata')
        if (run.get('head_sha') != control_sha or run.get('event') != 'workflow_dispatch'
                or run.get('path') != WORKFLOW_PATH
                or run.get('status') != 'completed' or run.get('conclusion') != 'success'
                or type(run.get('actor')) is not dict or run['actor'].get('login') != plan['author_login']
                or type(run.get('triggering_actor')) is not dict
                or run['triggering_actor'].get('login') != plan['author_login']
                or type(run.get('repository')) is not dict
                or run['repository'].get('full_name') != plan['repository']
                or type(run.get('id')) is not int or run['id'] <= 0):
            continue
        artifacts = client.get(REPO_API + '/actions/runs/' + str(run['id']) + '/artifacts')
        if type(artifacts) is not dict or type(artifacts.get('artifacts')) is not list:
            raise StateError('invalid artifact metadata')
        matches = [item for item in artifacts['artifacts'] if type(item) is dict
                   and item.get('name') == 'overnight-request' and item.get('expired') is False]
        if (len(matches) != 1 or type(matches[0].get('id')) is not int
                or matches[0]['id'] <= 0):
            raise StateError('missing or ambiguous request artifact')
        data = client.get_bytes(REPO_API + '/actions/artifacts/' + str(matches[0]['id']) + '/zip',
                                max_bytes=MAX_ARCHIVE)
        request = decode_request(data, plan, control_sha)
        if request['request_id'] != str(run['id']):
            raise StateError('request/run identity mismatch')
        return request
    return None


def bind_request(source, request, plan, control_sha, *, persist=True):
    """Keep one immutable authorization identity; append same-plan run IDs only.

    Validation alone never creates files. Execution records the request before
    constructing the adapter, and every run uses the authorization's existing
    Workflow directory. No state or subscription file is created or reset here.
    """
    request = validate_request(request, plan, control_sha)
    identity = {key: request[key] for key in IDENTITY_FIELDS}
    folder = Path(source).resolve() / 'work/overnight/workflow-requests'
    destination = folder / (plan['authorization_id'] + '.json')
    def check():
        record = read_json(destination) if destination.exists() else dict(identity, requests={})
        require_keys(record, IDENTITY_FIELDS | {'requests'})
        if type(record['version']) is not int or any(record[key] != value for key, value in identity.items()):
            raise StateError('authorization request identity changed; existing ledger must not reset')
        if type(record['requests']) is not dict:
            raise StateError('invalid persisted request map')
        for request_id, digest in record['requests'].items():
            if not re.fullmatch(r'[1-9][0-9]{0,29}', request_id):
                raise StateError('invalid persisted request identity')
            _sha(digest, 'request hash', 64)
            expected = plan_hash(dict(identity, request_id=request_id))
            if digest != expected:
                raise StateError('persisted request content changed')
        digest = plan_hash(request)
        previous = record['requests'].get(request['request_id'])
        if previous is not None and previous != digest:
            raise StateError('same request ID has different content')
        record['requests'][request['request_id']] = digest
        return record
    if persist:
        folder.mkdir(parents=True, exist_ok=True)
        with process_lock(folder / (plan['authorization_id'] + '.lock'), timeout_seconds=1):
            record = check()
            if not destination.exists() or read_json(destination) != record:
                atomic_json(destination, record)
    else:
        record = check()
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--control-sha', required=True)
    parser.add_argument('--deps', type=Path)
    parser.add_argument('--approvals', type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--publish', action='store_true', help='Allow validated personal-account Draft PRs only')
    parser.add_argument('--codex', default=shutil.which('codex'))
    args = parser.parse_args(argv)
    source = args.source.resolve(strict=True)
    if source != Path(__file__).resolve().parents[2]:
        raise StateError('source must be the repository containing the running queue module')
    if args.publish and not args.execute:
        raise StateError('--publish requires explicit --execute')
    if args.execute and args.deps is None:
        raise StateError('--execute requires --deps')
    # This must precede all untrusted request parsing and any credential lookup.
    plan, dag, config_sha = load_plan(source, PLAN_PATH, args.control_sha)
    client = GitHubClient.from_git_credentials(source)
    request = find_request(client, plan, args.control_sha)
    if request is None:
        print(json.dumps({'status': 'no_matching_pinned_request', 'model_calls': 0}))
        return 0
    bind_request(source, request, plan, args.control_sha, persist=args.execute)
    if not args.execute:
        print(json.dumps({'status': 'validated_not_executed', 'request_id': request['request_id'],
                          'authorization_id': plan['authorization_id'], 'control_sha': args.control_sha,
                          'config_sha': config_sha, 'model_calls': 0}))
        return 0
    from .workflow import Workflow
    from .workflow_adapter import ProductionAdapter
    root = source / 'work/overnight/workflows' / plan['authorization_id']
    adapter = ProductionAdapter(source, args.control_sha, root, args.deps, args.codex, args.publish)
    report = Workflow(source, args.control_sha, plan, dag, adapter, approvals=args.approvals,
                      publish=args.publish).run()
    print(json.dumps({'status': report['status'], 'request_id': request['request_id'],
                      'report': str(root / 'report.json')}))
    if report['status'] == 'GLOBAL_STOP':
        return 1
    return 0 if all(report['tasks'][key]['effective_state'] in {'SKIPPED', 'READY_FOR_HUMAN_REVIEW'}
                    for key in plan['enabled_tasks']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
