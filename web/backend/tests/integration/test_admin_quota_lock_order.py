"""PostgreSQL lock order for quota changes and projections racing an account disable."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from queue import Queue
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from classroom_web.infrastructure.auth_crypto import digest
from test_authentication import BASE, auth
from test_billing_integration import fee_api, fee_control, headers, pg, prove, sign_in


def signed_write(api, purpose, target, body):
    session, seed = sign_in(api, administrator=True)
    key = str(uuid4())
    credential = prove(api, session, seed, purpose, body, key, target=target)
    operation = dict(purpose=purpose, target=target, candidate_parameters=dict(idempotency_key=key, body=body))
    return json.dumps(dict(
        token_hash=digest('session', api.client.cookies.get('__Host-classroom_session')),
        reauth_hash=digest('reauth', credential['X-Reauth-Token']),
        purpose=purpose, target_id=target['id'], body=body, operation_key=key,
        commitment=api.a.crypto.commitment('operation', operation),
    ))


@pytest.fixture
def ordered_admins(fee_api):
    api = fee_api
    other_id = uuid4()
    email = other_id.hex + '@example.test'
    database = api.c.pg.admin
    with database.begin() as db:
        db.execute(sa.text("""INSERT INTO users(id,normalized_email,password_hash,role,state,email_verified_at)
            SELECT :id,:email,password_hash,'admin','active',clock_timestamp() FROM users WHERE id=:source"""),
            dict(id=other_id, email=email, source=api.a.uid))

    with closing(TestClient(api.client.app, base_url='https://testserver')) as client:
        other = SimpleNamespace(a=SimpleNamespace(uid=other_id, email=email, pg=api.c.pg, crypto=api.a.crypto),
                                client=client)
        yield sorted((api, other), key=lambda account: account.a.uid)


def disable_request(database, recipient, actor):
    with database.connect() as db:
        revision = db.scalar(sa.text('SELECT revision FROM users WHERE id=:id'), dict(id=actor.a.uid))
    status = dict(base_revision=revision, status='disabled', reason='Synthetic account disable')
    return signed_write(recipient, 'adminUpdateUser', dict(kind='user', id=str(actor.a.uid)), status)


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
def test_status_write_can_finish_while_quota_waits_for_its_lower_uuid_recipient(fee_api, ordered_admins):
    api = fee_api
    database = api.c.pg.admin
    recipient, actor = ordered_admins
    with database.connect() as db:
        start = db.scalar(sa.text("""SELECT date_trunc('month',clock_timestamp() AT TIME ZONE 'Asia/Shanghai')
            AT TIME ZONE 'Asia/Shanghai'"""))
    quota = dict(user_id=str(recipient.a.uid), period_start=start.isoformat(), bucket_kind='interactive_month_count',
                 additional_units=3, reason='Synthetic first quota adjustment')
    quota_request = signed_write(actor, 'adminAdjustQuota', dict(kind='account', id=str(actor.a.uid)), quota)
    status_request = disable_request(database, recipient, actor)

    apply = sa.text('SELECT billing_api.apply_admin(CAST(:payload AS jsonb))')
    worker_pid = Queue()

    def adjust():
        with api.c.gateway.engine.begin() as db:
            db.exec_driver_sql("SET LOCAL lock_timeout='10s'")
            worker_pid.put(db.scalar(sa.text('SELECT pg_backend_pid()')))
            return db.scalar(apply, dict(payload=quota_request))

    bucket_count = sa.text('SELECT count(*) FROM quota_periods WHERE owner_id=:id')
    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.begin() as status_db:
            status_db.exec_driver_sql("SET LOCAL lock_timeout='5s'")
            assert status_db.scalar(bucket_count, dict(id=recipient.a.uid)) == 0
            # Pause the status transaction immediately after its first ordered row lock.
            status_db.execute(sa.text('SELECT id FROM users WHERE id=:id FOR UPDATE'), dict(id=recipient.a.uid))
            gateway_login = api.c.gateway.engine.url.username
            status_db.exec_driver_sql(f'SET LOCAL ROLE "{gateway_login}"')
            waiting = pool.submit(adjust)
            pid = worker_pid.get(timeout=5)
            deadline = monotonic() + 5
            while not status_db.scalar(sa.text('SELECT pg_backend_pid()=ANY(pg_blocking_pids(:pid))'), dict(pid=pid)):
                assert not waiting.done(), f'quota did not wait for the recipient: {waiting.result()}'
                assert monotonic() < deadline, 'PostgreSQL did not report the quota-to-status lock dependency'
            changed = status_db.scalar(apply, dict(payload=status_request))
            assert (changed['id'], changed['status']) == (str(actor.a.uid), 'disabled')
        assert waiting.result(timeout=10) == {'error': 'FORBIDDEN'}

    with database.connect() as db:
        assert db.scalar(bucket_count, dict(id=recipient.a.uid)) == 0
        assert db.scalar(sa.text('SELECT state FROM users WHERE id=:id'), dict(id=actor.a.uid)) == 'disabled'


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
@pytest.mark.parametrize('after_adjustment', [False, True], ids=['get', 'post-adjustment-read'])
def test_quota_projection_rechecks_session_after_waiting_for_lower_uuid_recipient(
        fee_api, ordered_admins, after_adjustment, monkeypatch):
    api = fee_api
    recipient, actor = ordered_admins
    database = api.c.pg.admin
    session, seed = sign_in(actor, administrator=True)
    path = BASE + f'/admin/users/{recipient.a.uid}/quota'
    initial = actor.client.get(path)
    assert initial.status_code == 200, initial.text
    bucket = next(b for b in initial.json()['buckets'] if b['kind'] == 'interactive_month_count')
    adjustment = dict(user_id=str(recipient.a.uid), bucket_kind=bucket['kind'], period_start=bucket['period_start'],
                      additional_units=3, reason='Synthetic concurrent quota read')
    key = str(uuid4())
    if after_adjustment:
        proof = prove(actor, session, seed, 'adminAdjustQuota', adjustment, key)
    status_request = disable_request(database, recipient, actor)

    # At this boundary a POST has already committed its adjustment. Both HTTP
    # paths still perform their normal quota projection on a runtime connection.
    reached_projection, resume = Event(), Event()
    project = api.admin._quota

    def pause_before_projection(ctx, owner_id):
        reached_projection.set()
        assert resume.wait(timeout=5)
        return project(ctx, owner_id)

    monkeypatch.setattr(api.admin, '_quota', pause_before_projection)
    with ThreadPoolExecutor(max_workers=1) as pool:
        if after_adjustment:
            waiting = pool.submit(actor.client.post, BASE + '/admin/quota-adjustments',
                                  json=adjustment, headers=headers(session, **proof))
        else:
            waiting = pool.submit(actor.client.get, path)
        try:
            assert reached_projection.wait(timeout=5)
            with database.begin() as writer:
                writer.exec_driver_sql("SET LOCAL lock_timeout='3s'")
                writer.execute(sa.text('SELECT id FROM users WHERE id=:id FOR UPDATE'), dict(id=recipient.a.uid))
                resume.set()
                deadline = monotonic() + 5
                runtime = api.c.pg.db.engine.url.username
                while True:
                    writer.exec_driver_sql('SELECT pg_stat_clear_snapshot()')
                    if writer.scalar(sa.text('''SELECT EXISTS(SELECT FROM pg_stat_activity WHERE usename=:runtime
                            AND pg_backend_pid()=ANY(pg_blocking_pids(pid)))'''), dict(runtime=runtime)):
                        break
                    assert not waiting.done(), 'quota projection finished before the recipient lock was released'
                    assert monotonic() < deadline, 'quota projection never waited on the status connection'
                    sleep(.01)
                gateway = api.c.gateway.engine.url.username
                writer.exec_driver_sql(f'SET LOCAL ROLE "{gateway}"')
                changed = writer.scalar(sa.text('SELECT billing_api.apply_admin(CAST(:payload AS jsonb))'),
                                        dict(payload=status_request))
                assert (changed['id'], changed['status']) == (str(actor.a.uid), 'disabled')
        finally:
            resume.set()
        response = waiting.result(timeout=10)
        assert response.status_code == 401 and response.json()['error']['code'] == 'UNAUTHENTICATED', response.text

    with database.connect() as db:
        deltas = db.scalars(sa.text('SELECT delta FROM quota_adjustments WHERE owner_id=:id AND op_id=:op'),
                            dict(id=recipient.a.uid, op=key)).all()
        assert deltas == ([3] if after_adjustment else [])


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
@pytest.mark.parametrize('session_state, expected', [
    ('student', (403, 'FORBIDDEN')), ('expired', (401, 'UNAUTHENTICATED')),
    ('revoked-factor', (401, 'UNAUTHENTICATED')), ('admin', (404, 'NOT_FOUND')),
])
def test_quota_checks_current_session_before_disclosing_missing_recipient(fee_api, session_state, expected):
    api = fee_api
    sign_in(api, administrator=session_state != 'student')
    with api.c.pg.admin.begin() as db:
        if session_state == 'expired':
            db.execute(sa.text('UPDATE auth_sessions SET idle_deadline=clock_timestamp() WHERE owner_id=:id'),
                       dict(id=api.a.uid))
        elif session_state == 'revoked-factor':
            db.execute(sa.text("UPDATE mfa_credentials SET state='revoked',revoked_at=clock_timestamp() WHERE owner_id=:id"),
                       dict(id=api.a.uid))
    response = api.client.get(BASE + f'/admin/users/{uuid4()}/quota')
    assert (response.status_code, response.json()['error']['code']) == expected
