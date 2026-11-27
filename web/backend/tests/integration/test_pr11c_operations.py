"""PR-11c real browser sessions, scoped reads, and independent deletion replay."""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib.util
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
import pytest
import sqlalchemy as sa
from starlette.responses import PlainTextResponse
from uvicorn.protocols.utils import get_path_with_query_string

from classroom_web.application.course_read import CourseReader
from classroom_web.application.deletion import DeletionWorker, RestoreCoordinator
from classroom_web.application.operations import OperationsService
from classroom_web.application.review_browser import ReviewBrowser
from classroom_web.application.review_worker import ReviewWorker
from classroom_web.application.sync_browser import SyncBrowser
from classroom_web.domain.data import OwnerContext
from classroom_web.entrypoints.http import create_app
from classroom_web.entrypoints.deletion_http import DeletionGate
from classroom_web.entrypoints.media_http import MediaRequestLifecycle
from classroom_web.entrypoints.operations_http import ROUTES, OperationsRoutes, SearchQueryRedaction
from classroom_web.infrastructure.qa_repository import PgQaRepository
from classroom_web.infrastructure.review_provider import FakeReviewProvider
from classroom_web.infrastructure.review_repository import PgReviewRepository
from classroom_web.infrastructure.sync_repository import PgSyncRepository
from conftest import ROOT, migrated_database
from deletion_support import POLICY, auth, contract, d, deploy, new_user, services
from test_authentication import BASE, login, post
from test_billing_integration import fee_control
from test_capture_recovery import acquire, capture, prepare, put
from test_deletion_lifecycle import delete
from test_deletion_restore import Restored, backup
from test_filetrans import filetrans, source, versions_for
from test_persistent_tasks import scheduler
from classroom_web.infrastructure.review_generation import PgReviewGenerationRepository
from test_pr07a_sources import CONFIG, confirmation
from test_pr07b_versions import REQUEST, output, prepare_review, seeded
from test_realtime_asr import create, finish, live, run


# Use real deletion services for all route fixtures. The guest runner supplies
# PostgreSQL, two SeaweedFS processes and the PostgreSQL client tools.
@pytest.fixture(scope='module')
def pg():
    with migrated_database() as database:
        yield database


def review_contract():
    path = ROOT / 'tools/pr07b/contract_candidate.py'
    spec = importlib.util.spec_from_file_location('pr11c_review_contract', path)
    overlay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(overlay)
    base = contract()
    frozen = {route: base['paths'][route] for route in ('/search', '/feedback', '/notifications',
                                                        '/notifications/{notification_id}/read')}
    result = overlay.build(base)
    assert {route: result['paths'][route] for route in frozen} == frozen
    return result


@contextmanager
def browser(m, *, actor=None, deletion=None, db=None, live_readers=False, sync_reads=False,
            sign_in=True, app_wrapper=None):
    actor, db = actor or m.a, db or m.pg.db
    db.cursor_secret = b'pr11c-test-only-stable-cursor-secret'
    service = OperationsService(auth=actor.service, database=db)
    review = ReviewBrowser(auth=actor.service, database=db, config={'enabled': True})
    readers = dict(capture=m.capture, asr=m.asr, filetrans=m.ftrans) if live_readers else {}
    sync = SyncBrowser(auth=actor.service, database=db, enabled=True) if sync_reads else None
    app = create_app(auth=actor.service, contract=review_contract(), origin='https://testserver',
                     business=CourseReader(auth=actor.service, database=db), review=review,
                     deletion=deletion or m.deletion, operations=service, sync=sync,
                     websocket_admission=getattr(actor, 'ws_admission', None), **readers)
    with TestClient(app_wrapper(app) if app_wrapper else app, base_url='https://testserver') as client:
        member = SimpleNamespace(client=client, email=actor.email, csrf=None)
        if sign_in:
            member.csrf = login(member)['csrf_token']
        yield member


def result(reply, status=200):
    assert reply.status_code == status, reply.text
    assert reply.headers['cache-control'] == 'no-store'
    return reply.json()


def search(member, query, **params):
    return member.client.get(BASE + '/search', params={'q': query, **params})


def notice_list(member):
    return result(member.client.get(BASE + '/notifications'))['items']


def mark(member, notification_id, *, csrf=None, key=None):
    return member.client.post(BASE + f'/notifications/{notification_id}/read', headers={
        'Origin': 'https://testserver', 'Sec-Fetch-Site': 'same-origin',
        'X-CSRF-Token': member.csrf if csrf is None else csrf, 'Idempotency-Key': str(key or uuid4()),
    }, json={'read': True})


def note(tx, classroom, content, title='Synthetic note'):
    return PgReviewRepository(tx).create_note(dict(class_session_id=str(classroom.id), title=title,
        body_markdown=content, draft_id=str(uuid4())), uuid4())


@pytest.mark.parametrize('mount', ['', '/pilot'])
def test_search_redacts_success_and_failure_in_access_logs(d, caplog, monkeypatch, mount):
    marker = 'PR11C_PRIVATE_SEARCH_SENTINEL'
    logger = logging.getLogger('uvicorn.access')
    caplog.set_level(logging.INFO, logger=logger.name)
    with d.pg.db.transaction(OwnerContext(d.uid)) as tx:
        tx.catalog.create_course(d.semester.id, marker + mount.strip('/'))

    def capture_access(app):
        async def wrapped(scope, receive, send):
            async def log_response(message):
                if message['type'] == 'http.response.start':
                    logger.info('%s %s %d', scope['method'], get_path_with_query_string(scope), message['status'])
                await send(message)
            await app(scope, receive, log_response)
        return wrapped

    def mounted(app):
        # An ASGI server behind a mount sends root_path and a path that includes it.
        async def wrapped(scope, receive, send):
            if mount and scope['type'] in ('http', 'websocket'):
                scope = dict(scope, root_path=mount, path=mount + scope['path'])
            await app(scope, receive, send)
        return wrapped

    with browser(d, app_wrapper=lambda app: mounted(capture_access(app))) as member:
        assert result(search(member, marker + mount.strip('/')))['items'][0]['title'] == marker + mount.strip('/')
        assert result(search(member, marker, limit=101), 422)['error']['code'] == 'VALIDATION_FAILED'
        with monkeypatch.context() as changes:
            changes.setattr(d.deletion, 'ready', False)
            assert result(search(member, marker), 503)['error']['code'] == 'SERVICE_DISABLED'
    access_lines = [record.getMessage() for record in caplog.records if record.name == logger.name]
    assert [line for line in access_lines if line.startswith(f'GET {mount}{BASE}/search')] == [
        f'GET {mount}{BASE}/search {status}' for status in (200, 422, 503)]
    assert all(marker not in line for line in access_lines)


@pytest.mark.parametrize('root,path', [('', BASE + '/search'), ('/pilot', '/pilot' + BASE + '/search'),
                                       ('/pilot', BASE + '/search'), ('/pilot/', '/pilot' + BASE + '/search/')])
def test_search_redaction_matches_the_route_under_either_mount_convention(root, path):
    sentinel = b'q=PR11C_MOUNT_SENTINEL'
    seen, logged = {}, []

    async def handler(scope, receive, send):
        seen['handler'] = scope['query_string']
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b''})

    scope = {'type': 'http', 'path': path, 'root_path': root, 'query_string': sentinel}

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        if message['type'] == 'http.response.start':
            logged.append(scope['query_string'])

    asyncio.run(SearchQueryRedaction(handler, path=BASE + '/search')(scope, receive, send))
    assert seen['handler'] == sentinel
    assert logged == [b''] and scope['query_string'] == b''


MOUNTS = [('', ''), ('/pilot', '/pilot'), ('/pilot', '')]  # (root_path, prefix sent in path)


def drive(middleware, scope):
    sent = []

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        if message['type'] == 'http.response.start':
            sent.append((message['status'], scope.get('query_string')))

    asyncio.run(middleware(scope, receive, send))
    return sent


@pytest.mark.parametrize('root,sent_prefix', MOUNTS)
def test_deletion_gate_closes_api_routes_under_either_mount_convention(root, sent_prefix):
    reached = []

    async def app(scope, receive, send):
        reached.append(scope['path'])
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b''})

    def failure(error, request_id):
        return PlainTextResponse(error.code, status_code=error.status)

    closed = DeletionGate(app, deletion=SimpleNamespace(ready=False), failure=failure, prefix=BASE)
    for route in ('/search', '/notifications', '/media/assets/x', '/sync/bootstrap'):
        scope = {'type': 'http', 'method': 'GET', 'path': sent_prefix + BASE + route, 'root_path': root,
                 'query_string': b'', 'headers': []}
        assert drive(closed, scope) == [(503, b'')]
    assert reached == []
    opened = DeletionGate(app, deletion=SimpleNamespace(ready=True), failure=failure, prefix=BASE)
    assert drive(opened, {'type': 'http', 'method': 'GET', 'path': sent_prefix + BASE + '/search',
                          'root_path': root, 'query_string': b'', 'headers': []}) == [(200, b'')]


@pytest.mark.parametrize('root,sent_prefix', MOUNTS)
def test_media_admission_and_query_redaction_apply_under_either_mount_convention(root, sent_prefix):
    sentinel = b'ticket=PR11C_MEDIA_MOUNT_SENTINEL'
    slots, handled = threading.Semaphore(1), []

    async def app(scope, receive, send):
        handled.append(scope['query_string'])
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b''})

    def failure(error, request_id):
        return PlainTextResponse(error.code, status_code=error.status)

    lifecycle = MediaRequestLifecycle(app, slots=slots, failure=failure)
    scope = {'type': 'http', 'method': 'GET', 'path': sent_prefix + BASE + '/media/assets/x',
             'root_path': root, 'query_string': sentinel, 'headers': []}
    assert drive(lifecycle, scope) == [(200, b'')]
    assert handled == [sentinel] and scope['query_string'] == b''
    # The admission slot is enforced for mounted requests too: with it taken, the request is refused.
    assert slots.acquire(False)
    try:
        refused = {'type': 'http', 'method': 'GET', 'path': sent_prefix + BASE + '/media/assets/x',
                   'root_path': root, 'query_string': sentinel, 'headers': []}
        assert drive(lifecycle, refused) == [(429, b'')]
        assert handled == [sentinel]
    finally:
        slots.release()


@pytest.mark.parametrize('mount', ['', '/pilot'])
def test_feedback_body_limit_follows_the_route_under_a_mount_prefix(d, mount):
    def mounted(app):
        async def wrapped(scope, receive, send):
            if mount and scope['type'] in ('http', 'websocket'):
                scope = dict(scope, root_path=mount, path=mount + scope['path'])
            await app(scope, receive, send)
        return wrapped

    text = '合成反馈' * 1250  # 5000 characters, the contract maximum for both fields
    body = dict(category='other', build_id='synthetic-mounted', description=text, request_id=None,
                confirmed_excerpt=text, confirm_send_excerpt=True)
    assert len(json.dumps(body, ensure_ascii=False).encode()) > 16384
    with browser(d, app_wrapper=mounted) as member:
        assert result(post(member, '/feedback', body, member.csrf), 202)['accepted'] is True


@pytest.mark.parametrize('version,accepted', [('0.3.0', True), ('0.4.0', True), ('0.2.1', False), ('0.5.0', False)])
def test_operations_accept_only_contract_versions_with_identical_operations(version, accepted):
    auth = object()
    database = SimpleNamespace(cursor_secret=b'synthetic-cursor-secret')
    service = SimpleNamespace(auth=auth, database=database)
    deletion = SimpleNamespace(auth=auth, database=database)
    frozen = contract()
    candidate = {**frozen, 'info': {**frozen['info'], 'version': version}}

    def build():
        return OperationsRoutes(service, auth, candidate, deletion, None, None, None, None)

    if not accepted:
        with pytest.raises(ValueError):
            build()
        return
    routes = build()
    registered = []

    class App:
        def add_api_route(self, path, endpoint, methods, operation_id):
            registered.append((path, methods[0], operation_id))

    routes.register(App(), BASE, lambda path, method, operation: None)
    assert sorted(registered) == sorted((BASE + path, method.upper(), operation_id)
                                        for path, (method, operation_id) in ROUTES.items())


def test_search_isolated_readable_literal_paged_and_deleted(d):
    marker = '甲课堂%_独有字号'
    owner = OwnerContext(d.uid)
    other = new_user(d)
    with d.pg.db.transaction(owner) as tx:
        course = tx.catalog.create_course(d.semester.id, marker + '课程')
        classroom = tx.catalog.create_class_session(course.id, 'Synthetic match classroom')
        personal = note(tx, classroom, marker + '过期修订独有正文')
        repo = PgReviewRepository(tx)
        latest = repo.write_personal(UUID(personal['id']),
            dict(base_revision=personal['revision'], base_content_revision_id=personal['latest_personal_revision_id'],
                 draft_id=str(uuid4()), body_markdown=marker + '最新', personal_note=''), uuid4())
        note(tx, classroom, '甲课堂ZZ独有字号')
    before = billing_snapshot(d)
    outsider = SimpleNamespace(email=other.email, service=d.a.service)
    with browser(d) as a, browser(d, actor=outsider) as b:
        first = result(search(a, marker, limit=1))
        cursor = first['next_cursor']
        assert cursor and first['items'][0]['object_type'] == 'course'
        course_dto = result(a.client.get(BASE + '/courses/' + str(course.id)))
        assert course_dto['name'] == marker + '课程'
        second = result(search(a, marker, limit=1, cursor=cursor))
        hit, = second['items']
        assert (hit['object_type'], hit['object_id']) == ('review_document', personal['id'])
        body = result(a.client.get(BASE + '/review-revisions/' + hit['revision_id']))
        assert body['document_id'] == personal['id'] and body['body_markdown'] == marker + '最新'
        assert hit['revision_id'] == latest['id']
        assert second['next_cursor'] is None
        assert result(search(a, marker, class_session_id=classroom.id))['items']
        assert result(search(b, marker))['items'] == []
        assert result(search(a, '过期修订独有正文'))['items'] == []
        for field, target in [('course_id', course.id), ('class_session_id', classroom.id)]:
            assert result(search(b, marker, **{field: target}), 404)['error']['code'] == 'NOT_FOUND'
        for member, term, token in [(b, marker, cursor), (a, '变更查询', cursor), (a, marker, cursor + 'X')]:
            assert result(search(member, term, limit=1, cursor=token), 422)['error']['code'] == 'VALIDATION_FAILED'
        assert result(search(a, '甲'))['items']
        assert result(search(a, ' ', limit=101), 422)['error']['code'] == 'VALIDATION_FAILED'
        assert billing_snapshot(d) == before

        delete(d, 'class_session', classroom.id)
        assert result(a.client.get(BASE + '/review-revisions/' + hit['revision_id']), 404)
        assert result(search(a, marker, limit=1, cursor=cursor))['items'] == []
        hidden = search(a, marker, class_session_id=classroom.id)
        assert result(hidden, 404)['error']['code'] == 'NOT_FOUND' and marker not in hidden.text
        delete(d, 'course', course.id)
        gone = search(a, marker)
        assert result(gone)['items'] == [] and marker not in gone.text


def billing_snapshot(m):
    # Billing ledgers exist only when the PR-11a schema is installed; the task
    # ledger is always present and records every model-backed attempt.
    with m.pg.admin.connect() as conn:
        tables = ['tasks', 'task_attempts'] + [name for name in ('quota_reservations', 'usage_receipts')
                                                if conn.execute(sa.text('SELECT to_regclass(:name)'),
                                                                {'name': 'public.' + name}).scalar() is not None]
        return {name: conn.execute(sa.text(f'SELECT count(*) FROM {name} WHERE owner_id=:owner'),
                                   {'owner': m.uid}).scalar_one() for name in tables}


def test_review_candidate_adoption_and_source_revocation_match_authorized_reads(d):
    owner = OwnerContext(d.uid)
    tree = SimpleNamespace(classroom=d.classroom)
    source_row = seeded(d.pg, owner, tree)
    plan, task = prepare_review(d.pg, owner, tree)
    payload = output()
    payload['notes'][0]['supplement'] = 'Supplement only in note'
    payload['cards'][0].update(supplement='Supplement only in card', english='English term only',
                               english_meaning='English meaning only')
    with d.pg.db.transaction(owner) as tx:
        generated = PgReviewRepository(tx).publish(task, payload, UUID(plan['source_snapshot']['id']), REQUEST)
    # This fixture publishes candidate rows directly. Keep its queued task out
    # of the separate completion test's real worker claim.
    with d.pg.admin.begin() as conn:
        conn.execute(sa.text("UPDATE tasks SET not_before=clock_timestamp()+interval '1 day' WHERE id=:id"),
                     {'id': task.id})
    revision, card_revision = generated['revision_ids']
    document, card = generated['document_ids']
    fields = ('Supplement only in note', 'Supplement only in card', 'English term only', 'English meaning only')
    with browser(d) as member:
        authorized = result(member.client.get(BASE + '/review-revisions/' + revision))
        query = 'product of mass'
        assert query in authorized['body_markdown']
        assert all(not result(search(member, term))['items'] for term in (query, *fields))
        for target, version_id in ((document, revision), (card, card_revision)):
            current = result(member.client.get(BASE + '/review-documents/' + target))
            result(post(member, f'/review-documents/{target}/adopt', {
                'base_revision': current['revision'], 'revision_id': version_id,
            }, member.csrf))
        assert any(hit['revision_id'] == revision for hit in result(search(member, query))['items'])
        for term, kind, version_id in zip(fields, ('review_document', 'card', 'card', 'card'),
                                          (revision, card_revision, card_revision, card_revision)):
            match = next(hit for hit in result(search(member, term))['items'] if hit['revision_id'] == version_id)
            assert match['object_type'] == kind and match['snippet'] == term
        with d.pg.db.transaction(owner) as tx:
            PgQaRepository(tx).revoke_source(source_row.source_id)
        assert result(member.client.get(BASE + '/review-revisions/' + revision), 404)
        assert all(not result(search(member, term))['items'] for term in (query, *fields))


def test_transcript_hit_is_formal_and_matches_the_authorized_segment(filetrans):
    m = filetrans
    lease, _ = source(m)
    stream = result(create(m, lease), 201)
    assert finish(m, stream).status_code == 200
    run(m)
    version_info, = versions_for(m)
    before = billing_snapshot(m)
    with browser(m, live_readers=True) as member:
        class_dto = result(member.client.get(BASE + '/class-sessions/' + str(m.classroom.id)))
        assert result(search(member, class_dto['title']))['items']
        segments = result(member.client.get(BASE + f"/transcript-versions/{version_info['id']}/segments"))['items']
        assert segments
        segment = segments[0]
        hits = result(search(member, segment['text'][:16], class_session_id=m.classroom.id))['items']
        selected = next(hit for hit in hits if hit['object_type'] == 'transcript_segment')
        assert selected['revision_id'] == segment['segment_revision_id']
        assert selected['locator'] == f"transcript/{m.classroom.id}/{version_info['id']}/{segment['id']}"
        assert billing_snapshot(m) == before


def test_restore_gate_replays_ledger_before_search_opens(d, tmp_path):
    marker = '合成备份删除标记'
    erased = new_user(d)
    feedback = dict(category='other', build_id='synthetic-restore', description='Synthetic restore feedback',
                    request_id=None, confirmed_excerpt='Synthetic surviving excerpt', confirm_send_excerpt=True)
    with d.pg.db.transaction(OwnerContext(d.uid)) as tx:
        note(tx, d.classroom, marker)
    with browser(d) as member:
        assert result(search(member, marker))['items']
        kept_report = UUID(result(post(member, '/feedback', feedback, member.csrf), 202)['request_id'])
    with browser(d, actor=SimpleNamespace(email=erased.email, service=d.a.service)) as member:
        erased_report = UUID(result(post(member, '/feedback',
            feedback | {'confirmed_excerpt': 'Synthetic erased excerpt'}, member.csrf), 202)['request_id'])

    def visible_reports(database):
        answers = []
        for role, view, columns in (
            ('classroom_web_feedback_projection', 'feedback_admin_metadata', 'id'),
            ('classroom_web_feedback_projection', 'feedback_authorized_excerpts', 'id, confirmed_excerpt'),
        ):
            with database.begin() as conn:
                conn.exec_driver_sql(f'SET LOCAL ROLE {role}')
                answers.append(set(conn.execute(sa.text(f'SELECT {columns} FROM {view} WHERE id IN (:a,:b)'),
                                                {'a': kept_report, 'b': erased_report}).all()))
        return answers

    assert visible_reports(d.pg.admin) == [
        {(kept_report,), (erased_report,)},
        {(kept_report, 'Synthetic surviving excerpt'), (erased_report, 'Synthetic erased excerpt')},
    ]
    archive = backup(d, tmp_path / 'pr11c-backup.dump')
    delete(d, 'class_session', d.classroom.id)
    d.deletion.erase_account(OwnerContext(erased.uid))
    restored = Restored(d, archive)
    try:
        assert visible_reports(restored.admin) == [set(), set()]
        closed = restored.build(reconcile=False)
        with browser(d, actor=restored.a, deletion=closed.deletion, db=restored.db, sign_in=False) as member:
            assert result(search(member, marker), 503)['error']['code'] == 'SERVICE_DISABLED'
            worker = DeletionWorker(queue=restored.queue, store=d.store, policy=POLICY, worker_id='pr11c-restore')
            coordinator = RestoreCoordinator(service=closed.deletion, operator=restored.operator, worker=worker)
            report = coordinator.run(ledger_witness=d.deploy.ledger.head(), revoked_owners=[])
            assert report['ready']
            assert visible_reports(restored.admin) == [{(kept_report,)}, {(kept_report, 'Synthetic surviving excerpt')}]
            member.csrf = login(member)['csrf_token']
            reply = search(member, marker)
            assert result(reply)['items'] == [] and marker not in reply.text
    finally:
        restored.close()


def emit_task(pg, owner, task):
    with pg.db.transaction(OwnerContext(owner)) as tx:
        tx.events.task_changed(SimpleNamespace(id=task, class_session_id=None, revision=1,
            state='succeeded', error_code=None, may_have_charged=False))


def replay(member, cursor):
    body = {'channel': 'events', 'class_session_id': None, 'capture_epoch_id': None, 'fence_token': None}
    ticket = result(post(member, '/ws-tickets', body, member.csrf), 201)['ticket']
    with member.client.websocket_connect('wss://testserver' + BASE + '/ws/events',
            subprotocols=['classroom.v1'], headers={'Origin': 'https://testserver'}) as ws:
        ws.send_json({'type': 'authenticate', 'ticket': ticket})
        ws.send_json({'type': 'resume.after', 'cursor': cursor})
        messages = []
        for _ in range(10):
            event = ws.receive_json()
            if event['type'] == 'replay.complete':
                return messages
            messages.append(event)
    pytest.fail('Expected replay.complete')


def test_notifications_dedupe_concurrent_events_and_resolve_only_after_conflict_resolution(d):
    d.pg.db.cursor_secret = b'pr11c-test-only-stable-cursor-secret'
    with d.pg.db.transaction(OwnerContext(d.uid)) as tx:
        before = tx.events.watermark()
        task = tx.tasks.enqueue('export', {'schema_version': 1}, op_id=uuid4()).id
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(emit_task, d.pg, d.uid, task) for _ in range(2)]
        for job in jobs:
            job.result(timeout=20)
    emit_task(d.pg, d.uid, task)
    def edit(name):
        return dict(op_id=str(uuid4()), object_type='course', object_id=str(d.course.id),
                    draft_id=str(uuid4()), base_revision=1, payload=dict(base_revision=1, name=name))

    with d.pg.db.transaction(OwnerContext(d.uid)) as tx:
        sync = PgSyncRepository(tx)
        assert sync.apply(edit('Current course edit'))['status'] == 'applied'
        conflict = sync.apply(edit('Conflicting course edit'))
        assert conflict['status'] == 'conflict'
    other = new_user(d)
    with browser(d, sync_reads=True) as a, browser(d, actor=SimpleNamespace(email=other.email, service=d.a.service)) as b:
        listed = notice_list(a)
        assert len([item for item in listed if item['object_id'] == str(task)]) == 1
        fault, = [item for item in listed if item['kind'] == 'conflict']
        assert fault['object_id'] == conflict['conflict_id']
        conflict_path = f"/sync/conflicts/{fault['object_id']}"
        opened = result(a.client.get(BASE + conflict_path))
        assert opened['id'] == fault['object_id'] and opened['object_id'] == str(d.course.id)
        assert notice_list(b) == []
        denied = result(mark(b, fault['id']), 404)
        missing = result(mark(b, uuid4()), 404)
        assert denied['error'] | {'request_id': None} == missing['error'] | {'request_id': None}
        assert result(mark(a, fault['id'], csrf='invalid'), 403)['error']['code'] == 'FORBIDDEN'
        key = uuid4()
        seen = result(mark(a, fault['id'], key=key))
        assert seen['read_at'] and seen['resolved_at'] is None
        assert result(mark(a, fault['id'], key=key)) == seen
        assert seen in notice_list(a)
        resolved = result(post(a, conflict_path + '/resolve',
                               {'resolution': 'use_server', 'base_revision': opened['server_revision']}, a.csrf))
        assert resolved['status'] == 'applied'
        with d.pg.db.transaction(OwnerContext(d.uid)) as tx:
            events = [event for event in tx.events.replay().events if event['type'] == 'notification.created']
        assert len(events) == 2
        schema = json.loads((ROOT / 'docs/web-alpha/architecture/contracts/events.schema.json').read_text(encoding='utf8'))
        schema['$ref'] = '#/$defs/NotificationCreated'
        for event in events:
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(event)
        assert replay(a, before) == events
        assert any(item['id'] == seen['id'] and item['read_at'] == seen['read_at']
                   and item['resolved_at'] for item in notice_list(a))
        delete(d, 'course', d.course.id)
        assert all(item['id'] != seen['id'] for item in notice_list(a))
        assert result(mark(a, seen['id'], key=key), 404)
        assert all(event['data']['notification_id'] != seen['id'] for event in replay(a, before))


def test_completion_notification_reauthorizes_revoked_snapshot_in_list_read_and_replay(d, scheduler):
    owner = OwnerContext(d.uid)
    tree = SimpleNamespace(classroom=d.classroom)
    source_row = seeded(d.pg, owner, tree)
    # The plan's destination must match the provider, or the worker fails it unsent
    # with PROVIDER_CONFIG_CHANGED (as the PR-07b recovery fixture configures it).
    config = dict(CONFIG, destination=dict(CONFIG['destination'], provider=FakeReviewProvider.provider,
                                           model=FakeReviewProvider.model))
    with d.pg.db.transaction(owner) as tx:
        repo = PgReviewGenerationRepository(tx)
        plan = repo.prepare(tree.classroom.id, REQUEST, uuid4(), config)
        task = repo.execute(UUID(plan['id']), confirmation(plan), uuid4(), config)
    with browser(d) as member:
        with d.pg.db.transaction(owner) as tx:
            cursor = tx.events.watermark()
        # Leave only this task claimable, as test_pr07b_versions isolates its review queue.
        with d.pg.admin.begin() as connection:
            connection.execute(sa.text("""UPDATE tasks SET not_before=clock_timestamp()+interval '365 days'
                WHERE id<>:task AND state='queued'"""), {'task': task.id})
        provider = FakeReviewProvider()
        worker = ReviewWorker(d.pg.db, scheduler, provider, worker_id='pr11c-completion', enabled=True)
        assert worker.run_once() and provider.calls == 1
        path = BASE + f'/tasks/{task.id}'
        assert result(member.client.get(path))['state'] == 'succeeded'
        notice, = [item for item in notice_list(member) if item['object_id'] == str(task.id)]
        assert any(event['data']['notification_id'] == notice['id'] for event in replay(member, cursor))
        with d.pg.db.transaction(owner) as tx:
            PgQaRepository(tx).revoke_source(source_row.source_id)
        assert result(member.client.get(path), 404)
        assert all(item['id'] != notice['id'] for item in notice_list(member))
        assert result(mark(member, notice['id']), 404)['error']['code'] == 'NOT_FOUND'
        assert all(event['data']['notification_id'] != notice['id'] for event in replay(member, cursor))


def test_capture_takeover_and_audio_expiry_notify_without_per_chunk_ack_noise(capture):
    m = capture
    lease = acquire(m)
    chunks = [prepare(m, lease, seq=seq) for seq in (0, 1)]
    for chunk in chunks:
        assert put(m, chunk).status_code == 200
    with browser(m) as member:
        assert notice_list(member) == []
        acquire(m, confirm_takeover=True, expected_lease_id=lease['id'])
        fault, = notice_list(member)
        assert fault['kind'] == 'capture_failure' and fault['resolved_at'] is None
        assert result(mark(member, fault['id']))['resolved_at'] is None
        with m.pg.admin.begin() as conn:
            conn.execute(sa.text("""UPDATE media_assets a SET expires_at=clock_timestamp()-interval '1 second'
                FROM audio_chunks c WHERE c.owner_id=:owner AND c.asset_id=a.id"""), {'owner': m.uid})
        assert m.deletion.expire_due() >= 2
        notices = notice_list(member)
        assert sum(item['kind'] == 'audio_expiry' for item in notices) == 1
        assert any(item['id'] == fault['id'] and item['read_at'] and item['resolved_at'] is None for item in notices)


# Reader roles have no direct view grant; the projection owner exercises the same
# row policy (admission, xmin, isolation, tombstones) that any future reader would.
FEEDBACK_VIEWS = (
    ('classroom_web_feedback_projection', 'feedback_admin_metadata'),
    ('classroom_web_feedback_projection', 'feedback_authorized_excerpts'),
)


def submit_synthetic_feedback(d):
    with browser(d) as a:
        return UUID(result(post(a, '/feedback', dict(category='other', build_id='synthetic-admission',
            description='Synthetic projection boundary', request_id=None, confirmed_excerpt='Synthetic excerpt',
            confirm_send_excerpt=True), a.csrf), 202)['request_id'])


def feedback_visible(conn, view, report_id):
    return conn.scalar(sa.text(f'SELECT 1 FROM {view} WHERE id=:id'), {'id': report_id}) == 1


def test_in_place_copy_of_feedback_admission_does_not_reopen_projections(d):
    report_id = submit_synthetic_feedback(d)
    for role, view in FEEDBACK_VIEWS:
        with d.pg.admin.begin() as conn:
            conn.exec_driver_sql(f'SET LOCAL ROLE {role}')
            assert feedback_visible(conn, view, report_id)

    with d.pg.admin.begin() as conn:
        # Replay all of the dumped columns in place. The database, table and
        # postmaster identities stay the same; only the tuple's xmin differs.
        conn.exec_driver_sql('''CREATE TEMP TABLE copied_admission ON COMMIT DROP AS
                                SELECT * FROM pr11c_feedback.deletion_admission''')
        conn.exec_driver_sql('SELECT public.classroom_web_feedback_admit(false)')
        inserted = conn.exec_driver_sql('INSERT INTO pr11c_feedback.deletion_admission SELECT * FROM copied_admission')
        assert inserted.rowcount == 1
    for role, view in FEEDBACK_VIEWS:
        with d.pg.admin.begin() as conn:
            conn.exec_driver_sql(f'SET LOCAL ROLE {role}')
            assert not feedback_visible(conn, view, report_id)

    d.deletion.reconcile()
    for role, view in FEEDBACK_VIEWS:
        with d.pg.admin.begin() as conn:
            conn.exec_driver_sql(f'SET LOCAL ROLE {role}')
            assert feedback_visible(conn, view, report_id)


@pytest.mark.parametrize('role,view', FEEDBACK_VIEWS)
def test_feedback_projection_requires_fresh_read_committed_statements(d, role, view):
    report_id = submit_synthetic_feedback(d)
    with (d.pg.admin.connect().execution_options(isolation_level='REPEATABLE READ') as old, old.begin(),
          d.pg.admin.connect().execution_options(isolation_level='READ COMMITTED') as fresh, fresh.begin()):
        old.exec_driver_sql(f'SET LOCAL ROLE {role}')
        fresh.exec_driver_sql(f'SET LOCAL ROLE {role}')
        assert not feedback_visible(old, view, report_id)
        assert feedback_visible(fresh, view, report_id)
        with d.pg.admin.begin() as writer:
            writer.exec_driver_sql('SELECT public.classroom_web_feedback_admit(false)')
        assert not feedback_visible(old, view, report_id)
        assert not feedback_visible(fresh, view, report_id)


def test_feedback_consent_roles_idempotency_and_no_private_text_in_logs(d, caplog):
    caplog.set_level(logging.INFO)
    excerpt, description = 'PR11C_EXCERPT_PRIVATE_SENTINEL', 'PR11C_DESCRIPTION_PRIVATE_SENTINEL'
    other = new_user(d)
    with browser(d) as a, browser(d, actor=SimpleNamespace(email=other.email, service=d.a.service)) as b:
        body = dict(category='bug', build_id='synthetic-test', description=description, request_id=str(other.uid),
                    confirmed_excerpt=excerpt, confirm_send_excerpt=False)
        invalid = post(a, '/feedback', body, a.csrf)
        assert result(invalid, 422)['error']['code'] == 'CONSENT_REQUIRED'
        assert excerpt not in invalid.text and description not in invalid.text
        assert result(post(a, '/feedback', body, 'invalid'), 403)
        key = str(uuid4())
        body['confirm_send_excerpt'] = True
        approved = result(post(a, '/feedback', body, a.csrf, **{'Idempotency-Key': key}), 202)
        assert approved == result(post(a, '/feedback', body, a.csrf, **{'Idempotency-Key': key}), 202)
        assert result(post(a, '/feedback', body | {'description': 'changed'}, a.csrf,
                           **{'Idempotency-Key': key}), 409)['error']['code'] == 'IDEMPOTENCY_MISMATCH'
        second = result(post(b, '/feedback', body | {'confirmed_excerpt': '', 'confirm_send_excerpt': False}, b.csrf,
                             **{'Idempotency-Key': key}), 202)
        assert approved != second
        assert b.client.get(BASE + '/feedback/' + approved['request_id']).status_code == 404
        assert not a.client.app.state.errors and not b.client.app.state.errors
    for role, sql in [('classroom_web_runtime', 'SELECT * FROM feedback_reports'),
                      ('classroom_web_runtime', 'SELECT * FROM feedback_admin_metadata'),
                      ('classroom_web_runtime', 'SELECT * FROM feedback_authorized_excerpts'),
                      ('classroom_web_feedback_admin', 'SELECT * FROM feedback_admin_metadata'),
                      ('classroom_web_feedback_admin', 'SELECT * FROM feedback_authorized_excerpts'),
                      ('classroom_web_feedback_admin', 'SELECT id FROM feedback_reports'),
                      ('classroom_web_feedback_excerpt', 'SELECT * FROM feedback_authorized_excerpts'),
                      ('classroom_web_feedback_excerpt', 'SELECT * FROM feedback_admin_metadata'),
                      ('classroom_web_feedback_excerpt', 'SELECT description FROM feedback_reports')]:
        with d.pg.admin.begin() as conn, pytest.raises(sa.exc.DBAPIError):
            conn.exec_driver_sql(f'SET LOCAL ROLE {role}')
            conn.exec_driver_sql(sql)
    with d.pg.admin.begin() as conn:
        conn.exec_driver_sql('SET LOCAL ROLE classroom_web_feedback_projection')
        metadata = conn.execute(sa.text('SELECT * FROM feedback_admin_metadata WHERE id=:id'),
                                {'id': UUID(approved['request_id'])}).mappings().one()
        assert set(metadata) == {'id', 'category', 'build_id', 'request_id', 'consent', 'created_at'}
        assert metadata['consent'] is True and metadata['request_id'] == other.uid
    with d.pg.admin.begin() as conn:
        conn.exec_driver_sql('SET LOCAL ROLE classroom_web_feedback_projection')
        rows = conn.execute(sa.text('SELECT confirmed_excerpt FROM feedback_authorized_excerpts WHERE id IN (:a,:b)'),
                            {'a': UUID(approved['request_id']), 'b': UUID(second['request_id'])}).scalars().all()
        assert rows == [excerpt]
    with d.pg.admin.connect() as conn:
        evidence = str(conn.exec_driver_sql("""SELECT
            (SELECT json_agg(t) FROM identity_audit t),
            (SELECT json_agg(t) FROM outbox_events t),
            (SELECT json_agg(t) FROM operation_receipts t WHERE operation='submitFeedback')""").one())
    postgres_log = Path(os.environ['PR11C_POSTGRES_LOG']).read_text(encoding='utf8', errors='replace')
    for marker in (description, excerpt):
        assert marker not in caplog.text + evidence + postgres_log
    d.deletion.erase_account(OwnerContext(d.uid))
    with d.pg.admin.begin() as conn:
        conn.exec_driver_sql('SET LOCAL ROLE classroom_web_feedback_projection')
        assert conn.scalar(sa.text('SELECT count(*) FROM feedback_authorized_excerpts WHERE id=:id'),
                           {'id': UUID(approved['request_id'])}) == 0
    with d.pg.admin.begin() as conn:
        conn.exec_driver_sql('SET LOCAL ROLE classroom_web_feedback_projection')
        ids = conn.execute(sa.text('SELECT id FROM feedback_admin_metadata WHERE id IN (:a,:b)'),
                           {'a': UUID(approved['request_id']), 'b': UUID(second['request_id'])}).scalars().all()
        assert ids == [UUID(second['request_id'])]
