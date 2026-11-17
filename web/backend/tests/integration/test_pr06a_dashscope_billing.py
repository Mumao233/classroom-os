"""Single-request DashScope fee seam against isolated PostgreSQL and fake I/O."""
import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from test_billing import billing_pg, ledger

from classroom_web.asr_dashscope import DashScopeAdapter, FakeTransport
from classroom_web.asr_dashscope.billing import StreamIdentity, SyntheticRequestPermit
from classroom_web.domain.billing import BillingBlocked, Usage, UsageConflict
from classroom_web.domain.tasks import UnsafeRetry
from classroom_web.infrastructure.billing_repository import json_value


BOUNDS = {'audio_duration_ms': 750, 'input_tokens': 60, 'output_tokens': 40}
RATES = {'audio_duration_ms': {'microyuan': 0, 'units': 1},
         'input_tokens': {'microyuan': 2, 'units': 1},
         'output_tokens': {'microyuan': 3, 'units': 2}}
WORKER = 'pr06a-synthetic-worker'


def _price(ledger, rates):
    price_id = uuid4()
    with ledger.pg.admin.begin() as connection:
        connection.execute(sa.text('''INSERT INTO price_catalog(id,provider,model,region,revision,currency,
            rates,minimum_microyuan,synthetic,effective_at) VALUES(:id,'mock','synthetic-asr','synthetic',
            :revision,'CNY',cast(:rates AS jsonb),0,true,'2020-01-01T00:00:00Z')'''),
            {'id': price_id, 'revision': str(price_id), 'rates': json_value(rates)})
    return price_id


@pytest.fixture
def asr_request(ledger):
    row = ledger.reserve(ledger.request(purpose='asr_live', price_id=_price(ledger, RATES),
                                       maximum_units=BOUNDS, task_max_microyuan=180))
    claim, = ledger.pg.scheduler.claim(WORKER, 3600, 1)
    assert claim.id == row['task_id']
    with ledger.transaction() as repository:
        attempt = repository.tx.tasks.prepare_attempt(claim.id, claim.lease_token, WORKER)
    identity = StreamIdentity(str(claim.id), str(attempt.id), str(uuid4()), str(uuid4()))
    return row, claim, attempt, identity


def _consume(repository, asr_request):
    row, claim, attempt, _ = asr_request
    reservation = repository.authorize_send(row['id'], attempt.id, claim.lease_token, WORKER)
    return repository.consume_permit(reservation['id'], attempt.id, reservation['maximum_units'],
                                     claim.lease_token, WORKER)


def _committed_state(ledger, reservation_id):
    with ledger.pg.admin.connect() as connection:
        row = connection.execute(sa.text('''SELECT r.state, r.attempt_id, p.submission_key
            FROM quota_reservations r LEFT JOIN billing_permits p ON r.id=p.reservation_id
            WHERE r.id=:id'''), {'id': reservation_id}).one()
    return tuple(row)


def test_asr_token_only_tariff_cannot_satisfy_duration_quota(ledger):
    rates = {meter: rate for meter, rate in RATES.items() if meter != 'audio_duration_ms'}
    request = ledger.request(purpose='asr_live', price_id=_price(ledger, rates),
                             maximum_units=BOUNDS, task_max_microyuan=180)
    with pytest.raises(BillingBlocked, match='all configured meter bounds are required'):
        ledger.reserve(request)
    token_bounds = {meter: BOUNDS[meter] for meter in rates}
    with pytest.raises(ValueError, match='positive duration'):
        ledger.reserve(replace(request, maximum_units=token_bounds))
    assert ledger.budget()['held'] == 0


def test_fake_connection_requires_committed_intent_and_permit(ledger, asr_request):
    row, claim, attempt, identity = asr_request

    class CommitCheckingTransport(FakeTransport):
        async def connect(self):
            assert _committed_state(ledger, row['id']) == ('submitted', attempt.id, attempt.id)
            return await super().connect()

    transport = CommitCheckingTransport()

    with pytest.raises(RuntimeError, match='synthetic rollback'):
        with ledger.transaction() as repository:
            _consume(repository, asr_request)
            assert _committed_state(ledger, row['id']) == ('reserved', None, None)
            raise RuntimeError('synthetic rollback')
    assert _committed_state(ledger, row['id']) == ('reserved', None, None)
    assert transport.connect_count == 0
    with ledger.transaction() as repository:
        permit_row = _consume(repository, asr_request)
        assert _committed_state(ledger, row['id']) == ('reserved', None, None)
    assert (row['reserved_microyuan'], row['maximum_units']) == (180, BOUNDS)

    permit = SyntheticRequestPermit(reservation_id=str(row['id']), permit_id=str(permit_row['id']),
                                    identity=identity, maximum_units=permit_row['quantities'])
    transport.feed_json({'header': {'event': 'task-started', 'task_id': identity.provider_task_id},
                         'payload': {}})
    config = {**vars(identity), 'class_session_id': str(uuid4()), 'capture_epoch_id': str(uuid4())}

    async def start():
        adapter = DashScopeAdapter(transport, permit=permit)
        try:
            await adapter.start(config)
        finally:
            await adapter.aclose()

    asyncio.run(start())
    assert transport.connect_count == 1
    with pytest.raises(UnsafeRetry), ledger.transaction() as repository:
        repository.consume_permit(row['id'], attempt.id, BOUNDS, claim.lease_token, WORKER)
    with pytest.raises(BillingBlocked), ledger.transaction() as repository:
        repository.consume_permit(row['id'], uuid4(), {'audio_duration_ms': 1}, claim.lease_token, WORKER)
    assert transport.connect_count == 1


def test_asr_missing_final_usage_retains_high_water_until_bound_late_receipt(ledger, asr_request):
    row, _, attempt, identity = asr_request
    with ledger.transaction() as repository:
        _consume(repository, asr_request)
    partial = Usage(provider_request_id=identity.provider_task_id, meter='cumulative-asr', revision=1,
                    quantities={'audio_duration_ms': 500, 'input_tokens': 15, 'output_tokens': 4},
                    final=False)
    with ledger.transaction() as repository:
        for usage in (partial, partial):
            assert repository.reconcile(row['id'], usage)['settled_microyuan'] == 36
    budget = ledger.budget()
    assert (budget['held'], budget['unknown'], budget['settled']) == (144, 0, 36)

    incomplete = replace(partial, revision=2, final=True, quantities={'audio_duration_ms': 500})
    with ledger.transaction() as repository:
        assert repository.mark_unknown(row['id'])['state'] == 'unknown'
        evidence = repository.reconcile(row['id'], incomplete)
        assert (evidence['state'], evidence['settled_microyuan']) == ('unknown', 36)

    late = replace(partial, revision=3, final=True, bill_month='2099-01',
                   quantities={'audio_duration_ms': 630, 'input_tokens': 20, 'output_tokens': 5})
    for invalid in (replace(partial, quantities={**partial.quantities, 'input_tokens': 18}),
                    replace(late, quantities={**late.quantities, 'input_tokens': 14}),
                    replace(late, provider_request_id=str(uuid4()))):
        with pytest.raises(UsageConflict), ledger.transaction() as repository:
            repository.reconcile(row['id'], invalid)
        budget = ledger.budget()
        assert (budget['held'], budget['unknown'], budget['settled']) == (0, 144, 36)

    with ledger.transaction() as repository:
        settled = repository.reconcile(row['id'], late)
        assert (settled['state'], settled['settled_microyuan']) == ('settled', 48)
        assert settled['period_start'] == row['period_start']
        quotas = {bucket['kind']: bucket for bucket in repository.quota()['buckets']}
        assert quotas['asr_day_ms']['settled'] == quotas['asr_month_ms']['settled'] == 630
    with ledger.pg.admin.connect() as connection:
        history = connection.execute(sa.text('''SELECT attempt_id,provider_request_id,metric_revision,
            amount_microyuan,bill_month FROM usage_receipts WHERE reservation_id=:id ORDER BY metric_revision'''),
            {'id': row['id']}).all()
    assert history == [(attempt.id, identity.provider_task_id, 1, 36, None),
                       (attempt.id, identity.provider_task_id, 2, None, None),
                       (attempt.id, identity.provider_task_id, 3, 48, '2099-01')]
