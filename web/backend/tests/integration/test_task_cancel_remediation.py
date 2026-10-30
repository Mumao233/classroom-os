"""R2: cancellation classifies persisted submission evidence, not queue labels.

Every provider in this module is a local MockProvider. Real PostgreSQL persists
attempts/results and coordinates the concurrent cancellation/claim actors.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
from uuid import uuid4

import pytest
import sqlalchemy as sa

from classroom_web.application.task_worker import MockProvider, TaskWorker
from classroom_web.domain.tasks import Attempt
from classroom_web.infrastructure.task_schema import tables

from test_persistent_tasks import (
    WORKER, claim_one, enqueue, expire, persisted, prepared, scheduler,
    submitting, task_db,
)


def worker(database, queue, provider):
    return TaskWorker(database, queue, provider, worker_id=WORKER, lease_seconds=30)


def saved_result(database, queue, owner, pg, *, output='{"answer":"synthetic saved result"}'):
    task = enqueue(database, owner)
    claim = claim_one(queue, task)
    attempt = prepared(database, owner, task, claim)
    submitting(database, owner, task, attempt, claim)
    provider = MockProvider(output=output)
    raw = provider.submit(task, attempt)
    with database.transaction(owner) as tx:
        result = tx.tasks.store_result(task.id, attempt.id, raw)
    expire(pg, task.id)
    with database.transaction(owner) as tx:
        assert tx.tasks.recover(task.id, claim.lease_token).state == 'queued'
    assert provider.calls == 1
    return task, attempt, result, provider


def assert_finished(pg, task, attempt, result, provider, *, valid=True):
    final = persisted(pg, 'tasks', id=task.id)[0]
    final_attempt = persisted(pg, 'task_attempts', id=attempt.id)[0]
    raw = persisted(pg, 'provider_results', id=result.id)[0]
    expected = 'succeeded' if valid else 'failed'
    assert final['state'] == final_attempt['state'] == expected
    assert final['completed_at'] is not None and final_attempt['finished_at'] is not None
    assert final['may_have_charged'] is True
    assert final_attempt['provider_outcome'] == 'succeeded'
    assert raw['validation_status'] == ('valid' if valid else 'invalid')
    assert raw['raw_output'] == provider.output
    assert len(persisted(pg, 'task_attempts', task_id=task.id)) == 1
    assert provider.calls == 1
    event = persisted(pg, 'outbox_events', aggregate_id=task.id)[-1]
    assert event['payload']['state'] == expected
    assert event['payload']['may_have_charged'] is True


@pytest.mark.parametrize('planned', (False, True), ids=('never-claimed', 'planned-recovered'))
def test_cancel_proven_unsent_finishes_any_planned_attempt(
        planned, task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    attempt = None
    if planned:
        claim = claim_one(scheduler, task)
        attempt = prepared(task_db, owners.a, task, claim)
        expire(pg, task.id)
        with task_db.transaction(owners.a) as tx:
            assert tx.tasks.recover(task.id, claim.lease_token).state == 'queued'
    op_id = uuid4()
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.cancel(task.id, op_id).state == 'cancelled'
    count = len(persisted(pg, 'outbox_events', aggregate_id=task.id))
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.cancel(task.id, op_id).state == 'cancelled'
    assert len(persisted(pg, 'outbox_events', aggregate_id=task.id)) == count
    provider = MockProvider()
    assert worker(task_db, scheduler, provider).run_once() is False
    assert provider.calls == 0
    final = persisted(pg, 'tasks', id=task.id)[0]
    assert final['completed_at'] is not None and final['may_have_charged'] is False
    if attempt:
        row = persisted(pg, 'task_attempts', id=attempt.id)[0]
        assert row['state'] == 'cancelled' and row['finished_at'] is not None
        assert row['submitted_at'] is None and row['raw_output_ref'] is None


def test_cancel_sent_unknown_preserves_uncertainty_and_does_not_send_again(
        task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    submitting(task_db, owners.a, task, attempt, claim)
    provider = MockProvider()
    provider.submit(task, attempt)  # accepted by mock; reply is deliberately lost
    expire(pg, task.id)
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.recover(task.id, claim.lease_token).state == 'unknown'
    op_id = uuid4()
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.cancel(task.id, op_id).state == 'unknown'
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.cancel(task.id, op_id).state == 'unknown'
        assert tx.tasks.reconcile_result(task.id, uuid4()).state == 'unknown'
    assert worker(task_db, scheduler, provider).run_once() is False
    assert provider.calls == 1
    row = persisted(pg, 'tasks', id=task.id)[0]
    assert row['may_have_charged'] is True and row['completed_at'] is None
    assert row['cancel_requested_at'] is not None
    assert persisted(pg, 'task_attempts', id=attempt.id)[0]['state'] == 'unknown'
    assert persisted(pg, 'provider_results', attempt_id=attempt.id) == []


@pytest.mark.parametrize('finish', ('worker', 'reconcile'))
@pytest.mark.parametrize('valid', (True, False), ids=('valid', 'invalid'))
def test_cancel_recovered_raw_keeps_local_completion_path(
        finish, valid, task_db, scheduler, owners, pg):
    task, attempt, result, provider = saved_result(task_db, scheduler, owners.a, pg,
        output='{"answer":"synthetic saved result"}' if valid else 'synthetic invalid JSON')
    op_id = uuid4()
    with task_db.transaction(owners.a) as tx:
        cancelled = tx.tasks.cancel(task.id, op_id)
    assert cancelled.state == 'queued'
    assert cancelled.cancel_requested_at is not None and cancelled.completed_at is None
    assert persisted(pg, 'provider_results', id=result.id)[0]['validation_status'] == 'pending'
    assert persisted(pg, 'task_attempts', id=attempt.id)[0]['raw_output_ref'] == result.id
    events = len(persisted(pg, 'outbox_events', aggregate_id=task.id))
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.cancel(task.id, op_id).state == 'queued'
    assert len(persisted(pg, 'outbox_events', aggregate_id=task.id)) == events
    if finish == 'worker':
        assert worker(task_db, scheduler, provider).run_once() is True
    else:
        reconcile_op = uuid4()
        with task_db.transaction(owners.a) as tx:
            assert tx.tasks.reconcile_result(task.id, reconcile_op).state == ('succeeded' if valid else 'failed')
        events = len(persisted(pg, 'outbox_events', aggregate_id=task.id))
        with task_db.transaction(owners.a) as tx:
            assert tx.tasks.reconcile_result(task.id, reconcile_op).state == ('succeeded' if valid else 'failed')
        assert len(persisted(pg, 'outbox_events', aggregate_id=task.id)) == events
    assert_finished(pg, task, attempt, result, provider, valid=valid)
    assert worker(task_db, scheduler, provider).run_once() is False
    assert provider.calls == 1


def test_reconcile_repairs_preexisting_cancelled_task_with_pending_raw(
        task_db, scheduler, owners, pg):
    task, attempt, result, provider = saved_result(task_db, scheduler, owners.a, pg)
    # Reproduce exactly the state already persisted by the reviewed implementation.
    with pg.admin.begin() as connection:
        connection.execute(tables['tasks'].update().where(tables['tasks'].c.id == task.id)
            .values(state='cancelled', revision=tables['tasks'].c.revision + 1,
                    completed_at=sa.func.clock_timestamp(), cancel_requested_at=sa.func.clock_timestamp()))
    assert persisted(pg, 'task_attempts', id=attempt.id)[0]['state'] == 'submitting'
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.reconcile_result(task.id, uuid4()).state == 'succeeded'
    assert_finished(pg, task, attempt, result, provider)
    assert worker(task_db, scheduler, provider).run_once() is False


def test_cancellation_considers_older_submitted_attempt_not_only_latest_planned(
        task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    original_id, newer_id = uuid4(), uuid4()
    with task_db.transaction(owners.a) as tx:
        row = tx.connection.execute(tables['task_attempts'].insert().values(
            id=original_id, owner_id=owners.a.owner_id, task_id=task.id,
            attempt_number=1, state='unknown', submitted_at=sa.func.clock_timestamp(),
            provider='mock', provider_outcome='unknown').returning(tables['task_attempts'])).mappings().one()
        tx.connection.execute(tables['task_attempts'].insert().values(
            id=newer_id, owner_id=owners.a.owner_id, task_id=task.id,
            attempt_number=2, state='planned', provider='mock', provider_outcome='not_submitted'))
    provider = MockProvider()
    provider.submit(task, Attempt(**row))  # lost result from the earlier attempt
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.cancel(task.id, uuid4()).state == 'unknown'
    assert worker(task_db, scheduler, provider).run_once() is False
    assert provider.calls == 1
    task_row = persisted(pg, 'tasks', id=task.id)[0]
    assert task_row['may_have_charged'] is True and task_row['completed_at'] is None
    assert persisted(pg, 'task_attempts', id=original_id)[0]['state'] == 'unknown'
    newer = persisted(pg, 'task_attempts', id=newer_id)[0]
    assert newer['state'] == 'cancelled' and newer['submitted_at'] is None


def test_older_saved_raw_is_completed_instead_of_sending_newer_planned_attempt(
        task_db, scheduler, owners, pg):
    task, original, result, provider = saved_result(task_db, scheduler, owners.a, pg)
    newer_id = uuid4()
    with task_db.transaction(owners.a) as tx:
        tx.connection.execute(tables['task_attempts'].insert().values(
            id=newer_id, owner_id=owners.a.owner_id, task_id=task.id,
            attempt_number=2, state='planned', provider='mock', provider_outcome='not_submitted'))
    cancel_op = uuid4()
    with task_db.transaction(owners.a) as tx:
        pending = tx.tasks.cancel(task.id, cancel_op)
    assert pending.state == 'queued' and pending.cancel_requested_at is not None
    assert pending.completed_at is None and pending.may_have_charged is True
    unused = persisted(pg, 'task_attempts', id=newer_id)[0]
    assert unused['state'] == 'cancelled' and unused['finished_at'] is not None
    assert unused['submitted_at'] is None and unused['raw_output_ref'] is None
    assert persisted(pg, 'provider_results', id=result.id)[0]['validation_status'] == 'pending'
    assert worker(task_db, scheduler, provider).run_once() is True
    assert provider.calls == 1, 'local completion must never submit the newer planned attempt'
    final_task = persisted(pg, 'tasks', id=task.id)[0]
    finished = persisted(pg, 'task_attempts', id=original.id)[0]
    assert final_task['state'] == finished['state'] == 'succeeded'
    assert finished['raw_output_ref'] == result.id and finished['provider_outcome'] == 'succeeded'
    assert final_task['completed_at'] is not None and finished['finished_at'] is not None
    raw = persisted(pg, 'provider_results', id=result.id)[0]
    assert raw['validation_status'] == 'valid' and raw['raw_output'] == provider.output
    assert len(persisted(pg, 'task_attempts', task_id=task.id)) == 2
    assert persisted(pg, 'task_attempts', id=newer_id)[0]['state'] == 'cancelled'
    assert persisted(pg, 'outbox_events', aggregate_id=task.id)[-1]['payload']['may_have_charged'] is True
    assert worker(task_db, scheduler, provider).run_once() is False
    assert provider.calls == 1


def test_cancel_locks_first_reclaim_skips_then_local_result_finishes(
        task_db, scheduler, owners, pg):
    task, attempt, result, provider = saved_result(task_db, scheduler, owners.a, pg)
    locked, release = Event(), Event()

    def cancel_actor():
        with task_db.transaction(owners.a) as tx:
            pending = tx.tasks.cancel(task.id, uuid4())
            locked.set()
            assert release.wait(10)
            return pending

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(cancel_actor)
        try:
            assert locked.wait(10)
            # SKIP LOCKED must return promptly while another connection really
            # holds the cancellation row lock; it must not send or lose the raw.
            assert worker(task_db, scheduler, provider).run_once() is False
            assert provider.calls == 1
        finally:
            release.set()
        assert future.result(timeout=10).state == 'queued'
    assert worker(task_db, scheduler, provider).run_once() is True
    assert_finished(pg, task, attempt, result, provider)


def test_reclaim_locks_first_cancel_waits_then_local_result_finishes(
        task_db, scheduler, owners, pg):
    task, attempt, result, provider = saved_result(task_db, scheduler, owners.a, pg)
    claimed, cancel_started, release = Event(), Event(), Event()
    pids = {}

    def claim_actor():
        with scheduler.engine.begin() as connection:
            pids['claim'] = connection.scalar(sa.text('SELECT pg_backend_pid()'))
            rows = connection.execute(sa.text(
                'SELECT * FROM classroom_web_claim_tasks(:worker,30,1)'), {'worker': WORKER}).mappings().all()
            assert len(rows) == 1 and rows[0]['id'] == task.id
            claimed.set()
            assert release.wait(10)

    def cancel_actor():
        assert claimed.wait(10)
        with task_db.transaction(owners.a) as tx:
            pids['cancel'] = tx.connection.scalar(sa.text('SELECT pg_backend_pid()'))
            cancel_started.set()
            return tx.tasks.cancel(task.id, uuid4())

    with ThreadPoolExecutor(max_workers=2) as pool:
        claim_future, cancel_future = pool.submit(claim_actor), pool.submit(cancel_actor)
        try:
            assert cancel_started.wait(10)
            blocked = False
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                with pg.admin.connect() as connection:
                    blocking = connection.scalar(sa.text('SELECT pg_blocking_pids(:pid)'), {'pid': pids['cancel']})
                if pids['claim'] in blocking:
                    blocked = True
                    break
                time.sleep(0.01)
            assert blocked, 'cancellation must actually wait on the claimant task lock'
        finally:
            release.set()
        claim_future.result(timeout=10)
        assert cancel_future.result(timeout=10).state == 'cancel_requested'
    assert provider.calls == 1
    expire(pg, task.id)
    assert worker(task_db, scheduler, provider).run_once() is True
    assert_finished(pg, task, attempt, result, provider)
