"""Real incremental PR-02b upgrades preserve accepted data and migration history."""
from __future__ import annotations

import hashlib
import re
import shutil
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
import pytest
import sqlalchemy as sa

from classroom_web.domain.data import OwnerContext, Resource
from classroom_web.infrastructure.database import Database
from classroom_web.infrastructure.repositories import PgCatalogRepository, PgTombstoneRepository
from classroom_web.infrastructure.schema import tables as catalog_tables
from classroom_web.infrastructure.task_schema import tables as task_tables
from classroom_web.test_support import synthetic_connect_args, validate_test_database_target

from conftest import BACKEND, ROOT, migration_config
from test_persistent_tasks import task_db


PREVIOUS = "0002_pr02a_review"
CURRENT = "0003_persistent_tasks"
# Git-normalized SHA-256 at the explicitly authorized a76558ac baseline. EOL
# normalization accommodates Windows CRLF checkouts without accepting any
# substantive rewrite to an already-applied migration.
ACCEPTED_HASHES = {
    "0001_data_foundation.py": "d7f5bf06145eb21f9cd5d8f6ec093ecb1deebfb590b2829f39ce7e05fcbedf16",
    "0001_data_foundation.sql": "dd8065aa02c121e74a29207bdf15ad1a6599aacdd988e573faefb5202f67198c",
    "0002_pr02a_review.py": "f7c04dcbb80dc691e5060847a27be9c1fbeb4bbf4011eaa589e6fb7ad8631f69",
    "0002_pr02a_review.sql": "7dec2d80d9afabb00491428b2b5f01816f58dfa84f90216eee5e3ad20351df2d",
}


def upgrade(database, revision=CURRENT, location=None):
    cfg = migration_config(database.url.render_as_string(hide_password=False), location)
    with database.engine.connect() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, revision)


def version(database):
    with database.engine.connect() as connection:
        return connection.scalar(sa.text("SELECT version_num FROM alembic_version"))


def snapshot(database, tables):
    with database.engine.connect() as connection:
        return {name: [dict(row) for row in connection.execute(sa.select(table).order_by(table.c.id)).mappings()]
                for name, table in tables.items()}


@pytest.fixture
def previous_schema(pg):
    """Allocate one exact random database in the fixture's guarded cluster."""
    name = "pr02a_task_upgrade_" + uuid4().hex
    assert re.fullmatch(r"pr02a_task_upgrade_[0-9a-f]{32}", name)
    control_url = validate_test_database_target(pg.admin_url.render_as_string(hide_password=False))
    url = validate_test_database_target(control_url.set(database=name).render_as_string(hide_password=False))
    control = sa.create_engine(control_url, isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool,
                               connect_args=synthetic_connect_args())
    engine = None
    created = False
    try:
        with control.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE template0 ENCODING \'UTF8\'')
        created = True
        engine = sa.create_engine(url, poolclass=sa.pool.NullPool, connect_args=synthetic_connect_args())
        database = SimpleNamespace(url=url, engine=engine,
            runtime_url=pg.runtime_url.set(database=name), owner_id=uuid4())
        upgrade(database, PREVIOUS)
        assert version(database) == PREVIOUS
        with engine.begin() as connection:
            connection.execute(catalog_tables["users"].insert().values(
                id=database.owner_id, role="student", state="active"))
            connection.execute(sa.text("SELECT set_config('app.owner_id', :owner, true)"),
                               {"owner": str(database.owner_id)})
            catalog = PgCatalogRepository(connection, database.owner_id)
            semester = catalog.create_semester("Accepted synthetic semester")
            course = catalog.create_course(semester.id, "Accepted synthetic course")
            classroom = catalog.create_class_session(course.id, "Accepted synthetic classroom")
            revoked = catalog.create_semester("Accepted revoked synthetic semester")
            PgTombstoneRepository(catalog).revoke(Resource.SEMESTER, revoked.id, 1, 991)
            database.semester_id, database.course_id, database.classroom_id = semester.id, course.id, classroom.id
        yield database
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with control.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        control.dispose()


def test_accepted_migration_content_hashes_are_preserved():
    for name, expected in ACCEPTED_HASHES.items():
        content = (BACKEND / "migrations/versions" / name).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(content).hexdigest() == expected, name


def test_direct_insert_raw_reference_cannot_borrow_another_attempt_result(pg, owners, task_db):
    """The database FK, rather than repository/UPDATE guards, owns lineage."""
    source_id, other_id, result_id = uuid4(), uuid4(), uuid4()
    raw = '{"synthetic":"exact-attempt-lineage"}'
    with pg.db.transaction(owners.a) as tx:
        source_task = tx.tasks.enqueue("export", {"schema_version": 1}, op_id=uuid4())
        other_task = tx.tasks.enqueue("export", {"schema_version": 1}, op_id=uuid4())
        tx.connection.execute(task_tables["task_attempts"].insert().values(
            id=source_id, owner_id=owners.a.owner_id, task_id=source_task.id,
            attempt_number=1, state="submitting", submitted_at=sa.func.clock_timestamp(),
            provider="mock", provider_outcome="unknown"))
        tx.connection.execute(task_tables["provider_results"].insert().values(
            id=result_id, owner_id=owners.a.owner_id, attempt_id=source_id,
            raw_output=raw, sha256=hashlib.sha256(raw.encode()).hexdigest(),
            validation_status="pending"))
        tx.connection.execute(task_tables["task_attempts"].update().where(
            task_tables["task_attempts"].c.id == source_id).values(raw_output_ref=result_id))
    inserted_before_commit = False
    with pytest.raises(sa.exc.IntegrityError) as caught:
        with pg.db.transaction(owners.a) as tx:
            tx.connection.execute(task_tables["task_attempts"].insert().values(
                id=other_id, owner_id=owners.a.owner_id, task_id=other_task.id,
                attempt_number=1, state="planned", raw_output_ref=result_id))
            inserted_before_commit = True
    assert inserted_before_commit, "the exact-lineage constraint is deferred until COMMIT"
    assert caught.value.orig.sqlstate == "23503"
    assert caught.value.orig.diag.constraint_name == "fk_attempt_result_exact_lineage"
    with pg.db.transaction(owners.a) as tx:
        assert tx.connection.scalar(sa.select(task_tables["task_attempts"].c.id).where(
            task_tables["task_attempts"].c.id == other_id)) is None
        assert tx.connection.scalar(sa.select(task_tables["task_attempts"].c.raw_output_ref).where(
            task_tables["task_attempts"].c.id == source_id)) == result_id


def test_previous_schema_upgrades_with_all_seven_table_data_unchanged(previous_schema):
    database = previous_schema
    before = snapshot(database, catalog_tables)
    assert set(before) == {"users", "semesters", "courses", "course_material_versions",
                           "class_sessions", "deletion_requests", "deletion_tombstones"}
    assert all(before.values()), "every accepted table must contain data for preservation proof"
    original_tables = set(sa.inspect(database.engine).get_table_names())
    upgrade(database)
    assert version(database) == CURRENT
    assert snapshot(database, catalog_tables) == before
    assert set(sa.inspect(database.engine).get_table_names()) == original_tables | set(task_tables)
    runtime = Database(database.runtime_url.render_as_string(hide_password=False))
    try:
        with runtime.transaction(OwnerContext(database.owner_id)) as tx:
            assert tx.catalog.get_class_session(database.classroom_id).course_id == database.course_id
            assert tx.catalog.get_course(database.course_id).semester_id == database.semester_id
    finally:
        runtime.dispose()


def test_repeated_incremental_upgrade_preserves_schema_and_existing_rows(previous_schema):
    database = previous_schema
    upgrade(database)
    before = snapshot(database, catalog_tables | task_tables)
    before_tables = set(sa.inspect(database.engine).get_table_names())
    upgrade(database)
    upgrade(database)
    assert version(database) == CURRENT
    assert snapshot(database, catalog_tables | task_tables) == before
    assert set(sa.inspect(database.engine).get_table_names()) == before_tables


def test_failed_later_migration_rolls_back_task_data_ddl_and_version(previous_schema):
    database = previous_schema
    upgrade(database)
    runtime = Database(database.runtime_url.render_as_string(hide_password=False))
    try:
        with runtime.transaction(OwnerContext(database.owner_id)) as tx:
            task = tx.tasks.enqueue("export", {"schema_version": 1}, op_id=uuid4(),
                                    class_session_id=database.classroom_id)
        before = snapshot(database, catalog_tables | task_tables)
        scratch = ROOT / "work/pr02b/migration-failure" / uuid4().hex
        scratch.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(BACKEND / "migrations", scratch)
        (scratch / "versions/ffff_task_failure.py").write_text(
            "from alembic import op\n"
            "revision='ffff_task_failure'\n"
            f"down_revision={CURRENT!r}\n"
            "branch_labels=None\ndepends_on=None\n"
            "def upgrade():\n"
            "    op.execute('CREATE TABLE pr02b_failed_ddl (id integer)')\n"
            f"    op.execute(\"UPDATE tasks SET priority=99,revision=revision+1 WHERE id='{task.id}'\")\n"
            "    op.execute('SELECT 1/0')\n"
            "def downgrade():\n    raise RuntimeError('synthetic failure only')\n", encoding="utf-8")
        with pytest.raises(sa.exc.DBAPIError) as caught:
            upgrade(database, "ffff_task_failure", scratch)
        assert caught.value.orig.sqlstate == "22012"
        assert version(database) == CURRENT
        assert snapshot(database, catalog_tables | task_tables) == before
        with database.engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT to_regclass('public.pr02b_failed_ddl')")) is None
        with runtime.transaction(OwnerContext(database.owner_id)) as tx:
            assert tx.tasks.get(task.id).priority == task.priority
    finally:
        runtime.dispose()
