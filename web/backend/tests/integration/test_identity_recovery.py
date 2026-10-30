"""Real PostgreSQL recovery after login-bearing identity evidence is revoked.

Provider evidence contains only synthetic IDs and an input fingerprint. These
tests never import an external provider, use an account secret or send mail.
"""
from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa

from classroom_web.application.task_worker import MockProvider, TaskWorker
from classroom_web.domain.data import InactiveOwner
from classroom_web.domain.tasks import Claim, validate_json_output
from classroom_web.infrastructure.identity_passwords import Argon2PasswordHasher
from classroom_web.infrastructure.local_reconciler import PgLocalResultReconciler
from classroom_web.infrastructure.schema import tables as catalog_tables
from classroom_web.infrastructure.task_schema import tables

from test_persistent_tasks import (
    WORKER, claim_one, enqueue, expire, persisted, prepared, scheduler,
    submitting, task_db,
)
from test_task_worker import worker


class RecordingProvider(MockProvider):
    """Record the exact synthetic submission boundary across worker restarts."""

    def __init__(self):
        super().__init__(output='{"synthetic":"identity-recovery"}')
        self.evidence = []

    def submit(self, task, attempt):
        encoded = json.dumps(task.input_ref, sort_keys=True, separators=(",", ":")).encode()
        self.evidence.append({"task_id": str(task.id), "attempt_id": str(attempt.id),
                              "input_sha256": hashlib.sha256(encoded).hexdigest()})
        return super().submit(task, attempt)


def complete_identity(pg, owner):
    """Replace only this fixture's opaque owner shell with synthetic credentials."""
    users = catalog_tables["users"]
    with pg.admin.begin() as connection:
        connection.execute(users.update().where(users.c.id == owner.owner_id).values(
            normalized_email=f"synthetic-{owner.owner_id.hex}@example.test",
            password_hash=Argon2PasswordHasher().hash("Synthetic-recovery-password"),
            email_verified_at=sa.func.clock_timestamp()))


def invalidate_identity(pg, owner, field):
    users = catalog_tables["users"]
    values = ({"state": "disabled"} if field == "disabled" else
              {"deleted_at": sa.func.clock_timestamp()} if field == "deleted" else {field: None})
    with pg.admin.begin() as connection:
        connection.execute(users.update().where(users.c.id == owner.owner_id).values(**values))


@pytest.mark.parametrize("missing", ("email_verified_at", "password_hash", "disabled", "deleted"))
@pytest.mark.parametrize("stage", ("planned", "sent_accepted"))
@pytest.mark.parametrize("task_state", ("running", "cancel_requested"))
def test_expired_invalid_identity_terminates_without_resubmission(
        missing, stage, task_state, task_db, scheduler, owners, pg):
    complete_identity(pg, owners.a)
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    provider = RecordingProvider()
    sent = stage == "sent_accepted"
    if sent:
        submitting(task_db, owners.a, task, attempt, claim)
        provider.submit(task, attempt)
        # Simulate a committed acceptance receipt with the final result absent.
        with task_db.transaction(owners.a) as tx:
            tx.connection.execute(tables["task_attempts"].update().where(
                tables["task_attempts"].c.id == attempt.id).values(
                state="accepted", provider_request_id="synthetic-accepted-request"))
    if task_state == "cancel_requested":
        # A persisted historical cancellation must converge even when its owner
        # can no longer enter the ordinary cancellation/recovery transaction.
        with pg.admin.begin() as connection:
            connection.execute(tables["tasks"].update().where(tables["tasks"].c.id == task.id).values(
                state="cancel_requested", revision=tables["tasks"].c.revision + 1,
                cancel_requested_at=sa.func.clock_timestamp()))
    calls_before, evidence_before = provider.calls, list(provider.evidence)
    invalidate_identity(pg, owners.a, missing)
    expire(pg, task.id)
    before_task = persisted(pg, "tasks", id=task.id)[0]
    before_events = persisted(pg, "outbox_events", aggregate_id=task.id)
    with pytest.raises(InactiveOwner):
        with task_db.transaction(owners.a):
            pytest.fail("an invalid login-bearing subject entered the owner UoW")

    worker(task_db, scheduler, provider, "identity-expiry-recovery").recover_expired()
    expected = "unknown" if sent else "cancelled"
    result = persisted(pg, "tasks", id=task.id)[0]
    result_attempt = persisted(pg, "task_attempts", id=attempt.id)[0]
    assert result["state"] == expected
    assert result_attempt["state"] == expected
    assert result["may_have_charged"] is sent
    assert result["lease_token"] == before_task["lease_token"] + 1
    assert result["lease_expires_at"] is None and result["leased_by"] is None
    assert provider.calls == calls_before and provider.evidence == evidence_before
    assert provider.calls == (1 if sent else 0)
    assert len(persisted(pg, "task_attempts", task_id=task.id)) == 1
    assert persisted(pg, "provider_results", owner_id=owners.a.owner_id) == []
    events = persisted(pg, "outbox_events", aggregate_id=task.id)
    assert len(events) == len(before_events) + 1
    assert events[-1]["payload"]["state"] == expected
    assert events[-1]["payload"]["may_have_charged"] is sent
    assert set(events[-1]["payload"]) == {"task_id", "class_session_id", "revision", "state",
                                          "error_code", "may_have_charged"}

    # Recovery, explicit fallback replay and another worker process identity are
    # idempotent after the one terminal transition; none can acquire new work.
    worker(task_db, scheduler, provider, "identity-repeat").recover_expired()
    assert scheduler.recover_revoked(task.id, claim.lease_token) is False
    restarted = RecordingProvider()
    assert worker(task_db, scheduler, restarted, "identity-restarted").run_once() is False
    assert restarted.calls == 0 and restarted.evidence == []
    assert persisted(pg, "tasks", id=task.id)[0] == result
    assert persisted(pg, "task_attempts", id=attempt.id)[0] == result_attempt
    assert persisted(pg, "outbox_events", aggregate_id=task.id) == events
    assert provider.calls == calls_before and provider.evidence == evidence_before


def test_two_real_workers_overlap_one_invalid_identity_recovery_without_duplicate_events(
        task_db, scheduler, owners, pg):
    complete_identity(pg, owners.a)
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    invalidate_identity(pg, owners.a, "email_verified_at")
    expire(pg, task.id)
    before = persisted(pg, "tasks", id=task.id)[0]
    before_events = persisted(pg, "outbox_events", aggregate_id=task.id)
    gate = Barrier(2)
    class OverlappingScheduler:
        def expired(self):
            # Both actors obtain the actual PG-expired row before either enters
            # the row-lock/fence protected fallback function.
            return [row for row in scheduler.expired() if row.id == task.id]
        def recover_revoked(self, task_id, token):
            gate.wait(timeout=10)
            return scheduler.recover_revoked(task_id, token)
    providers = [RecordingProvider(), RecordingProvider()]
    def recover(index):
        return worker(task_db, OverlappingScheduler(), providers[index],
                      f"overlapping-recovery-{index}").recover_expired()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(recover, index) for index in range(2)]
        for future in futures:
            assert future.result(timeout=20) == []
    result = persisted(pg, "tasks", id=task.id)[0]
    assert result["state"] == "cancelled"
    assert result["lease_token"] == before["lease_token"] + 1
    assert result["revision"] == before["revision"] + 1
    assert result["leased_by"] is None and result["lease_expires_at"] is None
    assert persisted(pg, "task_attempts", id=attempt.id)[0]["state"] == "cancelled"
    events = persisted(pg, "outbox_events", aggregate_id=task.id)
    assert len(events) == len(before_events) + 1
    assert events[-1]["payload"]["revision"] == result["revision"]
    assert all(provider.calls == 0 and provider.evidence == [] for provider in providers)
    assert scheduler.recover_revoked(task.id, claim.lease_token) is False
    assert persisted(pg, "tasks", id=task.id)[0] == result
    assert persisted(pg, "outbox_events", aggregate_id=task.id) == events


def test_more_than_one_hundred_invalid_expired_tasks_do_not_starve_valid_saved_raw(
        task_db, scheduler, owners, pg):
    complete_identity(pg, owners.a)
    complete_identity(pg, owners.b)
    with task_db.transaction(owners.a) as tx:
        invalid = [tx.tasks.enqueue("ai_question", {"schema_version": 1}, op_id=uuid4())
                   for _ in range(101)]
    claims = scheduler.claim(WORKER, lease_seconds=30, batch_size=100)
    claims += scheduler.claim(WORKER, lease_seconds=30, batch_size=100)
    invalid_ids = {item.id for item in invalid}
    assert {item.id for item in claims} == invalid_ids
    with task_db.transaction(owners.a) as tx:
        attempts = [tx.tasks.prepare_attempt(item.id, item.lease_token, WORKER) for item in claims]
    valid = enqueue(task_db, owners.b)
    valid_claim = claim_one(scheduler, valid)
    valid_attempt = prepared(task_db, owners.b, valid, valid_claim)
    submitting(task_db, owners.b, valid, valid_attempt, valid_claim)
    provider = RecordingProvider()
    raw = provider.submit(valid, valid_attempt)
    with task_db.transaction(owners.b) as tx:
        saved = tx.tasks.store_result(valid.id, valid_attempt.id, raw)
    sent_evidence = list(provider.evidence)
    now = datetime.now(timezone.utc)
    with pg.admin.begin() as connection:
        connection.execute(tables["tasks"].update().where(tables["tasks"].c.id.in_(invalid_ids)).values(
            lease_expires_at=now - timedelta(minutes=2)))
        connection.execute(tables["tasks"].update().where(tables["tasks"].c.id == valid.id).values(
            lease_expires_at=now - timedelta(seconds=1)))
    invalidate_identity(pg, owners.a, "email_verified_at")
    first_page = scheduler.expired()
    assert len(first_page) == 100 and {row.id for row in first_page} <= invalid_ids
    assert valid.id not in {row.id for row in first_page}
    engine = worker(task_db, scheduler, provider, "bounded-expiry-progress")
    # Every pass is bounded by the scheduler's actual batch; the invalid prefix
    # must leave the queue so the later authorized raw can finish locally.
    for _ in range(3):
        engine.run_once()
        if persisted(pg, "tasks", id=valid.id)[0]["state"] == "succeeded":
            break
    assert persisted(pg, "tasks", id=valid.id)[0]["state"] == "succeeded"
    with pg.admin.connect() as connection:
        invalid_rows = connection.execute(sa.select(tables["tasks"]).where(
            tables["tasks"].c.id.in_(invalid_ids))).mappings().all()
        invalid_attempts = connection.execute(sa.select(tables["task_attempts"]).where(
            tables["task_attempts"].c.id.in_([item.id for item in attempts]))).mappings().all()
    assert len(invalid_rows) == len(invalid_attempts) == 101
    assert all(row["state"] == "cancelled" and row["lease_expires_at"] is None
               and row["may_have_charged"] is False for row in invalid_rows)
    assert all(row["state"] == "cancelled" and row["submitted_at"] is None for row in invalid_attempts)
    assert provider.calls == 1 and provider.evidence == sent_evidence
    retained = persisted(pg, "provider_results", id=saved.id)[0]
    assert retained["raw_output"] == raw and retained["sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert retained["validation_status"] == "valid"
    assert len(persisted(pg, "task_attempts", task_id=valid.id)) == 1
    assert persisted(pg, "task_attempts", id=valid_attempt.id)[0]["raw_output_ref"] == saved.id
    restarted = RecordingProvider()
    assert worker(task_db, scheduler, restarted, "after-bounded-recovery").run_once() is False
    assert restarted.calls == 0 and restarted.evidence == []


def submitted_result(task_db, scheduler, owners, pg, raw='{"synthetic":"saved-local-result"}'):
    complete_identity(pg, owners.a)
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    submitting(task_db, owners.a, task, attempt, claim)
    provider = RecordingProvider()
    provider.output = raw
    assert provider.submit(task, attempt) == raw
    return SimpleNamespace(task=task, claim=claim, attempt=attempt,
                           raw=raw, provider=provider, evidence=list(provider.evidence))


def saved_result(task_db, scheduler, owners, pg, raw='{"synthetic":"saved-local-result"}'):
    state = submitted_result(task_db, scheduler, owners, pg, raw)
    with task_db.transaction(owners.a) as tx:
        state.result = tx.tasks.store_result(state.task.id, state.attempt.id, raw)
    return state


def quarantine_saved(state, missing, task_db, scheduler, owners, pg):
    invalidate_identity(pg, owners.a, missing)
    expire(pg, state.task.id)
    worker(task_db, scheduler, state.provider, "quarantine-before-local-validation").recover_expired()
    current = persisted(pg, "tasks", id=state.task.id)[0]
    assert current["state"] == "unknown" and current["may_have_charged"] is True
    local = PgLocalResultReconciler(task_db)
    candidates = [row for row in local.candidates() if row.id == state.task.id]
    assert len(candidates) == 1
    assert set(vars(candidates[0])) == {"id", "owner_id", "kind", "lease_token", "lease_expires_at"}
    return local, candidates[0]


def assert_raw_immutable(before, after):
    for field in ("id", "owner_id", "attempt_id", "created_at", "raw_output", "asset_id", "sha256",
                  "received_at", "conflict_sha256", "conflict_detected_at"):
        assert after[field] == before[field], field


@pytest.mark.parametrize("missing", ("email_verified_at", "password_hash", "disabled", "deleted"))
@pytest.mark.parametrize("valid", (True, False))
def test_local_saved_result_converges_while_identity_stays_invalid_without_provider_or_receipt_write(
        missing, valid, task_db, scheduler, owners, pg):
    raw = '{"synthetic":"valid-saved-result"}' if valid else '{"x":1,"x":2}'
    state = saved_result(task_db, scheduler, owners, pg, raw)
    local, claim = quarantine_saved(state, missing, task_db, scheduler, owners, pg)
    before_raw = persisted(pg, "provider_results", id=state.result.id)[0]
    before_task = persisted(pg, "tasks", id=state.task.id)[0]
    before_receipts = persisted(pg, "operation_receipts", owner_id=owners.a.owner_id)
    before_events = persisted(pg, "outbox_events", aggregate_id=state.task.id)
    with pytest.raises(InactiveOwner):
        with task_db.transaction(owners.a):
            pytest.fail("local processing must not reactivate the unavailable account")
    assert local.reconcile(claim) is True
    result = persisted(pg, "tasks", id=state.task.id)[0]
    attempt = persisted(pg, "task_attempts", id=state.attempt.id)[0]
    stored = persisted(pg, "provider_results", id=state.result.id)[0]
    assert result["state"] == ("succeeded" if valid else "failed")
    assert attempt["state"] == result["state"]
    assert stored["validation_status"] == ("valid" if valid else "invalid")
    assert stored["validated_payload"] == (json.loads(raw) if valid else None)
    assert_raw_immutable(before_raw, stored)
    assert attempt["raw_output_ref"] == state.result.id and attempt["provider_outcome"] == "succeeded"
    assert result["may_have_charged"] is True and result["completed_at"] is not None
    assert result["lease_token"] == before_task["lease_token"] + 1
    assert result["revision"] == before_task["revision"] + 1
    assert result["lease_expires_at"] is None and result["leased_by"] is None
    assert len(persisted(pg, "task_attempts", task_id=state.task.id)) == 1
    assert persisted(pg, "operation_receipts", owner_id=owners.a.owner_id) == before_receipts
    events = persisted(pg, "outbox_events", aggregate_id=state.task.id)
    assert len(events) == len(before_events) + 1
    assert events[-1]["payload"]["state"] == result["state"]
    assert events[-1]["payload"]["may_have_charged"] is True
    assert "validated_payload" not in events[-1]["payload"] and "raw_output" not in events[-1]["payload"]
    assert state.provider.calls == 1 and state.provider.evidence == state.evidence
    # Finalization grants no owner access, even though the internal result is
    # terminal. Repeat/restart processing makes no new fence, event or receipt.
    with pytest.raises(InactiveOwner):
        with task_db.transaction(owners.a):
            pytest.fail("unavailable owner received access after local finalization")
    assert local.reconcile(claim) is False
    restarted = RecordingProvider()
    engine = TaskWorker(task_db, scheduler, restarted, worker_id="local-finalization-restart",
                        reconciler=PgLocalResultReconciler(task_db))
    engine.run_once()
    assert restarted.calls == 0 and restarted.evidence == []
    assert persisted(pg, "tasks", id=state.task.id)[0] == result
    assert persisted(pg, "provider_results", id=state.result.id)[0] == stored
    assert persisted(pg, "outbox_events", aggregate_id=state.task.id) == events
    assert persisted(pg, "operation_receipts", owner_id=owners.a.owner_id) == before_receipts


def parity_expected(raw):
    try:
        payload = validate_json_output(raw)
        if not isinstance(payload, dict) or len(json.dumps(payload, allow_nan=False).encode()) > 800000:
            return False
        return True
    except (ValueError, TypeError, RecursionError):
        return False


PARITY_CORPUS = [
    pytest.param('{"x":1}', id="object"),
    pytest.param('[]', id="array-not-object"),
    pytest.param('{"x":1,"x":2}', id="duplicate-root-key"),
    pytest.param('{"x":{"a":1,"a":2}}', id="duplicate-nested-key"),
    pytest.param('{"x":NaN}', id="nan"),
    pytest.param('{"x":Infinity}', id="infinity"),
    pytest.param('{"x":1e400}', id="float-overflow"),
    pytest.param('{"x":"汉字é😀"}', id="multibyte"),
    pytest.param('{"x":"\\u6c49\\u5b57","emoji":"\\ud83d\\ude00"}', id="escaped-unicode"),
    pytest.param(r'{"numbers":[1.0,1e-4,1e-5,1e15,1e16,1e100,1e-100,5e-324,1e-400,-0.0,9007199254740993.0],"controls":"\n\r\t\b\f\\\""}',
                 id="float-representation-and-escaped-controls"),
    *[pytest.param(json.dumps({"x": "汉" * 133330 + "a" * padding}, ensure_ascii=False),
                   id=f"validated-bytes-{799989 + padding}") for padding in (10, 11, 12)],
]


@pytest.mark.parametrize("raw", PARITY_CORPUS)
def test_local_validation_matches_existing_python_output_rules(raw, task_db, scheduler, owners, pg):
    expected = parity_expected(raw)
    state = saved_result(task_db, scheduler, owners, pg, raw)
    local, claim = quarantine_saved(state, "email_verified_at", task_db, scheduler, owners, pg)
    before = persisted(pg, "provider_results", id=state.result.id)[0]
    assert local.reconcile(claim) is True
    after = persisted(pg, "provider_results", id=state.result.id)[0]
    assert after["validation_status"] == ("valid" if expected else "invalid")
    assert persisted(pg, "tasks", id=state.task.id)[0]["state"] == ("succeeded" if expected else "failed")
    if expected:
        # The existing Python finisher serializes its parsed object into JSONB.
        # JSONB expands e.g. 1e100 to an integer on readback, which is not the
        # same comparison as the original in-memory approximate Python float.
        encoded = json.dumps(validate_json_output(raw), allow_nan=False)
        with pg.admin.connect() as connection:
            baseline_payload = connection.scalar(sa.text("SELECT CAST(:encoded AS jsonb)"),
                                                 {"encoded": encoded})
        assert after["validated_payload"] == baseline_payload
    assert_raw_immutable(before, after)
    assert state.provider.calls == 1 and state.provider.evidence == state.evidence


def test_local_reconciler_rejects_live_lease_wrong_owner_stale_fence_and_conflicting_raw(
        task_db, scheduler, owners, pg):
    state = saved_result(task_db, scheduler, owners, pg)
    local = PgLocalResultReconciler(task_db)
    invalidate_identity(pg, owners.a, "email_verified_at")
    live = Claim(id=state.task.id, owner_id=owners.a.owner_id, kind=state.task.kind,
                 lease_token=state.claim.lease_token)
    before = persisted(pg, "tasks", id=state.task.id)[0]
    before_raw = persisted(pg, "provider_results", id=state.result.id)[0]
    assert local.reconcile(live) is False
    assert persisted(pg, "tasks", id=state.task.id)[0] == before
    assert persisted(pg, "provider_results", id=state.result.id)[0] == before_raw
    expire(pg, state.task.id)
    worker(task_db, scheduler, state.provider).recover_expired()
    actual = [row for row in local.candidates() if row.id == state.task.id][0]
    quarantined = persisted(pg, "tasks", id=state.task.id)[0]
    events = persisted(pg, "outbox_events", aggregate_id=state.task.id)
    assert local.reconcile(replace(actual, owner_id=owners.b.owner_id)) is False
    assert local.reconcile(replace(actual, lease_token=actual.lease_token + 1)) is False
    assert persisted(pg, "tasks", id=state.task.id)[0] == quarantined
    assert persisted(pg, "outbox_events", aggregate_id=state.task.id) == events
    assert local.reconcile(actual) is True


def test_conflicting_saved_raw_never_enters_automatic_local_validation(task_db, scheduler, owners, pg):
    state = saved_result(task_db, scheduler, owners, pg)
    with task_db.transaction(owners.a) as tx:
        conflict = tx.tasks.store_result(state.task.id, state.attempt.id, '{"synthetic":"conflict"}')
        assert conflict.conflict_sha256 is not None
    invalidate_identity(pg, owners.a, "password_hash")
    current = persisted(pg, "tasks", id=state.task.id)[0]
    raw = persisted(pg, "provider_results", id=state.result.id)[0]
    events = persisted(pg, "outbox_events", aggregate_id=state.task.id)
    local = PgLocalResultReconciler(task_db)
    assert state.task.id not in {row.id for row in local.candidates()}
    candidate = Claim(id=state.task.id, owner_id=owners.a.owner_id, kind=state.task.kind,
                      lease_token=current["lease_token"])
    assert local.reconcile(candidate) is False
    assert persisted(pg, "tasks", id=state.task.id)[0] == current
    assert persisted(pg, "provider_results", id=state.result.id)[0] == raw
    assert raw["validation_status"] == "pending" and raw["validated_payload"] is None
    assert persisted(pg, "outbox_events", aggregate_id=state.task.id) == events
    assert state.provider.calls == 1 and state.provider.evidence == state.evidence


def test_two_real_local_reconcilers_finish_one_saved_result_once(task_db, scheduler, owners, pg):
    state = saved_result(task_db, scheduler, owners, pg)
    local, claim = quarantine_saved(state, "password_hash", task_db, scheduler, owners, pg)
    before = persisted(pg, "tasks", id=state.task.id)[0]
    events = persisted(pg, "outbox_events", aggregate_id=state.task.id)
    receipts = persisted(pg, "operation_receipts", owner_id=owners.a.owner_id)
    gate = Barrier(2)
    def reconcile(_):
        gate.wait(timeout=10)
        return local.reconcile(claim)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reconcile, range(2)))
    assert sorted(results) == [False, True]
    after = persisted(pg, "tasks", id=state.task.id)[0]
    assert after["state"] == "succeeded" and after["may_have_charged"] is True
    assert after["revision"] == before["revision"] + 1
    assert after["lease_token"] == before["lease_token"] + 1
    assert len(persisted(pg, "outbox_events", aggregate_id=state.task.id)) == len(events) + 1
    assert persisted(pg, "operation_receipts", owner_id=owners.a.owner_id) == receipts
    assert len(persisted(pg, "task_attempts", task_id=state.task.id)) == 1
    assert state.provider.calls == 1 and state.provider.evidence == state.evidence


def wait_for_database_lock(pg, blocker, query_fragment):
    until = time.monotonic() + 10
    while time.monotonic() < until:
        with pg.admin.connect() as connection:
            blocked = connection.scalar(sa.text("""
                SELECT EXISTS (SELECT FROM pg_stat_activity
                  WHERE datname=current_database() AND wait_event_type='Lock'
                    AND :blocker=ANY(pg_blocking_pids(pid))
                    AND position(:fragment IN lower(query)) > 0)
            """), {"blocker": blocker, "fragment": query_fragment.lower()})
        if blocked:
            return
        time.sleep(.02)
    pytest.fail("the second actor never demonstrated the required real PostgreSQL lock wait")


@pytest.mark.parametrize("first", ("store", "invalidate"))
def test_raw_store_and_identity_invalidation_serialize_both_orders_without_resubmission(
        first, task_db, scheduler, owners, pg):
    state = submitted_result(task_db, scheduler, owners, pg)
    held, release, entered = Event(), Event(), Event()
    pids = {}
    def store():
        entered.set()
        with task_db.transaction(owners.a) as tx:
            pids["store"] = tx.connection.scalar(sa.text("SELECT pg_backend_pid()"))
            result = tx.tasks.store_result(state.task.id, state.attempt.id, state.raw)
            if first == "store":
                held.set()
                assert release.wait(10)
            return result.id
    def invalidate():
        with pg.admin.begin() as connection:
            pids["invalidate"] = connection.scalar(sa.text("SELECT pg_backend_pid()"))
            entered.set()
            connection.execute(catalog_tables["users"].update().where(
                catalog_tables["users"].c.id == owners.a.owner_id).values(email_verified_at=None))
            if first == "invalidate":
                held.set()
                assert release.wait(10)
        return "invalidated"
    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(store if first == "store" else invalidate)
        try:
            assert held.wait(10)
            entered.clear()
            loser = pool.submit(invalidate if first == "store" else store)
            assert entered.wait(10)
            wait_for_database_lock(pg, pids[first],
                                   "update users" if first == "store" else "classroom_web_lock_owner")
        finally:
            release.set()
        if first == "store":
            raw_id = winner.result(timeout=10)
            assert loser.result(timeout=10) == "invalidated"
        else:
            assert winner.result(timeout=10) == "invalidated"
            with pytest.raises((InactiveOwner, sa.exc.DBAPIError)) as caught:
                loser.result(timeout=10)
            if isinstance(caught.value, sa.exc.DBAPIError):
                assert caught.value.orig.sqlstate == "42501"
            raw_id = None
    expire(pg, state.task.id)
    worker(task_db, scheduler, state.provider, "after-store-invalidation-race").recover_expired()
    assert persisted(pg, "tasks", id=state.task.id)[0]["state"] == "unknown"
    local = PgLocalResultReconciler(task_db)
    candidates = [row for row in local.candidates() if row.id == state.task.id]
    if raw_id is not None:
        assert len(candidates) == 1 and local.reconcile(candidates[0]) is True
        assert persisted(pg, "provider_results", id=raw_id)[0]["raw_output"] == state.raw
        assert persisted(pg, "tasks", id=state.task.id)[0]["state"] == "succeeded"
    else:
        assert candidates == []
        assert persisted(pg, "provider_results", owner_id=owners.a.owner_id) == []
    assert state.provider.calls == 1 and state.provider.evidence == state.evidence
    assert len(persisted(pg, "task_attempts", task_id=state.task.id)) == 1
    assert persisted(pg, "tasks", id=state.task.id)[0]["may_have_charged"] is True


def test_identity_invalidation_commits_before_waiting_local_reconciliation(task_db, scheduler, owners, pg):
    # A late saved raw can exist on an already unknown task. The account update
    # and result finalizer then contend on an actual PostgreSQL task row lock.
    state = submitted_result(task_db, scheduler, owners, pg)
    expire(pg, state.task.id)
    worker(task_db, scheduler, state.provider, "unknown-before-late-raw").recover_expired()
    with task_db.transaction(owners.a) as tx:
        stored = tx.tasks.store_result(state.task.id, state.attempt.id, state.raw)
    before = persisted(pg, "tasks", id=state.task.id)[0]
    assert before["state"] == "unknown"
    events = persisted(pg, "outbox_events", aggregate_id=state.task.id)
    claim = Claim(id=state.task.id, owner_id=owners.a.owner_id, kind=state.task.kind,
                  lease_token=before["lease_token"])
    local = PgLocalResultReconciler(task_db)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pg.admin.begin() as connection:
            blocker = connection.scalar(sa.text("SELECT pg_backend_pid()"))
            connection.execute(sa.select(tables["tasks"].c.id).where(
                tables["tasks"].c.id == state.task.id).with_for_update())
            connection.execute(catalog_tables["users"].update().where(
                catalog_tables["users"].c.id == owners.a.owner_id).values(email_verified_at=None))
            future = pool.submit(local.reconcile, claim)
            wait_for_database_lock(pg, blocker, "classroom_web_reconcile_saved_result")
        assert future.result(timeout=10) is True
    assert persisted(pg, "tasks", id=state.task.id)[0]["state"] == "succeeded"
    assert persisted(pg, "provider_results", id=stored.id)[0]["validation_status"] == "valid"
    assert len(persisted(pg, "outbox_events", aggregate_id=state.task.id)) == len(events) + 1
    with pytest.raises(InactiveOwner):
        with task_db.transaction(owners.a):
            pytest.fail("invalidation was lost by local result finalization")
    assert state.provider.calls == 1 and state.provider.evidence == state.evidence


def test_local_finalizer_repairs_legacy_exact_attempt_result_without_raw_pointer(
        task_db, scheduler, owners, pg):
    state = submitted_result(task_db, scheduler, owners, pg)
    raw_id = uuid4()
    with task_db.transaction(owners.a) as tx:
        # Construct a durable legacy INSERT whose pointer was never assigned.
        # No immutable pointer is cleared and no database trigger is disabled.
        tx.connection.execute(tables["provider_results"].insert().values(
            id=raw_id, owner_id=owners.a.owner_id, attempt_id=state.attempt.id,
            raw_output=state.raw, sha256=hashlib.sha256(state.raw.encode()).hexdigest(),
            validation_status="pending"))
    assert persisted(pg, "task_attempts", id=state.attempt.id)[0]["raw_output_ref"] is None
    raw_before = persisted(pg, "provider_results", id=raw_id)[0]
    local, claim = quarantine_saved(state, "password_hash", task_db, scheduler, owners, pg)
    receipts = persisted(pg, "operation_receipts", owner_id=owners.a.owner_id)
    assert local.reconcile(claim) is True
    task = persisted(pg, "tasks", id=state.task.id)[0]
    attempt = persisted(pg, "task_attempts", id=state.attempt.id)[0]
    raw_after = persisted(pg, "provider_results", id=raw_id)[0]
    assert task["state"] == attempt["state"] == "succeeded"
    assert task["may_have_charged"] is True
    assert attempt["raw_output_ref"] == raw_id and attempt["provider_outcome"] == "succeeded"
    assert raw_after["validation_status"] == "valid"
    assert_raw_immutable(raw_before, raw_after)
    assert persisted(pg, "operation_receipts", owner_id=owners.a.owner_id) == receipts
    assert len(persisted(pg, "task_attempts", task_id=state.task.id)) == 1
    assert state.provider.calls == 1 and state.provider.evidence == state.evidence


@pytest.mark.parametrize("first", ("invalidate", "claim"))
def test_claim_and_owner_preparation_serialize_with_identity_invalidation_both_orders(
        first, task_db, scheduler, owners, pg):
    complete_identity(pg, owners.a)
    task = enqueue(task_db, owners.a)
    held, release, entered = Event(), Event(), Event()
    pids = {}
    provider = RecordingProvider()
    def invalidate():
        with pg.admin.begin() as connection:
            pids["invalidate"] = connection.scalar(sa.text("SELECT pg_backend_pid()"))
            entered.set()
            connection.execute(catalog_tables["users"].update().where(
                catalog_tables["users"].c.id == owners.a.owner_id).values(password_hash=None))
            if first == "invalidate":
                held.set()
                assert release.wait(10)
        return "invalidated"
    def prepare(claim):
        entered.set()
        with task_db.transaction(owners.a) as tx:
            pids["claim"] = tx.connection.scalar(sa.text("SELECT pg_backend_pid()"))
            attempt = tx.tasks.prepare_attempt(task.id, claim.lease_token, WORKER)
            if first == "claim":
                held.set()
                assert release.wait(10)
            return attempt.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        if first == "invalidate":
            winner = pool.submit(invalidate)
            try:
                assert held.wait(10)
                # Queue claiming is a metadata-only operation and need not wait
                # for an account lock. The ordinary owner UoW must wait/recheck.
                claim = claim_one(scheduler, task)
                entered.clear()
                loser = pool.submit(prepare, claim)
                assert entered.wait(10)
                wait_for_database_lock(pg, pids["invalidate"], "classroom_web_lock_owner")
            finally:
                release.set()
            assert winner.result(timeout=10) == "invalidated"
            with pytest.raises(InactiveOwner):
                loser.result(timeout=10)
            assert persisted(pg, "task_attempts", task_id=task.id) == []
        else:
            claim = claim_one(scheduler, task)
            winner = pool.submit(prepare, claim)
            try:
                assert held.wait(10)
                entered.clear()
                loser = pool.submit(invalidate)
                assert entered.wait(10)
                wait_for_database_lock(pg, pids["claim"], "update users")
            finally:
                release.set()
            attempt_id = winner.result(timeout=10)
            assert loser.result(timeout=10) == "invalidated"
            assert persisted(pg, "task_attempts", id=attempt_id)[0]["state"] == "planned"
    expire(pg, task.id)
    engine = worker(task_db, scheduler, provider, "claim-identity-race-recovery")
    engine.recover_expired()
    final = persisted(pg, "tasks", id=task.id)[0]
    events = persisted(pg, "outbox_events", aggregate_id=task.id)
    assert final["state"] == "cancelled" and final["may_have_charged"] is False
    assert final["leased_by"] is None and final["lease_expires_at"] is None
    assert all(row["state"] == "cancelled" and row["submitted_at"] is None
               for row in persisted(pg, "task_attempts", task_id=task.id))
    assert provider.calls == 0 and provider.evidence == []
    engine.recover_expired()
    assert persisted(pg, "tasks", id=task.id)[0] == final
    assert persisted(pg, "outbox_events", aggregate_id=task.id) == events


def test_private_helpers_and_account_predicate_do_not_expand_scheduler_or_raw_access(
        task_db, scheduler, owners, pg):
    signatures = ["classroom_web_identity_usable(uuid)",
                  "classroom_web_attempt_submitted(uuid,uuid)",
                  "classroom_web_local_float_token(text)",
                  "classroom_web_local_json_token(text)",
                  "classroom_web_local_json_payload(text)"]
    with scheduler.engine.connect() as connection:
        for name in ("users", "tasks", "task_attempts", "provider_results"):
            assert connection.scalar(sa.text("SELECT has_table_privilege(current_user,:name,'SELECT')"),
                                     {"name": name}) is False
        for signature in signatures + ["classroom_web_local_reconcile_candidates(integer)",
                                       "classroom_web_reconcile_saved_result(uuid,bigint)"]:
            assert connection.scalar(sa.text("SELECT has_function_privilege(current_user,:signature,'EXECUTE')"),
                                     {"signature": signature}) is False
    with task_db.engine.connect() as connection:
        for signature in signatures:
            assert connection.scalar(sa.text("SELECT has_function_privilege(current_user,:signature,'EXECUTE')"),
                                     {"signature": signature}) is False
    with pg.admin.connect() as connection:
        for table, column in (("users", "normalized_email"), ("users", "email_verified_at"),
                              ("users", "password_hash"), ("task_attempts", "provider_request_id"),
                              ("provider_results", "raw_output"), ("provider_results", "validated_payload")):
            assert connection.scalar(sa.text("SELECT has_column_privilege('classroom_web_task_dispatcher',:table,:column,'SELECT')"),
                                     {"table": table, "column": column}) is False
        public_execute = connection.scalar(sa.text("""
            SELECT EXISTS (SELECT FROM pg_proc p
              CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
              WHERE p.proname IN ('classroom_web_identity_usable','classroom_web_attempt_submitted',
                'classroom_web_local_float_token','classroom_web_local_json_token',
                'classroom_web_local_json_payload','classroom_web_local_reconcile_candidates',
                'classroom_web_reconcile_saved_result')
                AND a.grantee=0 AND a.privilege_type='EXECUTE')
        """))
        assert public_execute is False
    password_hash = Argon2PasswordHasher().hash("Synthetic-predicate-password")
    for case in ("verified", "email_verified_at", "password_hash", "disabled", "deleted", "shell"):
        uid = uuid4()
        values = {"id": uid, "role": "student", "state": "active"}
        if case != "shell":
            values.update(normalized_email=f"synthetic-{uid.hex}@example.test",
                          password_hash=password_hash, email_verified_at=datetime.now(timezone.utc))
            if case in ("email_verified_at", "password_hash"):
                values[case] = None
            elif case == "disabled":
                values["state"] = "disabled"
            elif case == "deleted":
                values["deleted_at"] = datetime.now(timezone.utc)
        with pg.admin.begin() as connection:
            connection.execute(catalog_tables["users"].insert().values(**values))
            predicate = connection.scalar(sa.text("SELECT classroom_web_identity_usable(:uid)"), {"uid": uid})
        with task_db.engine.begin() as connection:
            connection.execute(sa.text("SELECT set_config('app.owner_id',:owner,true)"), {"owner": str(uid)})
            owner_active = connection.scalar(sa.text("SELECT classroom_web_owner_active()"))
        assert predicate is owner_active is (case in ("verified", "shell"))
    state = saved_result(task_db, scheduler, owners, pg)
    local, claim = quarantine_saved(state, "password_hash", task_db, scheduler, owners, pg)
    assert local.reconcile(claim) is True
    with task_db.engine.begin() as connection:
        connection.execute(sa.text("SELECT set_config('app.owner_id',:owner,true)"),
                           {"owner": str(owners.a.owner_id)})
        assert connection.execute(sa.select(tables["provider_results"])).all() == []
    assert state.provider.calls == 1 and state.provider.evidence == state.evidence


def test_local_result_transition_failure_rolls_back_every_write_before_one_successful_retry(
        task_db, scheduler, owners, pg):
    state = saved_result(task_db, scheduler, owners, pg)
    local, claim = quarantine_saved(state, "email_verified_at", task_db, scheduler, owners, pg)
    names = ("tasks", "task_attempts", "provider_results", "operation_receipts", "outbox_events",
             "owner_event_counters")
    before = {name: persisted(pg, name, owner_id=owners.a.owner_id) for name in names}
    marker = uuid4().hex
    function, trigger = "pr03a_local_fault_" + marker, "pr03a_local_fault_trigger_" + marker
    with pg.admin.begin() as connection:
        connection.exec_driver_sql(f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic local commit fault' USING ERRCODE='23514'; END $$")
        connection.exec_driver_sql(f"CREATE TRIGGER {trigger} BEFORE INSERT ON outbox_events FOR EACH ROW WHEN (NEW.owner_id='{owners.a.owner_id}'::uuid) EXECUTE FUNCTION {function}()")
    try:
        with pytest.raises(sa.exc.DBAPIError) as caught:
            local.reconcile(claim)
        assert caught.value.orig.sqlstate == "23514"
        assert {name: persisted(pg, name, owner_id=owners.a.owner_id) for name in names} == before
    finally:
        with pg.admin.begin() as connection:
            connection.exec_driver_sql(f"DROP TRIGGER {trigger} ON outbox_events")
            connection.exec_driver_sql(f"DROP FUNCTION {function}()")
    assert local.reconcile(claim) is True
    final = persisted(pg, "tasks", id=state.task.id)[0]
    assert final["state"] == "succeeded" and final["may_have_charged"] is True
    assert final["revision"] == before["tasks"][0]["revision"] + 1
    assert final["lease_token"] == before["tasks"][0]["lease_token"] + 1
    assert len(persisted(pg, "outbox_events", owner_id=owners.a.owner_id)) == len(before["outbox_events"]) + 1
    assert persisted(pg, "operation_receipts", owner_id=owners.a.owner_id) == before["operation_receipts"]
    assert state.provider.calls == 1 and state.provider.evidence == state.evidence


def test_ordinary_expired_recovery_waits_for_identity_invalidation_then_uses_safe_fallback(
        task_db, scheduler, owners, pg):
    complete_identity(pg, owners.a)
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    expire(pg, task.id)
    before = persisted(pg, "tasks", id=task.id)[0]
    before_events = persisted(pg, "outbox_events", aggregate_id=task.id)
    provider = RecordingProvider()
    engine = worker(task_db, scheduler, provider, "ordinary-recovery-identity-race")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pg.admin.begin() as connection:
            blocker = connection.scalar(sa.text("SELECT pg_backend_pid()"))
            connection.execute(catalog_tables["users"].update().where(
                catalog_tables["users"].c.id == owners.a.owner_id).values(email_verified_at=None))
            future = pool.submit(engine.recover_expired)
            # owner_active sees the previous committed valid row. lock_owner
            # then waits and must map its post-wait denial to InactiveOwner.
            wait_for_database_lock(pg, blocker, "classroom_web_lock_owner")
        assert future.result(timeout=10) == []
    after = persisted(pg, "tasks", id=task.id)[0]
    assert after["state"] == "cancelled" and after["may_have_charged"] is False
    assert after["leased_by"] is None and after["lease_expires_at"] is None
    assert after["revision"] == before["revision"] + 1
    assert after["lease_token"] == before["lease_token"] + 1
    assert persisted(pg, "task_attempts", id=attempt.id)[0]["state"] == "cancelled"
    assert len(persisted(pg, "outbox_events", aggregate_id=task.id)) == len(before_events) + 1
    assert provider.calls == 0 and provider.evidence == []
    assert engine.recover_expired() == []
    assert persisted(pg, "tasks", id=task.id)[0] == after
