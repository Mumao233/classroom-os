"""PR-08a default-closed trust guard on fresh and upgraded PostgreSQL databases."""
from uuid import uuid4

from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa

from test_task_migrations import previous_schema, upgrade, version


def test_trust_guard_is_validated_and_session_writer_cannot_insert_or_update_true(pg, owners):
    scripts = ScriptDirectory.from_config(pg.config)
    assert scripts.get_heads() == ['0013_trusted_device_guard']
    assert scripts.get_revision('0013_trusted_device_guard').down_revision == '0012_capture_recovery'
    device = uuid4()
    with pg.admin.begin() as connection:
        assert connection.scalar(sa.text("SELECT convalidated FROM pg_constraint WHERE conrelid='public.devices'::regclass AND conname='devices_trusted_check'")) is True
        connection.exec_driver_sql('SET LOCAL ROLE classroom_web_session_writer')
        assert connection.scalar(sa.text('INSERT INTO devices(id,owner_id,device_name) VALUES(:id,:owner,:name) RETURNING trusted'),
                                 {'id': device, 'owner': owners.a.owner_id, 'name': 'synthetic-untrusted'}) is False
    statements = [
        'INSERT INTO devices(id,owner_id,device_name,trusted) VALUES(:new_id,:owner,:name,true)',
        'UPDATE devices SET trusted=true WHERE id=:id',
    ]
    for statement in statements:
        with pytest.raises(sa.exc.IntegrityError) as caught:
            with pg.admin.begin() as connection:
                connection.exec_driver_sql('SET LOCAL ROLE classroom_web_session_writer')
                connection.execute(sa.text(statement), {'id': device, 'new_id': uuid4(), 'owner': owners.a.owner_id,
                                                        'name': 'synthetic-must-be-rejected'})
        assert caught.value.orig.sqlstate == '23514'
        assert caught.value.orig.diag.constraint_name == 'devices_trusted_check'


def test_trust_guard_forward_upgrade_clears_old_true_and_preserves_wrapped_keys(previous_schema):
    database = previous_schema
    upgrade(database, '0012_capture_recovery')
    device, key = uuid4(), uuid4()
    wrapped = b's' * 61
    with database.engine.begin() as connection:
        connection.execute(sa.text('INSERT INTO devices(id,owner_id,device_name,trusted,key_epoch) VALUES(:id,:owner,:name,true,7)'),
                           {'id': device, 'owner': database.owner_id, 'name': 'synthetic-pre-guard'})
        connection.execute(sa.text("INSERT INTO cache_keys(id,owner_id,device_id,purpose,wrapped_dek,kek_version,key_version) VALUES(:id,:owner,:device,'draft',:wrapped,'synthetic-v1',7)"),
                           {'id': key, 'owner': database.owner_id, 'device': device, 'wrapped': wrapped})
        before = dict(connection.execute(sa.text('SELECT * FROM cache_keys WHERE id=:id'), {'id': key}).mappings().one())
        tombstones = connection.scalar(sa.text('SELECT count(*) FROM deletion_tombstones'))
    upgrade(database, '0013_trusted_device_guard')
    upgrade(database, '0013_trusted_device_guard')
    assert version(database) == '0013_trusted_device_guard'
    with database.engine.connect() as connection:
        assert connection.execute(sa.text('SELECT trusted,key_epoch FROM devices WHERE id=:id'), {'id': device}).one() == (False, 7)
        assert dict(connection.execute(sa.text('SELECT * FROM cache_keys WHERE id=:id'), {'id': key}).mappings().one()) == before
        assert connection.scalar(sa.text('SELECT count(*) FROM deletion_tombstones')) == tombstones
        assert connection.scalar(sa.text("SELECT convalidated FROM pg_constraint WHERE conrelid='public.devices'::regclass AND conname='devices_trusted_check'")) is True
