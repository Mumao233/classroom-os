"""Upgrade the already-applied initial schema without rewriting its history."""
from __future__ import annotations

import re
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
import pytest
import sqlalchemy as sa

from classroom_web.infrastructure.repositories import PgCatalogRepository
from classroom_web.infrastructure.schema import tables
from classroom_web.test_support import synthetic_connect_args, validate_test_database_target

from conftest import migration_config


INITIAL_REVISION = "0001_data_foundation"
REVIEW_REVISION = "0002_pr02a_review"
LENGTH_CHECKS = {"ck_semesters_name_length", "ck_semesters_academic_year_length"}


def upgrade(database, revision):
    """Reuse the validated connection rather than resolve a second target."""
    config = migration_config(database.url.render_as_string(hide_password=False))
    with database.engine.connect() as connection:
        config.attributes["connection"] = connection
        try:
            command.upgrade(config, revision)
        finally:
            config.attributes.pop("connection", None)


def current_revision(database):
    with database.engine.connect() as connection:
        return connection.scalar(sa.text("SELECT version_num FROM alembic_version"))


def tombstone_function(database):
    with database.engine.connect() as connection:
        return connection.scalar(sa.text(
            "SELECT pg_get_functiondef('public.classroom_web_guard_tombstone()'::regprocedure)"))


def semester_checks(database):
    return {item["name"] for item in sa.inspect(database.engine).get_check_constraints("semesters")}


@pytest.fixture
def initial_schema_database(pg):
    """One additional random DB within the fixture's validated synthetic cluster."""
    name = "pr02a_upgrade_" + uuid4().hex
    assert re.fullmatch(r"pr02a_upgrade_[0-9a-f]{32}", name)
    control_url = validate_test_database_target(pg.admin_url.render_as_string(hide_password=False))
    url = validate_test_database_target(control_url.set(database=name)
                                         .render_as_string(hide_password=False))
    control = sa.create_engine(control_url, isolation_level="AUTOCOMMIT",
                                poolclass=sa.pool.NullPool, connect_args=synthetic_connect_args())
    engine = None
    created = False
    try:
        with control.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE template0 ENCODING \'UTF8\'')
        created = True
        engine = sa.create_engine(url, poolclass=sa.pool.NullPool,
                                   connect_args=synthetic_connect_args())
        database = SimpleNamespace(url=url, engine=engine)
        upgrade(database, INITIAL_REVISION)
        assert current_revision(database) == INITIAL_REVISION
        assert semester_checks(database).isdisjoint(LENGTH_CHECKS)
        yield database
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            # The generated and checked literal name above is the only drop target.
            with control.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        control.dispose()


def seed_owner(connection):
    owner_id = uuid4()
    connection.execute(tables["users"].insert().values(id=owner_id, role="student", state="active"))
    connection.execute(sa.text("SELECT set_config('app.owner_id', :owner, true)"),
                        {"owner": str(owner_id)})
    return owner_id


def snapshot(database):
    with database.engine.connect() as connection:
        return {name: [dict(row) for row in connection.execute(sa.select(table).order_by(table.c.id))
                       .mappings()]
                for name, table in tables.items()}


def test_applied_initial_schema_upgrades_to_review_with_data_intact(initial_schema_database):
    database = initial_schema_database
    with database.engine.begin() as connection:
        owner_id = seed_owner(connection)
        repository = PgCatalogRepository(connection, owner_id)
        semester = repository.create_semester("名" * 100, academic_year="年" * 30)
        course = repository.create_course(semester.id, "Synthetic original-schema course")
        repository.create_class_session(course.id, "Synthetic original-schema classroom")
    before = snapshot(database)
    original_function = tombstone_function(database)
    original_tables = set(sa.inspect(database.engine).get_table_names())

    upgrade(database, REVIEW_REVISION)

    assert current_revision(database) == REVIEW_REVISION
    assert snapshot(database) == before
    assert set(sa.inspect(database.engine).get_table_names()) == original_tables
    assert LENGTH_CHECKS <= semester_checks(database)
    assert tombstone_function(database) != original_function


@pytest.mark.parametrize("field,value,constraint", (
    ("name", "名" * 101, "ck_semesters_name_length"),
    ("academic_year", "年" * 31, "ck_semesters_academic_year_length"),
), ids=("name101", "academic_year31"))
def test_invalid_original_semester_blocks_upgrade_without_truncation(
        initial_schema_database, field, value, constraint):
    database = initial_schema_database
    semester_id = uuid4()
    with database.engine.begin() as connection:
        owner_id = seed_owner(connection)
        connection.execute(tables["semesters"].insert().values(**(
            {"id": semester_id, "owner_id": owner_id, "name": "名" * 100,
             "academic_year": "年" * 30} | {field: value})))
    before = snapshot(database)
    original_function = tombstone_function(database)
    original_tables = set(sa.inspect(database.engine).get_table_names())
    assert before["semesters"][0][field] == value

    with pytest.raises(sa.exc.IntegrityError) as caught:
        upgrade(database, REVIEW_REVISION)

    assert caught.value.orig.sqlstate == "23514"
    assert caught.value.orig.diag.constraint_name == constraint
    assert current_revision(database) == INITIAL_REVISION
    assert snapshot(database) == before
    assert semester_checks(database).isdisjoint(LENGTH_CHECKS)
    assert tombstone_function(database) == original_function
    assert set(sa.inspect(database.engine).get_table_names()) == original_tables
