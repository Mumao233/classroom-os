"""Single-request DashScope fee seam against isolated PostgreSQL and fake I/O."""
import asyncio
from dataclasses import replace
from hashlib import sha256
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
TOKENS = {'input_tokens': 15, 'output_tokens': 4}
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


class _UsageTransport(FakeTransport):
    def __init__(self, identity, *, complete):
        super().__init__()
        self.identity = identity
        self.request_id = f'fixture-{uuid4()}'
        self.terminal_usage = TOKENS if complete else {'duration': 400}

    def event(self, kind, *, after_control=None, **payload):
        self.feed_json({'header': {'event': kind, 'task_id': self.identity.provider_task_id},
                        'payload': payload}, after_control=after_control)

    async def send_json(self, message, *, on_dispatched=None):
        await super().send_json(message, on_dispatched=on_dispatched)
        if message['header']['action'] == 'finish-task':
            self.event('task-finished', usage=self.terminal_usage)


async def _usage_session(row, permit_row, transport):
    identity = transport.identity
    permit = SyntheticRequestPermit(reservation_id=str(row['id']), permit_id=str(permit_row['id']),
                                    identity=identity, maximum_units=permit_row['quantities'])
    config = {**vars(identity), 'class_session_id': str(uuid4()), 'capture_epoch_id': str(uuid4())}
    samples = 8_000
    pcm = b'\0\0' * samples
    frame = {'pcm': pcm, 'sample_rate': 16000, 'channels': 1, 'encoding': 'signed16le',
             'start_sample': 0, 'end_sample': samples, 'sample_count': samples,
             'byte_length': len(pcm), 'sha256': sha256(pcm).hexdigest()}
    output = {'request_id': transport.request_id,
              'sentence': {'sentence_id': 1, 'begin_time': 0, 'end_time': 500,
                           'sentence_end': True, 'text': 'synthetic ASR'}}
    async with DashScopeAdapter(transport, permit=permit) as adapter:
        transport.event('task-started', after_control='run-task')
        await adapter.start(config)
        await adapter.send(frame)
        transport.event('result-generated', output=output, usage=TOKENS)
        transport.event('result-generated', output=output, usage=TOKENS)
        assert (await adapter.finish())['state'] == 'succeeded'
        observed = [event['data'] for event in adapter.events() if event['type'] == 'asr.usage']
        audio_duration_ms = sum(end - start for start, end in adapter.sent_ranges) // 16
        assert audio_duration_ms == 500
        measurements = []
        for evidence in observed:
            assert all(evidence[key] == value for key, value in vars(identity).items())
            assert evidence['provider_request_id'] == transport.request_id
            quantities = {'audio_duration_ms': audio_duration_ms,
                          'input_tokens': evidence['input_tokens'], 'output_tokens': evidence['output_tokens']}
            measurements.append(Usage(provider_request_id=evidence['provider_request_id'], meter='cumulative-asr',
                                      revision=evidence['revision'], final=evidence['final'], quantities=quantities))
        return measurements, adapter.billing_pending


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

    class CommitCheckingTransport(_UsageTransport):
        async def connect(self):
            assert _committed_state(ledger, row['id']) == ('submitted', attempt.id, attempt.id)
            return await super().connect()

    transport = CommitCheckingTransport(identity, complete=True)

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

    observed, billing_pending = asyncio.run(_usage_session(row, permit_row, transport))
    partial, final = observed
    assert not billing_pending
    assert (partial.revision, partial.final, partial.quantities) == (1, False, {'audio_duration_ms': 500, **TOKENS})
    assert final == replace(partial, revision=2, final=True)
    assert transport.connect_count == 1
    with pytest.raises(UnsafeRetry), ledger.transaction() as repository:
        repository.consume_permit(row['id'], attempt.id, BOUNDS, claim.lease_token, WORKER)
    with pytest.raises(BillingBlocked), ledger.transaction() as repository:
        repository.consume_permit(row['id'], uuid4(), {'audio_duration_ms': 1}, claim.lease_token, WORKER)
    assert transport.connect_count == 1
    with ledger.transaction() as repository:
        for usage in (partial, partial):
            assert repository.reconcile(row['id'], usage)['state'] == 'submitted'
        for usage in (final, final):
            settled = repository.reconcile(row['id'], usage)
            assert (settled['state'], settled['settled_microyuan']) == ('settled', 36)
    with ledger.pg.admin.connect() as connection:
        revisions = connection.execute(sa.text('''SELECT metric_revision, final FROM usage_receipts
            WHERE reservation_id=:id ORDER BY metric_revision'''), {'id': row['id']}).all()
    assert revisions == [(1, False), (2, True)]


def test_asr_missing_final_usage_retains_high_water_until_bound_late_receipt(ledger, asr_request):
    row, _, attempt, identity = asr_request
    with ledger.transaction() as repository:
        permit_row = _consume(repository, asr_request)
    transport = _UsageTransport(identity, complete=False)
    observed, billing_pending = asyncio.run(_usage_session(row, permit_row, transport))
    partial, = observed
    assert billing_pending
    assert (partial.revision, partial.final, partial.quantities) == (1, False, {'audio_duration_ms': 500, **TOKENS})
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
                   quantities={'audio_duration_ms': 500, 'input_tokens': 20, 'output_tokens': 5})
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
        assert quotas['asr_day_ms']['settled'] == quotas['asr_month_ms']['settled'] == 500
    with ledger.pg.admin.connect() as connection:
        history = connection.execute(sa.text('''SELECT attempt_id,provider_request_id,metric_revision,
            amount_microyuan,bill_month FROM usage_receipts WHERE reservation_id=:id ORDER BY metric_revision'''),
            {'id': row['id']}).all()
    assert history == [(attempt.id, transport.request_id, 1, 36, None),
                       (attempt.id, transport.request_id, 2, None, None),
                       (attempt.id, transport.request_id, 3, 48, '2099-01')]
