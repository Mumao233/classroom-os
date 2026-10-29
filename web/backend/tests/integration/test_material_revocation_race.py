"""Real PostgreSQL row-lock coordination for current material and tombstones."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest
import sqlalchemy as sa

from classroom_web.domain.data import Gone
from classroom_web.infrastructure.schema import tables


def _pid(tx):
    return tx.connection.scalar(sa.text("SELECT pg_backend_pid()"))


def _point_at(tx, course_id, material_id, revision):
    result = tx.connection.execute(tables["courses"].update().where(
        tables["courses"].c.id == course_id,
    ).values(current_material_version_id=material_id, revision=revision))
    assert result.rowcount == 1
    # The originally vulnerable transaction can finish its deferred validation
    # before publishing the pointer. A lock must still protect this whole gap.
    tx.connection.execute(sa.text("SET CONSTRAINTS check_current_material IMMEDIATE"))


def _revoke_material(tx, owner, material, *, request_id):
    tx.connection.execute(tables["deletion_requests"].insert().values(
        id=request_id, owner_id=owner.owner_id, scope_type="course_material_version",
        scope_id=material.id, state="access_revoked",
        access_revoked_at=sa.func.transaction_timestamp(),
    ))
    tx.connection.execute(tables["deletion_tombstones"].insert().values(
        id=uuid4(), owner_id=owner.owner_id, request_id=request_id,
        resource_type="course_material_version", resource_id=material.id,
        deleted_revision=material.revision, erasure_epoch=1, ledger_sequence=201,
    ))
    tx.connection.execute(sa.text("SET CONSTRAINTS check_revocation IMMEDIATE"))


def _assert_database_waits_for(pg, future, *, waiter_pid, blocker_pid):
    """Observe an actual backend lock dependency before releasing the winner."""
    assert waiter_pid != blocker_pid
    deadline = monotonic() + 3
    with pg.admin.connect().execution_options(isolation_level="AUTOCOMMIT") as monitor:
        while monotonic() < deadline:
            blocked = monitor.execute(sa.text("""
                SELECT CAST(:blocker AS integer) = ANY(pg_blocking_pids(CAST(:waiter AS integer)))
            """), {"blocker": blocker_pid, "waiter": waiter_pid}).scalar_one()
            if blocked:
                return
            if future.done():
                pytest.fail("competing transaction finished without waiting for the owning course lock: "
                            + repr(future.result()))
    pytest.fail("PostgreSQL did not report the expected course-lock dependency")


def _append_v2(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        return tx.catalog.update_material(tree.course.id, 1, syllabus="Synthetic material v2",
                                          glossary=[], language="auto")


@pytest.mark.parametrize("winner", ["pointer", "revocation"])
def test_material_pointer_and_tombstone_serialize_both_actor_orders(pg, owners, tree, winner):
    """The already-validated winner commits; the observed waiter rejects 23514."""
    current = _append_v2(pg, owners, tree)
    request_id = uuid4()
    started = Event()
    worker = {}

    def compete():
        try:
            with pg.db.transaction(owners.a) as tx:
                worker["pid"] = _pid(tx)
                started.set()
                if winner == "pointer":
                    _revoke_material(tx, owners.a, tree.material, request_id=request_id)
                else:
                    _point_at(tx, tree.course.id, tree.material.id, 3)
        except sa.exc.DBAPIError as error:
            return error.orig.sqlstate
        return "committed"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with pg.db.transaction(owners.a) as tx:
            winner_pid = _pid(tx)
            if winner == "pointer":
                _point_at(tx, tree.course.id, tree.material.id, 3)
            else:
                _revoke_material(tx, owners.a, tree.material, request_id=request_id)
            future = pool.submit(compete)
            assert started.wait(timeout=10), "competing runtime connection did not start"
            _assert_database_waits_for(pg, future, waiter_pid=worker["pid"], blocker_pid=winner_pid)
            # Leaving this context commits only after PostgreSQL has reported
            # that the other runtime connection is waiting for this one.
        assert future.result(timeout=15) == "23514"

    with pg.db.transaction(owners.a) as tx:
        course = tx.catalog.get_course(tree.course.id)
        assert tx.catalog.get_material(current.id).id == current.id
        if winner == "pointer":
            assert course.current_material_version_id == tree.material.id
            assert course.revision == 3
            assert tx.catalog.get_material(tree.material.id).id == tree.material.id
        else:
            assert course.current_material_version_id == current.id
            assert course.revision == 2
            with pytest.raises(Gone):
                tx.catalog.get_material(tree.material.id)
    with pg.admin.connect() as connection:
        tombstone = connection.scalar(sa.select(sa.func.count()).select_from(tables["deletion_tombstones"])
                                      .where(tables["deletion_tombstones"].c.resource_id == tree.material.id))
        request = connection.scalar(sa.select(sa.func.count()).select_from(tables["deletion_requests"])
                                    .where(tables["deletion_requests"].c.id == request_id))
        expected = int(winner == "revocation")
        assert tombstone == request == expected


def test_switch_away_then_revoke_old_material_in_one_transaction(pg, owners, tree):
    """The coordination lock must preserve the legitimate atomic replacement."""
    with pg.db.transaction(owners.a) as tx:
        current = tx.catalog.update_material(tree.course.id, 1, syllabus="Replacement before revocation",
                                             glossary=[], language="auto")
        _revoke_material(tx, owners.a, tree.material, request_id=uuid4())
        tx.connection.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
    with pg.db.transaction(owners.a) as tx:
        assert tx.catalog.get_course(tree.course.id).current_material_version_id == current.id
        assert tx.catalog.get_material(current.id).syllabus == "Replacement before revocation"
        with pytest.raises(Gone):
            tx.catalog.get_material(tree.material.id)


def test_repository_append_and_historical_revocation_wait_without_deadlock(pg, owners, tree):
    """An append holds UPDATE before material insertion; revocation waits then succeeds."""
    historical = _append_v2(pg, owners, tree)
    started = Event()
    worker = {}

    def revoke_history():
        with pg.db.transaction(owners.a) as tx:
            worker["pid"] = _pid(tx)
            started.set()
            _revoke_material(tx, owners.a, tree.material, request_id=uuid4())
        return "committed"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with pg.db.transaction(owners.a) as tx:
            winner_pid = _pid(tx)
            current = tx.catalog.update_material(tree.course.id, 2, syllabus="Concurrent append v3",
                                                 glossary=[], language="auto")
            tx.connection.execute(sa.text("SET CONSTRAINTS check_current_material IMMEDIATE"))
            future = pool.submit(revoke_history)
            assert started.wait(timeout=10), "competing runtime connection did not start"
            _assert_database_waits_for(pg, future, waiter_pid=worker["pid"], blocker_pid=winner_pid)
        assert future.result(timeout=15) == "committed"

    with pg.db.transaction(owners.a) as tx:
        assert tx.catalog.get_course(tree.course.id).current_material_version_id == current.id
        assert current.revision == 3
        assert tx.catalog.get_material(historical.id).id == historical.id
        assert tx.catalog.get_material(current.id).syllabus == "Concurrent append v3"
        with pytest.raises(Gone):
            tx.catalog.get_material(tree.material.id)
