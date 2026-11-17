"""PR-08b real PostgreSQL state, sessions and frozen HTTP contracts."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa

from classroom_web.application.sync_browser import SyncBrowser
from classroom_web.application.review_browser import ReviewBrowser
from classroom_web.domain.data import OwnerContext, Resource
from classroom_web.domain.qa import QaRejected
from classroom_web.entrypoints.http import create_app
from classroom_web.infrastructure.database import Database
from classroom_web.infrastructure.qa_repository import _hash
from classroom_web.infrastructure.review_repository import PgReviewRepository
from classroom_web.infrastructure.sync_repository import PgSyncRepository, SYNC_TABLES
from test_authentication import auth, login, post, BASE
from test_task_migrations import previous_schema, upgrade, version


@pytest.fixture
def sync_api(auth,monkeypatch):
    monkeypatch.setattr(auth.pg.db,'cursor_secret',b'pr08b-synthetic-stable-cursor-secret-0001')
    owner = OwnerContext(auth.uid)
    with auth.pg.db.transaction(owner) as tx:
        semester = tx.catalog.create_semester('Sync semester')
        course = tx.catalog.create_course(semester.id,'Sync course')
        classroom = tx.catalog.create_class_session(course.id,'Sync classroom')
        note = PgReviewRepository(tx).create_note(dict(class_session_id=str(classroom.id),
            title='Sync note',body_markdown='original',draft_id=str(uuid4())),uuid4())
    root=Path(__file__).resolve().parents[4]
    base_contract=json.loads((root/'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf8'))
    spec=importlib.util.spec_from_file_location('pr08b_sync_review_contract',root/'tools/pr07b/contract_candidate.py')
    candidate=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(candidate)
    contract=candidate.build(base_contract)
    sync = SyncBrowser(auth=auth.service,database=auth.pg.db,enabled=True)
    review = ReviewBrowser(auth=auth.service,database=auth.pg.db,config=dict(enabled=True))
    app = create_app(auth=auth.service,contract=contract,origin='https://testserver',sync=sync,review=review)
    with TestClient(app,base_url='https://testserver') as client:
        browser = SimpleNamespace(client=client,email=auth.email)
        session = login(browser)
        yield SimpleNamespace(auth=auth,owner=owner,semester=semester,course=course,classroom=classroom,
            note=note,client=client,browser=browser,csrf=session['csrf_token'],sync=sync,app=app,
            base_contract=base_contract,contract=contract)
    with auth.pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE tasks SET not_before=clock_timestamp()+interval '365 days' WHERE owner_id=:owner"),
                           dict(owner=auth.uid))


def operation(api, *, kind='personal_revision', base=None, **changes):
    key, draft = str(uuid4()), str(uuid4())
    if kind=='personal_revision':
        identity, version = api.note['id'], api.note['revision']
        payload = dict(base_revision=base or version,base_content_revision_id=api.note['latest_personal_revision_id'],
            draft_id=draft,body_markdown='offline text',personal_note='personal note')
    elif kind=='course':
        identity, version = str(api.course.id), api.course.revision
        payload = dict(base_revision=base or version,name='offline course')
    else:
        identity, version = str(api.owner.owner_id), 1
        payload = dict(base_revision=base or version,theme='night')
    payload.update(changes)
    return dict(op_id=key,object_type=kind,object_id=identity,draft_id=draft,
                base_revision=base or version,payload=payload)


def batch(api, operations, *, key=None):
    return post(api.browser,'/sync/operations',dict(operations=operations),api.csrf,
                **{'Idempotency-Key':str(key or uuid4())})


def receipt(api, op):
    response = api.client.get(BASE+'/operations/'+op['op_id'],params=dict(operation='syncOperation'))
    assert response.status_code==200,response.text
    return response.json()


def build_partial_revocation_conflict(pg, owner, classroom):
    """Create an A-origin document whose newer conflict depends on A and B."""
    from test_pr07b_versions import generate,prepare_review,REQUEST,output
    from classroom_web.infrastructure.qa_repository import PgQaRepository

    tree=SimpleNamespace(classroom=classroom)
    first_source,_,_,initial=generate(pg,SimpleNamespace(a=owner),tree)
    document_id=UUID(initial['summary_document_id'])
    with pg.db.transaction(owner) as tx:
        original=PgReviewRepository(tx).document(document_id)
        second_source=PgQaRepository(tx).add_source(classroom.id,kind='transcript_segment',object_id=uuid4(),
            text='Source B may later be independently revoked.',start_ms=6000,end_ms=8000)
    request=dict(REQUEST,target_document_id=str(document_id),base_revision=original['revision'])
    plan,task=prepare_review(pg,owner,tree,request=request)
    assert {item['source_id'] for item in plan['source_snapshot']['items']} == {
        str(first_source.source_id),str(second_source.source_id)}
    with pg.db.transaction(owner) as tx:
        repo=PgReviewRepository(tx)
        newer=repo.publish(task,output(alias='S2',answer='Candidate using independently revocable source B.'),
                          UUID(plan['source_snapshot']['id']),request)
        document=repo.document(document_id)
    commands=[]
    for text in ('Cloud personal content using source B.','Offline personal content using source B.'):
        key,draft=str(uuid4()),str(uuid4())
        commands.append(dict(op_id=key,object_type='personal_revision',object_id=str(document_id),
            draft_id=draft,base_revision=document['revision'],payload=dict(base_revision=document['revision'],
                base_content_revision_id=newer['revision_ids'][0],draft_id=draft,body_markdown=text,personal_note='')))
    for command in commands:
        with pg.db.transaction(owner) as tx:
            result=PgSyncRepository(tx).apply(command)
    assert result['status']=='conflict'
    return dict(operation=commands[-1],conflict_id=result['conflict_id'],document_id=str(document_id),
                document_revision=result['revision'],base_content_revision_id=newer['revision_ids'][0],
                unaffected_revision_id=initial['revision_ids'][0],revoked_source_id=str(second_source.source_id))


def test_real_http_apply_replay_fingerprint_and_lost_response(sync_api):
    a=sync_api
    op=operation(a)
    outer=uuid4()
    first=batch(a,[op],key=outer)
    assert first.status_code==200,first.text
    result=first.json()['receipts'][0]
    assert result['status']=='applied' and result['request_sha256']==_hash(op)
    assert result['revision']==a.note['revision']+1
    assert receipt(a,op)==result
    assert batch(a,[op],key=outer).json()['receipts']==[result]
    assert batch(a,[op]).json()['receipts']==[result]
    with a.auth.pg.db.transaction(a.owner) as tx:
        revisions=PgReviewRepository(tx).revisions(UUID(a.note['id']))['items']
        assert len(revisions)==2
    changed=dict(op,payload=dict(op['payload'],body_markdown='different'))
    assert batch(a,[changed],key=outer).status_code==409
    assert batch(a,[changed]).status_code==409
    assert receipt(a,op)==result


def test_batch_commits_each_item_and_rejects_foreign_objects(sync_api):
    a=sync_api
    good=operation(a)
    foreign=dict(operation(a),object_id=str(uuid4()))
    response=batch(a,[good,foreign])
    assert response.status_code==200,response.text
    assert [r['status'] for r in response.json()['receipts']]==['applied','rejected']
    assert response.json()['receipts'][1]['object_id'] is None
    assert receipt(a,good)['status']=='applied'


def test_nested_base_and_draft_mismatch_rejected_without_writing(sync_api):
    a=sync_api
    wrong_base=operation(a)
    wrong_base['payload']['base_revision']+=1
    wrong_draft=operation(a)
    wrong_draft['payload']['draft_id']=str(uuid4())
    response=batch(a,[wrong_base,wrong_draft])
    assert response.status_code==200,response.text
    assert all(r['status']=='rejected' for r in response.json()['receipts'])
    with a.auth.pg.db.transaction(a.owner) as tx:
        assert PgReviewRepository(tx).document(UUID(a.note['id']))['revision']==a.note['revision']


@pytest.mark.parametrize('resolution',['use_server','edited_merge','copy_to_new_note'])
def test_two_device_conflict_preserves_both_and_explicit_resolution(sync_api,resolution):
    a=sync_api
    winner=operation(a,body_markdown='remote wins first')
    loser=operation(a,body_markdown='offline branch survives')
    assert batch(a,[winner]).json()['receipts'][0]['status']=='applied'
    response=batch(a,[loser])
    assert response.status_code==200,response.text
    result=response.json()['receipts'][0]
    assert result['status']=='conflict'
    conflict=a.client.get(BASE+'/sync/conflicts/'+result['conflict_id'])
    assert conflict.status_code==200,conflict.text
    state=conflict.json()
    assert state['local_operation']==loser
    assert state['server_content']['body_markdown']=='remote wins first'
    assert not state['resolved']
    body=dict(base_revision=result['revision'],resolution=resolution)
    if resolution=='edited_merge':
        body['merged_operation']=operation(a,base=result['revision'],body_markdown='manually merged')
    elif resolution=='copy_to_new_note':
        body['destination_class_session_id']=str(a.classroom.id)
    key=str(uuid4())
    resolved=post(a.browser,'/sync/conflicts/'+result['conflict_id']+'/resolve',body,a.csrf,**{'Idempotency-Key':key})
    assert resolved.status_code==200,resolved.text
    assert resolved.json()['status']=='applied'
    assert resolved.json()['request_sha256']==_hash(dict(conflict_id=result['conflict_id'],body=body))
    replay=post(a.browser,'/sync/conflicts/'+result['conflict_id']+'/resolve',body,a.csrf,**{'Idempotency-Key':key})
    assert replay.json()==resolved.json()
    second_key=post(a.browser,'/sync/conflicts/'+result['conflict_id']+'/resolve',body,a.csrf)
    assert second_key.status_code==409,second_key.text
    assert a.client.get(BASE+'/sync/conflicts/'+result['conflict_id']).json()['resolved']
    with a.auth.pg.db.transaction(a.owner) as tx:
        repo=PgReviewRepository(tx)
        assert 'offline branch survives' in [r['body_markdown'] for r in repo.revisions(UUID(a.note['id']))['items']]
        target=repo.document(UUID(resolved.json()['object_id']))
        saved=repo.revision(UUID(target['latest_personal_revision_id']))
        text=saved['body_markdown']
        assert text=={'use_server':'remote wins first','edited_merge':'manually merged',
                     'copy_to_new_note':'offline branch survives'}[resolution]
        if resolution=='copy_to_new_note':
            assert saved['personal_note']=='personal note'


def test_resolution_checks_newest_cas_and_typed_object(sync_api):
    a=sync_api
    batch(a,[operation(a)])
    result=batch(a,[operation(a)]).json()['receipts'][0]
    newer=operation(a,base=result['revision'],body_markdown='newer cloud')
    assert batch(a,[newer]).json()['receipts'][0]['status']=='applied'
    stale=post(a.browser,'/sync/conflicts/'+result['conflict_id']+'/resolve',
        dict(base_revision=result['revision'],resolution='use_server'),a.csrf)
    assert stale.status_code==409,stale.text
    with a.auth.pg.db.transaction(a.owner) as tx:
        before=PgReviewRepository(tx).document(UUID(a.note['id']))
    for resolution in ('use_server','edited_merge','copy_to_new_note'):
        body=dict(base_revision=before['revision'],resolution=resolution)
        if resolution=='edited_merge':
            body['merged_operation']=operation(a,base=before['revision'],body_markdown='Must compare a new conflict first')
        elif resolution=='copy_to_new_note':
            body['destination_class_session_id']=str(a.classroom.id)
        response=post(a.browser,'/sync/conflicts/'+result['conflict_id']+'/resolve',body,a.csrf)
        assert response.status_code==409,response.text
    with a.auth.pg.db.transaction(a.owner) as tx:
        assert PgReviewRepository(tx).document(UUID(a.note['id']))==before
        assert tx.connection.scalar(sa.text('SELECT count(*) FROM sync_resolutions WHERE owner_id=:owner'),
                                    dict(owner=a.owner.owner_id))==0
        assert tx.connection.scalar(sa.text('SELECT count(*) FROM review_documents WHERE owner_id=:owner'),
                                    dict(owner=a.owner.owner_id))==1
    fresh=batch(a,[operation(a)]).json()['receipts'][0]
    body=dict(base_revision=fresh['revision'],resolution='edited_merge',merged_operation=operation(a,kind='course'))
    rejected=post(a.browser,'/sync/conflicts/'+fresh['conflict_id']+'/resolve',body,a.csrf)
    assert rejected.status_code==422,rejected.text
    accepted=post(a.browser,'/sync/conflicts/'+fresh['conflict_id']+'/resolve',
                  dict(base_revision=fresh['revision'],resolution='use_server'),a.csrf)
    assert accepted.status_code==200,accepted.text


@pytest.mark.parametrize('kind',['course','preferences'])
def test_typed_course_and_preferences_conflicts(sync_api,kind):
    a=sync_api
    op=operation(a,kind=kind)
    first=batch(a,[op])
    assert first.status_code==200,first.text
    assert first.json()['receipts'][0]['status']=='applied'
    other=operation(a,kind=kind)
    conflict=batch(a,[other]).json()['receipts'][0]
    assert conflict['status']=='conflict'
    view=a.client.get(BASE+'/sync/conflicts/'+conflict['conflict_id'])
    assert view.status_code==200,view.text
    assert view.json()['local_operation']==other


def test_mark_normal_entry_point_and_offline_cas(sync_api):
    a=sync_api
    body=dict(kind='important',note='initial mark',anchor=dict(kind='review_revision',document_id=a.note['id'],
        revision_id=a.note['latest_personal_revision_id'],content_sha256=hashlib.sha256(b'original').hexdigest()))
    response=post(a.browser,f'/class-sessions/{a.classroom.id}/marks',body,a.csrf)
    assert response.status_code==201,response.text
    mark=response.json()
    op=dict(op_id=str(uuid4()),object_type='mark',object_id=mark['id'],draft_id=str(uuid4()),base_revision=1,
            payload=dict(base_revision=1,note='offline mark',understood=True))
    assert batch(a,[op]).json()['receipts'][0]['status']=='applied'
    other=dict(op,op_id=str(uuid4()))
    assert batch(a,[other]).json()['receipts'][0]['status']=='conflict'
    assert a.client.get(BASE+'/marks/'+mark['id']).json()['note']=='offline mark'


@pytest.mark.parametrize('scope',['semester','course','class_session'])
def test_deleted_ancestor_removes_complete_projection_and_hides_receipts(sync_api,scope):
    a=sync_api
    op=operation(a)
    assert batch(a,[op]).json()['receipts'][0]['status']=='applied'
    target={'semester':a.semester,'course':a.course,'class_session':a.classroom}[scope]
    with a.auth.pg.db.transaction(a.owner) as tx:
        tx.tombstones.revoke(Resource(scope),target.id,target.revision,1)
    response=a.client.get(BASE+'/sync/bootstrap')
    assert response.status_code==200,response.text
    identities={p['object_id'] for p in response.json()['projections']}
    assert str(a.classroom.id) not in identities and a.note['id'] not in identities
    if scope in ('semester','course'):
        assert str(a.course.id) not in identities
    assert receipt(a,op)['status']=='deleted'
    assert receipt(a,op)['object_id'] is None
    assert batch(a,[op]).json()['receipts'][0]['status']=='deleted'
    assert batch(a,[operation(a)]).json()['receipts'][0]['status']=='deleted'


def test_bootstrap_watermark_and_real_change_page(sync_api):
    a=sync_api
    bootstrap=a.client.get(BASE+'/sync/bootstrap')
    assert bootstrap.status_code==200,bootstrap.text
    state=bootstrap.json()
    assert state['projection_complete']
    assert 'original' not in bootstrap.text and 'Sync note' not in bootstrap.text
    batch(a,[operation(a),operation(a,kind='preferences')])
    changes=a.client.get(BASE+'/sync/changes',params=dict(after=state['watermark'],limit=1))
    assert changes.status_code==200,changes.text
    assert changes.json()['has_more']
    following=a.client.get(BASE+'/sync/changes',params=dict(after=changes.json()['next_cursor'],limit=1000))
    assert following.status_code==200,following.text
    assert not following.json()['has_more']
    invalid=a.client.get(BASE+'/sync/changes',params=dict(after='invalid-cursor'))
    assert invalid.status_code==409 and invalid.json()['error']['code']=='CURSOR_EXPIRED'


def test_bootstrap_repeatable_read_excludes_later_commit(sync_api):
    a=sync_api
    with a.auth.pg.db.transaction(a.owner,isolation_level='REPEATABLE READ') as tx:
        repo=PgSyncRepository(tx)
        original=repo.bootstrap()
        with a.auth.pg.db.transaction(a.owner) as other:
            PgSyncRepository(other).apply(operation(a))
        assert repo.bootstrap()['projections']==original['projections']
        assert repo.bootstrap()['watermark']==original['watermark']
    with a.auth.pg.db.transaction(a.owner,isolation_level='REPEATABLE READ') as tx:
        assert PgSyncRepository(tx).changes(original['watermark'])['items']


def test_concurrent_same_operation_applies_once(sync_api):
    a=sync_api
    op=operation(a)
    def execute():
        with a.auth.pg.db.transaction(a.owner) as tx:
            return PgSyncRepository(tx).apply(op)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first,second=list(pool.map(lambda _:execute(),range(2)))
    assert first==second and first['status']=='applied'


def test_sync_table_rls_and_immutable_conflict_evidence(sync_api):
    a=sync_api
    expected_columns={
        'sync_operations':{'id','owner_id','created_at','operation','op_id','request_hash','object_type','object_id','class_session_id','result_revision','conflict_id','status'},
        'sync_conflicts':{'id','owner_id','created_at','object_type','object_id','class_session_id','base_revision','server_revision','local_operation','server_content'},
        'sync_resolutions':{'id','owner_id','created_at','conflict_id','op_id'},
        'sync_preferences':{'id','owner_id','created_at','updated_at','revision','value'},
        'sync_marks':{'id','owner_id','created_at','updated_at','revision','class_session_id','kind','anchor','note','understood'},
    }
    inspector=sa.inspect(a.auth.pg.admin)
    for table,columns in expected_columns.items():
        assert {column['name'] for column in inspector.get_columns(table)}==columns
        assert inspector.get_pk_constraint(table)['constrained_columns']==['id']
    with a.auth.pg.admin.connect() as connection:
        rows=connection.execute(sa.text('''SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class
            WHERE relnamespace='public'::regnamespace AND relname=ANY(:names)'''),dict(names=list(SYNC_TABLES))).all()
        assert len(rows)==len(SYNC_TABLES)
        assert all(row.relrowsecurity and row.relforcerowsecurity for row in rows)
        for table in ('sync_operations','sync_conflicts','sync_resolutions'):
            assert not connection.scalar(sa.text("SELECT has_table_privilege('classroom_web_runtime',:name,'UPDATE')"),dict(name=table))


def test_default_off_routes_and_invalid_query(sync_api):
    a=sync_api
    a.sync.enabled=False
    assert a.client.get(BASE+'/sync/bootstrap').status_code==503
    a.sync.enabled=True
    assert a.client.get(BASE+'/sync/changes?limit=1&limit=2').status_code==422
    assert a.client.get(BASE+'/operations/'+str(uuid4())).status_code==422
    assert a.client.get(BASE+'/sync/bootstrap?owner_id='+str(uuid4())).status_code==422


def test_sync_requires_existing_review_contract_composition(sync_api):
    a=sync_api
    with pytest.raises(ValueError,match='Sync requires the existing PR-07b contract candidate'):
        create_app(auth=a.auth.service,contract=a.base_contract,origin='https://testserver',sync=a.sync)


def test_enabled_sync_requires_review_authorization_endpoints(sync_api):
    a=sync_api
    disabled=ReviewBrowser(auth=a.auth.service,database=a.auth.pg.db,config=dict(enabled=False))
    for review in (None,disabled):
        with pytest.raises(ValueError,match='Enabled Sync requires an enabled ReviewBrowser'):
            create_app(auth=a.auth.service,contract=a.contract,origin='https://testserver',sync=a.sync,review=review)
    assert a.client.get(BASE+'/review-documents/'+a.note['id']).status_code==200
    assert a.client.get(BASE+'/review-revisions/'+a.note['latest_personal_revision_id']).status_code==200


def test_bootstrap_10000_projection_bound_never_truncates(sync_api):
    a=sync_api
    with a.auth.pg.db.transaction(a.owner) as tx:
        baseline=len(PgSyncRepository(tx).bootstrap()['projections'])
        tx.connection.execute(sa.text('''INSERT INTO semesters(id,owner_id,name)
            SELECT gen_random_uuid(),:owner,'Synthetic bounded projection '||i
            FROM generate_series(1,:count) AS i'''),dict(owner=a.owner.owner_id,count=10000-baseline))
    with a.auth.pg.db.transaction(a.owner,isolation_level='REPEATABLE READ') as tx:
        snapshot=PgSyncRepository(tx).bootstrap()
        assert len(snapshot['projections'])==10000 and snapshot['projection_complete']
    with a.auth.pg.db.transaction(a.owner) as tx:
        tx.catalog.create_semester('One beyond the explicit capacity')
    with pytest.raises(QaRejected,match='CAPACITY_EXCEEDED'),a.auth.pg.db.transaction(a.owner,isolation_level='REPEATABLE READ') as tx:
        PgSyncRepository(tx).bootstrap()


def test_deferred_catalog_events_do_not_invert_review_lock_order(sync_api):
    a=sync_api
    catalog_created,review_locked=Event(),Event()
    def catalog_then_review():
        with a.auth.pg.db.transaction(a.owner) as tx:
            tx.catalog.create_semester('Catalog transaction waiting for review')
            catalog_created.set()
            assert review_locked.wait(4)
            PgReviewRepository(tx).update_document(UUID(a.note['id']),dict(base_revision=a.note['revision']+1,title='Second update'),uuid4())
    def review_then_catalog():
        assert catalog_created.wait(4)
        with a.auth.pg.db.transaction(a.owner) as tx:
            review=PgReviewRepository(tx)
            review._serialize()
            review_locked.set()
            review.update_document(UUID(a.note['id']),dict(base_revision=a.note['revision'],title='First update'),uuid4())
            tx.catalog.create_semester('Review transaction adding catalog metadata')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(catalog_then_review)
        second=pool.submit(review_then_catalog)
        first.result(timeout=15)
        second.result(timeout=15)
    with a.auth.pg.db.transaction(a.owner) as tx:
        assert PgReviewRepository(tx).document(UUID(a.note['id']))['title']=='Second update'


@pytest.mark.parametrize('resolution',['use_server','edited_merge','copy_to_new_note'])
def test_course_resolution_and_direct_note_creation_have_no_lock_cycle(sync_api,resolution):
    a=sync_api
    assert batch(a,[operation(a,kind='course')]).status_code==200
    conflict=batch(a,[operation(a,kind='course')]).json()['receipts'][0]
    source_shared,resolution_lock_attempt=Event(),Event()

    def create_note():
        with a.auth.pg.db.transaction(a.owner) as tx:
            review=PgReviewRepository(tx)
            serialize=review._serialize
            def wait_after_source_authorization():
                source_shared.set()
                assert resolution_lock_attempt.wait(4)
                serialize()
            review._serialize=wait_after_source_authorization
            return review.create_note(dict(class_session_id=str(a.classroom.id),title='Concurrent note',
                body_markdown='Created while resolving a course conflict.',draft_id=str(uuid4())),uuid4())

    def resolve_course():
        assert source_shared.wait(4)
        with a.auth.pg.db.transaction(a.owner) as tx:
            load=tx.catalog._load
            def signal_course_lock(resource,identity,**options):
                if resource==Resource.COURSE and identity==a.course.id and (options.get('write') or options.get('share')):
                    resolution_lock_attempt.set()
                return load(resource,identity,**options)
            tx.catalog._load=signal_course_lock
            body=dict(base_revision=conflict['revision'],resolution=resolution)
            if resolution=='edited_merge':
                body['merged_operation']=operation(a,kind='course',base=conflict['revision'],name='Explicitly merged course')
            elif resolution=='copy_to_new_note':
                body['destination_class_session_id']=str(a.classroom.id)
            return PgSyncRepository(tx).resolve(UUID(conflict['conflict_id']),body,uuid4())

    with ThreadPoolExecutor(max_workers=2) as pool:
        created=pool.submit(create_note)
        resolved=pool.submit(resolve_course)
        note=created.result(timeout=15)
        result=resolved.result(timeout=15)
    assert result['status']=='applied'
    with a.auth.pg.db.transaction(a.owner) as tx:
        assert PgReviewRepository(tx).document(UUID(note['id']))['title']=='Concurrent note'
        course=tx.catalog.get_course(a.course.id)
        assert course.revision==conflict['revision']+(resolution=='edited_merge')


def test_direct_review_conflict_locks_cloud_sources_before_event_counter(sync_api):
    from classroom_web.infrastructure.qa_repository import PgQaRepository
    a=sync_api
    state=build_partial_revocation_conflict(a.auth.pg,a.owner,a.classroom)
    writer_ready,revoker_started,source_revoked,counter_locked=Event(),Event(),Event(),Event()
    advance_allowed,revocation_commit_allowed=Event(),Event()
    connection_ids={}
    draft=dict(base_revision=state['document_revision']-1,
        base_content_revision_id=state['unaffected_revision_id'],draft_id=str(uuid4()),
        body_markdown='A-only personal branch remains preserved.',personal_note='')

    def write_direct_conflict():
        with a.auth.pg.db.transaction(a.owner) as tx:
            connection_ids['writer']=tx.connection.scalar(sa.text('SELECT pg_backend_pid()'))
            review=PgReviewRepository(tx)
            advance,event=review._advance,review._event
            def pause_before_counter(row,**fields):
                writer_ready.set()
                assert advance_allowed.wait(5)
                return advance(row,**fields)
            def observe_counter(row):
                event(row)
                counter_locked.set()
            review._advance,review._event=pause_before_counter,observe_counter
            return review.write_personal(UUID(state['document_id']),draft,uuid4())

    def revoke_cloud_source():
        with a.auth.pg.db.transaction(a.owner) as tx:
            connection_ids['revoker']=tx.connection.scalar(sa.text('SELECT pg_backend_pid()'))
            revoker_started.set()
            PgQaRepository(tx).revoke_source(UUID(state['revoked_source_id']))
            source_revoked.set()
            assert revocation_commit_allowed.wait(5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer=pool.submit(write_direct_conflict)
        assert writer_ready.wait(5)
        revoker=pool.submit(revoke_cloud_source)
        try:
            assert revoker_started.wait(5)
            deadline=monotonic()+4
            while not source_revoked.is_set():
                with a.auth.pg.admin.connect() as monitor:
                    blocked=monitor.scalar(sa.text('SELECT :writer=ANY(pg_blocking_pids(:revoker))'),connection_ids)
                if blocked:
                    break
                assert monotonic()<deadline,'source revocation did not reach its real PostgreSQL lock'
                sleep(.01)
            advance_allowed.set()
            assert counter_locked.wait(5)
            assert source_revoked.wait(5)
        finally:
            advance_allowed.set()
            revocation_commit_allowed.set()
        result=writer.result(timeout=15)
        revoker.result(timeout=15)
    assert result['submitted_revision']['body_markdown']==draft['body_markdown']
    with a.auth.pg.db.transaction(a.owner) as tx:
        review=PgReviewRepository(tx)
        assert review.document(UUID(state['document_id']))['revision']==state['document_revision']+1
        assert review.revision(UUID(result['submitted_revision']['id']))['body_markdown']==draft['body_markdown']
        with pytest.raises(QaRejected,match='NOT_FOUND'):
            review.revision(UUID(state['base_content_revision_id']))


def test_direct_review_receipt_and_conflict_join_the_sync_read_api(sync_api):
    a=sync_api
    first=operation(a)
    stale=operation(a)
    with a.auth.pg.db.transaction(a.owner) as tx:
        PgReviewRepository(tx).write_personal(UUID(a.note['id']),first['payload'],UUID(first['op_id']))
    with a.auth.pg.db.transaction(a.owner) as tx:
        conflict=PgReviewRepository(tx).write_personal(UUID(a.note['id']),stale['payload'],UUID(stale['op_id']))
    response=a.client.get(BASE+'/operations/'+first['op_id'],params=dict(operation='writePersonalRevision'))
    assert response.status_code==200,response.text
    assert response.json()['status']=='applied' and response.json()['object_id']==a.note['id']
    view=a.client.get(BASE+'/sync/conflicts/'+conflict['conflict_id'])
    assert view.status_code==200,view.text
    assert view.json()['local_operation']==stale


def test_ownership_probe_never_reveals_foreign_or_untyped_objects(sync_api):
    a=sync_api
    outsider=uuid4()
    with a.auth.pg.admin.begin() as connection:
        connection.execute(sa.text("INSERT INTO users(id,role,state) VALUES(:id,'student','active')"),dict(id=outsider))
    with a.auth.pg.db.transaction(OwnerContext(outsider)) as tx:
        assert not tx.connection.scalar(sa.text("SELECT classroom_web_sync_owned_object('personal_revision',:id)"),dict(id=UUID(a.note['id'])))
        assert not tx.connection.scalar(sa.text("SELECT classroom_web_sync_owned_object('users',:id)"),dict(id=a.owner.owner_id))
        assert not tx.connection.execute(sa.text('SELECT * FROM sync_conflicts')).all()
    with a.auth.pg.admin.connect() as connection:
        assert not connection.scalar(sa.text('''SELECT EXISTS(SELECT FROM pg_proc p,
            LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
            WHERE p.oid='classroom_web_sync_owned_object(text,uuid)'::regprocedure
            AND a.grantee=0 AND a.privilege_type='EXECUTE')'''))


def test_copy_preserves_both_maximum_length_personal_fields(sync_api):
    a=sync_api
    batch(a,[operation(a)])
    content,note='文'*120000,'注'*12000
    result=batch(a,[operation(a,body_markdown=content,personal_note=note)]).json()['receipts'][0]
    response=post(a.browser,'/sync/conflicts/'+result['conflict_id']+'/resolve',
        dict(base_revision=result['revision'],resolution='copy_to_new_note',destination_class_session_id=str(a.classroom.id)),a.csrf)
    assert response.status_code==200,response.text[:500]
    with a.auth.pg.db.transaction(a.owner) as tx:
        repo=PgReviewRepository(tx)
        document=repo.document(UUID(response.json()['object_id']))
        saved=repo.revision(UUID(document['latest_personal_revision_id']))
        assert saved['body_markdown']==content and saved['personal_note']==note


def test_legacy_exact_replay_backfills_conflict_and_never_appends_again(sync_api):
    a=sync_api
    first,stale=operation(a),operation(a)
    with a.auth.pg.db.transaction(a.owner) as tx:
        repo=PgReviewRepository(tx)
        repo.write_personal(UUID(a.note['id']),first['payload'],UUID(first['op_id']))
        conflict=repo.write_personal(UUID(a.note['id']),stale['payload'],UUID(stale['op_id']))
    identity=UUID(conflict['conflict_id'])
    # Build a pre-08b fixture by removing only this synthetic supplemental row.
    with a.auth.pg.admin.begin() as connection:
        connection.exec_driver_sql('ALTER TABLE sync_conflicts DISABLE TRIGGER sync_conflicts_immutable')
        connection.execute(sa.text('DELETE FROM sync_conflicts WHERE id=:id'),dict(id=identity))
        connection.exec_driver_sql('ALTER TABLE sync_conflicts ENABLE TRIGGER sync_conflicts_immutable')
    before=a.client.get(BASE+'/sync/conflicts/'+str(identity))
    assert before.status_code==409 and before.json()['error']['code']=='INCOMPATIBLE_CLIENT'
    assert batch(a,[first]).json()['receipts'][0]['status']=='applied'
    assert batch(a,[stale]).json()['receipts'][0]['conflict_id']==str(identity)
    after=a.client.get(BASE+'/sync/conflicts/'+str(identity))
    assert after.status_code==200,after.text
    assert after.json()['local_operation']==stale
    with a.auth.pg.db.transaction(a.owner) as tx:
        assert len(PgReviewRepository(tx).revisions(UUID(a.note['id']))['items'])==3


def test_legacy_preferences_unknown_fields_survive_supported_updates(sync_api):
    a=sync_api
    with a.auth.pg.db.transaction(a.owner) as tx:
        tx.connection.execute(sa.text('''UPDATE sync_preferences SET value=value||CAST(:extra AS jsonb),revision=revision+1
            WHERE owner_id=:owner'''),dict(extra=json.dumps(dict(legacy_setting=dict(preserve=True))),owner=a.owner.owner_id))
    response=batch(a,[operation(a,kind='preferences',base=2,theme='day')])
    assert response.status_code==200,response.text
    with a.auth.pg.db.transaction(a.owner) as tx:
        value=tx.connection.scalar(sa.text('SELECT value FROM sync_preferences WHERE owner_id=:owner'),dict(owner=a.owner.owner_id))
        assert value['legacy_setting']==dict(preserve=True) and value['theme']=='day'
    read=a.client.get(BASE+'/me/preferences')
    assert read.status_code==200 and 'legacy_setting' not in read.json()


def test_forward_upgrade_preserves_large_and_nonobject_legacy_preferences(previous_schema):
    database=previous_schema
    upgrade(database,'0013_trusted_device_guard')
    legacy=dict(theme='night',reading_font_px=29,legacy_opaque=[str(uuid4()) for _ in range(1024)])
    legacy_values={database.owner_id:legacy,uuid4():['legacy shape'],uuid4():None,
                   uuid4():dict(reading_font_px='large')}
    with database.engine.begin() as connection:
        for owner,value in legacy_values.items():
            if owner != database.owner_id:
                connection.execute(sa.text("INSERT INTO users(id,role,state) VALUES(:id,'student','active')"),dict(id=owner))
            connection.execute(sa.text('UPDATE users SET preferences=CAST(:value AS jsonb) WHERE id=:id'),
                               dict(id=owner,value=json.dumps(value)))
        assert connection.scalar(sa.text('SELECT pg_column_size(preferences) FROM users WHERE id=:id'),
                                 dict(id=database.owner_id))>8192
    upgrade(database,'0014_offline_sync')
    upgrade(database,'0014_offline_sync')
    assert version(database)=='0014_offline_sync'
    with database.engine.connect() as connection:
        rows=connection.execute(sa.text('''SELECT u.id,u.preferences,p.value FROM users u
            JOIN sync_preferences p ON p.owner_id=u.id''')).mappings().all()
        assert {row['id']:row['preferences'] for row in rows}==legacy_values
        assert {row['id']:row['value'] for row in rows}==legacy_values
    runtime=Database(database.runtime_url.render_as_string(hide_password=False),
                     cursor_secret=b'pr08b-upgrade-synthetic-cursor-secret')
    try:
        for owner,value in legacy_values.items():
            with runtime.transaction(OwnerContext(owner)) as tx:
                repo=PgSyncRepository(tx)
                assert any(p['object_type']=='preferences' for p in repo.bootstrap()['projections'])
                if owner == database.owner_id:
                    assert repo.preferences()['theme']=='night'
                    assert repo.preferences()['reading_font_px']==29
                else:
                    with pytest.raises(QaRejected,match='INCOMPATIBLE_CLIENT'):
                        repo.preferences()
        with runtime.transaction(OwnerContext(database.owner_id)) as tx:
            key=str(uuid4())
            result=PgSyncRepository(tx).apply(dict(op_id=key,object_type='preferences',
                object_id=str(database.owner_id),draft_id=key,base_revision=1,
                payload=dict(base_revision=1,theme='day')))
            assert result['status']=='applied'
        with database.engine.connect() as connection:
            stored=connection.scalar(sa.text('SELECT value FROM sync_preferences WHERE owner_id=:id'),
                                     dict(id=database.owner_id))
            assert stored==dict(legacy,theme='day')
            assert connection.scalar(sa.text('SELECT preferences FROM users WHERE id=:id'),
                                     dict(id=database.owner_id))==legacy
    finally:
        runtime.dispose()


@pytest.mark.parametrize('legacy',[['legacy shape'],None,dict(reading_font_px='large')])
def test_incompatible_legacy_preferences_fail_explicitly_without_overwrite(sync_api,legacy):
    a=sync_api
    with a.auth.pg.db.transaction(a.owner) as tx:
        tx.connection.execute(sa.text('''UPDATE sync_preferences SET value=CAST(:value AS jsonb),revision=revision+1
            WHERE owner_id=:owner'''),dict(value=json.dumps(legacy),owner=a.owner.owner_id))
    response=a.client.get(BASE+'/me/preferences')
    assert response.status_code==409 and response.json()['error']['code']=='INCOMPATIBLE_CLIENT'
    assert a.client.get(BASE+'/sync/bootstrap').status_code==200
    rejected=batch(a,[operation(a,kind='preferences',base=2)])
    assert rejected.status_code==200,rejected.text
    assert rejected.json()['receipts'][0]['status']=='rejected'
    with a.auth.pg.db.transaction(a.owner) as tx:
        row=tx.connection.execute(sa.text('SELECT revision,value FROM sync_preferences WHERE owner_id=:owner'),
                                  dict(owner=a.owner.owner_id)).mappings().one()
        assert row['revision']==2 and row['value']==legacy


def test_source_revocation_emits_change_and_revokes_all_sync_reads(sync_api):
    from test_pr07b_versions import generate
    from classroom_web.infrastructure.qa_repository import PgQaRepository
    a=sync_api
    source,_,_,generated=generate(a.auth.pg,SimpleNamespace(a=a.owner),a)
    with a.auth.pg.db.transaction(a.owner) as tx:
        document=PgReviewRepository(tx).document(UUID(generated['summary_document_id']))
    op=operation(a)
    op.update(object_id=document['id'],base_revision=document['revision'])
    op['payload'].update(base_revision=document['revision'],base_content_revision_id=document['candidate_revision_ids'][0])
    assert batch(a,[op]).json()['receipts'][0]['status']=='applied'
    before=a.client.get(BASE+'/sync/bootstrap').json()['watermark']
    with a.auth.pg.db.transaction(a.owner) as tx:
        PgQaRepository(tx).revoke_source(source.source_id)
    changes=a.client.get(BASE+'/sync/changes',params=dict(after=before))
    assert changes.status_code==200,changes.text
    assert any(item['object_id']==document['id'] and item['deleted'] for item in changes.json()['items'])
    bootstrap=a.client.get(BASE+'/sync/bootstrap')
    assert bootstrap.status_code==200,bootstrap.text
    assert document['id'] not in {item['object_id'] for item in bootstrap.json()['projections']}
    assert receipt(a,op)['status']=='deleted'
    assert batch(a,[dict(op,op_id=str(uuid4()))]).json()['receipts'][0]['status']=='deleted'


def test_partial_source_revocation_keeps_document_but_hides_exact_conflict_lineage(sync_api):
    from classroom_web.infrastructure.qa_repository import PgQaRepository
    a=sync_api
    state=build_partial_revocation_conflict(a.auth.pg,a.owner,a.classroom)
    document_path=BASE+'/review-documents/'+state['document_id']
    conflict_path=BASE+'/sync/conflicts/'+state['conflict_id']
    conflict=a.client.get(conflict_path)
    assert conflict.status_code==200,conflict.text
    server_revision=conflict.json()['server_content']['id']
    with a.auth.pg.db.transaction(a.owner) as tx:
        local_revision=str(tx.connection.scalar(sa.text('SELECT submitted_revision_id FROM review_conflicts WHERE id=:id'),
                                               dict(id=UUID(state['conflict_id']))))
    for revision in (state['base_content_revision_id'],server_revision,local_revision,state['unaffected_revision_id']):
        assert a.client.get(BASE+'/review-revisions/'+revision).status_code==200
    watermark=a.client.get(BASE+'/sync/bootstrap').json()['watermark']
    with a.auth.pg.db.transaction(a.owner) as tx:
        PgQaRepository(tx).revoke_source(UUID(state['revoked_source_id']))
    projection=a.client.get(BASE+'/sync/bootstrap')
    assert projection.status_code==200,projection.text
    document_projection=next(item for item in projection.json()['projections'] if item['object_id']==state['document_id'])
    assert document_projection['revision']==state['document_revision']
    changes=a.client.get(BASE+'/sync/changes',params=dict(after=watermark))
    assert changes.status_code==200,changes.text
    assert any(item['object_id']==state['document_id'] and not item['deleted']
               and item['revision']==state['document_revision'] for item in changes.json()['items'])
    assert a.client.get(document_path).status_code==200
    assert a.client.get(BASE+'/review-revisions/'+state['unaffected_revision_id']).status_code==200
    for revision in (state['base_content_revision_id'],server_revision,local_revision):
        assert a.client.get(BASE+'/review-revisions/'+revision).status_code==404
    assert a.client.get(conflict_path).status_code==404
    with a.auth.pg.db.transaction(a.owner) as tx:
        assert tx.connection.scalar(sa.text('SELECT id FROM review_documents WHERE id=:id'),
                                    dict(id=UUID(state['document_id']))) is not None
        assert tx.connection.scalar(sa.text('SELECT id FROM review_revisions WHERE id=:id'),
                                    dict(id=UUID(state['base_content_revision_id']))) is None
        assert tx.connection.scalar(sa.text('SELECT id FROM sync_conflicts WHERE id=:id'),
                                    dict(id=UUID(state['conflict_id']))) is None


def test_visible_personal_parent_can_be_updated_after_latest_source_revocation(sync_api):
    from classroom_web.infrastructure.qa_repository import PgQaRepository

    a = sync_api
    state = build_partial_revocation_conflict(a.auth.pg, a.owner, a.classroom)
    document_id = state['document_id']
    with a.auth.pg.db.transaction(a.owner) as tx:
        review = PgReviewRepository(tx)
        hidden_id = review.document(UUID(document_id))['latest_personal_revision_id']
        PgQaRepository(tx).revoke_source(UUID(state['revoked_source_id']))
    assert a.client.get(BASE + '/review-revisions/' + hidden_id).status_code == 404
    visible_id = state['unaffected_revision_id']
    draft = operation(a, base=state['document_revision'], base_content_revision_id=visible_id,
                      body_markdown='Personal edit grounded only in the visible revision.')
    draft['object_id'] = document_id
    result = batch(a, [draft])
    assert result.status_code == 200, result.text
    accepted = result.json()['receipts'][0]
    assert accepted['status'] == 'applied' and accepted['object_id'] == document_id
    assert accepted['revision'] == state['document_revision'] + 1
    assert batch(a, [draft]).json()['receipts'] == [accepted]
    document = a.client.get(BASE + '/review-documents/' + document_id).json()
    current = a.client.get(BASE + '/review-revisions/' + document['latest_personal_revision_id']).json()
    assert current['parent_revision_id'] == visible_id and current['body_markdown'] == draft['payload']['body_markdown']
    assert a.client.get(BASE + '/review-revisions/' + hidden_id).status_code == 404


@pytest.mark.parametrize('bad_field,bad_value',[('name','   '),('name','bad\x00name'),('teacher','bad\x00teacher')])
def test_domain_invalid_course_does_not_rollback_other_batch_items(sync_api,bad_field,bad_value):
    a=sync_api
    valid=operation(a,kind='course',name='first valid name')
    invalid=operation(a,kind='course',base=2,**{bad_field:bad_value})
    following=operation(a,kind='course',base=2,name='following valid name')
    response=batch(a,[valid,invalid,following])
    assert response.status_code==200,response.text
    assert [item['status'] for item in response.json()['receipts']]==['applied','rejected','applied']
    with a.auth.pg.db.transaction(a.owner) as tx:
        current=tx.catalog.get_course(a.course.id)
        assert current.name=='following valid name' and current.revision==3


def test_mark_nul_is_rejected_at_normal_entry_without_partial_row(sync_api):
    a=sync_api
    body=dict(kind='important',note='bad\x00note',anchor=dict(kind='review_revision',document_id=a.note['id'],
        revision_id=a.note['latest_personal_revision_id'],content_sha256=hashlib.sha256(b'original').hexdigest()))
    response=post(a.browser,f'/class-sessions/{a.classroom.id}/marks',body,a.csrf)
    assert response.status_code==422,response.text
    assert a.client.get(BASE+f'/class-sessions/{a.classroom.id}/marks').json()['items']==[]


def test_source_linked_mark_without_review_gets_revocation_event(sync_api):
    from classroom_web.infrastructure.qa_repository import PgQaRepository
    a=sync_api
    with a.auth.pg.db.transaction(a.owner) as tx:
        classroom=tx.catalog.create_class_session(a.course.id,'Only a source and a mark')
        source=PgQaRepository(tx).add_source(classroom.id,kind='transcript_segment',object_id=uuid4(),
            text='Synthetic source for a standalone mark',start_ms=0,end_ms=1000)
    body=dict(kind='unclear',note='User private mark',anchor=dict(kind='source',source_ref=dict(
        kind=source.kind,id=str(source.source_id),revision_id=str(source.revision_id),content_sha256=source.content_sha256)))
    created=post(a.browser,f'/class-sessions/{classroom.id}/marks',body,a.csrf)
    assert created.status_code==201,created.text
    identity=created.json()['id']
    watermark=a.client.get(BASE+'/sync/bootstrap').json()['watermark']
    with a.auth.pg.db.transaction(a.owner) as tx:
        PgQaRepository(tx).revoke_source(source.source_id)
    changed=a.client.get(BASE+'/sync/changes',params=dict(after=watermark))
    assert changed.status_code==200,changed.text
    assert any(item['object_type']=='mark' and item['object_id']==identity and item['deleted'] for item in changed.json()['items'])
    assert a.client.get(BASE+'/marks/'+identity).status_code==404
    assert identity not in {item['object_id'] for item in a.client.get(BASE+'/sync/bootstrap').json()['projections']}


@pytest.mark.parametrize('revocation', ['none', 'committed', 'concurrent'])
def test_legacy_replay_never_substitutes_later_visible_revision(sync_api, revocation):
    from classroom_web.infrastructure.qa_repository import PgQaRepository
    from test_pr07b_versions import generate, output, prepare_review, REQUEST

    a = sync_api
    first_source, _, _, initial = generate(a.auth.pg, SimpleNamespace(a=a.owner), a)
    document_id = UUID(initial['summary_document_id'])
    first_candidate = initial['revision_ids'][0]
    with a.auth.pg.db.transaction(a.owner) as tx:
        original = PgReviewRepository(tx).document(document_id)
        second_source = PgQaRepository(tx).add_source(a.classroom.id, kind='transcript_segment',
            object_id=uuid4(), text='Independently revocable source', start_ms=6000, end_ms=8000)
    request = dict(REQUEST, target_document_id=str(document_id), base_revision=original['revision'])
    plan, task = prepare_review(a.auth.pg, a.owner, a, request=request)
    aliases = {item['source_id']: item['alias'] for item in plan['source_snapshot']['items']}
    assert set(aliases) == {str(first_source.source_id), str(second_source.source_id)}
    structured = output(alias=aliases[str(second_source.source_id)], answer='Frozen historical candidate.')
    op = operation(a, body_markdown='Historical local branch uses only source A.')
    op.update(object_id=str(document_id), base_revision=original['revision'])
    op['payload'].update(base_revision=original['revision'], base_content_revision_id=first_candidate)
    key = UUID(op['op_id'])
    with a.auth.pg.db.transaction(a.owner) as tx:
        review = PgReviewRepository(tx)
        candidate = review.publish(task, structured, UUID(plan['source_snapshot']['id']), request)
        conflict = review.write_personal(document_id, op['payload'], key)
        latest = review.write_personal(document_id, dict(op['payload'], base_revision=conflict['current_revision'],
            draft_id=str(uuid4()), body_markdown='Later visible cloud uses source A.'), uuid4())
    cloud_id = candidate['revision_ids'][0]
    conflict_id = UUID(conflict['conflict_id'])
    original_path = BASE + '/operations/' + str(key)
    detail_path = BASE + '/sync/conflicts/' + str(conflict_id)
    with a.auth.pg.admin.begin() as connection:
        assert connection.scalar(sa.text('SELECT current_revision_id FROM review_conflicts WHERE id=:id'),
                                  dict(id=conflict_id)) is None
        # Model the pre-08b storage boundary. The actual 0013-to-0014 HTTPS gate
        # exercises the same operations without this fixture-only removal.
        connection.exec_driver_sql('ALTER TABLE sync_conflicts DISABLE TRIGGER sync_conflicts_immutable')
        assert connection.execute(sa.text('DELETE FROM sync_conflicts WHERE id=:id'), dict(id=conflict_id)).rowcount == 1
        connection.exec_driver_sql('ALTER TABLE sync_conflicts ENABLE TRIGGER sync_conflicts_immutable')
    if revocation == 'committed':
        with a.auth.pg.db.transaction(a.owner) as tx:
            PgQaRepository(tx).revoke_source(second_source.source_id)
    historical = a.client.get(BASE + '/review-revisions/' + cloud_id)
    assert historical.status_code == (404 if revocation == 'committed' else 200)
    for identity in (latest['id'], conflict['submitted_revision']['id']):
        assert a.client.get(BASE + '/review-revisions/' + identity).status_code == 200
    original_receipt = a.client.get(original_path, params=dict(operation='writePersonalRevision')).json()
    assert original_receipt['conflict_id'] == str(conflict_id)
    assert a.client.get(detail_path).status_code == 409

    wrong_base = original['revision'] + 1
    changed = dict(op, base_revision=wrong_base, payload=dict(op['payload'], base_revision=wrong_base))
    assert batch(a, [changed]).status_code == 409
    if revocation == 'concurrent':
        revoker_ready, replay_ready, allow_commit = Event(), Event(), Event()
        sessions = {}

        def revoke():
            with a.auth.pg.db.transaction(a.owner) as tx:
                sessions['revoker'] = tx.connection.scalar(sa.text('SELECT pg_backend_pid()'))
                PgQaRepository(tx).revoke_source(second_source.source_id)
                revoker_ready.set()
                assert allow_commit.wait(10)

        def replay():
            with a.auth.pg.db.transaction(a.owner) as tx:
                sessions['replay'] = tx.connection.scalar(sa.text('SELECT pg_backend_pid()'))
                replay_ready.set()
                return PgSyncRepository(tx).apply(op)

        with ThreadPoolExecutor(max_workers=2) as pool:
            revoker = pool.submit(revoke)
            try:
                assert revoker_ready.wait(5)
                replayer = pool.submit(replay)
                assert replay_ready.wait(5)
                deadline = monotonic() + 4
                while True:
                    with a.auth.pg.admin.connect() as monitor:
                        waits = monitor.scalar(sa.text('SELECT :revoker=ANY(pg_blocking_pids(:replay))'), sessions)
                    if waits:
                        break
                    assert monotonic() < deadline, 'replay did not serialize with revocation'
                    sleep(.01)
            finally:
                allow_commit.set()
            revoker.result(timeout=10)
            assert replayer.result(timeout=10)['conflict_id'] == str(conflict_id)
    first = batch(a, [op])
    assert first.status_code == 200, first.text
    receipt_value = first.json()['receipts'][0]
    assert receipt_value['status'] == 'conflict'
    assert receipt_value['conflict_id'] == str(conflict_id)
    assert receipt_value['request_sha256'] == _hash(op)
    assert batch(a, [op]).json()['receipts'] == [receipt_value]
    assert a.client.get(original_path, params=dict(operation='writePersonalRevision')).json() == original_receipt

    detail = a.client.get(detail_path)
    with a.auth.pg.admin.connect() as connection:
        saved_id = connection.scalar(sa.text("SELECT server_content->>'id' FROM sync_conflicts WHERE id=:id"),
                                     dict(id=conflict_id))
        assert connection.scalar(sa.text('SELECT count(*) FROM review_conflicts WHERE document_id=:id'),
                                 dict(id=document_id)) == 1
    if revocation == 'none':
        assert saved_id == cloud_id
    else:
        assert (saved_id, detail.status_code) == (None, 404)
        # Re-expose only this synthetic source in the isolated database to
        # prove access is checked afresh. Product revocation stays monotonic.
        with a.auth.pg.admin.begin() as connection:
            connection.exec_driver_sql('SET CONSTRAINTS ALL IMMEDIATE')
            connection.exec_driver_sql('ALTER TABLE qa_sources DISABLE TRIGGER qa_source_guard')
            connection.execute(sa.text('UPDATE qa_sources SET revoked_at=NULL WHERE id=:id'),
                               dict(id=second_source.source_id))
            connection.exec_driver_sql('ALTER TABLE qa_sources ENABLE TRIGGER qa_source_guard')
        assert batch(a, [op]).json()['receipts'] == [receipt_value]
        historical = a.client.get(BASE + '/review-revisions/' + cloud_id)
        assert historical.status_code == 200
        detail = a.client.get(detail_path)
    assert detail.status_code == 200, detail.text
    assert detail.json()['server_content'] == historical.json()
    assert detail.json()['server_revision'] == conflict['current_revision']
    assert detail.json()['local_operation'] == op
    if revocation == 'none':
        # Preserve the original identity even if a previous deployment had
        # persisted the wrong comparison. That immutable row must stay hidden.
        with a.auth.pg.admin.begin() as connection:
            connection.exec_driver_sql('ALTER TABLE sync_conflicts DISABLE TRIGGER sync_conflicts_immutable')
            connection.execute(sa.text('''UPDATE sync_conflicts SET server_content=CAST(:cloud AS jsonb)
                WHERE id=:id'''), dict(cloud=json.dumps(latest), id=conflict_id))
            connection.exec_driver_sql('ALTER TABLE sync_conflicts ENABLE TRIGGER sync_conflicts_immutable')
        assert a.client.get(detail_path).status_code == 404
        assert batch(a, [op]).json()['receipts'] == [receipt_value]
    else:
        with a.auth.pg.db.transaction(a.owner) as tx:
            tx.tombstones.revoke(Resource.CLASS_SESSION, a.classroom.id, a.classroom.revision, 1)
        assert a.client.get(detail_path).status_code == 404
        assert batch(a, [op]).json()['receipts'][0]['status'] == 'deleted'


def test_unadopted_multiple_candidates_freeze_actual_latest_cloud_candidate(sync_api):
    from test_pr07b_versions import generate,prepare_review,REQUEST,output
    a=sync_api
    _,_,_,generated=generate(a.auth.pg,SimpleNamespace(a=a.owner),a)
    identity=UUID(generated['summary_document_id'])
    with a.auth.pg.db.transaction(a.owner) as tx:
        original=PgReviewRepository(tx).document(identity)
    request=dict(REQUEST,target_document_id=str(identity),base_revision=original['revision'])
    plan,task=prepare_review(a.auth.pg,a.owner,a,request=request)
    with a.auth.pg.db.transaction(a.owner) as tx:
        repo=PgReviewRepository(tx)
        newer=repo.publish(task,output(answer='A second separately preserved candidate.'),UUID(plan['source_snapshot']['id']),request)
    draft=dict(base_revision=original['revision'],base_content_revision_id=original['candidate_revision_ids'][0],
               draft_id=str(uuid4()),body_markdown='Personal edit based on old candidate',personal_note='')
    with a.auth.pg.db.transaction(a.owner) as tx:
        conflict=PgReviewRepository(tx).write_personal(identity,draft,uuid4())
    response=a.client.get(BASE+'/sync/conflicts/'+conflict['conflict_id'])
    assert response.status_code==200,response.text
    assert response.json()['server_content']['id']==newer['revision_ids'][0]
    assert response.json()['server_content']['id']!=draft['base_content_revision_id']
    assert response.json()['server_content']['structured_content']==output(answer='A second separately preserved candidate.')
    assert response.json()['server_content']['generation_task_id']==str(task.id)
