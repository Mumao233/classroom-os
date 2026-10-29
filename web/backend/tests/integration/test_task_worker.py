"""Polling worker orchestration uses durable state and local mock providers only."""
from uuid import uuid4

import pytest
import sqlalchemy as sa

from classroom_web.application.task_worker import MockProvider, TaskWorker
from classroom_web.domain.data import Resource
from classroom_web.infrastructure.schema import tables as catalog_tables

from test_persistent_tasks import (
    WORKER, enqueue, claim_one, expire, persisted, prepared, submitting,
    task_db, scheduler,
)


def worker(task_db, scheduler, provider, worker_id=WORKER):
    return TaskWorker(task_db, scheduler, provider, worker_id=worker_id, lease_seconds=30)


def test_worker_commits_send_intent_before_calling_mock_provider(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)

    class ObservingMock(MockProvider):
        def submit(self, supplied_task, attempt):
            assert supplied_task.id == task.id
            stored = persisted(pg, "task_attempts", id=attempt.id)[0]
            assert stored["state"] == "submitting" and stored["submitted_at"] is not None
            # A separate UoW can take the same task lock while submit is active:
            # no network/provider call is wrapped in a long DB transaction.
            with task_db.transaction(owners.a) as tx:
                tx.tasks.heartbeat(task.id, supplied_task.lease_token, WORKER)
            return super().submit(supplied_task, attempt)

    provider = ObservingMock()
    assert worker(task_db, scheduler, provider).run_once() is True
    assert provider.calls == 1
    assert persisted(pg, "tasks", id=task.id)[0]["state"] == "succeeded"
    assert worker(task_db, scheduler, provider).run_once() is False
    assert provider.calls == 1


def test_worker_timeout_stays_unknown_across_restart_without_second_send(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)

    class TimeoutMock(MockProvider):
        def submit(self, task, attempt):
            self.calls += 1
            raise TimeoutError("synthetic provider result lost")

    provider = TimeoutMock()
    assert worker(task_db, scheduler, provider).run_once() is True
    assert provider.calls == 1
    assert persisted(pg, "tasks", id=task.id)[0]["state"] == "unknown"
    assert worker(task_db, scheduler, provider, "restarted").run_once() is False
    assert provider.calls == 1
    assert len(persisted(pg, "task_attempts", task_id=task.id)) == 1


def test_worker_reclaims_only_provably_unsent_planned_attempt(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    original = prepared(task_db, owners.a, task, claim)
    expire(pg, task.id)
    provider = MockProvider()
    assert worker(task_db, scheduler, provider, "restart").run_once() is True
    assert provider.calls == 1
    attempts = persisted(pg, "task_attempts", task_id=task.id)
    assert len(attempts) == 1 and attempts[0]["id"] == original.id
    assert attempts[0]["state"] == "succeeded"


def test_worker_revalidates_persisted_raw_with_zero_provider_calls(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    original = prepared(task_db, owners.a, task, claim)
    submitting(task_db, owners.a, task, original, claim)
    with task_db.transaction(owners.a) as tx:
        result = tx.tasks.store_result(task.id, original.id, '{"already":"persisted synthetic"}')
    expire(pg, task.id)
    provider = MockProvider()
    assert worker(task_db, scheduler, provider, "restart").run_once() is True
    assert provider.calls == 0
    assert persisted(pg, "tasks", id=task.id)[0]["state"] == "succeeded"
    assert persisted(pg, "provider_results", id=result.id)[0]["validation_status"] == "valid"
    assert len(persisted(pg, "task_attempts", task_id=task.id)) == 1


def test_worker_rejects_non_simulation_adapter_before_claim(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    provider = MockProvider(simulation_only=False)
    with pytest.raises(ValueError, match="simulat"):
        worker(task_db, scheduler, provider)
    assert provider.calls == 0
    stored = persisted(pg, "tasks", id=task.id)[0]
    assert stored["state"] == "queued" and stored["lease_token"] == 0


def test_duplicate_json_keys_fail_validation_without_automatic_model_repair(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    provider = MockProvider(output='{"x":1,"x":2}')
    assert worker(task_db, scheduler, provider).run_once() is True
    assert provider.calls == 1
    stored = persisted(pg, "tasks", id=task.id)[0]
    assert stored["state"] == "failed" and stored["error_code"] == "OUTPUT_INVALID"
    raw = persisted(pg, "provider_results", owner_id=owners.a.owner_id)[0]
    assert raw["raw_output"] == '{"x":1,"x":2}' and raw["validation_status"] == "invalid"
    assert persisted(pg, "task_attempts", task_id=task.id)[0]["provider_outcome"] == "succeeded"
    assert worker(task_db, scheduler, provider).run_once() is False
    assert provider.calls == 1


def test_cancel_during_mock_send_retains_successful_billable_result(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)

    class CancellingMock(MockProvider):
        def submit(self, task, attempt):
            with task_db.transaction(owners.a) as tx:
                assert tx.tasks.cancel(task.id, uuid4()).state == "cancel_requested"
            return super().submit(task, attempt)

    provider = CancellingMock()
    assert worker(task_db, scheduler, provider).run_once() is True
    assert provider.calls == 1
    assert persisted(pg, "tasks", id=task.id)[0]["state"] == "succeeded"
    assert persisted(pg, "task_attempts", task_id=task.id)[0]["provider_outcome"] == "succeeded"
    final_event = persisted(pg, "outbox_events", aggregate_id=task.id)[-1]
    assert final_event["payload"]["may_have_charged"] is True


def test_cancel_immediately_after_claim_prevents_provider_send(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)

    class CancellingScheduler:
        def expired(self):
            return scheduler.expired()

        def claim(self, *args, **kwargs):
            claims = scheduler.claim(*args, **kwargs)
            if claims:
                assert len(claims) == 1 and claims[0].id == task.id
                with task_db.transaction(owners.a) as tx:
                    assert tx.tasks.cancel(task.id, uuid4()).state == "cancelled"
            return claims

    provider = MockProvider()
    assert worker(task_db, CancellingScheduler(), provider).run_once() is True
    assert provider.calls == 0
    assert persisted(pg, "tasks", id=task.id)[0]["state"] == "cancelled"
    assert persisted(pg, "task_attempts", task_id=task.id) == []


def test_result_arriving_after_expiry_recovers_locally_without_resend(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)

    class ExpiringMock(MockProvider):
        def submit(self, task, attempt):
            expire(pg, task.id)
            return super().submit(task, attempt)

    provider = ExpiringMock()
    assert worker(task_db, scheduler, provider).run_once() is True
    assert provider.calls == 1
    assert persisted(pg, "tasks", id=task.id)[0]["state"] == "running"
    assert persisted(pg, "provider_results", owner_id=owners.a.owner_id)[0]["validation_status"] == "pending"
    restarted_provider = MockProvider()
    assert worker(task_db, scheduler, restarted_provider, "restart").run_once() is True
    assert restarted_provider.calls == 0 and provider.calls == 1
    assert persisted(pg, "tasks", id=task.id)[0]["state"] == "succeeded"


def test_timeout_after_expiry_recovers_to_unknown_without_resend(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)

    class ExpiringTimeout(MockProvider):
        def submit(self, task, attempt):
            self.calls += 1
            expire(pg, task.id)
            raise TimeoutError("synthetic lost result after expiry")

    provider = ExpiringTimeout()
    assert worker(task_db, scheduler, provider).run_once() is True
    assert provider.calls == 1
    assert persisted(pg, "tasks", id=task.id)[0]["state"] == "running"
    restarted_provider = MockProvider()
    assert worker(task_db, scheduler, restarted_provider, "restart").run_once() is False
    assert restarted_provider.calls == 0
    assert persisted(pg, "tasks", id=task.id)[0]["state"] == "unknown"


@pytest.mark.parametrize("revocation", ("classroom", "owner"))
@pytest.mark.parametrize("stage", ("unsent", "sent", "raw"))
def test_expired_revoked_work_preserves_charge_risk_without_loading_bodies(
        revocation, stage, task_db, scheduler, owners, pg, tree):
    task = enqueue(task_db, owners.a, class_session_id=tree.classroom.id)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    sent = stage != 'unsent'
    if sent:
        submitting(task_db, owners.a, task, attempt, claim)
    if stage == 'raw':
        with task_db.transaction(owners.a) as tx:
            raw = tx.tasks.store_result(task.id, attempt.id, '{"private":"synthetic only"}')
    if revocation == "classroom":
        with task_db.transaction(owners.a) as tx:
            tx.tombstones.revoke(Resource.CLASS_SESSION, tree.classroom.id, 1, ledger_sequence=1)
    else:
        with pg.admin.begin() as connection:
            connection.execute(catalog_tables["users"].update().where(
                catalog_tables["users"].c.id == owners.a.owner_id).values(state="disabled", revision=2))
    expire(pg, task.id)
    provider = MockProvider()
    worker(task_db, scheduler, provider, "after-revocation").recover_expired()
    expected = "unknown" if sent else "cancelled"
    assert persisted(pg, "tasks", id=task.id)[0]["state"] == expected
    assert persisted(pg, "task_attempts", id=attempt.id)[0]["state"] == expected
    assert provider.calls == 0
    assert scheduler.claim("never-resend-revoked") == []
    event = persisted(pg, "outbox_events", aggregate_id=task.id)[-1]
    assert event["payload"]["state"] == expected
    assert event["payload"]["may_have_charged"] is sent
    assert set(event["payload"]) == {"task_id", "class_session_id", "revision", "state",
                                      "error_code", "may_have_charged"}
    if stage == 'raw':
        stored = persisted(pg, 'provider_results', id=raw.id)[0]
        assert stored['validation_status'] == 'pending'
        assert persisted(pg, 'task_attempts', id=attempt.id)[0]['provider_outcome'] == 'succeeded'
        with scheduler.engine.begin() as connection:
            assert not connection.scalar(sa.text(
                "SELECT has_table_privilege(current_user,'provider_results','SELECT')"))
