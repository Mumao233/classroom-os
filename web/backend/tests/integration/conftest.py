"""Disposable PostgreSQL-only fixtures; never accept an arbitrary application DB."""
from __future__ import annotations

import os
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from classroom_web.domain.data import OwnerContext
from classroom_web.infrastructure.database import Database
from classroom_web.infrastructure.schema import tables


BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parents[1]


def migration_config(url, location=None):
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(location or BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", str(url).replace("%", "%%"))
    return cfg


@pytest.fixture(scope="session")
def pg():
    supplied = os.environ.get("PR02A_TEST_ADMIN_URL")
    if not supplied:
        pytest.fail("PR02A_TEST_ADMIN_URL must identify the isolated synthetic PostgreSQL cluster")
    url = make_url(supplied)
    if (url.get_backend_name() != "postgresql" or url.host not in {"127.0.0.1", "localhost"}
            or not url.port or url.port == 5432 or not (url.database or "").startswith("pr02a_")):
        pytest.fail("Refusing non-loopback/default-port/non-synthetic PostgreSQL URL")
    marker = uuid4().hex
    name, login = "pr02a_test_" + marker, "pr02a_login_" + marker
    assert re.fullmatch(r"pr02a_(?:test|login)_[0-9a-f]{32}", name)
    control = sa.create_engine(url, isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool)
    admin_url = url.set(database=name)
    admin = None
    db = None
    try:
        with control.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE template0 ENCODING \'UTF8\'')
        admin = sa.create_engine(admin_url, poolclass=sa.pool.NullPool)
        cfg = migration_config(admin_url.render_as_string(hide_password=False))
        command.upgrade(cfg, "head")
        with control.connect() as connection:
            connection.exec_driver_sql(f'CREATE ROLE "{login}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
            connection.exec_driver_sql(f'GRANT classroom_web_runtime TO "{login}"')
        runtime_url = admin_url.set(username=login, password=None)
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
