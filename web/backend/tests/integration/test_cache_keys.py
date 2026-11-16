"""PR-05a incremental real identity/PG tests. Candidate policy is synthetic-only."""

import base64
import json
import secrets
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from classroom_web.application.cache_keys import CacheKeyService
from classroom_web.domain.data import OwnerContext, Resource
from classroom_web.entrypoints.http import create_app
from classroom_web.infrastructure.cache_key_crypto import CacheKeyCrypto
from classroom_web.infrastructure.cache_key_repository import PgCacheKeyRepository
from test_authentication import auth, login, post, PASSWORD, BASE, install_factor

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools/pr05a/formal"))
from candidate import candidate_contract, candidate_sql


@pytest.fixture(scope="module")
def cache_schema(pg):
    with pg.admin.begin() as connection:
        connection.exec_driver_sql(candidate_sql(ROOT), execution_options={"no_parameters": True})
    yield


@pytest.fixture
def cache(auth, cache_schema):
    a = auth
    with a.pg.admin.begin() as connection:
        connection.execute(sa.text("""
            UPDATE identity_control.cache_policy SET enabled=true,history_enabled=true,
              policy_revision='synthetic-candidate-only',unlock_window_seconds=30,reauth_window_seconds=120
        """))
    repository = PgCacheKeyRepository(a.pg.db)
    crypto = CacheKeyCrypto({"fixture-v1": secrets.token_bytes(32)}, active_version="fixture-v1")
    service = CacheKeyService(auth=a.service, repository=repository, crypto=crypto,
                              enabled=True, allow_recovery=True)
    contract = candidate_contract(json.loads(
        (ROOT / "docs/web-alpha/architecture/contracts/openapi.json").read_text(encoding="utf-8")))
    app = create_app(auth=a.service, contract=contract, origin="https://testserver", cache=service,
                     websocket_admission=a.ws_admission)
    with TestClient(app, base_url="https://testserver") as client:
        a.client = client
        session = login(a)
        with a.pg.admin.begin() as connection:
            connection.execute(sa.text("UPDATE devices SET trusted=true WHERE owner_id=:owner AND id=:device"),
                               {"owner": a.uid, "device": session["device_id"]})
        yield SimpleNamespace(a=a, service=service, repository=repository, crypto=crypto,
                              contract=contract, session=session)
    with a.pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE identity_control.cache_policy SET enabled=false,history_enabled=false"))


def request_body(cache):
    return dict(device_id=cache.session["device_id"], key_epoch=1, scope="drafts", class_session_id=None)


def reauthorized(cache, body, *, recovery=False, mfa_code=None):
    purpose = "recoverCache" if recovery else "unlockCache"
    key = str(uuid4())
    operation = dict(purpose=purpose, target=dict(kind="device", id=body["device_id"]),
                     candidate_parameters=dict(idempotency_key=key, body=body))
    parameters = dict(password=PASSWORD, operation=operation)
    if mfa_code is not None:
        parameters["mfa_code"] = mfa_code
    trace = []
    original = cache.a.repository.call
    def traced(operation, payload, **options):
        value = original(operation, payload, **options)
        trace.append((operation, value is not None))
        return value
    cache.a.repository.call = traced
    try:
        response = post(cache.a, "/auth/reauth", parameters, cache.session["csrf_token"])
    finally:
        cache.a.repository.call = original
    assert response.status_code == 200, {"null": [name for name, valid in trace if not valid], "last": trace[-1:]}
    return {"Idempotency-Key": key, "X-Reauth-Token": response.json()["reauth_token"]}


def unlock(cache, body=None, headers=None, *, recovery=False):
    body = body or request_body(cache)
    headers = headers or reauthorized(cache, body, recovery=recovery)
    return post(cache.a, "/me/cache-recover" if recovery else "/me/cache-unlock",
                body, cache.session["csrf_token"], **headers)


def test_default_service_and_database_gates_are_closed(cache):
    body = request_body(cache)
    cache.service.enabled = False
    response = unlock(cache, body)
    assert response.status_code == 503 and response.json()["error"]["code"] == "SERVICE_DISABLED"
    cache.service.enabled = True
    with cache.a.pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE identity_control.cache_policy SET enabled=false"))
    assert unlock(cache, body, {"Idempotency-Key": str(uuid4()), "X-Reauth-Token": "A" * 43}).status_code == 503


def test_candidate_schema_positive_and_negative_fixtures(cache):
    from jsonschema import Draft202012Validator, FormatChecker
    fixtures = json.loads((ROOT / 'tools/pr05a/formal/fixtures.json').read_text(encoding='utf-8'))
    for group in ('positive', 'negative'):
        for fixture in fixtures[group]:
            schema = {'$ref': '#/components/schemas/' + fixture['schema'], 'components': cache.contract['components']}
            valid = Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(fixture['value'])
            assert valid is (group == 'positive')


def test_cookie_bound_unlock_uses_pg_wrapped_key_and_no_secret_receipts(cache):
    response = unlock(cache)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert len(base64.b64decode(response.json()["key_material_b64"])) == 32
    with cache.a.pg.admin.begin() as connection:
        rows = connection.execute(sa.text("SELECT * FROM cache_keys WHERE owner_id=:owner"), {"owner": cache.a.uid}).mappings().all()
        assert len(rows) == 1 and len(rows[0]["wrapped_dek"]) == 61
        leaked = base64.b64decode(response.json()["key_material_b64"]) in bytes(rows[0]["wrapped_dek"])
        assert not leaked
        assert connection.scalar(sa.text("SELECT count(*) FROM preauth_receipts WHERE operation IN ('unlockCache','recoverCache')")) == 0
    second = unlock(cache)
    assert second.status_code == 200
    assert second.json()["key_id"] == response.json()["key_id"]
    same = secrets.compare_digest(second.json()["key_material_b64"], response.json()["key_material_b64"])
    assert same


def test_missing_proof_and_wrong_csrf_rejected(cache):
    response = post(cache.a, "/me/cache-unlock", request_body(cache), cache.session["csrf_token"])
    assert response.status_code == 403 and response.json()["error"]["code"] == "REAUTH_REQUIRED"
    assert post(cache.a, "/me/cache-unlock", request_body(cache), "wrong").status_code == 403


def test_proof_is_bound_to_exact_body_device_and_single_use(cache):
    body = request_body(cache)
    headers = reauthorized(cache, body)
    assert unlock(cache, {**body, "scope": "learning"}, headers).status_code == 403
    response = unlock(cache, body, headers)
    assert response.status_code == 200
    assert unlock(cache, body, headers).status_code == 403
    assert unlock(cache, {**body, "device_id": str(uuid4())}, headers).status_code == 403


def test_untrusted_device_cannot_release_learning_or_drafts(cache):
    body = request_body(cache)
    headers = reauthorized(cache, body)
    with cache.a.pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE devices SET trusted=false WHERE id=:id"), {"id": body["device_id"]})
    assert unlock(cache, body, headers).status_code == 403


def test_actual_device_revoke_rejects_preissued_proof_and_retains_key(cache):
    first = unlock(cache)
    old_cookie = cache.a.client.cookies.get('__Host-classroom_session')
    body = request_body(cache)
    old = reauthorized(cache, body)
    target = dict(purpose="revokeDevice", target=dict(kind="device", id=body["device_id"]),
                  candidate_parameters=dict(idempotency_key=str(uuid4()), body={}))
    response = post(cache.a, "/auth/reauth", dict(password=PASSWORD, operation=target), cache.session["csrf_token"])
    assert response.status_code == 200
    revoke = post(cache.a, f"/me/devices/{body['device_id']}/revoke", {}, cache.session["csrf_token"], **{
        "Idempotency-Key": target["candidate_parameters"]["idempotency_key"], "X-Reauth-Token": response.json()["reauth_token"]})
    assert revoke.status_code == 200
    cache.a.client.cookies.set('__Host-classroom_session', old_cookie)
    assert unlock(cache, body, old).status_code == 401
    with cache.a.pg.admin.begin() as connection:
        assert connection.scalar(sa.text("SELECT key_epoch FROM devices WHERE id=:id"), {"id": body["device_id"]}) == 2
        assert connection.scalar(sa.text("SELECT count(*) FROM cache_keys WHERE id=:id"), {"id": first.json()["key_id"]}) == 1


def test_real_password_change_invalidates_proof_and_does_not_resurrect_device(cache):
    first = unlock(cache)
    old_cookie = cache.a.client.cookies.get('__Host-classroom_session')
    body = request_body(cache)
    old = reauthorized(cache, body)
    key = str(uuid4())
    change = {"new_password": "Synthetic-new-password-20261115"}
    operation = dict(purpose="changePassword", target=dict(kind="account", id=str(cache.a.uid)),
                     candidate_parameters=dict(idempotency_key=key, body=change))
    proof = post(cache.a, "/auth/reauth", dict(password=PASSWORD, operation=operation), cache.session["csrf_token"])
    assert proof.status_code == 200
    result = post(cache.a, "/auth/password/change", change, cache.session["csrf_token"], **{
        "Idempotency-Key": key, "X-Reauth-Token": proof.json()["reauth_token"]})
    assert result.status_code == 200
    cache.a.client.cookies.set('__Host-classroom_session', old_cookie)
    assert unlock(cache, body, old).status_code == 401
    with cache.a.pg.admin.begin() as connection:
        assert connection.scalar(sa.text("SELECT count(*) FROM cache_keys WHERE id=:id"), {"id": first.json()["key_id"]}) == 1


def test_real_password_reset_invalidates_existing_cookie_and_preserves_key(cache):
    from classroom_web.infrastructure.auth_crypto import digest, random_token
    first = unlock(cache).json()
    body = request_body(cache)
    old = reauthorized(cache, body)
    token = random_token()
    with cache.a.pg.admin.begin() as connection:
        connection.execute(sa.text("""
            INSERT INTO email_actions(id,owner_id,purpose,token_hash,security_epoch,expires_at)
            VALUES(:id,:owner,'reset',:hash,1,clock_timestamp()+interval '5 minutes')
        """), {"id": uuid4(), "owner": cache.a.uid, "hash": digest("email_action", token)})
    reset_app = create_app(auth=cache.a.service, contract=cache.contract, origin="https://testserver")
    with TestClient(reset_app, base_url="https://testserver") as reset_client:
        csrf = reset_client.get(BASE + "/auth/csrf").json()["csrf_token"]
        reset = post(SimpleNamespace(client=reset_client), "/auth/password/reset",
                     dict(token=token, new_password="Synthetic-reset-password-20261115"), csrf)
        assert reset.status_code == 200, reset.text
    assert unlock(cache, body, old).status_code == 401
    with cache.a.pg.admin.begin() as connection:
        assert connection.scalar(sa.text("SELECT security_epoch FROM users WHERE id=:id"), {"id": cache.a.uid}) == 2
        assert connection.scalar(sa.text("SELECT count(*) FROM cache_keys WHERE id=:id"), {"id": first["key_id"]}) == 1


def test_postgres_key_failure_rolls_back_proof_consumption(cache):
    body = request_body(cache)
    headers = reauthorized(cache, body)
    with cache.a.pg.admin.begin() as connection:
        connection.exec_driver_sql("""
            CREATE FUNCTION cache_injected_failure() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'synthetic transaction failure'; END $$;
            CREATE TRIGGER cache_injected_failure BEFORE INSERT ON cache_keys FOR EACH ROW EXECUTE FUNCTION cache_injected_failure()
        """)
    try:
        assert unlock(cache, body, headers).status_code == 503
    finally:
        with cache.a.pg.admin.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER cache_injected_failure ON cache_keys; DROP FUNCTION cache_injected_failure()")
    assert unlock(cache, body, headers).status_code == 200


def test_crypto_failure_rolls_back_existing_reauth_proof(cache):
    assert unlock(cache).status_code == 200
    body = request_body(cache)
    headers = reauthorized(cache, body)
    original = cache.service.crypto
    cache.service.crypto = CacheKeyCrypto({"other": secrets.token_bytes(32)}, active_version="other")
    assert unlock(cache, body, headers).status_code == 503
    cache.service.crypto = original
    assert unlock(cache, body, headers).status_code == 200


def test_historical_key_requires_separate_proof_and_current_active_device(cache):
    first = unlock(cache).json()
    with cache.a.pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE devices SET key_epoch=key_epoch+1 WHERE id=:id"), {"id": cache.session["device_id"]})
    body = {**request_body(cache), "key_epoch": 2}
    current = unlock(cache, body).json()
    assert current["key_id"] != first["key_id"]
    recovery = {**body, "key_id": first["key_id"]}
    wrong_purpose = reauthorized(cache, body)
    assert unlock(cache, recovery, wrong_purpose, recovery=True).status_code == 403
    result = unlock(cache, recovery, recovery=True)
    assert result.status_code == 200 and result.json()["key_version"] == 1 and result.json()["key_epoch"] == 2
    same = secrets.compare_digest(result.json()["key_material_b64"], first["key_material_b64"])
    assert same


def test_other_current_device_cannot_recover_old_device_key(cache):
    first = unlock(cache).json()
    cache.a.client.cookies.clear()
    cache.session = login(cache.a)  # PR-03b registers a new device, even in the same browser.
    with cache.a.pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE devices SET trusted=true WHERE id=:id"), {"id": cache.session["device_id"]})
    body = {**request_body(cache), "key_id": first["key_id"]}
    assert unlock(cache, body, recovery=True).status_code == 404


def test_other_authenticated_owner_cannot_recover_or_address_foreign_key(cache):
    first = unlock(cache).json()
    owner = uuid4()
    email = uuid4().hex + '@example.test'
    with cache.a.pg.admin.begin() as connection:
        connection.execute(sa.text("""INSERT INTO users(id,normalized_email,password_hash,role,state,email_verified_at)
            VALUES(:id,:email,:password,'student','active',clock_timestamp())"""),
            {'id': owner, 'email': email, 'password': cache.a.crypto.hash(PASSWORD)})
    cache.a.client.cookies.clear()
    cache.a.email = email
    cache.session = login(cache.a)
    with cache.a.pg.admin.begin() as connection:
        connection.execute(sa.text('UPDATE devices SET trusted=true WHERE owner_id=:owner AND id=:device'),
                           {'owner': owner, 'device': cache.session['device_id']})
    body = {**request_body(cache), 'key_id': first['key_id']}
    assert unlock(cache, body, recovery=True).status_code == 404


def test_owner_rls_and_composite_device_fk(cache):
    first = unlock(cache).json()
    other = uuid4()
    with cache.a.pg.admin.begin() as connection:
        connection.execute(sa.text("INSERT INTO users(id,role,state,email_verified_at) VALUES(:id,'student','active',clock_timestamp())"), {"id": other})
    with cache.a.pg.db.transaction(OwnerContext(other)) as tx:
        assert tx.connection.scalar(sa.text("SELECT count(*) FROM cache_keys WHERE id=:id"), {"id": first["key_id"]}) == 0
        with pytest.raises(sa.exc.DBAPIError):
            tx.connection.execute(sa.text("""INSERT INTO cache_keys(id,owner_id,device_id,purpose,key_version,wrapped_dek,kek_version)
                VALUES(:id,:owner,:device,'draft',1,:wrapped,'fixture')"""),
                {"id": uuid4(), "owner": other, "device": cache.session["device_id"], "wrapped": b"x" * 61})


def test_runtime_cannot_enable_policy_or_read_auth_secrets(cache):
    for query in ("SELECT * FROM identity_control.cache_policy", "SELECT * FROM public.reauth_proofs"):
        with cache.a.pg.db.engine.connect() as connection:
            with pytest.raises(sa.exc.DBAPIError):
                connection.execute(sa.text(query))


def test_runtime_cannot_overwrite_envelopes_or_rename_key_identity(cache):
    first = unlock(cache).json()
    for query, parameters in (
        ("UPDATE cache_keys SET wrapped_dek=:wrapped WHERE id=:id", {"wrapped": b"x" * 61, "id": first["key_id"]}),
        ("UPDATE cache_keys SET id=:new_id WHERE id=:id", {"new_id": uuid4(), "id": first["key_id"]}),
    ):
        with pytest.raises(sa.exc.DBAPIError):
            with cache.a.pg.db.transaction(OwnerContext(cache.a.uid)) as tx:
                tx.connection.execute(sa.text(query), parameters)
    assert unlock(cache).json()["key_id"] == first["key_id"]


def test_recording_authorization_port_is_closed_and_classroom_owner_checked(cache):
    with cache.a.pg.db.transaction(OwnerContext(cache.a.uid)) as tx:
        semester = tx.catalog.create_semester("Synthetic")
        course = tx.catalog.create_course(semester.id, "Synthetic")
        classroom = tx.catalog.create_class_session(course.id, "Synthetic")
    body = {**request_body(cache), "scope": "recording_recovery", "class_session_id": str(classroom.id)}
    assert unlock(cache, body).status_code == 503
    invalid = {**body, "class_session_id": str(uuid4())}
    assert unlock(cache, invalid).status_code == 404
    with cache.a.pg.db.transaction(OwnerContext(cache.a.uid)) as tx:
        tx.tombstones.revoke(Resource.CLASS_SESSION, classroom.id, 1, ledger_sequence=1)
    deleted = unlock(cache, body)
    assert deleted.status_code == 410 and deleted.json()['error']['code'] == 'GONE'


@pytest.mark.parametrize('role', ['student', 'admin'])
def test_enrolled_mfa_must_be_proved_again_for_cache_release(cache, role):
    import pyotp
    from datetime import datetime, timedelta, timezone

    _, seed = install_factor(cache.a, role=role)
    if role == 'admin':
        csrf = cache.a.client.get(BASE + '/auth/csrf').json()['csrf_token']
        challenge = post(cache.a, '/auth/login', dict(email=cache.a.email, password=PASSWORD,
            remember_me=False, device_name='Synthetic MFA admin'), csrf)
        assert challenge.status_code == 200 and challenge.json()['state'] == 'mfa_required'
        completed = post(cache.a, '/auth/login/mfa', dict(factor_type='totp',
            challenge_id=challenge.json()['challenge_id'], code=pyotp.TOTP(seed).now()), csrf)
        assert completed.status_code == 200
        cache.session = completed.json()
        with cache.a.pg.admin.begin() as connection:
            connection.execute(sa.text('UPDATE devices SET trusted=true WHERE id=:id'), {'id': cache.session['device_id']})
    # Existing session precedes enrollment. Reauth must still observe the current factor.
    body = request_body(cache)
    operation = dict(purpose="unlockCache", target=dict(kind="device", id=body["device_id"]),
                     candidate_parameters=dict(idempotency_key=str(uuid4()), body=body))
    rejected = post(cache.a, "/auth/reauth", dict(password=PASSWORD, operation=operation), cache.session["csrf_token"])
    assert rejected.status_code == 401
    code = pyotp.TOTP(seed).at(datetime.now(timezone.utc) + timedelta(seconds=30))
    headers = reauthorized(cache, body, mfa_code=code)
    assert unlock(cache, body, headers).status_code == 200


def test_expired_real_reauth_proof_cannot_release_a_key(cache):
    body = request_body(cache)
    headers = reauthorized(cache, body)
    with cache.a.pg.admin.begin() as connection:
        connection.execute(sa.text("""
            UPDATE reauth_proofs SET verified_at=clock_timestamp()-interval '130 seconds',
              expires_at=clock_timestamp()-interval '10 seconds' WHERE owner_id=:owner AND purpose='unlockCache'
        """), {'owner': cache.a.uid})
    assert unlock(cache, body, headers).status_code == 403


def test_real_tls_browser_cookie_cache_unlock_and_device_revocation(cache, tmp_path):
    import os
    import subprocess
    import threading
    import time
    import uvicorn
    from fastapi.responses import HTMLResponse
    from test_auth_browser import _certificate

    dependency = Path(os.environ['PR05A_DEPENDENCY_ROOT'])
    cert, key, pin = _certificate(tmp_path, 'cache-formal', uuid4().hex[:12])
    import socket
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    origin = f'https://127.0.0.1:{port}'
    app = create_app(auth=cache.a.service, contract=cache.contract, origin=origin, cache=cache.service)
    app.add_api_route('/', lambda: HTMLResponse('<!doctype html><title>PR05a formal synthetic test</title>'), methods=['GET'])
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
        ssl_certfile=str(cert), ssl_keyfile=str(key), access_log=False, log_level='warning', ws='none'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    env = {**os.environ, 'PR05A_EMAIL': cache.a.email, 'PR05A_PASSWORD': PASSWORD,
        'PR05A_ORIGIN': origin, 'PR05A_PIN': pin, 'PR05A_BROWSER_PROFILE': str(tmp_path / 'browser-profile'),
        'PR05A_PLAYWRIGHT_MODULE': str(dependency / 'web/frontend/node_modules/@playwright/test/index.mjs'),
        'PLAYWRIGHT_BROWSERS_PATH': str(dependency / 'work/pr03b/vmware-broker/payload/ms-playwright')}
    process = None
    try:
        with (tmp_path / 'browser-stderr.log').open('w', encoding='utf-8') as stderr:
            process = subprocess.Popen([str(dependency / 'work/pr03b/vmware-broker/payload/node/node.exe'),
                str(ROOT / 'tools/pr05a/formal/browser.mjs')], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=stderr, env=env, text=True, encoding='utf-8')
            ready = process.stdout.readline()
            assert ready, (tmp_path / 'browser-stderr.log').read_text(encoding='utf-8')
            device = json.loads(ready)['device_id']
            with cache.a.pg.admin.begin() as connection:
                connection.execute(sa.text('UPDATE devices SET trusted=true WHERE owner_id=:owner AND id=:device'),
                                   {'owner': cache.a.uid, 'device': device})
            stdout, _ = process.communicate('synthetic trusted device configured\n', timeout=25)
        assert process.returncode == 0, (tmp_path / 'browser-stderr.log').read_text(encoding='utf-8')
        result = json.loads(stdout.strip().splitlines()[-1])
        assert result['real_device_revoke_blocks_old_unlock']
        (Path(os.environ['PR05A_REVIEW_OUTPUT']) / 'browser.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    finally:
        if process is not None and process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, check=False)
            process.wait(timeout=10)
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive()
