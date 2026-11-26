"""PR-08a actual AuthClient, public contract, PostgreSQL and native browser storage."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import threading
import time
from unittest.mock import patch
from uuid import uuid4

import pytest
import sqlalchemy as sa
import uvicorn
from fastapi.responses import HTMLResponse, Response

from classroom_web.entrypoints.http import create_app
from test_auth_browser import _certificate
from test_authentication import auth, PASSWORD, post
from test_cache_keys import cache, cache_schema, candidate_contract, pg, request_body


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def cache_contract_base():
    return json.loads((ROOT / 'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('already_has_unlock', [True, False])
def test_cache_proofs_follow_device_contract_after_reordering(cache_contract_base, already_has_unlock):
    branches = cache_contract_base['components']['schemas']['ReauthOperation']['oneOf']
    branches.sort(key=lambda branch: branch['properties']['purpose']['const'] != 'student_recovery_status')
    if not already_has_unlock:
        branches[:] = [branch for branch in branches if branch['properties']['purpose']['const'] != 'unlockCache']
    original = deepcopy(branches)
    device = next(branch for branch in original if branch['properties']['purpose']['const'] == 'revokeDevice')
    contract = candidate_contract(cache_contract_base)
    schemas = contract['components']['schemas']
    composed = schemas['ReauthOperation']['oneOf']
    assert branches == original
    cache_device_id = {'$ref': '#/components/schemas/CacheDeviceId'}
    cache_key = {'$ref': '#/components/schemas/CanonicalUuidV4'}
    assert contract['components']['parameters']['CacheIdempotencyKey']['schema'] == cache_key
    for purpose, body, path in [('unlockCache', 'CacheUnlockRequest', '/me/cache-unlock'),
                                ('recoverCache', 'CacheRecoverRequest', '/me/cache-recover')]:
        assert {'$ref': '#/components/parameters/CacheIdempotencyKey'} in contract['paths'][path]['post']['parameters']
        assert schemas[body]['properties']['device_id'] == cache_device_id
        expected = deepcopy(device)
        expected['properties']['purpose']['const'] = purpose
        expected['properties']['target']['properties']['id'] = cache_device_id
        expected['properties']['candidate_parameters']['properties']['idempotency_key'] = cache_key
        expected['properties']['candidate_parameters']['properties']['body'] = {'$ref': '#/components/schemas/' + body}
        assert [branch for branch in composed if branch['properties']['purpose']['const'] == purpose] == [expected]


@pytest.mark.parametrize('defect', ['duplicate', 'wrong_target', 'wrong_key', 'wrong_header', 'wrong_body'])
def test_cache_overlay_rejects_conflicting_public_unlock(cache_contract_base, defect):
    branches = cache_contract_base['components']['schemas']['ReauthOperation']['oneOf']
    unlock = next(branch for branch in branches if branch['properties']['purpose']['const'] == 'unlockCache')
    # The existing unlock is deliberately first, so a self-comparison could hide corrupt public inputs.
    branches.remove(unlock)
    branches.insert(0, unlock)
    if defect == 'duplicate':
        branches.append(deepcopy(unlock))
    elif defect == 'wrong_target':
        unlock['properties']['target']['properties']['id'] = {'type': 'integer'}
    elif defect == 'wrong_key':
        unlock['properties']['candidate_parameters']['properties']['idempotency_key'] = {
            '$ref': '#/components/schemas/CanonicalIdempotencyKey'}
    elif defect == 'wrong_header':
        headers = cache_contract_base['paths']['/me/cache-unlock']['post']['parameters']
        header = next(item for item in headers if item['$ref'].endswith('/CacheIdempotencyKey'))
        header['$ref'] = '#/components/parameters/IdempotencyKey'
    else:
        unlock['properties']['candidate_parameters']['properties']['body'] = {'$ref': '#/components/schemas/Empty'}
    with pytest.raises(ValueError, match='unlockCache'):
        candidate_contract(cache_contract_base)


@pytest.mark.parametrize('recovery', [False, True], ids=['unlock', 'recover'])
def test_cache_v7_write_key_is_rejected_before_reauth_or_release(cache, recovery):
    body = request_body(cache)
    if recovery:
        body['key_id'] = str(uuid4())
    purpose = 'recoverCache' if recovery else 'unlockCache'
    path = '/me/cache-recover' if recovery else '/me/cache-unlock'
    key = 'abcdabcd-abcd-7def-8abc-abcdefabcdef'
    csrf = cache.session['csrf_token']
    operation = dict(purpose=purpose, target=dict(kind='device', id=body['device_id']),
                     candidate_parameters=dict(idempotency_key=key, body=body))
    with patch.object(cache.a.service, 'reauthenticate', wraps=cache.a.service.reauthenticate) as issue:
        response = post(cache.a, '/auth/reauth', dict(password=PASSWORD, operation=operation), csrf)
        assert response.status_code == 422 and response.json()['error']['code'] == 'VALIDATION_FAILED'
        issue.assert_not_called()
    with patch.object(cache.service, 'release', wraps=cache.service.release) as release:
        response = post(cache.a, path, body, csrf, **{'Idempotency-Key': key})
        assert response.status_code == 422 and response.json()['error']['code'] == 'VALIDATION_FAILED'
        release.assert_not_called()


def test_formal_authclient_encrypted_cache_and_revocation(cache, tmp_path):
    output = Path(os.environ.get('PR08A_OUTPUT', tmp_path / 'browser-output'))
    dependency = Path(os.environ.get('PR05A_DEPENDENCY_ROOT', ROOT))
    node = shutil.which('node') or str(dependency / 'work/pr03b/vmware-broker/payload/node/node.exe')
    module = dependency / 'web/frontend/node_modules/@playwright/test/index.mjs'
    if not (output / 'cache.js').is_file():
        bundle_env = {**os.environ, 'PR08A_OUTPUT': str(output), 'PR08A_FRONTEND_MODULES': str(dependency / 'web/frontend/node_modules')}
        subprocess.run([node, str(ROOT / 'tools/pr08a/build.mjs')], env=bundle_env, check=True, timeout=30, capture_output=True)
    contract = json.loads((ROOT / 'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf-8'))
    assert contract['info']['version'] == '0.4.0'
    cert, key, pin = _certificate(tmp_path, 'pr08a-formal', uuid4().hex[:12])
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    origin = f'https://127.0.0.1:{port}'
    app = create_app(auth=cache.a.service, contract=contract, origin=origin, cache=cache.service)
    app.add_api_route('/', lambda: HTMLResponse('<!doctype html><title>PR08a actual AuthClient</title>'), methods=['GET'])
    app.add_api_route('/cache.js', lambda: Response((output / 'cache.js').read_bytes(), media_type='text/javascript'), methods=['GET'])
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
        ssl_certfile=str(cert), ssl_keyfile=str(key), access_log=False, log_level='warning', ws='none'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    env = {**os.environ, 'PR08A_EMAIL': cache.a.email, 'PR08A_PASSWORD': PASSWORD, 'PR08A_ORIGIN': origin,
           'PR08A_PIN': pin, 'PR08A_PROFILE': str(tmp_path / 'profile'), 'PR08A_PLAYWRIGHT_MODULE': str(module),
           'PR08A_CONTRACT_VERSION': contract['info']['version']}
    process = None
    try:
        with (output / 'formal-browser-stderr.log').open('w', encoding='utf-8') as stderr:
            process = subprocess.Popen([str(node), str(ROOT / 'tools/pr08a/formal-browser.mjs')],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, env=env, text=True, encoding='utf-8', start_new_session=os.name != 'nt')
            ready = process.stdout.readline()
            assert ready, (output / 'formal-browser-stderr.log').read_text(encoding='utf-8')
            device = json.loads(ready)['device_id']
            with cache.a.pg.admin.begin() as connection:
                connection.execute(sa.text('UPDATE devices SET trusted=true WHERE owner_id=:owner AND id=:device'),
                                   {'owner': cache.a.uid, 'device': device})
            stdout, _ = process.communicate('synthetic trusted device enabled\n', timeout=45)
        assert process.returncode == 0, (output / 'formal-browser-stderr.log').read_text(encoding='utf-8')
        result = json.loads(stdout.strip().splitlines()[-1])
        assert result['public_contract'] == contract['info']['version']
        assert result['actual_authclient_login_reauth_unlock'] and result['actual_device_revocation']
        (output / 'formal-browser.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    finally:
        if process is not None and process.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, check=False)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive()
