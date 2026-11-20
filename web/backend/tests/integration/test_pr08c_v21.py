"""The three backend §8 experiments, in an isolated VMware deployment."""
from datetime import timedelta
import hashlib
import io
import json
import struct
from uuid import UUID, uuid4
import zlib

from PIL import Image
import pytest
import sqlalchemy as sa

from deletion_support import as_user, auth, classroom, d, deploy, new_user, object_key, one, pg, services, versions
from test_deletion_lifecycle import delete

from classroom_web.application.portable_transfers import PortableTransfers
from classroom_web.domain.auth import AuthRejected
from classroom_web.domain.data import OwnerContext
from classroom_web.domain.portable import PORTABLE_V21
from classroom_web.domain.portable_export import ArchiveAsset, ArchiveClassroom, aggregate_entries
from classroom_web.infrastructure.portable_lifecycle import PortableLifecycle

pytestmark = pytest.mark.filterwarnings('error::ResourceWarning')


def candidate(d):
    return PortableTransfers(d.service, d.deletion, d.spool)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def modify(d, sql, **values):
    with d.pg.admin.begin() as conn:
        conn.execute(sa.text(sql), values)


def archive_room(room, media=(), files=(), slides=()):
    session = dict(id=room.id.hex, title=room.title, created_at=room.created_at.isoformat(),
                   segments=[dict(id='S0001', text='Synthetic transcript', version=0, start=0, end=1)],
                   slides=list(slides))
    return ArchiveClassroom(session, tuple(media), tuple(files))


def stage_json(service, ctx, room):
    payload = json.dumps(archive_room(room).session).encode()
    upload = service.begin_upload(ctx, uuid4(), len(payload), digest(payload), 'application/json')
    service.put_part(ctx, uuid4(), upload['id'], 0, 0, len(payload), digest(payload), io.BytesIO(payload))
    service.seal(ctx, uuid4(), upload['id'], digest(payload))
    return upload['id']


def denied(code, action, *args, **kwargs):
    with pytest.raises(AuthRejected) as caught:
        action(*args, **kwargs)
    assert caught.value.code == code
    return caught.value


def test_four_waiting_previews_reject_fifth_without_queue(d):
    service = candidate(d)
    contexts = [(d.ctx, d.uid, d.course.id, d.classroom)]
    for _ in range(4):
        user = new_user(d)
        as_user(d, user.email)
        with d.pg.db.transaction(OwnerContext(user.uid)) as tx:
            semester = tx.catalog.create_semester('Synthetic')
            course = tx.catalog.create_course(semester.id, 'Candidate')
            room = tx.catalog.create_class_session(course.id, 'Synthetic')
        contexts.append((d.ctx, user.uid, course.id, room))
    uploads = [stage_json(service, ctx, room) for ctx, _, _, room in contexts]
    first = []
    for (ctx, _, course, _), upload in zip(contexts[:4], uploads[:4], strict=True):
        key = uuid4()
        result = service.preview(ctx, key, upload, course)
        assert service.preview(ctx, key, upload, course) == result
        first.append(result)
    busy_ctx, owner, _, busy_room = contexts[0]
    denied('RATE_LIMITED', service.publish_zip, busy_ctx, uuid4(), 'class_session', [archive_room(busy_room)])
    assert one(d, "SELECT count(*) FROM portable_transfers WHERE kind='export' AND owner_id=:uid", uid=owner)['count'] == 0
    ctx, fifth, course, _ = contexts[-1]
    key = uuid4()
    error = denied('RATE_LIMITED', service.preview, ctx, key, uploads[-1], course)
    assert error.status == 429 and error.retry_after_seconds > 0
    assert one(d, 'SELECT count(*) FROM portable_slots WHERE owner_id=:uid', uid=fifth)['count'] == 0
    denied('NOT_FOUND', service.upload_status, ctx, uploads[0])

    oldest = UUID(first[0]['slot_id'])
    modify(d, "UPDATE portable_slots SET deadline=clock_timestamp()-interval '1 second' WHERE id=:id", id=oldest)
    replacement = candidate(d)
    admitted = replacement.preview(ctx, key, uploads[-1], course)
    assert admitted['class_session_count'] == 1
    with d.pg.db.transaction(OwnerContext(contexts[0][1])) as tx:
        denied('GONE', PortableLifecycle(tx).confirm, oldest, uuid4())
    assert one(d, 'SELECT state FROM portable_transfers WHERE id=:id', id=uploads[0])['state'] == 'verified'

    admitted_id = UUID(admitted['slot_id'])
    worker = uuid4()
    with d.pg.db.transaction(OwnerContext(fifth)) as tx:
        running = PortableLifecycle(tx).confirm(admitted_id, worker)
    modify(d, "UPDATE portable_slots SET deadline=clock_timestamp()-interval '1 second' WHERE id=:id", id=admitted_id)
    replacement.maintain()
    with d.pg.db.transaction(OwnerContext(fifth)) as tx:
        life = PortableLifecycle(tx)
        denied('LEASE_CONFLICT', life.heartbeat, admitted_id, worker, running['fence'])
        denied('LEASE_CONFLICT', life.finish, admitted_id, worker, running['fence'], 'completed')
    assert one(d, 'SELECT state FROM portable_slots WHERE id=:id', id=admitted_id)['state'] == 'failed'


def test_range_rechecks_expiry_source_and_object_version(d):
    service = candidate(d)
    secondary = classroom(d)
    published = service.publish_zip(d.ctx, uuid4(), 'course', [archive_room(d.classroom), archive_room(secondary)])
    asset = UUID(published['asset_id'])
    ticket = service.issue_ticket(d.ctx, asset)
    assert ticket['asset_version'] == published['asset_version']
    assert ticket['expires_at'] - d.deletion.clock.now() <= timedelta(seconds=60)
    assert service.read_range(d.ctx, asset, ticket['ticket'], 'bytes=0-3') == b'PK\x03\x04'
    denied('NOT_FOUND', d.service.access, d.ctx, uuid4(), asset)
    denied('SOURCE_CHANGED', service.issue_ticket, d.ctx, asset, expected_version='wrong-version')
    modify(d, "UPDATE media_tickets SET expires_at=clock_timestamp()-interval '1 second' WHERE asset_id=:id", id=asset)
    denied('FORBIDDEN', service.read_range, d.ctx, asset, ticket['ticket'], 'bytes=4-20')
    renewed = service.issue_ticket(d.ctx, asset, expected_version=ticket['asset_version'])
    assert len(service.read_range(d.ctx, asset, renewed['ticket'], 'bytes=4-20')) == 17

    delete(d, 'class_session', secondary.id)
    denied('GONE', service.read_range, d.ctx, asset, renewed['ticket'], 'bytes=21-40')
    denied('GONE', service.issue_ticket, d.ctx, asset, expected_version=ticket['asset_version'])
    transfer = one(d, 'SELECT transfer_id FROM portable_transfer_media WHERE asset_id=:id', id=asset)['transfer_id']
    service.maintain()
    cutoff = one(d, 'SELECT access_until FROM portable_transfers WHERE id=:id', id=transfer)['access_until']
    assert cutoff == one(d, "SELECT access_revoked_at FROM deletion_requests WHERE scope_id=:id", id=secondary.id)['access_revoked_at']
    modify(d, "UPDATE portable_transfers SET access_until=clock_timestamp()-interval '25 hours' WHERE id=:id", id=transfer)
    assert service.maintain()['alerts'] == 1
    modify(d, "UPDATE portable_transfers SET access_until=clock_timestamp()-interval '73 hours' WHERE id=:id", id=transfer)
    assert service.maintain()['incidents'] == 1
    key = object_key(d, asset)
    assert versions(d, key)
    d.worker.drain()
    service.maintain()
    assert not versions(d, key)
    assert one(d, 'SELECT erased_at FROM portable_transfers WHERE id=:id', id=transfer)['erased_at'] is not None

    next_export = service.publish_zip(d.ctx, uuid4(), 'class_session', [archive_room(d.classroom)])
    expired = UUID(next_export['asset_id'])
    new_ticket = service.issue_ticket(d.ctx, expired)
    assert service.read_range(d.ctx, expired, new_ticket['ticket'], 'bytes=0-3') == b'PK\x03\x04'
    expired_key = object_key(d, expired)
    d.store.client.put_object(Bucket=d.store.bucket, Key=expired_key, Body=b'synthetic replacement')
    assert len(versions(d, expired_key)) == 2
    denied('SOURCE_CHANGED', service.read_range, d.ctx, expired, new_ticket['ticket'], 'bytes=4-20')
    denied('SOURCE_CHANGED', service.issue_ticket, d.ctx, expired, expected_version=new_ticket['asset_version'])
    modify(d, '''UPDATE portable_transfers SET access_until=clock_timestamp()-interval '1 second'
        WHERE id=(SELECT transfer_id FROM portable_transfer_media WHERE asset_id=:id)''', id=expired)
    denied('GONE', service.read_range, d.ctx, expired, new_ticket['ticket'], 'bytes=4-20')
    denied('GONE', service.issue_ticket, d.ctx, expired, expected_version=new_ticket['asset_version'])
    service.maintain()
    d.worker.drain()
    service.maintain()
    assert not versions(d, expired_key)


class Slice(io.RawIOBase):
    """One bounded input stream over a part, backed by the downloaded spool."""
    def __init__(self, content):
        self.content = memoryview(content)
        self.offset = 0

    def read(self, length):
        end = self.offset + length
        block = self.content[self.offset:end].tobytes()
        self.offset += len(block)
        return block


def padded_png(base, index, size):
    # Deterministic valid ancillary chunk. The image still goes through the real
    # PNG checks and decoder; repeated bytes are stored with ZIP_STORED.
    payload = struct.pack('>I', index) + bytes(size - len(base) - 16)
    chunk = b'paDd' + payload
    return base[:-12] + struct.pack('>I', len(payload)) + chunk + struct.pack('>I', zlib.crc32(chunk)) + base[-12:]


def big_classroom(d):
    output = io.BytesIO()
    Image.new('RGB', (2, 3), 'blue').save(output, format='PNG')
    base = output.getvalue()

    def make(last):
        media, files, slides = [], [], []
        for index in range(32):
            size = 15_700_000 if index < 31 else last
            sha = digest(padded_png(base, index, size))
            path = f'assets/{sha}.png'
            reference = f'slides/{index:032x}.png'
            identity = f'P{index:04d}'
            files.append(ArchiveAsset(path, size, sha, lambda n=index, count=size: io.BytesIO(padded_png(base, n, count))))
            slides.append(dict(id=identity, path=reference, start=0, end=1, version=1,
                               sha256=sha, mime_type='image/png', width=2, height=3))
            media.append(dict(kind='image', id=identity, version=1, sha256=sha, original_path=reference,
                              archive_path=path, availability='available', size=size))
        return archive_room(d.classroom, media, files, slides)

    first = make(13_200_000)
    _, size = aggregate_entries('class_session', [first])
    return make(13_200_000 + PORTABLE_V21.archive_bytes - size)


def test_500mb_actual_export_download_and_multipart_repreview(d):
    service = candidate(d)
    snapshot = big_classroom(d)
    result = service.publish_zip(d.ctx, uuid4(), 'class_session', [snapshot])
    assert result['byte_length'] == PORTABLE_V21.archive_bytes
    initial = service.issue_ticket(d.ctx, result['asset_id'])
    denied('CAPACITY_EXCEEDED', service.begin_upload, d.ctx, uuid4(),
           PORTABLE_V21.archive_bytes + 1, result['sha256'], 'application/zip')
    upload = service.begin_upload(d.ctx, uuid4(), result['byte_length'], result['sha256'], 'application/zip')
    denied('RATE_LIMITED', service.begin_upload, d.ctx, uuid4(), result['byte_length'], result['sha256'], 'application/zip')
    denied('SOURCE_UNAVAILABLE', service.seal, d.ctx, uuid4(), upload['id'], result['sha256'])
    verification = hashlib.sha256()
    first_part_key = uuid4()
    for number, offset in enumerate(range(0, result['byte_length'], PORTABLE_V21.part_bytes)):
        end = min(result['byte_length'], offset + PORTABLE_V21.part_bytes)
        if number == 16:
            service = candidate(d)
            assert len(service.upload_status(d.ctx, upload['id'])[1]) == number
        ticket = service.issue_ticket(d.ctx, result['asset_id'], expected_version=initial['asset_version'])
        chunk = service.read_range(d.ctx, result['asset_id'], ticket['ticket'], f'bytes={offset}-{end-1}')
        verification.update(chunk)
        if number == 0:
            denied('VALIDATION_FAILED', service.put_part, d.ctx, uuid4(), upload['id'], number,
                   offset, len(chunk), '0' * 64, Slice(chunk))
        if number == 1:
            denied('IDEMPOTENCY_MISMATCH', service.put_part, d.ctx, first_part_key, upload['id'],
                   number, offset, len(chunk), digest(chunk), Slice(chunk))
        op = first_part_key if number == 0 else uuid4()
        service.put_part(d.ctx, op, upload['id'], number, offset, len(chunk), digest(chunk), Slice(chunk))
    assert verification.hexdigest() == result['sha256']
    assert len(service.upload_status(d.ctx, upload['id'])[1]) == 32
    service.seal(d.ctx, uuid4(), upload['id'], result['sha256'])
    shown = service.preview(d.ctx, uuid4(), upload['id'], d.course.id)
    assert shown['archive_sha256'] == result['sha256']
    assert shown['class_session_count'] == 1
    assert shown['media_bytes'] == sum(item.size for item in snapshot.assets)
    service.cancel(d.ctx, upload['id'])
    export = one(d, 'SELECT transfer_id FROM portable_transfer_media WHERE asset_id=:id',
                 id=UUID(result['asset_id']))['transfer_id']
    service.cancel(d.ctx, export)
    service.maintain()
    d.worker.drain()
    service.maintain()
    for transfer in (upload['id'], export):
        assert one(d, 'SELECT erased_at FROM portable_transfers WHERE id=:id', id=transfer)['erased_at'] is not None
