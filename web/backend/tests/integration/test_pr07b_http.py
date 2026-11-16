"""PR-07b through real sessions, contract validation and PostgreSQL transactions."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa

from classroom_web.application.qa_browser import QaBrowser
from classroom_web.application.qa_worker import QaWorker
from classroom_web.application.review_browser import ReviewBrowser
from classroom_web.application.review_worker import ReviewWorker
from classroom_web.domain.data import OwnerContext
from classroom_web.entrypoints.http import create_app
from classroom_web.infrastructure.qa_provider import FakeProvider
from classroom_web.infrastructure.qa_repository import PgQaRepository
from classroom_web.infrastructure.review_provider import FakeReviewProvider
from classroom_web.infrastructure.review_repository import PgReviewRepository
from conftest import migrated_database
from test_authentication import auth, login, post, BASE, PASSWORD
from test_billing_integration import fee_api, fee_control, fee_snapshot, headers as billing_headers, reserve, sign_in
from test_persistent_tasks import scheduler


ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location('pr07b_http_contract', ROOT / 'tools/pr07b/contract_candidate.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


@pytest.fixture(scope='module')
def pg():
    """Keep the cancellation candidate ledger isolated from formal schema checks."""
    with migrated_database() as database:
        yield database


@pytest.fixture
def review_api(auth, scheduler):
    owner = OwnerContext(auth.uid)
    with auth.pg.db.transaction(owner) as tx:
        semester = tx.catalog.create_semester('Synthetic review semester')
        course = tx.catalog.create_course(semester.id, 'Synthetic review course')
        classroom = tx.catalog.create_class_session(course.id, 'Synthetic review classroom')
        sources = PgQaRepository(tx)
        first = sources.add_source(classroom.id, kind='transcript_segment', object_id=uuid4(),
            text='边际成本是增加一单位产量带来的额外成本。', start_ms=1000, end_ms=3000)
        second = sources.add_source(classroom.id, kind='transcript_segment', object_id=uuid4(),
            text='边际收益与边际成本的关系用于分析利润。', start_ms=5000, end_ms=8000)
    config = dict(enabled=True, billing_required=False, price_id=None,
        price_version='synthetic-no-charge', maximum_cost_microyuan=0,
        input_token_budget=8192, max_output_tokens=2048,
        destination=dict(provider='fake', model='review-deterministic-v1', region='synthetic',
                         configuration_revision='pr07b-http-fake-1'))
    qa_config = dict(config, destination=dict(provider='fake', model='qa-deterministic-v1',
        region='synthetic', configuration_revision='pr07b-http-qa-1'))
    qa = QaBrowser(auth=auth.service, database=auth.pg.db, config=qa_config)
    review = ReviewBrowser(auth=auth.service, database=auth.pg.db, config=config, qa=qa)
    base = json.loads((ROOT / 'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf8'))
    contract = candidate.build(base)
    app = create_app(auth=auth.service, contract=contract, origin='https://testserver', qa=qa, review=review)
    with TestClient(app, base_url='https://testserver') as client:
        browser = SimpleNamespace(client=client, email=auth.email, pg=auth.pg, uid=auth.uid)
        session = login(browser)
        yield SimpleNamespace(a=auth, owner=owner, semester=semester, course=course,
            classroom=classroom, first=first, second=second, client=client, browser=browser,
            csrf=session['csrf_token'], scheduler=scheduler, config=config, qa=qa,
            review=review, contract=contract)
    with auth.pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE tasks SET not_before=clock_timestamp()+interval '365 days' WHERE owner_id=:owner"),
                           dict(owner=auth.uid))


def request_body(**changes):
    return dict(kind='review_summary', scope='all', seen_sources=[], include_images=False,
                accept_partial_scope=True, granularity='standard', card_types=['concept', 'example'],
                anchor_ms=120000) | changes


def prepare(api, *, body=None, key=None, classroom_id=None):
    return post(api.browser, f'/class-sessions/{classroom_id or api.classroom.id}/generation-plans',
                request_body() if body is None else body, api.csrf,
                **{'Idempotency-Key': str(key or uuid4())})


def execute(api, plan, *, key=None, **changes):
    body = dict(plan_sha256=plan['plan_sha256'], confirm_send=True, confirm_images=False,
        confirm_original_snapshot=False, confirm_partial_scope=True,
        confirmed_destination_revision=plan['destination']['configuration_revision']) | changes
    return post(api.browser, f"/generation-plans/{plan['id']}/execute", body, api.csrf,
                **{'Idempotency-Key': str(key or uuid4())})


def generated(api, **changes):
    prepared = prepare(api, body=request_body(**changes))
    assert prepared.status_code == 201, prepared.text
    submitted = execute(api, prepared.json())
    assert submitted.status_code == 202, submitted.text
    provider = FakeReviewProvider()
    worker = ReviewWorker(api.a.pg.db, api.scheduler, provider, worker_id='pr07b-http', enabled=True)
    assert worker.run_once()
    assert provider.calls == 1
    response = api.client.get(BASE + '/tasks/' + submitted.json()['id'])
    assert response.status_code == 200, response.text
    task = response.json()
    assert task['state'] == 'succeeded', task
    response = api.client.get(BASE + '/review-documents/' + task['result_id'])
    assert response.status_code == 200, response.text
    return SimpleNamespace(plan=prepared.json(), task=task, document=response.json())


def write_personal(api, document, parent_id, body='用户整理后的课堂理解。', *, key=None, draft=None):
    return post(api.browser, '/review-documents/' + document['id'] + '/personal-revisions',
        dict(base_revision=document['revision'], base_content_revision_id=parent_id,
             draft_id=str(draft or uuid4()), body_markdown=body, personal_note='用户补充说明'),
        api.csrf, **{'Idempotency-Key': str(key or uuid4())})


def create_note(api, **changes):
    body = dict(class_session_id=str(api.classroom.id), title='合成个人笔记',
                body_markdown='用户手动记录的内容。', draft_id=str(uuid4())) | changes
    return post(api.browser, '/review-documents', body, api.csrf)


def patch_document(api, document, **changes):
    return api.client.patch(BASE + '/review-documents/' + document['id'],
        json=dict(base_revision=document['revision']) | changes,
        headers={'Origin': 'https://testserver', 'Sec-Fetch-Site': 'same-origin',
                 'X-CSRF-Token': api.csrf, 'Idempotency-Key': str(uuid4())})


def test_summary_cards_snapshot_and_raw_output_follow_the_http_contract(review_api):
    api = review_api
    result = generated(api, granularity='detailed', english_answer=True)
    document = result.document
    assert document['kind'] == 'summary'
    assert document['class_session_id'] == str(api.classroom.id)
    assert document['adopted_revision_id'] is None
    assert document['latest_personal_revision_id'] is None
    assert len(document['candidate_revision_ids']) == 1
    revision = api.client.get(BASE + '/review-revisions/' + document['candidate_revision_ids'][0])
    assert revision.status_code == 200, revision.text
    value = revision.json()
    assert value['kind'] == 'ai_candidate' and value['body_markdown']
    assert value['source_snapshot_id'] == result.task['source_snapshot_id']
    assert value['structured_content']['notes'][0]['source_ids'] == ['S1', 'S2']
    snapshot = api.client.get(BASE + '/source-snapshots/' + result.task['source_snapshot_id'])
    assert snapshot.status_code == 200 and snapshot.json() == result.plan['source_snapshot']
    assert len(snapshot.json()['items']) == 2
    assert snapshot.headers['cache-control'] == 'no-store'
    cards = api.client.get(BASE + '/review-documents',
        params=dict(class_session_id=str(api.classroom.id), kind='card', understood='false'))
    assert cards.status_code == 200, cards.text
    assert len(cards.json()['items']) == 1
    card_document = cards.json()['items'][0]
    card = api.client.get(BASE + '/review-revisions/' + card_document['candidate_revision_ids'][0])
    assert card.status_code == 200, card.text
    content = card.json()['structured_content']['cards'][0]
    assert content['type'] == 'concept' and content['front'] and content['back']
    assert content['english'] and content['english_meaning']
    assert content['source_ids'] == ['S1', 'S2']
    raw = api.client.get(BASE + '/tasks/' + result.task['id'] + '/raw-output')
    assert raw.status_code == 200, raw.text
    assert raw.json()['validation_status'] == 'valid'
    assert json.loads(raw.json()['raw_text']) == value['structured_content']
    assert raw.headers['cache-control'] == 'no-store'


def test_personal_note_create_edit_metadata_history_and_list_filters(review_api):
    api = review_api
    created = create_note(api)
    assert created.status_code == 201, created.text
    initial = created.json()
    assert initial['kind'] == 'note' and initial['candidate_revision_ids'] == []
    personal = api.client.get(BASE + '/review-revisions/' + initial['latest_personal_revision_id'])
    assert personal.status_code == 200, personal.text
    assert personal.json()['kind'] == 'personal'
    assert personal.json()['source_snapshot_id'] is None
    assert personal.json()['structured_content'] is None
    patched = patch_document(api, initial, title='修改后的标题', understood=True)
    assert patched.status_code == 200, patched.text
    current = patched.json()
    assert current['revision'] == initial['revision'] + 1
    assert current['title'] == '修改后的标题' and current['understood'] is True
    assert patch_document(api, initial, title='过期修改').status_code == 409
    historical = api.client.get(BASE + '/review-documents/' + initial['id'], params={'revision': initial['revision']})
    assert historical.status_code == 200 and historical.json() == initial
    edited = write_personal(api, current, initial['latest_personal_revision_id'])
    assert edited.status_code == 201, edited.text
    assert edited.json()['parent_revision_id'] == initial['latest_personal_revision_id']
    for filters in (dict(semester_id=str(api.semester.id)), dict(course_id=str(api.course.id)),
                    dict(class_session_id=str(api.classroom.id), kind='note', understood='true')):
        page = api.client.get(BASE + '/review-documents', params=filters)
        assert page.status_code == 200, page.text
        assert [item['id'] for item in page.json()['items']] == [initial['id']]
    history = api.client.get(BASE + '/review-documents/' + initial['id'] + '/revisions')
    assert history.status_code == 200, history.text
    assert [item['id'] for item in history.json()['items']] == [personal.json()['id'], edited.json()['id']]


def test_adoption_is_explicit_and_regeneration_keeps_the_personal_version(review_api):
    api = review_api
    original = generated(api)
    first_candidate = original.document['candidate_revision_ids'][0]
    adopted = post(api.browser, '/review-documents/' + original.document['id'] + '/adopt',
        dict(base_revision=original.document['revision'], revision_id=first_candidate), api.csrf)
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()['adopted_revision_id'] == first_candidate
    with api.a.pg.db.transaction(api.owner) as tx:
        PgQaRepository(tx).revise_source(api.first.source_id, '修订来源解释边际成本及其适用条件。')
    prepared = prepare(api, body=request_body(target_document_id=original.document['id'],
        base_revision=adopted.json()['revision'], granularity='concise'))
    assert prepared.status_code == 201, prepared.text
    edited = write_personal(api, adopted.json(), first_candidate, '用户明确确认并需要保留的修改。')
    assert edited.status_code == 201, edited.text
    submitted = execute(api, prepared.json())
    assert submitted.status_code == 202, submitted.text
    worker = ReviewWorker(api.a.pg.db, api.scheduler, FakeReviewProvider(), worker_id='pr07b-http-regenerate', enabled=True)
    assert worker.run_once()
    task = api.client.get(BASE + '/tasks/' + submitted.json()['id']).json()
    assert task['state'] == 'succeeded' and task['result_id'] == original.document['id']
    current = api.client.get(BASE + '/review-documents/' + original.document['id']).json()
    assert current['adopted_revision_id'] == first_candidate
    assert current['latest_personal_revision_id'] == edited.json()['id']
    assert len(current['candidate_revision_ids']) == 2
    assert current['source_status'] == 'changed'
    previous = api.client.get(BASE + '/review-revisions/' + first_candidate).json()
    fresh = api.client.get(BASE + '/review-revisions/' + current['candidate_revision_ids'][-1]).json()
    assert previous['source_snapshot_id'] == original.task['source_snapshot_id']
    assert fresh['source_snapshot_id'] == task['source_snapshot_id']
    assert fresh['source_snapshot_id'] != previous['source_snapshot_id']
    assert api.client.get(BASE + '/review-revisions/' + edited.json()['id']).json()['body_markdown'] == '用户明确确认并需要保留的修改。'
    cards = api.client.get(BASE + '/review-documents', params=dict(class_session_id=str(api.classroom.id), kind='card')).json()['items']
    assert len(cards) == 1 and len(cards[0]['candidate_revision_ids']) == 2


def test_new_client_traces_regenerated_history_and_personal_ancestry_to_raw_output(review_api):
    api = review_api
    original = generated(api)
    first_candidate = original.document['candidate_revision_ids'][0]
    first_personal = write_personal(api, original.document, first_candidate, '第一层个人整理。')
    assert first_personal.status_code == 201, first_personal.text
    current = api.client.get(BASE + '/review-documents/' + original.document['id']).json()
    second_personal = write_personal(api, current, first_personal.json()['id'], '第二层个人整理。')
    assert second_personal.status_code == 201, second_personal.text
    current = api.client.get(BASE + '/review-documents/' + original.document['id']).json()
    with api.a.pg.db.transaction(api.owner) as tx:
        PgQaRepository(tx).revise_source(api.first.source_id, '重新生成所用的合成边际成本来源。')
    regenerated = generated(api, target_document_id=current['id'], base_revision=current['revision'])
    note = create_note(api)
    assert note.status_code == 201, note.text
    expected_tasks = {original.task['id'], regenerated.task['id']}

    with TestClient(api.client.app, base_url='https://testserver') as client:
        login(SimpleNamespace(client=client, email=api.a.email))
        listed = client.get(BASE + '/review-documents', params={'class_session_id': str(api.classroom.id)})
        assert listed.status_code == 200, listed.text
        documents = listed.json()['items']
        assert {document['kind'] for document in documents} == {'summary', 'card', 'note'}
        for document in documents:
            history = client.get(BASE + '/review-documents/' + document['id'] + '/revisions')
            assert history.status_code == 200, history.text
            versions = history.json()['items']
            candidates = [version for version in versions if version['kind'] == 'ai_candidate']
            if document['kind'] == 'note':
                assert candidates == []
                assert all(version['generation_task_id'] is None for version in versions)
                continue
            assert {version['generation_task_id'] for version in candidates} == expected_tasks
            for candidate_version in candidates:
                detail = client.get(BASE + '/review-revisions/' + candidate_version['id'])
                assert detail.status_code == 200 and detail.json() == candidate_version
                generation_id = candidate_version['generation_task_id']
                task_response = client.get(BASE + '/tasks/' + generation_id)
                assert task_response.status_code == 200, task_response.text
                task = task_response.json()
                assert task['state'] == 'succeeded' and task['id'] == generation_id
                assert task['source_snapshot_id'] == candidate_version['source_snapshot_id']
                raw_response = client.get(BASE + '/tasks/' + generation_id + '/raw-output')
                assert raw_response.status_code == 200, raw_response.text
                raw = raw_response.json()
                assert raw['task_id'] == generation_id and raw['validation_status'] == 'valid'
                assert candidate_version['raw_output_ref'] is not None
                assert json.loads(raw['raw_text']) == candidate_version['structured_content']
            if document['kind'] == 'summary':
                personal = client.get(BASE + '/review-revisions/' + document['latest_personal_revision_id'])
                assert personal.status_code == 200, personal.text
                assert personal.json()['id'] == second_personal.json()['id']
                for expected_parent in (first_personal.json()['id'], first_candidate):
                    version = personal.json()
                    assert version['kind'] == 'personal' and version['generation_task_id'] is None
                    assert version['raw_output_ref'] is None and version['parent_revision_id'] == expected_parent
                    personal = client.get(BASE + '/review-revisions/' + version['parent_revision_id'])
                    assert personal.status_code == 200, personal.text
                assert personal.json()['generation_task_id'] == original.task['id']
                assert personal.json()['source_snapshot_id'] == original.task['source_snapshot_id']


def test_new_client_traces_multibatch_history_to_every_saved_raw_output(review_api):
    api = review_api
    with api.a.pg.db.transaction(api.owner) as tx:
        sources = PgQaRepository(tx)
        sources.revise_source(api.first.source_id, 'A' * 4000)
        sources.revise_source(api.second.source_id, 'B' * 4000)
        sources.add_source(api.classroom.id, kind='transcript_segment', object_id=uuid4(),
            text='C' * 4000, start_ms=9000, end_ms=11000)
    prepared = prepare(api)
    assert prepared.status_code == 201, prepared.text
    plan = prepared.json()
    assert plan['estimated_batches'] == 3
    submitted = execute(api, plan)
    assert submitted.status_code == 202, submitted.text
    provider = FakeReviewProvider()
    worker = ReviewWorker(api.a.pg.db, api.scheduler, provider, worker_id='pr07b-http-batches', enabled=True)
    for _ in range(plan['estimated_batches'] + 2):
        calls = provider.calls
        worker.run_once()
        assert provider.calls - calls <= 1
        progress = api.client.get(BASE + '/tasks/' + submitted.json()['id'])
        assert progress.status_code == 200, progress.text
        if progress.json()['state'] == 'succeeded':
            break
    assert progress.json()['state'] == 'succeeded' and provider.calls == plan['estimated_batches']

    with TestClient(api.client.app, base_url='https://testserver') as client:
        login(SimpleNamespace(client=client, email=api.a.email))
        listed = client.get(BASE + '/review-documents', params={'class_session_id': str(api.classroom.id)})
        assert listed.status_code == 200, listed.text
        documents = listed.json()['items']
        summary = next(document for document in documents if document['kind'] == 'summary')
        history = client.get(BASE + '/review-documents/' + summary['id'] + '/revisions')
        assert history.status_code == 200, history.text
        version = history.json()['items'][0]
        root_id = version['generation_task_id']
        root_response = client.get(BASE + '/tasks/' + root_id)
        assert root_response.status_code == 200, root_response.text
        root = root_response.json()
        assert root['id'] == submitted.json()['id'] and root['result_id'] == summary['id']
        assert root['total_units'] == root['completed_units'] == plan['estimated_batches']
        batches = root['batch_tasks']
        assert [batch['batch_index'] for batch in batches] == list(range(plan['estimated_batches']))
        assert batches[0]['task_id'] == root_id
        assert len({batch['task_id'] for batch in batches}) == plan['estimated_batches']
        cited = set()
        for batch in batches:
            task_response = client.get(BASE + '/tasks/' + batch['task_id'])
            assert task_response.status_code == 200, task_response.text
            assert task_response.json()['state'] == batch['state'] == 'succeeded'
            raw_response = client.get(BASE + '/tasks/' + batch['task_id'] + '/raw-output')
            assert raw_response.status_code == 200, raw_response.text
            raw = raw_response.json()
            assert raw['task_id'] == batch['task_id'] and raw['validation_status'] == 'valid'
            assert raw_response.headers['cache-control'] == 'no-store'
            payload = json.loads(raw['raw_text'])
            aliases = {alias for item in payload['cards'] + payload['notes'] for alias in item['source_ids']}
            assert aliases == set(batch['source_ids'])
            cited.update(aliases)
        assert cited == {'S1', 'S2', 'S3'}
        assert {alias for item in version['structured_content']['notes'] for alias in item['source_ids']} == cited
        snapshot = client.get(BASE + '/source-snapshots/' + version['source_snapshot_id'])
        assert snapshot.status_code == 200 and snapshot.json() == plan['source_snapshot']
        assert len(documents) == plan['estimated_batches'] + 1
        for document in documents:
            revisions_response = client.get(BASE + '/review-documents/' + document['id'] + '/revisions')
            assert revisions_response.status_code == 200, revisions_response.text
            assert all(revision['generation_task_id'] == root_id for revision in revisions_response.json()['items'])


@pytest.mark.parametrize('access_loss', ['foreign_owner', 'revoked_source'])
def test_history_generation_links_reauthorize_every_hop(review_api, access_loss):
    api = review_api
    result = generated(api)
    personal = write_personal(api, result.document, result.document['candidate_revision_ids'][0])
    assert personal.status_code == 201, personal.text
    with TestClient(api.client.app, base_url='https://testserver') as client:
        browser = SimpleNamespace(client=client, email=api.a.email)
        login(browser)
        listed = client.get(BASE + '/review-documents', params={'kind': 'summary'})
        assert listed.status_code == 200, listed.text
        document = listed.json()['items'][0]
        history = client.get(BASE + '/review-documents/' + document['id'] + '/revisions')
        assert history.status_code == 200, history.text
        candidate_version = next(version for version in history.json()['items'] if version['kind'] == 'ai_candidate')
        root_id = candidate_version['generation_task_id']
        raw = client.get(BASE + '/tasks/' + root_id + '/raw-output')
        assert raw.status_code == 200, raw.text
        if access_loss == 'foreign_owner':
            browser.email = uuid4().hex + '@example.test'
            with api.a.pg.admin.begin() as connection:
                connection.execute(sa.text('''INSERT INTO users(id,normalized_email,password_hash,role,state,
                    email_verified_at) VALUES(:id,:email,:hash,'student','active',clock_timestamp())'''),
                    {'id': uuid4(), 'email': browser.email, 'hash': api.a.crypto.hash(PASSWORD)})
            client.cookies.clear()
            login(browser)
        else:
            with api.a.pg.db.transaction(api.owner) as tx:
                PgQaRepository(tx).revoke_source(api.first.source_id)
        paths = ['/review-documents/' + document['id'],
                 '/review-documents/' + document['id'] + '/revisions',
                 '/review-revisions/' + candidate_version['id'],
                 '/review-revisions/' + personal.json()['id'],
                 '/tasks/' + root_id, '/tasks/' + root_id + '/raw-output',
                 '/source-snapshots/' + candidate_version['source_snapshot_id']]
        denied_statuses = (404,) if access_loss == 'foreign_owner' else (404, 409)
        for path in paths:
            response = client.get(BASE + path)
            assert response.status_code in denied_statuses, (path, response.text)
            assert raw.json()['raw_text'] not in response.text
            assert '边际成本' not in response.text and '用户整理' not in response.text
        remaining = client.get(BASE + '/review-documents')
        assert remaining.status_code == 200 and remaining.json()['items'] == []


def test_personal_conflict_returns_a_durable_conflict_id_and_preserves_both_branches(review_api):
    api = review_api
    original = generated(api)
    parent_id = original.document['candidate_revision_ids'][0]
    first = write_personal(api, original.document, parent_id, '先保存的个人版本。')
    assert first.status_code == 201, first.text
    key, draft = uuid4(), uuid4()
    stale = write_personal(api, original.document, parent_id, '迟到但需要保留的个人分支。', key=key, draft=draft)
    assert stale.status_code == 409, stale.text
    error = stale.json()['error']
    assert error['code'] == 'REVISION_CONFLICT'
    assert UUID(error['conflict_id'])
    assert error['current_revision'] > original.document['revision']
    replay = write_personal(api, original.document, parent_id, '迟到但需要保留的个人分支。', key=key, draft=draft)
    assert replay.status_code == 409 and replay.json()['error']['conflict_id'] == error['conflict_id']
    current = api.client.get(BASE + '/review-documents/' + original.document['id']).json()
    assert current['latest_personal_revision_id'] == first.json()['id']
    history = api.client.get(BASE + '/review-documents/' + original.document['id'] + '/revisions')
    assert history.status_code == 200, history.text
    rows = history.json()['items']
    assert len(rows) == 3
    assert {row['body_markdown'] for row in rows if row['kind'] == 'personal'} == {
        '先保存的个人版本。', '迟到但需要保留的个人分支。'}
    with api.a.pg.admin.connect() as connection:
        assert connection.execute(sa.text('SELECT count(*) FROM review_conflicts WHERE id=:id'),
                                  {'id': UUID(error['conflict_id'])}).scalar_one() == 1


def test_generation_and_note_submission_are_idempotent(review_api):
    api = review_api
    key = uuid4()
    first = prepare(api, key=key)
    assert first.status_code == 201, first.text
    assert prepare(api, key=key).json()['id'] == first.json()['id']
    mismatch = prepare(api, key=key, body=request_body(granularity='detailed'))
    assert mismatch.status_code == 409 and mismatch.json()['error']['code'] == 'IDEMPOTENCY_MISMATCH'
    execution_key = uuid4()
    task = execute(api, first.json(), key=execution_key)
    assert task.status_code == 202, task.text
    replay = execute(api, first.json(), key=execution_key)
    assert replay.status_code == 202 and replay.json()['id'] == task.json()['id']
    worker = ReviewWorker(api.a.pg.db, api.scheduler, FakeReviewProvider(), worker_id='pr07b-http-idempotent', enabled=True)
    assert worker.run_once() and not worker.run_once()
    body = dict(class_session_id=str(api.classroom.id), title='幂等笔记', body_markdown='只保存一次', draft_id=str(uuid4()))
    note_key = str(uuid4())
    note = post(api.browser, '/review-documents', body, api.csrf, **{'Idempotency-Key': note_key})
    assert note.status_code == 201, note.text
    replay = post(api.browser, '/review-documents', body, api.csrf, **{'Idempotency-Key': note_key})
    assert replay.status_code == 201 and replay.json()['id'] == note.json()['id']
    changed = post(api.browser, '/review-documents', body | {'body_markdown': '不同内容'}, api.csrf,
                   **{'Idempotency-Key': note_key})
    assert changed.status_code == 409 and changed.json()['error']['code'] == 'IDEMPOTENCY_MISMATCH'


def test_source_revocation_blocks_review_history_raw_output_and_replayed_writes(review_api):
    api = review_api
    result = generated(api)
    document = result.document
    revision_id = document['candidate_revision_ids'][0]
    key, draft = uuid4(), uuid4()
    personal = write_personal(api, document, revision_id, key=key, draft=draft)
    assert personal.status_code == 201, personal.text
    with api.a.pg.db.transaction(api.owner) as tx:
        PgQaRepository(tx).revoke_source(api.first.source_id)
    paths = ['/review-documents/' + document['id'],
             '/review-documents/' + document['id'] + '/revisions',
             '/review-revisions/' + revision_id,
             '/review-revisions/' + personal.json()['id'],
             '/tasks/' + result.task['id'] + '/raw-output',
             '/tasks/' + result.task['id'],
             '/source-snapshots/' + result.task['source_snapshot_id']]
    for path in paths:
        response = api.client.get(BASE + path)
        assert response.status_code in (404, 409), (path, response.text)
        assert '边际成本' not in response.text and '合成课堂' not in response.text
    replay = write_personal(api, document, revision_id, key=key, draft=draft)
    assert replay.status_code in (404, 409)
    page = api.client.get(BASE + '/review-documents', params={'class_session_id': str(api.classroom.id)})
    assert page.status_code == 200 and page.json()['items'] == []


def test_cross_owner_and_classroom_requests_cannot_read_or_attach_other_documents(review_api, owners):
    api = review_api
    with api.a.pg.db.transaction(owners.a) as tx:
        semester = tx.catalog.create_semester('Foreign synthetic semester')
        course = tx.catalog.create_course(semester.id, 'Foreign synthetic course')
        classroom = tx.catalog.create_class_session(course.id, 'Foreign synthetic classroom')
        foreign = PgReviewRepository(tx).create_note(dict(class_session_id=str(classroom.id),
            title='Foreign private title', body_markdown='FOREIGN_REVIEW_SECRET', draft_id=str(uuid4())), uuid4())
    for path in ('/review-documents/' + foreign['id'],
                 '/review-documents/' + foreign['id'] + '/revisions',
                 '/review-revisions/' + foreign['latest_personal_revision_id']):
        response = api.client.get(BASE + path)
        assert response.status_code == 404 and 'FOREIGN_REVIEW_SECRET' not in response.text
    assert patch_document(api, foreign, title='forbidden').status_code == 404
    assert create_note(api, class_session_id=str(classroom.id)).status_code == 404
    assert prepare(api, classroom_id=classroom.id).status_code == 404
    result = generated(api)
    with api.a.pg.db.transaction(api.owner) as tx:
        other_classroom = tx.catalog.create_class_session(api.course.id, 'Other owned classroom')
        PgQaRepository(tx).add_source(other_classroom.id, kind='transcript_segment', object_id=uuid4(),
            text='另一课堂的独立合成来源。', start_ms=1000, end_ms=3000)
    response = prepare(api, classroom_id=other_classroom.id, body=request_body(
        target_document_id=result.document['id'], base_revision=result.document['revision']))
    assert response.status_code == 404, response.text
    note = create_note(api, class_session_id=str(other_classroom.id)).json()
    adoption = post(api.browser, '/review-documents/' + result.document['id'] + '/adopt',
        dict(base_revision=result.document['revision'], revision_id=note['latest_personal_revision_id']), api.csrf)
    assert adoption.status_code == 404, adoption.text


def test_csrf_and_revoked_sessions_precede_review_writes(review_api):
    api = review_api
    note_body = dict(class_session_id=str(api.classroom.id), title='CSRF probe',
                     body_markdown='synthetic', draft_id=str(uuid4()))
    assert api.client.post(BASE + '/review-documents', json=note_body).status_code == 403
    assert post(api.browser, '/review-documents', note_body, 'wrong-csrf').status_code == 403
    note = create_note(api).json()
    assert api.client.patch(BASE + '/review-documents/' + note['id'],
                            json={'base_revision': note['revision'], 'title': 'blocked'}).status_code == 403
    with api.a.pg.admin.begin() as connection:
        connection.execute(sa.text('UPDATE users SET security_epoch=security_epoch+1 WHERE id=:id'),
                           dict(id=api.owner.owner_id))
    assert create_note(api).status_code == 401
    assert api.client.get(BASE + '/review-documents/' + note['id']).status_code == 401


def test_escaped_unicode_maximum_document_fits_the_bounded_http_body(review_api):
    api = review_api
    content = '\U0001f642' * 120000
    body = dict(class_session_id=str(api.classroom.id), title='Unicode boundary',
                body_markdown=content, draft_id=str(uuid4()))
    encoded = json.dumps(body, ensure_ascii=True).encode('ascii')
    assert 1_000_000 < len(encoded) < 2 * 1024 * 1024
    headers = {'Origin': 'https://testserver', 'Sec-Fetch-Site': 'same-origin',
               'X-CSRF-Token': api.csrf, 'Idempotency-Key': str(uuid4()), 'Content-Type': 'application/json'}
    response = api.client.post(BASE + '/review-documents', content=encoded, headers=headers)
    assert response.status_code == 201, response.text
    revision = api.client.get(BASE + '/review-revisions/' + response.json()['latest_personal_revision_id'])
    assert revision.status_code == 200 and revision.json()['body_markdown'] == content
    too_large = encoded + b' ' * (2 * 1024 * 1024 + 1 - len(encoded))
    response = api.client.post(BASE + '/review-documents', content=too_large,
                               headers=headers | {'Idempotency-Key': str(uuid4())})
    assert response.status_code == 422, response.text
    invalid = create_note(api, body_markdown=content + '\U0001f642')
    assert invalid.status_code == 422, invalid.text


def test_review_query_and_request_validation_rejects_ambiguity(review_api):
    api = review_api
    note = create_note(api).json()
    for query in ('limit=1&limit=2', 'owner_id=' + str(api.owner.owner_id), 'limit=0',
                  'class_session_id=bad', 'understood=1', 'kind=invalid', 'unknown=value'):
        response = api.client.get(BASE + '/review-documents?' + query)
        assert response.status_code == 422, (query, response.text)
    for suffix in ('?revision=1&revision=2', '?revision=0', '?owner_id=' + str(api.owner.owner_id)):
        assert api.client.get(BASE + '/review-documents/' + note['id'] + suffix).status_code == 422
    for suffix in ('?limit=1&limit=2', '?owner_id=' + str(api.owner.owner_id)):
        assert api.client.get(BASE + '/review-documents/' + note['id'] + '/revisions' + suffix).status_code == 422
    assert api.client.get(BASE + '/review-revisions/' + note['latest_personal_revision_id'] + '?revision=1').status_code == 422
    for changes in ({'granularity': 'unbounded'}, {'card_types': ['concept', 'concept']},
                    {'target_document_id': str(uuid4())}, {'base_revision': 1},
                    {'owner_id': str(api.owner.owner_id)}):
        response = prepare(api, body=request_body(**changes))
        assert response.status_code == 422, (changes, response.text)


def test_qa_and_review_composition_keep_distinct_providers_and_disabled_gates(review_api):
    api = review_api
    result = generated(api)
    api.review.config['enabled'] = False
    assert prepare(api).status_code == 503
    assert api.client.get(BASE + '/review-documents/' + result.document['id']).status_code == 503
    question = dict(kind='ai_question', scope='all', question='边际成本是什么？', seen_sources=[],
                    include_images=False, accept_partial_scope=True, anchor_ms=120000)
    prepared = prepare(api, body=question)
    assert prepared.status_code == 201, prepared.text
    submitted = execute(api, prepared.json())
    assert submitted.status_code == 202, submitted.text
    provider = FakeProvider()
    worker = QaWorker(api.a.pg.db, api.scheduler, provider, worker_id='pr07b-http-qa', enabled=True)
    assert worker.run_once() and provider.calls == 1
    task = api.client.get(BASE + '/tasks/' + submitted.json()['id'])
    assert task.status_code == 200 and task.json()['state'] == 'succeeded'
    answer = api.client.get(BASE + '/questions/' + task.json()['result_id'])
    assert answer.status_code == 200 and answer.json()['cited_source_ids'] == ['S1', 'S2']
    api.review.config.pop('enabled')
    assert prepare(api).status_code == 503
    review_provider = FakeReviewProvider()
    disabled = ReviewWorker(api.a.pg.db, api.scheduler, review_provider, worker_id='pr07b-http-default-disabled')
    assert not disabled.run_once() and review_provider.calls == 0
    unmarked = dict(api.contract)
    unmarked.pop('x-pr07b-candidate')
    with pytest.raises(ValueError, match='candidate'):
        create_app(auth=api.a.service, contract=unmarked, origin='https://testserver', qa=api.qa, review=api.review)


@pytest.mark.parametrize('qa_present', [False, True])
def test_enabled_review_preserves_legacy_billing_cancel_with_missing_or_disabled_qa(fee_api, qa_present):
    api = fee_api
    qa = QaBrowser(auth=api.a.service, database=api.c.pg.db,
                   config={'enabled': False}) if qa_present else None
    review = ReviewBrowser(auth=api.a.service, database=api.c.pg.db,
                           config={'enabled': True}, qa=qa)
    contract = candidate.build(api.contract)
    app = create_app(auth=api.a.service, contract=contract, origin='https://testserver',
                     qa=qa, review=review, billing=api.browser)
    with TestClient(app, base_url='https://testserver') as client:
        combined = SimpleNamespace(client=client, a=api.a, c=api.c)
        session, _ = sign_in(combined)
        reservation = reserve(combined, session)
        before = fee_snapshot(combined, reservation)
        assert before['state'] == 'reserved' and before['held'] == 100
        with api.c.pg.db.transaction(OwnerContext(api.a.uid)) as tx:
            task = tx.tasks.get(reservation['task_id'])
            assert task.schema_version == '1' and task.state == 'queued'
        path = BASE + f"/tasks/{reservation['task_id']}/cancel"
        request_headers = billing_headers(session)
        missing_csrf = dict(request_headers)
        missing_csrf.pop('X-CSRF-Token')
        assert client.post(path, json={}, headers=missing_csrf).status_code == 403
        assert fee_snapshot(combined, reservation) == before
        response = client.post(path, json={}, headers=request_headers)
        assert response.status_code == 200, response.text
        assert response.json()['state'] == 'cancelled'
        assert response.json()['maximum_cost_microyuan'] == '100'
        after = fee_snapshot(combined, reservation)
        assert after['task_state'] == 'cancelled' and after['state'] == 'released'
        assert (after['held'], after['unknown'], after['settled']) == (0, 0, 0)
        repeated = client.post(path, json={}, headers=request_headers)
        assert repeated.status_code == 200 and repeated.json()['state'] == 'cancelled'
        assert fee_snapshot(combined, reservation) == after


def test_review_routing_keeps_qa_billing_lifecycle_and_unbilled_review_independent(review_api, fee_api):
    api = review_api
    price = uuid4()
    with api.a.pg.admin.begin() as connection:
        connection.execute(sa.text('''INSERT INTO price_catalog(id,provider,model,region,revision,currency,rates,
            minimum_microyuan,synthetic,effective_at) VALUES(:id,'fake','qa-deterministic-v1','synthetic',:revision,
            'CNY',CAST(:rates AS jsonb),0,true,'2020-01-01T00:00:00Z')'''),
            dict(id=price, revision=str(price), rates=json.dumps({
                'input_tokens': {'microyuan': 1, 'units': 1},
                'output_tokens': {'microyuan': 1, 'units': 1}})))
    api.qa.config.update(price_id=str(price), price_version=str(price),
                         maximum_cost_microyuan=100000, billing_required=True)
    api.qa.billing = fee_api.c.lifecycle
    assert api.review.billing is None and api.review.config['billing_required'] is False
    body = dict(kind='ai_question', scope='all', question='边际成本是什么？', seen_sources=[],
                include_images=False, accept_partial_scope=True, anchor_ms=120000)
    prepared = prepare(api, body=body)
    assert prepared.status_code == 201, prepared.text
    submitted = execute(api, prepared.json())
    assert submitted.status_code == 202, submitted.text
    task_id = UUID(submitted.json()['id'])
    with api.a.pg.admin.connect() as connection:
        reservation = dict(connection.execute(sa.text(
            'SELECT id,task_id FROM quota_reservations WHERE owner_id=:owner AND task_id=:task'),
            dict(owner=api.owner.owner_id, task=task_id)).mappings().one())
    before = fee_snapshot(fee_api, reservation)
    assert before['state'] == 'reserved' and before['held'] > 0
    with api.a.pg.db.transaction(api.owner) as tx:
        task = tx.tasks.get(task_id)
        assert task.schema_version == 'pr07a-1' and task.state == 'queued'
    key = str(uuid4())
    response = post(api.browser, '/tasks/' + str(task_id) + '/cancel', {}, api.csrf,
                    **{'Idempotency-Key': key})
    assert response.status_code == 200 and response.json()['state'] == 'cancelled', response.text
    after = fee_snapshot(fee_api, reservation)
    assert after['task_state'] == 'cancelled' and after['state'] == 'released'
    assert (after['held'], after['unknown'], after['settled']) == (0, 0, 0)
    repeated = post(api.browser, '/tasks/' + str(task_id) + '/cancel', {}, api.csrf,
                    **{'Idempotency-Key': key})
    assert repeated.status_code == 200
    assert fee_snapshot(fee_api, reservation) == after
    review = generated(api)
    assert review.document['kind'] == 'summary'
    assert review.task['maximum_cost_microyuan'] == '0'
    with api.a.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT count(*) FROM quota_reservations WHERE task_id=:task'),
                                 {'task': UUID(review.task['id'])}) == 0
        assert connection.scalar(sa.text('SELECT count(*) FROM quota_reservations WHERE owner_id=:owner'),
                                 {'owner': api.owner.owner_id}) == 1
