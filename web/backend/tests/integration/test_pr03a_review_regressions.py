"""Independent reviewer probes. Synthetic review copy only, never product code."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
import json
from threading import Event
import time
from uuid import uuid4

import pytest
import sqlalchemy as sa

from classroom_web.application.identity import IdentityService
from classroom_web.application.task_worker import MockProvider, TaskWorker
from classroom_web.domain.data import InactiveOwner, OwnerContext
from classroom_web.domain.identity import IdentityRejected
from classroom_web.infrastructure.identity_schema import tables as ids
from classroom_web.infrastructure.schema import tables as data
from classroom_web.infrastructure.task_schema import tables as tasks
from classroom_web.infrastructure.task_scheduler import PgTaskScheduler
from test_identity import identity, queued, activated, rows, PASSWORD


@contextmanager
def local_scheduler(identity):
    login = 'pr02a_review_scheduler_' + uuid4().hex
    with identity.admin.begin() as c:
        c.exec_driver_sql(f'CREATE ROLE "{login}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
        c.exec_driver_sql(f'GRANT classroom_web_scheduler TO "{login}"')
    scheduler = PgTaskScheduler(identity.rurl.set(username=login).render_as_string(hide_password=False))
    try:
        yield scheduler
    finally:
        scheduler.dispose()
        with identity.admin.begin() as c:
            c.exec_driver_sql(f'DROP ROLE "{login}"')


@pytest.mark.parametrize('invalid_field', ['email_verified_at', 'password_hash'])
@pytest.mark.parametrize('sent', [False, True])
def test_lost_verification_or_password_must_not_strand_expired_task(identity, invalid_field, sent):
    user, _, _, _ = activated(identity)
    owner = OwnerContext(user.user_id)
    with identity.runtime.authorized_transaction(user) as tx:
        task = tx.tasks.enqueue('ai_question', {'schema_version': 1}, op_id=uuid4())
    provider = MockProvider()
    with local_scheduler(identity) as scheduler:
        claim = scheduler.claim('reviewer', 30, 1)[0]
        with identity.runtime.transaction(owner) as tx:
            attempt = tx.tasks.prepare_attempt(task.id, claim.lease_token, 'reviewer')
        if sent:
            with identity.runtime.transaction(owner) as tx:
                tx.tasks.mark_submitting(task.id, attempt.id, claim.lease_token, 'reviewer')
            # Accepted by a simulated provider, response deliberately not saved.
            provider.submit(task, attempt)
        with identity.admin.begin() as c:
            c.execute(data['users'].update().where(data['users'].c.id == user.user_id).values(**{invalid_field: None}))
            c.execute(tasks['tasks'].update().where(tasks['tasks'].c.id == task.id).values(lease_expires_at=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
        with pytest.raises(InactiveOwner):
            with identity.runtime.transaction(owner):
                pytest.fail('inactive owner was admitted')
        worker = TaskWorker(identity.runtime, scheduler, provider, worker_id='reviewer-restart')
        worker.recover_expired()
        worker.recover_expired()
        with identity.admin.connect() as c:
            stored = c.execute(sa.select(tasks['tasks']).where(tasks['tasks'].c.id == task.id)).mappings().one()
            saved_attempt = c.execute(sa.select(tasks['task_attempts']).where(tasks['task_attempts'].c.id == attempt.id)).mappings().one()
        still_expired = [str(item.id) for item in scheduler.expired()]
        print(json.dumps({'invalid_field': invalid_field, 'sent': sent, 'provider_calls': provider.calls,
                          'task_state': stored['state'], 'attempt_state': saved_attempt['state'],
                          'task_still_in_expired': str(task.id) in still_expired,
                          'lease_retained': stored['lease_expires_at'] is not None,
                          'may_have_charged': stored['may_have_charged']}, sort_keys=True))
        assert provider.calls == int(sent), 'recovery must never resubmit'
        assert stored['state'] == ('unknown' if sent else 'cancelled')
        assert saved_attempt['state'] == ('unknown' if sent else 'cancelled')
        assert stored['lease_expires_at'] is None
        assert str(task.id) not in still_expired


def outcome(call):
    try:
        return ('ok', call())
    except IdentityRejected as e:
        return ('rejected', e.code)


def wait_for_blocked_identity_query(identity):
    until = time.monotonic() + 5
    while time.monotonic() < until:
        with identity.admin.connect() as c:
            waiting = c.scalar(sa.text("SELECT EXISTS(SELECT FROM pg_stat_activity WHERE datname=current_database() AND usename=:login AND wait_event_type='Lock' AND cardinality(pg_blocking_pids(pid)) > 0)"), {'login': identity.iurl.username})
        if waiting:
            return
        time.sleep(.02)
    pytest.fail('second transaction never demonstrated a database lock wait')


@pytest.mark.parametrize('first', ['revoke', 'activate'])
def test_revoke_and_activate_serialize_at_invitation_lock(identity, monkeypatch, first):
    invite, _, token = queued(identity)
    held, release = Event(), Event()
    original = identity.repository._audit
    target_action = 'invitation_revoked' if first == 'revoke' else 'account_activated'
    def audit(connection, action, **kwargs):
        original(connection, action, **kwargs)
        if action == target_action:
            held.set()
            assert release.wait(8)
    monkeypatch.setattr(identity.repository, '_audit', audit)
    calls = {'revoke': lambda: identity.service.revoke_invitation(identity.admin_principal, invite.id),
             'activate': lambda: identity.service.activate(token)}
    second = 'activate' if first == 'revoke' else 'revoke'
    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(outcome, calls[first])
        try:
            assert held.wait(5)
            loser = pool.submit(outcome, calls[second])
            wait_for_blocked_identity_query(identity)
        finally:
            release.set()
        assert winner.result(timeout=8)[0] == 'ok'
        assert loser.result(timeout=8)[0] == 'rejected'
    assert rows(identity, 'invitations')[0]['status'] == ('revoked' if first == 'revoke' else 'consumed')
    assert len(rows(identity, 'identity_events')) == (1 if first == 'activate' else 0)
    assert (rows(identity, 'pending_registrations')[0]['consumed_at'] is not None) == (first == 'activate')


def test_resend_wins_before_old_activation_and_rotates_all_proofs(identity, monkeypatch):
    invite, _, token = queued(identity)
    held, release = Event(), Event()
    original = identity.repository._audit
    def audit(connection, action, **kwargs):
        original(connection, action, **kwargs)
        if action == 'verification_resent':
            held.set()
            assert release.wait(8)
    monkeypatch.setattr(identity.repository, '_audit', audit)
    with ThreadPoolExecutor(max_workers=2) as pool:
        resend = pool.submit(outcome, lambda: identity.service.resend('synthetic-student@example.test', invite.token))
        try:
            assert held.wait(5)
            activation = pool.submit(outcome, lambda: identity.service.activate(token))
            wait_for_blocked_identity_query(identity)
        finally:
            release.set()
        assert resend.result(timeout=8)[0] == 'ok'
        assert activation.result(timeout=8) == ('rejected', 'VERIFICATION_INVALID')
    assert rows(identity, 'invitations')[0]['status'] == 'open'
    assert rows(identity, 'pending_registrations')[0]['consumed_at'] is None
    assert rows(identity, 'identity_events') == []


def test_default_resend_interval_and_registration_pause_preserve_ledgers(identity):
    invite, _, token = queued(identity)
    service = IdentityService(identity.repository, identity.service.hasher, admin_reauthentication_window=timedelta(minutes=5))
    before = {n: rows(identity, n) for n in ids}
    with pytest.raises(IdentityRejected) as err:
        service.resend('synthetic-student@example.test', invite.token)
    assert err.value.code == 'RATE_LIMITED'
    assert {n: rows(identity, n) for n in ids} == before
    with identity.admin.begin() as c:
        c.execute(ids['identity_mail_intents'].update().values(created_at=sa.func.clock_timestamp()-sa.text("interval '61 seconds'")))
    service.resend('synthetic-student@example.test', invite.token)
    with pytest.raises(IdentityRejected):
        service.activate(token)
    with identity.admin.begin() as c:
        c.execute(ids['identity_capacity'].update().values(registration_enabled=False))
    before = {n: rows(identity, n) for n in ids}
    with pytest.raises(IdentityRejected):
        service.register('paused@example.test', PASSWORD, invite.token)
    with pytest.raises(IdentityRejected):
        service.activate(token)
    assert {n: rows(identity, n) for n in ids} == before


def test_two_student_owners_are_isolated_in_repository_and_direct_sql(identity):
    from classroom_web.domain.data import NotFound
    a, _, _, _ = activated(identity, 'student-a@example.test')
    b, _, _, _ = activated(identity, 'student-b@example.test')
    with identity.runtime.authorized_transaction(b) as tx:
        semester = tx.catalog.create_semester('B only')
        course = tx.catalog.create_course(semester.id, 'B course')
        classroom = tx.catalog.create_class_session(course.id, 'B classroom')
    with identity.runtime.authorized_transaction(a) as tx:
        for fn, uid in ((tx.catalog.get_course, course.id), (tx.catalog.get_class_session, classroom.id)):
            with pytest.raises(NotFound):
                fn(uid)
        assert tx.connection.execute(sa.select(data['courses'])).all() == []
        assert tx.connection.execute(sa.select(data['class_sessions'])).all() == []
        assert tx.connection.execute(data['courses'].update().where(data['courses'].c.id == course.id).values(name='forbidden')).rowcount == 0
    with identity.runtime.authorized_transaction(b) as tx:
        assert tx.catalog.get_course(course.id).name == 'B course'


def test_identity_role_cannot_insert_admin_or_promote_student_by_direct_sql(identity):
    student, _, _, _ = activated(identity)
    with pytest.raises(sa.exc.DBAPIError):
        with identity.repository.engine.begin() as c:
            c.execute(data['users'].insert().values(id=uuid4(), normalized_email='forged-admin@example.test',
                password_hash='$argon2id$synthetic-placeholder', role='admin', state='active', email_verified_at=sa.func.clock_timestamp()))
    with pytest.raises(sa.exc.DBAPIError):
        with identity.repository.engine.begin() as c:
            c.execute(data['users'].update().where(data['users'].c.id == student.user_id).values(role='admin'))
    with identity.admin.connect() as c:
        assert c.scalar(sa.select(data['users'].c.role).where(data['users'].c.id == student.user_id)) == 'student'
