"""Disposable PostgreSQL-only fixtures; never accept an arbitrary application DB."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

# The real-browser test needs a built frontend, Node and Playwright's Chromium, which exist
# only in the prepared VMware guest. Its runner (tools/pr03b/product/test_backend.py) opts in
# explicitly; everywhere else, including CI, the module is not collected at all, so it
# counts neither as passed nor as skipped.
collect_ignore = [] if os.environ.get("PR03B_REAL_BROWSER") == "1" else ["test_auth_browser.py"]
# PR-04a's owned object-service runner explicitly enables its additional service suite.
if os.environ.get("PR04A_OBJECT_INTEGRATION") != "1":
    collect_ignore.append("test_private_media.py")
    collect_ignore.append("test_media_stream_lifecycle.py")
# PR-04b's runner owns two object services (media and the independent ledger) and enables these.
if os.environ.get("PR04B_DELETION_INTEGRATION") != "1":
    collect_ignore.append("test_deletion_lifecycle.py")
    collect_ignore.append("test_deletion_restore.py")
if os.environ.get("PR05B_CAPTURE_INTEGRATION") != "1":
    collect_ignore.append("test_capture_recovery.py")
# PR-08b's VMware runner supplies the native browser and two owned object services.
if os.environ.get("PR08B_SYNC_INTEGRATION") != "1":
    collect_ignore.append("test_pr08b_browser.py")
# Manual recovery talks to its control plane over a Windows named pipe authenticated by the
# kernel (recovery_transport_windows.py), and every case in that module needs it. The feature
# is Windows-only and stays disabled; on other systems the module is likewise not collected.
if os.name != "nt":
    collect_ignore.append("test_manual_recovery.py")


@pytest.fixture
def password_policy(tmp_path):
    """Explicit synthetic approval for new-password regression fixtures only."""
    import hashlib
    from datetime import datetime,timedelta,timezone
    from classroom_web.infrastructure.auth_crypto import LocalPasswordPolicy,ListApproval
    path=tmp_path/'synthetic-approved-password-list.txt'
    path.write_text(hashlib.sha256(b'synthetic-blocked-password').hexdigest()+'\n',encoding='ascii')
    return LocalPasswordPolicy(path,ListApproval(hashlib.sha256(path.read_bytes()).hexdigest(),datetime.now(timezone.utc)+timedelta(hours=1)))

from classroom_web.domain.data import OwnerContext
from classroom_web.infrastructure.database import Database
from classroom_web.infrastructure.schema import tables
from classroom_web.test_support import (
    UnsafeTestDatabaseTarget, SYNTHETIC_PASSWORD, synthetic_connect_args,
    validate_test_database_target,
)


BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parents[1]


def migration_config(url, location=None):
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(location or BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", str(url).replace("%", "%%"))
    return cfg


@contextmanager
def migrated_database():
    """Own a fresh migrated database and runtime login for one test scope."""
    supplied = os.environ.get("PR02A_TEST_ADMIN_URL")
    if not supplied:
        pytest.fail("PR02A_TEST_ADMIN_URL must identify the isolated synthetic PostgreSQL cluster")
    try:
        url = validate_test_database_target(supplied)
    except UnsafeTestDatabaseTarget as error:
        pytest.fail(str(error))
    marker = uuid4().hex
    name, login = "pr02a_test_" + marker, "pr02a_login_" + marker
    assert re.fullmatch(r"pr02a_(?:test|login)_[0-9a-f]{32}", name)
    control = sa.create_engine(url, isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool,
                               connect_args=synthetic_connect_args())
    admin_url = url.set(database=name)
    admin = None
    db = None
    try:
        with control.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE template0 ENCODING \'UTF8\'')
        admin = sa.create_engine(admin_url, poolclass=sa.pool.NullPool,
                                 connect_args=synthetic_connect_args())
        cfg = migration_config(admin_url.render_as_string(hide_password=False))
        # Use the already validated engine for initial setup, without a second
        # configuration/environment resolution inside Alembic.
        with admin.connect() as connection:
            cfg.attributes["connection"] = connection
            try:
                command.upgrade(cfg, "head")
            finally:
                cfg.attributes.pop("connection", None)
        with control.connect() as connection:
            connection.exec_driver_sql(f'CREATE ROLE "{login}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
            connection.exec_driver_sql(f'GRANT classroom_web_runtime TO "{login}"')
        runtime_url = admin_url.set(username=login, password=SYNTHETIC_PASSWORD)
        db = Database(runtime_url.render_as_string(hide_password=False))
        yield SimpleNamespace(admin=admin, db=db, admin_url=admin_url, runtime_url=runtime_url,
                              config=cfg, name=name, login=login)
    finally:
        if db is not None:
            db.engine.dispose()
        if admin is not None:
            admin.dispose()
        with control.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{login}"')
        control.dispose()


@pytest.fixture(scope="session")
def pg():
    with migrated_database() as database:
        yield database


@pytest.fixture
def owners(pg):
    first, second = uuid4(), uuid4()
    with pg.admin.begin() as connection:
        connection.execute(tables["users"].insert(), [
            {"id": first, "role": "student", "state": "active"},
            {"id": second, "role": "admin", "state": "active"},
        ])
    return SimpleNamespace(a=OwnerContext(first), b=OwnerContext(second))


@pytest.fixture
def tree(pg, owners):
    with pg.db.transaction(owners.a) as tx:
        semester = tx.catalog.create_semester("Synthetic semester")
        course = tx.catalog.create_course(semester.id, "Synthetic course")
        classroom = tx.catalog.create_class_session(course.id, "Synthetic classroom")
        material = tx.catalog.get_material(course.current_material_version_id)
    return SimpleNamespace(semester=semester, course=course, classroom=classroom, material=material)
