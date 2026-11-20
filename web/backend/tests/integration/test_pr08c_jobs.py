"""PR-08c HTTP, crash recovery, queue and charged pool capacity over PostgreSQL and S3."""
import hashlib
import io
import json
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from classroom_web.application.portable_commit import ImportCopy
from classroom_web.application.portable_jobs import PortableJobs, PortableTaskWorker
from classroom_web.application.portable_maintenance import PortableMaintenance
from classroom_web.application.portable_transfers import PortableTransfers
from classroom_web.domain.data import OwnerContext
from classroom_web.domain.portable import PORTABLE_V21
from classroom_web.domain.portable_export import ArchiveAsset, write_aggregate
from classroom_web.entrypoints.http import create_app
from classroom_web.infrastructure.portable_lifecycle import PortableLifecycle
from classroom_web.infrastructure.qa_repository import PgQaRepository
from classroom_web.entrypoints.worker import portable_worker
from deletion_support import BASE, auth, contract, d, deploy, new_user, one, pg, rows, services, versions
from test_persistent_tasks import scheduler
from test_deletion_lifecycle import delete
from object_service import ACCESS, REGION, SECRET
from test_pr08c_v21 import archive_room, modify

@pytest.fixture
def enabled(d):
    service = PortableTransfers(d.service, d.deletion, d.spool)
    jobs = PortableJobs(service)
    app = create_app(auth=d.a.service, contract=contract(), origin='https://testserver', media=d.service,
                     media_spool_directory=d.spool, deletion=d.deletion, portable_jobs=jobs)
    with TestClient(app, base_url='https://testserver', cookies=dict(d.client.cookies)) as client:
        yield jobs, client


def write_headers(d, key=None):
    return {'origin': 'https://testserver', 'sec-fetch-site': 'same-origin', 'x-csrf-token': d.session['csrf_token'],
            'idempotency-key': str(key or uuid4())}


def response(response, status):
    assert response.status_code == status, response.text
    return response.json()


def upload(d, client, data, mime):
    digest = hashlib.sha256(data).hexdigest()
    root = BASE + '/imports/uploads'
    opened = response(client.post(root, headers=write_headers(d),
        json=dict(byte_length=len(data), archive_sha256=digest, mime_type=mime)), 201)
    scope = f'{root}/{opened["upload_id"]}'
    for part, start in enumerate(range(0, len(data), PORTABLE_V21.part_bytes)):
        block = data[start:start + PORTABLE_V21.part_bytes]
        headers = write_headers(d) | {'content-type': 'application/octet-stream',
            'x-part-sha256': hashlib.sha256(block).hexdigest(), 'x-part-offset': str(start)}
        response(client.put(f'{scope}/parts/{part}', headers=headers, content=block), 200)
    sealed = response(client.post(f'{scope}/seal', headers=write_headers(d), json={'archive_sha256':digest}), 202)
    assert sealed['state'] == 'verified'
    return response(client.post(BASE + '/imports/preview', headers=write_headers(d),
        json={'upload_id':opened['upload_id'], 'destination_course_id':str(d.course.id)}), 201)


def pack(d):
    output = io.BytesIO()
    Image.new('RGB', (2, 3), 'blue').save(output, 'PNG')
    image = output.getvalue()
    sha = hashlib.sha256(image).hexdigest()
    path, original = f'assets/{sha}.png', 'slides/' + 'b'*32 + '.png'
    media = dict(id='P0001', kind='image', version=1, sha256=sha, availability='available',
                 original_path=original, archive_path=path, size=len(image))
    slide = dict(id='P0001', version=1, path=original, sha256=sha, mime_type='image/png',
                 start=0, end=1, width=2, height=3)
    snapshot = archive_room(d.classroom, [media], [ArchiveAsset(path, len(image), sha, lambda: io.BytesIO(image))], [slide])
    segment = snapshot.session['segments'][0]
    snapshot.session['review'] = dict(schema_version=1,cards=[dict(id='card01',
        evidence=dict(schema_version=1,source_session_id=d.classroom.id.hex,segments=[segment],slides=[],
                      source_ids=['S0001']),
        ai_content=dict(title='Synthetic review',front='Question',back='Answer',source_ids=['S0001']),
        user_draft=dict(revision=1,content='<script>synthetic</script>',personal_note='Source kept'))])
    package = io.BytesIO()
    write_aggregate(package, 'class_session', [snapshot], heartbeat=lambda: None)
    return package.getvalue(), sha


def enqueue_import(d, client, data):
    preview = upload(d, client, data, 'application/zip')
    key = uuid4()
    body = dict(preview_id=preview['id'], archive_sha256=preview['archive_sha256'],
                confirm_independent_copy=True, allow_missing=False)
    task = response(client.post(BASE+'/imports/commit', json=body, headers=write_headers(d,key)), 202)
    assert task['state'] == 'queued'
    return UUID(task['id']), body, key


def state(client, task):
    return response(client.get(f'{BASE}/tasks/{task}'), 200)


def test_commit_crash_rolls_back_catalog_and_ack_loss_replays(d, enabled, scheduler, monkeypatch):
    jobs, client = enabled
    data, image_sha = pack(d)
    task, body, key = enqueue_import(d, client, data)
    previous = ImportCopy.publish

    def abort(*args, **kwargs):
        previous(*args, **kwargs)
        raise RuntimeError('synthetic lost DB commit after staged object write')

    with monkeypatch.context() as faults:
        faults.setattr(ImportCopy, 'publish', abort)
        with pytest.raises(RuntimeError, match='lost DB commit'):
            PortableTaskWorker(jobs, scheduler, worker_id='first').run_once()
    assert state(client, task)['state'] == 'running'
    assert one(d, 'SELECT count(*) FROM class_sessions WHERE owner_id=:owner', owner=d.uid)['count'] == 1
    assert not rows(d, 'SELECT * FROM portable_imported_classrooms WHERE task_id=:id', id=task)
    staged = one(d, 'SELECT * FROM portable_import_assets WHERE task_id=:id', id=task)
    assert staged['published_at'] is None and len(versions(d, staged['object_key'])) == 1
    modify(d, "UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id", id=task)
    assert PortableTaskWorker(jobs, scheduler, worker_id='replacement').run_once()
    final = state(client, task)
    assert final['state'] == 'succeeded' and final['may_have_charged'] is False
    assert final['provider_outcome'] == 'not_submitted' and final['result_id'] != str(d.classroom.id)
    assert response(client.post(BASE+'/imports/commit', json=body, headers=write_headers(d,key)), 202) == final
    assert client.post(BASE+'/imports/commit', json=body|{'allow_missing':True}, headers=write_headers(d,key)).status_code == 409
    assert one(d, 'SELECT count(*) FROM portable_imported_classrooms WHERE task_id=:id', id=task)['count'] == 1
    lineage = one(d, 'SELECT * FROM portable_imported_classrooms WHERE task_id=:id', id=task)
    assert lineage['original_id'] == d.classroom.id.hex and lineage['id_map']['classroom'][d.classroom.id.hex] == final['result_id']
    assert lineage['id_map']['segments']['S0001']['original_version'] == 0
    assert lineage['snapshot']['session']['review']['cards'][0]['evidence']['source_ids'] == ['S0001']
    note = UUID(lineage['id_map']['review']['cards:card01']['id'])
    assert '<script>' not in one(d, 'SELECT body_markdown FROM review_revisions WHERE document_id=:id',id=note)['body_markdown']
    with d.pg.db.transaction(OwnerContext(new_user(d).uid)) as other:
        assert PortableLifecycle(other).rows('SELECT * FROM portable_imported_classrooms').all() == []
    assert one(d, 'SELECT sha256 FROM media_assets WHERE id=:id', id=staged['id'])['sha256'] == image_sha
    assert row_states(d, task) == ['queued','running','queued','running','succeeded']
    attempt = one(d,'SELECT * FROM task_attempts WHERE task_id=:id',id=task)
    assert attempt['provider'] is None and attempt['submitted_at'] is None and attempt['raw_output_ref'] is None
    assert one(d,'SELECT count(*) FROM provider_results WHERE attempt_id=:id',id=attempt['id'])['count'] == 0
    delete(d,'class_session',UUID(final['result_id']))
    with d.pg.db.transaction(OwnerContext(d.uid)) as tx:
        assert tx.catalog.get_class_session(d.classroom.id).deleted_at is None
        assert PortableLifecycle(tx).rows('SELECT * FROM portable_imported_classrooms').all() == []


def row_states(d, task):
    return [item['payload']['state'] for item in rows(d,
        'SELECT payload FROM outbox_events WHERE aggregate_id=:id ORDER BY event_sequence', id=task)]


def test_http_export_range_pins_version_and_rechecks_raw_lineage(d, enabled, scheduler):
    jobs, client = enabled
    with d.pg.db.transaction(OwnerContext(d.uid)) as tx:
        source = PgQaRepository(tx).add_source(d.classroom.id, kind='transcript_segment',
            object_id=uuid4(), text='synthetic export source', start_ms=0, end_ms=100)
    body = dict(scope='class_session', scope_id=str(d.classroom.id), format='zip',
                include_audio=False, confirmed_audio_privacy=False, allow_missing=False)
    key = uuid4()
    t = response(client.post(BASE+'/exports', json=body, headers=write_headers(d,key)), 202)
    assert t['state'] == 'queued' and t['result_id'] is None
    def settings(endpoint,bucket):
        return dict(endpoint=endpoint,bucket=bucket,region=REGION,access_key=ACCESS,secret_key=SECRET)
    runner, cleanup = portable_worker(d.pg.db,scheduler,'export',dict(spool_directory=str(d.spool),
        deletion_queue_url=d.deploy.worker_url.render_as_string(hide_password=False),
        media=settings(d.deploy.services.media.endpoint,d.deploy.media_bucket),
        ledger=settings(d.deploy.services.ledger.endpoint,d.deploy.ledger_bucket)))
    try:
        assert runner.run_once()
    finally:
        cleanup.dispose()
    final = state(client, t['id'])
    assert final['state'] == 'succeeded', final
    assert row_states(d, t['id']) == ['queued','running','succeeded']
    assert response(client.post(BASE+'/exports', json=body, headers=write_headers(d,key)), 202) == final
    path = f'{BASE}/media/{final["result_id"]}/access'
    issued = client.post(path, json={}, headers=write_headers(d))
    download = response(issued, 200)['download_path']
    pinned = issued.headers['x-asset-version']
    first = client.get(download, headers={'range':'bytes=0-3'})
    assert first.status_code == 206 and first.content == b'PK\x03\x04'
    assert first.headers['content-range'] == 'bytes 0-3/' + issued.headers['x-archive-length']
    wrong = client.post(path, json={}, headers=write_headers(d)|{'x-asset-version':'0'*64})
    assert wrong.status_code == 409
    renewal = client.post(path, json={}, headers=write_headers(d)|{'x-asset-version':pinned})
    assert renewal.headers['x-asset-version'] == pinned
    next_path = response(renewal, 200)['download_path']
    assert client.get(next_path,headers={'range':'bytes=4-10'}).status_code == 206
    with d.pg.db.transaction(OwnerContext(d.uid)) as tx:
        PgQaRepository(tx).revoke_source(source.source_id)
    assert client.get(next_path,headers={'range':'bytes=11-20'}).status_code == 410
    assert client.post(path,json={},headers=write_headers(d)|{'x-asset-version':pinned}).status_code == 410


def test_maintenance_alerts_cleanup_and_no_partial_copy_on_cancel(d, enabled, scheduler, monkeypatch, caplog):
    jobs, client = enabled
    task, _, _ = enqueue_import(d, client, pack(d)[0])
    def abort(*args, **kwargs):
        raise RuntimeError('synthetic crash before publication')
    with monkeypatch.context() as faults:
        faults.setattr(ImportCopy,'publish',abort)
        with pytest.raises(RuntimeError, match='crash before'):
            PortableTaskWorker(jobs, scheduler, worker_id='stopped').run_once()
    cancelled = response(client.post(f'{BASE}/tasks/{task}/cancel',json={},headers=write_headers(d)),200)
    assert cancelled['state'] == 'cancelled'
    staged = one(d,'SELECT * FROM portable_import_assets WHERE task_id=:id',id=task)
    assert versions(d,staged['object_key'])
    modify(d, "UPDATE portable_transfers SET access_until=clock_timestamp()-interval '73 hours' WHERE id=:id",
           id=staged['transfer_id'])
    maintainer = PortableMaintenance(jobs.transfers,d.worker)
    assert maintainer.tick()['incidents'] == 0
    assert 'erasure incident' in caplog.text and 'erasure overdue' in caplog.text
    assert not versions(d,staged['object_key'])
    assert one(d,'SELECT erased_at FROM portable_import_assets WHERE id=:id',id=staged['id'])['erased_at']
    assert not rows(d,'SELECT * FROM portable_imported_classrooms WHERE task_id=:id',id=task)
    assert one(d,'SELECT count(*) FROM class_sessions WHERE owner_id=:owner',owner=d.uid)['count'] == 1
