"""Admission at the shared account limit, with real admin and registration logins."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
from uuid import uuid4

import pytest
import sqlalchemy as sa

from conftest import migrated_database
from classroom_web.application.identity import token_digest
from classroom_web.infrastructure.auth_crypto import random_token
from test_admin_identity_writes import (
    admin, audit, auth, create_student, fee_api, invitation, proof,
    registration, rejected, write,
)
from test_billing_integration import installed_fee_control


pytestmark = pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
ACTIVE_COUNT = sa.text("SELECT count(*) FROM users WHERE state='active' AND deleted_at IS NULL")


@pytest.fixture
def pg():
    """Committed writes on either login belong to this test's disposable database."""
    with migrated_database() as database:
        yield database


@pytest.fixture
def fee_control(pg):
    with installed_fee_control(pg) as control:
        yield control


def prepare_seats(api, *, stage, available, activated):
    with api.c.pg.admin.begin() as db:
        # Only the maintenance actor and the signed-in admin start active.
        assert db.scalar(ACTIVE_COUNT) == 2
        db.execute(sa.text("INSERT INTO users(id,role,state) VALUES(:id,'student','active')"),
                   [dict(id=uuid4()) for _ in range(stage - available - 2)])
        db.execute(sa.text('UPDATE identity_capacity SET max_active_users=:stage,activated_count=:count WHERE id=1'),
                   dict(stage=stage, count=activated))
        assert db.scalar(ACTIVE_COUNT) == stage - available


def restore_request(api, target):
    body = dict(base_revision=1, status='active', reason='Synthetic account re-enabled')
    credential = proof(api, 'adminUpdateUser', body, 'user', target)
    return '/admin/users/' + str(target), body, credential


def test_reenable_disabled_user(admin):
    target, _ = create_student(admin, 'disabled')
    path, body, credential = restore_request(admin, target)
    # Restoring a previously admitted account consumes a seat but no new registration.
    prepare_seats(admin, stage=10, available=1, activated=10)
    response = write(admin, 'PATCH', path, body, credential)
    assert response.status_code == 200, response.text
    assert (response.json()['status'], response.json()['revision']) == ('active', 2)
    with admin.c.pg.admin.connect() as db:
        assert db.scalar(ACTIVE_COUNT) == 10
        assert db.scalar(sa.text('SELECT activated_count FROM identity_capacity WHERE id=1')) == 10


@pytest.mark.parametrize('stage', [10, 30])
def test_reenable_rejected_when_stage_is_full(admin, stage):
    target, _ = create_student(admin, 'disabled')
    path, body, credential = restore_request(admin, target)
    prepare_seats(admin, stage=stage, available=0, activated=stage)
    rejected(write(admin, 'PATCH', path, body, credential), 422, 'VALIDATION_FAILED')
    assert audit(admin, 'adminUpdateUser', target) == []
    with admin.c.pg.admin.connect() as db:
        assert db.scalar(ACTIVE_COUNT) == stage
        assert db.execute(sa.text('SELECT state,revision FROM users WHERE id=:id'), dict(id=target)).one() == ('disabled', 1)
        key = dict(op=credential['Idempotency-Key'])
        assert db.scalar(sa.text('SELECT count(*) FROM preauth_receipts WHERE operation_key=:op'), key) == 0
        assert db.execute(sa.text('SELECT consumed_at FROM reauth_proofs WHERE operation_key=:op'), key).scalar_one() is None


def test_registration_and_restore_compete_for_last_seat(admin, registration, monkeypatch):
    target, _ = create_student(admin, 'disabled')
    path, body, credential = restore_request(admin, target)
    _, invite_token = invitation(admin)
    email, mail_proof = uuid4().hex + '@example.test', token_digest(random_token())
    with admin.c.pg.admin.connect() as db:
        password_hash = db.scalar(sa.text('SELECT password_hash FROM users WHERE id=:id'), dict(id=admin.a.uid))
    registration.register(email, password_hash, token_digest(invite_token), mail_proof, {})

    # Hold registration just before commit until PostgreSQL observes the admin
    # gateway actually waiting on this connection. Poll on the existing writer.
    holds_seat = Event()
    original_audit = registration._audit
    gateway_login = admin.c.gateway.engine.url.username

    def wait_for_restore(db, action, **kwargs):
        original_audit(db, action, **kwargs)
        assert action == 'account_activated'
        holds_seat.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            db.execute(sa.text('SELECT pg_stat_clear_snapshot()'))
            if db.scalar(sa.text('''SELECT EXISTS(SELECT FROM pg_stat_activity WHERE usename=:gateway
                AND pg_backend_pid()=ANY(pg_blocking_pids(pid)))'''), dict(gateway=gateway_login)):
                return
            time.sleep(.01)
        pytest.fail('admin restoration did not block on the registration connection')

    monkeypatch.setattr(registration, '_audit', wait_for_restore)
    prepare_seats(admin, stage=10, available=1, activated=9)
    with ThreadPoolExecutor(max_workers=1) as pool:
        activation = pool.submit(registration.activate, mail_proof)
        assert holds_seat.wait(timeout=5)
        response = write(admin, 'PATCH', path, body, credential)
        assert activation.result(timeout=5) is not None
        rejected(response, 422, 'VALIDATION_FAILED')
        with admin.c.pg.admin.connect() as db:
            assert db.scalar(ACTIVE_COUNT) == 10
            assert db.scalar(sa.text('SELECT activated_count FROM identity_capacity WHERE id=1')) == 10
            assert db.scalar(sa.text('SELECT state FROM users WHERE id=:id'), dict(id=target)) == 'disabled'
            assert db.scalar(sa.text('SELECT state FROM users WHERE normalized_email=:email'), dict(email=email)) == 'active'
