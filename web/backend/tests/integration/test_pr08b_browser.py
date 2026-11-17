"""Actual PR-08b HTTP, sessions, PostgreSQL, encryption and browser recovery."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import threading
import time
from uuid import UUID, uuid4

import sqlalchemy as sa
import uvicorn
from fastapi.responses import HTMLResponse, Response

from classroom_web.application.deletion import DeletionService
from classroom_web.application.review_browser import ReviewBrowser
from classroom_web.application.sync_browser import SyncBrowser
from classroom_web.domain.data import OwnerContext
from classroom_web.entrypoints.http import create_app
from classroom_web.infrastructure.qa_repository import PgQaRepository
from classroom_web.infrastructure.review_repository import PgReviewRepository
from deletion_support import POLICY, SECRET_KEY, deploy, services
from test_auth_browser import _certificate
from test_authentication import PASSWORD, auth
from test_cache_keys import cache, cache_schema, pg
from test_pr08b_sync import build_partial_revocation_conflict


ROOT = Path(__file__).resolve().parents[4]


def test_actual_browser_offline_recovery_conflict_and_deletion(cache, deploy, tmp_path):
    output = Path(os.environ.get('PR08B_OUTPUT', tmp_path / 'browser-output'))
    output.mkdir(parents=True, exist_ok=True)
    dependency = Path(os.environ.get('PR05A_DEPENDENCY_ROOT', ROOT))
    node = shutil.which('node') or str(dependency / 'work/pr03b/vmware-broker/payload/node/node.exe')
    modules = dependency / 'web/frontend/node_modules'
    if not (output / 'sync.js').is_file():
        env = {**os.environ, 'PR08B_OUTPUT': str(output), 'PR08B_FRONTEND_MODULES': str(modules)}
        subprocess.run([node, str(ROOT / 'tools/pr08b/build.mjs')], env=env,
                       check=True, timeout=30, capture_output=True)

    database = cache.a.pg.db
    database.cursor_secret = b'pr08b-synthetic-event-cursor-secret-0001'
    with database.transaction(OwnerContext(cache.a.uid)) as tx:
        semester = tx.catalog.create_semester('PR08b synthetic semester')
        course = tx.catalog.create_course(semester.id, 'PR08b synthetic course')
        classroom = tx.catalog.create_class_session(course.id, 'PR08b synthetic classroom')
        note = PgReviewRepository(tx).create_note(dict(
            class_session_id=str(classroom.id), title='PR08b browser note',
            body_markdown='Initial synthetic content', draft_id=str(uuid4())), uuid4())
    partial = build_partial_revocation_conflict(cache.a.pg, OwnerContext(cache.a.uid), classroom)
    # The fixture's existing product policy uses a short synthetic window. This test
    # also exercises browser refresh and user-driven conflict resolution within it.
    with cache.a.pg.admin.begin() as connection:
        connection.execute(sa.text('UPDATE identity_control.cache_policy SET unlock_window_seconds=120'))
        # Model a known conflict receipt whose exact comparison has not been
        # backfilled. Preserve both Review branches and the two original receipts.
        connection.exec_driver_sql('ALTER TABLE sync_conflicts DISABLE TRIGGER sync_conflicts_immutable')
        partial['frozen_cloud_revision_id'] = connection.scalar(sa.text('''DELETE FROM sync_conflicts
            WHERE id=:id RETURNING server_content->>'id' '''), {'id': UUID(partial['conflict_id'])})
        connection.exec_driver_sql('ALTER TABLE sync_conflicts ENABLE TRIGGER sync_conflicts_immutable')
        assert partial['frozen_cloud_revision_id']

    contract = json.loads((ROOT / 'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf-8'))
    spec = importlib.util.spec_from_file_location('pr08b_review_contract', ROOT / 'tools/pr07b/contract_candidate.py')
    candidate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(candidate)
    contract = candidate.build(contract)
    review = ReviewBrowser(auth=cache.a.service, database=database, config={'enabled': True})
    sync = SyncBrowser(auth=cache.a.service, database=database, enabled=True)
    deletion = DeletionService(auth=cache.a.service, database=database, queue=deploy.queue,
        store=deploy.store, ledger=deploy.ledger, confirmation_secret=SECRET_KEY, policy=POLICY)
    deletion.reconcile()

    cert, key, pin = _certificate(tmp_path, 'pr08b-formal', uuid4().hex[:12])
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    origin = f'https://127.0.0.1:{port}'
    app = create_app(auth=cache.a.service, contract=contract, origin=origin,
                     cache=cache.service, review=review, sync=sync, deletion=deletion)
    app.add_api_route('/', lambda: HTMLResponse('<!doctype html><title>PR08b actual sync</title>'), methods=['GET'])
    app.add_api_route('/sync.js', lambda: Response((output / 'sync.js').read_bytes(), media_type='text/javascript'), methods=['GET'])
    # Synthetic test control on this owned loopback service. Product reads still
    # pass through the authenticated HTTP endpoints and their real PostgreSQL RLS.
    def revoke_partial_source():
        with database.transaction(OwnerContext(cache.a.uid)) as tx:
            PgQaRepository(tx).revoke_source(UUID(partial['revoked_source_id']))
        return {'revoked': True}
    app.add_api_route('/test/revoke-partial-source', revoke_partial_source, methods=['POST'])
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
        ssl_certfile=str(cert), ssl_keyfile=str(key), access_log=False, log_level='warning',
        ws='none', timeout_graceful_shutdown=3))
    thread = threading.Thread(target=server.run, daemon=True)
    process = None
    try:
        thread.start()
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        scenario = dict(note=note, semester_id=str(semester.id), course_id=str(course.id),
                        class_session_id=str(classroom.id), partial=partial)
        env = {**os.environ, 'PR08B_EMAIL': cache.a.email, 'PR08B_PASSWORD': PASSWORD,
               'PR08B_ORIGIN': origin, 'PR08B_PIN': pin, 'PR08B_PROFILE': str(tmp_path / 'profile'),
               'PR08B_PLAYWRIGHT_MODULE': str(modules / '@playwright/test/index.mjs'),
               'PR08B_SCENARIO': json.dumps(scenario)}
        stderr_path = output / 'formal-browser-stderr.log'
        with stderr_path.open('w', encoding='utf-8') as stderr:
            process = subprocess.Popen([node, str(ROOT / 'tools/pr08b/formal-browser.mjs')],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, env=env,
                text=True, encoding='utf-8', start_new_session=os.name != 'nt')
            ready = process.stdout.readline()
            assert ready, stderr_path.read_text(encoding='utf-8')
            device = json.loads(ready)['device_id']
            with cache.a.pg.admin.begin() as connection:
                connection.execute(sa.text('UPDATE devices SET trusted=true WHERE owner_id=:owner AND id=:device'),
                                   {'owner': cache.a.uid, 'device': device})
            stdout, _ = process.communicate('synthetic device ready\n', timeout=120)
        assert process.returncode == 0, stderr_path.read_text(encoding='utf-8')
        result = json.loads(stdout.strip().splitlines()[-1])
        assert result['actual_http_postgres'] and result['offline_refresh_recovery']
        assert result['lost_ack_idempotency'] and result['cas_conflict_preserved']
        assert result['explicit_resolution'] and result['actual_conflict_panel']
        assert result['recovered_conflict_from_original_frozen_request']
        assert result['deletion_blocks_replay']
        assert result['deletion_scope_outage_locks_and_retries']
        assert result['device_revocation_locks']
        assert result['partial_source_revocation_clears_view']
        assert result['partial_source_revocation_survives_reopen']
        (output / 'formal-browser.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    finally:
        if process is not None and process.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, check=False)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        server.should_exit = True
        try:
            if thread.ident is not None:
                thread.join(timeout=15)
                assert not thread.is_alive()
        finally:
            with cache.a.pg.admin.begin() as connection:
                connection.execute(sa.text("UPDATE tasks SET not_before=clock_timestamp()+interval '365 days' WHERE owner_id=:owner"),
                                   {'owner': cache.a.uid})
