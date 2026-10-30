"""Incremental 0003/0004 to identity-recovery migration with real ledgers."""
from __future__ import annotations

from datetime import timedelta
import shutil
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
import pytest
import sqlalchemy as sa

from classroom_web.application.identity import IdentityService
from classroom_web.domain.data import OwnerContext
from classroom_web.infrastructure.database import Database
from classroom_web.infrastructure.identity_mail import CapturingMailAdapter
from classroom_web.infrastructure.identity_passwords import Argon2PasswordHasher
from classroom_web.infrastructure.identity_repository import PgIdentityRepository
from classroom_web.infrastructure.identity_schema import tables as identity_tables
from classroom_web.infrastructure.schema import tables as catalog_tables
from classroom_web.infrastructure.task_scheduler import PgTaskScheduler
from classroom_web.infrastructure.task_schema import tables as task_tables

from conftest import BACKEND, ROOT, migration_config
from test_identity import principal
from test_identity_recovery import RecordingProvider
from test_persistent_tasks import CURSOR_KEY, WORKER
from test_task_migrations import previous_schema, snapshot, upgrade, version


CURRENT = "0005_identity_recovery"
IDENTITY = "0004_identity"
TASKS = "0003_persistent_tasks"


def function_snapshot(database):
    """Definitions, owners and ACLs must roll back together with migration DDL."""
    with database.engine.connect() as connection:
        return list(connection.execute(sa.text("""
            SELECT p.proname, pg_get_function_identity_arguments(p.oid) AS arguments,
                   pg_get_functiondef(p.oid) AS definition,
                   pg_get_userbyid(p.proowner) AS owner, p.proacl::text AS acl
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname LIKE 'classroom_web_%'
            ORDER BY p.proname, pg_get_function_identity_arguments(p.oid)
        """)).mappings())


@pytest.fixture
def previous_recovery_schema(request, previous_schema):
    database = previous_schema
    previous = getattr(request, "param", IDENTITY)
    assert previous in (TASKS, IDENTITY)
    upgrade(database, previous)
    marker = uuid4().hex
    qlogin, ilogin = "pr02a_recovery_queue_" + marker, "pr02a_recovery_identity_" + marker
    created = []
    scheduler = repository = runtime = None
    try:
        with database.engine.begin() as connection:
            for login, role in ((qlogin, "classroom_web_scheduler"),
                                (ilogin, "classroom_web_identity")):
                # The group exists cluster-wide from pg's current-schema DB;
                # each fixture login is random and has no direct table grant.
                connection.exec_driver_sql(f'CREATE ROLE "{login}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
                connection.exec_driver_sql(f'GRANT {role} TO "{login}"')
                created.append(login)
        scheduler = PgTaskScheduler(database.runtime_url.set(username=qlogin).render_as_string(hide_password=False))
        runtime = Database(database.runtime_url.render_as_string(hide_password=False), cursor_secret=CURSOR_KEY)
        owner = OwnerContext(database.owner_id)
        with runtime.transaction(owner) as tx:
            task = tx.tasks.enqueue("export", {"schema_version": 1}, op_id=uuid4(),
                                    class_session_id=database.classroom_id)
        claims = scheduler.claim(WORKER)
        assert len(claims) == 1 and claims[0].id == task.id
        claim = claims[0]
        with runtime.transaction(owner) as tx:
            attempt = tx.tasks.prepare_attempt(task.id, claim.lease_token, WORKER)
            tx.tasks.mark_submitting(task.id, attempt.id, claim.lease_token, WORKER)
        provider = RecordingProvider()
        raw = provider.submit(task, attempt)
        with runtime.transaction(owner) as tx:
            saved = tx.tasks.store_result(task.id, attempt.id, raw)
        known_tables = catalog_tables | task_tables
        if previous == IDENTITY:
            hasher = Argon2PasswordHasher()
            admin_id = uuid4()
            with database.engine.begin() as connection:
                connection.execute(catalog_tables["users"].insert().values(
                    id=admin_id, role="admin", state="active",
                    normalized_email=f"synthetic-migration-admin-{marker}@example.test",
                    password_hash=hasher.hash("Synthetic-migration-password"),
                    email_verified_at=sa.func.clock_timestamp()))
            repository = PgIdentityRepository(database.runtime_url.set(username=ilogin).render_as_string(hide_password=False))
            service = IdentityService(repository, hasher,
                                      admin_reauthentication_window=timedelta(minutes=5))
            invitation = service.create_invitation(principal(admin_id, "admin"), kind="single_use")
            service.register(f"synthetic-migration-student-{marker}@example.test",
                             "Synthetic-migration-password", invitation.token)
            captured = CapturingMailAdapter()
            assert service.dispatch_mail(captured)
            assert service.activate(captured.messages[0][1]) is not None
            known_tables |= identity_tables
        initial = snapshot(database, known_tables)
        assert len(initial) == (19 if previous == IDENTITY else 13)
        assert all(initial.values()), "all accepted tables must hold real synthetic fixture data"
        yield SimpleNamespace(database=database, previous=previous, tables=known_tables,
                              task_id=task.id, attempt_id=attempt.id, raw_id=saved.id)
    finally:
        if scheduler is not None:
            scheduler.dispose()
        if repository is not None:
            repository.dispose()
        if runtime is not None:
            runtime.dispose()
        with database.engine.begin() as connection:
            for login in reversed(created):
                connection.exec_driver_sql(f'DROP ROLE "{login}"')


@pytest.mark.parametrize("previous_recovery_schema", [TASKS, IDENTITY], indirect=True)
def test_incremental_identity_recovery_upgrade_preserves_all_prior_ledgers_and_repeats(previous_recovery_schema):
    state = previous_recovery_schema
    database = state.database
    before = snapshot(database, state.tables)
    upgrade(database, CURRENT)
    assert version(database) == CURRENT
    assert snapshot(database, state.tables) == before
    all_tables = catalog_tables | task_tables | identity_tables
    assert set(sa.inspect(database.engine).get_table_names()) == set(all_tables) | {"alembic_version"}
    after = snapshot(database, all_tables)
    functions = function_snapshot(database)
    upgrade(database, CURRENT)
    upgrade(database, CURRENT)
    assert version(database) == CURRENT
    assert snapshot(database, all_tables) == after
    assert function_snapshot(database) == functions


def test_failed_identity_recovery_migration_restores_functions_grants_data_and_version(previous_recovery_schema):
    state = previous_recovery_schema
    database = state.database
    assert state.previous == IDENTITY
    before = snapshot(database, state.tables)
    functions = function_snapshot(database)
    scratch = ROOT / "work/pr03a/remediation/migration-failure" / uuid4().hex
    scratch.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BACKEND / "migrations", scratch)
    source = scratch / "versions/0005_identity_recovery.sql"
    source.write_text(source.read_text("utf-8") +
                      "\nUPDATE tasks SET revision=revision+1;\nSELECT 1/0;\n", "utf-8")
    with pytest.raises(sa.exc.DBAPIError) as caught:
        upgrade(database, CURRENT, scratch)
    assert caught.value.orig.sqlstate == "22012"
    assert version(database) == IDENTITY
    assert snapshot(database, state.tables) == before
    assert function_snapshot(database) == functions
    upgrade(database, CURRENT)
    assert version(database) == CURRENT
    assert snapshot(database, state.tables) == before


def test_identity_recovery_downgrade_refuses_to_erase_recovery_evidence(previous_recovery_schema):
    state = previous_recovery_schema
    database = state.database
    upgrade(database, CURRENT)
    before = snapshot(database, state.tables)
    functions = function_snapshot(database)
    cfg = migration_config(database.url.render_as_string(hide_password=False))
    with pytest.raises(RuntimeError):
        with database.engine.connect() as connection:
            cfg.attributes["connection"] = connection
            command.downgrade(cfg, IDENTITY)
    assert version(database) == CURRENT
    assert snapshot(database, state.tables) == before
    assert function_snapshot(database) == functions
