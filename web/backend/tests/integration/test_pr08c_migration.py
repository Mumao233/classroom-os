"""Literal migration failure rollback and forward recovery on populated PostgreSQL."""
from shutil import copytree

import pytest
import sqlalchemy as sa

from conftest import BACKEND
from test_task_migrations import previous_schema, upgrade, version


def test_portable_forward_migration_keeps_old_rows_after_injected_ddl_failure(previous_schema,tmp_path):
    database = previous_schema
    upgrade(database,'0014_offline_sync')
    with database.engine.connect() as conn:
        old = conn.execute(sa.text('SELECT * FROM class_sessions WHERE id=:id'),
                           {'id':database.classroom_id}).mappings().one()
        tombstones = conn.scalar(sa.text('SELECT count(*) FROM deletion_tombstones'))
    upgrade(database,'0015_portable_lifecycle')
    migrations = tmp_path/'migrations'
    copytree(BACKEND/'migrations',migrations)
    path = migrations/'versions/0016_portable_jobs.sql'
    with path.open('a', encoding='utf8') as file:
        file.write('\nSELECT 1/0;\n')
    with pytest.raises(sa.exc.DBAPIError) as failed:
        upgrade(database,'0016_portable_jobs',migrations)
    assert failed.value.orig.sqlstate == '22012'
    assert version(database) == '0015_portable_lifecycle'
    with database.engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT to_regclass('public.portable_task_bindings') IS NULL"))
        assert conn.scalar(sa.text("SELECT to_regclass('public.portable_transfers') IS NOT NULL"))
    upgrade(database,'0016_portable_jobs')
    upgrade(database,'0016_portable_jobs')
    assert version(database) == '0016_portable_jobs'
    with database.engine.connect() as conn:
        assert conn.execute(sa.text('SELECT * FROM class_sessions WHERE id=:id'),
                            {'id':database.classroom_id}).mappings().one() == old
        assert conn.scalar(sa.text('SELECT count(*) FROM deletion_tombstones')) == tombstones
