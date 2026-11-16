"""PR-04b backup/restore drill: a real pg_dump copy must not revive deleted or revoked data."""
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa

from deletion_support import *  # noqa: F401,F403  (fixtures and helpers)
from deletion_support import POLICY, build, ledger_store
from test_deletion_lifecycle import delete, preview, status, upload
from test_private_media import access, headers
from classroom_web.application.auth import AuthService
from classroom_web.application.deletion import DeletionWorker, RestoreCoordinator
from classroom_web.domain.data import Gone, OwnerContext
from classroom_web.domain.deletion import LedgerCorrupt, StaleDeletionFence
from classroom_web.infrastructure.auth_admission import HashAdmission
from classroom_web.infrastructure.auth_repository import PgAuthRepository
from classroom_web.infrastructure.database import Database
from classroom_web.infrastructure.deletion_repository import PgDeletionQueue, PgRestoreOperator
from classroom_web.test_support import SYNTHETIC_PASSWORD


def pg_tool(name, *args):
    binary = Path(os.environ.get('PR04B_PG_BIN') or pytest.fail('PR04B_PG_BIN must name the PostgreSQL tools')) / name
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PG')}
    done = subprocess.run([str(binary), *map(str, args)], env=env, capture_output=True, text=True, timeout=180,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert done.returncode == 0, done.stderr[-3000:]


def backup(m, target):
    url = m.pg.admin_url
    pg_tool('pg_dump.exe', '-h', url.host, '-p', url.port, '-U', url.username, '-w', '-Fc', '-f', target, url.database)
    return target


class Restored:
    """A database created from the dump, with the same narrow logins as the original."""
    def __init__(self, m, dump):
        self.m, self.name = m, 'pr02a_restore_' + uuid4().hex[:24]
        url = m.pg.admin_url
        self.control = sa.create_engine(url, isolation_level='AUTOCOMMIT', poolclass=sa.pool.NullPool)
        with self.control.connect() as c:
            c.exec_driver_sql(f'CREATE DATABASE "{self.name}" TEMPLATE template0 ENCODING \'UTF8\'')
        pg_tool('pg_restore.exe', '-h', url.host, '-p', url.port, '-U', url.username, '-w', '--exit-on-error', '-d', self.name, dump)
        self.admin = sa.create_engine(url.set(database=self.name), poolclass=sa.pool.NullPool)
        plain = lambda value: value.set(database=self.name).render_as_string(hide_password=False)  # noqa: E731
        self.db = Database(plain(m.pg.runtime_url))
        self.queue = PgDeletionQueue(plain(m.deploy.worker_url))
        self.operator = PgRestoreOperator(plain(m.deploy.operator_url))
        logins = {kind: engine.url.username for kind, engine in m.a.repository.engines.items()}
        self.repository = PgAuthRepository(
            verifier_url=plain(url.set(username=logins['verifier'], password=SYNTHETIC_PASSWORD)),
            session_url=plain(url.set(username=logins['session'], password=SYNTHETIC_PASSWORD)))
        self.auth = AuthService(self.repository, m.a.crypto, m.a.policy, hash_gate=HashAdmission(m.spool / ('hash-' + uuid4().hex)))
        self.a = SimpleNamespace(service=self.auth, pg=SimpleNamespace(db=self.db, admin=self.admin), crypto=m.a.crypto,
                                 email=m.a.email, uid=m.uid, client=None)

    def build(self, **options):
        ledger = options.pop('ledger', None) or ledger_store(self.m.deploy.services, self.m.deploy.ledger_bucket)
        return build(self.a, self.m.deploy, self.m.spool, database=self.db, queue=self.queue, ledger=ledger, **options)

    def rows(self, sql, **parameters):
        with self.admin.connect() as c:
            return [dict(row) for row in c.execute(sa.text(sql), parameters).mappings()]

    def close(self):
        self.repository.dispose()
        for part in (self.queue, self.operator):
            part.dispose()
        self.db.dispose()
        self.admin.dispose()
        with self.control.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)')
        self.control.dispose()


def test_restored_copy_replays_deletions_revokes_sessions_and_never_revives(d, tmp_path):
    m = d
    m.worker.drain()
    rooms = {name: classroom(m, f'Synthetic {name}') for name in ('erased', 'pending', 'leased', 'kept', 'expiring')}
    assets = {name: upload(m, room, name) for name, room in rooms.items()}
    keys = {name: object_key(m, asset['id']) for name, (asset, _) in assets.items()}
    # Before the backup: one deletion is already leased by a worker, one asset will expire soon.
    leased = delete(m, 'class_session', rooms['leased'].id)
    stale_claim = m.deploy.queue.claim('worker-at-backup-time', 600)
    assert str(stale_claim.request_id) == leased['id']
    with m.pg.admin.begin() as c:
        c.execute(sa.text("UPDATE media_assets SET expires_at=clock_timestamp()+interval '3 seconds' WHERE id=:id"),
                  dict(id=assets['expiring'][0]['id']))
    # Another owner with data of their own, to show the procedure leaves it alone.
    owner_email, other = m.a.email, new_user(m)
    as_user(m, other.email)
    with m.pg.db.transaction(OwnerContext(other.uid)) as tx:
        other_semester = tx.catalog.create_semester('Other synthetic owner')
        other_room = tx.catalog.create_class_session(tx.catalog.create_course(other_semester.id, 'Other course').id, 'Other classroom')
    other_asset, other_data = upload(m, other_room, 'other-owner')
    as_user(m, owner_email)
    old_cookie = m.client.cookies.get('__Host-classroom_session')
    dump = backup(m, tmp_path / 'backup.dump')
    # After the backup: deletions, a logout, and a new upload the copy will not know.
    erased = delete(m, 'class_session', rooms['erased'].id)
    with m.pg.admin.begin() as c:  # release the pre-backup lease so the live system can finish
        c.execute(sa.text("UPDATE deletion_executions SET lease_expires_at=clock_timestamp() WHERE request_id=:id"), dict(id=leased['id']))
    m.worker.drain()
    assert status(m, erased['id'])['state'] == 'deleted' and versions(m, keys['erased']) == []
    pending = delete(m, 'class_session', rooms['pending'].id)
    late, _ = upload(m, rooms['kept'], 'after-backup')
    late_key = object_key(m, late['id'])
    assert m.client.post(BASE + '/auth/logout', json={}, headers=headers(m)).status_code == 200
    m.client.cookies.set('__Host-classroom_session', old_cookie)
    assert m.client.get(BASE + '/auth/session').status_code == 401
    time.sleep(3.2)

    restored = Restored(m, dump)
    try:
        # The raw copy is exactly the hazard: deleted classrooms are live and the old session is valid.
        raw = restored.rows("SELECT id FROM class_sessions WHERE owner_id=:o AND deleted_at IS NULL", o=m.uid)
        assert {r['id'] for r in raw} >= {rooms['erased'].id, rooms['pending'].id}
        assert restored.rows('SELECT 1 FROM auth_sessions WHERE owner_id=:o AND revoked_at IS NULL', o=m.uid)
        assert restored.rows("SELECT status FROM media_assets WHERE id=:id", id=assets['erased'][0]['id'])[0]['status'] == 'committed'
        # It serves nothing until the restore procedure has run.
        closed = restored.build(reconcile=False)
        with TestClient(closed.app, base_url='https://testserver') as client:
            client.cookies.set('__Host-classroom_session', old_cookie)
            for path in ('/auth/session', '/auth/csrf', f'/class-sessions/{rooms["erased"].id}/deletion-preview'):
                assert client.get(BASE + path).status_code == 503
        opened = restored.build(reconcile=False)
        worker = DeletionWorker(queue=restored.queue, store=m.store, policy=POLICY, worker_id='restore-drill')
        head = len(m.deploy.ledger.entries())
        report = RestoreCoordinator(service=opened.deletion, operator=restored.operator, worker=worker).run()
        assert report['ready'] is True and opened.deletion.ready, report
        # The ledger was read at its live head; the expiry found during the restore was appended to it.
        assert report['ledger_head'] == head and len(m.deploy.ledger.entries()) == head + report['expired']
        assert report['seal']['accounts_sealed'] >= 1 and report['seal']['leases_cleared'] >= 1
        assert len(report['reconcile']['applied']) >= 2 and report['reconcile']['unresolved'] == []
        assert report['expired'] >= 1
        assert report['audit']['exposed_media'] == 0 and report['audit']['unaccepted_requests'] == 0
        # Deletions made after the backup hold in the copy, with the same ledger positions.
        for name in ('erased', 'pending', 'leased'):
            with pytest.raises(Gone):
                with restored.db.transaction(OwnerContext(m.uid)) as tx:
                    tx.catalog.get_class_session(rooms[name].id)
            assert restored.rows('SELECT status FROM media_assets WHERE id=:id', id=assets[name][0]['id'])[0]['status'] == 'deleted'
            assert versions(m, keys[name]) == []
        original = {r['intent_id']: r['ledger_generation'] for r in rows(m, 'SELECT intent_id,ledger_generation FROM deletion_executions WHERE owner_id=:o', o=m.uid)}
        copied = {r['intent_id']: r['ledger_generation'] for r in restored.rows('SELECT intent_id,ledger_generation FROM deletion_executions WHERE owner_id=:o', o=m.uid)}
        assert all(copied.get(intent) == generation for intent, generation in original.items() if generation is not None)
        # The deadline that passed after the backup is applied against the present time.
        assert restored.rows('SELECT status FROM media_assets WHERE id=:id', id=assets['expiring'][0]['id'])[0]['status'] == 'deleted'
        assert versions(m, keys['expiring']) == []
        # The lease a worker held at backup time is void in the copy.
        with pytest.raises(StaleDeletionFence):
            restored.queue.finish(stale_claim, 0)
        # The object uploaded after the backup has no manifest in the copy: counted, and kept by default.
        assert report['orphans']['unreferenced'] >= 1 and report['orphans']['unreferenced_removed'] == 0
        assert m.store.inspect(late_key) is not None
        with TestClient(opened.app, base_url='https://testserver') as client:
            # Every session in the copy predates the incident.
            client.cookies.set('__Host-classroom_session', old_cookie)
            assert client.get(BASE + '/auth/session').status_code == 401
            client.cookies.clear()
            assert restored.rows('SELECT 1 FROM auth_sessions WHERE owner_id=:o AND revoked_at IS NULL', o=m.uid) == []
            # A new login works and data that was never deleted is intact, byte for byte.
            restored.a.client = client
            session = login(restored.a)
            view = SimpleNamespace(client=client, ctx=SimpleNamespace(csrf=session['csrf_token']), classroom=rooms['kept'])
            assert client.get(access(view, assets['kept'][0])).content == assets['kept'][1]
            assert client.post(BASE + f'/media/{assets["erased"][0]["id"]}/access', json={}, headers=headers(view)).status_code == 410
            assert client.get(BASE + f'/class-sessions/{rooms["erased"].id}/deletion-preview').status_code == 410
            assert client.get(BASE + f'/class-sessions/{rooms["kept"].id}/deletion-preview').status_code == 200
        # Another owner's data is untouched by the whole procedure and still theirs alone.
        assert restored.rows('SELECT status FROM media_assets WHERE id=:id', id=other_asset['id'])[0]['status'] == 'committed'
        assert m.store.client.get_object(Bucket=m.store.bucket, Key=object_key(m, other_asset['id']))['Body'].read() == other_data
        # (A second process over the same copy: an application instance is not reused after shutdown.)
        with TestClient(restored.build().app, base_url='https://testserver') as client:
            restored.a.client, restored.a.email = client, other.email
            session = login(restored.a)
            view = SimpleNamespace(client=client, ctx=SimpleNamespace(csrf=session['csrf_token']), classroom=other_room)
            assert client.get(access(view, other_asset)).content == other_data
            assert client.post(BASE + f'/media/{assets["kept"][0]["id"]}/access', json={}, headers=headers(view)).status_code == 404
            assert client.get(BASE + f'/class-sessions/{rooms["kept"].id}/deletion-preview').status_code == 404
            restored.a.email = owner_email
        # Running the procedure again is harmless, and the explicit switch removes the unreferenced object.
        again = RestoreCoordinator(service=opened.deletion, operator=restored.operator, worker=worker).run(purge_unreferenced=True)
        assert again['ready'] is True and again['reconcile']['applied'] == [] and again['orphans']['unreferenced_removed'] >= 1
        assert versions(m, late_key) == []
    finally:
        restored.close()
    # The original deployment still completes its own pending work.
    as_user(m, m.a.email)
    m.worker.drain()
    assert status(m, pending['id'])['state'] == 'deleted'


def test_restore_stays_closed_without_an_intact_ledger(d, tmp_path):
    m = d
    m.worker.drain()
    asset, _ = upload(m)
    earlier_room = classroom(m, 'Deleted before the backup')
    upload(m, earlier_room, 'earlier')
    earlier = delete(m, 'class_session', earlier_room.id)
    m.worker.drain()
    assert status(m, earlier['id'])['state'] == 'deleted'
    dump = backup(m, tmp_path / 'backup.dump')
    done = delete(m, 'class_session', m.classroom.id)
    restored = Restored(m, dump)
    try:
        worker = DeletionWorker(queue=restored.queue, store=m.store, policy=POLICY, worker_id='restore-drill')
        # A tampered ledger is refused before any step runs: the copy is not even sealed.
        client = m.deploy.services.ledger.client()
        copy = 'pr04b-tampered-' + uuid4().hex
        client.create_bucket(Bucket=copy)
        for item in client.list_objects_v2(Bucket=m.deploy.ledger_bucket, Prefix='ledger/v1/')['Contents']:
            body = client.get_object(Bucket=m.deploy.ledger_bucket, Key=item['Key'])['Body'].read()
            client.put_object(Bucket=copy, Key=item['Key'], Body=body)
        first = client.get_object(Bucket=copy, Key='ledger/v1/00000000000000000001.json')['Body'].read()
        client.put_object(Bucket=copy, Key='ledger/v1/00000000000000000001.json', Body=first[:-1] + b' }')
        tampered = restored.build(ledger=ledger_store(m.deploy.services, copy), reconcile=False)
        with pytest.raises(LedgerCorrupt):
            RestoreCoordinator(service=tampered.deletion, operator=restored.operator, worker=worker).run()
        assert tampered.deletion.ready is False
        assert restored.rows('SELECT 1 FROM auth_sessions WHERE owner_id=:o AND revoked_at IS NULL', o=m.uid)
        with TestClient(tampered.app, base_url='https://testserver') as browser:
            assert browser.get(BASE + '/auth/csrf').status_code == 503
        # A ledger that has lost history (here: an empty one) contradicts the deletions the copy
        # already holds. The copy is sealed, never opened, and nothing recorded later is applied.
        empty = 'pr04b-empty-' + uuid4().hex
        client.create_bucket(Bucket=empty)
        lost = restored.build(ledger=ledger_store(m.deploy.services, empty), reconcile=False)
        with pytest.raises(LedgerCorrupt):
            RestoreCoordinator(service=lost.deletion, operator=restored.operator, worker=worker).run()
        assert lost.deletion.ready is False
        assert restored.rows('SELECT status FROM media_assets WHERE id=:id', id=asset['id'])[0]['status'] == 'committed'
        with TestClient(lost.app, base_url='https://testserver') as browser:
            assert browser.get(BASE + '/auth/csrf').status_code == 503
        # With the intact ledger the same copy opens, and the post-backup deletion holds.
        good = restored.build(reconcile=False)
        report = RestoreCoordinator(service=good.deletion, operator=restored.operator, worker=worker).run()
        assert report['ready'] is True
        assert restored.rows('SELECT status FROM media_assets WHERE id=:id', id=asset['id'])[0]['status'] == 'deleted'
    finally:
        restored.close()
    as_user(m, m.a.email)
    m.worker.drain()
    assert status(m, done['id'])['state'] == 'deleted'
