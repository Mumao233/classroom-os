"""PR-04a against actual PG and a private S3-compatible object service."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import io
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from test_authentication import auth, login, BASE
from test_task_migrations import previous_schema
from classroom_web.application.media import MediaService
from classroom_web.domain.auth import BrowserContext, AuthRejected
from classroom_web.domain.data import OwnerContext
from classroom_web.entrypoints.http import create_app
from classroom_web.infrastructure.object_store import S3ObjectStore

CONTENT = b'\x89PNG\r\n\x1a\n' + b'SYNTHETIC-MEDIA-ONLY' * 7000


@pytest.fixture
def media(auth, tmp_path):
    endpoint = os.environ.get('PR04A_S3_ENDPOINT')
    if not endpoint:
        pytest.fail('PR04A_S3_ENDPOINT must select the owned synthetic object service')
    store = S3ObjectStore(endpoint=endpoint,bucket='pr04a-'+uuid4().hex,region='us-east-1',
        access_key='pr04a-synthetic',secret_key='pr04a-synthetic-secret',synthetic_loopback=True)
    store.client.create_bucket(Bucket=store.bucket)
    service = MediaService(auth=auth.service,database=auth.pg.db,store=store)
    contract=json.loads((__import__('pathlib').Path(__file__).resolve().parents[4]/'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf8'))
    app=create_app(auth=auth.service,contract=contract,origin='https://testserver',media=service,media_spool_directory=tmp_path)
    with TestClient(app,base_url='https://testserver') as client:
        auth.client=client
        session=login(auth)
        ctx=BrowserContext(session=client.cookies.get('__Host-classroom_session'),csrf=session['csrf_token'])
        with auth.pg.db.transaction(OwnerContext(auth.uid)) as tx:
            semester=tx.catalog.create_semester('Synthetic media')
            course=tx.catalog.create_course(semester.id,'Synthetic course')
            classroom=tx.catalog.create_class_session(course.id,'Synthetic media session')
        yield SimpleNamespace(a=auth,service=service,store=store,client=client,ctx=ctx,classroom=classroom,course=course,session=session)


def headers(m, key=None):
    return {'Origin':'https://testserver','Sec-Fetch-Site':'same-origin','X-CSRF-Token':m.ctx.csrf,'Idempotency-Key':key or str(uuid4())}


def create(m, content=CONTENT, **changes):
    body=dict(purpose='slide',class_session_id=str(m.classroom.id),mime_type='image/png',
        byte_length=len(content),sha256=hashlib.sha256(content).hexdigest(),filename='../../SYNTHETIC.png')
    body.update(changes)
    r=m.client.post(BASE+'/media/uploads',json=body,headers=headers(m))
    assert r.status_code==201,(r.text,list(m.client.app.state.errors))
    return r.json(),body


def put(m, upload, content=CONTENT, key=None):
    return m.client.put(upload['upload_path'],content=content,headers={**headers(m,key),'Content-Type':'application/octet-stream'})


def complete(m, upload, body):
    return m.client.post(BASE+f'/media/uploads/{upload["id"]}/complete',json={k:body[k] for k in ('sha256','byte_length')},headers=headers(m))


def available(m):
    upload,body=create(m)
    r=put(m,upload)
    assert r.status_code==200,r.text
    r=complete(m,upload,body)
    assert r.status_code==200,r.text
    return upload,r.json()


def access(m, asset, key=None):
    r=m.client.post(BASE+f'/media/{asset["id"]}/access',json={},headers=headers(m,key))
    assert r.status_code==200,r.text
    return r.json()['download_path']


def test_normal_http_upload_download_ranges_and_private_bucket(media):
    m=media;upload,asset=available(m);path=access(m,asset)
    response=m.client.get(path)
    assert response.status_code==200 and response.content==CONTENT
    assert response.headers['cache-control']=='no-store'
    assert m.client.get(path,headers={'Range':'bytes=3-18'}).content==CONTENT[3:19]
    assert m.client.get(path,headers={'Range':'bytes=-11'}).content==CONTENT[-11:]
    r=m.client.get(path,headers={'Range':'bytes=999999999-'})
    assert r.status_code==416 and r.headers['content-range']==f'bytes */{len(CONTENT)}'
    with m.a.pg.admin.begin() as c:
        row=c.execute(sa.text('SELECT * FROM media_assets WHERE id=:id'),dict(id=asset['id'])).mappings().one()
    assert '..' not in row['object_key'] and 'SYNTHETIC' not in row['object_key']
    assert httpx.get(os.environ['PR04A_S3_ENDPOINT']+'/'+m.store.bucket+'/'+row['object_key']).status_code in (401,403)
    assert 'ticket=' not in json.dumps(m.client.app.state.errors,default=str)


def test_bad_hash_length_magic_and_csrf(media):
    m=media;upload,body=create(m)
    assert put(m,upload,CONTENT[:-1]).status_code==422
    assert put(m,upload,b'x'*len(CONTENT)).status_code==422
    r=m.client.put(upload['upload_path'],content=CONTENT,headers={'Content-Type':'application/octet-stream'})
    assert r.status_code==403
    fake=b'not an image'
    upload,_=create(m,fake)
    assert put(m,upload,fake).status_code==422
    assert len(list(m.store.client.list_objects_v2(Bucket=m.store.bucket).get('Contents',[])))==0


def test_object_success_database_failure_recovers_after_service_restart(media):
    m=media;upload,body=create(m)
    original=m.a.pg.db.authorized_transaction
    @contextmanager
    def fail_commit(*args,**kwargs):
        with original(*args,**kwargs) as tx:
            yield tx
            raise RuntimeError('synthetic database failure')
    m.a.pg.db.authorized_transaction=fail_commit
    try:
        with pytest.raises(RuntimeError):
            m.service.put(m.ctx,str(uuid4()),upload['id'],io.BytesIO(CONTENT))
    finally:
        m.a.pg.db.authorized_transaction=original
    with m.a.pg.admin.begin() as c:
        assert c.scalar(sa.text('SELECT state FROM media_uploads WHERE id=:id'),dict(id=upload['id']))=='staging'
        object_key=c.scalar(sa.text('SELECT object_key FROM media_assets WHERE id=:id'),dict(id=upload['asset_id']))
    first_received=m.store.inspect(object_key).received_at
    restarted=MediaService(auth=m.a.service,database=m.a.pg.db,store=m.store)
    assert restarted.recover(m.ctx)==[dict(upload_id=upload['id'],state='verified')]
    with m.a.pg.admin.begin() as c:
        assert c.scalar(sa.text('SELECT first_cloud_received_at FROM media_assets WHERE id=:id'),dict(id=upload['asset_id']))==first_received
    assert m.client.get(access(m,dict(id=upload['asset_id']))).content==CONTENT


def test_concurrent_complete_and_lost_ack_keep_single_asset(media):
    m=media;upload,body=create(m)
    assert put(m,upload).status_code==200
    verify={k:body[k] for k in ('sha256','byte_length')}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:m.service.complete(m.ctx,str(uuid4()),upload['id'],verify),range(2)))
    assert results[0]==results[1]
    assert complete(m,upload,body).json()==results[0]
    assert m.store.client.list_objects_v2(Bucket=m.store.bucket)['KeyCount']==1


def test_tickets_reissue_and_fail_after_session_revocation(media):
    m=media;_,asset=available(m);key=str(uuid4());first=access(m,asset,key);second=access(m,asset,key)
    assert first!=second
    with m.a.pg.admin.begin() as c:
        receipts=c.execute(sa.text("SELECT response_body FROM operation_receipts WHERE owner_id=:owner AND operation='issueMediaAccess'"),dict(owner=m.a.uid)).scalars().all()
    assert all(set(r)=={'asset_id'} for r in receipts)
    m.a.service.logout(m.ctx)
    assert m.client.get(first).status_code==401


def test_cross_owner_and_no_cookie_do_not_get_media(media):
    m=media;upload,asset=available(m);path=access(m,asset)
    m.client.cookies.clear()
    assert m.client.get(path).status_code==401
    from test_authentication import PASSWORD
    other=uuid4()
    with m.a.pg.admin.begin() as c:
        c.execute(sa.text("INSERT INTO users(id,normalized_email,password_hash,role,state,email_verified_at) VALUES(:id,:email,:hash,'student','active',clock_timestamp())"),dict(id=other,email=str(other)+'@example.test',hash=m.a.crypto.hash(PASSWORD)))
    m.a.email=str(other)+'@example.test';session=login(m.a)
    assert m.client.get(path).status_code==404
    r=m.client.post(BASE+f'/media/{asset["id"]}/access',json={},headers={**headers(m),'X-CSRF-Token':session['csrf_token']})
    assert r.status_code==404


def test_expired_ticket_asset_and_ancestor_revoke(media):
    m=media;_,asset=available(m);path=access(m,asset)
    with m.a.pg.admin.begin() as c:
        c.execute(sa.text("UPDATE media_tickets SET expires_at=clock_timestamp()-interval '1 second' WHERE asset_id=:id"),dict(id=asset['id']))
    assert m.client.get(path).status_code==403
    path=access(m,asset)
    with m.a.pg.db.transaction(OwnerContext(m.a.uid)) as tx:
        from classroom_web.domain.data import Resource
        tx.tombstones.revoke(Resource.COURSE,m.course.id,m.course.revision,1)
    assert m.client.get(path).status_code==410
    assert complete(m,dict(id='00000000-0000-4000-8000-000000000000'),dict(sha256='0'*64,byte_length=1)).status_code==404


def test_stream_checks_revocation_between_blocks(media):
    m=media;_,asset=available(m);path=access(m,asset);token=path.split('ticket=')[1]
    assert m.service.read_block(m.ctx,asset['id'],token,0,1023)==CONTENT[:1024]
    with m.a.pg.admin.begin() as c:
        c.execute(sa.text("UPDATE media_assets SET access_revoked_at=clock_timestamp() WHERE id=:id"),dict(id=asset['id']))
    with pytest.raises(AuthRejected) as error:
        m.service.read_block(m.ctx,asset['id'],token,1024,2047)
    assert error.value.status==410


def test_media_tables_force_rls_cross_owner_fk_and_immutable_identity(media):
    m=media;_,asset=available(m)
    from pathlib import Path
    model=json.loads((Path(__file__).resolve().parents[4]/'docs/web-alpha/architecture/contracts/domain-model.json').read_text(encoding='utf8'))
    frozen=next(table for table in model['tables'] if table['name']=='media_assets')
    columns={
        'media_assets':{column['name'] for column in frozen['columns']}|{'object_etag'},
        'media_uploads':{'id','owner_id','asset_id','created_at','expires_at','state'},
        'media_tickets':{'id','owner_id','asset_id','created_at','expires_at','session_id','security_epoch','asset_revision','purpose','token_hash'},
    }
    inspector=sa.inspect(m.a.pg.admin)
    for name,expected in columns.items():
        assert {column['name'] for column in inspector.get_columns(name)}==expected
    actual={column['name']:column for column in inspector.get_columns('media_assets')}
    for column in frozen['columns']:
        assert actual[column['name']]['nullable']==column['nullable']
        actual_type=actual[column['name']]['type']
        if column['type']=='timestamptz':
            assert isinstance(actual_type,sa.DateTime) and actual_type.timezone
        else:
            assert str(actual_type).lower()==column['type']
    indexes={tuple(index['column_names']) for index in inspector.get_indexes('media_assets')}
    assert all(tuple(index) in indexes for index in frozen['indexes'])
    assert inspector.get_pk_constraint('media_assets')['constrained_columns']==frozen['primary_key']
    uniques={tuple(item['column_names']) for item in inspector.get_unique_constraints('media_assets')}
    assert all(tuple(key) in uniques for key in frozen['unique_keys'])
    foreign_keys=inspector.get_foreign_keys('media_assets')
    for expected in frozen['foreign_keys']:
        assert any(item['constrained_columns']==expected['columns'] and item['referred_table']==expected['references']['table']
            and item['referred_columns']==expected['references']['columns'] and item['options']['ondelete']==expected['on_delete'] for item in foreign_keys)
    checks=' '.join(item['sqltext'] for item in inspector.get_check_constraints('media_assets'))
    assert 'size_bytes >= 0' in checks
    assert all(value in checks for value in ('staging','verified','committed','delete_pending','delete_failed','deleted'))
    for invalid in ('available','access_revoked','pending_delete','unknown'):
        with pytest.raises(sa.exc.IntegrityError):
            with m.a.pg.admin.begin() as c:
                c.execute(sa.text('UPDATE media_assets SET status=:status WHERE id=:id'),dict(status=invalid,id=asset['id']))
    with m.a.pg.admin.begin() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM pg_class WHERE relname IN ('media_assets','media_uploads','media_tickets') AND relrowsecurity AND relforcerowsecurity"))==3
    with m.a.pg.db.transaction(OwnerContext(m.a.uid)) as tx:
        with pytest.raises(sa.exc.DBAPIError):
            tx.connection.execute(sa.text('DELETE FROM media_assets WHERE id=:id'),dict(id=asset['id']))
    with pytest.raises(sa.exc.DBAPIError):
        with m.a.pg.admin.begin() as c:
            c.execute(sa.text("UPDATE media_assets SET sha256=repeat('0',64) WHERE id=:id"),dict(id=asset['id']))
    other=uuid4()
    with m.a.pg.admin.begin() as c:
        c.execute(sa.text("INSERT INTO users(id,role,state) VALUES(:id,'admin','active')"),dict(id=other))
    with m.a.pg.db.transaction(OwnerContext(other)) as tx:
        assert tx.connection.scalar(sa.text('SELECT count(*) FROM media_assets WHERE id=:id'),dict(id=asset['id']))==0
    with pytest.raises(sa.exc.IntegrityError):
        with m.a.pg.admin.begin() as c:
            c.execute(sa.text('''INSERT INTO media_uploads(id,owner_id,asset_id,expires_at)
                VALUES(:id,:owner,:asset,clock_timestamp()+interval '1 hour')'''),dict(id=uuid4(),owner=other,asset=asset['id']))


def test_conditional_objects_and_corruption_never_confirm(media):
    m=media;upload,body=create(m)
    assert put(m,upload).status_code==200
    with m.a.pg.admin.begin() as c:
        row=c.execute(sa.text('SELECT * FROM media_assets WHERE id=:id'),dict(id=upload['asset_id'])).mappings().one()
    one=m.store.put(row['object_key'],io.BytesIO(CONTENT),len(CONTENT),body['sha256'])
    two=m.store.put(row['object_key'],io.BytesIO(CONTENT),len(CONTENT),body['sha256'])
    assert one==two
    m.store.client.put_object(Bucket=m.store.bucket,Key=row['object_key'],Body=b'x'*len(CONTENT))
    assert complete(m,upload,body).status_code==422
    with m.a.pg.admin.begin() as c:
        assert c.scalar(sa.text('SELECT status FROM media_assets WHERE id=:id'),dict(id=upload['asset_id']))=='verified'


def test_create_idempotency_and_asset_expiry(media):
    m=media;key=str(uuid4())
    upload,body=create(m)
    first=m.client.post(BASE+'/media/uploads',json=body,headers=headers(m,key))
    second=m.client.post(BASE+'/media/uploads',json=body,headers=headers(m,key))
    assert first.status_code==201 and first.json()==second.json()
    changed={**body,'filename':'different.png'}
    assert m.client.post(BASE+'/media/uploads',json=changed,headers=headers(m,key)).status_code==409
    assert put(m,upload).status_code==200
    verify={k:body[k] for k in ('sha256','byte_length')}
    complete_path=BASE+f'/media/uploads/{upload["id"]}/complete'
    complete_key=str(uuid4())
    assert m.client.post(complete_path,json=verify,headers=headers(m,complete_key)).status_code==200
    assert m.client.post(complete_path,json={**verify,'sha256':'0'*64},headers=headers(m,complete_key)).status_code==409
    _,asset=available(m);path=access(m,asset)
    with m.a.pg.admin.begin() as c:
        c.execute(sa.text("UPDATE media_assets SET expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"),dict(id=asset['id']))
    assert m.client.get(path).status_code==410


def test_public_bucket_policy_blocks_service_start(media):
    m=media
    policy=dict(Version='2012-10-17',Statement=[dict(Effect='Allow',Principal='*',Action=['s3:GetObject'],Resource=[f'arn:aws:s3:::{m.store.bucket}/*'])])
    m.store.client.put_bucket_policy(Bucket=m.store.bucket,Policy=json.dumps(policy))
    from classroom_web.domain.media import ObjectUnavailable
    with pytest.raises(ObjectUnavailable):
        MediaService(auth=m.a.service,database=m.a.pg.db,store=m.store)


def test_media_upgrade_preserves_existing_catalog_and_task_data(previous_schema):
    from test_task_migrations import snapshot,upgrade,version
    from classroom_web.infrastructure.schema import tables as catalog_tables
    from classroom_web.infrastructure.task_schema import tables as task_tables
    database=previous_schema
    upgrade(database,'0008_manual_recovery')
    before=snapshot(database,catalog_tables|task_tables)
    before_names=set(sa.inspect(database.engine).get_table_names())
    upgrade(database,'0009_private_media')
    assert version(database)=='0009_private_media'
    assert snapshot(database,catalog_tables|task_tables)==before
    assert set(sa.inspect(database.engine).get_table_names())==before_names|{'media_assets','media_uploads','media_tickets'}
    upgrade(database,'0009_private_media')
    assert snapshot(database,catalog_tables|task_tables)==before


def test_audio_commit_requires_future_approved_policy_authority(media):
    m=media
    # This package has no policy authority yet, so a caller-supplied policy label grants nothing.
    for policy,with_deadline in (('unassigned',False),('unassigned',True),('pretend-approved',True)):
        expires='clock_timestamp()+interval \'7 days\'' if with_deadline else 'NULL'
        with pytest.raises(sa.exc.IntegrityError) as error:
            with m.a.pg.admin.begin() as c:
                c.execute(sa.text(f'''INSERT INTO media_assets(id,owner_id,kind,sha256,size_bytes,mime_type,object_key,
                    object_etag,status,confirmed_at,first_cloud_received_at,retention_anchor_at,expires_at,hard_expire_at,retention_policy_version)
                    VALUES(:id,:owner,'audio_chunk',:hash,1,'audio/pcm',:key,'synthetic','committed',clock_timestamp(),
                    clock_timestamp(),clock_timestamp(),{expires},{expires},:policy)'''),
                    dict(id=uuid4(),owner=m.a.uid,hash='0'*64,key=f'media/{m.a.uid.hex}/{uuid4().hex}/'+ '0'*64,policy=policy))
        assert error.value.orig.diag.constraint_name=='media_audio_commit_not_enabled'
    body=dict(purpose='audio_chunk',class_session_id=str(m.classroom.id),mime_type='audio/pcm',byte_length=1,sha256='0'*64,filename='synthetic.pcm')
    assert m.client.post(BASE+'/media/uploads',json=body,headers=headers(m)).status_code==422


def test_real_browser_media_over_https(media,tmp_path,caplog):
    import logging
    import os
    from pathlib import Path
    import subprocess
    import threading
    import time
    import uvicorn
    from test_auth_browser import _certificate,_port
    m=media;root=Path(__file__).resolve().parents[4]
    base=Path('J:/class-assistant/worktrees/web-pr03b-sessions')
    cert,key,pin=_certificate(tmp_path,'media',uuid4().hex[:12])
    origin=f'https://127.0.0.1:{_port()}'
    port=int(origin.rsplit(':',1)[1])
    contract=json.loads((root/'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf8'))
    app=create_app(auth=m.a.service,contract=contract,origin=origin,media=m.service,media_spool_directory=tmp_path)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,ssl_certfile=str(cert),ssl_keyfile=str(key),
        proxy_headers=False,access_log=True,log_level='info',timeout_graceful_shutdown=3))
    thread=threading.Thread(target=server.run,daemon=True)
    from test_authentication import PASSWORD
    config=dict(origin=origin,spki=pin,email=m.a.email,password=PASSWORD,classroomId=str(m.classroom.id),
        playwrightModule=str(base/'web/frontend/node_modules/@playwright/test/index.mjs'))
    env={**os.environ,'PLAYWRIGHT_BROWSERS_PATH':str(base/'work/pr03b/vmware-broker/payload/ms-playwright')}
    access_logger=logging.getLogger('uvicorn.access')
    access_logger.addHandler(caplog.handler)
    thread.start()
    try:
        until=time.monotonic()+15
        while not server.started and thread.is_alive() and time.monotonic()<until:
            time.sleep(.02)
        assert server.started
        run=subprocess.run([str(base/'work/pr03b/vmware-broker/payload/node/node.exe'),str(root/'tools/pr04a/browser_media.mjs')],
            input=json.dumps(config),capture_output=True,text=True,encoding='utf8',timeout=90,env=env,
            creationflags=subprocess.CREATE_NO_WINDOW)
    finally:
        server.should_exit=True
        thread.join(15)
        access_logger.removeHandler(caplog.handler)
    assert run.returncode==0,run.stdout[-2000:]+run.stderr[-2000:]
    result=json.loads(run.stdout.strip())
    assert result['passed'] and result['steps']==['HTTPS_login','media_upload_verify','authorized_range','logout_revokes_ticket','browser_storage_empty']
    assert not thread.is_alive()
    logged='\n'.join(record.getMessage() for record in caplog.records if record.name=='uvicorn.access')
    assert '/media/' in logged and 'ticket=' not in logged


def test_configured_upload_byte_boundary_and_oversize(media):
    m=media
    content=b'\x89PNG\r\n\x1a\n'+b'x'*(m.service.policy.max_bytes-8)
    upload,body=create(m,content)
    assert put(m,upload,content).status_code==200
    result=complete(m,upload,body)
    assert result.status_code==200,result.text
    asset=result.json()
    assert asset['byte_length']==m.service.policy.max_bytes
    path=access(m,asset)
    response=m.client.get(path,headers={'Range':f'bytes={len(content)-16}-'})
    assert response.status_code==206 and response.content==content[-16:]
    body['byte_length']+=1
    assert m.client.post(BASE+'/media/uploads',json=body,headers=headers(m)).status_code==422
