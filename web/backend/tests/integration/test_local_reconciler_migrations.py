"""0005->0006 data/security compatibility under explicit synthetic DDL identities."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
import pytest
import sqlalchemy as sa

from classroom_web.infrastructure.identity_schema import tables as identity_tables
from classroom_web.infrastructure.schema import tables as catalog_tables
from classroom_web.infrastructure.task_schema import tables as task_tables
from classroom_web.test_support import SYNTHETIC_PASSWORD, synthetic_connect_args, validate_test_database_target

from conftest import BACKEND, ROOT, migration_config
from test_identity_recovery_migrations import previous_recovery_schema
from test_task_migrations import previous_schema, snapshot, upgrade, version

PREVIOUS = "0005_identity_recovery"
CURRENT = "0006_local_reconciler_security"
INTERNAL = ("classroom_web_identity_checker", "classroom_web_recovery_metadata",
            "classroom_web_local_result_processor")
TABLES = catalog_tables | task_tables | identity_tables
MANAGED_TABLES = ("users", "tasks", "task_attempts", "provider_results", "semesters", "courses",
                  "class_sessions", "course_material_versions", "deletion_tombstones",
                  "owner_event_counters", "outbox_events")
MANAGED_FUNCTIONS = (
    "classroom_web_identity_usable(uuid)", "classroom_web_attempt_submitted(uuid,uuid)",
    "classroom_web_local_reconcile_candidates(integer)",
    "classroom_web_reconcile_saved_result(uuid,bigint)",
    "classroom_web_local_json_payload(text)", "classroom_web_local_json_token(text)",
    "classroom_web_local_float_token(text)",
)


def evidence(name, value):
    supplied = os.environ.get("PR03A_THIRD_ROUND_EVIDENCE")
    output = Path(supplied) if supplied else ROOT / "outputs/pr03a/third-round/migration-observations" / uuid4().hex
    assert (ROOT / "outputs/pr03a/third-round").resolve() in output.resolve().parents
    output.mkdir(parents=True, exist_ok=True)
    target = output / (name + "-" + uuid4().hex + ".json")
    target.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")


def security_snapshot(database):
    queries = {
        "functions": """SELECT p.proname,pg_get_function_identity_arguments(p.oid) arguments,
            pg_get_functiondef(p.oid) definition,pg_get_userbyid(p.proowner) owner,p.proacl::text acl
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname LIKE 'classroom_web_%'
            ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)""",
        "roles": """SELECT rolname,rolsuper,rolbypassrls,rolcanlogin,rolinherit,
            rolcreatedb,rolcreaterole,rolreplication,rolconfig FROM pg_roles
            WHERE rolname LIKE 'classroom_web_%' ORDER BY rolname""",
        "memberships": """SELECT r.rolname role,m.rolname member,g.rolname grantor,
            a.admin_option,a.inherit_option,a.set_option FROM pg_auth_members a
            JOIN pg_roles r ON r.oid=a.roleid JOIN pg_roles m ON m.oid=a.member
            JOIN pg_roles g ON g.oid=a.grantor
            WHERE r.rolname LIKE 'classroom_web_%' OR m.rolname LIKE 'classroom_web_%'
            ORDER BY r.rolname,m.rolname,g.rolname""",
        "policies": """SELECT schemaname,tablename,policyname,permissive,roles,cmd,qual,with_check
            FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname""",
        "tables": """SELECT c.relname,pg_get_userbyid(c.relowner) owner,c.relacl::text acl,
            c.relrowsecurity,c.relforcerowsecurity FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r'
            ORDER BY c.relname""",
        "columns": """SELECT c.relname,a.attname,a.attacl::text acl FROM pg_attribute a
            JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped
            ORDER BY c.relname,a.attname""",
        "schemas": """SELECT nspname,pg_get_userbyid(nspowner) owner,nspacl::text acl
            FROM pg_namespace WHERE nspname='public'""",
        "defaults": """SELECT pg_get_userbyid(defaclrole) role,defaclnamespace,
            defaclobjtype,defaclacl::text acl FROM pg_default_acl ORDER BY defaclrole,defaclnamespace,defaclobjtype""",
    }
    with database.engine.connect() as conn:
        return {name: [dict(row) for row in conn.execute(sa.text(query)).mappings()]
                for name, query in queries.items()}


def assert_restricted_owners(database):
    with database.engine.connect() as conn:
        rows = [dict(row) for row in conn.execute(sa.text("""
            SELECT p.proname,r.rolname,r.rolsuper,r.rolbypassrls,r.rolcanlogin,r.rolinherit,
              r.rolcreatedb,r.rolcreaterole,r.rolreplication
            FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
            WHERE p.proname IN ('classroom_web_identity_usable','classroom_web_attempt_submitted',
              'classroom_web_local_reconcile_candidates','classroom_web_reconcile_saved_result')
            ORDER BY p.proname
        """)).mappings()]
        memberships = conn.scalar(sa.text("""SELECT count(*) FROM pg_auth_members m
            JOIN pg_roles r ON r.oid=m.roleid JOIN pg_roles member ON member.oid=m.member
            WHERE r.rolname IN ('classroom_web_identity_checker','classroom_web_recovery_metadata',
              'classroom_web_local_result_processor') OR member.rolname IN
              ('classroom_web_identity_checker','classroom_web_recovery_metadata','classroom_web_local_result_processor')"""))
        creates = conn.scalar(sa.text("""SELECT count(*) FROM pg_roles WHERE rolname IN
            ('classroom_web_identity_checker','classroom_web_recovery_metadata','classroom_web_local_result_processor')
            AND has_schema_privilege(oid,'public','CREATE')"""))
    assert len(rows) == 4
    assert {row["rolname"] for row in rows} == set(INTERNAL)
    for row in rows:
        assert all(not row[key] for key in ("rolsuper", "rolbypassrls", "rolcanlogin", "rolinherit",
                                           "rolcreatedb", "rolcreaterole", "rolreplication"))
    assert memberships == creates == 0
    return rows


@pytest.fixture
def deployed_reconciler(previous_recovery_schema):
    state = previous_recovery_schema
    upgrade(state.database, PREVIOUS)
    assert version(state.database) == PREVIOUS
    initial = snapshot(state.database, TABLES)
    assert len(initial) == 19 and all(initial.values())
    yield state.database


@contextmanager
def ddl_login(database, *, superuser=False, provision_objects=False, provision_memberships=False):
    """Explicit provisioning in this random synthetic DB, separately evidenced."""
    login = "pr02a_ddl_" + uuid4().hex
    managed = False
    with database.engine.begin() as conn:
        bootstrap = conn.scalar(sa.text("SELECT current_user"))
        conn.exec_driver_sql(f'CREATE ROLE "{login}" LOGIN NOINHERIT NOCREATEDB CREATEROLE '
                             + ("SUPERUSER BYPASSRLS" if superuser else "NOSUPERUSER NOBYPASSRLS"))
        conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{login}"')
        conn.exec_driver_sql(f'GRANT SELECT,UPDATE ON public.alembic_version TO "{login}"')
        if provision_objects:
            conn.exec_driver_sql(f'ALTER SCHEMA public OWNER TO "{login}"')
            for name in MANAGED_TABLES:
                conn.exec_driver_sql(f'ALTER TABLE public.{name} OWNER TO "{login}"')
            for signature in MANAGED_FUNCTIONS:
                conn.exec_driver_sql(f'ALTER FUNCTION public.{signature} OWNER TO "{login}"')
            managed = True
        if provision_memberships:
            for name in INTERNAL:
                conn.exec_driver_sql(f'GRANT {name} TO "{login}" WITH ADMIN TRUE, INHERIT FALSE, SET TRUE')
    url = database.url.set(username=login, password=SYNTHETIC_PASSWORD)
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool, connect_args=synthetic_connect_args())
    actor = type("DdlIdentity", (), {"url": url, "engine": engine})()
    try:
        with engine.connect() as conn:
            flags = dict(conn.execute(sa.text("SELECT session_user,current_user,rolsuper,rolbypassrls,rolcreaterole FROM pg_roles WHERE rolname=current_user")).mappings().one())
        evidence("ddl-provisioning", {"identity": flags, "old_object_management": managed,
            "internal_membership_provisioned": provision_memberships,
            "management_tables": MANAGED_TABLES if managed else (),
            "management_functions": MANAGED_FUNCTIONS if managed else (),
            "security_after_provisioning": security_snapshot(database),
            "scope": "Only random synthetic DB objects; provisioning is separate from 0006 atomic rollback"})
        yield actor
    finally:
        engine.dispose()
        with database.engine.begin() as conn:
            for name in INTERNAL:
                conn.exec_driver_sql(f'REVOKE {name} FROM "{login}"')
            if managed:
                for signature in MANAGED_FUNCTIONS:
                    owner = conn.scalar(sa.text("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid=to_regprocedure(:name)"), {"name": "public." + signature})
                    if owner == login:
                        conn.exec_driver_sql(f'ALTER FUNCTION public.{signature} OWNER TO "{bootstrap}"')
                for name in MANAGED_TABLES:
                    conn.exec_driver_sql(f'ALTER TABLE public.{name} OWNER TO "{bootstrap}"')
                conn.exec_driver_sql(f'ALTER SCHEMA public OWNER TO "{bootstrap}"')
            conn.exec_driver_sql(f'REVOKE ALL ON public.alembic_version FROM "{login}"')
            conn.exec_driver_sql(f'REVOKE ALL ON SCHEMA public FROM "{login}"')
            conn.exec_driver_sql(f'DROP ROLE "{login}"')


@pytest.mark.parametrize("alternate_bootstrap", [False, True])
def test_0005_incremental_upgrade_preserves_19_ledgers_and_security_repeats(deployed_reconciler, alternate_bootstrap):
    database = deployed_reconciler
    before = snapshot(database, TABLES)
    if alternate_bootstrap:
        with ddl_login(database, superuser=True) as actor:
            upgrade(actor, CURRENT)
    else:
        upgrade(database, CURRENT)
    assert version(database) == CURRENT
    assert snapshot(database, TABLES) == before
    owners = assert_restricted_owners(database)
    security = security_snapshot(database)
    upgrade(database, CURRENT)
    upgrade(database, "head")
    assert version(database) == CURRENT
    assert snapshot(database, TABLES) == before
    assert security_snapshot(database) == security
    evidence("incremental-success", {"alternate_bootstrap": alternate_bootstrap, "owners": owners,
                                      "tables": len(before), "security": security, "repeats": 2})


def test_non_superuser_createrole_without_old_object_management_fails_closed(deployed_reconciler):
    database = deployed_reconciler
    with ddl_login(database) as actor:
        before, security = snapshot(database, TABLES), security_snapshot(database)
        with pytest.raises(sa.exc.DBAPIError) as caught:
            upgrade(actor, CURRENT)
        assert caught.value.orig.sqlstate == "42501"
        assert version(database) == PREVIOUS
        assert snapshot(database, TABLES) == before
        assert security_snapshot(database) == security
        evidence("non-super-without-old-management-rejected", {"sqlstate": caught.value.orig.sqlstate,
            "version": PREVIOUS, "data_and_full_security_rollback": True})


def test_non_superuser_old_management_cannot_leave_bootstrap_grantor_memberships(deployed_reconciler):
    """PG17 ADMIN is not permission to revoke bootstrap's grants; reject safely."""
    database = deployed_reconciler
    with ddl_login(database, provision_objects=True, provision_memberships=True) as actor:
        before, security = snapshot(database, TABLES), security_snapshot(database)
        assert any(row["member"] == actor.url.username for row in security["memberships"])
        with pytest.raises(sa.exc.DBAPIError) as caught:
            upgrade(actor, CURRENT)
        assert caught.value.orig.sqlstate == "P0001"
        assert caught.value.orig.diag.message_primary == "local reconciliation function owner cleanup failed"
        assert version(database) == PREVIOUS
        assert snapshot(database, TABLES) == before
        assert security_snapshot(database) == security
        evidence("non-super-unremovable-provisioning-rejected", {"sqlstate": caught.value.orig.sqlstate,
            "message_primary": caught.value.orig.diag.message_primary,
            "version": PREVIOUS, "provisioned_memberships": security["memberships"],
            "data_functions_policies_acl_roles_and_memberships_rolled_back": True,
            "limitation": "A standalone non-superuser 0006 success is not claimed; use trusted bootstrap"})


def test_failed_0006_restores_functions_owner_acl_roles_policies_ledgers_and_version(deployed_reconciler):
    database = deployed_reconciler
    before, security = snapshot(database, TABLES), security_snapshot(database)
    scratch = ROOT / "work/pr03a/third-round/migration-failure" / uuid4().hex
    scratch.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BACKEND / "migrations", scratch)
    source = scratch / "versions/0006_local_reconciler_security.sql"
    source.write_text(source.read_text("utf-8") + "\nUPDATE tasks SET revision=revision+1;\nSELECT 1/0;\n", "utf-8")
    with pytest.raises(sa.exc.DBAPIError) as caught:
        upgrade(database, CURRENT, scratch)
    assert caught.value.orig.sqlstate == "22012"
    assert version(database) == PREVIOUS
    assert snapshot(database, TABLES) == before
    assert security_snapshot(database) == security
    upgrade(database, CURRENT)
    assert version(database) == CURRENT
    assert snapshot(database, TABLES) == before
    assert_restricted_owners(database)
    evidence("0006-failure-rollback", {"sqlstate": "22012", "version_rolled_back_to": PREVIOUS,
        "tables_preserved": len(before), "full_security_equal": True, "subsequent_upgrade_succeeded": True})


def test_existing_dangerous_function_role_is_rejected_not_silently_repaired(deployed_reconciler):
    database = deployed_reconciler
    role = INTERNAL[0]
    with database.engine.begin() as conn:
        conn.exec_driver_sql(f'ALTER ROLE {role} LOGIN')
    try:
        before, security = snapshot(database, TABLES), security_snapshot(database)
        with pytest.raises(sa.exc.DBAPIError) as caught:
            upgrade(database, CURRENT)
        assert caught.value.orig.sqlstate == "P0001"
        assert caught.value.orig.diag.message_primary == "local reconciliation function owner must be isolated NOLOGIN"
        assert version(database) == PREVIOUS
        assert snapshot(database, TABLES) == before
        assert security_snapshot(database) == security
    finally:
        with database.engine.begin() as conn:
            conn.exec_driver_sql(f'ALTER ROLE {role} NOLOGIN')


def test_0006_downgrade_refuses_to_erase_evidence(deployed_reconciler):
    database = deployed_reconciler
    upgrade(database, CURRENT)
    before, security = snapshot(database, TABLES), security_snapshot(database)
    cfg = migration_config(database.url.render_as_string(hide_password=False))
    with pytest.raises(RuntimeError):
        with database.engine.connect() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, PREVIOUS)
    assert version(database) == CURRENT
    assert snapshot(database, TABLES) == before
    assert security_snapshot(database) == security


def test_empty_database_upgrades_entire_history_to_unique_0006_head(pg):
    name = "pr02a_empty_" + uuid4().hex
    url = validate_test_database_target(pg.admin_url.set(database=name).render_as_string(hide_password=False))
    control = sa.create_engine(pg.admin_url, isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool,
                               connect_args=synthetic_connect_args())
    engine = None
    try:
        with control.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE template0 ENCODING \'UTF8\'')
        engine = sa.create_engine(url, poolclass=sa.pool.NullPool, connect_args=synthetic_connect_args())
        database = SimpleNamespace(url=url, engine=engine)
        assert not sa.inspect(engine).get_table_names()
        upgrade(database, "head")
        assert version(database) == CURRENT
        assert set(sa.inspect(engine).get_table_names()) == set(TABLES) | {"alembic_version"}
        owners = assert_restricted_owners(database)
        security = security_snapshot(database)
        upgrade(database, "head")
        assert security_snapshot(database) == security
        evidence("empty-database-head", {"version": CURRENT, "owners": owners,
                                         "table_count": len(TABLES), "repeat_unchanged": True})
    finally:
        if engine is not None:
            engine.dispose()
        with control.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        control.dispose()
