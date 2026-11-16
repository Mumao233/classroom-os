"""PR-07a through HTTPS entrypoints, real sessions and real PostgreSQL."""
from copy import deepcopy
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
from classroom_web.domain.data import OwnerContext
from classroom_web.entrypoints.http import create_app
from classroom_web.infrastructure.qa_provider import FakeProvider
from classroom_web.infrastructure.qa_repository import PgQaRepository
from test_authentication import auth, login, post, BASE
from test_persistent_tasks import scheduler


ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location('pr07a_contract', ROOT / 'tools/pr07a/contract_candidate.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


@pytest.fixture
def qa_api(auth, scheduler):
    a = auth
    owner = OwnerContext(a.uid)
    with a.pg.db.transaction(owner) as tx:
        semester = tx.catalog.create_semester('Synthetic QA semester')
        course = tx.catalog.create_course(semester.id, 'Synthetic QA course')
        classroom = tx.catalog.create_class_session(course.id, 'Synthetic QA classroom')
        repository = PgQaRepository(tx)
        first = repository.add_source(classroom.id, kind='transcript_segment', object_id=uuid4(),
                                      text='边际成本是增加一单位产量带来的额外成本。', start_ms=1000, end_ms=3000)
        second = repository.add_source(classroom.id, kind='transcript_segment', object_id=uuid4(),
                                       text='边际收益等于边际成本时，可以检查利润最大化条件。', start_ms=5000, end_ms=8000)
    config = dict(enabled=True, billing_required=False, price_id=None,
                  price_version='synthetic-no-charge', maximum_cost_microyuan=0,
                  input_token_budget=8192, max_output_tokens=1024,
                  destination=dict(provider='fake', model='qa-deterministic-v1', region='synthetic',
                                   configuration_revision='pr07a-fake-1'))
    base = json.loads((ROOT / 'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf-8'))
    contract = candidate.build(base)
    service = QaBrowser(auth=a.service, database=a.pg.db, config=config)
    app = create_app(auth=a.service, contract=contract, origin='https://testserver', qa=service)
    with TestClient(app, base_url='https://testserver') as client:
        browser = SimpleNamespace(client=client, email=a.email, pg=a.pg, uid=a.uid)
        session = login(browser)
        yield SimpleNamespace(a=a, browser=browser, client=client, csrf=session['csrf_token'],
                              owner=owner, classroom=classroom, first=first, second=second,
                              config=config, service=service, scheduler=scheduler, contract=contract)
    with a.pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE tasks SET not_before=clock_timestamp()+interval '365 days' WHERE owner_id=:owner"),
                           dict(owner=a.uid))


def request_body(**changes):
    return dict(kind='ai_question', scope='all', question='边际成本是什么？', seen_sources=[],
                include_images=False, accept_partial_scope=True, anchor_ms=120000) | changes


def prepare(api, *, key=None, body=None):
    return post(api.browser, f'/class-sessions/{api.classroom.id}/generation-plans',
                body or request_body(), api.csrf, **{'Idempotency-Key': str(key or uuid4())})


def execute(api, plan, **changes):
    body = dict(plan_sha256=plan['plan_sha256'],
                confirmed_destination_revision=plan['destination']['configuration_revision'],
                confirm_send=True, confirm_images=False,
                confirm_original_snapshot=False, confirm_partial_scope=True) | changes
    return post(api.browser, f"/generation-plans/{plan['id']}/execute", body, api.csrf)


def run_worker(api, provider=None):
    provider = provider or FakeProvider()
    worker = QaWorker(api.a.pg.db, api.scheduler, provider, worker_id='pr07a-http', enabled=True)
    assert worker.run_once()
    return provider


def test_http_end_to_end_with_real_auth_and_frozen_references(qa_api):
    api = qa_api
    response = prepare(api)
    assert response.status_code == 201, response.text
    plan = response.json()
    assert len(plan['source_snapshot']['items']) == 2
    response = execute(api, plan)
    assert response.status_code == 202, response.text
    task = response.json()
    assert task['state'] == 'queued'
    provider = run_worker(api)
    assert provider.calls == 1
    response = api.client.get(BASE + '/tasks/' + task['id'])
    assert response.status_code == 200, response.text
    completed = response.json()
    assert completed['state'] == 'succeeded'
    question = api.client.get(BASE + '/questions/' + completed['result_id'])
    assert question.status_code == 200, question.text
    assert question.json()['cited_source_ids'] == ['S1', 'S2']
    assert question.json()['answer_markdown']
    snapshot = api.client.get(BASE + '/source-snapshots/' + completed['source_snapshot_id'])
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json() == plan['source_snapshot']
    assert snapshot.headers['cache-control'] == 'no-store'
    page = api.client.get(BASE + '/questions', params={'class_session_id': str(api.classroom.id)})
    assert page.status_code == 200, page.text
    assert [item['id'] for item in page.json()['items']] == [completed['result_id']]


def test_prepare_and_execute_idempotency_and_changed_source_confirmation(qa_api):
    api = qa_api
    key = uuid4()
    first = prepare(api, key=key)
    assert first.status_code == 201, first.text
    assert prepare(api, key=key).json()['id'] == first.json()['id']
    conflict = prepare(api, key=key, body=request_body(question='另一问题'))
    assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'IDEMPOTENCY_MISMATCH'
    plan = first.json()
    with api.a.pg.db.transaction(api.owner) as tx:
        PgQaRepository(tx).revise_source(api.first.source_id, '修正后的边际成本说明。')
    refreshed = api.client.get(BASE + '/generation-plans/' + plan['id'])
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()['source_status'] == 'changed'
    assert execute(api, plan).status_code == 409
    accepted = execute(api, plan, confirm_original_snapshot=True)
    assert accepted.status_code == 202, accepted.text
    repeated = execute(api, plan, confirm_original_snapshot=True)
    assert repeated.status_code == 202 and repeated.json()['id'] == accepted.json()['id']
    run_worker(api)
    snapshot = api.client.get(BASE + '/source-snapshots/' + plan['source_snapshot']['id'])
    assert snapshot.json() == plan['source_snapshot']


def test_source_update_during_generation_keeps_exact_old_excerpt(qa_api):
    api = qa_api
    plan = prepare(api).json()
    task = execute(api, plan).json()

    class UpdatingProvider(FakeProvider):
        def generate(self, request):
            with api.a.pg.db.transaction(api.owner) as tx:
                PgQaRepository(tx).revise_source(api.first.source_id, '生成期间到达的修正。')
            return super().generate(request)

    run_worker(api, UpdatingProvider())
    assert api.client.get(BASE + '/tasks/' + task['id']).json()['state'] == 'succeeded'
    snapshot = api.client.get(BASE + '/source-snapshots/' + task['source_snapshot_id'])
    assert snapshot.json() == plan['source_snapshot']


def test_revocation_blocks_answer_snapshot_task_and_receipt_replay(qa_api):
    api = qa_api
    key = uuid4()
    plan = prepare(api, key=key).json()
    task = execute(api, plan).json()
    run_worker(api)
    task = api.client.get(BASE + '/tasks/' + task['id']).json()
    with api.a.pg.db.transaction(api.owner) as tx:
        PgQaRepository(tx).revoke_source(api.first.source_id)
    for path in ('/tasks/' + task['id'], '/questions/' + task['result_id'],
                 '/source-snapshots/' + task['source_snapshot_id'], '/generation-plans/' + plan['id']):
        response = api.client.get(BASE + path)
        assert response.status_code in (404, 409), response.text
        assert '边际成本' not in response.text
    assert prepare(api, key=key).status_code in (404, 409)


def test_csrf_session_and_query_validation_precede_qa_work(qa_api):
    api = qa_api
    response = api.client.post(BASE + f'/class-sessions/{api.classroom.id}/generation-plans',
                               json=request_body())
    assert response.status_code == 403
    for query in ('limit=1&limit=2', 'owner_id=' + str(api.owner.owner_id), 'limit=0', 'class_session_id=bad'):
        assert api.client.get(BASE + '/questions?' + query).status_code == 422
    with api.a.pg.admin.begin() as connection:
        connection.execute(sa.text('UPDATE users SET security_epoch=security_epoch+1 WHERE id=:id'),
                           dict(id=api.owner.owner_id))
    assert prepare(api).status_code == 401


def test_empty_scope_forged_source_and_unsupported_scope_are_explicit(qa_api):
    api = qa_api
    response = prepare(api, body=request_body(scope='range', start_ms=50000, end_ms=60000))
    assert response.status_code in (409, 422), response.text
    assert response.json()['error']['code'] in ('CONTEXT_EMPTY', 'CONTEXT_INSUFFICIENT')
    forged = dict(kind='transcript_segment', id=str(uuid4()), revision_id=str(uuid4()), content_sha256='0' * 64)
    response = prepare(api, body=request_body(seen_sources=[forged]))
    assert response.status_code in (404, 409, 422), response.text
    assert prepare(api, body=request_body(scope='slides')).status_code == 422


def test_cancel_before_dispatch_persists_without_provider_call(qa_api):
    api = qa_api
    task = execute(api, prepare(api).json()).json()
    response = post(api.browser, '/tasks/' + task['id'] + '/cancel', {}, api.csrf)
    assert response.status_code == 200, response.text
    assert response.json()['state'] == 'cancelled'
    provider = FakeProvider()
    worker = QaWorker(api.a.pg.db, api.scheduler, provider, worker_id='pr07a-http-cancel', enabled=True)
    assert worker.run_once() is False
    assert provider.calls == 0


def test_candidate_is_required_and_disabled_config_rejects(qa_api):
    api = qa_api
    base = json.loads((ROOT / 'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf-8'))
    with pytest.raises(ValueError, match='candidate'):
        create_app(auth=api.a.service, contract=base, origin='https://testserver', qa=api.service)
    api.service.config['enabled'] = False
    assert prepare(api).status_code == 503
