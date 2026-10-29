"""PR-02b real PostgreSQL races, persisted crash boundaries and tenant isolation.

All provider text, users and process credentials in this file are synthetic.
No external provider adapter or billable endpoint is imported.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier, Event
import time
from uuid import uuid4

import pytest
import sqlalchemy as sa

from classroom_web.domain.data import Gone, NotFound, OwnerContext, Resource
from classroom_web.domain.tasks import (
    IdempotencyMismatch, ResultConflict, ResyncRequired, StaleFence,
)
from classroom_web.infrastructure.database import Database
from classroom_web.infrastructure.task_scheduler import PgTaskScheduler
from classroom_web.infrastructure.task_schema import tables
from classroom_web.test_support import SYNTHETIC_PASSWORD


CURSOR_KEY = b"pr02b-test-only-stable-cursor-key-0001"
INPUT = {"schema_version": 1}
WORKER = "synthetic-worker"


@pytest.fixture
def task_db(pg, owners):
    database = Database(pg.runtime_url.render_as_string(hide_password=False),
                        cursor_secret=CURSOR_KEY)
    try:
        yield database
    finally:
        database.dispose()
        # Every test has new owners. Retain their complete synthetic ledgers,
        # but do not let a later test's global scheduler consume their leftovers.
        with pg.admin.begin() as connection:
            connection.execute(tables["tasks"].update().where(
                tables["tasks"].c.owner_id.in_([owners.a.owner_id, owners.b.owner_id])
            ).values(not_before=datetime.now(timezone.utc) + timedelta(days=365)))


@pytest.fixture
def scheduler(pg):
    login = "pr02a_scheduler_" + uuid4().hex
    with pg.admin.begin() as connection:
        connection.exec_driver_sql(
            f'CREATE ROLE "{login}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
        connection.exec_driver_sql(f'GRANT classroom_web_scheduler TO "{login}"')
    url = pg.runtime_url.set(username=login, password=SYNTHETIC_PASSWORD)
    value = PgTaskScheduler(url.render_as_string(hide_password=False))
    try:
        yield value
    finally:
        value.dispose()
        with pg.admin.begin() as connection:
            connection.exec_driver_sql(f'DROP ROLE "{login}"')


def enqueue(task_db, owner, **kwargs):
    with task_db.transaction(owner) as tx:
        return tx.tasks.enqueue("ai_question", INPUT, op_id=uuid4(), **kwargs)


def claim_one(scheduler, task):
    rows = scheduler.claim(WORKER, lease_seconds=30)
    assert len(rows) == 1 and rows[0].id == task.id
    return rows[0]


def expire(pg, task_id):
    with pg.admin.begin() as connection:
        connection.execute(tables["tasks"].update().where(tables["tasks"].c.id == task_id)
                           .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))


def persisted(pg, table, **filters):
    target = tables[table]
    with pg.admin.connect() as connection:
        query = sa.select(target).filter_by(**filters)
        if table == "outbox_events":
            query = query.order_by(target.c.event_sequence)
        return connection.execute(query).mappings().all()


def prepared(task_db, owner, task, claim):
    with task_db.transaction(owner) as tx:
        return tx.tasks.prepare_attempt(task.id, claim.lease_token, WORKER)


def submitting(task_db, owner, task, attempt, claim):
    with task_db.transaction(owner) as tx:
        tx.tasks.mark_submitting(task.id, attempt.id, claim.lease_token, WORKER)


def test_task_receipt_and_outbox_commit_atomically(task_db, owners, pg):
    task = enqueue(task_db, owners.a)
    assert task.state == "queued" and task.revision == 1 and task.lease_token == 0
    assert len(persisted(pg, "tasks", id=task.id)) == 1
    receipt = persisted(pg, "operation_receipts", resource_id=task.id)
    events = persisted(pg, "outbox_events", aggregate_id=task.id)
    assert len(receipt) == len(events) == 1
    assert receipt[0]["response_status"] == 202
    assert receipt[0]["response_body"] == {"task_id": str(task.id)}
    assert events[0]["event_type"] == "task.changed"
    assert "input_ref" not in events[0]["payload"]


def test_rollback_removes_task_receipt_event_and_sequence(task_db, owners, pg):
    with pytest.raises(RuntimeError, match="rollback"):
        with task_db.transaction(owners.a) as tx:
            task = tx.tasks.enqueue("ai_question", INPUT, op_id=uuid4())
            raise RuntimeError("rollback")
    for name, filters in (("tasks", {"id": task.id}),
                          ("operation_receipts", {"resource_id": task.id}),
                          ("outbox_events", {"aggregate_id": task.id}),
                          ("owner_event_counters", {"owner_id": owners.a.owner_id})):
        assert persisted(pg, name, **filters) == []


def test_same_operation_replays_one_task_and_different_body_rejects(task_db, owners, pg):
    op_id = uuid4()
    with task_db.transaction(owners.a) as tx:
        first = tx.tasks.enqueue("ai_question", INPUT, op_id=op_id)
    with task_db.transaction(owners.a) as tx:
        repeated = tx.tasks.enqueue("ai_question", INPUT, op_id=op_id)
    assert repeated.id == first.id
    with pytest.raises(IdempotencyMismatch):
        with task_db.transaction(owners.a) as tx:
            tx.tasks.enqueue("review_summary", INPUT, op_id=op_id)
    assert len(persisted(pg, "tasks", owner_id=owners.a.owner_id)) == 1
    assert len(persisted(pg, "outbox_events", owner_id=owners.a.owner_id)) == 1


def test_concurrent_identical_operation_creates_exactly_one_task(task_db, owners, pg):
    gate = Barrier(2)
    op_id = uuid4()

    def execute():
        gate.wait(timeout=10)
        with task_db.transaction(owners.a) as tx:
            return tx.tasks.enqueue("ai_question", INPUT, op_id=op_id).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute) for _ in range(2)]
        ids = [future.result(timeout=15) for future in futures]
    assert ids[0] == ids[1]
    assert len(persisted(pg, "operation_receipts", owner_id=owners.a.owner_id)) == 1
    assert len(persisted(pg, "outbox_events", owner_id=owners.a.owner_id)) == 1


def test_operation_id_is_scoped_by_owner(task_db, owners):
    op_id = uuid4()
    with task_db.transaction(owners.a) as tx:
        a = tx.tasks.enqueue("ai_question", INPUT, op_id=op_id)
    with task_db.transaction(owners.b) as tx:
        b = tx.tasks.enqueue("ai_question", INPUT, op_id=op_id)
    assert a.id != b.id


def test_cross_owner_and_empty_scope_cannot_read_task_or_result(task_db, owners, pg):
    task = enqueue(task_db, owners.a)
    with task_db.transaction(owners.b) as tx:
        with pytest.raises(NotFound):
            tx.tasks.get(task.id)
        for name in tables:
            assert tx.connection.execute(sa.select(tables[name])).all() == []
    with task_db.engine.begin() as connection:
        connection.execute(sa.text("SELECT set_config('app.owner_id','',true)"))
        for name in tables:
            assert connection.execute(sa.select(tables[name])).all() == []
    with pytest.raises(sa.exc.DBAPIError):
        with task_db.transaction(owners.b) as tx:
            tx.connection.execute(tables["tasks"].insert().values(
                id=uuid4(), owner_id=owners.a.owner_id, state="queued", kind="export", input_ref=INPUT))


def test_scheduler_can_claim_only_metadata_and_cannot_select_body(scheduler, task_db, owners, pg):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    assert not hasattr(claim, "input_ref")
    # Check SQL privileges independently from the Python return projection.
    with scheduler.engine.connect() as connection:
        assert not connection.scalar(sa.text(
            "SELECT has_table_privilege(current_user,'tasks','SELECT')"))
        assert not connection.scalar(sa.text(
            "SELECT has_table_privilege(current_user,'provider_results','SELECT')"))
    with pytest.raises(sa.exc.DBAPIError):
        with scheduler.engine.begin() as connection:
            connection.execute(sa.text("SELECT input_ref FROM tasks"))
    with pytest.raises(sa.exc.DBAPIError):
        with task_db.engine.begin() as connection:
            connection.execute(sa.text("SELECT * FROM classroom_web_claim_tasks('forbidden',30,1)"))
    events = persisted(pg, "outbox_events", aggregate_id=task.id)
    assert [event["payload"]["state"] for event in events] == ["queued", "running"]


def test_skip_locked_multiple_workers_never_double_claim(scheduler, task_db, owners, pg):
    tasks = [enqueue(task_db, owners.a) for _ in range(12)]
    gate = Barrier(4)

    def take(index):
        gate.wait(timeout=10)
        return scheduler.claim("worker-" + str(index), lease_seconds=30, batch_size=3)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(take, index) for index in range(4)]
        claims = [claim for future in futures for claim in future.result(timeout=15)]
    assert len(claims) == 12
    assert {claim.id for claim in claims} == {task.id for task in tasks}
    assert all(claim.lease_token == 1 for claim in claims)
    assert scheduler.claim("empty", lease_seconds=30, batch_size=3) == []
    assert all(len(persisted(pg, "outbox_events", aggregate_id=task.id)) == 2 for task in tasks)


def test_skip_locked_does_not_wait_for_locked_queue_row(scheduler, task_db, owners, pg):
    first, second = [enqueue(task_db, owners.a) for _ in range(2)]
    with pg.admin.connect() as connection, connection.begin():
        connection.execute(sa.select(tables["tasks"]).where(tables["tasks"].c.id == first.id)
                           .with_for_update())
        claims = scheduler.claim(WORKER, lease_seconds=30)
        assert len(claims) == 1 and claims[0].id == second.id


def test_unexpired_lease_cannot_be_recovered(task_db, scheduler, owners):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    with pytest.raises(StaleFence):
        with task_db.transaction(owners.a) as tx:
            tx.tasks.recover(task.id, claim.lease_token)


def test_heartbeat_requires_current_worker_fence_and_unexpired_lease(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    for token, worker in ((claim.lease_token + 1, WORKER), (claim.lease_token, "other")):
        with pytest.raises(StaleFence):
            with task_db.transaction(owners.a) as tx:
                tx.tasks.heartbeat(task.id, token, worker, lease_seconds=60)
    with task_db.transaction(owners.a) as tx:
        tx.tasks.heartbeat(task.id, claim.lease_token, WORKER, lease_seconds=60)
    assert persisted(pg, "tasks", id=task.id)[0]["lease_expires_at"] > claim.lease_expires_at
    expire(pg, task.id)
    with pytest.raises(StaleFence):
        with task_db.transaction(owners.a) as tx:
            tx.tasks.heartbeat(task.id, claim.lease_token, WORKER)


def test_unsent_attempt_survives_recovery_and_new_fence(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    first_claim = claim_one(scheduler, task)
    first_attempt = prepared(task_db, owners.a, task, first_claim)
    expire(pg, task.id)
    assert any(row.id == task.id for row in scheduler.expired())
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.recover(task.id, first_claim.lease_token).state == "queued"
    second_claim = claim_one(scheduler, task)
    second_attempt = prepared(task_db, owners.a, task, second_claim)
    assert second_claim.lease_token > first_claim.lease_token
    assert second_attempt.id == first_attempt.id
    assert len(persisted(pg, "task_attempts", task_id=task.id)) == 1
    with pytest.raises(StaleFence):
        with task_db.transaction(owners.a) as tx:
            tx.tasks.mark_submitting(task.id, first_attempt.id, first_claim.lease_token, WORKER)


def test_submitting_expiry_becomes_unknown_and_never_claims_again(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    submitting(task_db, owners.a, task, attempt, claim)
    expire(pg, task.id)
    with task_db.transaction(owners.a) as tx:
        result = tx.tasks.recover(task.id, claim.lease_token)
        assert result.state == "unknown"
    assert scheduler.claim("restart", lease_seconds=30) == []
    assert persisted(pg, "task_attempts", id=attempt.id)[0]["state"] == "unknown"
    event = persisted(pg, "outbox_events", aggregate_id=task.id)[-1]
    assert event["payload"]["may_have_charged"] is True


def test_explicit_reconcile_publishes_late_raw_once_without_new_attempt(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    submitting(task_db, owners.a, task, attempt, claim)
    with task_db.transaction(owners.a) as tx:
        unknown = tx.tasks.mark_unknown(task.id, attempt.id, claim.lease_token, WORKER)
    with task_db.transaction(owners.a) as tx:
        raw = tx.tasks.store_result(task.id, attempt.id, '{"answer":"late confirmed synthetic"}')
    count_before = len(persisted(pg, "outbox_events", aggregate_id=task.id))
    op_id = uuid4()
    with task_db.transaction(owners.a) as tx:
        final = tx.tasks.reconcile_result(task.id, op_id)
    assert final.state == "succeeded" and final.lease_token > unknown.lease_token
    assert final.may_have_charged is True
    assert len(persisted(pg, "task_attempts", task_id=task.id)) == 1
    assert persisted(pg, "provider_results", id=raw.id)[0]["validation_status"] == "valid"
    assert len(persisted(pg, "outbox_events", aggregate_id=task.id)) == count_before + 1
    with task_db.transaction(owners.a) as tx:
        replay = tx.tasks.reconcile_result(task.id, op_id)
    assert replay.id == final.id and replay.revision == final.revision
    assert len(persisted(pg, "outbox_events", aggregate_id=task.id)) == count_before + 1
    assert len(persisted(pg, "operation_receipts", operation="reconcileTask", op_id=op_id)) == 1
    assert scheduler.claim("must-not-resubmit-reconciled") == []


def test_explicit_reconcile_without_raw_keeps_unknown_without_new_submission(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    submitting(task_db, owners.a, task, attempt, claim)
    with task_db.transaction(owners.a) as tx:
        tx.tasks.mark_unknown(task.id, attempt.id, claim.lease_token, WORKER)
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.reconcile_result(task.id, uuid4()).state == "unknown"
    attempts = persisted(pg, "task_attempts", task_id=task.id)
    assert len(attempts) == 1 and attempts[0]["id"] == attempt.id and attempts[0]["state"] == "unknown"
    assert persisted(pg, "provider_results", attempt_id=attempt.id) == []
    assert scheduler.claim("cannot-query-by-resubmitting") == []


def test_explicit_reconcile_refuses_conflicting_raw_without_publication(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    submitting(task_db, owners.a, task, attempt, claim)
    with task_db.transaction(owners.a) as tx:
        first = tx.tasks.store_result(task.id, attempt.id, '{"answer":"first synthetic"}')
    with task_db.transaction(owners.a) as tx:
        tx.tasks.store_result(task.id, attempt.id, '{"answer":"conflicting synthetic"}')
    before = len(persisted(pg, "outbox_events", aggregate_id=task.id))
    op_id = uuid4()
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.reconcile_result(task.id, op_id).state == "unknown"
    stored = persisted(pg, "provider_results", id=first.id)[0]
    assert stored["raw_output"] == '{"answer":"first synthetic"}'
    assert stored["validation_status"] == "pending" and stored["conflict_sha256"] is not None
    assert persisted(pg, "tasks", id=task.id)[0]["state"] == "unknown"
    assert len(persisted(pg, "outbox_events", aggregate_id=task.id)) == before
    assert len(persisted(pg, "operation_receipts", operation="reconcileTask", op_id=op_id)) == 1
    assert scheduler.claim("cannot-retry-conflict") == []


def test_explicit_reconcile_reauthorizes_deleted_lineage(task_db, scheduler, owners, pg, tree):
    task = enqueue(task_db, owners.a, class_session_id=tree.classroom.id)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    submitting(task_db, owners.a, task, attempt, claim)
    with task_db.transaction(owners.a) as tx:
        tx.tasks.mark_unknown(task.id, attempt.id, claim.lease_token, WORKER)
    with task_db.transaction(owners.a) as tx:
        raw = tx.tasks.store_result(task.id, attempt.id, '{"answer":"before revocation"}')
    with task_db.transaction(owners.a) as tx:
        tx.tombstones.revoke(Resource.CLASS_SESSION, tree.classroom.id, 1, ledger_sequence=1)
    with pytest.raises(Gone):
        with task_db.transaction(owners.a) as tx:
            tx.tasks.reconcile_result(task.id, uuid4())
    assert persisted(pg, "provider_results", id=raw.id)[0]["validation_status"] == "pending"
    assert persisted(pg, "tasks", id=task.id)[0]["state"] == "unknown"


def test_aging_prevents_old_low_priority_task_from_starving(task_db, scheduler, owners, pg):
    oldest = enqueue(task_db, owners.a, priority=-100)
    newest = enqueue(task_db, owners.a, priority=100)
    with pg.admin.begin() as connection:
        connection.execute(tables["tasks"].update().where(tables["tasks"].c.id == oldest.id)
            .values(not_before=datetime.now(timezone.utc) - timedelta(minutes=11)))
    claims = scheduler.claim(WORKER, lease_seconds=30, batch_size=1)
    assert len(claims) == 1 and claims[0].id == oldest.id
    later = scheduler.claim(WORKER, lease_seconds=30, batch_size=1)
    assert len(later) == 1 and later[0].id == newest.id


def test_persisted_raw_result_recovers_without_second_attempt(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    submitting(task_db, owners.a, task, attempt, claim)
    with task_db.transaction(owners.a) as tx:
        result = tx.tasks.store_result(task.id, attempt.id, '{"answer":"synthetic"}')
    expire(pg, task.id)
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.recover(task.id, claim.lease_token).state == "queued"
    new_claim = claim_one(scheduler, task)
    assert persisted(pg, "outbox_events", aggregate_id=task.id)[-1]["payload"]["may_have_charged"] is True
    assert prepared(task_db, owners.a, task, new_claim).id == attempt.id
    with task_db.transaction(owners.a) as tx:
        final = tx.tasks.publish_result(task.id, attempt.id, new_claim.lease_token, WORKER,
                                       validator=json.loads)
    assert final.state == "succeeded"
    assert len(persisted(pg, "task_attempts", task_id=task.id)) == 1
    assert persisted(pg, "provider_results", id=result.id)[0]["validation_status"] == "valid"


def test_duplicate_raw_result_is_idempotent_but_conflicting_body_is_retained(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    submitting(task_db, owners.a, task, attempt, claim)
    with task_db.transaction(owners.a) as tx:
        first = tx.tasks.store_result(task.id, attempt.id, "synthetic original")
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.store_result(task.id, attempt.id, "synthetic original").id == first.id
    with task_db.transaction(owners.a) as tx:
        conflict = tx.tasks.store_result(task.id, attempt.id, "different synthetic output")
    assert conflict.raw_output == "synthetic original"
    stored = persisted(pg, "provider_results", id=first.id)[0]
    assert stored["raw_output"] == "synthetic original"
    assert stored["conflict_sha256"] is not None and stored["conflict_detected_at"] is not None
    assert len(persisted(pg, "provider_results", attempt_id=attempt.id)) == 1
    assert persisted(pg, "tasks", id=task.id)[0]["state"] == "unknown"
    with pytest.raises((StaleFence, ResultConflict)):
        with task_db.transaction(owners.a) as tx:
            tx.tasks.publish_result(task.id, attempt.id, claim.lease_token, WORKER)
    assert scheduler.claim("must-not-resend-conflict") == []


def test_attempt_cannot_reference_another_attempts_result_even_by_direct_sql(
        task_db, scheduler, owners, pg):
    a, b = [enqueue(task_db, owners.a) for _ in range(2)]
    claims = scheduler.claim(WORKER, lease_seconds=30, batch_size=2)
    claim_by_id = {claim.id: claim for claim in claims}
    first = prepared(task_db, owners.a, a, claim_by_id[a.id])
    second = prepared(task_db, owners.a, b, claim_by_id[b.id])
    submitting(task_db, owners.a, a, first, claim_by_id[a.id])
    submitting(task_db, owners.a, b, second, claim_by_id[b.id])
    with task_db.transaction(owners.a) as tx:
        second_result = tx.tasks.store_result(b.id, second.id, '{"owner":"same, attempt different"}')
    with pytest.raises(sa.exc.IntegrityError) as caught:
        with task_db.transaction(owners.a) as tx:
            tx.connection.execute(tables["task_attempts"].update().where(
                tables["task_attempts"].c.id == first.id).values(raw_output_ref=second_result.id))
    assert caught.value.orig.sqlstate == "23514"
    assert persisted(pg, "task_attempts", id=first.id)[0]["raw_output_ref"] is None
    assert persisted(pg, "provider_results", id=second_result.id)[0]["attempt_id"] == second.id


def test_unknown_task_and_attempt_cannot_reset_to_unsent_by_direct_sql(
        task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    submitting(task_db, owners.a, task, attempt, claim)
    with task_db.transaction(owners.a) as tx:
        unknown = tx.tasks.mark_unknown(task.id, attempt.id, claim.lease_token, WORKER)
    with pytest.raises(sa.exc.IntegrityError) as caught:
        with task_db.transaction(owners.a) as tx:
            tx.connection.execute(tables["tasks"].update().where(tables["tasks"].c.id == task.id)
                                   .values(state="queued", revision=unknown.revision + 1))
    assert caught.value.orig.sqlstate == "23514"
    with pytest.raises(sa.exc.IntegrityError) as caught:
        with task_db.transaction(owners.a) as tx:
            tx.connection.execute(tables["task_attempts"].update().where(
                tables["task_attempts"].c.id == attempt.id).values(state="planned", submitted_at=None))
    assert caught.value.orig.sqlstate == "23514"
    assert persisted(pg, "tasks", id=task.id)[0]["state"] == "unknown"


def test_invalid_raw_result_preserves_provider_success_and_fails_locally(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    submitting(task_db, owners.a, task, attempt, claim)
    with task_db.transaction(owners.a) as tx:
        result = tx.tasks.store_result(task.id, attempt.id, "not json")
    with task_db.transaction(owners.a) as tx:
        final = tx.tasks.publish_result(task.id, attempt.id, claim.lease_token, WORKER,
                                       validator=json.loads)
    assert final.state == "failed" and final.error_code == "OUTPUT_INVALID"
    raw = persisted(pg, "provider_results", id=result.id)[0]
    assert raw["raw_output"] == "not json" and raw["validation_status"] == "invalid"
    assert persisted(pg, "task_attempts", id=attempt.id)[0]["provider_outcome"] == "succeeded"
    assert scheduler.claim("repair-must-not-run") == []


def test_late_result_can_be_saved_but_old_fence_cannot_publish(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    first_claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, first_claim)
    submitting(task_db, owners.a, task, attempt, first_claim)
    expire(pg, task.id)
    with task_db.transaction(owners.a) as tx:
        tx.tasks.recover(task.id, first_claim.lease_token)
    with task_db.transaction(owners.a) as tx:
        tx.tasks.store_result(task.id, attempt.id, '{"answer":"late synthetic"}')
    with pytest.raises(StaleFence):
        with task_db.transaction(owners.a) as tx:
            tx.tasks.publish_result(task.id, attempt.id, first_claim.lease_token, WORKER,
                                    validator=json.loads)
    assert persisted(pg, "tasks", id=task.id)[0]["state"] == "unknown"
    assert persisted(pg, "provider_results", attempt_id=attempt.id)[0]["validation_status"] == "pending"


def test_queued_cancellation_is_receipted_and_cannot_be_claimed(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    op_id = uuid4()
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.cancel(task.id, op_id).state == "cancelled"
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.cancel(task.id, op_id).state == "cancelled"
    assert scheduler.claim(WORKER) == []
    assert len(persisted(pg, "operation_receipts", resource_id=task.id)) == 2


def test_cancel_after_send_does_not_claim_zero_cost_or_auto_resend(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    submitting(task_db, owners.a, task, attempt, claim)
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.cancel(task.id, uuid4()).state == "cancel_requested"
    expire(pg, task.id)
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.recover(task.id, claim.lease_token).state == "unknown"
    assert scheduler.claim("cannot-resend") == []
    assert persisted(pg, "outbox_events", aggregate_id=task.id)[-1]["payload"]["may_have_charged"] is True


def test_deleted_classroom_blocks_receipt_replay_dispatch_and_read(task_db, scheduler, owners, pg, tree):
    op_id = uuid4()
    with task_db.transaction(owners.a) as tx:
        task = tx.tasks.enqueue("ai_question", INPUT, op_id=op_id,
                                class_session_id=tree.classroom.id)
    claim = claim_one(scheduler, task)
    with task_db.transaction(owners.a) as tx:
        tx.tombstones.revoke(Resource.CLASS_SESSION, tree.classroom.id, 1, ledger_sequence=1)
    with pytest.raises(Gone):
        with task_db.transaction(owners.a) as tx:
            tx.tasks.get(task.id)
    with pytest.raises(Gone):
        with task_db.transaction(owners.a) as tx:
            tx.tasks.enqueue("ai_question", INPUT, op_id=op_id,
                             class_session_id=tree.classroom.id)
    with pytest.raises(Gone):
        with task_db.transaction(owners.a) as tx:
            tx.tasks.prepare_attempt(task.id, claim.lease_token, WORKER)
    assert persisted(pg, "task_attempts", task_id=task.id) == []


def test_events_replay_same_ids_after_ack_and_cursor_does_not_cross_owner(task_db, owners):
    first, second = [enqueue(task_db, owners.a) for _ in range(2)]
    with task_db.transaction(owners.a) as tx:
        page = tx.events.replay(limit=1)
        assert len(page.events) == 1
        event = page.events[0]
        assert event["data"]["task_id"] == str(first.id)
        assert set(event) == {"protocol_version", "type", "sent_at", "data", "event_id", "cursor"}
        assert set(event["data"]) == {"task_id", "class_session_id", "revision", "state",
                                      "error_code", "may_have_charged"}
        tx.events.ack(event["event_id"])
    with task_db.transaction(owners.a) as tx:
        assert tx.events.replay(limit=1).events[0]["event_id"] == event["event_id"]
        next_page = tx.events.replay(page.cursor)
        assert [item["data"]["task_id"] for item in next_page.events] == [str(second.id)]
        assert tx.events.replay(next_page.cursor).events == []
    with pytest.raises(ResyncRequired):
        with task_db.transaction(owners.b) as tx:
            tx.events.replay(page.cursor)


def test_tampered_and_retention_expired_cursors_require_resync(task_db, owners, pg):
    for _ in range(3):
        enqueue(task_db, owners.a)
    with task_db.transaction(owners.a) as tx:
        cursor = tx.events.replay(limit=1).cursor
    changed = len(cursor) // 2
    tampered = cursor[:changed] + ("A" if cursor[changed] != "A" else "B") + cursor[changed + 1:]
    with pytest.raises(ResyncRequired):
        with task_db.transaction(owners.a) as tx:
            tx.events.replay(tampered)
    with pg.admin.begin() as connection:
        connection.execute(tables["outbox_events"].delete().where(
            tables["outbox_events"].c.owner_id == owners.a.owner_id,
            tables["outbox_events"].c.event_sequence <= 2))
    with pytest.raises(ResyncRequired):
        with task_db.transaction(owners.a) as tx:
            tx.events.replay(cursor)


def test_owner_event_sequence_serializes_actual_commit_order(task_db, owners, pg):
    # Seed the row first so the test specifically observes a counter-row wait,
    # rather than relying on a UNIQUE conflict during lazy counter creation.
    enqueue(task_db, owners.a)
    entered = Event()
    release = Event()
    second_started = Event()
    actor = {}

    def first():
        with task_db.transaction(owners.a) as tx:
            tx.tasks.enqueue("ai_question", INPUT, op_id=uuid4())
            actor["first"] = tx.connection.scalar(sa.text("SELECT pg_backend_pid()"))
            entered.set()
            assert release.wait(10)

    def second():
        assert entered.wait(10)
        with task_db.transaction(owners.a) as tx:
            actor["second"] = tx.connection.scalar(sa.text("SELECT pg_backend_pid()"))
            second_started.set()
            tx.tasks.enqueue("ai_question", INPUT, op_id=uuid4())

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first)
        second_future = pool.submit(second)
        try:
            assert second_started.wait(10)
            observed = False
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                with pg.admin.connect() as connection:
                    blockers = connection.scalar(sa.text("SELECT pg_blocking_pids(:pid)"),
                                                 {"pid": actor["second"]})
                if actor["first"] in blockers:
                    observed = True
                    break
                time.sleep(0.01)
            assert observed, "second transaction must actually wait for first owner's counter lock"
            # A third connection sees only committed sequence 1 during the wait.
            rows = persisted(pg, "outbox_events", owner_id=owners.a.owner_id)
            assert [row["event_sequence"] for row in rows] == [1]
        finally:
            release.set()
        first_future.result(timeout=10)
        second_future.result(timeout=10)
    rows = persisted(pg, "outbox_events", owner_id=owners.a.owner_id)
    assert sorted(row["event_sequence"] for row in rows) == [1, 2, 3]
    with task_db.transaction(owners.a) as tx:
        assert len(tx.events.replay().events) == 3


def test_bootstrap_projection_and_watermark_share_repeatable_read_snapshot(task_db, owners):
    first = enqueue(task_db, owners.a)
    with task_db.transaction(owners.a, isolation_level="REPEATABLE READ") as snapshot:
        before = snapshot.events.watermark()
        visible_before = snapshot.connection.execute(sa.select(tables["tasks"].c.id)).scalars().all()
        assert visible_before == [first.id]
        second = enqueue(task_db, owners.a)
        assert snapshot.events.watermark() == before
        assert snapshot.connection.execute(sa.select(tables["tasks"].c.id)).scalars().all() == [first.id]
    with task_db.transaction(owners.a) as current:
        assert current.events.watermark() != before
        delta = current.events.replay(before)
        assert [event["data"]["task_id"] for event in delta.events] == [str(second.id)]


CRASH_SCRIPT = r'''
import json, os, pathlib, sys, time
from uuid import UUID
from classroom_web.domain.data import OwnerContext
from classroom_web.infrastructure.database import Database
from classroom_web.test_support import validate_test_database_target
stage, ready, owner_id, task_id, fence = sys.argv[1:]
url = validate_test_database_target(os.environ["PR02B_CRASH_RUNTIME_URL"])
db = Database(url.render_as_string(hide_password=False), cursor_secret=b"pr02b-test-only-stable-cursor-key-0001")
owner, task_id, fence = OwnerContext(UUID(owner_id)), UUID(task_id), int(fence)
with db.transaction(owner) as tx:
    attempt = tx.tasks.prepare_attempt(task_id, fence, "synthetic-worker")
if stage != "planned":
    with db.transaction(owner) as tx:
        tx.tasks.mark_submitting(task_id, attempt.id, fence, "synthetic-worker")
if stage == "sent":
    # This fixed local mock is the only provider. A disk receipt survives the kill
    # even though the task engine has received no result.
    pathlib.Path(ready + ".mock-sent").write_text("one simulated send", encoding="utf-8")
if stage == "result":
    with db.transaction(owner) as tx:
        tx.tasks.store_result(task_id, attempt.id, '{"answer":"persisted synthetic"}')
checkpoint = pathlib.Path(ready + '.writing')
checkpoint.write_text(str(attempt.id), encoding='ascii')
checkpoint.replace(ready)
while True:
    time.sleep(1)
'''


@pytest.mark.parametrize("stage,expected", (("planned", "queued"),
    ("submitting", "unknown"), ("sent", "unknown"), ("result", "queued")))
def test_os_kill_and_new_database_instance_recover_only_safe_work(
        stage, expected, task_db, scheduler, owners, pg, tmp_path):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    helper = tmp_path / "synthetic_crash_worker.py"
    ready = tmp_path / "committed-boundary"
    helper.write_text(CRASH_SCRIPT, encoding="utf-8")
    env = dict(os.environ)
    env["PR02B_CRASH_RUNTIME_URL"] = pg.runtime_url.render_as_string(hide_password=False)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (
        env.get("PYTHONPATH"), str(Path(__file__).resolve().parents[2]))))
    process = subprocess.Popen([sys.executable, str(helper), stage, str(ready),
                                str(owners.a.owner_id), str(task.id), str(claim.lease_token)],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        deadline = time.monotonic() + 12
        while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.01)
        assert ready.exists(), process.communicate(timeout=2) if process.poll() is not None else "checkpoint timeout"
        process.kill()
        process.communicate(timeout=5)
        assert process.returncode != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    expire(pg, task.id)
    restarted = Database(pg.runtime_url.render_as_string(hide_password=False), cursor_secret=CURSOR_KEY)
    try:
        with restarted.transaction(owners.a) as tx:
            assert tx.tasks.recover(task.id, claim.lease_token).state == expected
        if expected == "unknown":
            assert scheduler.claim("new-process") == []
        else:
            replacement = claim_one(scheduler, task)
            attempt = prepared(restarted, owners.a, task, replacement)
            assert str(attempt.id) == ready.read_text("ascii")
            if stage == "result":
                with restarted.transaction(owners.a) as tx:
                    assert tx.tasks.publish_result(task.id, attempt.id, replacement.lease_token,
                        WORKER, validator=json.loads).state == "succeeded"
        if stage == "sent":
            assert Path(str(ready) + ".mock-sent").read_text("utf-8") == "one simulated send"
        assert len(persisted(pg, "task_attempts", task_id=task.id)) == 1
    finally:
        restarted.dispose()
