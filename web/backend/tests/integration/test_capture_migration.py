"""Forward-only shared cache dependency and capture migration checks on real PG."""
from alembic import command
from alembic.script import ScriptDirectory
import sqlalchemy as sa

from test_task_migrations import previous_schema, upgrade, version


def test_cache_dependency_empty_database_has_one_chain_and_closed_policy(pg):
    scripts = ScriptDirectory.from_config(pg.config)
    assert len(scripts.get_heads()) == 1
    assert scripts.get_revision('0011_cache_keys').down_revision == '0010_deletion_lifecycle'
    with pg.admin.connect() as connection:
        policy = connection.execute(sa.text('SELECT * FROM identity_control.cache_policy')).mappings().one()
        assert policy['enabled'] is False and policy['history_enabled'] is False
        assert policy['policy_revision'] is None and policy['unlock_window_seconds'] is None
        row = connection.execute(sa.text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname='cache_keys'")).one()
        assert row == (True, True)


def test_cache_dependency_upgrade_preserves_existing_catalog_and_tombstone(previous_schema):
    database = previous_schema
    upgrade(database, '0010_deletion_lifecycle')
    with database.engine.connect() as connection:
        before = connection.scalar(sa.text('SELECT count(*) FROM deletion_tombstones'))
        assert connection.scalar(sa.text("SELECT to_regclass('public.cache_keys')")) is None
    upgrade(database, '0011_cache_keys')
    assert version(database) == '0011_cache_keys'
    with database.engine.connect() as connection:
        assert connection.scalar(sa.text('SELECT count(*) FROM deletion_tombstones')) == before
        assert connection.scalar(sa.text('SELECT title FROM class_sessions WHERE id=:id'), {'id': database.classroom_id}) == 'Accepted synthetic classroom'
    upgrade(database, '0011_cache_keys')


def test_capture_forward_upgrade_and_default_closed_policy(previous_schema):
    database=previous_schema
    upgrade(database,'0011_cache_keys')
    upgrade(database,'0012_capture_recovery')
    assert version(database)=='0012_capture_recovery'
    with database.engine.connect() as connection:
        policy=connection.execute(sa.text('SELECT * FROM capture_policy')).mappings().one()
        assert policy['enabled'] is False and policy['retention_policy_version'] is None
        assert connection.scalar(sa.text('SELECT title FROM class_sessions WHERE id=:id'),{'id':database.classroom_id})=='Accepted synthetic classroom'
        tables=('consent_records','capture_epochs','capture_leases','capture_gaps','audio_chunks',
                'capture_epoch_state','capture_recovery_grants','audio_chunk_state')
        rows=connection.execute(sa.text('SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=ANY(:names)'),{'names':list(tables)}).all()
        assert len(rows)==len(tables) and all(row==(True,True) for row in rows)
    upgrade(database,'0012_capture_recovery')

