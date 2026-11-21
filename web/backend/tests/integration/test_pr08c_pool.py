"""Fill each portable pool with exactly 5 GB of actual private object versions."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
from threading import Barrier
import os
from pathlib import Path
from shutil import disk_usage
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4

import pytest

from classroom_web.application.portable_maintenance import PortableMaintenance
from classroom_web.application.portable_transfers import PortableTransfers
from classroom_web.domain.auth import AuthRejected
from classroom_web.domain.data import OwnerContext
from classroom_web.domain.portable import PORTABLE_V21
from classroom_web.infrastructure.portable_lifecycle import PortableLifecycle
from deletion_support import auth, d, deploy, new_user, pg, versions
from object_service import ObjectService
from test_pr08c_v21 import denied, modify

SHA = 'a' * 64


@pytest.fixture(scope='module')
def services():
    root = Path(os.environ['PR04B_OBJECT_ROOT'])
    root.mkdir(parents=True, exist_ok=True)
    if disk_usage(root).free < 13_000_000_000:
        pytest.fail('owned VMware volume needs 13 GB free for both physical pool runs')
    with TemporaryDirectory(prefix='portable-pools-', dir=root) as path:
        media = ObjectService(Path(path), 'media', volumes=256)
        ledger = ObjectService(Path(path), 'ledger')
        try:
            media.start()
            ledger.start()
            yield SimpleNamespace(media=media, ledger=ledger)
        finally:
            ledger.stop()
            media.stop()


def binary_fixture(root, length):
    path = root / f'capacity-{length}.bin'
    with path.open('wb') as output:
        output.truncate(length)
    with path.open('rb') as content:
        sha = hashlib.file_digest(content,'sha256').hexdigest()
    return path, sha


@pytest.mark.parametrize('kind', ['import','export'])
def test_real_5gb_pool_rejects_any_new_reservation_until_versions_are_removed(d, kind):
    service = PortableTransfers(d.service,d.deletion,d.spool)
    owners = [OwnerContext(new_user(d).uid) for _ in range(11)]
    package = PORTABLE_V21.archive_bytes
    sizes = [package] if kind == 'export' else [16_000_000]*31 + [4_000_000]
    fixtures = {size:binary_fixture(d.spool,size) for size in set(sizes)}
    archive_sha = fixtures[package][1] if package in fixtures else hashlib.sha256(bytes(1)).hexdigest()

    def reserve(owner):
        with d.pg.db.transaction(owner) as tx:
            return PortableLifecycle(tx).reserve(kind, package, uuid4(), 'a'*64,
                **(dict(archive_sha256=archive_sha,mime='application/zip') if kind=='import' else {}))['id']

    def fill(item):
        owner, transfer = item
        keys = []
        for number,size in enumerate(sizes):
            path,sha = fixtures[size]
            with d.pg.db.transaction(owner) as tx:
                asset = PortableLifecycle(tx).stage(transfer,number,size,sha,'application/octet-stream')
            with path.open('rb') as content:
                version = service._put(asset,content)
            with d.pg.db.transaction(owner) as tx:
                PortableLifecycle(tx).attach(transfer,asset,version)
            assert version.byte_length == size and version.version_id
            keys.append(version.key)
        return keys

    transfers = [(owner,reserve(owner)) for owner in owners[:10]]
    with ThreadPoolExecutor(max_workers=2) as workers:
        keys = list(workers.map(fill,transfers))
    assert sum(d.store.inspect(key).byte_length for group in keys for key in group) == 5_000_000_000
    assert d.deploy.queue.portable_maintenance()[kind+'_bytes'] == 5_000_000_000
    assert denied('RATE_LIMITED',reserve,owners[-1]).status == 429
    assert all(versions(d,key) for key in keys[0])
    owner,transfer = transfers.pop(0)
    with d.pg.db.transaction(owner) as tx:
        PortableLifecycle(tx).close(transfer)
    denied('RATE_LIMITED',reserve,owners[-1])
    maintainer = PortableMaintenance(service,d.worker)
    assert maintainer.tick()[kind+'_bytes'] == 4_500_000_000
    assert not any(versions(d,key) for key in keys[0])
    transfers.append((owners[-1],reserve(owners[-1])))
    for owner,transfer in transfers:
        with d.pg.db.transaction(owner) as tx:
            PortableLifecycle(tx).close(transfer)
    for _ in range(6):
        maintainer.next_tick = 0
        if maintainer.tick()[kind+'_bytes'] == 0:
            break
    assert d.deploy.queue.portable_maintenance()[kind+'_bytes'] == 0
    assert not any(versions(d,key) for group in keys for key in group)
    print(f'{kind} pool: 5,000,000,000 stored bytes in {sum(map(len,keys))} versioned objects; zero versions remain')


@pytest.mark.parametrize('kind', ['import','export'])
def test_5gb_pool_saturates_transactionally_and_waits_for_real_erase(d, kind):
    transfer_service = PortableTransfers(d.service,d.deletion,d.spool)
    owners = [OwnerContext(new_user(d).uid) for _ in range(12)]
    start = Barrier(len(owners))
    def reserve(owner, *, synchronized=True):
        if synchronized:
            start.wait(timeout=30)
        try:
            with d.pg.db.transaction(owner) as tx:
                life = PortableLifecycle(tx)
                row = life.reserve(kind, PORTABLE_V21.archive_bytes, uuid4(), SHA,
                    **(dict(archive_sha256=SHA,mime='application/zip') if kind=='import' else {}))
                return owner, row['id']
        except AuthRejected as error:
            assert error.status == 429
            return owner, None
    with ThreadPoolExecutor(max_workers=len(owners)) as workers:
        results = list(workers.map(reserve,owners))
    accepted = [(owner, identity) for owner,identity in results if identity]
    rejected = [owner for owner,identity in results if identity is None]
    assert len(accepted) == 10 and len(rejected) == 2
    assert d.deploy.queue.portable_maintenance()[kind+'_bytes'] == PORTABLE_V21.pool_bytes
    owner, identity = accepted[0]
    content = b'synthetic storage occupancy proof'
    with d.pg.db.transaction(owner) as tx:
        life = PortableLifecycle(tx)
        asset = life.stage(identity,0,len(content),hashlib.sha256(content).hexdigest(),'application/octet-stream')
    stored = transfer_service._put(asset,io.BytesIO(content))
    assert stored.byte_length == len(content)
    with d.pg.db.transaction(owner) as tx:
        life = PortableLifecycle(tx)
        life.attach(identity,asset,stored)
        life.close(identity)
    queue = d.deploy.queue
    assert queue.portable_maintenance()[kind+'_bytes'] == PORTABLE_V21.pool_bytes
    assert reserve(rejected[0],synchronized=False)[1] is None
    PortableMaintenance(transfer_service,d.worker).tick()
    assert not versions(d,asset['object_key'])
    assert queue.portable_maintenance()[kind+'_bytes'] == 4_500_000_000
    assert reserve(rejected[0],synchronized=False)[1]
    assert queue.portable_maintenance()[kind+'_bytes'] == PORTABLE_V21.pool_bytes
    modify(d,"UPDATE portable_transfers SET state='closed',access_until=clock_timestamp() WHERE kind=:kind AND erased_at IS NULL",kind=kind)
    assert queue.portable_maintenance()[kind+'_bytes'] == 0
