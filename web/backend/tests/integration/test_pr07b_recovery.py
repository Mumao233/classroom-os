"""Real PostgreSQL review recovery, revocation races and synthetic billing paths."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
import subprocess
import sys
from threading import Barrier, Event
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from conftest import migrated_database
from test_billing_integration import fee_control
from test_persistent_tasks import expire, persisted, scheduler, task_db

from classroom_web.application.review_worker import ReviewWorker
from classroom_web.application.task_worker import MockProvider, TaskWorker
from classroom_web.domain.billing import BillingBlocked, QuotaExceeded, Usage
from classroom_web.domain.data import NotFound, Resource
from classroom_web.domain.qa import ProviderFailure, QaRejected
from classroom_web.domain.tasks import StaleFence
from classroom_web.infrastructure.qa_repository import PgQaRepository
from classroom_web.infrastructure.review_generation import PgReviewGenerationRepository
from classroom_web.infrastructure.review_provider import FakeReviewProvider
from classroom_web.infrastructure.review_repository import PgReviewRepository
from classroom_web.infrastructure.task_repository import PgTaskRepository
from classroom_web.infrastructure.task_schema import tables


@pytest.fixture(scope='module')
def pg():
    with migrated_database() as value:
        yield value


@pytest.fixture
def review(task_db, scheduler, owners, tree, pg):
    config = dict(destination=dict(provider='fake', model='review-deterministic-v1',
        region='synthetic', configuration_revision='pr07b-recovery-test'), price_id=None,
        price_version='synthetic-no-charge', maximum_cost_microyuan=0,
        input_token_budget=8192, max_output_tokens=1024, enabled=True, billing_required=False)
    with task_db.transaction(owners.a) as tx:
        source = PgQaRepository(tx).add_source(tree.classroom.id,
            kind='transcript_segment', object_id=uuid4(), text='合成课堂讨论机会成本与资源稀缺。',
            start_ms=1000, end_ms=3000)
    return SimpleNamespace(database=task_db, scheduler=scheduler, owner=owners.a,
                           tree=tree, pg=pg, source=source, config=config)


def prepare_review(review, **changes):
    request = dict(kind='review_summary', scope='all', include_images=False,
        seen_sources=[], accept_partial_scope=False,
        card_types=['concept', 'derivation', 'example', 'pitfall'], english_answer=False)
    request.update(changes)
    with review.database.transaction(review.owner) as tx:
        return PgReviewGenerationRepository(tx).prepare(
            review.tree.classroom.id, request, uuid4(), review.config)


def execute_review(review, plan, key=None):
    body = dict(plan_sha256=plan['plan_sha256'], confirm_send=True, confirm_images=False,
        confirm_original_snapshot=True, confirm_partial_scope=False,
        confirmed_destination_revision=review.config['destination']['configuration_revision'])
    with review.database.transaction(review.owner) as tx:
        return PgReviewGenerationRepository(tx).execute(
            UUID(plan['id']), body, key or uuid4(), review.config)


def review_task(review):
    return execute_review(review, prepare_review(review))


def review_worker(review, provider=None, *, billing=None, enabled=True, worker_id='review-recovery'):
    return ReviewWorker(review.database, review.scheduler, provider or FakeReviewProvider(),
        worker_id=worker_id, enabled=enabled, billing=billing)


def send_intent(review, task, worker, *, save=False):
    claims = review.scheduler.claim(worker.worker_id, 30, 1, schema_version='pr07b-1')
    assert len(claims) == 1 and claims[0].id == task.id
    claim = claims[0]
    with review.database.transaction(review.owner) as tx:
        attempt = tx.tasks.prepare_attempt(task.id, claim.lease_token, worker.worker_id)
    with review.database.transaction(review.owner) as tx:
        request = worker._send_intent(tx, task, attempt, claim)
    assert request is not None and request.purpose == 'review_summary'
    if save:
        reply = worker.provider.generate(request)
        with review.database.transaction(review.owner) as tx:
            tx.tasks.store_result(task.id, attempt.id, reply.raw_output)
    return claim, attempt, request


def revisions(review, task):
    with review.pg.admin.connect() as connection:
        return connection.execute(sa.text('''SELECT * FROM review_revisions
            WHERE owner_id=:owner AND task_id=:task ORDER BY artifact_key'''),
            {'owner': review.owner.owner_id, 'task': task.id}).mappings().all()


def assert_one_publication(review, task):
    rows = revisions(review, task)
    assert len(rows) == 2
    assert sum(row['artifact_key'] == 'summary' for row in rows) == 1
    assert all(row['kind'] == 'ai_candidate' and row['raw_output_ref'] is not None for row in rows)
    assert len({row['id'] for row in rows}) == len({row['document_id'] for row in rows}) == 2
    with review.database.transaction(review.owner) as tx:
        for row in rows:
            document = PgReviewRepository(tx).document(row['document_id'])
            assert document['candidate_revision_ids'] == [str(row['id'])]
            assert document['adopted_revision_id'] is None
            assert document['latest_personal_revision_id'] is None
    return rows


def valid_output():
    return dict(schema_version=1, cards=[dict(type='concept', title='合成概念',
        front='合成问题是什么？', back='合成答案。', supplement='', english='', english_meaning='',
        source_ids=['S1'], missing=[])], notes=[dict(section='structure', title='合成结构',
        body='课堂概念与知识关系的合成说明。', supplement='', source_ids=['S1'], missing=[])])


def test_disabled_and_generic_workers_cannot_dispatch_review(review):
    task = review_task(review)
    provider = FakeReviewProvider()
    assert not review_worker(review, provider, enabled=False).run_once()
    generic = MockProvider()
    assert not TaskWorker(review.database, review.scheduler, generic, worker_id='generic').run_once()
    assert provider.calls == generic.calls == 0
    assert persisted(review.pg, 'tasks', id=task.id)[0]['state'] == 'queued'
    assert review_worker(review, provider).run_once()
    assert provider.calls == 1
    assert_one_publication(review, task)


def test_real_provider_rejected_before_review_claim(review):
    class PaidProvider:
        simulation_only = False
    task = review_task(review)
    with pytest.raises(ValueError, match='simulated'):
        review_worker(review, PaidProvider())
    assert persisted(review.pg, 'tasks', id=task.id)[0]['state'] == 'queued'
    assert persisted(review.pg, 'task_attempts', task_id=task.id) == []


def test_provider_configuration_drift_preserves_unsent_review(review):
    task = review_task(review)
    provider = FakeReviewProvider()
    provider.model = 'changed-synthetic-model'
    assert review_worker(review, provider).run_once()
    row = persisted(review.pg, 'tasks', id=task.id)[0]
    assert row['state'] == 'failed' and row['error_code'] == 'PROVIDER_CONFIG_CHANGED'
    assert row['may_have_charged'] is False and provider.calls == 0
    assert revisions(review, task) == []
    assert persisted(review.pg, 'task_attempts', task_id=task.id)[0]['submitted_at'] is None


def test_late_result_from_old_worker_is_saved_but_requires_local_reconciliation(review):
    task = review_task(review)
    initial = review_worker(review, worker_id='original-review')
    claim, attempt, request = send_intent(review, task, initial)
    expire(review.pg, task.id)
    recovered = review_worker(review, worker_id='recovered-review')
    recovered.recover_expired()
    assert persisted(review.pg, 'tasks', id=task.id)[0]['state'] == 'unknown'
    reply = initial.provider.generate(request)
    with review.database.transaction(review.owner) as tx:
        tx.tasks.store_result(task.id, attempt.id, reply.raw_output)
    with pytest.raises(StaleFence):
        with review.database.transaction(review.owner) as tx:
            tx.tasks.publish_result(task.id, attempt.id, claim.lease_token, initial.worker_id)
    assert revisions(review, task) == []
    assert recovered.run_once() and recovered.provider.calls == 0
    assert_one_publication(review, task)
    assert len(persisted(review.pg, 'task_attempts', task_id=task.id)) == 1
    assert initial.provider.calls == 1


def test_older_durable_result_closes_later_unsubmitted_attempt(review):
    task = review_task(review)
    initial = review_worker(review)
    _, attempt, _ = send_intent(review, task, initial, save=True)
    unused = uuid4()
    with review.database.transaction(review.owner) as tx:
        tx.connection.execute(tables['task_attempts'].insert().values(id=unused,
            owner_id=review.owner.owner_id, task_id=task.id, attempt_number=2,
            state='planned', provider='fake', provider_outcome='not_submitted'))
    expire(review.pg, task.id)
    recovered = review_worker(review, worker_id='recovered-review')
    assert recovered.run_once() and recovered.provider.calls == 0
    assert_one_publication(review, task)
    assert persisted(review.pg, 'task_attempts', id=attempt.id)[0]['state'] == 'succeeded'
    row = persisted(review.pg, 'task_attempts', id=unused)[0]
    assert row['state'] == 'cancelled' and row['submitted_at'] is None
    assert row['raw_output_ref'] is None and row['finished_at'] is not None
    with review.database.transaction(review.owner) as tx:
        raw = PgReviewGenerationRepository(tx).raw_output(task.id)
        assert raw['attempt_id'] == str(attempt.id)
        assert raw['validation_status'] == 'valid'


def test_concurrent_local_reconciliation_publishes_each_version_once(review):
    task = review_task(review)
    worker = review_worker(review)
    claim, attempt, _ = send_intent(review, task, worker, save=True)
    with review.database.transaction(review.owner) as tx:
        tx.tasks.mark_unknown(task.id, attempt.id, claim.lease_token, worker.worker_id)
    before = len(persisted(review.pg, 'outbox_events', aggregate_id=task.id))
    barrier = Barrier(2)
    def reconcile(_):
        barrier.wait(timeout=5)
        with review.database.transaction(review.owner) as tx:
            return tx.tasks.reconcile_result(task.id, uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(reconcile, range(2)))
    assert first.state == second.state == 'succeeded'
    assert first.revision == second.revision
    assert_one_publication(review, task)
    assert len(persisted(review.pg, 'outbox_events', aggregate_id=task.id)) == before + 1
    assert worker.provider.calls == 1
    assert review.scheduler.claim('cannot-resend', schema_version='pr07b-1') == []


def test_reconcile_receipt_can_complete_after_a_late_result(review):
    task = review_task(review)
    worker = review_worker(review)
    claim, attempt, request = send_intent(review, task, worker)
    with review.database.transaction(review.owner) as tx:
        tx.tasks.mark_unknown(task.id, attempt.id, claim.lease_token, worker.worker_id)
    key = uuid4()
    with review.database.transaction(review.owner) as tx:
        assert tx.tasks.reconcile_result(task.id, key).state == 'unknown'
    reply = worker.provider.generate(request)
    with review.database.transaction(review.owner) as tx:
        tx.tasks.store_result(task.id, attempt.id, reply.raw_output)
    with review.database.transaction(review.owner) as tx:
        completed = tx.tasks.reconcile_result(task.id, key)
    with review.database.transaction(review.owner) as tx:
        replay = tx.tasks.reconcile_result(task.id, key)
    assert completed.state == 'succeeded' and replay.revision == completed.revision
    assert_one_publication(review, task)
    assert len(persisted(review.pg, 'operation_receipts', operation='reconcileTask', op_id=key)) == 1
    assert worker.provider.calls == 1


def test_partial_publication_failure_rolls_back_then_recovers_saved_result(review, monkeypatch):
    task = review_task(review)
    original_append = PgReviewRepository._append
    appended = []
    def failing_append(repository, document, **kwargs):
        result = original_append(repository, document, **kwargs)
        appended.append(result['id'])
        if len(appended) == 2:
            raise RuntimeError('synthetic failure after card version insert')
        return result
    initial = review_worker(review)
    with monkeypatch.context() as patch:
        patch.setattr(PgReviewRepository, '_append', failing_append)
        with pytest.raises(RuntimeError, match='after card version'):
            initial.run_once()
    assert len(appended) == 2 and initial.provider.calls == 1
    assert revisions(review, task) == []
    with review.pg.admin.connect() as connection:
        for table in ('review_documents', 'review_document_history'):
            assert connection.scalar(sa.text(f'SELECT count(*) FROM {table} WHERE owner_id=:owner'),
                {'owner': review.owner.owner_id}) == 0
    attempt = persisted(review.pg, 'task_attempts', task_id=task.id)[0]
    raw = persisted(review.pg, 'provider_results', id=attempt['raw_output_ref'])[0]
    assert raw['validation_status'] == 'pending'
    assert persisted(review.pg, 'tasks', id=task.id)[0]['state'] == 'running'
    expire(review.pg, task.id)
    recovered = review_worker(review, worker_id='after-publication-failure')
    assert recovered.run_once() and recovered.provider.calls == 0
    assert_one_publication(review, task)
    assert persisted(review.pg, 'tasks', id=task.id)[0]['state'] == 'succeeded'


@pytest.mark.parametrize('during_send', [False, True])
def test_cancel_before_or_during_review_generation_preserves_candidate_semantics(review, during_send):
    task = review_task(review)
    class CancellingProvider(FakeReviewProvider):
        def generate(self, request):
            with review.database.transaction(review.owner) as tx:
                tx.tasks.cancel(task.id, uuid4())
            return super().generate(request)
    provider = CancellingProvider() if during_send else FakeReviewProvider()
    if not during_send:
        key = uuid4()
        with review.database.transaction(review.owner) as tx:
            cancelled = tx.tasks.cancel(task.id, key)
        with review.database.transaction(review.owner) as tx:
            assert tx.tasks.cancel(task.id, key).revision == cancelled.revision
    review_worker(review, provider).run_once()
    result = persisted(review.pg, 'tasks', id=task.id)[0]
    assert result['state'] == ('succeeded' if during_send else 'cancelled')
    assert result['cancel_requested_at'] is not None
    assert provider.calls == int(during_send)
    if during_send:
        assert_one_publication(review, task)
    else:
        assert revisions(review, task) == []


def test_cancel_after_intent_without_raw_preserves_unknown_and_never_resends(review):
    task = review_task(review)
    worker = review_worker(review)
    send_intent(review, task, worker)
    with review.database.transaction(review.owner) as tx:
        assert tx.tasks.cancel(task.id, uuid4()).state == 'cancel_requested'
    expire(review.pg, task.id)
    assert not worker.run_once()
    assert not worker.run_once()
    result = persisted(review.pg, 'tasks', id=task.id)[0]
    assert result['state'] == 'unknown' and result['may_have_charged']
    assert worker.provider.calls == 0 and revisions(review, task) == []


def test_provider_timeout_keeps_unknown_without_retry(review):
    class TimeoutProvider(FakeReviewProvider):
        def generate(self, request):
            self.calls += 1
            raise ProviderFailure('TIMEOUT', retryable=True, may_have_charged=True)
    task = review_task(review)
    provider = TimeoutProvider()
    worker = review_worker(review, provider)
    assert worker.run_once() and provider.calls == 1
    assert not worker.run_once() and provider.calls == 1
    row = persisted(review.pg, 'tasks', id=task.id)[0]
    assert row['state'] == 'unknown' and row['error_code'] == 'PROVIDER_RESULT_UNKNOWN'
    assert len(persisted(review.pg, 'task_attempts', task_id=task.id)) == 1
    assert revisions(review, task) == []


@pytest.mark.parametrize('invalid', [
    'json', 'foreign_source', 'empty_answer', 'partial', 'control_field', 'summary_size', 'unrequested_english',
])
def test_invalid_review_output_preserves_raw_and_creates_no_partial_documents(review, invalid):
    payload = valid_output()
    if invalid == 'foreign_source':
        payload['notes'][0]['source_ids'] = ['S999']
    elif invalid == 'empty_answer':
        payload['cards'][0]['back'] = ' '
    elif invalid == 'partial':
        broken = deepcopy(payload['cards'][0])
        broken['front'] = '另一张卡片的问题？'
        broken.pop('back')
        payload['cards'].append(broken)
    elif invalid == 'control_field':
        payload['cards'][0]['adopted_revision_id'] = str(uuid4())
    elif invalid == 'summary_size':
        template = payload['notes'][0]
        payload['notes'] = [dict(template, title=f'Topic {index}', body='x' * 11000 + str(index))
                            for index in range(11)]
    elif invalid == 'unrequested_english':
        payload['cards'][0].update(english='Synthetic English', english_meaning='合成中文解释')
    raw = 'not JSON' if invalid == 'json' else json.dumps(payload, ensure_ascii=False)
    task = review_task(review)
    provider = FakeReviewProvider(output=raw)
    assert review_worker(review, provider).run_once()
    result = persisted(review.pg, 'tasks', id=task.id)[0]
    assert result['state'] == 'failed' and result['error_code'] == 'OUTPUT_INVALID'
    assert revisions(review, task) == []
    attempt = persisted(review.pg, 'task_attempts', task_id=task.id)[0]
    assert attempt['provider_outcome'] == 'succeeded' and attempt['state'] == 'failed'
    saved = persisted(review.pg, 'provider_results', id=attempt['raw_output_ref'])[0]
    assert saved['validation_status'] == 'invalid' and saved['raw_output'] == raw
    with review.database.transaction(review.owner) as tx:
        assert PgReviewGenerationRepository(tx).raw_output(task.id)['raw_text'] == raw
    assert provider.calls == 1


@pytest.mark.parametrize('submitted', [False, True])
def test_revoked_source_recovers_through_metadata_without_body_access(review, submitted):
    task = review_task(review)
    worker = review_worker(review)
    if submitted:
        send_intent(review, task, worker)
    else:
        assert review.scheduler.claim(worker.worker_id, schema_version='pr07b-1')[0].id == task.id
    with review.database.transaction(review.owner) as tx:
        PgQaRepository(tx).revoke_source(review.source.source_id)
    expire(review.pg, task.id)
    worker.recover_expired()
    row = persisted(review.pg, 'tasks', id=task.id)[0]
    assert row['state'] == ('unknown' if submitted else 'cancelled')
    assert row['may_have_charged'] is submitted and row['lease_expires_at'] is None
    assert worker.provider.calls == 0
    with review.database.transaction(review.owner) as tx:
        with pytest.raises(NotFound):
            tx.tasks.get(task.id)
        assert tx.connection.scalar(sa.text('SELECT count(*) FROM task_attempts WHERE task_id=:id'),
                                    {'id': task.id}) == 0
    with review.scheduler.engine.connect() as connection, pytest.raises(sa.exc.DBAPIError):
        connection.execute(sa.text('SELECT request FROM generation_plans'))


def test_committed_source_lock_orders_real_concurrent_revocation_and_send_intent(review):
    task = review_task(review)
    worker = review_worker(review)
    claim = review.scheduler.claim(worker.worker_id, schema_version='pr07b-1')[0]
    with review.database.transaction(review.owner) as tx:
        attempt = tx.tasks.prepare_attempt(task.id, claim.lease_token, worker.worker_id)
    intent_ready, revoker_ready, release = Event(), Event(), Event()
    revoker_pid = []
    def commit_intent():
        with review.database.transaction(review.owner) as tx:
            request = worker._send_intent(tx, task, attempt, claim)
            assert request is not None
            intent_ready.set()
            assert release.wait(5)
    def revoke():
        assert intent_ready.wait(5)
        with review.database.transaction(review.owner) as tx:
            revoker_pid.append(tx.connection.scalar(sa.text('SELECT pg_backend_pid()')))
            revoker_ready.set()
            PgQaRepository(tx).revoke_source(review.source.source_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        sending = pool.submit(commit_intent)
        revoking = pool.submit(revoke)
        try:
            assert revoker_ready.wait(5)
            deadline = time.monotonic() + 3
            blocked = []
            with review.pg.admin.connect() as connection:
                while time.monotonic() < deadline and not blocked:
                    blocked = connection.scalar(sa.text('SELECT pg_blocking_pids(:pid)'),
                                                {'pid': revoker_pid[0]})
                    if not blocked:
                        time.sleep(0.01)
            assert blocked, 'revocation must wait for the existing source share lock'
        finally:
            release.set()
        sending.result(timeout=5)
        revoking.result(timeout=5)
    assert persisted(review.pg, 'task_attempts', id=attempt.id)[0]['submitted_at'] is not None
    expire(review.pg, task.id)
    worker.recover_expired()
    assert persisted(review.pg, 'tasks', id=task.id)[0]['state'] == 'unknown'
    assert worker.provider.calls == 0 and revisions(review, task) == []


def test_revocation_winning_real_pg_race_prevents_send_intent(review):
    task = review_task(review)
    worker = review_worker(review)
    claim = review.scheduler.claim(worker.worker_id, schema_version='pr07b-1')[0]
    with review.database.transaction(review.owner) as tx:
        attempt = tx.tasks.prepare_attempt(task.id, claim.lease_token, worker.worker_id)
    revoked, sender_ready, release = Event(), Event(), Event()
    sender_pid = []
    def revoke():
        with review.database.transaction(review.owner) as tx:
            PgQaRepository(tx).revoke_source(review.source.source_id)
            revoked.set()
            assert release.wait(5)
    def try_send():
        assert revoked.wait(5)
        try:
            with review.database.transaction(review.owner) as tx:
                sender_pid.append(tx.connection.scalar(sa.text('SELECT pg_backend_pid()')))
                sender_ready.set()
                worker._send_intent(tx, task, attempt, claim)
        except (QaRejected, NotFound):
            return 'revoked'
        return 'sent'
    with ThreadPoolExecutor(max_workers=2) as pool:
        revoking = pool.submit(revoke)
        sending = pool.submit(try_send)
        try:
            assert sender_ready.wait(5)
            deadline = time.monotonic() + 3
            blocked = []
            with review.pg.admin.connect() as connection:
                while time.monotonic() < deadline and not blocked:
                    blocked = connection.scalar(sa.text('SELECT pg_blocking_pids(:pid)'),
                                                {'pid': sender_pid[0]})
                    if not blocked:
                        time.sleep(0.01)
            assert blocked, 'send authorization must wait for the source revocation lock'
        finally:
            release.set()
        revoking.result(timeout=5)
        assert sending.result(timeout=5) == 'revoked'
    assert persisted(review.pg, 'task_attempts', id=attempt.id)[0]['submitted_at'] is None
    expire(review.pg, task.id)
    worker.recover_expired()
    assert persisted(review.pg, 'tasks', id=task.id)[0]['state'] == 'cancelled'
    assert worker.provider.calls == 0 and revisions(review, task) == []


def switch(control, enabled):
    with control.pg.admin.begin() as connection:
        revision = connection.scalar(sa.text('SELECT revision FROM billing_control.switches'))
        connection.execute(sa.text('''SELECT billing_api.update_switches(
            :actor,:op,:revision,CAST(:flags AS jsonb),:reason)'''),
            dict(actor=control.actor, op=uuid4(), revision=revision,
                 flags=json.dumps({'billing': enabled, 'ai_summary': enabled}),
                 reason='Synthetic PR-07b recovery test'))


@pytest.fixture
def billed_review(review, fee_control):
    switch(fee_control, True)
    price = uuid4()
    with review.pg.admin.begin() as connection:
        connection.execute(sa.text('''INSERT INTO price_catalog(id,provider,model,region,revision,currency,rates,
            minimum_microyuan,synthetic,effective_at) VALUES(:id,'fake','review-deterministic-v1','synthetic',
            :revision,'CNY',CAST(:rates AS jsonb),0,true,'2020-01-01T00:00:00Z')'''),
            dict(id=price, revision=str(price), rates=json.dumps({
                'input_tokens': {'microyuan': 1, 'units': 1},
                'output_tokens': {'microyuan': 1, 'units': 1}})))
    review.config = dict(review.config, price_id=str(price), price_version=str(price),
                         maximum_cost_microyuan=100000, billing_required=True)
    review.billing = fee_control.lifecycle
    review.fee_control = fee_control
    return review


def fee_state(review, task):
    with review.pg.admin.connect() as connection:
        return connection.execute(sa.text('''SELECT r.*,e.held,e.unknown,e.settled FROM quota_reservations r
            JOIN billing_control.exposures e ON e.reservation_id=r.id WHERE r.task_id=:id'''),
            {'id': task.id}).mappings().one()


def test_review_plan_idempotency_reserves_one_quota_and_settles_once(billed_review):
    review = billed_review
    plan = prepare_review(review)
    key = uuid4()
    task = execute_review(review, plan, key)
    assert execute_review(review, plan, key).id == task.id
    assert execute_review(review, plan, uuid4()).id == task.id
    assert fee_state(review, task)['state'] == 'reserved'
    with review.pg.admin.connect() as connection:
        rows = connection.execute(sa.text('''SELECT q.resource,a.quantity FROM reservation_allocations a
            JOIN quota_periods q ON q.id=a.quota_period_id WHERE a.reservation_id=:id'''),
            {'id': fee_state(review, task)['id']}).all()
    assert rows == [('summary_month_count', 1)]
    class InspectProvider(FakeReviewProvider):
        def generate(self, request):
            row = fee_state(review, task)
            assert row['state'] == 'submitted'
            with review.pg.admin.connect() as connection:
                assert connection.scalar(sa.text('SELECT count(*) FROM billing_permits WHERE reservation_id=:id'),
                                          {'id': row['id']}) == 1
            self.reply = super().generate(request)
            return self.reply
    provider = InspectProvider()
    assert review_worker(review, provider, billing=review.billing).run_once()
    review.billing.completed(review.owner.owner_id, task.id, provider.reply.usage)
    row = fee_state(review, task)
    assert row['state'] == 'settled' and row['unknown'] == row['held'] == 0
    assert row['settled'] == sum(provider.reply.usage.quantities.values())
    with review.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT count(*) FROM usage_receipts WHERE reservation_id=:id'), row) == 1
        assert connection.scalar(sa.text('SELECT count(*) FROM quota_reservations WHERE task_id=:task'),
                                 {'task': task.id}) == 1
    assert_one_publication(review, task)
    assert provider.calls == 1


def test_missing_usage_keeps_unknown_until_idempotent_late_usage(billed_review):
    review = billed_review
    task = review_task(review)
    provider = FakeReviewProvider(missing_usage=True)
    worker = review_worker(review, provider, billing=review.billing)
    assert worker.run_once()
    assert_one_publication(review, task)
    row = fee_state(review, task)
    assert row['state'] == 'unknown' and row['unknown'] > 0 and row['held'] == 0
    usage = Usage(provider_request_id='review-late-' + str(task.id), meter='tokens', revision=1,
                  quantities={'input_tokens': 100, 'output_tokens': 20}, final=True)
    review.billing.late_usage(review.owner.owner_id, task.id, usage)
    review.billing.late_usage(review.owner.owner_id, task.id, usage)
    row = fee_state(review, task)
    assert row['state'] == 'settled' and row['settled'] == 120 and row['unknown'] == 0
    assert not worker.run_once() and provider.calls == 1


def test_fee_reservation_denials_leave_plan_unconsumed_and_create_no_extra_task(billed_review):
    review = billed_review
    first = review_task(review)
    with review.pg.admin.begin() as connection:
        connection.execute(sa.text('UPDATE quota_periods SET limit_value=1 WHERE owner_id=:owner'),
                           {'owner': review.owner.owner_id})
    blocked_plan = prepare_review(review)
    with pytest.raises(QuotaExceeded):
        execute_review(review, blocked_plan)
    switch(review.fee_control, False)
    provider = FakeReviewProvider()
    review_worker(review, provider, billing=review.billing).run_once()
    assert provider.calls == 0 and fee_state(review, first)['state'] == 'reserved'
    with pytest.raises(BillingBlocked):
        execute_review(review, blocked_plan)
    with review.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT consumed_task_id FROM generation_plans WHERE id=:id'),
                                 {'id': UUID(blocked_plan['id'])}) is None
        assert connection.scalar(sa.text('SELECT count(*) FROM tasks WHERE owner_id=:owner'),
                                 {'owner': review.owner.owner_id}) == 1
        assert connection.scalar(sa.text('SELECT count(*) FROM quota_reservations WHERE owner_id=:owner'),
                                 {'owner': review.owner.owner_id}) == 1
    assert revisions(review, first) == []


@pytest.mark.parametrize('submitted', [False, True])
def test_revocation_releases_unsent_reservation_and_preserves_sent_unknown(billed_review, submitted):
    review = billed_review
    task = review_task(review)
    worker = review_worker(review, billing=review.billing)
    if submitted:
        send_intent(review, task, worker)
    else:
        review.scheduler.claim(worker.worker_id, schema_version='pr07b-1')
    with review.database.transaction(review.owner) as tx:
        PgQaRepository(tx).revoke_source(review.source.source_id)
    expire(review.pg, task.id)
    worker.recover_expired()
    row = fee_state(review, task)
    assert row['state'] == ('unknown' if submitted else 'released')
    assert row['held'] == 0 and (row['unknown'] > 0) is submitted
    assert revisions(review, task) == [] and worker.provider.calls == 0


@pytest.mark.parametrize('resource', ['source', 'classroom', 'account'])
def test_returned_usage_is_settled_when_revocation_blocks_review_body(billed_review, resource):
    review = billed_review
    task = review_task(review)
    class RevokingProvider(FakeReviewProvider):
        def generate(self, request):
            reply = super().generate(request)
            if resource == 'source':
                with review.database.transaction(review.owner) as tx:
                    PgQaRepository(tx).revoke_source(review.source.source_id)
            elif resource == 'classroom':
                with review.database.transaction(review.owner) as tx:
                    tx.tombstones.revoke(Resource.CLASS_SESSION, review.tree.classroom.id,
                                         1, ledger_sequence=1)
            else:
                with review.pg.admin.begin() as connection:
                    connection.execute(sa.text("UPDATE users SET state='disabled' WHERE id=:id"),
                                       {'id': review.owner.owner_id})
            self.usage = reply.usage
            return reply
    provider = RevokingProvider()
    review_worker(review, provider, billing=review.billing).run_once()
    row = fee_state(review, task)
    assert row['state'] == 'settled' and row['unknown'] == row['held'] == 0
    assert row['settled'] == sum(provider.usage.quantities.values())
    assert revisions(review, task) == [] and provider.calls == 1
    with review.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT count(*) FROM usage_receipts WHERE reservation_id=:id'), row) == 1
        assert connection.scalar(sa.text('''SELECT count(*) FROM provider_results r JOIN task_attempts a
            ON a.id=r.attempt_id WHERE a.task_id=:id'''), {'id': task.id}) == 0


def test_returned_usage_survives_raw_result_storage_failure(billed_review, monkeypatch):
    review = billed_review
    task = review_task(review)
    def broken_store(*args, **kwargs):
        raise sa.exc.OperationalError('synthetic review result storage interruption', {}, None)
    provider = FakeReviewProvider()
    with monkeypatch.context() as patch:
        patch.setattr(PgTaskRepository, 'store_result', broken_store)
        with pytest.raises(sa.exc.OperationalError):
            review_worker(review, provider, billing=review.billing).run_once()
    assert fee_state(review, task)['state'] == 'settled'
    assert revisions(review, task) == []
    expire(review.pg, task.id)
    recovered = review_worker(review, billing=review.billing)
    recovered.run_once()
    assert recovered.provider.calls == 0 and provider.calls == 1
    assert persisted(review.pg, 'tasks', id=task.id)[0]['state'] == 'unknown'
    assert fee_state(review, task)['state'] == 'settled'


def test_invalid_review_output_still_settles_returned_usage(billed_review):
    review = billed_review
    task = review_task(review)
    provider = FakeReviewProvider(output='not valid JSON')
    assert review_worker(review, provider, billing=review.billing).run_once()
    assert persisted(review.pg, 'tasks', id=task.id)[0]['state'] == 'failed'
    assert fee_state(review, task)['state'] == 'settled'
    assert revisions(review, task) == [] and provider.calls == 1


def test_revoked_regeneration_target_terminates_saved_result_and_settles_usage(billed_review):
    review = billed_review
    original = review_task(review)
    assert review_worker(review, billing=review.billing).run_once()
    with review.database.transaction(review.owner) as tx:
        generation = PgReviewGenerationRepository(tx)
        target_id = generation.result_id(original.id)
        document = PgReviewRepository(tx).document(target_id)
        second = PgQaRepository(tx).add_source(review.tree.classroom.id,
            kind='transcript_segment', object_id=uuid4(), text='A separately authorized replacement source.',
            start_ms=5000, end_ms=8000)
    reference = dict(kind=second.kind, id=str(second.source_id),
                     revision_id=str(second.revision_id), content_sha256=second.content_sha256)
    plan = prepare_review(review, target_document_id=str(target_id),
                          base_revision=document['revision'], seen_sources=[reference])
    task = execute_review(review, plan)

    class RevokeTargetProvider(FakeReviewProvider):
        def generate(self, request):
            reply = super().generate(request)
            with review.database.transaction(review.owner) as tx:
                PgQaRepository(tx).revoke_source(review.source.source_id)
            return reply

    provider = RevokeTargetProvider()
    worker = review_worker(review, provider, billing=review.billing)
    assert worker.run_once()
    result = persisted(review.pg, 'tasks', id=task.id)[0]
    assert result['state'] == 'failed' and result['error_code'] == 'RESULT_TARGET_UNAVAILABLE'
    assert result['lease_expires_at'] is None and result['completed_at'] is not None
    assert revisions(review, task) == [] and fee_state(review, task)['state'] == 'settled'
    with review.database.transaction(review.owner) as tx:
        raw = PgReviewGenerationRepository(tx).raw_output(task.id)
        assert raw['validation_status'] == 'valid'
        assert raw['error_code'] == 'RESULT_TARGET_UNAVAILABLE'
    assert not worker.run_once() and provider.calls == 1


REVIEW_HARD_EXIT = '''
import os
from pathlib import Path
import sqlalchemy as sa
from classroom_web.application.billing_tasks import BillingTaskLifecycle
from classroom_web.application.review_worker import ReviewWorker
from classroom_web.infrastructure.database import Database
from classroom_web.infrastructure.review_provider import FakeReviewProvider
from classroom_web.infrastructure.task_scheduler import PgTaskScheduler
class JournalProvider(FakeReviewProvider):
    def generate(self, request):
        with Path(os.environ['PR07B_CALL_JOURNAL']).open('a', encoding='utf-8') as stream:
            stream.write(str(request.attempt_id)+'\\n')
            stream.flush()
            os.fsync(stream.fileno())
        if os.environ['PR07B_CRASH_POINT'] == 'send':
            os._exit(74)
        return super().generate(request)
class CrashLifecycle(BillingTaskLifecycle):
    def completed(self, owner_id, task_id, usage):
        os._exit(73)
database=Database(os.environ['PR07B_RUNTIME_URL'])
scheduler=PgTaskScheduler(os.environ['PR07B_SCHEDULER_URL'])
engine=sa.create_engine(os.environ['PR07B_RECONCILER_URL'],hide_parameters=True)
ReviewWorker(database,scheduler,JournalProvider(),worker_id='review-hard-exit',
             billing=CrashLifecycle(engine),enabled=True).run_once()
raise RuntimeError('expected hard exit at the selected review boundary')
'''


@pytest.mark.parametrize('boundary,exit_code', [('send', 74), ('raw', 73)])
def test_real_process_exit_preserves_review_state_and_prevents_resend(billed_review, tmp_path,
                                                                    boundary, exit_code):
    review = billed_review
    task = review_task(review)
    journal = tmp_path / 'synthetic-review-provider-calls.log'
    environment = os.environ.copy()
    environment.update(PR07B_RUNTIME_URL=review.pg.runtime_url.render_as_string(hide_password=False),
        PR07B_SCHEDULER_URL=review.scheduler.engine.url.render_as_string(hide_password=False),
        PR07B_RECONCILER_URL=review.fee_control.reconciler.url.render_as_string(hide_password=False),
        PR07B_CALL_JOURNAL=str(journal), PR07B_CRASH_POINT=boundary)
    process = subprocess.run([sys.executable, '-B', '-c', REVIEW_HARD_EXIT],
                             env=environment, capture_output=True, timeout=30)
    assert process.returncode == exit_code, process.stderr.decode('utf-8', errors='replace')
    assert len(journal.read_text(encoding='utf-8').splitlines()) == 1
    assert fee_state(review, task)['state'] == 'submitted'
    attempt = persisted(review.pg, 'task_attempts', task_id=task.id)[0]
    assert (attempt['raw_output_ref'] is not None) is (boundary == 'raw')
    assert revisions(review, task) == []
    expire(review.pg, task.id)
    recovered = review_worker(review, billing=review.billing, worker_id='after-hard-exit')
    recovered.run_once()
    row = persisted(review.pg, 'tasks', id=task.id)[0]
    assert row['state'] == ('succeeded' if boundary == 'raw' else 'unknown')
    if boundary == 'raw':
        assert_one_publication(review, task)
    else:
        assert revisions(review, task) == []
    assert not recovered.run_once() and recovered.provider.calls == 0
    assert fee_state(review, task)['state'] == 'unknown'
    assert fee_state(review, task)['unknown'] > 0 and fee_state(review, task)['held'] == 0
    assert len(persisted(review.pg, 'task_attempts', task_id=task.id)) == 1
    assert len(journal.read_text(encoding='utf-8').splitlines()) == 1
