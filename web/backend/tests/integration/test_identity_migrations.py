"""Incremental identity upgrades on real isolated PostgreSQL databases."""
from __future__ import annotations

import shutil
from uuid import uuid4

from alembic import command
import pytest
import sqlalchemy as sa

from conftest import BACKEND, ROOT, migration_config
from test_task_migrations import previous_schema, snapshot, upgrade, version
from classroom_web.infrastructure.schema import tables as catalog_tables
from classroom_web.infrastructure.task_schema import tables as task_tables


CURRENT = "0004_identity"
PREVIOUS = "0003_persistent_tasks"


def test_identity_upgrade_preserves_all_accepted_tables_and_repeats(previous_schema):
    database = previous_schema
    upgrade(database, PREVIOUS)
    before = snapshot(database, catalog_tables | task_tables)
    before_names = set(sa.inspect(database.engine).get_table_names())
    upgrade(database, CURRENT)
    assert version(database) == CURRENT
    assert snapshot(database, catalog_tables | task_tables) == before
    from classroom_web.infrastructure.identity_schema import tables as identity_tables
    assert set(sa.inspect(database.engine).get_table_names()) == before_names | set(identity_tables)
    upgrade(database, CURRENT)
    upgrade(database, CURRENT)
    assert snapshot(database, catalog_tables | task_tables) == before


def test_identity_failure_rolls_back_ddl_data_and_alembic_version(previous_schema):
    database = previous_schema
    upgrade(database, PREVIOUS)
    before = snapshot(database, catalog_tables | task_tables)
    scratch = ROOT / "work/pr03a/migration-failure" / uuid4().hex
    scratch.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BACKEND / "migrations", scratch)
    source = scratch / "versions/0004_identity.sql"
    source.write_text(source.read_text("utf-8") + "\nUPDATE users SET revision=revision+1;\nSELECT 1/0;\n", "utf-8")
    with pytest.raises(sa.exc.DBAPIError) as caught:
        upgrade(database, CURRENT, scratch)
    assert caught.value.orig.sqlstate == "22012"
    assert version(database) == PREVIOUS
    assert snapshot(database, catalog_tables | task_tables) == before
    names = set(sa.inspect(database.engine).get_table_names())
    assert "invitations" not in names
    assert "pending_registrations" not in names
    upgrade(database, CURRENT)
    assert version(database) == CURRENT


def test_identity_downgrade_refuses_to_destroy_identity_ledgers(previous_schema):
    database = previous_schema
    upgrade(database, CURRENT)
    before = snapshot(database, catalog_tables | task_tables)
    names = set(sa.inspect(database.engine).get_table_names())
    cfg = migration_config(database.url.render_as_string(hide_password=False))
    with pytest.raises(RuntimeError):
        with database.engine.connect() as connection:
            cfg.attributes["connection"] = connection
            command.downgrade(cfg, PREVIOUS)
    assert version(database) == CURRENT
    assert snapshot(database, catalog_tables | task_tables) == before
    assert set(sa.inspect(database.engine).get_table_names()) == names
