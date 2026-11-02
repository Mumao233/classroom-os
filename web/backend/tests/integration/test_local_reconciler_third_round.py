"""Third-round LocalReconciler regressions against isolated, real PostgreSQL.

Only synthetic fixtures and the local recording Provider are used. No trigger
is disabled: revocation concurrency uses the real repositories and their guards.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa

from classroom_web.domain.data import InactiveOwner, Resource
from classroom_web.domain.tasks import Claim
from classroom_web.infrastructure.local_reconciler import PgLocalResultReconciler
from classroom_web.infrastructure.repositories import PgCatalogRepository, PgTombstoneRepository
from classroom_web.infrastructure.schema import tables as catalog_tables
from classroom_web.infrastructure.task_schema import tables

from test_identity_recovery import (
    RecordingProvider, assert_raw_immutable, complete_identity, invalidate_identity,
    quarantine_saved, saved_result,
)
from test_persistent_tasks import (
    WORKER, claim_one, expire, persisted, prepared, scheduler, submitting, task_db,
)
from test_task_material_remediation import moved_source, enqueue_source, revoke_source
from test_task_worker import worker


ROOT = Path(__file__).resolve().parents[4]
I = "classroom_web_identity_checker"
M = "classroom_web_recovery_metadata"
P = "classroom_web_local_result_processor"
INTERNAL_ROLES = (I, M, P)
SOURCE_TABLES = ("semesters", "courses", "class_sessions")
LEDGERS = ("tasks", "task_attempts", "provider_results", "operation_receipts",
           "owner_event_counters", "outbox_events")


def evidence(label, value):
    """Use a new file for every observation; never replace earlier evidence."""
    configured = os.environ.get("PR03A_THIRD_ROUND_EVIDENCE")
    folder = Path(configured) if configured else ROOT / "outputs/pr03a/third-round/test-observations"
    folder = folder.resolve()
    assert folder.is_relative_to((ROOT / "outputs/pr03a/third-round").resolve())
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"{label}-{uuid4().hex}.json"
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, default=str)


def snapshot(pg, owner):
    return {name: persisted(pg, name, owner_id=owner.owner_id) for name in LEDGERS}


def log_position():
    configured = os.environ.get("PR03A_THIRD_ROUND_PG_LOG")
    assert configured, "the runner must identify its verified synthetic PostgreSQL log"
    path = Path(configured).resolve(strict=True)
    assert path.is_relative_to((ROOT / "outputs/pr03a/third-round").resolve())
    return path, path.stat().st_size


def assert_log_clean(position, marker, label):
    path, offset = position
    with path.open("rb") as stream:
        stream.seek(offset)
        tail = stream.read()
    assert marker.encode() not in tail, "synthetic body escaped to PostgreSQL's raw server log"
    evidence(label, {"postgres_log": path, "offset": offset, "bytes_scanned": len(tail),
                     "body_marker_absent": True})


def exception_text(error):
    """Include Python chaining plus psycopg DETAIL/CONTEXT, not just str()."""
    pieces, seen = [], set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        pieces.extend((str(error), repr(error)))
        original = getattr(error, "orig", error)
        diagnostic = getattr(original, "diag", None)
        if diagnostic:
            pieces.extend(str(getattr(diagnostic, key, "") or "") for key in (
                "message_primary", "message_detail", "message_hint", "context",
                "internal_query", "schema_name", "table_name", "constraint_name"))
        error = error.__cause__ or error.__context__
    return "\n".join(pieces)


def test_final_jsonb_limit_uses_storage_size_and_advances_following_owner(
        task_db, scheduler, owners, pg):
    marker = "SYNTHETIC_THIRD_ROUND_LARGE_" + uuid4().hex
    raw = json.dumps({"marker": marker, "v": [0] * 99900}, separators=(",", ":"))
    assert len(raw) <= 200000 and len(json.dumps(json.loads(raw)).encode()) < 800000
    with pg.admin.connect() as connection:
        jsonb_bytes = connection.scalar(sa.text("SELECT pg_column_size(CAST(:raw AS jsonb))"),
                                       {"raw": raw})
    assert jsonb_bytes > 1048576
    state = saved_result(task_db, scheduler, owners, pg, raw)
    local, claim = quarantine_saved(state, "password_hash", task_db, scheduler, owners, pg)
    other = SimpleNamespace(a=owners.b, b=owners.a)
    following = saved_result(task_db, scheduler, other, pg)
    quarantine_saved(following, "email_verified_at", task_db, scheduler, other, pg)
    before = snapshot(pg, owners.a)
    position = log_position()
    assert local.run_once() == 2
    current = persisted(pg, "tasks", id=state.task.id)[0]
    attempt = persisted(pg, "task_attempts", id=state.attempt.id)[0]
    result = persisted(pg, "provider_results", id=state.result.id)[0]
    assert current["state"] == attempt["state"] == "failed"
    assert current["error_code"] == attempt["error_code"] == "OUTPUT_INVALID"
    assert current["may_have_charged"] is True and current["lease_expires_at"] is None
    assert result["validation_status"] == "invalid" and result["validated_payload"] is None
    assert_raw_immutable(before["provider_results"][0], result)
    assert attempt["provider_outcome"] == before["task_attempts"][0]["provider_outcome"]
    assert persisted(pg, "tasks", id=following.task.id)[0]["state"] == "succeeded"
    assert persisted(pg, "operation_receipts", owner_id=owners.a.owner_id) == before["operation_receipts"]
    events = persisted(pg, "outbox_events", aggregate_id=state.task.id)
    assert len(events) == len(before["outbox_events"]) + 1
    assert events[-1]["payload"]["state"] == "failed"
    assert marker not in json.dumps(events[-1]["payload"])
    stable = snapshot(pg, owners.a)
    assert local.reconcile(claim) is False and local.run_once() == 0
    assert snapshot(pg, owners.a) == stable
    assert state.provider.calls == following.provider.calls == 1
    assert state.provider.evidence == state.evidence
    assert_log_clean(position, marker, "large-jsonb-log")
    evidence("large-jsonb-progress", {"jsonb_bytes": jsonb_bytes, "task": current["state"],
             "error_code": current["error_code"], "following": "succeeded",
             "provider_calls": [state.provider.calls, following.provider.calls]})


@pytest.mark.parametrize("stage", ("provider_results", "outbox_events"))
def test_sql_exception_boundary_hides_body_and_rolls_back_every_ledger(
        stage, task_db, scheduler, owners, pg):
    marker = "SYNTHETIC_THIRD_ROUND_FAULT_" + uuid4().hex
    state = saved_result(task_db, scheduler, owners, pg, json.dumps({"synthetic": marker}))
    local, claim = quarantine_saved(state, "password_hash", task_db, scheduler, owners, pg)
    before = snapshot(pg, owners.a)
    suffix = uuid4().hex
    function, trigger = "pr03a_r3_fault_" + suffix, "pr03a_r3_fault_trigger_" + suffix
    with pg.admin.begin() as connection:
        # The result trigger obtains its text from NEW.raw_output. The late
        # Outbox trigger proves the boundary also includes all following writes.
        expression = "NEW.raw_output" if stage == "provider_results" else "'" + marker + "'"
        connection.execution_options(no_parameters=True).exec_driver_sql(f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION '%', {expression} USING ERRCODE='23514'; END $$")
        operation = "UPDATE" if stage == "provider_results" else "INSERT"
        connection.exec_driver_sql(f"CREATE TRIGGER {trigger} BEFORE {operation} ON {stage} FOR EACH ROW WHEN (NEW.owner_id='{owners.a.owner_id}'::uuid) EXECUTE FUNCTION {function}()")
    position = log_position()
    try:
        with pytest.raises(sa.exc.DBAPIError) as caught:
            local.reconcile(claim)
        assert caught.value.orig.sqlstate == "23514"
        assert marker not in exception_text(caught.value)
        assert snapshot(pg, owners.a) == before
        assert_log_clean(position, marker, "safe-error-" + stage)
    finally:
        with pg.admin.begin() as connection:
            connection.exec_driver_sql(f"DROP TRIGGER {trigger} ON {stage}")
            connection.exec_driver_sql(f"DROP FUNCTION {function}()")
    assert local.reconcile(claim) is True
    assert persisted(pg, "tasks", id=state.task.id)[0]["state"] == "succeeded"
    assert persisted(pg, "operation_receipts", owner_id=owners.a.owner_id) == before["operation_receipts"]
    assert state.provider.calls == 1 and state.provider.evidence == state.evidence


@pytest.mark.parametrize("elements,valid", ((87000, True), (88000, False)))
def test_actual_jsonb_storage_boundary_keeps_bounded_large_normal_output(
        elements, valid, task_db, scheduler, owners, pg):
    raw = '{"v":[' + ','.join(['0'] * elements) + ']}'
    with pg.admin.connect() as connection:
        size = connection.scalar(sa.text("SELECT pg_column_size(CAST(:raw AS jsonb))"),
                                 {"raw": raw})
    assert (size <= 1048576) is valid
    state = saved_result(task_db, scheduler, owners, pg, raw)
    local, claim = quarantine_saved(state, "password_hash", task_db, scheduler, owners, pg)
    assert local.reconcile(claim) is True
    result = persisted(pg, "provider_results", id=state.result.id)[0]
    assert result["validation_status"] == ("valid" if valid else "invalid")
    assert result["validated_payload"] == ({"v": [0] * elements} if valid else None)
    assert result["raw_output"] == raw
    assert state.provider.calls == 1
    evidence("jsonb-boundary", {"elements": elements, "actual_jsonb_bytes": size,
             "validation_status": result["validation_status"], "provider_calls": 1})


def source_saved(task_db, scheduler, owners, tree, pg, *, unknown=False):
    complete_identity(pg, owners.a)
    source = moved_source(task_db, owners.a, tree)
    task = enqueue_source(task_db, owners.a, source, explicit=False)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    submitting(task_db, owners.a, task, attempt, claim)
    provider = RecordingProvider()
    raw = provider.submit(task, attempt)
    with task_db.transaction(owners.a) as tx:
        if unknown:
            tx.tasks.mark_unknown(task.id, attempt.id, claim.lease_token, WORKER)
        result = tx.tasks.store_result(task.id, attempt.id, raw)
    return SimpleNamespace(source=source, task=task, claim=claim, attempt=attempt,
                           provider=provider, raw=raw, result=result,
                           evidence=list(provider.evidence))


def revoke_target(tx, owner, source, target):
    if target in ("material", "course", "semester"):
        return revoke_source(tx, owner, source, target)
    resource, resource_id = {
        "current_course": (Resource.COURSE, source.destination_course.id),
        "current_semester": (Resource.SEMESTER, source.destination_semester.id),
        "classroom": (Resource.CLASS_SESSION, source.classroom.id),
    }[target]
    current = {Resource.COURSE: tx.catalog.get_course,
               Resource.SEMESTER: tx.catalog.get_semester,
               Resource.CLASS_SESSION: tx.catalog.get_class_session}[resource](resource_id)
    return tx.tombstones.revoke(resource, current.id, current.revision, 809)


@pytest.mark.parametrize("target", ("material", "course", "semester", "current_course",
                                    "current_semester", "classroom"))
def test_revoked_source_is_quarantined_once_without_validating_or_starving_other_owner(
        target, task_db, scheduler, owners, tree, pg):
    state = source_saved(task_db, scheduler, owners, tree, pg)
    with task_db.transaction(owners.a) as tx:
        revoke_target(tx, owners.a, state.source, target)
    local, claim = quarantine_saved(state, "email_verified_at", task_db, scheduler, owners, pg)
    before = snapshot(pg, owners.a)
    assert local.reconcile(claim) is False
    current = persisted(pg, "tasks", id=state.task.id)[0]
    result = persisted(pg, "provider_results", id=state.result.id)[0]
    attempt = persisted(pg, "task_attempts", id=state.attempt.id)[0]
    assert current["state"] == attempt["state"] == "unknown"
    assert current["error_code"] == attempt["error_code"] == "ACCESS_REVOKED"
    assert current["may_have_charged"] is True and current["lease_expires_at"] is None
    assert current["lease_token"] == before["tasks"][0]["lease_token"] + 1
    assert current["revision"] == before["tasks"][0]["revision"] + 1
    assert result == before["provider_results"][0]
    assert result["validation_status"] == "pending" and result["validated_payload"] is None
    assert attempt["provider_outcome"] == before["task_attempts"][0]["provider_outcome"]
    assert persisted(pg, "operation_receipts", owner_id=owners.a.owner_id) == before["operation_receipts"]
    events = persisted(pg, "outbox_events", aggregate_id=state.task.id)
    assert len(events) == len(before["outbox_events"]) + 1
    assert events[-1]["payload"]["error_code"] == "ACCESS_REVOKED"
    assert state.task.id not in {item.id for item in local.candidates()}
    stable = snapshot(pg, owners.a)
    assert local.reconcile(claim) is False and local.run_once() == 0
    assert snapshot(pg, owners.a) == stable
    other = SimpleNamespace(a=owners.b, b=owners.a)
    following = saved_result(task_db, scheduler, other, pg)
    quarantine_saved(following, "password_hash", task_db, scheduler, other, pg)
    assert local.run_once() == 1
    assert persisted(pg, "tasks", id=following.task.id)[0]["state"] == "succeeded"
    assert snapshot(pg, owners.a) == stable
    assert state.provider.calls == following.provider.calls == 1
    with pytest.raises(InactiveOwner):
        with task_db.transaction(owners.a):
            pytest.fail("source quarantine reactivated the invalid identity")
    evidence("source-" + target, {"state": current["state"], "error": current["error_code"],
             "raw_pending": True, "repeat_unchanged": True, "following": "succeeded",
             "provider_calls": [state.provider.calls, following.provider.calls]})


def observe_block(pg, future, blocker, label):
    deadline = monotonic() + 4
    with pg.admin.connect().execution_options(isolation_level="AUTOCOMMIT") as monitor:
        while monotonic() < deadline:
            rows = monitor.execute(sa.text("""
                SELECT pid, wait_event_type, wait_event, pg_blocking_pids(pid) AS blockers
                FROM pg_stat_activity WHERE datname=current_database()
                  AND :blocker=ANY(pg_blocking_pids(pid)) AND wait_event_type='Lock'
            """), {"blocker": blocker}).mappings().all()
            if rows:
                evidence(label, [dict(row) for row in rows])
                return
            if future.done():
                pytest.fail("contender completed without the required PostgreSQL lock edge: "
                            + repr(future.result()))
            sleep(.005)
    pytest.fail("PostgreSQL did not report the required source-lock dependency")


@contextmanager
def atomic_revoker(pg, owner):
    """Exercise real guards with a synthetic atomic revoke/invalidate actor.

    Identity is valid only inside this privileged test transaction and becomes
    invalid again before COMMIT. Other connections never see it reactivated.
    This avoids disabling guards merely to stage an invalid-owner revocation.
    """
    with pg.admin.begin() as connection:
        connection.execute(sa.text("SELECT set_config('app.owner_id',:owner,true)"),
                           {"owner": str(owner.owner_id)})
        connection.execute(catalog_tables["users"].update().where(
            catalog_tables["users"].c.id == owner.owner_id).values(
                email_verified_at=sa.func.clock_timestamp()))
        catalog = PgCatalogRepository(connection, owner.owner_id)
        yield SimpleNamespace(connection=connection, catalog=catalog,
                              tombstones=PgTombstoneRepository(catalog))
        connection.execute(catalog_tables["users"].update().where(
            catalog_tables["users"].c.id == owner.owner_id).values(email_verified_at=None))


@pytest.mark.parametrize("target", ("material", "course", "semester"))
@pytest.mark.parametrize("winner", ("revocation", "reconciliation"))
def test_source_revocation_and_local_finish_serialize_in_both_orders(
        target, winner, task_db, scheduler, owners, tree, pg):
    state = source_saved(task_db, scheduler, owners, tree, pg, unknown=True)
    invalidate_identity(pg, owners.a, "email_verified_at")
    current = persisted(pg, "tasks", id=state.task.id)[0]
    claim = Claim(id=state.task.id, owner_id=owners.a.owner_id, kind=state.task.kind,
                  lease_token=current["lease_token"])
    local = PgLocalResultReconciler(task_db)
    before_raw = persisted(pg, "provider_results", id=state.result.id)[0]
    receipts = persisted(pg, "operation_receipts", owner_id=owners.a.owner_id)

    def revoke():
        with atomic_revoker(pg, owners.a) as tx:
            revoke_source(tx, owners.a, state.source, target)
        return "revoked"

    with ThreadPoolExecutor(max_workers=1) as pool:
        if winner == "revocation":
            with atomic_revoker(pg, owners.a) as tx:
                blocker = tx.connection.scalar(sa.text("SELECT pg_backend_pid()"))
                revoke_source(tx, owners.a, state.source, target)
                future = pool.submit(local.reconcile, claim)
                observe_block(pg, future, blocker, "revocation-first-" + target)
            assert future.result(timeout=10) is False
            assert persisted(pg, "tasks", id=state.task.id)[0]["state"] == "unknown"
            assert persisted(pg, "provider_results", id=state.result.id)[0] == before_raw
        else:
            with local._transaction(owners.a.owner_id) as connection:
                blocker = connection.scalar(sa.text("SELECT pg_backend_pid()"))
                assert connection.scalar(sa.text(
                    "SELECT classroom_web_reconcile_saved_result(:task,:token)"),
                    {"task": claim.id, "token": claim.lease_token}) is True
                future = pool.submit(revoke)
                observe_block(pg, future, blocker, "reconciliation-first-" + target)
            assert future.result(timeout=10) == "revoked"
            assert persisted(pg, "tasks", id=state.task.id)[0]["state"] == "succeeded"
            final_raw = persisted(pg, "provider_results", id=state.result.id)[0]
            assert final_raw["validation_status"] == "valid"
            assert_raw_immutable(before_raw, final_raw)
    with pytest.raises(InactiveOwner):
        with task_db.transaction(owners.a):
            pytest.fail("atomic synthetic revocation leaked an identity reactivation")
    assert state.task.id not in {item.id for item in local.candidates()}
    assert persisted(pg, "tasks", id=state.task.id)[0]["may_have_charged"] is True
    assert persisted(pg, "operation_receipts", owner_id=owners.a.owner_id) == receipts
    assert state.provider.calls == 1 and state.provider.evidence == state.evidence


def test_source_graph_move_during_discovery_rolls_back_then_next_poll_finishes(
        task_db, scheduler, owners, tree, pg):
    state = source_saved(task_db, scheduler, owners, tree, pg, unknown=True)
    with task_db.transaction(owners.a) as tx:
        destination = tx.catalog.create_course(state.source.destination_semester.id,
                                                "Synthetic third destination")
    invalidate_identity(pg, owners.a, "email_verified_at")
    before = snapshot(pg, owners.a)
    task = before["tasks"][0]
    claim = Claim(id=state.task.id, owner_id=owners.a.owner_id, kind=state.task.kind,
                  lease_token=task["lease_token"])
    local = PgLocalResultReconciler(task_db)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with atomic_revoker(pg, owners.a) as tx:
            blocker = tx.connection.scalar(sa.text("SELECT pg_backend_pid()"))
            tx.connection.execute(sa.select(catalog_tables["semesters"].c.id).where(
                catalog_tables["semesters"].c.id == state.source.destination_semester.id
            ).with_for_update())
            future = pool.submit(local.reconcile, claim)
            observe_block(pg, future, blocker, "source-discovery-move")
            moved = tx.catalog.move_class_session(state.source.classroom.id,
                state.source.classroom.revision, destination.id)
            assert moved.course_material_version_id == state.source.original.material.id
        assert future.result(timeout=10) is False
    assert snapshot(pg, owners.a) == before
    assert local.reconcile(claim) is True
    assert persisted(pg, "tasks", id=state.task.id)[0]["state"] == "succeeded"
    assert state.provider.calls == 1 and state.provider.evidence == state.evidence


def test_over_one_hundred_revoked_saved_results_leave_bounded_queue_for_later_owner(
        task_db, scheduler, owners, tree, pg):
    complete_identity(pg, owners.a)
    source = moved_source(task_db, owners.a, tree)
    with task_db.transaction(owners.a) as tx:
        tasks = [tx.tasks.enqueue("ai_question", {"schema_version": 1}, op_id=uuid4(),
                                  class_session_id=source.classroom.id) for _ in range(101)]
    identities = {task.id for task in tasks}
    claims = scheduler.claim(WORKER, lease_seconds=30, batch_size=100)
    claims += scheduler.claim(WORKER, lease_seconds=30, batch_size=100)
    assert {claim.id for claim in claims} == identities
    provider = RecordingProvider()
    intents = []
    with task_db.transaction(owners.a) as tx:
        for claim in claims:
            attempt = tx.tasks.prepare_attempt(claim.id, claim.lease_token, WORKER)
            tx.tasks.mark_submitting(claim.id, attempt.id, claim.lease_token, WORKER)
            task = tx.tasks.get(claim.id)
            intents.append((task, attempt))
    # Match the real dispatch boundary: every send intent commits before the
    # local Provider is called, and raw persistence is a separate transaction.
    for task, attempt in intents:
        raw = provider.submit(task, attempt)
        with task_db.transaction(owners.a) as tx:
            tx.tasks.store_result(task.id, attempt.id, raw)
    with task_db.transaction(owners.a) as tx:
        revoke_source(tx, owners.a, source, "material")
    assert provider.calls == 101
    submissions = list(provider.evidence)
    with pg.admin.begin() as connection:
        connection.execute(tables["tasks"].update().where(tables["tasks"].c.id.in_(identities))
                           .values(lease_expires_at=sa.func.clock_timestamp() - sa.text("interval '1 second'")))
    invalidate_identity(pg, owners.a, "email_verified_at")
    engine = worker(task_db, scheduler, provider)
    engine.recover_expired()
    engine.recover_expired()
    other = SimpleNamespace(a=owners.b, b=owners.a)
    following = saved_result(task_db, scheduler, other, pg)
    # This candidate deliberately belongs to page two. Do not use the helper
    # whose setup contract requires finding its candidate in the first page.
    invalidate_identity(pg, owners.b, "password_hash")
    expire(pg, following.task.id)
    worker(task_db, scheduler, following.provider).recover_expired()
    local = PgLocalResultReconciler(task_db)
    following_before = persisted(pg, "tasks", id=following.task.id)[0]
    assert following_before["state"] == "unknown" and following_before["may_have_charged"] is True
    first = local.candidates()
    assert len(first) == 100 and {claim.id for claim in first} <= identities
    assert local.run_once() == 0  # false means quarantined, rather than validated
    second = local.candidates()
    assert len(second) == 2 and following.task.id in {claim.id for claim in second}
    assert local.run_once() == 1
    assert local.candidates() == []
    result = persisted(pg, "tasks", id=following.task.id)[0]
    assert result["state"] == "succeeded"
    with pg.admin.connect() as connection:
        quarantined = connection.execute(sa.select(tables["tasks"]).where(
            tables["tasks"].c.id.in_(identities))).mappings().all()
        saved = connection.execute(sa.select(tables["provider_results"]).where(
            tables["provider_results"].c.owner_id == owners.a.owner_id)).mappings().all()
    assert len(quarantined) == len(saved) == 101
    assert all(row["state"] == "unknown" and row["error_code"] == "ACCESS_REVOKED"
               and row["may_have_charged"] and row["lease_expires_at"] is None for row in quarantined)
    assert all(row["validation_status"] == "pending" and row["validated_payload"] is None for row in saved)
    stable = snapshot(pg, owners.a)
    assert local.run_once() == 0 and snapshot(pg, owners.a) == stable
    assert provider.calls == 101 and provider.evidence == submissions and following.provider.calls == 1
    evidence("bounded-revoked-prefix", {"prefix": 101, "batch_sizes": [len(first), len(second)],
             "later_owner": result["state"], "provider_calls": [provider.calls, following.provider.calls],
             "remaining_candidates": 0})


def test_four_definer_owners_have_exact_restricted_flags_membership_and_acl(pg):
    expected_owners = {"classroom_web_identity_usable": I,
                       "classroom_web_attempt_submitted": M,
                       "classroom_web_local_reconcile_candidates": M,
                       "classroom_web_reconcile_saved_result": P}
    with pg.admin.connect() as connection:
        roles = [dict(row) for row in connection.execute(sa.text("""
            SELECT rolname,rolsuper,rolbypassrls,rolcanlogin,rolinherit,rolcreatedb,
                   rolcreaterole,rolreplication FROM pg_roles WHERE rolname=ANY(:roles)
        """), {"roles": list(INTERNAL_ROLES)}).mappings()]
        assert len(roles) == 3
        assert all(not row[key] for row in roles for key in row if key != "rolname")
        memberships = [dict(row) for row in connection.execute(sa.text("""
            SELECT r.rolname AS role,m.rolname AS member,g.rolname AS grantor,
                   a.admin_option,a.inherit_option,a.set_option FROM pg_auth_members a
            JOIN pg_roles r ON r.oid=a.roleid JOIN pg_roles m ON m.oid=a.member
            JOIN pg_roles g ON g.oid=a.grantor
            WHERE r.rolname=ANY(:roles) OR m.rolname=ANY(:roles)
        """), {"roles": list(INTERNAL_ROLES)}).mappings()]
        assert memberships == []
        functions = [dict(row) for row in connection.execute(sa.text("""
            SELECT p.proname,r.rolname,p.prosecdef,p.proconfig,p.oid,
                   p.proacl::text AS acl FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
            JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname=ANY(:names)
        """), {"names": list(expected_owners)}).mappings()]
        assert len(functions) == 4
        for row in functions:
            assert row["rolname"] == expected_owners[row["proname"]] and row["prosecdef"] is True
            assert any("search_path=" in value and "public" in value for value in row["proconfig"])
        wrapper_owner = connection.scalar(sa.text("""
            SELECT pg_get_userbyid(proowner) FROM pg_proc
            WHERE oid='classroom_web_owner_active()'::regprocedure
        """))
        expected_grants = {
            "classroom_web_identity_usable": {I, M, P, "classroom_web_task_dispatcher", wrapper_owner},
            "classroom_web_attempt_submitted": {M, "classroom_web_task_dispatcher"},
            "classroom_web_local_reconcile_candidates": {M, "classroom_web_runtime"},
            "classroom_web_reconcile_saved_result": {P, "classroom_web_runtime"},
        }
        for row in functions:
            acl = connection.execute(sa.text("""
                SELECT a.grantee,pg_get_userbyid(a.grantee) AS grantee_name,
                       a.privilege_type,a.is_grantable FROM pg_proc p
                CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                WHERE p.oid=:oid
            """), {"oid": row["oid"]}).mappings().all()
            assert {item["grantee_name"] for item in acl} == expected_grants[row["proname"]]
            assert all(item["grantee"] != 0 and item["privilege_type"] == "EXECUTE" for item in acl)
            assert not any(item["is_grantable"] for item in acl)
        assert connection.scalar(sa.text("""
            SELECT EXISTS(SELECT FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner
              WHERE r.rolname=ANY(:roles)) OR
              EXISTS(SELECT FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner
                WHERE r.rolname=ANY(:roles)) OR
              EXISTS(SELECT FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba
                WHERE r.rolname=ANY(:roles))
        """), {"roles": list(INTERNAL_ROLES)}) is False
        for role in INTERNAL_ROLES:
            assert connection.scalar(sa.text("SELECT has_schema_privilege(:role,'public','USAGE')"),
                                     {"role": role}) is True
            assert connection.scalar(sa.text("SELECT has_schema_privilege(:role,'public','CREATE')"),
                                     {"role": role}) is False
        evidence("function-role-acl", {"roles": roles, "memberships": memberships,
                                      "functions": functions})


SELECT_COLUMNS = {
    I: {"users": "id state deleted_at normalized_email email_verified_at password_hash"},
    M: {"tasks": "id owner_id kind lease_token state not_before lease_expires_at updated_at error_code",
        "task_attempts": "id owner_id task_id attempt_number raw_output_ref submitted_at provider_request_id provider_outcome state",
        "provider_results": "id owner_id attempt_id validation_status conflict_sha256"},
    P: {"tasks": "id owner_id kind state lease_token lease_expires_at revision class_session_id not_before error_code may_have_charged",
        "task_attempts": "id owner_id task_id attempt_number raw_output_ref provider_outcome state finished_at",
        "provider_results": "id owner_id attempt_id raw_output validation_status conflict_sha256",
        "semesters": "id owner_id deleted_at revision", "courses": "id owner_id semester_id deleted_at revision",
        "class_sessions": "id owner_id course_id course_material_version_id deleted_at revision",
        "course_material_versions": "id owner_id course_id",
        "deletion_tombstones": "owner_id resource_type resource_id",
        "owner_event_counters": "owner_id next_sequence"},
}
UPDATE_COLUMNS = {
    P: {"tasks": "state error_code may_have_charged lease_token leased_by lease_expires_at revision updated_at completed_at",
        "task_attempts": "state raw_output_ref provider_outcome error_code finished_at",
        "provider_results": "validation_status validated_payload",
        "semesters": "id", "courses": "id", "class_sessions": "id",
        "owner_event_counters": "next_sequence"},
}
INSERT_COLUMNS = {P: {
    "owner_event_counters": "id owner_id next_sequence",
    "outbox_events": "id owner_id event_sequence event_type aggregate_type aggregate_id aggregate_revision payload schema_version",
}}


def test_internal_role_effective_columns_match_approved_matrix(pg):
    expected = {"SELECT": SELECT_COLUMNS, "UPDATE": UPDATE_COLUMNS, "INSERT": INSERT_COLUMNS}
    with pg.admin.connect() as connection:
        rows = connection.execute(sa.text("""
            SELECT r.rolname,c.relname,a.attname,action.privilege,
              has_column_privilege(r.oid,c.oid,a.attnum,action.privilege) AS allowed
            FROM pg_roles r CROSS JOIN pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
            CROSS JOIN (VALUES('SELECT'),('UPDATE'),('INSERT')) action(privilege)
            WHERE r.rolname=ANY(:roles) AND n.nspname='public' AND c.relkind IN ('r','p')
              AND c.relname<>'alembic_version'
        """), {"roles": list(INTERNAL_ROLES)}).mappings().all()
        assert rows
        for row in rows:
            allowed = expected[row["privilege"]].get(row["rolname"], {}).get(row["relname"], "").split()
            assert row["allowed"] is (row["attname"] in allowed), dict(row)
        for role in INTERNAL_ROLES:
            forbidden = connection.scalar(sa.text("""
                SELECT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                  WHERE n.nspname='public' AND c.relkind IN ('r','p') AND
                    (has_table_privilege(:role,c.oid,'DELETE') OR
                     has_table_privilege(:role,c.oid,'TRUNCATE') OR
                     has_table_privilege(:role,c.oid,'TRIGGER') OR
                     has_table_privilege(:role,c.oid,'REFERENCES')))
            """), {"role": role})
            assert forbidden is False
    evidence("approved-column-matrix", {"effective_column_checks": len(rows), "roles": INTERNAL_ROLES})


@pytest.mark.parametrize("name", SOURCE_TABLES)
def test_processor_source_row_locks_work_but_real_writes_and_cross_owner_reads_fail(
        name, pg, owners, tree):
    resource_id = {"semesters": tree.semester.id, "courses": tree.course.id,
                   "class_sessions": tree.classroom.id}[name]
    with pg.admin.begin() as connection:
        policies = [dict(row) for row in connection.execute(sa.text("""
            SELECT p.polname,p.polcmd,p.polpermissive,
              pg_get_expr(p.polqual,p.polrelid) AS qual,
              pg_get_expr(p.polwithcheck,p.polrelid) AS check_expression
            FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
            WHERE c.relname=:table AND 'classroom_web_local_result_processor'::regrole=ANY(p.polroles)
        """), {"table": name}).mappings()]
        assert {row["polcmd"] for row in policies} == {"r", "w"}
        assert len(policies) == 2 and all(row["polpermissive"] for row in policies)
        update = next(row for row in policies if row["polcmd"] == "w")
        assert update["check_expression"] == "false"
        assert all("owner_id" in row["qual"] for row in policies)
        connection.exec_driver_sql(f"SET LOCAL ROLE {P}")
        connection.execute(sa.text("SELECT set_config('app.owner_id',:owner,true)"),
                           {"owner": str(owners.a.owner_id)})
        assert connection.scalar(sa.text(f"SELECT id FROM {name} WHERE id=:id FOR UPDATE"),
                                 {"id": resource_id}) == resource_id
        connection.execute(sa.text("SELECT set_config('app.owner_id',:owner,true)"),
                           {"owner": str(owners.b.owner_id)})
        assert connection.execute(sa.text(f"SELECT id FROM {name} WHERE id=:id FOR UPDATE"),
                                  {"id": resource_id}).all() == []
    with pytest.raises(sa.exc.DBAPIError) as denied:
        with pg.admin.begin() as connection:
            connection.exec_driver_sql(f"SET LOCAL ROLE {P}")
            connection.execute(sa.text("SELECT set_config('app.owner_id',:owner,true)"),
                               {"owner": str(owners.a.owner_id)})
            connection.execute(sa.text(f"UPDATE {name} SET id=id WHERE id=:id"), {"id": resource_id})
    assert denied.value.orig.sqlstate == "42501"
    evidence("source-policy-" + name, {"policies": policies, "for_update": "allowed",
             "real_update_sqlstate": denied.value.orig.sqlstate, "cross_owner": "zero rows"})


def test_runtime_cannot_set_internal_roles_or_turn_guc_into_direct_body_access(
        task_db, scheduler, owners, pg):
    state = saved_result(task_db, scheduler, owners, pg)
    local, claim = quarantine_saved(state, "password_hash", task_db, scheduler, owners, pg)
    for role in INTERNAL_ROLES:
        with pytest.raises(sa.exc.DBAPIError) as denied:
            with task_db.engine.begin() as connection:
                connection.exec_driver_sql(f"SET LOCAL ROLE {role}")
        assert denied.value.orig.sqlstate == "42501"
    for owner in (owners.a.owner_id, owners.b.owner_id, None):
        with task_db.engine.begin() as connection:
            connection.execute(sa.text("SELECT set_config('app.owner_id',:owner,true)"),
                               {"owner": "" if owner is None else str(owner)})
            assert connection.execute(sa.select(tables["provider_results"]).where(
                tables["provider_results"].c.id == state.result.id)).all() == []
    assert local.reconcile(replace(claim, owner_id=owners.b.owner_id)) is False
    assert local.reconcile(replace(claim, lease_token=claim.lease_token + 1)) is False
    # Runtime's approved fixed background capability still works; GUC does not
    # claim to authenticate the terminal user or grant direct body SELECT.
    assert local.reconcile(claim) is True
    assert state.provider.calls == 1


def test_permissive_policy_or_counterexample_and_actual_policy_clone(pg, owners, tree):
    """Demonstrate the OR hazard without altering any real policy or trigger."""
    name = "pr03a_policy_probe_" + uuid4().hex
    with pg.admin.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql(f"CREATE TABLE {name}(id uuid PRIMARY KEY,owner_id uuid NOT NULL)")
            connection.exec_driver_sql(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
            connection.exec_driver_sql(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
            connection.exec_driver_sql(f"GRANT SELECT(id,owner_id),UPDATE(id) ON {name} TO {P}")
            connection.execute(sa.text(f"INSERT INTO {name}(id,owner_id) VALUES(:id,:owner)"),
                               {"id": tree.semester.id, "owner": owners.a.owner_id})
            connection.exec_driver_sql(f"CREATE POLICY selected ON {name} FOR SELECT TO {P} USING(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid)")
            connection.exec_driver_sql(f"CREATE POLICY locked ON {name} FOR UPDATE TO {P} USING(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid) WITH CHECK(false)")
            connection.exec_driver_sql(f"SET LOCAL ROLE {P}")
            connection.execute(sa.text("SELECT set_config('app.owner_id',:owner,true)"),
                               {"owner": str(owners.a.owner_id)})
            assert connection.scalar(sa.text(f"SELECT id FROM {name} FOR UPDATE")) == tree.semester.id
            savepoint = connection.begin_nested()
            with pytest.raises(sa.exc.DBAPIError) as denied:
                connection.exec_driver_sql(f"UPDATE {name} SET id=id")
            assert denied.value.orig.sqlstate == "42501"
            savepoint.rollback()
            connection.exec_driver_sql("RESET ROLE")
            connection.exec_driver_sql(f"CREATE POLICY dangerous_all ON {name} FOR ALL TO {P} USING(true) WITH CHECK(true)")
            connection.exec_driver_sql(f"SET LOCAL ROLE {P}")
            assert connection.exec_driver_sql(f"UPDATE {name} SET id=id").rowcount == 1
            evidence("permissive-or-counterexample", {"separate_update_check_false": "42501",
                     "extra_for_all": "would permit UPDATE", "probe_rolled_back": True})
        finally:
            transaction.rollback()
