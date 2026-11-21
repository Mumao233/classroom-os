"""Frozen HTTP contract and synthetic Filetrans on isolated PostgreSQL/SeaweedFS."""
import json
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Event
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from classroom_web.application.filetrans import FiletransService
from classroom_web.application.filetrans_worker import FiletransWorker
from classroom_web.domain.billing import period
from classroom_web.domain.data import OwnerContext
from classroom_web.domain.filetrans import DESTINATION, SCHEMA, SyntheticFiletrans
from classroom_web.domain.qa import QaRejected
from classroom_web.entrypoints.http import create_app
from classroom_web.infrastructure.qa_repository import PgQaRepository
from deletion_support import auth, pg, services, deploy, d, contract
from test_authentication import BASE
from test_billing_integration import fee_control
from test_capture_recovery import CONTENT, acquire, capture, command, headers, prepare, put
from test_deletion_lifecycle import delete
from test_realtime_asr import live, create, finish, run


@pytest.fixture
def filetrans(live):
    m = live
    price = uuid4()
    with m.pg.admin.begin() as c:
        c.execute(sa.text("""INSERT INTO price_catalog(id,provider,model,region,revision,
            currency,rates,minimum_microyuan,synthetic,effective_at)
            VALUES(:id,:provider,:model,:region,:revision,'CNY',
                CAST(:rates AS jsonb),0,true,'2020-01-01T00:00:00Z')"""),
            {**DESTINATION, 'id': price, 'revision': str(price),
                'rates': json.dumps({'audio_duration_ms': {'microyuan': 1, 'units': 1}})})
    switches(m, True)
    m.ftrans = FiletransService(m.capture, price_id=price, lifecycle=m.fees.lifecycle, enabled=True)
    app = create_app(auth=m.a.service, contract=contract(), origin='https://testserver', capture=m.capture,
                     deletion=m.deletion, asr=m.asr, filetrans=m.ftrans, websocket_admission=m.a.ws_admission)
    with TestClient(app, base_url='https://testserver') as client:
        client.cookies.update(m.client.cookies)
        m.client = m.a.client = client
        yield m
    with m.pg.admin.begin() as c:
        c.execute(sa.text("UPDATE tasks SET not_before=clock_timestamp()+interval '365 days' WHERE owner_id=:owner"),
                  {'owner': m.uid})


def switches(m, state):
    with m.fees.operator.begin() as c:
        with m.pg.admin.connect() as admin:
            revision = admin.scalar(sa.text('SELECT revision FROM billing_control.switches'))
        c.execute(sa.text("""SELECT billing_api.update_switches(:actor,:op,:revision,
            CAST(:flags AS jsonb),'synthetic Filetrans integration')"""),
            dict(actor=m.fees.actor, op=uuid4(), revision=revision,
                 flags=json.dumps({'billing': True, 'asr_filetrans': state})))


def source(m, *, missing=False):
    lease = acquire(m)
    chunk = prepare(m, lease)
    if not missing:
        assert put(m, chunk).status_code == 200
    return lease, chunk


def seal(m, lease, *, sample=16000, seq=0):
    stopped = command(m, lease, 'stop', sample=sample, seq=seq, manifest_sha256='1'*64, known_gaps=[])
    assert stopped.status_code == 200, stopped.text


def plan(m, *, key=None, **changes):
    body = dict(kind='asr_retranscribe', seen_sources=[], scope='all', include_images=False, accept_partial_scope=False)
    return m.client.post(BASE + f'/class-sessions/{m.classroom.id}/generation-plans',
                         json=body | changes, headers=headers(m, key))


def execute(m, prepared, *, key=None, **changes):
    body = dict(plan_sha256=prepared['plan_sha256'],
        confirmed_destination_revision=prepared['destination']['configuration_revision'],
        confirm_send=True, confirm_images=False, confirm_original_snapshot=False, confirm_partial_scope=False)
    return m.client.post(BASE + f'/generation-plans/{prepared["id"]}/execute', json=body | changes, headers=headers(m, key))


def task(m, identity):
    response = m.client.get(BASE + '/tasks/' + identity)
    assert response.status_code == 200, response.text
    return response.json()


def versions_for(m):
    response = m.client.get(BASE + f'/class-sessions/{m.classroom.id}/transcript-versions')
    assert response.status_code == 200, response.text
    return response.json()['items']


def worker(m, outcome='success'):
    return FiletransWorker(m.ftrans, m.fees.scheduler, worker_id='synthetic-filetrans', provider=SyntheticFiletrans(outcome))


def approval(m, prepared, original=None):
    with m.fees.operator.begin() as c:
        return c.scalar(sa.text("""SELECT classroom_web_approve_filetrans(:id,:actor,:owner,:classroom,:plan,
            :original,'synthetic additional operation',clock_timestamp()+interval '10 min')"""),
            dict(id=uuid4(), actor=m.fees.actor, owner=m.uid, classroom=m.classroom.id,
                 plan=UUID(prepared['id']), original=UUID(original) if original else None))


def accounting(m, identity):
    with m.pg.admin.connect() as c:
        return c.execute(sa.text("""SELECT r.state,r.pool,r.maximum_units,r.settled_microyuan,r.created_at,r.expires_at,
                (SELECT count(*) FROM usage_receipts u WHERE u.reservation_id=r.id) AS receipts
            FROM quota_reservations r WHERE r.task_id=:id"""), {'id': UUID(identity)}).mappings().one()


def test_range_uses_durable_pcm_and_explicit_cas_adoption(filetrans, monkeypatch):
    m = filetrans
    lease, chunk = source(m)
    stream_response = create(m, lease)
    assert stream_response.status_code == 201, stream_response.text
    stream = stream_response.json()
    assert finish(m, stream).status_code == 200
    run(m)
    realtime, = versions_for(m)
    seal(m, lease)
    prepared = plan(m, scope='range', start_ms=8, end_ms=10)
    assert prepared.status_code == 201, prepared.text
    prepared = prepared.json()
    assert prepared['source_snapshot'] is None and prepared['audio_chunk_ids'] == [chunk['id']]
    assert prepared['quota_units'] == 2 and len(prepared['audio_manifest_sha256']) == 64
    key = str(uuid4())
    admitted = execute(m, prepared, key=key)
    assert admitted.status_code == 202, admitted.text
    identity = admitted.json()['id']
    assert execute(m, prepared, key=key).json()['id'] == identity
    assert execute(m, prepared).json()['id'] == identity
    w = worker(m)
    submit = w.provider.submit
    calls = []
    def track(attempt, parts):
        part, = parts
        calls.append(attempt)
        assert part.selected_pcm_sha256 == sha256(CONTENT[256:320]).hexdigest()
        assert part.selected_byte_count == 64
        assert accounting(m, identity)['state'] == 'submitted'
        return submit(attempt, parts)
    monkeypatch.setattr(w.provider, 'submit', track)
    assert w.run_once() and not w.run_once()
    assert len(calls) == 1
    completed = task(m, identity)
    assert completed['state'] == 'succeeded' and completed['settled_cost_microyuan'] == '2'
    charge = accounting(m, identity)
    assert charge['pool'] == 'filetrans'
    assert charge['expires_at'] == min(period(charge['created_at'])[1], period(charge['created_at'], 'day')[1])
    candidate = completed['result_id']
    versions = {item['id']: item for item in versions_for(m)}
    assert set(versions) == {realtime['id'], candidate}
    assert all(not item['adopted'] for item in versions.values())
    assert versions[candidate]['kind'] == 'retranscription'
    segment, = m.client.get(BASE + f'/transcript-versions/{candidate}/segments').json()['items']
    assert (segment['start_ms'], segment['end_ms'], segment['origin'], segment['stream_id']) == (8, 10, 'retranscription', None)
    assert segment['source_chunk_ids'] == [chunk['id']]
    class_url = BASE + f'/class-sessions/{m.classroom.id}'
    before = m.client.get(class_url).json()
    principal = m.a.service.principal(m.ctx)
    qa_config = dict(enabled=True, billing_required=False, price_id=None, price_version='test',
        maximum_cost_microyuan=0, input_token_budget=8192, max_output_tokens=1024,
        destination=dict(provider='fake', model='test', region='lab', configuration_revision='1'))
    question = dict(kind='ai_question', scope='all', question='合成问题', include_images=False,
                    accept_partial_scope=True, seen_sources=[])
    def question_plan():
        with m.pg.db.authorized_transaction(principal) as tx:
            return PgQaRepository(tx).prepare(m.classroom.id, question, uuid4(), qa_config)
    old_qa = question_plan()
    assert {item['origin'] for item in old_qa['source_snapshot']['items']} == {'live_asr'}
    adoption = dict(base_revision=before['revision'], transcript_version_id=candidate)
    adopted = m.client.post(class_url+'/adopt-transcript', json=adoption, headers=headers(m, key))
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()['adopted_transcript_version_id'] == candidate
    assert adopted.json()['revision'] == before['revision']+1
    assert m.client.post(class_url+'/adopt-transcript', json=adoption, headers=headers(m, key)).json() == adopted.json()
    assert m.client.post(class_url+'/adopt-transcript', json=adoption, headers=headers(m)).status_code == 409
    assert m.client.get(class_url).json()['adopted_transcript_version_id'] == candidate
    assert {item['origin'] for item in question_plan()['source_snapshot']['items']} == {'retranscription'}
    assert m.client.get(class_url, params={'revision': before['revision']}).status_code == 409
    with m.pg.db.authorized_transaction(principal) as tx:
        repo = PgQaRepository(tx)
        assert repo.plan(UUID(old_qa['id']))['source_status'] == 'changed'
        with pytest.raises(QaRejected, match='CONFIRMATION_REQUIRED'):
            repo.execute(UUID(old_qa['id']), dict(plan_sha256=old_qa['plan_sha256'],
                confirmed_destination_revision='1', confirm_send=True, confirm_images=False,
                confirm_partial_scope=True, confirm_original_snapshot=False), uuid4(), qa_config)
        assert repo.snapshot(UUID(old_qa['source_snapshot']['id'])) == old_qa['source_snapshot']
        changed = tx.connection.scalar(sa.text("""SELECT count(*) FROM outbox_events WHERE owner_id=:owner
            AND aggregate_id=:classroom AND aggregate_revision=:revision AND event_type='object.changed'"""),
            dict(owner=m.uid, classroom=m.classroom.id, revision=before['revision']+1))
        assert changed == 1
    rollback = dict(base_revision=adopted.json()['revision'], transcript_version_id=realtime['id'])
    restored = m.client.post(class_url+'/adopt-transcript', json=rollback, headers=headers(m))
    assert restored.status_code == 200, restored.text
    assert {item['origin'] for item in question_plan()['source_snapshot']['items']} == {'live_asr'}


def test_only_confirmed_chunks_and_partial_confirmation(filetrans):
    m = filetrans
    lease, pending = source(m, missing=True)
    durable = prepare(m, lease, seq=1)
    assert put(m, durable).status_code == 200
    seal(m, lease, sample=32000, seq=1)
    assert plan(m).status_code == 409
    selected = plan(m, accept_partial_scope=True)
    assert selected.status_code == 201, selected.text
    prepared = selected.json()
    assert prepared['audio_chunk_ids'] == [durable['id']] and pending['id'] not in prepared['audio_chunk_ids']
    assert execute(m, prepared).status_code == 422
    admitted = execute(m, prepared, confirm_partial_scope=True)
    assert admitted.status_code == 202, admitted.text
    assert worker(m).run_once()
    assert task(m, admitted.json()['id'])['state'] == 'succeeded'


def test_parallel_class_limit_and_single_use_approval(filetrans):
    m = filetrans
    lease, _ = source(m)
    seal(m, lease)
    plans = [plan(m).json() for _ in range(2)]
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda item: execute(m, item), plans))
    assert sorted(item.status_code for item in results) == [202, 409], [item.text for item in results]
    assert worker(m).run_once()
    rejected_plan = plans[next(i for i, response in enumerate(results) if response.status_code == 409)]
    rejected = execute(m, rejected_plan)
    assert rejected.json()['error']['code'] == 'QUOTA_EXCEEDED' and 'approval' in rejected.json()['error']['message']
    approval(m, rejected_plan)
    allowed = execute(m, rejected_plan)
    assert allowed.status_code == 202, allowed.text
    assert worker(m).run_once()
    assert len(versions_for(m)) == 2
    assert execute(m, plan(m).json()).status_code == 409


@pytest.mark.parametrize('outcome', ['unknown', 'timeout'])
def test_unknown_keeps_original_fee_and_explicit_retry(filetrans, outcome):
    m = filetrans
    lease, _ = source(m)
    seal(m, lease)
    prepared = plan(m).json()
    admission = execute(m, prepared)
    assert admission.status_code == 202, admission.text
    original = admission.json()['id']
    assert worker(m, outcome).run_once()
    assert not worker(m).run_once()
    first = task(m, original)
    assert first['state'] == 'unknown' and first['may_have_charged']
    assert accounting(m, original)['state'] == 'unknown'
    assert versions_for(m) == []
    check = m.client.post(BASE + f'/tasks/{original}/reconcile', json={}, headers=headers(m))
    assert check.status_code == 200, check.text
    assert check.json()['provider_query_supported'] is False and check.json()['new_submission'] is False
    url = BASE + f'/tasks/{original}/retry'
    confirm = dict(original_attempt_id=first['attempt_id'], reason='合成用户确认', confirm_duplicate_billing=True)
    assert m.client.post(url, json=confirm | {'confirm_duplicate_billing': False}, headers=headers(m)).status_code == 422
    assert m.client.post(url, json=confirm, headers=headers(m)).status_code == 409
    approval(m, prepared, original)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: m.client.post(url, json=confirm, headers=headers(m)), range(2)))
    assert sorted(item.status_code for item in results) == [202, 409], [item.text for item in results]
    retry = next(item.json() for item in results if item.status_code == 202)
    assert retry['parent_task_id'] == original and retry['id'] != original
    assert worker(m).run_once()
    assert task(m, retry['id'])['state'] == 'succeeded'
    assert task(m, original)['attempt_id'] == first['attempt_id'] and accounting(m, original)['state'] == 'unknown'


@pytest.mark.parametrize(('outcome','task_state','fee_state'),
    [('failure','failed','settled'), ('success_no_usage','succeeded','unknown')])
def test_result_and_fee_are_independent(filetrans, outcome, task_state, fee_state):
    m = filetrans
    lease, _ = source(m)
    seal(m, lease)
    admitted = execute(m, plan(m).json())
    assert admitted.status_code == 202, admitted.text
    assert worker(m, outcome).run_once()
    assert task(m, admitted.json()['id'])['state'] == task_state
    assert accounting(m, admitted.json()['id'])['state'] == fee_state
    assert len(versions_for(m)) == int(task_state == 'succeeded')


def test_expiry_and_explicit_audio_deletion(filetrans):
    m = filetrans
    lease, chunk = source(m)
    seal(m, lease)
    prepared = plan(m).json()
    with m.pg.admin.begin() as c:
        # Move the fixture's observation clock by shortening access under the
        # existing media policy; preserve the immutable capture evidence.
        c.execute(sa.text("""UPDATE media_assets SET expires_at=clock_timestamp()-interval '1 second',
                    revision=revision+1,updated_at=clock_timestamp()
            WHERE id=(SELECT asset_id FROM audio_chunks WHERE id=:id)"""), {'id': UUID(chunk['id'])})
    expired = execute(m, prepared)
    assert expired.status_code == 410 and 'expired' in expired.json()['error']['message'], expired.text
    deletion_id = delete(m, 'audio', m.classroom.id)
    assert deletion_id
    deleted = plan(m)
    assert deleted.status_code == 410 and 'deleted' in deleted.json()['error']['message'], deleted.text


def test_quota_and_kill_switch_block_admission_and_send(filetrans):
    m = filetrans
    lease, _ = source(m)
    seal(m, lease)
    prepared = plan(m).json()
    with m.pg.admin.begin() as c:
        c.execute(sa.text("""INSERT INTO quota_periods(id,owner_id,resource,period_start,period_end,limit_value)
            VALUES(gen_random_uuid(),:owner,'retranscribe_month_ms',
                date_trunc('month',clock_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai',
                (date_trunc('month',clock_timestamp() AT TIME ZONE 'Asia/Shanghai')+interval '1 month') AT TIME ZONE 'Asia/Shanghai',1)"""),
            {'owner':m.uid})
    denied = execute(m, prepared)
    assert denied.status_code == 409 and denied.json()['error']['code'] == 'QUOTA_EXCEEDED'
    with m.pg.admin.begin() as c:
        c.execute(sa.text("UPDATE quota_periods SET limit_value=8640000 WHERE owner_id=:owner"), {'owner':m.uid})
    admitted = execute(m, prepared)
    assert admitted.status_code == 202, admitted.text
    switches(m, False)
    assert plan(m).json()['error']['code'] == 'BUDGET_BLOCKED'
    assert worker(m).run_once()
    identity = admitted.json()['id']
    assert task(m, identity)['state'] == 'failed'
    assert accounting(m, identity)['state'] == 'released'


def test_audio_deletion_during_file_job_keeps_only_accounting(filetrans, monkeypatch):
    m = filetrans
    lease, _ = source(m)
    seal(m, lease)
    admitted = execute(m, plan(m).json())
    assert admitted.status_code == 202, admitted.text
    w = worker(m)
    submit = w.provider.submit
    def deleting(attempt, parts):
        assert delete(m, 'audio', m.classroom.id)
        return submit(attempt, parts)
    monkeypatch.setattr(w.provider, 'submit', deleting)
    assert w.run_once()
    assert versions_for(m) == []
    identity = admitted.json()['id']
    assert task(m, identity)['state'] == 'unknown'
    assert accounting(m, identity)['state'] == 'settled'
    assert plan(m).status_code == 410


def test_unready_deletion_gate_and_classroom_deletion_prevent_dispatch(filetrans, monkeypatch):
    m = filetrans
    lease, _ = source(m)
    seal(m, lease)
    admitted = execute(m, plan(m).json())
    assert admitted.status_code == 202, admitted.text
    w = worker(m)
    def forbidden(*args):
        raise AssertionError('no object read or Filetrans submit allowed')
    monkeypatch.setattr(m.capture.store, 'read', forbidden)
    monkeypatch.setattr(w.provider, 'submit', forbidden)
    with monkeypatch.context() as guard:
        guard.setattr(m.deletion, 'ready', False)
        assert not w.run_once()
    assert delete(m, 'class_session', m.classroom.id)
    w.run_once()
    assert m.client.get(BASE + '/tasks/' + admitted.json()['id']).status_code in (404,410)
    assert m.client.get(BASE + f'/class-sessions/{m.classroom.id}/transcript-versions').status_code == 410


def test_frozen_all_plan_detects_new_epoch_and_keeps_original_chunks(filetrans):
    m = filetrans
    first, chunk = source(m)
    seal(m, first)
    original = plan(m).json()
    second, later = source(m)
    seal(m, second)
    revised = m.client.get(BASE + '/generation-plans/' + original['id']).json()
    assert revised['source_status'] == 'changed' and revised['audio_chunk_ids'] == [chunk['id']]
    assert later['id'] != chunk['id']
    assert execute(m, original).status_code == 409
    admitted = execute(m, original, confirm_original_snapshot=True)
    assert admitted.status_code == 202, admitted.text
    assert worker(m).run_once()
    complete = task(m, admitted.json()['id'])
    segment, = m.client.get(BASE + f'/transcript-versions/{complete["result_id"]}/segments').json()['items']
    assert segment['source_chunk_ids'] == [chunk['id']]


def test_delete_does_not_wait_for_private_object_read(filetrans, monkeypatch):
    m = filetrans
    lease, _ = source(m)
    other = prepare(m, lease, seq=1)
    assert put(m, other).status_code == 200
    seal(m, lease, sample=32000, seq=1)
    admitted = execute(m, plan(m).json())
    assert admitted.status_code == 202, admitted.text
    w = worker(m)
    started, release = Event(), Event()
    read = m.capture.store.read
    reads = []

    def slow_read(*args):
        reads.append(args[0].key)
        started.set()
        assert release.wait(10), 'private read was not released'
        return read(*args)

    def no_file_job(*args):
        pytest.fail('revoked audio was handed off')

    monkeypatch.setattr(m.capture.store, 'read', slow_read)
    monkeypatch.setattr(w.provider, 'submit', no_file_job)
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(w.run_once)
        try:
            assert started.wait(10)
            assert delete(m, 'audio', m.classroom.id)
        finally:
            release.set()
        assert result.result(timeout=10)
    assert len(reads) == 1
    assert accounting(m, admitted.json()['id'])['state'] == 'released'
    assert versions_for(m) == []


def test_disabled_worker_recovers_committed_intent_without_submission(filetrans, monkeypatch):
    m = filetrans
    lease, _ = source(m)
    seal(m, lease)
    admitted = execute(m, plan(m).json())
    assert admitted.status_code == 202, admitted.text
    w = worker(m)
    claim, = m.fees.scheduler.claim(w.worker_id, schema_version=SCHEMA)
    owner = OwnerContext(m.uid)
    attempt, _ = w._authorize(owner, claim, w._parts(owner, claim))
    with m.pg.admin.begin() as c:
        c.execute(sa.text("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"),
                  {'id': claim.id})
    m.ftrans.enabled = False
    def no_file_job(*args):
        pytest.fail('rollback cannot resubmit the original attempt')
    monkeypatch.setattr(w.provider, 'submit', no_file_job)
    assert not w.run_once()
    current = task(m, admitted.json()['id'])
    assert current['state'] == 'unknown' and current['attempt_id'] == str(attempt.id)
    assert accounting(m, current['id'])['state'] == 'unknown'
    assert plan(m).status_code == 503
