"""R1: frozen material authorization and revocation/dispatch linearization in PG.

The review's courses A/B share one tenant. Moving classrooms between tenants is
separately forbidden; no synthetic source content is ever sent off this machine.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa

from classroom_web.application.task_worker import MockProvider, TaskWorker
from classroom_web.domain.data import Gone, NotFound, Resource
from classroom_web.infrastructure.schema import tables as catalog_tables
from classroom_web.infrastructure.task_sources import PgTaskSources

from test_material_revocation_race import _revoke_material
from test_persistent_tasks import (
    INPUT, WORKER, claim_one, enqueue, expire, persisted, prepared, submitting,
    task_db, scheduler,
)


def moved_source(task_db, owner, tree):
    """Frozen M1 belongs to A; the classroom now belongs to B in another semester."""
    with task_db.transaction(owner) as tx:
        # M1 must be historical to permit material-only revocation legitimately.
        tx.catalog.update_material(tree.course.id, 1, syllabus="Synthetic replacement M2",
                                   glossary=[], language="auto")
        semester_b = tx.catalog.create_semester("Synthetic destination semester")
        course_b = tx.catalog.create_course(semester_b.id, "Synthetic destination B")
        classroom = tx.catalog.move_class_session(tree.classroom.id, 1, course_b.id)
        assert classroom.course_material_version_id == tree.material.id
    return SimpleNamespace(original=tree, classroom=classroom,
                           destination_course=course_b, destination_semester=semester_b)


def reference(source, explicit):
    return dict(INPUT, **({'course_material_version_id': str(source.original.material.id)}
                          if explicit else {}))


def enqueue_source(task_db, owner, source, explicit, op_id=None):
    with task_db.transaction(owner) as tx:
        return tx.tasks.enqueue('ai_question', reference(source, explicit),
                                op_id=op_id or uuid4(), class_session_id=source.classroom.id)


def revoke_source(tx, owner, source, target):
    tree = source.original
    if target == 'material':
        _revoke_material(tx, owner, tree.material, request_id=uuid4())
    elif target == 'course':
        course = tx.catalog.get_course(tree.course.id)
        tx.tombstones.revoke(Resource.COURSE, course.id, course.revision, 509)
    else:
        semester = tx.catalog.get_semester(tree.semester.id)
        tx.tombstones.revoke(Resource.SEMESTER, semester.id, semester.revision, 509)


def worker(task_db, scheduler, provider):
    return TaskWorker(task_db, scheduler, provider, worker_id=WORKER)


@pytest.mark.parametrize('explicit', [False, True], ids=['omitted', 'explicit'])
@pytest.mark.parametrize('target', ['course', 'semester', 'material'])
def test_revoked_frozen_source_blocks_enqueue_even_after_classroom_move(
        target, explicit, task_db, scheduler, owners, tree, pg):
    source = moved_source(task_db, owners.a, tree)
    with task_db.transaction(owners.a) as tx:
        revoke_source(tx, owners.a, source, target)
    provider = MockProvider()
    with pytest.raises(Gone):
        enqueue_source(task_db, owners.a, source, explicit)
    assert worker(task_db, scheduler, provider).run_once() is False
    assert provider.calls == 0
    for name in ('tasks', 'task_attempts', 'provider_results', 'operation_receipts',
                 'owner_event_counters', 'outbox_events'):
        assert persisted(pg, name, owner_id=owners.a.owner_id) == []


@pytest.mark.parametrize('explicit', [False, True], ids=['omitted', 'explicit'])
@pytest.mark.parametrize('target', ['course', 'semester', 'material'])
def test_queued_task_rechecks_original_material_before_actual_worker_send(
        target, explicit, task_db, scheduler, owners, tree, pg):
    source = moved_source(task_db, owners.a, tree)
    task = enqueue_source(task_db, owners.a, source, explicit)
    with task_db.transaction(owners.a) as tx:
        revoke_source(tx, owners.a, source, target)
    provider = MockProvider()
    assert worker(task_db, scheduler, provider).run_once() is True
    assert provider.calls == 0
    assert persisted(pg, 'task_attempts', task_id=task.id) == []
    assert persisted(pg, 'provider_results', owner_id=owners.a.owner_id) == []
    stored = persisted(pg, 'tasks', id=task.id)[0]
    assert stored['may_have_charged'] is False
    events = persisted(pg, 'outbox_events', aggregate_id=task.id)
    assert [event['payload']['state'] for event in events] == ['queued', 'running']
    assert all(event['payload']['may_have_charged'] is False for event in events)
    # Expiry cleanup uses only access metadata and must not dispatch a new call.
    expire(pg, task.id)
    worker(task_db, scheduler, provider).recover_expired()
    assert persisted(pg, 'tasks', id=task.id)[0]['state'] == 'cancelled'
    assert provider.calls == 0


@pytest.mark.parametrize('explicit', [False, True], ids=['omitted', 'explicit'])
@pytest.mark.parametrize('operation', ['get', 'receipt', 'prepare', 'submit',
                                       'store', 'publish', 'reconcile'])
def test_all_task_paths_reauthorize_the_server_frozen_material(
        operation, explicit, task_db, scheduler, owners, tree, pg):
    source = moved_source(task_db, owners.a, tree)
    op_id = uuid4()
    task = enqueue_source(task_db, owners.a, source, explicit, op_id)
    claim = claim_one(scheduler, task)
    attempt = None
    provider = MockProvider()
    if operation in ('submit', 'store', 'publish', 'reconcile'):
        attempt = prepared(task_db, owners.a, task, claim)
    if operation in ('store', 'publish', 'reconcile'):
        submitting(task_db, owners.a, task, attempt, claim)
    if operation in ('publish', 'reconcile'):
        raw = provider.submit(task, attempt)
        with task_db.transaction(owners.a) as tx:
            tx.tasks.store_result(task.id, attempt.id, raw)
    with task_db.transaction(owners.a) as tx:
        revoke_source(tx, owners.a, source, 'course')
    calls_before = provider.calls
    before_task = persisted(pg, 'tasks', id=task.id)[0]
    before_attempts = persisted(pg, 'task_attempts', task_id=task.id)
    before_events = persisted(pg, 'outbox_events', aggregate_id=task.id)
    before_results = persisted(pg, 'provider_results', owner_id=owners.a.owner_id)
    with pytest.raises(Gone):
        with task_db.transaction(owners.a) as tx:
            if operation == 'get':
                tx.tasks.get(task.id)
            elif operation == 'receipt':
                tx.tasks.enqueue('ai_question', reference(source, explicit), op_id=op_id,
                                 class_session_id=source.classroom.id)
            elif operation == 'prepare':
                tx.tasks.prepare_attempt(task.id, claim.lease_token, WORKER)
            elif operation == 'submit':
                tx.tasks.mark_submitting(task.id, attempt.id, claim.lease_token, WORKER)
            elif operation == 'store':
                tx.tasks.store_result(task.id, attempt.id, '{"synthetic":"late raw"}')
            elif operation == 'publish':
                tx.tasks.publish_result(task.id, attempt.id, claim.lease_token, WORKER)
            else:
                tx.tasks.reconcile_result(task.id, uuid4())
    assert provider.calls == calls_before
    assert persisted(pg, 'tasks', id=task.id)[0] == before_task
    assert persisted(pg, 'task_attempts', task_id=task.id) == before_attempts
    assert persisted(pg, 'outbox_events', aggregate_id=task.id) == before_events
    assert persisted(pg, 'provider_results', owner_id=owners.a.owner_id) == before_results


def test_explicit_material_cannot_replace_the_frozen_server_reference(
        task_db, scheduler, owners, tree, pg):
    source = moved_source(task_db, owners.a, tree)
    with task_db.transaction(owners.a) as tx:
        wrong = tx.catalog.get_course(source.destination_course.id).current_material_version_id
    with pytest.raises(ValueError):
        with task_db.transaction(owners.a) as tx:
            tx.tasks.enqueue('ai_question', dict(INPUT, course_material_version_id=str(wrong)),
                             op_id=uuid4(), class_session_id=source.classroom.id)
    provider = MockProvider()
    assert worker(task_db, scheduler, provider).run_once() is False
    assert provider.calls == 0
    assert persisted(pg, 'tasks', owner_id=owners.a.owner_id) == []


def test_classroom_transfer_does_not_authorize_cross_owner_material(
        task_db, scheduler, owners, tree, pg):
    with task_db.transaction(owners.b) as tx:
        semester = tx.catalog.create_semester('Synthetic foreign tenant')
        foreign = tx.catalog.create_course(semester.id, 'Synthetic foreign destination')
    with pytest.raises(NotFound):
        with task_db.transaction(owners.a) as tx:
            tx.catalog.move_class_session(tree.classroom.id, 1, foreign.id)
    with pytest.raises((ValueError, NotFound)):
        with task_db.transaction(owners.a) as tx:
            tx.tasks.enqueue('ai_question', dict(INPUT,
                course_material_version_id=str(foreign.current_material_version_id)),
                op_id=uuid4(), class_session_id=tree.classroom.id)
    with task_db.transaction(owners.a) as tx:
        assert tx.catalog.get_class_session(tree.classroom.id).course_id == tree.course.id
    provider = MockProvider()
    assert worker(task_db, scheduler, provider).run_once() is False
    assert provider.calls == 0
    assert persisted(pg, 'tasks', owner_id=owners.a.owner_id) == []


def assert_waits_for(pg, future, *, waiter, blocker):
    """A real PG blocker edge, never a sleep or timeout used as lock evidence."""
    assert waiter != blocker
    deadline = monotonic() + 3.5
    with pg.admin.connect().execution_options(isolation_level='AUTOCOMMIT') as monitor:
        while monotonic() < deadline:
            if blocker in monitor.scalar(sa.text('SELECT pg_blocking_pids(:pid)'), {'pid': waiter}):
                return
            if future.done():
                pytest.fail('competing operation completed without source lock: ' + repr(future.result()))
            sleep(0.005)
    pytest.fail('PostgreSQL did not report the expected frozen-source lock dependency')


@pytest.mark.parametrize('winner', ['revocation', 'submission'])
@pytest.mark.parametrize('explicit', [False, True], ids=['omitted', 'explicit'])
@pytest.mark.parametrize('target', ['course', 'semester', 'material'])
def test_material_revocation_and_worker_submission_serialize_both_actor_orders(
        target, explicit, winner, task_db, scheduler, owners, tree, pg):
    source = moved_source(task_db, owners.a, tree)
    task = enqueue_source(task_db, owners.a, source, explicit)
    provider = MockProvider()
    worker_entered, revoker_entered = Event(), Event()
    intent_ready, allow_intent_commit, intent_committed = Event(), Event(), Event()
    revoked, allow_provider = Event(), Event()
    actors = {}

    class ObservedDatabase:
        @contextmanager
        def transaction(self, owner, **kwargs):
            submitted = False
            with task_db.transaction(owner, **kwargs) as tx:
                actors['worker'] = tx.connection.scalar(sa.text('SELECT pg_backend_pid()'))
                worker_entered.set()
                original = tx.tasks.mark_submitting

                def mark_submitting(*args, **values):
                    nonlocal submitted
                    original(*args, **values)
                    submitted = True
                    intent_ready.set()
                    assert allow_intent_commit.wait(12), 'test did not release intent COMMIT'

                tx.tasks.mark_submitting = mark_submitting
                yield tx
            if submitted:
                intent_committed.set()
                assert allow_provider.wait(12), 'test did not release provider boundary'

    def execute():
        return worker(ObservedDatabase(), scheduler, provider).run_once()

    def revoke():
        with task_db.transaction(owners.a) as tx:
            actors['revoker'] = tx.connection.scalar(sa.text('SELECT pg_backend_pid()'))
            revoker_entered.set()
            revoke_source(tx, owners.a, source, target)
        revoked.set()
        return 'revoked'

    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            if winner == 'revocation':
                # Main thread owns T1 until PG proves the actual worker waits.
                with task_db.transaction(owners.a) as tx:
                    actors['revoker'] = tx.connection.scalar(sa.text('SELECT pg_backend_pid()'))
                    revoke_source(tx, owners.a, source, target)
                    pending_worker = pool.submit(execute)
                    assert worker_entered.wait(10)
                    assert_waits_for(pg, pending_worker, waiter=actors['worker'], blocker=actors['revoker'])
                    assert provider.calls == 0
                assert pending_worker.result(timeout=10) is True
                assert provider.calls == 0
                assert persisted(pg, 'task_attempts', task_id=task.id) == []
                assert persisted(pg, 'provider_results', owner_id=owners.a.owner_id) == []
                assert persisted(pg, 'tasks', id=task.id)[0]['may_have_charged'] is False
                assert [event['payload']['state'] for event in persisted(
                    pg, 'outbox_events', aggregate_id=task.id)] == ['queued', 'running']
            else:
                pending_worker = pool.submit(execute)
                assert intent_ready.wait(10)
                pending_revoke = pool.submit(revoke)
                assert revoker_entered.wait(10)
                assert_waits_for(pg, pending_revoke, waiter=actors['revoker'], blocker=actors['worker'])
                assert provider.calls == 0
                allow_intent_commit.set()
                assert intent_committed.wait(10)
                assert pending_revoke.result(timeout=10) == 'revoked'
                assert revoked.is_set() and provider.calls == 0
                # The intent COMMIT is the authorization linearization point.
                # A later revocation cannot roll back an already authorized send.
                allow_provider.set()
                assert pending_worker.result(timeout=10) is True
                assert provider.calls == 1
                attempts = persisted(pg, 'task_attempts', task_id=task.id)
                assert len(attempts) == 1 and attempts[0]['submitted_at'] is not None
                assert persisted(pg, 'provider_results', owner_id=owners.a.owner_id) == []
                assert persisted(pg, 'tasks', id=task.id)[0]['may_have_charged'] is True
                expire(pg, task.id)
                worker(task_db, scheduler, provider).recover_expired()
                assert persisted(pg, 'tasks', id=task.id)[0]['state'] == 'unknown'
                assert scheduler.claim('no-resend-after-revocation') == []
                assert provider.calls == 1
        finally:
            allow_intent_commit.set()
            allow_provider.set()


def test_recovery_retries_source_discovery_conflict_without_calling_provider(
        task_db, scheduler, owners, tree, pg, monkeypatch):
    """A legitimate move during discovery rolls back; the next tick can recover."""
    source = moved_source(task_db, owners.a, tree)
    task = enqueue_source(task_db, owners.a, source, explicit=False)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    expire(pg, task.id)
    before_task = persisted(pg, 'tasks', id=task.id)[0]
    before_events = persisted(pg, 'outbox_events', aggregate_id=task.id)
    provider = MockProvider()
    engine = worker(task_db, scheduler, provider)
    original_rows = PgTaskSources._rows
    changed = []

    def move_after_discovery(repository, name, identities, *, lock=False):
        rows = original_rows(repository, name, identities, lock=lock)
        if (repository.owner_id == owners.a.owner_id and name == 'courses'
                and not lock and not changed):
            # _rows has completed its metadata read, and no resource lock has
            # yet been taken. Use another actual owner transaction to move the
            # classroom legitimately before authorization locks the old graph.
            changed.append(True)
            with task_db.transaction(owners.a) as tx:
                destination = tx.catalog.create_course(source.destination_semester.id,
                                                        'Synthetic concurrent destination C')
                moved = tx.catalog.move_class_session(source.classroom.id,
                    source.classroom.revision, destination.id)
                assert moved.course_material_version_id == source.original.material.id
        return rows

    monkeypatch.setattr(PgTaskSources, '_rows', move_after_discovery)
    recovered = engine.recover_expired()
    assert changed == [True]
    assert task.id not in {item.id for item in recovered}
    assert provider.calls == 0
    assert persisted(pg, 'tasks', id=task.id)[0] == before_task
    assert persisted(pg, 'task_attempts', id=attempt.id)[0]['state'] == 'planned'
    assert persisted(pg, 'outbox_events', aggregate_id=task.id) == before_events
    assert persisted(pg, 'provider_results', owner_id=owners.a.owner_id) == []

    # No blind loop or immediate external retry: a later scheduler tick obtains
    # the new graph and can requeue the still-provably-unsent planned attempt.
    recovered = engine.recover_expired()
    own = [item for item in recovered if item.id == task.id]
    assert len(own) == 1 and own[0].state == 'queued'
    assert provider.calls == 0
    assert engine.run_once() is True
    assert provider.calls == 1
    assert persisted(pg, 'tasks', id=task.id)[0]['state'] == 'succeeded'
    attempts = persisted(pg, 'task_attempts', task_id=task.id)
    assert len(attempts) == 1 and attempts[0]['id'] == attempt.id
    assert attempts[0]['state'] == 'succeeded'
