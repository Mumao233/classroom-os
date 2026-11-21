"""Shared PR-04b fixtures: one deployment = database + media bucket + independent ledger.

The media store and the ledger store are two separate SeaweedFS processes so either can
be taken away on its own. Every test gets a new user; the deployment is shared by the
module because the ledger position of a deletion is unique per database.
"""
from datetime import timedelta
import json
import os
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa

from conftest import migrated_database
from object_service import ACCESS, REGION, SECRET, ObjectService
from test_authentication import BASE, PASSWORD, auth, login  # noqa: F401  (auth is a fixture)
from classroom_web.application.deletion import DeletionService, DeletionWorker
from classroom_web.application.media import MediaService, fingerprint, upload_dto
from classroom_web.domain.auth import BrowserContext
from classroom_web.domain.data import OwnerContext
from classroom_web.domain.deletion import DeletionPolicy
from classroom_web.entrypoints.http import create_app
from classroom_web.infrastructure.deletion_ledger import S3DeletionLedger
from classroom_web.infrastructure.deletion_repository import PgDeletionQueue, PgRestoreOperator
from classroom_web.infrastructure.object_store import S3ObjectStore
from classroom_web.test_support import SYNTHETIC_PASSWORD

__all__ = ['BASE', 'PASSWORD', 'POLICY', 'SECRET_KEY', 'auth', 'login', 'pg', 'services', 'deploy', 'd',
           'contract', 'build', 'rows', 'one', 'object_key', 'versions', 'as_user', 'new_user', 'classroom', 'settled']

SECRET_KEY = b'pr04b-synthetic-confirmation-key-0001'
# No waiting in ordinary tests; the settle window has its own test with a real delay.
POLICY = DeletionPolicy(write_settle=timedelta(0), retry_backoff=timedelta(0), max_backoff=timedelta(0),
                        lease_seconds=30, orphan_grace=timedelta(0))


def contract():
    return json.loads((Path(__file__).resolve().parents[4] / 'docs/web-alpha/architecture/contracts/openapi.json')
                      .read_text(encoding='utf8'))


@pytest.fixture(scope='module')
def services():
    root = Path(os.environ.get('PR04B_OBJECT_ROOT') or pytest.fail('PR04B_OBJECT_ROOT must name the owned object directory'))
    root = root / uuid4().hex
    media = ObjectService(root, 'media')
    ledger = None
    try:
        # Let the first service bind its HTTP and gRPC ports before choosing the next set.
        media.start()
        ledger = ObjectService(root, 'ledger')
        ledger.start()
        yield SimpleNamespace(media=media, ledger=ledger)
    finally:
        media.stop()
        if ledger is not None:
            ledger.stop()


@pytest.fixture(scope='module')
def pg():
    """A dedicated database: this module's deletions never share ledger positions with another."""
    with migrated_database() as database:
        yield database


def _login(pg, group, created):
    name = 'pr02a_del_' + uuid4().hex[:20]
    with pg.admin.begin() as c:
        c.exec_driver_sql(f'CREATE ROLE "{name}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
        c.exec_driver_sql(f'GRANT {group} TO "{name}"')
    created.append(name)
    return pg.admin_url.set(username=name, password=SYNTHETIC_PASSWORD)


def object_store(services, bucket):
    return S3ObjectStore(endpoint=services.media.endpoint, bucket=bucket, region=REGION,
                         access_key=ACCESS, secret_key=SECRET, synthetic_loopback=True)


def ledger_store(services, bucket, endpoint=None):
    return S3DeletionLedger(endpoint=endpoint or services.ledger.endpoint, bucket=bucket, region=REGION,
                            access_key=ACCESS, secret_key=SECRET, synthetic_loopback=True)


class StallProxy:
    """Loopback TCP relay to a real store. While ``stalled`` it keeps every peer
    connected and relays nothing: the store has accepted the connection and is silent."""
    def __init__(self, endpoint):
        self.target = int(endpoint.rsplit(':', 1)[1])
        self.stalled = self.closed = False
        self.sockets = []
        self.listener = socket.create_server(('127.0.0.1', 0))
        self.endpoint = f'http://127.0.0.1:{self.listener.getsockname()[1]}'
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                client, _ = self.listener.accept()
                upstream = socket.create_connection(('127.0.0.1', self.target))
            except OSError:
                return
            self.sockets += [client, upstream]
            for source, sink in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._relay, args=(source, sink), daemon=True).start()

    def _relay(self, source, sink):
        try:
            while True:
                data = source.recv(65536)
                if not data:
                    break
                while self.stalled and not self.closed:
                    time.sleep(0.05)
                sink.sendall(data)
        except OSError:
            pass
        finally:
            for item in (source, sink):
                try:
                    item.close()
                except OSError:
                    pass

    def close(self):
        self.closed = True
        for item in [self.listener, *self.sockets]:
            try:
                item.close()
            except OSError:
                pass


@pytest.fixture(scope='module')
def deploy(pg, services):
    created = []
    media_bucket, ledger_bucket = 'pr04b-media-' + uuid4().hex, 'pr04b-ledger-' + uuid4().hex
    client = services.media.client()
    client.create_bucket(Bucket=media_bucket)
    # Versioned on purpose: deletion must remove history, not add a delete marker.
    client.put_bucket_versioning(Bucket=media_bucket, VersioningConfiguration={'Status': 'Enabled'})
    services.ledger.client().create_bucket(Bucket=ledger_bucket)
    worker_url, operator_url = (_login(pg, group, created) for group in
                                ('classroom_web_deletion_worker', 'classroom_web_restore_operator'))
    queue = PgDeletionQueue(worker_url.render_as_string(hide_password=False))
    operator = PgRestoreOperator(operator_url.render_as_string(hide_password=False))
    try:
        yield SimpleNamespace(pg=pg, services=services, media_bucket=media_bucket, ledger_bucket=ledger_bucket,
                              store=object_store(services, media_bucket), ledger=ledger_store(services, ledger_bucket),
                              queue=queue, operator=operator, worker_url=worker_url, operator_url=operator_url)
    finally:
        queue.dispose()
        operator.dispose()
        with pg.admin.begin() as c:
            for name in created:
                c.exec_driver_sql(f'DROP OWNED BY "{name}"')
                c.exec_driver_sql(f'DROP ROLE "{name}"')


def build(a, deploy, spool, *, database=None, queue=None, ledger=None, store=None, policy=POLICY, reconcile=True):
    """One application instance (media + deletion) over the given deployment parts."""
    database = database or a.pg.db
    store = store or deploy.store
    media = MediaService(auth=a.service, database=database, store=store)
    deletion = DeletionService(auth=a.service, database=database, queue=queue or deploy.queue, store=store,
                               ledger=ledger or deploy.ledger, confirmation_secret=SECRET_KEY, policy=policy)
    if reconcile:
        deletion.reconcile()
    app = create_app(auth=a.service, contract=contract(), origin='https://testserver', media=media,
                     media_spool_directory=spool, deletion=deletion)
    worker = DeletionWorker(queue=queue or deploy.queue, store=store, policy=policy, worker_id='pr04b-' + uuid4().hex[:12])
    return SimpleNamespace(app=app, service=media, deletion=deletion, worker=worker, store=store)


def seed_legacy_package_upload(m, body, key=None):
    """Persist a reservation made before the default import-package gate."""
    with m.service.transaction(m.ctx, write=True) as (repo, _):
        upload, asset = repo.create(body, key or uuid4(), fingerprint(body), m.service.policy)
    return upload_dto(upload, asset)


def classroom(m, title='Synthetic classroom', course=None):
    with m.a.pg.db.transaction(OwnerContext(m.uid)) as tx:
        return tx.catalog.create_class_session((course or m.course).id, title)


@pytest.fixture
def d(auth, deploy, tmp_path):
    built = build(auth, deploy, tmp_path)
    with TestClient(built.app, base_url='https://testserver') as client:
        auth.client = client
        session = login(auth)
        ctx = BrowserContext(session=client.cookies.get('__Host-classroom_session'), csrf=session['csrf_token'])
        with auth.pg.db.transaction(OwnerContext(auth.uid)) as tx:
            semester = tx.catalog.create_semester('Synthetic deletion')
            course = tx.catalog.create_course(semester.id, 'Synthetic course')
            room = tx.catalog.create_class_session(course.id, 'Synthetic deletion session')
        yield SimpleNamespace(a=auth, uid=auth.uid, client=client, ctx=ctx, session=session, deploy=deploy, pg=auth.pg,
                              semester=semester, course=course, classroom=room, spool=tmp_path, **vars(built))


def rows(m, sql, **parameters):
    with m.pg.admin.connect() as c:
        return [dict(row) for row in c.execute(sa.text(sql), parameters).mappings()]


def one(m, sql, **parameters):
    found = rows(m, sql, **parameters)
    assert len(found) == 1, found
    return found[0]


def settled(m, deletion_id):
    """Every inventoried object is proven absent and the worker has let go of the request.

    For a classroom, course, semester or account that is not completion: database content
    is not erased by this package, so the request must still be open, not reported deleted.
    """
    row = one(m, """SELECT d.state,d.completed_at,d.last_error_code,x.objects_absent_at,x.leased_by FROM deletion_requests d
        JOIN deletion_executions x ON x.request_id=d.id WHERE d.id=:id""", id=deletion_id)
    open_items = rows(m, "SELECT 1 FROM deletion_assets WHERE request_id=:id AND state<>'absent'", id=deletion_id)
    return (row['state'] == 'pending' and row['completed_at'] is None and row['last_error_code'] is None
            and row['objects_absent_at'] is not None and row['leased_by'] is None and open_items == [])


def object_key(m, asset_id):
    return one(m, 'SELECT object_key FROM media_assets WHERE id=:id', id=asset_id)['object_key']


def versions(m, key):
    """Every stored version and delete marker of one key, straight from the store."""
    return m.store._versions(key)


def new_user(m):
    other = uuid4()
    with m.pg.admin.begin() as c:
        c.execute(sa.text("""INSERT INTO users(id,normalized_email,password_hash,role,state,email_verified_at)
            VALUES(:id,:email,:hash,'student','active',clock_timestamp())"""),
                  dict(id=other, email=str(other) + '@example.test', hash=m.a.crypto.hash(PASSWORD)))
    return SimpleNamespace(uid=other, email=str(other) + '@example.test')


def as_user(m, email):
    """Sign the shared browser client in as another account; returns the new session."""
    m.client.cookies.clear()
    m.a.email = email
    session = login(m.a)
    m.ctx = BrowserContext(session=m.client.cookies.get('__Host-classroom_session'), csrf=session['csrf_token'])
    m.session = session
    return session
