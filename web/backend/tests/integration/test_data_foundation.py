"""Actual PostgreSQL transactions, constraints, RLS, migrations and repositories."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import hashlib
import json
from pathlib import Path
import shutil
from threading import Barrier, Event
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa

from classroom_web.domain.data import Gone, InactiveOwner, NotFound, Resource, RevisionConflict
from classroom_web.infrastructure.database import Database
from classroom_web.infrastructure.schema import metadata, tables
from classroom_web.infrastructure.task_schema import tables as task_tables
from classroom_web.infrastructure.identity_schema import tables as identity_tables

from conftest import BACKEND, ROOT, migration_config


def scoped(connection, owner):
    connection.execute(sa.text("SELECT set_config('app.owner_id', :owner, true)"),
                       {"owner": str(owner.owner_id)})


def test_only_required_tables_and_contract_columns_exist(pg):
    expected = {"users", "semesters", "courses", "course_material_versions", "class_sessions",
                "deletion_requests", "deletion_tombstones"}
    inspector = sa.inspect(pg.admin)
    # Additive PR-02b tables are required too; every original catalog column
    # assertion below remains unchanged and full metadata still must match.
    assert set(inspector.get_table_names()) == expected | set(task_tables) | set(identity_tables) | {"alembic_version"}
    contract = json.loads((ROOT / "docs/web-alpha/architecture/contracts/domain-model.json").read_text("utf-8"))
    for table in contract["tables"]:
        if table["name"] in expected:
            actual = {column["name"]: column for column in inspector.get_columns(table["name"])}
            assert set(actual) == {column["name"] for column in table["columns"]}
            for column in table["columns"]:
                assert actual[column["name"]]["nullable"] == column["nullable"]
    with pg.admin.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), metadata)
    assert differences == []


def test_upgrade_is_repeatable_and_has_one_head(pg):
    command.upgrade(pg.config, "head")
    command.upgrade(pg.config, "head")
    heads = ScriptDirectory.from_config(pg.config).get_heads()
    assert len(heads) == 1
    with pg.admin.connect() as connection:
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == heads[0]


def test_explicit_migration_config_is_not_redirected_by_environment(pg, monkeypatch):
    monkeypatch.setenv("CLASSROOM_WEB_DATABASE_URL",
                       "postgresql+psycopg://synthetic_trap@127.0.0.1:1/pr02a_do_not_connect")
    command.upgrade(migration_config(pg.admin_url.render_as_string(hide_password=False)), "head")
    with pg.admin.connect() as connection:
        assert connection.execute(sa.text("SELECT current_database()")).scalar_one() == pg.name


def test_runtime_role_is_restricted_and_force_rls_is_enabled(pg):
    with pg.db.engine.connect() as connection:
        role = connection.execute(sa.text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")).one()
        assert not role.rolsuper and not role.rolbypassrls
        private = connection.execute(sa.text("""
            SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,
                   pg_get_userbyid(c.relowner)=current_user AS owns
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname IN
              ('semesters','courses','course_material_versions','class_sessions','deletion_requests','deletion_tombstones')
        """)).all()
        assert len(private) == 6
        assert all(row.relrowsecurity and row.relforcerowsecurity and not row.owns for row in private)
        assert not connection.execute(sa.text("SELECT has_table_privilege(current_user,'users','SELECT')")).scalar_one()
    with pytest.raises(sa.exc.DBAPIError):
        with pg.db.engine.begin() as connection:
            connection.execute(sa.text("SELECT * FROM users"))


def test_privileged_migration_connection_cannot_be_used_for_application_uow(pg, owners):
    unsafe = Database(pg.admin_url.render_as_string(hide_password=False))
    try:
        with pytest.raises(PermissionError):
            with unsafe.transaction(owners.a):
                pytest.fail("migration identity entered runtime UoW")
    finally:
        unsafe.dispose()


def test_empty_owner_denies_reads_and_writes(pg, owners, tree):
    with pg.db.engine.begin() as connection:
        connection.execute(sa.text("SELECT set_config('app.owner_id','',true)"))
        assert connection.execute(sa.select(tables["semesters"])).all() == []
    with pytest.raises(sa.exc.DBAPIError):
        with pg.db.engine.begin() as connection:
            connection.execute(sa.text("SELECT set_config('app.owner_id','',true)"))
            connection.execute(tables["semesters"].insert().values(id=uuid4(), owner_id=owners.a.owner_id, name="Forbidden"))


def test_rls_and_admin_have_no_cross_owner_read_update_or_insert(pg, owners, tree):
    with pg.db.transaction(owners.b) as tx:
        assert tx.connection.execute(sa.select(tables["courses"])).all() == []
        result = tx.connection.execute(tables["courses"].update().where(tables["courses"].c.id == tree.course.id)
                                       .values(name="Must remain private", revision=2))
        assert result.rowcount == 0
        with pytest.raises(NotFound):
            tx.catalog.get_course(tree.course.id)
    with pytest.raises(sa.exc.DBAPIError):
        with pg.db.transaction(owners.b) as tx:
            tx.connection.execute(tables["semesters"].insert().values(id=uuid4(), owner_id=owners.a.owner_id, name="Forbidden"))


def test_owner_scope_does_not_leak_on_commit_or_rollback(pg, owners, tree):
    for fail in (False, True):
        try:
            with pg.db.transaction(owners.a) as tx:
                assert tx.catalog.get_course(tree.course.id).id == tree.course.id
                if fail:
                    raise RuntimeError("synthetic rollback")
        except RuntimeError:
            pass
        with pg.db.engine.connect() as connection:
            assert connection.execute(sa.text("SELECT nullif(current_setting('app.owner_id',true),'')")).scalar_one() is None
            assert connection.execute(sa.select(tables["courses"])).all() == []
        with pg.db.transaction(owners.b) as tx:
            with pytest.raises(NotFound):
                tx.catalog.get_course(tree.course.id)


def test_disabled_owner_is_denied_by_uow_and_rls(pg, owners, tree):
    with pg.admin.begin() as connection:
        connection.execute(tables["users"].update().where(tables["users"].c.id == owners.a.owner_id)
                           .values(state="disabled", revision=2))
    with pytest.raises(InactiveOwner):
        with pg.db.transaction(owners.a):
            pytest.fail("inactive owner entered application transaction")
    with pg.db.engine.begin() as connection:
        scoped(connection, owners.a)
        assert connection.execute(sa.select(tables["courses"])).all() == []


def test_owner_change_cannot_bypass_rls_with_check(pg, owners, tree):
    with pytest.raises(sa.exc.DBAPIError):
        with pg.db.transaction(owners.a) as tx:
            tx.connection.execute(tables["semesters"].update().where(tables["semesters"].c.id == tree.semester.id)
                                  .values(owner_id=owners.b.owner_id, revision=2))


def test_cross_owner_composite_fk_rejects_privileged_sql(pg, owners, tree):
    with pytest.raises(sa.exc.IntegrityError) as caught:
        with pg.admin.begin() as connection:
            scoped(connection, owners.b)
            # Remove only the application guard in this rolled-back transaction:
            # the owner composite FK itself must still reject the relationship.
            connection.exec_driver_sql("ALTER TABLE courses DISABLE TRIGGER guard_course")
            connection.execute(tables["courses"].insert().values(
                id=uuid4(), owner_id=owners.b.owner_id, semester_id=tree.semester.id,
                name="Cross-owner", language="auto", color_token="blue", icon_key="book"))
    assert caught.value.orig.sqlstate == "23503"


def test_same_names_are_independent_and_empty_v1_is_atomic(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        other = tx.catalog.create_course(tree.semester.id, tree.course.name)
        material = tx.catalog.get_material(other.current_material_version_id)
        assert other.id != tree.course.id
        assert material.course_id == other.id and material.revision == 1
        assert material.syllabus == "" and list(material.glossary) == []
        assert other.language == material.language == "auto"
        assert len(material.content_sha256) == 64
        assert material.id != tree.material.id


def test_course_creation_failure_rolls_back_every_row(pg, owners, tree):
    created = None
    with pytest.raises(RuntimeError):
        with pg.db.transaction(owners.a) as tx:
            created = tx.catalog.create_course(tree.semester.id, "Never committed")
            raise RuntimeError("synthetic application failure")
    with pg.admin.connect() as connection:
        assert connection.execute(sa.select(tables["courses"].c.id).where(tables["courses"].c.id == created.id)).first() is None
        assert connection.execute(sa.select(tables["course_material_versions"].c.id)
                                  .where(tables["course_material_versions"].c.course_id == created.id)).first() is None


def test_material_edit_creates_immutable_version_and_preserves_class_snapshot(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        new = tx.catalog.update_material(tree.course.id, tree.material.revision, syllabus="Synthetic v2",
                                         glossary=[{"term": "CAS", "meaning": "compare and swap", "language": "en"}], language="en")
        old = tx.catalog.get_material(tree.material.id)
        classroom = tx.catalog.get_class_session(tree.classroom.id)
        assert new.id != old.id and new.revision == 2
        assert old.syllabus == "" and classroom.course_material_version_id == old.id
        assert tx.catalog.get_course(tree.course.id).current_material_version_id == new.id
        assert new.content_sha256 != old.content_sha256


def test_material_conflict_does_not_append_orphan_version(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        tx.catalog.update_material(tree.course.id, 1, syllabus="Committed", glossary=[], language="auto")
    with pytest.raises(RevisionConflict):
        with pg.db.transaction(owners.a) as tx:
            tx.catalog.update_material(tree.course.id, 1, syllabus="Conflicting", glossary=[], language="auto")
    with pg.admin.connect() as connection:
        assert connection.execute(sa.select(sa.func.count()).select_from(tables["course_material_versions"])
                                  .where(tables["course_material_versions"].c.course_id == tree.course.id)).scalar_one() == 2


def test_current_material_pointer_must_belong_to_same_course(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        other = tx.catalog.create_course(tree.semester.id, "Other course")
    with pytest.raises(sa.exc.DBAPIError):
        with pg.admin.begin() as connection:
            scoped(connection, owners.a)
            connection.execute(tables["courses"].update().where(tables["courses"].c.id == tree.course.id)
                               .values(current_material_version_id=other.current_material_version_id, revision=2))


def test_material_rows_are_immutable_even_with_privileged_sql(pg, owners, tree):
    with pytest.raises(sa.exc.DBAPIError):
        with pg.admin.begin() as connection:
            scoped(connection, owners.a)
            connection.execute(tables["course_material_versions"].update()
                               .where(tables["course_material_versions"].c.id == tree.material.id).values(syllabus="Tampered"))


def test_course_without_material_is_rejected_at_commit(pg, owners, tree):
    with pytest.raises(sa.exc.DBAPIError):
        with pg.admin.begin() as connection:
            scoped(connection, owners.a)
            connection.execute(tables["courses"].insert().values(
                id=uuid4(), owner_id=owners.a.owner_id, semester_id=tree.semester.id,
                name="No material", language="auto", color_token="blue", icon_key="book"))


def test_class_creation_rejects_material_from_other_course(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        other = tx.catalog.create_course(tree.semester.id, "Other course")
    with pytest.raises(sa.exc.DBAPIError):
        with pg.admin.begin() as connection:
            scoped(connection, owners.a)
            connection.execute(tables["class_sessions"].insert().values(
                id=uuid4(), owner_id=owners.a.owner_id, course_id=other.id, title="Invalid snapshot",
                course_material_version_id=tree.material.id, capture_status="idle"))


def test_reassignment_preserves_ids_material_and_clock(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        semester = tx.catalog.create_semester("Destination")
        destination = tx.catalog.create_course(semester.id, "Destination course")
        moved_course = tx.catalog.move_course(tree.course.id, 1, semester.id)
        moved_class = tx.catalog.move_class_session(tree.classroom.id, 1, destination.id)
        assert moved_course.id == tree.course.id and moved_course.semester_id == semester.id
        assert moved_course.current_material_version_id == tree.material.id
        assert moved_class.id == tree.classroom.id and moved_class.course_id == destination.id
        assert moved_class.course_material_version_id == tree.material.id
        assert moved_class.started_at == tree.classroom.started_at
        assert moved_class.ended_at == tree.classroom.ended_at


def test_class_material_snapshot_cannot_be_changed_after_creation(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        version = tx.catalog.update_material(tree.course.id, 1, syllabus="Future default", glossary=[], language="auto")
    with pytest.raises(sa.exc.DBAPIError):
        with pg.admin.begin() as connection:
            scoped(connection, owners.a)
            connection.execute(tables["class_sessions"].update().where(tables["class_sessions"].c.id == tree.classroom.id)
                               .values(course_material_version_id=version.id, revision=2))


@pytest.mark.parametrize("column", ["scheduled_class_ref", "adopted_transcript_version_id"])
def test_reserved_and_not_yet_migrated_pointers_fail_closed(pg, owners, tree, column):
    with pytest.raises(sa.exc.DBAPIError):
        with pg.admin.begin() as connection:
            scoped(connection, owners.a)
            connection.execute(tables["class_sessions"].update().where(tables["class_sessions"].c.id == tree.classroom.id)
                               .values(**{column: uuid4(), "revision": 2}))


def test_semester_dates_and_revision_enforced_by_postgresql(pg, owners):
    with pytest.raises(sa.exc.IntegrityError):
        with pg.admin.begin() as connection:
            scoped(connection, owners.a)
            connection.execute(tables["semesters"].insert().values(
                id=uuid4(), owner_id=owners.a.owner_id, name="Invalid dates",
                start_date=date(2026, 10, 2), end_date=date(2026, 10, 1)))
    with pytest.raises(sa.exc.IntegrityError):
        with pg.admin.begin() as connection:
            scoped(connection, owners.a)
            connection.execute(tables["semesters"].insert().values(
                id=uuid4(), owner_id=owners.a.owner_id, name="Invalid revision", revision=0))


def test_concurrent_cas_has_exactly_one_winner(pg, owners, tree):
    barrier = Barrier(2)

    def save(name):
        barrier.wait(timeout=10)
        try:
            with pg.db.transaction(owners.a) as tx:
                course = tx.catalog.rename_course(tree.course.id, 1, name)
                return ("applied", course.name, course.revision)
        except RevisionConflict:
            return ("conflict", name, 1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(save, "Device A")
        b = pool.submit(save, "Device B")
        outcomes = [a.result(timeout=20), b.result(timeout=20)]
    assert sorted(item[0] for item in outcomes) == ["applied", "conflict"]
    winner = next(item for item in outcomes if item[0] == "applied")
    with pg.db.transaction(owners.a) as tx:
        saved = tx.catalog.get_course(tree.course.id)
        assert saved.name == winner[1] and saved.revision == 2


def test_concurrent_material_cas_has_one_winner_and_no_orphan_version(pg, owners, tree):
    barrier = Barrier(2)

    def save(syllabus):
        barrier.wait(timeout=10)
        try:
            with pg.db.transaction(owners.a) as tx:
                version = tx.catalog.update_material(tree.course.id, 1, syllabus=syllabus,
                                                     glossary=[], language="auto")
                return ("applied", version.id)
        except RevisionConflict:
            return ("conflict", None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(save, "Material A"), pool.submit(save, "Material B")
        outcomes = [a.result(timeout=20), b.result(timeout=20)]
    assert sorted(item[0] for item in outcomes) == ["applied", "conflict"]
    with pg.admin.connect() as connection:
        assert connection.execute(sa.select(sa.func.count()).select_from(tables["course_material_versions"])
                                  .where(tables["course_material_versions"].c.course_id == tree.course.id)).scalar_one() == 2
    with pg.db.transaction(owners.a) as tx:
        winner = next(item[1] for item in outcomes if item[0] == "applied")
        assert tx.catalog.get_course(tree.course.id).current_material_version_id == winner


def test_tombstone_blocks_read_stale_write_and_descendant_creation(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        tx.tombstones.revoke(Resource.COURSE, tree.course.id, 1, ledger_sequence=100)
    with pg.db.transaction(owners.a) as tx:
        with pytest.raises(Gone):
            tx.catalog.get_course(tree.course.id)
        with pytest.raises(Gone):
            tx.catalog.get_class_session(tree.classroom.id)
        with pytest.raises(Gone):
            tx.catalog.rename_course(tree.course.id, 1, "Late write")
        with pytest.raises(Gone):
            tx.catalog.create_class_session(tree.course.id, "Late child")
    with pg.db.transaction(owners.b) as tx:
        with pytest.raises(NotFound):
            tx.catalog.get_course(tree.course.id)


def test_tombstone_transaction_failure_restores_resource(pg, owners, tree):
    with pytest.raises(RuntimeError):
        with pg.db.transaction(owners.a) as tx:
            tx.tombstones.revoke(Resource.COURSE, tree.course.id, 1, ledger_sequence=101)
            raise RuntimeError("synthetic failure before commit")
    with pg.db.transaction(owners.a) as tx:
        assert tx.catalog.get_course(tree.course.id).revision == 1
    with pg.admin.connect() as connection:
        assert connection.execute(sa.select(tables["deletion_tombstones"].c.id)
                                  .where(tables["deletion_tombstones"].c.resource_id == tree.course.id)).first() is None
        assert connection.execute(sa.select(tables["deletion_requests"].c.id)
                                  .where(tables["deletion_requests"].c.scope_id == tree.course.id)).first() is None


def test_parent_tombstone_revokes_descendant_reads_and_moves(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        target_semester = tx.catalog.create_semester("Live destination")
        target = tx.catalog.create_course(target_semester.id, "Live destination course")
    with pg.db.transaction(owners.a) as tx:
        tx.tombstones.revoke(Resource.SEMESTER, tree.semester.id, 1, ledger_sequence=104)
    with pg.db.transaction(owners.a) as tx:
        with pytest.raises(Gone):
            tx.catalog.get_course(tree.course.id)
        with pytest.raises(Gone):
            tx.catalog.get_class_session(tree.classroom.id)
        with pytest.raises(Gone):
            tx.catalog.move_class_session(tree.classroom.id, 1, target.id)


def test_raw_sql_cannot_resurrect_course_by_moving_out_of_revoked_semester(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        destination = tx.catalog.create_semester("Live destination")
    with pg.db.transaction(owners.a) as tx:
        tx.tombstones.revoke(Resource.SEMESTER, tree.semester.id, 1, ledger_sequence=109)
    with pytest.raises(sa.exc.DBAPIError):
        with pg.db.transaction(owners.a) as tx:
            tx.connection.execute(tables["courses"].update().where(tables["courses"].c.id == tree.course.id)
                                  .values(semester_id=destination.id, revision=2))


def test_raw_sql_cannot_resurrect_class_by_moving_out_of_revoked_course(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        destination = tx.catalog.create_course(tree.semester.id, "Live destination")
    with pg.db.transaction(owners.a) as tx:
        tx.tombstones.revoke(Resource.COURSE, tree.course.id, 1, ledger_sequence=110)
    with pytest.raises(sa.exc.DBAPIError):
        with pg.db.transaction(owners.a) as tx:
            tx.connection.execute(tables["class_sessions"].update().where(tables["class_sessions"].c.id == tree.classroom.id)
                                  .values(course_id=destination.id, revision=2))


def test_deleting_course_does_not_hide_invalid_same_transaction_material_pointer(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        other = tx.catalog.create_course(tree.semester.id, "Other course")
    with pytest.raises(sa.exc.DBAPIError):
        with pg.db.transaction(owners.a) as tx:
            tx.connection.execute(tables["courses"].update().where(tables["courses"].c.id == tree.course.id)
                                  .values(current_material_version_id=other.current_material_version_id, revision=2))
            tx.tombstones.revoke(Resource.COURSE, tree.course.id, 2, ledger_sequence=111)
    with pg.db.transaction(owners.a) as tx:
        course = tx.catalog.get_course(tree.course.id)
        assert course.revision == 1 and course.current_material_version_id == tree.material.id


def test_tombstones_are_immutable_and_minimal(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        tx.tombstones.revoke(Resource.COURSE, tree.course.id, 1, ledger_sequence=105)
    with pg.admin.connect() as connection:
        tombstone = connection.execute(sa.select(tables["deletion_tombstones"])
                                       .where(tables["deletion_tombstones"].c.resource_id == tree.course.id)).mappings().one()
        assert "name" not in tombstone and "content" not in tombstone
        assert tombstone["deleted_revision"] == 2
    for operation in (tables["deletion_tombstones"].update().values(ledger_sequence=106),
                      tables["deletion_tombstones"].delete()):
        with pytest.raises(sa.exc.DBAPIError):
            with pg.admin.begin() as connection:
                scoped(connection, owners.a)
                connection.execute(operation.where(tables["deletion_tombstones"].c.resource_id == tree.course.id))


def test_tombstone_requires_request_for_exact_same_owned_scope(pg, owners, tree):
    request = uuid4()
    with pytest.raises(sa.exc.DBAPIError):
        with pg.db.transaction(owners.a) as tx:
            tx.connection.execute(tables["deletion_requests"].insert().values(
                id=request, owner_id=owners.a.owner_id, scope_type="semester", scope_id=tree.semester.id,
                state="access_revoked", access_revoked_at=sa.func.transaction_timestamp()))
            tx.connection.execute(tables["deletion_tombstones"].insert().values(
                id=uuid4(), owner_id=owners.a.owner_id, request_id=request,
                resource_type="course", resource_id=tree.course.id,
                deleted_revision=2, erasure_epoch=1, ledger_sequence=107))


def test_historical_material_tombstone_precedes_owned_material_read(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        current = tx.catalog.update_material(tree.course.id, 1, syllabus="New current version", glossary=[], language="auto")
    with pg.db.transaction(owners.a) as tx:
        request = uuid4()
        tx.connection.execute(tables["deletion_requests"].insert().values(
            id=request, owner_id=owners.a.owner_id, scope_type="course_material_version", scope_id=tree.material.id,
            state="access_revoked", access_revoked_at=sa.func.transaction_timestamp()))
        tx.connection.execute(tables["deletion_tombstones"].insert().values(
            id=uuid4(), owner_id=owners.a.owner_id, request_id=request,
            resource_type="course_material_version", resource_id=tree.material.id,
            deleted_revision=tree.material.revision, erasure_epoch=1, ledger_sequence=108))
    with pg.db.transaction(owners.a) as tx:
        with pytest.raises(Gone):
            tx.catalog.get_material(tree.material.id)
        assert tx.catalog.get_material(current.id).id == current.id
    with pg.db.transaction(owners.b) as tx:
        with pytest.raises(NotFound):
            tx.catalog.get_material(tree.material.id)


def test_soft_delete_without_tombstone_fails_at_commit_and_rolls_back(pg, owners, tree):
    with pytest.raises(sa.exc.DBAPIError):
        with pg.db.transaction(owners.a) as tx:
            tx.connection.execute(tables["courses"].update().where(tables["courses"].c.id == tree.course.id)
                                  .values(deleted_at=sa.func.transaction_timestamp(), revision=2))
    with pg.db.transaction(owners.a) as tx:
        course = tx.catalog.get_course(tree.course.id)
        assert course.deleted_at is None and course.revision == 1


def test_deleted_resource_cannot_be_resurrected_by_direct_sql(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        tx.tombstones.revoke(Resource.COURSE, tree.course.id, 1, ledger_sequence=102)
    with pytest.raises(sa.exc.DBAPIError):
        with pg.admin.begin() as connection:
            scoped(connection, owners.a)
            connection.execute(tables["courses"].update().where(tables["courses"].c.id == tree.course.id)
                               .values(deleted_at=None, name="Resurrected", revision=3))
    with pytest.raises(sa.exc.DBAPIError):
        with pg.admin.begin() as connection:
            scoped(connection, owners.a)
            connection.execute(tables["class_sessions"].insert().values(
                id=uuid4(), owner_id=owners.a.owner_id, course_id=tree.course.id, title="Late direct child",
                course_material_version_id=tree.material.id, capture_status="idle"))


def test_delete_and_late_cas_are_serialized(pg, owners, tree):
    revoked, attempted, committed = Event(), Event(), Event()

    def deletion():
        with pg.db.transaction(owners.a) as tx:
            tx.tombstones.revoke(Resource.COURSE, tree.course.id, 1, ledger_sequence=103)
            revoked.set()
            assert attempted.wait(timeout=10)
        committed.set()

    def late_write():
        assert revoked.wait(timeout=10)
        attempted.set()
        try:
            with pg.db.transaction(owners.a) as tx:
                tx.catalog.rename_course(tree.course.id, 1, "Late competing edit")
        except Gone:
            return "gone"
        return "unexpected success"

    with ThreadPoolExecutor(max_workers=2) as pool:
        d = pool.submit(deletion)
        w = pool.submit(late_write)
        d.result(timeout=20)
        assert w.result(timeout=20) == "gone"
    assert committed.is_set()


def test_migration_failure_rolls_back_ddl_data_and_revision(pg, owners, tree):
    scratch = ROOT / "work/pr02a/test-migrations" / uuid4().hex
    scratch.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BACKEND / "migrations", scratch)
    head = ScriptDirectory.from_config(pg.config).get_current_head()
    revision = scratch / "versions/ffff_synthetic_failure.py"
    revision.write_text(
        "from alembic import op\n"
        "import sqlalchemy as sa\n"
        "revision = 'ffff_synthetic_failure'\n"
        f"down_revision = {head!r}\n"
        "branch_labels = None\ndepends_on = None\n"
        "def upgrade():\n"
        "    op.execute('CREATE TABLE pr02a_failed_ddl (id integer)')\n"
        f"    op.execute(\"SELECT set_config('app.owner_id','{owners.a.owner_id}',true)\")\n"
        f"    op.execute(\"UPDATE semesters SET name='Failed migration',revision=revision+1 WHERE id='{tree.semester.id}'\")\n"
        "    op.execute('SELECT 1/0')\n"
        "def downgrade():\n    raise RuntimeError('synthetic only')\n", encoding="utf-8")
    config = migration_config(pg.admin_url.render_as_string(hide_password=False), scratch)
    with pytest.raises(sa.exc.DBAPIError) as caught:
        command.upgrade(config, "head")
    assert caught.value.orig.sqlstate == "22012"
    with pg.admin.connect() as connection:
        assert connection.execute(sa.text("SELECT to_regclass('public.pr02a_failed_ddl')")).scalar_one() is None
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == head
        name = connection.execute(sa.select(tables["semesters"].c.name)
                                  .where(tables["semesters"].c.id == tree.semester.id)).scalar_one()
        assert name == tree.semester.name
    with pg.db.transaction(owners.a) as tx:
        assert tx.catalog.get_course(tree.course.id).name == tree.course.name


def test_downgrade_refuses_destructive_data_loss(pg, owners, tree):
    head = ScriptDirectory.from_config(pg.config).get_current_head()
    with pytest.raises(Exception, match="(?i)(forward|destructive|downgrade|refus|retain)"):
        command.downgrade(pg.config, "base")
    with pg.admin.connect() as connection:
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == head
    with pg.db.transaction(owners.a) as tx:
        assert tx.catalog.get_course(tree.course.id).id == tree.course.id
