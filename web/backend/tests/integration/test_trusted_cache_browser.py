"""PR-08a actual AuthClient, public contract, PostgreSQL and native browser storage."""
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from uuid import uuid4

import sqlalchemy as sa
import uvicorn
from fastapi.responses import HTMLResponse, Response

from classroom_web.entrypoints.http import create_app
from test_auth_browser import _certificate
from test_authentication import auth, PASSWORD
from test_cache_keys import cache, cache_schema, pg


ROOT = Path(__file__).resolve().parents[4]


def test_formal_authclient_encrypted_cache_and_revocation(cache, tmp_path):
    output = Path(os.environ['PR08A_OUTPUT'])
    dependency = Path(os.environ['PR05A_DEPENDENCY_ROOT'])
    node = dependency / 'work/pr03b/vmware-broker/payload/node/node.exe'
    module = dependency / 'web/frontend/node_modules/@playwright/test/index.mjs'
    contract = json.loads((ROOT / 'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf-8'))
    assert contract['info']['version'] == '0.2.1'
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
           'PR08A_PIN': pin, 'PR08A_PROFILE': str(tmp_path / 'profile'), 'PR08A_PLAYWRIGHT_MODULE': str(module)}
    process = None
    try:
        with (output / 'formal-browser-stderr.log').open('w', encoding='utf-8') as stderr:
            process = subprocess.Popen([str(node), str(ROOT / 'tools/pr08a/formal-browser.mjs')],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, env=env, text=True, encoding='utf-8')
            ready = process.stdout.readline()
            assert ready, (output / 'formal-browser-stderr.log').read_text(encoding='utf-8')
            device = json.loads(ready)['device_id']
            with cache.a.pg.admin.begin() as connection:
                connection.execute(sa.text('UPDATE devices SET trusted=true WHERE owner_id=:owner AND id=:device'),
                                   {'owner': cache.a.uid, 'device': device})
            stdout, _ = process.communicate('synthetic trusted device enabled\n', timeout=45)
        assert process.returncode == 0, (output / 'formal-browser-stderr.log').read_text(encoding='utf-8')
        result = json.loads(stdout.strip().splitlines()[-1])
        assert result['actual_authclient_login_reauth_unlock'] and result['actual_device_revocation']
        (output / 'formal-browser.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    finally:
        if process is not None and process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, check=False)
            process.wait(timeout=10)
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive()
