"""PR-07a real PostgreSQL recovery, dispatch and synthetic fee integration."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from conftest import migrated_database
from test_billing_integration import fee_control
from test_persistent_tasks import (
    INPUT, WORKER, claim_one, enqueue, expire, persisted, prepared,
    scheduler, submitting, task_db,
)

from classroom_web.application.qa_worker import QaWorker
from classroom_web.application.task_worker import MockProvider, TaskWorker
from classroom_web.domain.billing import BillingBlocked, QuotaExceeded, Usage
from classroom_web.domain.data import NotFound, Resource
from classroom_web.domain.qa import ProviderFailure
from classroom_web.infrastructure.local_reconciler import PgLocalResultReconciler
from classroom_web.infrastructure.qa_provider import FakeProvider
from classroom_web.infrastructure.qa_repository import PgQaRepository
from classroom_web.infrastructure.task_schema import tables
from classroom_web.infrastructure.task_repository import PgTaskRepository


@pytest.fixture(scope='module')
def pg():
    with migrated_database() as value:
        yield value


@pytest.fixture
def qa(task_db, scheduler, owners, tree, pg):
    config = dict(destination=dict(provider='fake', model='qa-deterministic-v1',
        region='synthetic', configuration_revision='pr07a-test'), price_id=None,
        price_version='synthetic-no-charge', maximum_cost_microyuan=0,
        input_token_budget=8192, max_output_tokens=128, enabled=True, billing_required=False)
    with task_db.transaction(owners.a) as tx:
        source = PgQaRepository(tx).add_source(tree.classroom.id,
            kind='transcript_segment', object_id=uuid4(), text='合成课堂讨论机会成本与资源稀缺。',
            start_ms=1000, end_ms=3000)
    return SimpleNamespace(database=task_db, scheduler=scheduler, owner=owners.a,
                           tree=tree, pg=pg, source=source, config=config)


def prepare_question(qa):
    request = dict(kind='ai_question', question='课堂如何解释机会成本？', scope='all',
                   include_images=False, seen_sources=[], accept_partial_scope=True)
    with qa.database.transaction(qa.owner) as tx:
        return PgQaRepository(tx).prepare(qa.tree.classroom.id, request, uuid4(), qa.config)


def execute_question(qa, plan, key=None):
    body = dict(plan_sha256=plan['plan_sha256'], confirm_send=True, confirm_images=False,
        confirm_original_snapshot=True, confirm_partial_scope=True,
        confirmed_destination_revision=qa.config['destination']['configuration_revision'])
    with qa.database.transaction(qa.owner) as tx:
        return PgQaRepository(tx).execute(UUID(plan['id']), body, key or uuid4(), qa.config)


def question_task(qa):
    plan = prepare_question(qa)
    return execute_question(qa, plan)


def qa_worker(qa, provider=None, *, billing=None, enabled=True, worker_id='qa-test-worker'):
    return QaWorker(qa.database, qa.scheduler, provider or FakeProvider(),
                    worker_id=worker_id, enabled=enabled, billing=billing)


def send_intent(qa, task, worker, *, save=False):
    claim = qa.scheduler.claim(worker.worker_id, 30, 1, schema_version='pr07a-1')[0]
    assert claim.id == task.id
    with qa.database.transaction(qa.owner) as tx:
        attempt = tx.tasks.prepare_attempt(task.id, claim.lease_token, worker.worker_id)
    with qa.database.transaction(qa.owner) as tx:
        request = worker._send_intent(tx, task, attempt, claim)
    reply = worker.provider.generate(request) if save else None
    if reply is not None:
        with qa.database.transaction(qa.owner) as tx:
            tx.tasks.store_result(task.id, attempt.id, reply.raw_output)
    return claim, attempt, reply


def question_row(qa, task):
    with qa.pg.admin.connect() as connection:
        return connection.execute(sa.text('SELECT * FROM questions WHERE task_id=:id'),
                                  {'id': task.id}).mappings().one()


def test_same_reconcile_key_without_raw_then_late_raw_finishes_once(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    submitting(task_db, owners.a, task, attempt, claim)
    with task_db.transaction(owners.a) as tx:
        tx.tasks.mark_unknown(task.id, attempt.id, claim.lease_token, WORKER)
    key = uuid4()
    with task_db.transaction(owners.a) as tx:
        assert tx.tasks.reconcile_result(task.id, key).state == 'unknown'
    before = len(persisted(pg, 'outbox_events', aggregate_id=task.id))
    with task_db.transaction(owners.a) as tx:
        tx.tasks.store_result(task.id, attempt.id, '{"answer":"late synthetic"}')
    with task_db.transaction(owners.a) as tx:
        completed = tx.tasks.reconcile_result(task.id, key)
    with task_db.transaction(owners.a) as tx:
        replay = tx.tasks.reconcile_result(task.id, key)
    assert completed.state == 'succeeded' and replay.revision == completed.revision
    assert len(persisted(pg, 'operation_receipts', operation='reconcileTask', op_id=key)) == 1
    assert len(persisted(pg, 'outbox_events', aggregate_id=task.id)) == before + 1
    assert len(persisted(pg, 'task_attempts', task_id=task.id)) == 1
    assert scheduler.claim('cannot-resend') == []


def test_finishing_older_raw_cancels_unsubmitted_plan_without_cancel_request(task_db, scheduler, owners, pg):
    task = enqueue(task_db, owners.a)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    submitting(task_db, owners.a, task, attempt, claim)
    newer = uuid4()
    with task_db.transaction(owners.a) as tx:
        tx.tasks.store_result(task.id, attempt.id, '{"answer":"first durable result"}')
        tx.connection.execute(tables['task_attempts'].insert().values(id=newer,
            owner_id=owners.a.owner_id, task_id=task.id, attempt_number=2,
            state='planned', provider='mock', provider_outcome='not_submitted'))
    expire(pg, task.id)
    provider = MockProvider()
    assert TaskWorker(task_db, scheduler, provider, worker_id='recovered').run_once()
    assert provider.calls == 0
    result = persisted(pg, 'tasks', id=task.id)[0]
    unused = persisted(pg, 'task_attempts', id=newer)[0]
    assert result['state'] == 'succeeded' and result['cancel_requested_at'] is None
    assert unused['state'] == 'cancelled' and unused['submitted_at'] is None
    assert unused['finished_at'] is not None and unused['raw_output_ref'] is None
    assert scheduler.claim('cannot-reclaim-finished') == []


def test_default_disabled_and_generic_workers_do_not_dispatch_question(qa):
    task = question_task(qa)
    provider = FakeProvider()
    assert not qa_worker(qa, provider, enabled=False).run_once()
    generic = MockProvider()
    assert not TaskWorker(qa.database, qa.scheduler, generic, worker_id='generic').run_once()
    assert provider.calls == generic.calls == 0
    assert persisted(qa.pg, 'tasks', id=task.id)[0]['state'] == 'queued'
    assert qa_worker(qa, provider).run_once() and provider.calls == 1
    assert question_row(qa, task)['answer_markdown'] == '合成课堂回答。'


def test_real_provider_is_rejected_before_claim(qa):
    class PaidProvider:
        simulation_only = False
    with pytest.raises(ValueError, match='simulated'):
        qa_worker(qa, PaidProvider())


def test_provider_configuration_drift_fails_before_send(qa):
    task = question_task(qa)
    provider = FakeProvider()
    provider.model = 'different-synthetic-model'
    qa_worker(qa, provider).run_once()
    row = persisted(qa.pg, 'tasks', id=task.id)[0]
    assert row['state'] == 'failed' and row['error_code'] == 'PROVIDER_CONFIG_CHANGED'
    assert row['may_have_charged'] is False and provider.calls == 0
    assert persisted(qa.pg, 'task_attempts', task_id=task.id)[0]['submitted_at'] is None


def test_saved_question_result_survives_worker_loss_without_resend(qa):
    task = question_task(qa)
    initial = qa_worker(qa)
    _, attempt, _ = send_intent(qa, task, initial, save=True)
    expire(qa.pg, task.id)
    restarted = qa_worker(qa, worker_id='restarted')
    assert restarted.run_once() and restarted.provider.calls == 0
    assert persisted(qa.pg, 'tasks', id=task.id)[0]['state'] == 'succeeded'
    assert question_row(qa, task)['cited_source_ids'] == ['S1']
    assert len(persisted(qa.pg, 'task_attempts', task_id=task.id)) == 1
    assert persisted(qa.pg, 'task_attempts', id=attempt.id)[0]['state'] == 'succeeded'


def test_provider_timeout_and_late_result_are_reconciled_without_new_send(qa):
    class TimeoutProvider(FakeProvider):
        def generate(self, request):
            self.calls += 1
            raise ProviderFailure('TIMEOUT', retryable=True, may_have_charged=True)
    task = question_task(qa)
    provider = TimeoutProvider()
    worker = qa_worker(qa, provider)
    assert worker.run_once() and provider.calls == 1
    assert not worker.run_once() and provider.calls == 1
    attempt = persisted(qa.pg, 'task_attempts', task_id=task.id)[0]
    with qa.database.transaction(qa.owner) as tx:
        tx.tasks.store_result(task.id, attempt['id'], '{"answer_markdown":"迟到回答","source_ids":["S1"]}')
    assert worker.run_once() and provider.calls == 1
    assert question_row(qa, task)['answer_markdown'] == '迟到回答'
    assert len(persisted(qa.pg, 'task_attempts', task_id=task.id)) == 1


@pytest.mark.parametrize('raw', [
    '{"answer_markdown":"伪造引用","source_ids":["S999"]}',
    '{"answer_markdown":"重复引用","source_ids":["S1","S1"]}',
    '{"answer_markdown":"空引用","source_ids":[]}',
    '{"answer_markdown":"x","source_ids":["S1"],"tool_call":"read-all"}',
])
def test_invalid_provider_citations_fail_atomically(qa, raw):
    task = question_task(qa)
    provider = FakeProvider(output=raw)
    assert qa_worker(qa, provider).run_once()
    result = persisted(qa.pg, 'tasks', id=task.id)[0]
    assert result['state'] == 'failed' and result['error_code'] == 'OUTPUT_INVALID'
    assert question_row(qa, task)['answer_markdown'] is None
    assert provider.calls == 1


def test_concurrent_local_completion_writes_one_answer_and_one_terminal_event(qa):
    task = question_task(qa)
    worker = qa_worker(qa)
    claim, attempt, _ = send_intent(qa, task, worker, save=True)
    with qa.database.transaction(qa.owner) as tx:
        tx.tasks.mark_unknown(task.id, attempt.id, claim.lease_token, worker.worker_id)
    before = len(persisted(qa.pg, 'outbox_events', aggregate_id=task.id))
    def finish():
        with qa.database.transaction(qa.owner) as tx:
            return tx.tasks.reconcile_result(task.id, uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: finish(), range(2)))
    assert first.state == second.state == 'succeeded'
    assert first.revision == second.revision
    assert question_row(qa, task)['revision'] == 2
    assert len(persisted(qa.pg, 'outbox_events', aggregate_id=task.id)) == before + 1
    assert worker.provider.calls == 1


@pytest.mark.parametrize('submitted', [False, True])
def test_source_revocation_recovers_using_metadata_only(qa, submitted):
    task = question_task(qa)
    worker = qa_worker(qa)
    if submitted:
        send_intent(qa, task, worker)
    else:
        assert qa.scheduler.claim(worker.worker_id, schema_version='pr07a-1')[0].id == task.id
    with qa.database.transaction(qa.owner) as tx:
        PgQaRepository(tx).revoke_source(qa.source.source_id)
    expire(qa.pg, task.id)
    worker.recover_expired()
    row = persisted(qa.pg, 'tasks', id=task.id)[0]
    assert row['state'] == ('unknown' if submitted else 'cancelled')
    assert row['may_have_charged'] is submitted
    assert row['lease_expires_at'] is None and worker.provider.calls == 0
    with qa.database.transaction(qa.owner) as tx:
        with pytest.raises(NotFound):
            tx.tasks.get(task.id)
        assert tx.connection.scalar(sa.text('SELECT count(*) FROM task_attempts WHERE task_id=:id'), {'id':task.id}) == 0
    with qa.scheduler.engine.connect() as connection, pytest.raises(sa.exc.DBAPIError):
        connection.execute(sa.text('SELECT request FROM generation_plans'))


def test_generic_sql_reconciler_cannot_publish_question_when_account_disabled(qa):
    task = question_task(qa)
    worker = qa_worker(qa)
    claim, attempt, _ = send_intent(qa, task, worker, save=True)
    with qa.database.transaction(qa.owner) as tx:
        tx.tasks.mark_unknown(task.id, attempt.id, claim.lease_token, worker.worker_id)
    with qa.pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE users SET state='disabled' WHERE id=:id"), {'id':qa.owner.owner_id})
    reconciler = PgLocalResultReconciler(qa.database)
    assert all(candidate.id != task.id for candidate in reconciler.candidates())
    assert not reconciler.reconcile(claim)
    assert question_row(qa, task)['answer_markdown'] is None
    assert persisted(qa.pg, 'tasks', id=task.id)[0]['state'] == 'unknown'


@pytest.mark.parametrize('resource', ['class_sessions', 'courses', 'semesters'])
def test_saved_question_candidates_exclude_deleted_lineage(qa, resource):
    task = question_task(qa)
    worker = qa_worker(qa)
    claim, attempt, _ = send_intent(qa, task, worker, save=True)
    with qa.database.transaction(qa.owner) as tx:
        tx.tasks.mark_unknown(task.id, attempt.id, claim.lease_token, worker.worker_id)
    resource_type, identity = {
        'class_sessions':(Resource.CLASS_SESSION, qa.tree.classroom.id),
        'courses':(Resource.COURSE, qa.tree.course.id),
        'semesters':(Resource.SEMESTER, qa.tree.semester.id),
    }[resource]
    with qa.database.transaction(qa.owner) as tx:
        tx.tombstones.revoke(resource_type, identity, 1, ledger_sequence=1)
    assert all(candidate.id != task.id for candidate in qa.scheduler.question_results())
    assert not worker.run_once()
    assert question_row(qa, task)['answer_markdown'] is None


@pytest.mark.parametrize('during_send', [False, True])
def test_question_cancel_preserves_existing_task_semantics(qa, during_send):
    task = question_task(qa)
    class CancellingProvider(FakeProvider):
        def generate(self, request):
            with qa.database.transaction(qa.owner) as tx:
                tx.tasks.cancel(task.id, uuid4())
            return super().generate(request)
    provider = CancellingProvider() if during_send else FakeProvider()
    if not during_send:
        with qa.database.transaction(qa.owner) as tx:
            tx.tasks.cancel(task.id, uuid4())
    qa_worker(qa, provider).run_once()
    result = persisted(qa.pg, 'tasks', id=task.id)[0]
    assert result['state'] == ('succeeded' if during_send else 'cancelled')
    assert result['cancel_requested_at'] is not None
    assert provider.calls == int(during_send)


def switch(fee_control, enabled):
    with fee_control.pg.admin.begin() as connection:
        revision = connection.scalar(sa.text('SELECT revision FROM billing_control.switches'))
        connection.execute(sa.text('SELECT billing_api.update_switches(:actor,:op,:revision,CAST(:flags AS jsonb),:reason)'),
            dict(actor=fee_control.actor, op=uuid4(), revision=revision,
                 flags=json.dumps({'billing':enabled, 'ai_interaction':enabled}), reason='Synthetic PR-07a switch test'))


@pytest.fixture
def billed_qa(qa, fee_control):
    switch(fee_control, True)
    price = uuid4()
    with qa.pg.admin.begin() as connection:
        connection.execute(sa.text('''INSERT INTO price_catalog(id,provider,model,region,revision,currency,rates,
            minimum_microyuan,synthetic,effective_at) VALUES(:id,'fake','qa-deterministic-v1','synthetic',:revision,
            'CNY',CAST(:rates AS jsonb),0,true,'2020-01-01T00:00:00Z')'''),
            dict(id=price, revision=str(price), rates=json.dumps({
                'input_tokens':{'microyuan':1,'units':1}, 'output_tokens':{'microyuan':1,'units':1}})))
    qa.config = dict(qa.config, price_id=str(price), price_version=str(price),
                     maximum_cost_microyuan=100000, billing_required=True)
    qa.billing = fee_control.lifecycle
    qa.fee_control = fee_control
    return qa


def fee_state(qa, task):
    with qa.pg.admin.connect() as connection:
        return connection.execute(sa.text('''SELECT r.*,e.held,e.unknown,e.settled FROM quota_reservations r
            JOIN billing_control.exposures e ON e.reservation_id=r.id WHERE r.task_id=:id'''),
            {'id':task.id}).mappings().one()


def test_question_fee_reserve_send_and_idempotent_settlement(billed_qa):
    qa = billed_qa
    plan = prepare_question(qa)
    key = uuid4()
    task = execute_question(qa, plan, key)
    assert execute_question(qa, plan, key).id == task.id
    assert fee_state(qa, task)['state'] == 'reserved'
    class InspectProvider(FakeProvider):
        def generate(self, request):
            assert fee_state(qa, task)['state'] == 'submitted'
            with qa.pg.admin.connect() as connection:
                assert connection.scalar(sa.text('SELECT count(*) FROM billing_permits WHERE reservation_id=:id'),
                    {'id':fee_state(qa, task)['id']}) == 1
            self.usage = super().generate(request)
            return self.usage
    provider = InspectProvider()
    assert qa_worker(qa, provider, billing=qa.billing).run_once()
    qa.billing.completed(qa.owner.owner_id, task.id, provider.usage.usage)
    row = fee_state(qa, task)
    assert row['state'] == 'settled' and row['held'] == row['unknown'] == 0
    assert row['settled'] == sum(provider.usage.usage.quantities.values())
    with qa.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT count(*) FROM usage_receipts WHERE reservation_id=:id'), row) == 1
    assert question_row(qa, task)['answer_markdown'] is not None and provider.calls == 1


def test_question_missing_usage_keeps_unknown_until_late_final_receipt(billed_qa):
    qa = billed_qa
    task = question_task(qa)
    provider = FakeProvider(missing_usage=True)
    qa_worker(qa, provider, billing=qa.billing).run_once()
    row = fee_state(qa, task)
    assert row['state'] == 'unknown' and row['unknown'] > 0 and row['held'] == 0
    assert question_row(qa, task)['answer_markdown'] is not None
    usage = Usage(provider_request_id='synthetic-late-'+str(task.id), meter='tokens', revision=1,
                  quantities={'input_tokens':100, 'output_tokens':20}, final=True)
    qa.billing.late_usage(qa.owner.owner_id, task.id, usage)
    qa.billing.late_usage(qa.owner.owner_id, task.id, usage)
    assert fee_state(qa, task)['settled'] == 120
    assert fee_state(qa, task)['unknown'] == 0 and provider.calls == 1


def test_question_fee_callback_failure_recovers_saved_output_and_unknown_cost(billed_qa):
    qa = billed_qa
    task = question_task(qa)
    class BrokenCompletion:
        before_submit = qa.billing.before_submit
        synchronize = qa.billing.synchronize
        def completed(self, owner_id, task_id, usage):
            raise RuntimeError('synthetic settlement interruption')
    provider = FakeProvider()
    with pytest.raises(RuntimeError, match='synthetic settlement'):
        qa_worker(qa, provider, billing=BrokenCompletion()).run_once()
    assert provider.calls == 1
    assert question_row(qa, task)['answer_markdown'] is None
    expire(qa.pg, task.id)
    restarted = qa_worker(qa, billing=qa.billing, worker_id='fee-recovery')
    assert restarted.run_once() and restarted.provider.calls == 0
    assert question_row(qa, task)['answer_markdown'] is not None
    assert fee_state(qa, task)['state'] == 'unknown'
    assert fee_state(qa, task)['unknown'] > 0


def test_question_fee_quota_and_switch_denials_create_no_execution(billed_qa):
    qa = billed_qa
    first = question_task(qa)
    with qa.pg.admin.begin() as connection:
        connection.execute(sa.text('UPDATE quota_periods SET limit_value=1 WHERE owner_id=:owner'),
                           {'owner':qa.owner.owner_id})
    blocked_plan = prepare_question(qa)
    with pytest.raises(QuotaExceeded):
        execute_question(qa, blocked_plan)
    switch(qa.fee_control, False)
    provider = FakeProvider()
    qa_worker(qa, provider, billing=qa.billing).run_once()
    assert provider.calls == 0
    assert fee_state(qa, first)['state'] == 'reserved'
    with pytest.raises(BillingBlocked):
        execute_question(qa, blocked_plan)
    with qa.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT count(*) FROM tasks WHERE owner_id=:owner'),
                                 {'owner':qa.owner.owner_id}) == 1


@pytest.mark.parametrize('submitted', [False, True])
def test_question_source_revocation_synchronizes_fee_risk(billed_qa, submitted):
    qa = billed_qa
    task = question_task(qa)
    worker = qa_worker(qa, billing=qa.billing)
    if submitted:
        send_intent(qa, task, worker)
    else:
        qa.scheduler.claim(worker.worker_id, schema_version='pr07a-1')
    with qa.database.transaction(qa.owner) as tx:
        PgQaRepository(tx).revoke_source(qa.source.source_id)
    expire(qa.pg, task.id)
    worker.recover_expired()
    row = fee_state(qa, task)
    assert row['state'] == ('unknown' if submitted else 'released')
    assert row['held'] == 0
    assert (row['unknown'] > 0) is submitted
    assert worker.provider.calls == 0


@pytest.mark.parametrize('resource', ['source', 'classroom', 'account'])
def test_returned_usage_settles_after_content_access_is_revoked(billed_qa, resource):
    qa = billed_qa
    task = question_task(qa)
    class RevokingProvider(FakeProvider):
        def generate(self, request):
            reply = super().generate(request)
            if resource == 'source':
                with qa.database.transaction(qa.owner) as tx:
                    PgQaRepository(tx).revoke_source(qa.source.source_id)
            elif resource == 'classroom':
                with qa.database.transaction(qa.owner) as tx:
                    tx.tombstones.revoke(Resource.CLASS_SESSION, qa.tree.classroom.id,
                                         1, ledger_sequence=1)
            else:
                with qa.pg.admin.begin() as connection:
                    connection.execute(sa.text("UPDATE users SET state='disabled' WHERE id=:id"),
                                       {'id':qa.owner.owner_id})
            self.usage = reply.usage
            return reply
    provider = RevokingProvider()
    qa_worker(qa, provider, billing=qa.billing).run_once()
    row = fee_state(qa, task)
    assert row['state'] == 'settled'
    assert row['settled'] == sum(provider.usage.quantities.values())
    assert row['unknown'] == row['held'] == 0
    assert question_row(qa, task)['answer_markdown'] is None
    assert provider.calls == 1
    with qa.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT count(*) FROM usage_receipts WHERE reservation_id=:id'), row) == 1
        assert connection.scalar(sa.text('''SELECT count(*) FROM provider_results r JOIN task_attempts a
            ON a.id=r.attempt_id WHERE a.task_id=:id'''), {'id':task.id}) == 0


def test_returned_usage_survives_result_database_failure(billed_qa, monkeypatch):
    qa = billed_qa
    task = question_task(qa)
    def broken_store(*args, **kwargs):
        raise sa.exc.OperationalError('synthetic result storage interruption', {}, None)
    provider = FakeProvider()
    with monkeypatch.context() as patch:
        patch.setattr(PgTaskRepository, 'store_result', broken_store)
        with pytest.raises(sa.exc.OperationalError):
            qa_worker(qa, provider, billing=qa.billing).run_once()
    assert fee_state(qa, task)['state'] == 'settled'
    assert question_row(qa, task)['answer_markdown'] is None
    expire(qa.pg, task.id)
    restarted = qa_worker(qa, billing=qa.billing)
    restarted.run_once()
    assert restarted.provider.calls == 0 and provider.calls == 1
    assert persisted(qa.pg, 'tasks', id=task.id)[0]['state'] == 'unknown'
    assert fee_state(qa, task)['state'] == 'settled'


QA_RAW_COMMITTED_CRASH = '''
import os
from pathlib import Path
import sqlalchemy as sa
from classroom_web.application.billing_tasks import BillingTaskLifecycle
from classroom_web.application.qa_worker import QaWorker
from classroom_web.infrastructure.database import Database
from classroom_web.infrastructure.qa_provider import FakeProvider
from classroom_web.infrastructure.task_scheduler import PgTaskScheduler
class JournalProvider(FakeProvider):
    def generate(self, request):
        with Path(os.environ['PR07A_CALL_JOURNAL']).open('a', encoding='utf-8') as stream:
            stream.write(str(request.attempt_id)+'\\n')
            stream.flush()
            os.fsync(stream.fileno())
        return super().generate(request)
class CrashLifecycle(BillingTaskLifecycle):
    def completed(self, owner_id, task_id, usage):
        os._exit(73)
database=Database(os.environ['PR07A_RUNTIME_URL'])
scheduler=PgTaskScheduler(os.environ['PR07A_SCHEDULER_URL'])
engine=sa.create_engine(os.environ['PR07A_RECONCILER_URL'],hide_parameters=True)
QaWorker(database,scheduler,JournalProvider(),worker_id='qa-hard-exit',
         billing=CrashLifecycle(engine),enabled=True).run_once()
raise RuntimeError('expected hard exit after committed question output')
'''


def test_question_worker_hard_exit_after_raw_commit_recovers_without_resend(billed_qa, tmp_path):
    qa = billed_qa
    task = question_task(qa)
    journal = tmp_path / 'synthetic-provider-calls.log'
    environment = os.environ.copy()
    environment.update(PR07A_RUNTIME_URL=qa.pg.runtime_url.render_as_string(hide_password=False),
        PR07A_SCHEDULER_URL=qa.scheduler.engine.url.render_as_string(hide_password=False),
        PR07A_RECONCILER_URL=qa.fee_control.reconciler.url.render_as_string(hide_password=False),
        PR07A_CALL_JOURNAL=str(journal))
    process = subprocess.run([sys.executable, '-B', '-c', QA_RAW_COMMITTED_CRASH],
                             env=environment, capture_output=True, timeout=30)
    assert process.returncode == 73, process.stderr.decode('utf-8', errors='replace')
    assert len(journal.read_text(encoding='utf-8').splitlines()) == 1
    assert fee_state(qa, task)['state'] == 'submitted'
    attempt = persisted(qa.pg, 'task_attempts', task_id=task.id)[0]
    assert persisted(qa.pg, 'provider_results', id=attempt['raw_output_ref'])[0]['validation_status'] == 'pending'
    expire(qa.pg, task.id)
    worker = qa_worker(qa, billing=qa.billing, worker_id='qa-after-hard-exit')
    assert worker.run_once() and worker.provider.calls == 0
    assert question_row(qa, task)['answer_markdown'] is not None
    assert fee_state(qa, task)['state'] == 'unknown'
    assert fee_state(qa, task)['unknown'] > 0 and fee_state(qa, task)['held'] == 0
    assert len(persisted(qa.pg, 'task_attempts', task_id=task.id)) == 1
    assert len(journal.read_text(encoding='utf-8').splitlines()) == 1
