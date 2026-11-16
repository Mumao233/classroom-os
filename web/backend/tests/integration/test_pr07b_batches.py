"""Real PostgreSQL multi-call review orchestration and per-call fee recovery."""
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
from threading import Barrier, Event
import time
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from conftest import migrated_database
from test_billing_integration import fee_control
from test_persistent_tasks import expire, persisted, scheduler, task_db
from test_pr07b_recovery import (
    REVIEW_HARD_EXIT, billed_review, execute_review, fee_state, prepare_review,
    review, review_task, review_worker, revisions,
)

from classroom_web.application.billing_tasks import BillingTaskLifecycle
from classroom_web.domain.data import NotFound
from classroom_web.domain.qa import ProviderFailure
from classroom_web.infrastructure.qa_repository import PgQaRepository
from classroom_web.infrastructure.review_generation import PgReviewGenerationRepository
from classroom_web.infrastructure.review_generation import PgReviewBatchRepository
from classroom_web.infrastructure.review_provider import FakeReviewProvider
from classroom_web.infrastructure.review_repository import PgReviewRepository
from classroom_web.infrastructure.task_repository import PgTaskRepository


@pytest.fixture(scope='module')
def pg():
    """Candidate billing tables stay within this module's owned database."""
    with migrated_database() as database:
        yield database


@pytest.fixture(autouse=True)
def isolate_batch_queue(pg, owners):
    yield
    with pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE tasks SET not_before=clock_timestamp()+interval '365 days' WHERE owner_id=ANY(:owners)"),
                           {'owners': [owners.a.owner_id, owners.b.owner_id]})


def add_large_sources(review):
    with review.database.transaction(review.owner) as tx:
        sources = PgQaRepository(tx)
        review.source = sources.revise_source(review.source.source_id, 'A' * 4000)
        for number, letter in enumerate(('B', 'C'), 1):
            sources.add_source(review.tree.classroom.id, kind='transcript_segment', object_id=uuid4(),
                text=letter * 4000, start_ms=5000 * number, end_ms=5000 * number + 2000)
    return review


@pytest.fixture
def batch_review(review):
    return add_large_sources(review)


@pytest.fixture
def billed_batch_review(billed_review):
    return add_large_sources(billed_review)


class RecordingProvider(FakeReviewProvider):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.replies = []

    def generate(self, request):
        self.requests.append(request)
        reply = super().generate(request)
        self.replies.append(reply)
        return reply


def task_family(review, root):
    with review.pg.admin.connect() as connection:
        return connection.execute(sa.text('''SELECT * FROM tasks WHERE owner_id=:owner
            AND (id=:root OR parent_task_id=:root) ORDER BY created_at,id'''),
            {'owner': review.owner.owner_id, 'root': root.id}).mappings().all()


def root_state(review, root):
    return persisted(review.pg, 'tasks', id=root.id)[0]


def saved_results(review, task_id):
    with review.pg.admin.connect() as connection:
        return connection.execute(sa.text('''SELECT r.* FROM provider_results r
            JOIN task_attempts a ON a.owner_id=r.owner_id AND a.id=r.attempt_id
            WHERE r.owner_id=:owner AND a.task_id=:task ORDER BY a.attempt_number'''),
            {'owner': review.owner.owner_id, 'task': task_id}).mappings().all()


def drain(review, task, worker, batches):
    for _ in range(batches + 3):
        state = root_state(review, task)
        if state['state'] in ('succeeded', 'failed', 'cancelled'):
            return state
        calls = worker.provider.calls
        worker.run_once()
        assert worker.provider.calls - calls <= 1
    pytest.fail('bounded review batch driver did not reach a terminal state')


def summary_revision(review, task):
    with review.database.transaction(review.owner) as tx:
        identity = PgReviewGenerationRepository(tx).result_id(task.id)
        assert identity is not None
        document = PgReviewRepository(tx).document(identity)
        version = PgReviewRepository(tx).revision(UUID(document['candidate_revision_ids'][-1]))
        return document, version


def fee_family(review, root):
    with review.pg.admin.connect() as connection:
        return connection.execute(sa.text('''SELECT r.*,e.held,e.unknown,e.settled FROM quota_reservations r
            JOIN billing_control.exposures e ON e.reservation_id=r.id
            JOIN tasks t ON t.id=r.task_id WHERE r.owner_id=:owner
            AND (t.id=:root OR t.parent_task_id=:root) ORDER BY t.created_at,t.id'''),
            {'owner': review.owner.owner_id, 'root': root.id}).mappings().all()


def test_multibatch_output_uses_one_snapshot_and_global_aliases_without_partial_publication(batch_review):
    review = batch_review
    plan = prepare_review(review)
    batches = plan['estimated_batches']
    assert batches > 1
    aliases = [item['alias'] for item in plan['source_snapshot']['items']]
    assert aliases == ['S1', 'S2', 'S3']
    assert all(len(item['excerpt']) == 4000 for item in plan['source_snapshot']['items'])
    task = execute_review(review, plan)
    provider = RecordingProvider()
    worker = review_worker(review, provider)
    assert worker.run_once() and provider.calls == 1
    waiting = root_state(review, task)
    assert waiting['state'] == 'running' and waiting['error_code'] == 'BATCHES_PENDING'
    assert waiting['leased_by'] is None and waiting['lease_expires_at'] is None
    assert revisions(review, task) == []
    family = task_family(review, task)
    assert len(family) == 2
    assert family[1]['schema_version'] == 'pr07b-batch-1' and family[1]['state'] == 'queued'
    completed = drain(review, task, worker, batches)
    assert completed['state'] == 'succeeded'
    assert provider.calls == batches
    received = [entry.alias for request in provider.requests for entry in request.selection.entries]
    assert received == aliases
    assert len({entry.source.revision_id for request in provider.requests for entry in request.selection.entries}) == 3
    assert all(request.selection.token_bound <= review.config['input_token_budget'] for request in provider.requests)
    family = task_family(review, task)
    assert len(family) == batches and all(row['state'] == 'succeeded' for row in family)
    for row in family:
        attempts = persisted(review.pg, 'task_attempts', task_id=row['id'])
        assert len(attempts) == 1 and attempts[0]['raw_output_ref'] is not None
        assert attempts[0]['provider_outcome'] == 'succeeded'
    document, version = summary_revision(review, task)
    assert document['adopted_revision_id'] is None and document['latest_personal_revision_id'] is None
    assert version['generation_task_id'] == str(task.id)
    assert version['source_snapshot_id'] == plan['source_snapshot']['id']
    payload = version['structured_content']
    cited = {alias for item in payload['notes'] + payload['cards'] for alias in item['source_ids']}
    assert cited == set(aliases)
    assert len(payload['notes']) == batches and len(payload['cards']) == batches
    with review.database.transaction(review.owner) as tx:
        assert PgQaRepository(tx).snapshot(UUID(version['source_snapshot_id'])) == plan['source_snapshot']
        for revision in revisions(review, task):
            assert PgReviewRepository(tx).revision(revision['id'])['generation_task_id'] == str(task.id)
    assert not worker.run_once() and provider.calls == batches


@pytest.mark.parametrize('boundary', ['parent', 'child'])
def test_parent_and_child_process_crash_recover_saved_raw_without_resending(billed_batch_review, tmp_path, boundary):
    review = billed_batch_review
    plan = prepare_review(review)
    task = execute_review(review, plan)
    if boundary == 'child':
        initial = review_worker(review, billing=review.billing)
        assert initial.run_once() and initial.provider.calls == 1
        crashing_task_id = task_family(review, task)[1]['id']
    else:
        crashing_task_id = task.id
    journal = tmp_path / ('review-batch-' + boundary + '-calls.log')
    environment = os.environ.copy()
    environment.update(PR07B_RUNTIME_URL=review.pg.runtime_url.render_as_string(hide_password=False),
        PR07B_SCHEDULER_URL=review.scheduler.engine.url.render_as_string(hide_password=False),
        PR07B_RECONCILER_URL=review.fee_control.reconciler.url.render_as_string(hide_password=False),
        PR07B_CALL_JOURNAL=str(journal), PR07B_CRASH_POINT='raw')
    process = subprocess.run([sys.executable, '-B', '-c', REVIEW_HARD_EXIT],
                             env=environment, capture_output=True, timeout=30)
    assert process.returncode == 73, process.stderr.decode('utf8', errors='replace')
    journal_lines = journal.read_text(encoding='utf8').splitlines()
    assert len(journal_lines) == 1
    attempt = persisted(review.pg, 'task_attempts', task_id=crashing_task_id)[0]
    assert str(attempt['id']) == journal_lines[0] and attempt['raw_output_ref'] is not None
    assert revisions(review, task) == []
    expire(review.pg, crashing_task_id)
    recovered = review_worker(review, billing=review.billing, worker_id='recovered-' + boundary)
    assert recovered.run_once()
    assert recovered.provider.calls == 0
    assert len(persisted(review.pg, 'task_attempts', task_id=crashing_task_id)) == 1
    final = drain(review, task, recovered, plan['estimated_batches'])
    assert final['state'] == 'succeeded'
    expected_remaining_calls = plan['estimated_batches'] - (2 if boundary == 'child' else 1)
    assert recovered.provider.calls == expected_remaining_calls
    assert journal.read_text(encoding='utf8').splitlines() == journal_lines
    crashed_fee = next(row for row in fee_family(review, task) if row['task_id'] == crashing_task_id)
    assert crashed_fee['state'] == 'unknown' and crashed_fee['unknown'] > 0
    assert crashed_fee['held'] == 0
    summary_revision(review, task)


def test_unknown_child_keeps_no_successor_and_never_resends(batch_review):
    review = batch_review
    plan = prepare_review(review)
    assert plan['estimated_batches'] >= 3
    task = execute_review(review, plan)

    class TimeoutChild(RecordingProvider):
        def generate(self, request):
            if request.task_id != task.id:
                self.calls += 1
                raise ProviderFailure('TIMEOUT', retryable=True, may_have_charged=True)
            return super().generate(request)

    provider = TimeoutChild()
    worker = review_worker(review, provider)
    assert worker.run_once() and provider.calls == 1
    assert worker.run_once() and provider.calls == 2
    for _ in range(3):
        worker.run_once()
    family = task_family(review, task)
    assert len(family) == 2 and family[1]['state'] == 'unknown'
    assert family[1]['may_have_charged'] is True
    assert provider.calls == 2
    assert len(persisted(review.pg, 'task_attempts', task_id=family[1]['id'])) == 1
    assert revisions(review, task) == []
    assert root_state(review, task)['state'] != 'succeeded'


def test_user_edit_between_batches_survives_final_candidate_publication(review):
    original = review_task(review)
    initial_worker = review_worker(review)
    assert initial_worker.run_once()
    original_document, original_version = summary_revision(review, original)
    with review.database.transaction(review.owner) as tx:
        document = PgReviewRepository(tx).adopt(UUID(original_document['id']),
            dict(base_revision=original_document['revision'], revision_id=original_version['id']), uuid4())
    add_large_sources(review)
    plan = prepare_review(review, target_document_id=document['id'], base_revision=document['revision'])
    task = execute_review(review, plan)
    worker = review_worker(review)
    assert worker.run_once() and worker.provider.calls == 1
    assert revisions(review, task) == []
    with review.database.transaction(review.owner) as tx:
        saved = PgReviewRepository(tx).write_personal(UUID(document['id']),
            dict(base_revision=document['revision'], base_content_revision_id=original_version['id'],
                 draft_id=str(uuid4()), body_markdown='My personal edit between review batches.', personal_note='Keep my interpretation.'), uuid4())
    assert drain(review, task, worker, plan['estimated_batches'])['state'] == 'succeeded'
    current, candidate = summary_revision(review, task)
    assert current['id'] == original_document['id']
    assert current['latest_personal_revision_id'] == saved['id']
    assert current['adopted_revision_id'] == original_version['id']
    assert len(current['candidate_revision_ids']) == 2
    assert candidate['source_snapshot_id'] != original_version['source_snapshot_id']
    with review.database.transaction(review.owner) as tx:
        repository = PgReviewRepository(tx)
        assert repository.revision(UUID(saved['id']))['body_markdown'] == 'My personal edit between review batches.'
        assert repository.revision(UUID(original_version['id'])) == original_version


def test_parent_cancellation_stops_later_batches_and_releases_unsent_reservation(billed_batch_review):
    review = billed_batch_review
    plan = prepare_review(review)
    task = execute_review(review, plan)
    worker = review_worker(review, billing=review.billing)
    assert worker.run_once() and worker.provider.calls == 1
    family = task_family(review, task)
    assert len(family) == 2 and family[1]['state'] == 'queued'
    before = fee_family(review, task)
    assert before[0]['state'] == 'settled' and before[1]['state'] == 'reserved'
    with review.database.transaction(review.owner) as tx:
        repository = PgReviewGenerationRepository(tx)
        repository.cancel(task.id, uuid4())
        synchronize = repository.synchronization_ids(task.id)
    for task_id in synchronize:
        review.billing.synchronize(review.owner.owner_id, task_id)
    worker.run_once()
    worker.run_once()
    family = task_family(review, task)
    assert len(family) == 2
    assert family[0]['state'] == family[1]['state'] == 'cancelled'
    assert worker.provider.calls == 1 and revisions(review, task) == []
    assert persisted(review.pg, 'task_attempts', task_id=family[1]['id']) == []
    after = fee_family(review, task)
    assert after[0]['state'] == 'settled' and after[1]['state'] == 'released'
    assert after[1]['held'] == after[1]['unknown'] == after[1]['settled'] == 0
    assert len(saved_results(review, task.id)) == 1


def test_each_call_settles_once_while_summary_quota_is_consumed_once(billed_batch_review):
    review = billed_batch_review
    plan = prepare_review(review)
    task = execute_review(review, plan)
    provider = RecordingProvider()
    worker = review_worker(review, provider, billing=review.billing)
    assert len(fee_family(review, task)) == 1
    assert drain(review, task, worker, plan['estimated_batches'])['state'] == 'succeeded'
    reservations = fee_family(review, task)
    assert len(reservations) == plan['estimated_batches'] == provider.calls
    root_fee = reservations[0]
    assert root_fee['quota_root_id'] is None
    assert all(row['quota_root_id'] == root_fee['id'] for row in reservations[1:])
    assert all(row['state'] == 'settled' and row['held'] == row['unknown'] == 0 for row in reservations)
    actual_usage = sum(sum(reply.usage.quantities.values()) for reply in provider.replies)
    assert sum(row['settled'] for row in reservations) == actual_usage
    assert sum(row['reserved_microyuan'] for row in reservations) <= int(plan['maximum_cost_microyuan'])
    with review.pg.admin.connect() as connection:
        allocation = connection.execute(sa.text('''SELECT q.resource,sum(a.quantity) FROM reservation_allocations a
            JOIN quota_periods q ON q.id=a.quota_period_id WHERE a.reservation_id=ANY(:ids)
            GROUP BY q.resource'''), {'ids': [row['id'] for row in reservations]}).all()
        assert allocation == [('summary_month_count', 1)]
        assert connection.scalar(sa.text('SELECT count(*) FROM usage_receipts WHERE reservation_id=ANY(:ids)'),
                                 {'ids': [row['id'] for row in reservations]}) == len(reservations)
    for request, reply in zip(provider.requests, provider.replies):
        review.billing.completed(review.owner.owner_id, request.task_id, reply.usage)
    assert [row['settled'] for row in fee_family(review, task)] == [row['settled'] for row in reservations]


def test_later_budget_denial_terminates_root_and_preserves_first_raw(billed_batch_review):
    review = billed_batch_review
    plan = prepare_review(review)
    task = execute_review(review, plan)
    reservation = fee_state(review, task)
    with review.pg.admin.connect() as connection:
        budget = connection.execute(sa.text('''SELECT budget_microyuan,safety_microyuan FROM billing_control.platform_budgets
            WHERE period_start=:start'''), {'start': reservation['period_start']}).mappings().one()

    class ExhaustBudgetAfterResult(BillingTaskLifecycle):
        def completed(self, owner_id, task_id, usage):
            super().completed(owner_id, task_id, usage)
            if task_id == task.id:
                with review.pg.admin.begin() as connection:
                    connection.execute(sa.text('''UPDATE billing_control.platform_budgets
                        SET budget_microyuan=1,safety_microyuan=0 WHERE period_start=:start'''),
                        {'start': reservation['period_start']})

    lifecycle = ExhaustBudgetAfterResult(review.fee_control.reconciler)
    provider = RecordingProvider()
    worker = review_worker(review, provider, billing=lifecycle)
    try:
        assert worker.run_once()
        worker.run_once()
        result = root_state(review, task)
        assert result['state'] == 'failed' and result['error_code'] == 'BUDGET_BLOCKED'
        assert result['completed_at'] is not None and result['lease_expires_at'] is None
        assert provider.calls == 1 and len(task_family(review, task)) == 1
        assert len(fee_family(review, task)) == 1 and fee_state(review, task)['state'] == 'settled'
        raw = saved_results(review, task.id)
        assert len(raw) == 1 and raw[0]['validation_status'] == 'valid'
        assert revisions(review, task) == []
    finally:
        with review.pg.admin.begin() as connection:
            connection.execute(sa.text('''UPDATE billing_control.platform_budgets SET budget_microyuan=:budget,
                safety_microyuan=:safety WHERE period_start=:start'''),
                {'budget': budget['budget_microyuan'], 'safety': budget['safety_microyuan'],
                 'start': reservation['period_start']})


def test_repeated_execute_and_concurrent_coordinators_create_one_task_per_batch(batch_review):
    review = batch_review
    plan = prepare_review(review)
    task = execute_review(review, plan)
    assert execute_review(review, plan, uuid4()).id == task.id
    assert execute_review(review, plan, uuid4()).id == task.id
    with review.pg.admin.connect() as connection:
        batches_before = connection.execute(sa.text('''SELECT id,batch_index,task_id FROM review_batches
            WHERE owner_id=:owner AND plan_id=:plan ORDER BY batch_index'''),
            {'owner': review.owner.owner_id, 'plan': UUID(plan['id'])}).mappings().all()
    assert len(batches_before) == plan['estimated_batches']
    assert [row['batch_index'] for row in batches_before] == list(range(plan['estimated_batches']))
    assert batches_before[0]['task_id'] == task.id
    assert all(row['task_id'] is None for row in batches_before[1:])
    initial = review_worker(review)
    assert initial.run_once() and initial.provider.calls == 1
    assert len(task_family(review, task)) == 2
    barrier = Barrier(2)
    providers = [RecordingProvider(), RecordingProvider()]
    workers = [review_worker(review, provider, worker_id='batch-coordinator-' + str(index))
               for index, provider in enumerate(providers)]

    def advance(index):
        barrier.wait(timeout=5)
        return workers[index].run_once()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(advance, range(2)))
    remaining = review_worker(review, RecordingProvider(), worker_id='batch-coordinator-final')
    assert drain(review, task, remaining, plan['estimated_batches'])['state'] == 'succeeded'
    assert initial.provider.calls + sum(provider.calls for provider in providers) + remaining.provider.calls == plan['estimated_batches']
    family = task_family(review, task)
    assert len(family) == plan['estimated_batches']
    for row in family:
        assert len(persisted(review.pg, 'task_attempts', task_id=row['id'])) == 1
        assert len(saved_results(review, row['id'])) == 1
    with review.pg.admin.connect() as connection:
        batches_after = connection.execute(sa.text('''SELECT id,batch_index,task_id FROM review_batches
            WHERE owner_id=:owner AND plan_id=:plan ORDER BY batch_index'''),
            {'owner': review.owner.owner_id, 'plan': UUID(plan['id'])}).mappings().all()
    assert [row['id'] for row in batches_after] == [row['id'] for row in batches_before]
    assert {row['task_id'] for row in batches_after} == {row['id'] for row in family}
    assert len(revisions(review, task)) == plan['estimated_batches'] + 1


@pytest.mark.parametrize('boundary', ['parent', 'child'])
def test_conflicting_saved_batch_quarantines_parent_and_prevents_next_call(batch_review, boundary):
    review = batch_review
    task = execute_review(review, prepare_review(review))
    worker = review_worker(review)
    assert worker.run_once()
    if boundary == 'child':
        assert worker.run_once()
    family = task_family(review, task)
    index = 0 if boundary == 'parent' else 1
    conflicted = family[index]['id']
    result = saved_results(review, conflicted)[0]
    calls = worker.provider.calls
    with review.database.transaction(review.owner) as tx:
        tx.tasks.store_result(conflicted, result['attempt_id'], result['raw_output'] + ' ')
    for _ in range(3):
        worker.run_once()
    assert worker.provider.calls == calls
    assert root_state(review, task)['state'] == 'unknown'
    assert revisions(review, task) == []
    assert len(task_family(review, task)) == len(family)
    assert saved_results(review, conflicted)[0]['conflict_sha256'] is not None
    with review.database.transaction(review.owner) as tx:
        repository = PgReviewGenerationRepository(tx)
        if boundary == 'child':
            repository = PgReviewBatchRepository(tx)
        assert repository.raw_output(conflicted)['raw_text'] == result['raw_output']


@pytest.mark.parametrize('boundary', ['parent', 'child'])
@pytest.mark.parametrize('recovery', ['worker', 'reconcile'])
def test_late_raw_for_unknown_batch_resumes_remaining_batches_without_resend(batch_review, boundary, recovery):
    review = batch_review
    plan = prepare_review(review)
    task = execute_review(review, plan)

    class DelayedReply(RecordingProvider):
        delayed_request = None
        delayed_reply = None

        def generate(self, request):
            reply = super().generate(request)
            if self.delayed_request is None and (boundary == 'parent' or request.task_id != task.id):
                self.delayed_request = request
                self.delayed_reply = reply
                raise ProviderFailure('TIMEOUT', retryable=True, may_have_charged=True)
            return reply

    provider = DelayedReply()
    worker = review_worker(review, provider)
    assert worker.run_once()
    if boundary == 'child':
        assert worker.run_once()
    delayed = provider.delayed_request
    calls_before = provider.calls
    family_before = task_family(review, task)
    assert delayed is not None and root_state(review, task)['state'] == 'unknown'
    assert persisted(review.pg, 'tasks', id=delayed.task_id)[0]['state'] == 'unknown'
    assert saved_results(review, delayed.task_id) == []
    assert revisions(review, task) == []
    for _ in range(2):
        worker.run_once()
    assert provider.calls == calls_before and len(task_family(review, task)) == len(family_before)

    reconcile_key = uuid4()
    if recovery == 'reconcile':
        with review.database.transaction(review.owner) as tx:
            assert PgReviewGenerationRepository(tx).reconcile(task.id, reconcile_key).state == 'unknown'
    with review.database.transaction(review.owner) as tx:
        tx.tasks.store_result(delayed.task_id, delayed.attempt_id, provider.delayed_reply.raw_output)
    if recovery == 'reconcile':
        with review.database.transaction(review.owner) as tx:
            PgReviewGenerationRepository(tx).reconcile(task.id, reconcile_key)
        assert provider.calls == calls_before
        assert len(task_family(review, task)) == len(family_before)
        assert revisions(review, task) == []

    assert drain(review, task, worker, plan['estimated_batches'])['state'] == 'succeeded'
    assert provider.calls == plan['estimated_batches']
    family = task_family(review, task)
    assert len(family) == plan['estimated_batches']
    assert len({request.task_id for request in provider.requests}) == provider.calls
    for row in family:
        assert row['state'] == 'succeeded'
        assert len(persisted(review.pg, 'task_attempts', task_id=row['id'])) == 1
        assert len(saved_results(review, row['id'])) == 1
    assert saved_results(review, delayed.task_id)[0]['raw_output'] == provider.delayed_reply.raw_output
    assert len(revisions(review, task)) == plan['estimated_batches'] + 1
    summary_revision(review, task)
    assert not worker.run_once() and provider.calls == plan['estimated_batches']


def test_terminal_parent_cancel_and_reconcile_replays_preserve_cancelled_batches(billed_batch_review):
    review = billed_batch_review
    task = execute_review(review, prepare_review(review))
    worker = review_worker(review, billing=review.billing)
    assert worker.run_once() and worker.provider.calls == 1
    first_cancel = uuid4()
    with review.database.transaction(review.owner) as tx:
        repository = PgReviewGenerationRepository(tx)
        cancelled = repository.cancel(task.id, first_cancel)
        identities = repository.synchronization_ids(task.id)
    for identity in identities:
        review.billing.synchronize(review.owner.owner_id, identity)
    assert cancelled.state == 'cancelled'
    terminal = root_state(review, task)
    family_before = task_family(review, task)
    assert len(family_before) == 2 and all(row['state'] == 'cancelled' for row in family_before)
    reconcile_key = uuid4()
    operations = [('cancel', first_cancel), ('cancel', uuid4()),
                  ('reconcile', reconcile_key), ('reconcile', reconcile_key), ('reconcile', uuid4())]
    for operation, key in operations:
        with review.database.transaction(review.owner) as tx:
            repository = PgReviewGenerationRepository(tx)
            result = getattr(repository, operation)(task.id, key)
            identities = repository.synchronization_ids(task.id)
        for identity in identities:
            review.billing.synchronize(review.owner.owner_id, identity)
        assert result.state == 'cancelled' and result.revision == terminal['revision']
        assert not worker.run_once() and worker.provider.calls == 1
        current = root_state(review, task)
        assert current['completed_at'] == terminal['completed_at']
        assert current['cancel_requested_at'] == terminal['cancel_requested_at']
        assert current['lease_token'] == terminal['lease_token']
        assert revisions(review, task) == []
    family_after = task_family(review, task)
    assert [row['id'] for row in family_after] == [row['id'] for row in family_before]
    assert all(row['state'] == 'cancelled' for row in family_after)
    assert len(saved_results(review, task.id)) == 1
    assert persisted(review.pg, 'task_attempts', task_id=family_after[1]['id']) == []
    reservations = fee_family(review, task)
    assert reservations[0]['state'] == 'settled' and reservations[1]['state'] == 'released'
    assert all(row['held'] == row['unknown'] == 0 for row in reservations)


def test_source_revocation_quarantines_unleased_parent_without_exposing_saved_batch(batch_review):
    review = batch_review
    task = execute_review(review, prepare_review(review))
    worker = review_worker(review)
    assert worker.run_once() and worker.provider.calls == 1
    waiting = root_state(review, task)
    assert waiting['state'] == 'running' and waiting['error_code'] == 'BATCHES_PENDING'
    assert waiting['lease_expires_at'] is None and waiting['leased_by'] is None
    family_before = task_family(review, task)
    raw_before = saved_results(review, task.id)
    assert len(family_before) == 2 and len(raw_before) == 1
    with review.database.transaction(review.owner) as tx:
        PgQaRepository(tx).revoke_source(review.source.source_id)
    for _ in range(3):
        worker.run_once()
    quarantined = root_state(review, task)
    assert quarantined['state'] == 'unknown' and quarantined['error_code'] == 'PROVIDER_RESULT_UNKNOWN'
    assert quarantined['lease_token'] > waiting['lease_token']
    assert quarantined['lease_expires_at'] is None and quarantined['leased_by'] is None
    assert worker.provider.calls == 1 and revisions(review, task) == []
    assert [row['id'] for row in task_family(review, task)] == [row['id'] for row in family_before]
    assert saved_results(review, task.id) == raw_before
    assert persisted(review.pg, 'task_attempts', task_id=family_before[1]['id']) == []
    assert task.id not in {claim.id for claim in review.scheduler.review_pending()}
    with review.database.transaction(review.owner) as tx:
        with pytest.raises(NotFound):
            tx.tasks.get(task.id)
        assert tx.connection.scalar(sa.text('SELECT count(*) FROM task_attempts WHERE task_id=:id'),
                                    {'id': task.id}) == 0
        assert tx.connection.scalar(sa.text('SELECT count(*) FROM provider_results WHERE id=:id'),
                                    {'id': raw_before[0]['id']}) == 0
    with review.scheduler.engine.connect() as connection, pytest.raises(sa.exc.DBAPIError):
        connection.execute(sa.text('SELECT raw_output FROM provider_results'))


def test_parent_reconcile_and_advance_share_owner_root_child_lock_order(batch_review, monkeypatch):
    review = batch_review
    plan = prepare_review(review)
    task = execute_review(review, plan)
    worker = review_worker(review)
    assert worker.run_once() and worker.provider.calls == 1
    child_id = task_family(review, task)[1]['id']
    child_locked, advancing_ready, release_reconcile = Event(), Event(), Event()
    backend_pids = {}
    original_reconcile = PgTaskRepository.reconcile_result

    def pause_after_child_lock(repository, task_id, op_id, *args, **kwargs):
        result = original_reconcile(repository, task_id, op_id, *args, **kwargs)
        if task_id == child_id:
            child_locked.set()
            assert release_reconcile.wait(5)
        return result

    def reconcile_parent():
        with review.database.transaction(review.owner) as tx:
            tx.connection.execute(sa.text("SET LOCAL statement_timeout='8s'"))
            backend_pids['reconcile'] = tx.connection.scalar(sa.text('SELECT pg_backend_pid()'))
            return PgReviewGenerationRepository(tx).reconcile(task.id, uuid4())

    def advance_parent():
        with review.database.transaction(review.owner) as tx:
            tx.connection.execute(sa.text("SET LOCAL statement_timeout='8s'"))
            backend_pids['advance'] = tx.connection.scalar(sa.text('SELECT pg_backend_pid()'))
            advancing_ready.set()
            return PgReviewGenerationRepository(tx).advance(task.id)

    with monkeypatch.context() as patch:
        patch.setattr(PgTaskRepository, 'reconcile_result', pause_after_child_lock)
        with ThreadPoolExecutor(max_workers=2) as pool:
            reconciling = pool.submit(reconcile_parent)
            try:
                assert child_locked.wait(5)
                advancing = pool.submit(advance_parent)
                assert advancing_ready.wait(5)
                deadline = time.monotonic() + 3
                blocked = []
                with review.pg.admin.connect() as connection:
                    while time.monotonic() < deadline and not blocked:
                        blocked = connection.scalar(sa.text('SELECT pg_blocking_pids(:pid)'),
                            {'pid': backend_pids['advance']})
                        if not blocked:
                            time.sleep(0.01)
                assert backend_pids['reconcile'] in blocked
            finally:
                release_reconcile.set()
            assert reconciling.result(timeout=10).state == 'running'
            assert advancing.result(timeout=10) is False
    assert worker.provider.calls == 1 and len(task_family(review, task)) == 2
    assert drain(review, task, worker, plan['estimated_batches'])['state'] == 'succeeded'
    assert worker.provider.calls == plan['estimated_batches']
    for row in task_family(review, task):
        assert len(persisted(review.pg, 'task_attempts', task_id=row['id'])) == 1
    assert len(revisions(review, task)) == plan['estimated_batches'] + 1


def test_billed_source_revocation_releases_unsent_child_after_metadata_recovery(billed_batch_review):
    review = billed_batch_review
    task = execute_review(review, prepare_review(review))
    worker = review_worker(review, billing=review.billing)
    assert worker.run_once() and worker.provider.calls == 1
    family = task_family(review, task)
    assert len(family) == 2 and family[1]['state'] == 'queued'
    child_id = family[1]['id']
    raw_before = saved_results(review, task.id)
    fees_before = fee_family(review, task)
    assert len(raw_before) == 1
    assert fees_before[0]['state'] == 'settled' and fees_before[1]['state'] == 'reserved'
    with review.database.transaction(review.owner) as tx:
        PgQaRepository(tx).revoke_source(review.source.source_id)
    worker.run_once()
    claimed = persisted(review.pg, 'tasks', id=child_id)[0]
    assert claimed['state'] == 'running' and claimed['lease_expires_at'] is not None
    assert claimed['leased_by'] == worker.worker_id and claimed['may_have_charged'] is False
    assert persisted(review.pg, 'task_attempts', task_id=child_id) == []
    with review.database.transaction(review.owner) as tx:
        with pytest.raises(NotFound):
            tx.tasks.get(child_id)
    expire(review.pg, child_id)
    worker.recover_expired()
    worker.run_once()
    worker.run_once()
    recovered = persisted(review.pg, 'tasks', id=child_id)[0]
    assert recovered['state'] == 'cancelled' and recovered['error_code'] == 'ACCESS_REVOKED'
    assert recovered['lease_expires_at'] is None and recovered['leased_by'] is None
    assert recovered['may_have_charged'] is False
    assert root_state(review, task)['state'] == 'unknown'
    assert worker.provider.calls == 1 and revisions(review, task) == []
    assert [row['id'] for row in task_family(review, task)] == [row['id'] for row in family]
    assert saved_results(review, task.id) == raw_before
    assert persisted(review.pg, 'task_attempts', task_id=child_id) == []
    fees_after = fee_family(review, task)
    assert fees_after[0]['state'] == 'settled'
    assert fees_after[0]['settled'] == fees_before[0]['settled'] > 0
    assert fees_after[1]['state'] == 'released' and fees_after[1]['settled'] == 0
    assert all(row['held'] == row['unknown'] == 0 for row in fees_after)


def test_pending_scheduler_rotates_more_than_one_page_of_uncertain_parents(batch_review):
    review = batch_review
    task = execute_review(review, prepare_review(review))
    assert review_worker(review).run_once()
    # Synthetic stalled metadata isolates the dispatcher's fairness rule. These
    # Each task has its own authorized plan and never enters a Provider call.
    stalled = [uuid4() for _ in range(32)]
    with review.pg.admin.begin() as connection:
        for identity in stalled:
            plan_id = uuid4()
            connection.execute(sa.text('''INSERT INTO generation_plans(id,owner_id,class_session_id,
                snapshot_id,kind,plan_hash,request,config,expires_at)
                SELECT :plan,p.owner_id,p.class_session_id,p.snapshot_id,p.kind,p.plan_hash,
                    p.request,p.config,p.expires_at FROM generation_plans p
                    JOIN tasks t ON t.owner_id=p.owner_id AND t.plan_id=p.id WHERE t.id=:root'''),
                {'plan': plan_id, 'root': task.id})
            connection.execute(sa.text('''INSERT INTO tasks(id,owner_id,class_session_id,kind,state,
                input_ref,schema_version,plan_id,error_code,not_before)
                SELECT :id,owner_id,class_session_id,kind,'unknown',input_ref,schema_version,:plan,
                'BATCH_RESULT_UNKNOWN',clock_timestamp()-interval '1 day' FROM tasks WHERE id=:root'''),
                {'id': identity, 'root': task.id, 'plan': plan_id})
    first = {claim.id for claim in review.scheduler.review_pending()}
    second = {claim.id for claim in review.scheduler.review_pending()}
    assert first == set(stalled)
    assert task.id not in first and task.id in second
    with review.scheduler.engine.connect() as connection:
        assert not connection.scalar(sa.text("SELECT has_table_privilege(current_user,'tasks','UPDATE')"))
