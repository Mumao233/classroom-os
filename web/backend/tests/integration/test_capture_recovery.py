"""PR-05b normal HTTP and service paths on real PostgreSQL and private object storage."""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
import secrets
import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa

from deletion_support import auth, pg, services, deploy, d, new_user, as_user, versions, classroom
from test_authentication import BASE, login
from test_deletion_lifecycle import delete
from classroom_web.application.capture import CaptureService, CaptureRecordingAuthorizer
from classroom_web.domain.auth import AuthRejected, BrowserContext
from classroom_web.entrypoints.http import create_app
from classroom_web.infrastructure import capture_repository

CONTENT = bytes(range(256)) * 125


@pytest.fixture
def capture(d):
    with d.pg.admin.begin() as connection:
        connection.execute(sa.text("""INSERT INTO retention_policies(version,media_kind,max_lifetime,approval_reference,synthetic)
            VALUES('synthetic-pr05b','audio_chunk',interval '1 hour','isolated VMware fixture only',true)
            ON CONFLICT(version) DO NOTHING"""))
        connection.execute(sa.text("""UPDATE capture_policy SET enabled=true,lease_seconds=30,recovery_seconds=30,
            max_epoch_samples=57600000,max_chunks=50000,microphone_consent_version='synthetic-v1',
            recovery_consent_version='synthetic-v1',asr_consent_version='synthetic-v1',retention_policy_version='synthetic-pr05b'"""))
    service = CaptureService(auth=d.a.service, database=d.pg.db, store=d.store, deletion=d.deletion, enabled=True)
    contract = json.loads((Path(__file__).resolve().parents[4] / 'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf8'))
    d.pg.db.cursor_secret = secrets.token_bytes(32)
    app = create_app(auth=d.a.service, contract=contract, origin='https://testserver', capture=service,
                     deletion=d.deletion, websocket_admission=d.a.ws_admission)
    with TestClient(app, base_url='https://testserver') as client:
        client.cookies.update(d.client.cookies)
        d.client = d.a.client = client
        d.capture = service
        yield d
    with d.pg.admin.begin() as connection:
        connection.execute(sa.text('UPDATE capture_policy SET enabled=false'))


def headers(m, key=None):
    return {'Origin':'https://testserver', 'Sec-Fetch-Site':'same-origin', 'X-CSRF-Token':m.ctx.csrf,
            'Idempotency-Key':key or str(uuid4())}


def acquire_body(m, **changes):
    body = dict(device_id=m.session['device_id'], microphone_consent_version='synthetic-v1',
        recovery_consent_version='synthetic-v1', asr_consent_version='synthetic-v1', confirm_takeover=False)
    return {**body, **changes}


def acquire(m, key=None, **changes):
    response = m.client.post(BASE+f'/class-sessions/{m.classroom.id}/capture/acquire',
                             json=acquire_body(m, **changes), headers=headers(m, key))
    assert response.status_code == 201, (response.text, list(m.client.app.state.errors))
    return response.json()


def descriptor(lease, seq=0, content=CONTENT):
    return dict(capture_epoch_id=lease['capture_epoch_id'], fence_token=lease['fence_token'], seq=seq,
        start_sample=seq*16000, end_sample=seq*16000+len(content)//2, byte_length=len(content), sha256=hashlib.sha256(content).hexdigest())


def prepare(m, lease, seq=0, content=CONTENT, token=None):
    response = m.client.post(BASE+f'/class-sessions/{m.classroom.id}/audio-chunks',
        json=descriptor(lease, seq, content), headers={**headers(m), **({'X-Recovery-Upload-Token':token} if token else {})})
    assert response.status_code == 201, (response.text, list(m.client.app.state.errors))
    return response.json()


def put(m, chunk, content=CONTENT, key=None):
    digest = base64.b64encode(hashlib.sha256(content).digest()).decode()
    return m.client.put(BASE+f'/audio-chunks/{chunk["id"]}/content', content=content,
        headers={**headers(m, key), 'Content-Type':'application/octet-stream', 'Content-Digest':f'sha-256=:{digest}:'})


def command_body(lease, sample=16000, seq=0, **extra):
    return dict(capture_epoch_id=lease['capture_epoch_id'], fence_token=lease['fence_token'],
                last_local_seq=seq, last_sample=sample, client_monotonic_ms=sample//16, **extra)


def command(m, lease, action, sample=16000, seq=0, key=None, **extra):
    return m.client.post(BASE+f'/class-sessions/{m.classroom.id}/capture/{action}',
                        json=command_body(lease, sample, seq, **extra), headers=headers(m, key))


def read_manifests(m, query=(), *, single=False, room=None):
    path = 'audio-manifest' if single else 'audio-manifests'
    response = m.client.get(BASE + f'/class-sessions/{room or m.classroom.id}/{path}', params=query)
    assert response.headers['cache-control'] == 'no-store'
    return response


def assert_manifest_error(response, status, code):
    assert response.status_code == status, response.text
    assert set(response.json()) == {'error'}
    assert response.json()['error']['code'] == code


def test_manifest_queries_and_empty_classroom(capture):
    m = capture
    page = read_manifests(m)
    assert page.status_code == 200
    assert page.json() == {'items': [], 'next_cursor': None}
    lease = acquire(m)
    epoch = ('capture_epoch_id', lease['capture_epoch_id'])
    for query in ([], [epoch, epoch], [('capture_epoch_id', 'invalid')], [epoch, ('limit', '1')]):
        assert_manifest_error(read_manifests(m, query, single=True), 422, 'VALIDATION_FAILED')
    single = read_manifests(m, [('capture_epoch_id', epoch[1].upper())], single=True)
    assert single.status_code == 200 and single.json()['capture_epoch_id'] == epoch[1]
    assert read_manifests(m, {'limit': 100}).json() == {'items': [single.json()], 'next_cursor': None}
    for query in ([epoch], [('limit', '1'), ('limit', '1')], [('cursor', 'a'), ('cursor', 'b')],
                  [('limit', '0')], [('limit', '101')], [('limit', '-1')], [('limit', '1.5')],
                  [('limit', '')], [('cursor', '')], [('cursor', 'x' * 513)], [('cursor', 'invalid')]):
        assert_manifest_error(read_manifests(m, query), 422, 'VALIDATION_FAILED')


def test_resumed_manifests_keep_retired_gap_and_complete_ended_epoch(capture):
    m = capture
    old = acquire(m)
    for seq in (0, 2):
        assert put(m, prepare(m, old, seq)).status_code == 200
    assert command(m, old, 'pause', sample=48000, seq=2).status_code == 200
    paused, = read_manifests(m).json()['items']
    assert paused['capture_epoch_id'] == old['capture_epoch_id']
    resumed = command(m, old, 'resume', sample=48000, seq=2)
    assert resumed.status_code == 200
    active = resumed.json()
    retired, recording = read_manifests(m).json()['items']
    assert recording['capture_epoch_id'] == active['capture_epoch_id'] and not recording['sealed']
    assert retired['capture_epoch_id'] == old['capture_epoch_id'] and not retired['sealed']
    assert retired['missing_ranges'] == [{'first_seq': 1, 'last_seq': 1}]
    assert put(m, prepare(m, active)).status_code == 200
    stopped = command(m, active, 'stop', manifest_sha256='1'*64, known_gaps=[])
    assert stopped.status_code == 200
    ended = stopped.json()
    assert ended['sealed'] and ended['missing_ranges'] == []
    assert ended['durable_ranges'] == [{'first_seq': 0, 'last_seq': 0}]
    page = read_manifests(m).json()
    assert page == {'items': [retired, ended], 'next_cursor': None}
    for item in page['items']:
        assert read_manifests(m, {'capture_epoch_id': item['capture_epoch_id']}, single=True).json() == item


def test_manifest_default_page_size(capture):
    m = capture
    lease = acquire(m)
    epochs = [lease['capture_epoch_id']]
    for _ in range(20):
        lease = acquire(m, confirm_takeover=True, expected_lease_id=lease['id'])
        epochs.append(lease['capture_epoch_id'])
    first = read_manifests(m).json()
    assert [item['capture_epoch_id'] for item in first['items']] == epochs[:20]
    last = read_manifests(m, {'cursor': first['next_cursor']}).json()
    assert [item['capture_epoch_id'] for item in last['items']] == epochs[20:]
    assert last['next_cursor'] is None


def test_manifest_cursor_freezes_membership_and_reads_current_contents(capture):
    m = capture
    first = acquire(m)
    second = acquire(m, confirm_takeover=True, expected_lease_id=first['id'])
    third = acquire(m, confirm_takeover=True, expected_lease_id=second['id'])
    assert [epoch['timeline_offset_ms'] for epoch in (first, second, third)] == [0, 0, 0]
    page = read_manifests(m, {'limit': 1}).json()
    assert [item['capture_epoch_id'] for item in page['items']] == [first['capture_epoch_id']]
    cursor = page['next_cursor']
    assert 1 <= len(cursor) <= 512
    assert put(m, prepare(m, third)).status_code == 200
    writer = m.ctx
    takeover = acquire_body(m, confirm_takeover=True, expected_lease_id=third['id'])
    as_user(m, m.a.email)
    assert m.session['device_id'] != takeover['device_id']
    with ThreadPoolExecutor(max_workers=1) as pool:
        new_epoch = pool.submit(m.capture.acquire, writer, str(m.classroom.id), str(uuid4()), takeover)
        middle = read_manifests(m, {'limit': 1, 'cursor': cursor}).json()
        fourth = new_epoch.result()
    assert [item['capture_epoch_id'] for item in middle['items']] == [second['capture_epoch_id']]
    last = read_manifests(m, {'cursor': middle['next_cursor']}).json()
    final_item, = last['items']
    assert final_item['capture_epoch_id'] == third['capture_epoch_id'] and last['next_cursor'] is None
    assert final_item['durable_ranges'] == [{'first_seq': 0, 'last_seq': 0}]
    assert read_manifests(m, {'cursor': cursor}).json() == {'items': middle['items'] + [final_item], 'next_cursor': None}
    fresh = read_manifests(m).json()
    assert [item['capture_epoch_id'] for item in fresh['items']] == [
        epoch['capture_epoch_id'] for epoch in (first, second, third, fourth)]


def test_manifest_cursor_scope_tampering_and_expiry(capture, monkeypatch):
    m = capture
    first = acquire(m)
    acquire(m, confirm_takeover=True, expected_lease_id=first['id'])
    cursor = read_manifests(m, {'limit': 1}).json()['next_cursor']
    altered = cursor[:-1] + ('1' if cursor[-1] == '0' else '0')
    assert_manifest_error(read_manifests(m, {'cursor': altered}), 422, 'VALIDATION_FAILED')
    assert_manifest_error(read_manifests(m, {'cursor': cursor}, room=classroom(m).id), 422, 'VALIDATION_FAILED')
    # A signed event cursor for this same user also belongs to a different listing.
    with m.pg.db.authorized_transaction(m.a.service.principal(m.ctx)) as tx:
        event_cursor = tx.events.watermark()
    assert_manifest_error(read_manifests(m, {'cursor': event_cursor}), 422, 'VALIDATION_FAILED')
    monkeypatch.setattr(capture_repository, 'MANIFEST_CURSOR_LIFETIME', timedelta(0))
    expired = read_manifests(m, {'limit': 1}).json()['next_cursor']
    assert_manifest_error(read_manifests(m, {'cursor': expired}), 409, 'CURSOR_EXPIRED')
    as_user(m, new_user(m).email)
    assert_manifest_error(read_manifests(m, {'cursor': cursor}), 422, 'VALIDATION_FAILED')
    assert_manifest_error(read_manifests(m, {'cursor': expired}), 422, 'VALIDATION_FAILED')
    assert_manifest_error(read_manifests(m), 404, 'NOT_FOUND')


@pytest.mark.parametrize('denial,status,code', [
    ('consent', 403, 'CONSENT_REQUIRED'), ('expiry', 410, 'GONE'), ('audio', 410, 'GONE'),
    ('class_session', 410, 'GONE'),
])
def test_manifest_continuation_rechecks_access_and_fails_whole_page(capture, denial, status, code):
    m = capture
    first = acquire(m)
    assert put(m, prepare(m, first)).status_code == 200
    second = acquire(m, confirm_takeover=True, expected_lease_id=first['id'])
    if denial == 'expiry':
        with m.pg.admin.begin() as c:
            c.execute(sa.text("""INSERT INTO retention_policies(version,media_kind,max_lifetime,approval_reference,synthetic)
                VALUES('synthetic-manifest-short','audio_chunk',interval '5 seconds','VMware expiry fixture',true)
                ON CONFLICT(version) DO NOTHING"""))
            c.execute(sa.text("UPDATE capture_policy SET retention_policy_version='synthetic-manifest-short'"))
    third = acquire(m, confirm_takeover=True, expected_lease_id=second['id'])
    cursor = read_manifests(m, {'limit': 1}).json()['next_cursor']
    target = {'capture_epoch_id': third['capture_epoch_id']}
    if denial == 'consent':
        with m.pg.admin.begin() as c:
            c.execute(sa.text("""UPDATE consent_records SET revoked_at=clock_timestamp()
                WHERE id=(SELECT asr_consent_id FROM capture_epoch_state WHERE epoch_id=:capture_epoch_id)"""), target)
    elif denial == 'expiry':
        with m.pg.admin.connect() as c:
            remaining = c.scalar(sa.text("""SELECT extract(epoch FROM hard_expire_at-clock_timestamp())
                FROM capture_epoch_state WHERE epoch_id=:capture_epoch_id"""), target)
        time.sleep(max(0, float(remaining)) + 0.05)
    else:
        delete(m, denial, m.classroom.id)
    assert_manifest_error(read_manifests(m, {'cursor': cursor}), status, code)
    assert_manifest_error(read_manifests(m, target, single=True), status, code)


def test_acquire_idempotency_and_parallel_owner_lease(capture):
    m=capture;key=str(uuid4());first=acquire(m,key)
    assert acquire(m,key)==first
    with ThreadPoolExecutor(max_workers=2) as pool:
        def contender(_):
            try:
                return m.capture.acquire(m.ctx,str(m.classroom.id),str(uuid4()),acquire_body(m))
            except AuthRejected as error:
                return error.code
        assert list(pool.map(contender,range(2)))==['LEASE_CONFLICT','LEASE_CONFLICT']
    with m.pg.admin.connect() as c:
        assert c.scalar(sa.text('SELECT count(*) FROM capture_leases WHERE owner_id=:id'),{'id':m.uid})==1


def test_independent_device_discovers_lease_and_explicit_cas_takeover(capture):
    m=capture;old=acquire(m);old_context=m.ctx;old_device=m.session['device_id']
    path=BASE+f'/class-sessions/{m.classroom.id}/capture/acquire'
    m.client.cookies.clear();m.session=login(m.a)
    m.ctx=BrowserContext(session=m.client.cookies.get('__Host-classroom_session'),csrf=m.session['csrf_token'])
    assert m.session['device_id']!=old_device
    conflict=m.client.post(path,json=acquire_body(m),headers=headers(m))
    assert conflict.status_code==409
    target=conflict.json()['error']['conflict_id'];assert target==old['id']
    wrong=m.client.post(path,json=acquire_body(m,device_id=str(uuid4())),headers=headers(m))
    assert wrong.status_code==403 and wrong.json()['error']['conflict_id'] is None
    stale=m.client.post(path,json=acquire_body(m,confirm_takeover=True,expected_lease_id=str(uuid4())),headers=headers(m))
    assert stale.status_code==409 and stale.json()['error']['conflict_id']==target
    taken=acquire(m,confirm_takeover=True,expected_lease_id=target)
    assert taken['fence_token']==old['fence_token']+1
    with TestClient(m.client.app,base_url='https://testserver') as original:
        original.cookies.set('__Host-classroom_session',old_context.session)
        rejected=original.post(BASE+f'/class-sessions/{m.classroom.id}/capture/renew',json=command_body(old),
            headers={**headers(m),'X-CSRF-Token':old_context.csrf})
        assert rejected.status_code==409 and rejected.json()['error']['code']=='STALE_FENCE'
    superseded=m.client.post(path,json=acquire_body(m,confirm_takeover=True,expected_lease_id=target),headers=headers(m))
    assert superseded.status_code==409 and superseded.json()['error']['conflict_id']==taken['id']
    # Both contenders observed this same lease. Only one may consume that CAS target.
    def contend(_):
        try:return m.capture.acquire(m.ctx,str(m.classroom.id),str(uuid4()),acquire_body(m,confirm_takeover=True,expected_lease_id=taken['id']))
        except AuthRejected as error:return error
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(contend,range(2)))
    successful=[result for result in results if isinstance(result,dict)]
    refused=[result for result in results if isinstance(result,AuthRejected)]
    assert len(successful)==len(refused)==1 and refused[0].conflict_id==successful[0]['id']
    m.client.cookies.clear()
    anonymous=m.client.post(path,json=acquire_body(m),headers=headers(m))
    assert anonymous.status_code in (401,403) and anonymous.json()['error']['conflict_id'] is None


def test_tail_chunk_durable_ack_retry_outbox_and_source(capture):
    m=capture;lease=acquire(m);tail=CONTENT[:514];pending=prepare(m,lease,content=tail)
    assert pending['state']=='pending' and pending['durable_at'] is None
    key=str(uuid4());done=put(m,pending,tail,key)
    assert done.status_code==200,(done.text,list(m.client.app.state.errors))
    assert done.json()['state']=='durable' and done.json()['receipt_id']==pending['receipt_id']
    assert put(m,pending,tail,key).json()==done.json()
    assert prepare(m,lease,content=tail)==done.json()
    source=m.capture.durable_source(m.ctx,pending['id'])
    assert source.end_sample==257 and source.ingest_mode=='live'
    with m.pg.admin.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM outbox_events WHERE owner_id=:id AND event_type='audio.chunk_durable'"),{'id':m.uid})==1
    manifest=m.capture.manifest(m.ctx,str(m.classroom.id),lease['capture_epoch_id'])
    assert manifest['durable_ranges']==[dict(first_seq=0,last_seq=0)]


def test_descriptor_hash_conflict_and_out_of_order_manifest(capture):
    m=capture;lease=acquire(m)
    second=prepare(m,lease,1);assert put(m,second).status_code==200
    manifest=m.capture.manifest(m.ctx,str(m.classroom.id),lease['capture_epoch_id'])
    assert manifest['missing_ranges']==[dict(first_seq=0,last_seq=0)]
    first=prepare(m,lease,0);assert put(m,first).status_code==200
    body=descriptor(lease);body['sha256']='0'*64
    response=m.client.post(BASE+f'/class-sessions/{m.classroom.id}/audio-chunks',json=body,headers=headers(m))
    assert response.status_code==409 and response.json()['error']['code']=='IDEMPOTENCY_MISMATCH'
    assert m.capture.manifest(m.ctx,str(m.classroom.id),lease['capture_epoch_id'])['durable_ranges']==[dict(first_seq=0,last_seq=1)]


def test_pause_resume_stop_seal_and_monotonic_commands(capture):
    m=capture;lease=acquire(m);pending=prepare(m,lease);assert put(m,pending).status_code==200
    assert command(m,lease,'pause').status_code==200
    response=m.client.post(BASE+f'/class-sessions/{m.classroom.id}/audio-chunks',json=descriptor(lease,1),headers=headers(m))
    assert response.status_code==409
    resumed=command(m,lease,'resume')
    assert resumed.status_code==200 and resumed.json()['capture_epoch_id']!=lease['capture_epoch_id']
    assert command(m,lease,'renew').status_code==409
    lease=resumed.json()
    assert command(m,lease,'renew',sample=16000).status_code==200
    assert command(m,lease,'renew',sample=1).status_code==422
    key=str(uuid4());extra=dict(manifest_sha256='1'*64,known_gaps=[])
    stopped=command(m,lease,'stop',key=key,**extra)
    assert stopped.status_code==200 and stopped.json()['sealed']
    assert command(m,lease,'stop',key=key,**extra).json()==stopped.json()
    assert command(m,lease,'renew').status_code==409


def test_takeover_fences_old_device_but_explicit_recovery_upload_is_bounded(capture):
    m=capture;old=acquire(m);pending=prepare(m,old)
    new=acquire(m,confirm_takeover=True,expected_lease_id=old['id'])
    assert new['fence_token']==old['fence_token']+1 and new['capture_epoch_id']!=old['capture_epoch_id']
    assert put(m,pending).status_code==409
    body=dict(capture_epoch_id=old['capture_epoch_id'],manifest_sha256='a'*64,known_last_seq=0,
              known_last_sample=16000,confirm_retired_epoch=True)
    response=m.client.post(BASE+f'/class-sessions/{m.classroom.id}/capture/recovery-authorize',json=body,headers=headers(m))
    assert response.status_code==200,(response.text,list(m.client.app.state.errors))
    grant=response.json();assert grant['asr_authorized'] is False
    recovered=prepare(m,old,token=grant['recovery_upload_token'])
    assert recovered['id']==pending['id'] and put(m,recovered).status_code==200
    assert m.capture.durable_source(m.ctx,recovered['id']).ingest_mode=='recovery'
    bad=m.client.post(BASE+f'/class-sessions/{m.classroom.id}/audio-chunks',json=descriptor(old,1),
                     headers={**headers(m),'X-Recovery-Upload-Token':grant['recovery_upload_token']})
    assert bad.status_code==409
    with m.pg.admin.connect() as c:
        receipts=c.execute(sa.text("SELECT response_body FROM operation_receipts WHERE owner_id=:id AND operation='authorizeRecovery'"),{'id':m.uid}).scalars().all()
        assert grant['recovery_upload_token'] not in json.dumps(receipts)


def test_object_success_database_rollback_survives_service_restart(capture):
    m=capture;lease=acquire(m);pending=prepare(m,lease)
    original=m.pg.db.authorized_transaction
    @contextmanager
    def failing(*args,**kwargs):
        with original(*args,**kwargs) as tx:
            yield tx
            # Only the put transaction has written the asset and event.
            if tx.connection.scalar(sa.text("SELECT count(*) FROM outbox_events WHERE owner_id=:id AND event_type='audio.chunk_durable'"),{'id':m.uid}):
                raise RuntimeError('synthetic commit failure')
    m.pg.db.authorized_transaction=failing
    try:
        with pytest.raises(RuntimeError):m.capture.put(m.ctx,pending['id'],str(uuid4()),CONTENT)
    finally:
        m.pg.db.authorized_transaction=original
    restarted=CaptureService(auth=m.a.service,database=m.pg.db,store=m.store,deletion=m.deletion,enabled=True)
    result=restarted.put(m.ctx,pending['id'],str(uuid4()),CONTENT)
    assert result['state']=='durable'
    with m.pg.admin.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM outbox_events WHERE owner_id=:id AND event_type='audio.chunk_durable'"),{'id':m.uid})==1


def test_deletion_restore_gate_and_audio_revocation_block_all_paths(capture):
    m=capture;lease=acquire(m);pending=prepare(m,lease);assert put(m,pending).status_code==200
    m.deletion.ready=False
    with pytest.raises(AuthRejected) as error:m.capture.manifest(m.ctx,str(m.classroom.id),lease['capture_epoch_id'])
    assert error.value.code=='SERVICE_DISABLED'
    m.deletion.ready=True
    delete(m,'audio',m.classroom.id)
    assert put(m,pending).status_code==410
    with pytest.raises(AuthRejected) as error:m.capture.acquire(m.ctx,str(m.classroom.id),str(uuid4()),acquire_body(m,confirm_takeover=True,expected_lease_id=lease['id']))
    assert error.value.code=='GONE'
    with pytest.raises(AuthRejected) as error:m.capture.manifest(m.ctx,str(m.classroom.id),lease['capture_epoch_id'])
    assert error.value.code=='GONE'


def test_consent_revocation_blocks_recording_key_and_pending_put(capture):
    m=capture;lease=acquire(m);pending=prepare(m,lease)
    authorizer=CaptureRecordingAuthorizer(m.capture);principal=m.a.service.principal(m.ctx)
    with m.pg.db.authorized_transaction(principal) as tx:
        authorizer.authorize(tx.connection,principal,m.classroom.id)
    with m.pg.admin.begin() as c:
        c.execute(sa.text("UPDATE consent_records SET revoked_at=clock_timestamp() WHERE owner_id=:id AND kind='recovery'"),{'id':m.uid})
    assert put(m,pending).status_code==403
    with m.pg.db.authorized_transaction(principal) as tx:
        with pytest.raises(AuthRejected) as error:authorizer.authorize(tx.connection,principal,m.classroom.id)
        assert error.value.code=='CONSENT_REQUIRED'


def test_session_and_device_binding_policy_permissions_and_rls(capture):
    m=capture;lease=acquire(m);pending=prepare(m,lease)
    with m.pg.db.engine.begin() as c:
        with pytest.raises(sa.exc.DBAPIError):c.execute(sa.text('UPDATE capture_policy SET enabled=true'))
    with m.pg.db.engine.connect() as c:
        assert c.scalar(sa.text('SELECT count(*) FROM capture_epochs'))==0
    m.client.cookies.clear();new_session=login(m.a)
    m.ctx=BrowserContext(session=m.client.cookies.get('__Host-classroom_session'),csrf=new_session['csrf_token'])
    assert put(m,pending).status_code==409


def test_default_application_and_database_audio_gates(capture):
    m=capture
    closed=CaptureService(auth=m.a.service,database=m.pg.db,store=m.store,deletion=m.deletion)
    with pytest.raises(AuthRejected) as error:closed.acquire(m.ctx,str(m.classroom.id),str(uuid4()),acquire_body(m))
    assert error.value.code=='SERVICE_DISABLED'
    with m.pg.admin.begin() as c:c.execute(sa.text('UPDATE capture_policy SET enabled=false'))
    response=m.client.post(BASE+f'/class-sessions/{m.classroom.id}/capture/acquire',json=acquire_body(m),headers=headers(m))
    assert response.status_code==503


def test_matching_descriptor_still_binds_each_operation_key(capture):
    m=capture;lease=acquire(m);first=prepare(m,lease);key=str(uuid4())
    path=BASE+f'/class-sessions/{m.classroom.id}/audio-chunks'
    assert m.client.post(path,json=descriptor(lease),headers=headers(m,key)).json()==first
    rejected=m.client.post(path,json=descriptor(lease,1),headers=headers(m,key))
    assert rejected.status_code==409 and rejected.json()['error']['code']=='IDEMPOTENCY_MISMATCH'


def test_object_rollback_without_retry_is_cleaned_by_real_04b_expiry(capture):
    m=capture;lease=acquire(m);pending=prepare(m,lease)
    original=m.pg.db.authorized_transaction
    @contextmanager
    def failing(*args,**kwargs):
        with original(*args,**kwargs) as tx:
            yield tx
            if tx.connection.scalar(sa.text("SELECT count(*) FROM outbox_events WHERE owner_id=:id AND event_type='audio.chunk_durable'"),{'id':m.uid}):
                raise RuntimeError('synthetic commit failure')
    m.pg.db.authorized_transaction=failing
    try:
        with pytest.raises(RuntimeError):m.capture.put(m.ctx,pending['id'],str(uuid4()),CONTENT)
    finally:m.pg.db.authorized_transaction=original
    with m.pg.admin.begin() as c:
        asset=c.execute(sa.text('SELECT a.* FROM media_assets a JOIN audio_chunks c ON c.asset_id=a.id WHERE c.id=:id'),{'id':pending['id']}).mappings().one()
        assert asset['expires_at'] is not None and asset['hard_expire_at'] is not None
        assert asset['status']=='staging' and versions(m,asset['object_key'])
        c.execute(sa.text("UPDATE media_assets SET expires_at=clock_timestamp()+interval '1 second' WHERE id=:id"),{'id':asset['id']})
    time.sleep(1.2)
    assert m.deletion.expire_due()>=1
    m.worker.drain()
    assert versions(m,asset['object_key'])==[]
    assert put(m,pending).status_code==410


def test_second_owner_http_and_composite_foreign_keys(capture):
    m=capture;lease=acquire(m);pending=prepare(m,lease);other=new_user(m)
    with m.pg.admin.begin() as c:
        with pytest.raises(sa.exc.DBAPIError):
            c.execute(sa.text('UPDATE capture_epochs SET owner_id=:other WHERE id=:id'),{'other':other.uid,'id':lease['capture_epoch_id']})
    with m.pg.admin.begin() as c:
        with pytest.raises(sa.exc.DBAPIError):
            c.execute(sa.text('UPDATE audio_chunks SET owner_id=:other WHERE id=:id'),{'other':other.uid,'id':pending['id']})
    as_user(m,other.email)
    other_acquire=m.client.post(BASE+f'/class-sessions/{m.classroom.id}/capture/acquire',json=acquire_body(m),headers=headers(m))
    assert other_acquire.status_code==404 and other_acquire.json()['error']['conflict_id'] is None
    assert put(m,pending).status_code==404
    response=m.client.get(BASE+f'/class-sessions/{m.classroom.id}/audio-manifest',params={'capture_epoch_id':lease['capture_epoch_id']})
    assert response.status_code==404


def ticket(m,lease):
    response=m.client.post(BASE+'/ws-tickets',json=dict(channel='capture',class_session_id=str(m.classroom.id),
        capture_epoch_id=lease['capture_epoch_id'],fence_token=lease['fence_token']),headers=headers(m))
    assert response.status_code==201,(response.text,list(m.client.app.state.errors))
    return response.json()['ticket']


def ws_frame(kind,data,seq):
    return dict(protocol_version=1,type=kind,sent_at='2026-11-17T00:00:00Z',data=data,connection_seq=seq)


def test_capture_ws_durable_ack_matches_http_and_ticket_is_once(capture):
    from starlette.websockets import WebSocketDisconnect
    m=capture;lease=acquire(m);chunk=str(uuid4());key=str(uuid4());tail=CONTENT[:514]
    issued=ticket(m,lease)
    scope=dict(class_session_id=str(m.classroom.id),capture_epoch_id=lease['capture_epoch_id'],fence_token=lease['fence_token'])
    with m.client.websocket_connect('wss://testserver'+BASE+'/ws/capture',subprotocols=['classroom.v1'],headers={'Origin':'https://testserver'}) as ws:
        ws.send_json(dict(type='authenticate',ticket=issued))
        assert ws.receive_json()['type']=='capture.ready'
        ws.send_json(ws_frame('heartbeat',{'last_cursor':None},0))
        ws.send_json(ws_frame('capture.chunk_start',dict(**scope,chunk_id=chunk,op_id=key,
            **{k:v for k,v in descriptor(lease,content=tail).items() if k not in scope}),1))
        ws.send_json(ws_frame('capture.frame',dict(**scope,chunk_id=chunk,frame_seq=0,start_sample=0,
            sample_count=len(tail)//2,byte_length=len(tail),sha256=hashlib.sha256(tail).hexdigest(),chunk_final_frame=True),2))
        ws.send_bytes(tail)
        assert ws.receive_json()['data']['durable'] is False
        ack=ws.receive_json()
        assert ack['type']=='audio.chunk_durable' and ack['data']['chunk_id']==chunk and ack['cursor']
    http=prepare(m,lease,content=tail)
    assert http['id']==chunk and http['receipt_id']==ack['data']['receipt_id'] and http['state']=='durable'
    with pytest.raises(WebSocketDisconnect):
        with m.client.websocket_connect('wss://testserver'+BASE+'/ws/capture',subprotocols=['classroom.v1'],headers={'Origin':'https://testserver'}) as ws:
            ws.send_json(dict(type='authenticate',ticket=issued));ws.receive_json()


def test_capture_ws_rejects_bad_pair_and_old_fence(capture):
    from starlette.websockets import WebSocketDisconnect
    m=capture;lease=acquire(m)
    with m.client.websocket_connect('wss://testserver'+BASE+'/ws/capture',subprotocols=['classroom.v1'],headers={'Origin':'https://testserver'}) as ws:
        ws.send_json(dict(type='authenticate',ticket=ticket(m,lease)));assert ws.receive_json()['type']=='capture.ready'
        ws.send_bytes(b'no-header')
        with pytest.raises(WebSocketDisconnect) as error:ws.receive_json()
        assert error.value.code==4403
    with m.client.websocket_connect('wss://testserver'+BASE+'/ws/capture',subprotocols=['classroom.v1'],headers={'Origin':'https://testserver'}) as ws:
        ws.send_json(dict(type='authenticate',ticket=ticket(m,lease)));ws.receive_json()
        acquire(m,confirm_takeover=True,expected_lease_id=lease['id'])
        ws.send_bytes(b'x')
        with pytest.raises(WebSocketDisconnect) as error:ws.receive_json()
        assert error.value.code==4409


def test_native_chromium_pcm_and_real_wss_capture(capture,tmp_path):
    import os
    import shutil
    import socket
    import subprocess
    import threading
    import uvicorn
    from fastapi.responses import HTMLResponse, FileResponse
    from test_auth_browser import _certificate
    from test_authentication import PASSWORD
    m=capture;root=Path(__file__).resolve().parents[4]
    dependency=Path(os.environ['PR05A_DEPENDENCY_ROOT'])
    node=shutil.which('node') or str(dependency/'work/pr03b/vmware-broker/payload/node/node.exe')
    bundle=tmp_path/'capture-browser.js'
    build=subprocess.run([node,str(root/'tools/pr05b/build_browser.mjs'),str(dependency),str(bundle)],cwd=root,
                         capture_output=True,text=True,timeout=30)
    assert build.returncode==0,build.stderr
    cert,key,pin=_certificate(tmp_path,'capture',uuid4().hex)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    origin=f'https://127.0.0.1:{port}'
    contract=json.loads((root/'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf8'))
    app=create_app(auth=m.a.service,contract=contract,origin=origin,capture=m.capture,deletion=m.deletion,
                   websocket_admission=m.a.ws_admission)
    app.add_api_route('/',lambda:HTMLResponse('<!doctype html><title>PR05b synthetic validation</title><script src="/synthetic/capture.js"></script>'),methods=['GET'])
    app.add_api_route('/synthetic/capture.js',lambda:FileResponse(bundle,media_type='text/javascript'),methods=['GET'])
    app.add_api_route('/synthetic/pcm-worklet.js',lambda:FileResponse(root/'static/pcm-worklet.js',media_type='text/javascript'),methods=['GET'])
    def expire_synthetic_lease():
        with m.pg.admin.begin() as connection:
            connection.execute(sa.text("UPDATE capture_leases SET expires_at=clock_timestamp()-interval '1 second' WHERE owner_id=:owner"),{'owner':m.uid})
        return {'synthetic':True}
    app.add_api_route('/synthetic/expire-lease',expire_synthetic_lease,methods=['POST'])
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,ssl_certfile=str(cert),ssl_keyfile=str(key),
        proxy_headers=False,access_log=False,log_level='warning',ws='websockets-sansio',ws_max_size=8192,timeout_graceful_shutdown=3))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    try:
        until=time.monotonic()+15
        while not server.started and thread.is_alive() and time.monotonic()<until:time.sleep(.02)
        assert server.started
        config=dict(module=str(dependency/'web/frontend/node_modules/@playwright/test/index.mjs'),pin=pin,origin=origin,
            email=m.a.email,password=PASSWORD,classroom=str(m.classroom.id),course=str(m.course.id),semester=str(m.semester.id))
        run=subprocess.run([node,str(root/'tools/pr05b/browser.mjs')],input=json.dumps(config),capture_output=True,
                           text=True,encoding='utf8',timeout=90,creationflags=subprocess.CREATE_NO_WINDOW)
        output=Path(os.environ['PR05A_REVIEW_OUTPUT']);output.mkdir(parents=True,exist_ok=True)
        (output/'capture-browser-stderr.log').write_text(run.stderr,encoding='utf8')
        assert run.returncode==0,run.stderr
        result=json.loads(run.stdout.strip().splitlines()[-1]);assert result['passed']
        (output/'capture-browser.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    finally:
        server.should_exit=True;thread.join(10)
        assert not thread.is_alive()
