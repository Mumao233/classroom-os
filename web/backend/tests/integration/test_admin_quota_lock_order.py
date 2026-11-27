"""PostgreSQL lock order for a first quota adjustment racing an account disable."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from queue import Queue
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from classroom_web.infrastructure.auth_crypto import digest
from test_authentication import auth
from test_billing_integration import fee_api, fee_control, pg, prove, sign_in


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


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
def test_status_write_can_finish_while_quota_waits_for_its_lower_uuid_recipient(fee_api):
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
        recipient, actor = sorted((api, other), key=lambda account: account.a.uid)
        # Establish both accounts and their real operation-bound MFA proofs before taking locks.
        with database.connect() as db:
            start = db.scalar(sa.text("""SELECT date_trunc('month',clock_timestamp() AT TIME ZONE 'Asia/Shanghai')
                AT TIME ZONE 'Asia/Shanghai'"""))
        quota = dict(user_id=str(recipient.a.uid), period_start=start.isoformat(), bucket_kind='interactive_month_count',
                     additional_units=3, reason='Synthetic first quota adjustment')
        quota_request = signed_write(actor, 'adminAdjustQuota', dict(kind='account', id=str(actor.a.uid)), quota)
        with database.connect() as db:
            revision = db.scalar(sa.text('SELECT revision FROM users WHERE id=:id'), dict(id=actor.a.uid))
        status = dict(base_revision=revision, status='disabled', reason='Synthetic account disable')
        status_request = signed_write(recipient, 'adminUpdateUser', dict(kind='user', id=str(actor.a.uid)), status)

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
