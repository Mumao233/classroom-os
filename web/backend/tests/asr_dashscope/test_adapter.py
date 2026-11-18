"""Single-connection lifecycle against the offline protocol transport."""
import asyncio
from functools import wraps
from hashlib import sha256
from uuid import uuid4

import pytest

from classroom_web.asr_dashscope import (
    DashScopeAdapter, Deadlines, FakeTransport, StreamIdentity, SyntheticRequestPermit,
)
from classroom_web.asr_dashscope.transport import AuthenticationRejected
from classroom_web.asr_dashscope.wire import TokenUsage
from classroom_web.asr_sim import ProtocolError


def offline(test):
    @wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


def config():
    return {field: str(uuid4()) for field in (
        'task_id', 'attempt_id', 'stream_id', 'provider_task_id',
        'class_session_id', 'capture_epoch_id',
    )}


def permit_for(cfg, *, duration=100, input_limit=60, output_limit=40):
    identity = StreamIdentity(**{key: cfg[key] for key in (
        'task_id', 'attempt_id', 'stream_id', 'provider_task_id',
    )})
    return SyntheticRequestPermit(
        reservation_id=str(uuid4()), permit_id=str(uuid4()), identity=identity,
        maximum_units={'audio_duration_ms': duration, 'input_tokens': input_limit,
                       'output_tokens': output_limit},
    )


def wire(cfg, kind, **payload):
    header = {'event': kind, 'task_id': cfg['provider_task_id']}
    if kind == 'task-failed':
        header.update(error_code='provider-code', error_message='Bearer fixture-credential')
    return {'header': header, 'payload': payload}


def result(cfg, *, number=1, begin=0, end=None, final=False, text='首段',
           request='provider-request', usage=None):
    output = {'sentence': {'sentence_id': number, 'begin_time': begin, 'end_time': end,
                           'sentence_end': final, 'text': text}}
    if request is not None:
        output['request_id'] = request
    payload = {'output': output}
    if usage is not None:
        payload['usage'] = usage
    return wire(cfg, 'result-generated', **payload)


def usage(first, second):
    return {'input_tokens': first, 'output_tokens': second, 'total_tokens': first + second}


def pcm(start=0, samples=160):
    data = bytes(range(160)) * (2 * samples // 160) + bytes(range(2 * samples % 160))
    return {'pcm': data, 'sample_rate': 16000, 'channels': 1, 'encoding': 'signed16le',
            'start_sample': start, 'end_sample': start + samples, 'sample_count': samples,
            'byte_length': len(data), 'sha256': sha256(data).hexdigest()}


class ScriptedPeer(FakeTransport):
    def __init__(self, after_finish=()):
        super().__init__()
        self.after_finish = after_finish
        self.run_sent = asyncio.Event()
        self.finish_sent = asyncio.Event()

    async def send_json(self, message, *, on_dispatched=None):
        await super().send_json(message, on_dispatched=on_dispatched)
        if message['header']['action'] == 'run-task':
            self.run_sent.set()
        if message['header']['action'] == 'finish-task':
            self.finish_sent.set()
            for event in self.after_finish:
                self.feed_json(event)


async def open_adapter(cfg, peer, **kwargs):
    adapter = DashScopeAdapter(peer, permit=permit_for(cfg), **kwargs)
    peer.feed_json(wire(cfg, 'task-started'), after_control='run-task')
    assert await adapter.start(cfg) == {'stream_id': cfg['stream_id'], 'state': 'streaming'}
    return adapter


async def wait_state(adapter, state):
    async with asyncio.timeout(2):
        while adapter.state != state:
            await asyncio.sleep(0)


def just(events, kind):
    return [event['data'] for event in events if event['type'] == kind]


def assert_clean(peer):
    assert peer.closed and not peer.connected
    assert not (asyncio.all_tasks() - {asyncio.current_task()})


@offline
async def test_full_duplex_multiple_frames_stable_identity_and_tail_final():
    cfg = config()
    last = result(cfg, number=2, begin=10, end=20, final=True, text='尾段')
    terminal = wire(cfg, 'task-finished', usage=usage(17, 9))
    peer = ScriptedPeer([last, last, terminal, terminal])
    adapter = await open_adapter(cfg, peer)
    partial = result(cfg, text='第一', usage=usage(3, 1))
    revised = result(cfg, begin=1, text='第一段')
    confirmed = result(cfg, begin=2, end=10, final=True, text='第一段。', usage=usage(10, 6))
    await adapter.send(pcm(1600))
    for event in (wire(cfg, 'task-started'), partial, partial, revised, partial, confirmed, partial, confirmed, revised):
        peer.feed_json(event)
    await adapter.send(pcm(1760))
    one, two = await asyncio.gather(adapter.finish(), adapter.finish())
    assert one == two == await adapter.finish() == {'stream_id': cfg['stream_id'], 'state': 'succeeded'}
    assert adapter.query(one) == {'status': 'unsupported'}
    assert peer.connect_count == peer.close_count == 1
    assert [kind for kind, _ in peer.sent] == ['json', 'binary', 'binary', 'json']
    assert [entry['header']['action'] for entry in peer.sent_json] == ['run-task', 'finish-task']
    assert {entry['header']['task_id'] for entry in peer.sent_json} == {cfg['provider_task_id']}
    assert peer.sent_binary == [pcm(1600)['pcm'], pcm(1760)['pcm']]
    events = adapter.events()
    partials = just(events, 'asr.partial')
    finals = just(events, 'asr.final_candidate')
    assert [row['revision'] for row in partials] == [1, 2]
    assert [row['revision'] for row in finals] == [3, 1]
    assert {row['segment_id'] for row in [*partials, finals[0]]} == {finals[0]['segment_id']}
    assert finals[0]['segment_id'] != finals[1]['segment_id']
    assert [row['sequence'] for row in finals] == [0, 1]
    assert [(row['start_sample'], row['end_sample']) for row in finals] == [(1632, 1760), (1760, 1920)]
    assert finals[0]['provider_request_id'] == 'provider-request'
    assert all(row[key] == cfg[key] for row in finals for key in cfg)
    assert all(not {'cursor', 'event_id', 'transcript_version_id'} & row.keys() for row in finals)
    assert [(row['input_tokens'], row['output_tokens'], row['revision'], row['final'])
            for row in just(events, 'asr.usage')] == [(3, 1, 1, False), (10, 6, 2, False), (17, 9, 3, True)]
    assert just(events, 'asr.terminal')[0]['provider_terminal'] == 'task-finished'
    assert adapter.usage == TokenUsage(17, 9) and not adapter.billing_pending
    assert adapter.events() == []
    assert adapter.sent_ranges == adapter.attempted_ranges == ((1600, 1760), (1760, 1920))
    assert_clean(peer)


@offline
async def test_real_mode_rejected_before_connect_even_with_permit(monkeypatch):
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'fixture-bearer')
    cfg = config()
    peer = ScriptedPeer()
    peer.feed_json(wire(cfg, 'task-started'), after_control='run-task')
    adapter = DashScopeAdapter(peer, permit=permit_for(cfg))
    async with adapter:
        with pytest.raises(ProtocolError, match='real ASR is disabled'):
            await adapter.start({**cfg, 'mode': 'real'})
    assert adapter.state == 'idle'
    assert peer.connect_count == 0 and not peer.sent


@pytest.mark.parametrize('issue', ['missing_permit', 'foreign_permit', 'network_transport',
                                   'bad_config', 'supplier_alias', 'api_key'])
@offline
async def test_start_validation_precedes_any_io(issue):
    cfg = config()
    peer = ScriptedPeer()
    bound = permit_for(cfg)
    if issue == 'missing_permit':
        bound = None
    elif issue == 'foreign_permit':
        bound = permit_for(config())
    elif issue == 'network_transport':
        peer.offline = False
    elif issue == 'bad_config':
        cfg['task_id'] = 'not-a-uuid'
    elif issue == 'supplier_alias':
        cfg['provider_task_id'] = cfg['class_session_id']
    else:
        cfg['api_key'] = 'fixture-bearer'
    adapter = DashScopeAdapter(peer, permit=bound)
    with pytest.raises(ProtocolError) as rejected:
        await adapter.start(cfg)
    assert 'fixture-bearer' not in str(rejected.value)
    assert adapter.state == 'idle'
    assert peer.connect_count == 0 and not peer.sent


@pytest.mark.parametrize('bounds', [
    {}, {'audio_duration_ms': 16, 'input_tokens': 1},
    {'audio_duration_ms': 0, 'input_tokens': 1, 'output_tokens': 1},
    {'audio_duration_ms': 1, 'input_tokens': True, 'output_tokens': 1},
    {'audio_duration_ms': 1, 'input_tokens': 1, 'output_tokens': -1},
])
def test_invalid_synthetic_reservation_bounds_are_rejected(bounds):
    identity = StreamIdentity(*(str(uuid4()) for _ in range(4)))
    with pytest.raises(ProtocolError):
        SyntheticRequestPermit(reservation_id=str(uuid4()), permit_id=str(uuid4()),
                               identity=identity, maximum_units=bounds)


@offline
async def test_permit_cannot_create_two_requests_and_unused_close_is_final():
    cfg = config()
    permit = permit_for(cfg)
    first, second = ScriptedPeer(), ScriptedPeer()
    first.feed_json(wire(cfg, 'task-started'), after_control='run-task')
    async with DashScopeAdapter(first, permit=permit) as first_adapter:
        await first_adapter.start(cfg)
        second_adapter = DashScopeAdapter(second, permit=permit)
        with pytest.raises(ProtocolError, match='already consumed'):
            await second_adapter.start(cfg)
        await second_adapter.aclose()
        await second_adapter.aclose()
        with pytest.raises(ProtocolError, match='already used'):
            await second_adapter.start(cfg)
    assert first.connect_count == 1 and second.connect_count == 0
    assert_clean(first)


@pytest.mark.parametrize('mutation', [
    {'sample_rate': 44100}, {'channels': 2}, {'encoding': 's16be'},
    {'byte_length': 2}, {'sample_count': 159}, {'end_sample': 161}, {'sha256': '0' * 64},
    {'pcm': b'a'}, {'pcm': bytearray(320)}, {'start_sample': True},
])
@offline
async def test_bad_pcm_metadata_never_reaches_transport(mutation):
    cfg = config()
    peer = ScriptedPeer()
    async with await open_adapter(cfg, peer) as adapter:
        with pytest.raises(ProtocolError):
            await adapter.send({**pcm(), **mutation})
        assert adapter.attempted_ranges == ()
        await adapter.send(pcm())
        assert len(peer.sent_binary) == 1
    assert_clean(peer)


@offline
async def test_samples_are_contiguous_and_the_permit_caps_aggregate_duration():
    cfg = config()
    peer = ScriptedPeer()
    peer.feed_json(wire(cfg, 'task-started'), after_control='run-task')
    async with DashScopeAdapter(peer, permit=permit_for(cfg, duration=10)) as adapter:
        await adapter.start(cfg)
        await adapter.send(pcm(500, 80))
        for start, count, message in [(500, 80, 'contiguous'), (581, 80, 'contiguous'),
                                      (580, 81, 'reserved duration')]:
            with pytest.raises(ProtocolError, match=message):
                await adapter.send(pcm(start, count))
        await adapter.send(pcm(580, 80))
        assert adapter.sent_ranges == ((500, 580), (580, 660))
        assert len(peer.sent_binary) == 2
    assert_clean(peer)


@pytest.mark.parametrize('failure,reason,state', [
    ('authentication', 'authentication_rejected', 'failed'),
    ('handshake', 'connect_failed', 'failed'),
    ('connect_timeout', 'connect_timeout', 'failed'),
    ('ack_timeout', 'initialize_timeout', 'unknown'),
    ('bad_json', 'provider_protocol_error', 'unknown'),
    ('foreign', 'provider_protocol_error', 'unknown'),
    ('prequeued_ack', 'provider_protocol_error', 'unknown'),
    ('early_result', 'provider_protocol_error', 'unknown'),
    ('early_failure', 'task_failed', 'failed'),
    ('run-write', 'initialize_failed', 'unknown'),
])
@offline
async def test_start_failures_are_bounded_and_errors_are_redacted(failure, reason, state, caplog):
    class StartPeer(ScriptedPeer):
        async def connect(self):
            await super().connect()
            if failure == 'authentication':
                raise AuthenticationRejected('Authorization: Bearer fixture-secret')
            if failure == 'handshake':
                raise RuntimeError('Authorization: Bearer fixture-secret')
            if failure == 'connect_timeout':
                await asyncio.Event().wait()

        async def send_json(self, message, *, on_dispatched=None):
            await super().send_json(message, on_dispatched=on_dispatched)
            if failure == 'run-write':
                raise RuntimeError('Bearer fixture-secret')

    cfg = config()
    peer = StartPeer()
    adapter = DashScopeAdapter(peer, permit=permit_for(cfg),
                               deadlines=Deadlines(connect=.04, initialize=.04))
    if failure == 'bad_json':
        peer.feed_text('{"sensitive":"Bearer fixture-secret"', after_control='run-task')
    elif failure == 'foreign':
        peer.feed_json(wire(config(), 'task-started'), after_control='run-task')
    elif failure == 'prequeued_ack':
        peer.feed_json(wire(cfg, 'task-started'))
    elif failure == 'early_result':
        peer.feed_json(result(cfg), after_control='run-task')
    elif failure == 'early_failure':
        peer.feed_json(wire(cfg, 'task-failed'), after_control='run-task')
    with pytest.raises(ProtocolError) as caught:
        await adapter.start(cfg)
    terminal, = just(adapter.events(), 'asr.terminal')
    assert (terminal['reason'], adapter.state) == (reason, state)
    assert str(caught.value) == reason
    assert 'fixture-secret' not in str(caught.value) + repr(terminal) + caplog.text
    assert len(peer.sent_json) == (0 if failure in ('authentication', 'handshake', 'connect_timeout') else 1)
    assert adapter.query({'stream_id': cfg['stream_id']}) == {'status': 'unsupported'}
    assert_clean(peer)


@pytest.mark.parametrize('fault,state,reason', [
    ('disconnect', 'unknown', 'receive_failed'),
    ('reader_error', 'unknown', 'receive_failed'),
    ('bad_json', 'unknown', 'provider_protocol_error'),
    ('task_failed', 'failed', 'task_failed'),
    ('early_finished', 'unknown', 'provider_protocol_error'),
    ('missing_field', 'unknown', 'provider_protocol_error'),
    ('receipt_mismatch', 'unknown', 'provider_protocol_error'),
    ('foreign', 'unknown', 'provider_protocol_error'),
    ('usage_regression', 'unknown', 'usage_conflict'),
    ('usage_overflow', 'unknown', 'usage_conflict'),
    ('usage_conflict', 'unknown', 'usage_conflict'),
    ('changed_final', 'unknown', 'provider_protocol_error'),
    ('sequence_gap', 'unknown', 'provider_protocol_error'),
    ('out_of_range', 'unknown', 'provider_protocol_error'),
])
@offline
async def test_stream_fault_preserves_final_attempt_and_trusted_usage(fault, state, reason, caplog):
    cfg = config()
    peer = ScriptedPeer()
    adapter = await open_adapter(cfg, peer)
    await adapter.send(pcm())
    peer.feed_json(result(cfg, number=1, end=5, final=True, usage=usage(10, 8)))
    if fault == 'disconnect':
        peer.feed_disconnect()
    elif fault == 'reader_error':
        peer.feed_failure(RuntimeError('Bearer receiver-secret'))
    elif fault == 'bad_json':
        peer.feed_text('{"raw":"Bearer receiver-secret"')
    elif fault == 'task_failed':
        peer.feed_json(wire(cfg, 'task-failed'))
    elif fault == 'early_finished':
        peer.feed_json(wire(cfg, 'task-finished', usage=usage(30, 25)))
    elif fault == 'missing_field':
        peer.feed_json(wire(cfg, 'result-generated', output={}))
    elif fault == 'receipt_mismatch':
        peer.feed_json(result(cfg, request='foreign-request', usage=usage(20, 10)))
    elif fault == 'foreign':
        peer.feed_json(wire(config(), 'task-failed'))
    elif fault == 'usage_regression':
        peer.feed_json(result(cfg, number=1, end=5, final=True, usage=usage(11, 7)))
    elif fault == 'usage_overflow':
        peer.feed_json(result(cfg, number=1, end=5, final=True, usage=usage(61, 10)))
    elif fault == 'usage_conflict':
        event = result(cfg, number=1, end=5, final=True, usage=usage(10, 8))
        event['payload']['output']['usage'] = usage(10, 9)
        peer.feed_json(event)
    elif fault == 'changed_final':
        peer.feed_json(result(cfg, number=1, end=5, final=True, text='conflicting-body'))
    elif fault == 'sequence_gap':
        peer.feed_json(result(cfg, number=3, end=3, final=True))
    else:
        peer.feed_json(result(cfg, number=2, begin=5, end=11, final=True))
    await wait_state(adapter, state)
    assert await adapter.finish() == {'stream_id': cfg['stream_id'], 'state': state}
    events = adapter.events()
    final, = just(events, 'asr.final_candidate')
    assert final['text'] == '首段'
    terminal, = just(events, 'asr.terminal')
    assert terminal['reason'] == reason
    assert terminal['billing_pending']
    assert adapter.usage == TokenUsage(10, 8)
    if fault == 'usage_overflow':
        anomaly, = just(events, 'asr.usage_anomaly')
        assert anomaly['reason'] == 'overrun' and not anomaly['final']
        assert (anomaly['observed_input_tokens'], anomaly['observed_output_tokens']) == (61, 10)
        assert (anomaly['input_tokens_limit'], anomaly['output_tokens_limit']) == (60, 40)
        assert all(anomaly[key] == cfg[key] for key in cfg)
    if fault == 'usage_regression':
        anomaly, = just(events, 'asr.usage_anomaly')
        assert (anomaly['reason'], anomaly['observed_output_tokens']) == ('regression', 7)
    assert adapter.attempted_ranges == adapter.sent_ranges == ((0, 160),)
    assert len(peer.sent_binary) == 1 and len(peer.sent_json) == 1
    assert 'receiver-secret' not in repr(events) + caplog.text
    with pytest.raises(ProtocolError):
        await adapter.send(pcm(160))
    with pytest.raises(ProtocolError):
        adapter.query({'stream_id': config()['stream_id']})
    assert_clean(peer)


@pytest.mark.parametrize('terminal', ['missing_usage', 'short_partial', 'no_final', 'no_terminal',
                                       'write_failure', 'closed_by_peer'])
@offline
async def test_finish_keeps_recognition_and_billing_evidence_independent(terminal):
    cfg = config()
    events = {
        'missing_usage': [wire(cfg, 'task-finished', usage={'duration': 10})],
        'short_partial': [result(cfg, number=2, begin=6), wire(cfg, 'task-finished', usage=usage(5, 1))],
        'no_final': [wire(cfg, 'task-finished', usage=usage(5, 1))],
    }

    class FinishPeer(ScriptedPeer):
        async def send_json(self, message, *, on_dispatched=None):
            await super().send_json(message, on_dispatched=on_dispatched)
            if message['header']['action'] == 'finish-task':
                if terminal == 'write_failure':
                    raise RuntimeError('finish write failed')
                if terminal == 'closed_by_peer':
                    self.feed_disconnect()

    peer = FinishPeer(events.get(terminal, ()))
    adapter = await open_adapter(cfg, peer, deadlines=Deadlines(finish=.05))
    await adapter.send(pcm())
    if terminal != 'no_final':
        peer.feed_json(result(cfg, end=5, final=True, usage=usage(5, 1)))
    reply = await adapter.finish()
    assert await adapter.finish() == reply
    assert reply['state'] == ('succeeded' if terminal == 'missing_usage' else 'unknown')
    assert adapter.billing_pending and adapter.usage == TokenUsage(5, 1)
    assert len(peer.sent_json) == 2
    seen = adapter.events()
    assert len(just(seen, 'asr.final_candidate')) == int(terminal != 'no_final')
    assert just(seen, 'asr.terminal')[0]['provider_terminal'] == (
        'task-finished' if terminal in events else None)
    assert_clean(peer)


@pytest.mark.parametrize('failure', ['binary_error', 'binary_disconnect', 'write_timeout',
                                   'cancel_send', 'external_close'])
@offline
async def test_maybe_sent_range_never_retried_and_writer_released(failure):
    class WritePeer(ScriptedPeer):
        wrote = asyncio.Event()

        async def send_binary(self, frame):
            await super().send_binary(frame)
            self.wrote.set()
            if failure == 'binary_error':
                raise RuntimeError('Bearer writer-secret')
            if failure == 'binary_disconnect':
                self.feed_disconnect()
            await asyncio.Event().wait()

    cfg = config()
    peer = WritePeer()
    adapter = await open_adapter(cfg, peer, deadlines=Deadlines(write=.08))
    writer = asyncio.create_task(adapter.send(pcm(80)))
    await peer.wrote.wait()
    if failure == 'cancel_send':
        writer.cancel()
    elif failure == 'external_close':
        await adapter.aclose()
    expected = asyncio.CancelledError if failure == 'cancel_send' else ProtocolError
    with pytest.raises(expected):
        await writer
    with pytest.raises(ProtocolError):
        await adapter.send(pcm(80))
    with pytest.raises(ProtocolError):
        await adapter.start(cfg)
    assert await adapter.finish() == {'stream_id': cfg['stream_id'], 'state': 'unknown'}
    assert adapter.attempted_ranges == ((80, 240),)
    assert adapter.sent_ranges == ()
    assert len(peer.sent_binary) == 1
    assert_clean(peer)


@pytest.mark.parametrize('close_kind', ['timeout', 'failure', 'cancelled'])
@offline
async def test_close_is_bounded_uses_abort_and_can_be_repeated(close_kind):
    class ClosePeer(ScriptedPeer):
        async def close(self):
            if close_kind == 'cancelled':
                raise asyncio.CancelledError
            if close_kind == 'failure':
                raise RuntimeError('Bearer close-secret')
            await asyncio.Event().wait()

    cfg = config()
    peer = ClosePeer()
    adapter = await open_adapter(cfg, peer, deadlines=Deadlines(close=.03))
    await asyncio.gather(adapter.aclose(), adapter.aclose())
    assert adapter.close_error == ('close_timeout' if close_kind == 'timeout' else 'close_failed')
    assert peer.abort_count == 1
    assert adapter.state == 'unknown'
    assert_clean(peer)


@pytest.mark.parametrize('trigger', ['close', 'cancel'])
@offline
async def test_start_cancellation_while_awaiting_ack_leaves_unknown_and_closes(trigger):
    class AckPeer(ScriptedPeer):
        reading = asyncio.Event()

        async def receive_text(self):
            self.reading.set()
            await asyncio.Event().wait()

    cfg = config()
    peer = AckPeer()
    adapter = DashScopeAdapter(peer, permit=permit_for(cfg))
    pending = asyncio.create_task(adapter.start(cfg))
    await peer.reading.wait()
    if trigger == 'close':
        await asyncio.wait_for(adapter.aclose(), 1)
    else:
        pending.cancel()
    with pytest.raises(ProtocolError if trigger == 'close' else asyncio.CancelledError):
        await asyncio.wait_for(pending, 1)
    assert adapter.state == 'unknown' and adapter.billing_pending
    assert len(peer.sent_json) == 1
    assert_clean(peer)


@offline
async def test_finish_cancellation_aborts_inflight_send_without_resubmission():
    class StuckSendPeer(ScriptedPeer):
        wrote = asyncio.Event()

        async def send_binary(self, frame):
            await super().send_binary(frame)
            self.wrote.set()
            await asyncio.Event().wait()

    cfg = config()
    peer = StuckSendPeer()
    adapter = await open_adapter(cfg, peer)
    writing = asyncio.create_task(adapter.send(pcm()))
    await peer.wrote.wait()
    finishing = asyncio.create_task(adapter.finish())
    await wait_state(adapter, 'draining')
    with pytest.raises(ProtocolError):
        await adapter.send(pcm(160))
    finishing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(finishing, 1)
    with pytest.raises(ProtocolError):
        await writing
    assert adapter.state == 'unknown'
    assert not peer.finish_sent.is_set() and peer.connect_count == 1
    assert_clean(peer)


@offline
async def test_finish_cancellation_after_control_does_not_send_again():
    cfg = config()
    peer = ScriptedPeer()
    adapter = await open_adapter(cfg, peer)
    finishing = asyncio.create_task(adapter.finish())
    await peer.finish_sent.wait()
    finishing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await finishing
    assert (await adapter.finish())['state'] == 'unknown'
    assert len(peer.sent_json) == 2
    assert_clean(peer)


@pytest.mark.parametrize('deadline', ['idle_read', 'absolute_session'])
@offline
async def test_reader_and_session_deadlines_require_no_later_owner_call(deadline):
    cfg = config()
    peer = ScriptedPeer()
    deadlines = Deadlines(read=.05) if deadline == 'idle_read' else Deadlines(session=.05)
    adapter = await open_adapter(cfg, peer, deadlines=deadlines)
    await wait_state(adapter, 'unknown')
    assert (await adapter.finish())['state'] == 'unknown'
    assert just(adapter.events(), 'asr.terminal')[0]['reason'] == 'receive_timeout'
    assert_clean(peer)


@pytest.mark.parametrize('late_kind', ['task-failed', 'result-generated'])
@offline
async def test_ready_reader_cannot_change_a_closed_stream_or_append_after_terminal(late_kind):
    class ObservedReadPeer(ScriptedPeer):
        reading = asyncio.Event()

        async def receive_text(self):
            if self.run_sent.is_set() and not self._inbound.qsize():
                self.reading.set()
            return await super().receive_text()

    cfg = config()
    peer = ObservedReadPeer()
    adapter = await open_adapter(cfg, peer)
    await adapter.send(pcm())
    await peer.reading.wait()
    peer.feed_json(wire(cfg, late_kind) if late_kind == 'task-failed'
                   else result(cfg, end=5, final=True, usage=usage(5, 1)))
    await adapter.aclose()
    observed = adapter.events()
    assert not just(observed, 'asr.final_candidate'), 'post-terminal transcript escaped'
    assert [event['type'] for event in observed] == ['asr.started', 'asr.terminal']
    assert adapter.state == 'unknown' and adapter.usage is None
    assert peer.close_count == 1
    assert_clean(peer)


@offline
async def test_transport_ownership_cannot_be_stolen_by_another_adapter():
    first_cfg, second_cfg = config(), config()
    peer = ScriptedPeer()
    async with await open_adapter(first_cfg, peer) as first:
        second = DashScopeAdapter(peer, permit=permit_for(second_cfg))
        with pytest.raises(ProtocolError, match='transport ownership unavailable'):
            await second.start(second_cfg)
        await second.aclose()
        assert first.state == 'streaming' and peer.connected
        assert peer.connect_count == 1 and peer.close_count == 0
        await first.send(pcm())
    assert peer.close_count == 1
    assert_clean(peer)


@offline
async def test_expired_deadline_never_enters_the_transport_for_a_new_audio_frame():
    cfg = config()
    peer = ScriptedPeer()
    async with await open_adapter(cfg, peer) as adapter:
        adapter._session_deadline = asyncio.get_running_loop().time() - 1
        with pytest.raises(ProtocolError, match='send_timeout'):
            await adapter.send(pcm())
        assert not peer.sent_binary and adapter.attempted_ranges == ()
        assert adapter.state == 'unknown'
    assert_clean(peer)


@offline
async def test_finish_ack_is_invalid_before_finish_transport_write_starts():
    class HeldFinishPeer(ScriptedPeer):
        async def send_json(self, message, *, on_dispatched=None):
            if message['header']['action'] == 'finish-task':
                await asyncio.Event().wait()
            await super().send_json(message, on_dispatched=on_dispatched)

    cfg = config()
    peer = HeldFinishPeer()
    adapter = await open_adapter(cfg, peer)
    await adapter.send(pcm())
    peer.feed_json(result(cfg, end=5, final=True))
    await asyncio.sleep(0)
    finishing = asyncio.create_task(adapter.finish())
    asyncio.get_running_loop().call_soon(peer.feed_json, wire(cfg, 'task-finished'))
    assert (await asyncio.wait_for(finishing, 1))['state'] == 'unknown'
    terminal, = just(adapter.events(), 'asr.terminal')
    assert terminal['reason'] == 'provider_protocol_error'
    assert len(peer.sent_json) == 1
    assert_clean(peer)


@offline
async def test_reader_owned_cancelled_error_stops_the_stream():
    class CancelledReadPeer(ScriptedPeer):
        async def receive_text(self):
            if not self._inbound.qsize():
                raise asyncio.CancelledError
            return await super().receive_text()

    cfg = config()
    peer = CancelledReadPeer()
    adapter = await open_adapter(cfg, peer)
    await wait_state(adapter, 'unknown')
    assert (await adapter.finish())['state'] == 'unknown'
    assert just(adapter.events(), 'asr.terminal')[0]['reason'] == 'receive_failed'
    assert_clean(peer)


@offline
async def test_first_sentence_must_start_at_one():
    cfg = config()
    peer = ScriptedPeer()
    adapter = await open_adapter(cfg, peer)
    await adapter.send(pcm())
    peer.feed_json(result(cfg, number=2, end=8, final=True))
    await wait_state(adapter, 'unknown')
    assert (await adapter.finish())['state'] == 'unknown'
    assert not just(adapter.events(), 'asr.final_candidate')
    assert_clean(peer)

@pytest.mark.parametrize('dispatch', ['succeeds', 'fails', 'timeout'])
@offline
async def test_terminal_waits_for_confirmed_finish_dispatch(dispatch):
    class HeldDispatchPeer(ScriptedPeer):
        writing = asyncio.Event()
        release_write = asyncio.Event()
        read_terminal = asyncio.Event()

        async def send_json(self, message, *, on_dispatched=None):
            await super().send_json(message, on_dispatched=on_dispatched)
            if message['header']['action'] == 'finish-task':
                self.writing.set()
                await self.release_write.wait()
                if dispatch == 'fails':
                    raise RuntimeError('finish write did not complete')

        async def receive_text(self):
            raw = await super().receive_text()
            if '"task-finished"' in raw.text:
                self.read_terminal.set()
            return raw

    cfg = config()
    peer = HeldDispatchPeer()
    adapter = await open_adapter(cfg, peer, deadlines=Deadlines(finish=.15 if dispatch == 'timeout' else 2))
    await adapter.send(pcm())
    peer.feed_json(result(cfg, end=5, final=True, usage=usage(5, 1)))
    finish = asyncio.create_task(adapter.finish())
    await peer.writing.wait()
    peer.feed_json(wire(cfg, 'task-finished', usage=usage(7, 1)))
    await peer.read_terminal.wait()
    assert adapter.state == 'draining' and len(peer.sent_json) == 2
    assert adapter.usage == TokenUsage(5, 1) and adapter.billing_pending
    if dispatch != 'timeout':
        peer.release_write.set()
    reply = await finish
    events = adapter.events()
    terminal, = just(events, 'asr.terminal')
    assert terminal['provider_terminal'] == 'task-finished'
    if dispatch == 'succeeds':
        assert reply['state'] == 'succeeded' and not adapter.billing_pending
        assert peer.sent_json[-1]['header']['action'] == 'finish-task'
        assert adapter.usage == TokenUsage(7, 1)
        assert not just(events, 'asr.usage_anomaly')
    else:
        assert reply['state'] == 'unknown' and adapter.billing_pending
        assert adapter.usage == TokenUsage(5, 1) and len(peer.sent_json) == 2
        anomaly, = just(events, 'asr.usage_anomaly')
        assert (anomaly['reason'], anomaly['observed_input_tokens']) == ('unconfirmed_finish_dispatch', 7)
    assert_clean(peer)


@pytest.mark.parametrize('terminal_usage,reason,observed', [
    ({'input_tokens': 7}, 'invalid_terminal_usage', None),
    (usage(4, 1), 'regression', 4),
    (usage(61, 1), 'overrun', 61),
])
@offline
async def test_recognition_terminal_survives_malformed_or_conflicting_usage(terminal_usage, reason, observed):
    cfg = config()
    peer = ScriptedPeer([wire(cfg, 'task-finished', usage=terminal_usage)])
    adapter = await open_adapter(cfg, peer)
    await adapter.send(pcm())
    peer.feed_json(result(cfg, end=5, final=True, usage=usage(5, 1)))
    assert (await adapter.finish())['state'] == 'succeeded'
    assert adapter.billing_pending and adapter.usage == TokenUsage(5, 1)
    events = adapter.events()
    assert len(just(events, 'asr.final_candidate')) == 1
    terminal, = just(events, 'asr.terminal')
    assert terminal['provider_terminal'] == 'task-finished' and terminal['billing_pending']
    accepted, = just(events, 'asr.usage')
    assert (accepted['revision'], accepted['final']) == (1, False)
    anomaly, = just(events, 'asr.usage_anomaly')
    assert (anomaly['reason'], anomaly['observed_input_tokens'], anomaly['final']) == (reason, observed, True)
    assert_clean(peer)


@offline
async def test_close_after_run_write_cannot_start_an_acknowledgement_read():
    class CloseDuringRunPeer(ScriptedPeer):
        reading = 0

        async def send_json(self, message, *, on_dispatched=None):
            await super().send_json(message, on_dispatched=on_dispatched)
            self.owner_close = asyncio.create_task(adapter.aclose())

        async def receive_text(self):
            self.reading += 1
            await asyncio.Event().wait()

    cfg = config()
    peer = CloseDuringRunPeer()
    adapter = DashScopeAdapter(peer, permit=permit_for(cfg))
    with pytest.raises(ProtocolError):
        await asyncio.wait_for(adapter.start(cfg), 1)
    await peer.owner_close
    assert adapter.state == 'unknown' and peer.reading == 0
    assert len(peer.sent_json) == 1
    assert_clean(peer)


@offline
async def test_finish_deadline_is_checked_before_a_ready_terminal_can_succeed():
    cfg = config()
    peer = ScriptedPeer()
    adapter = await open_adapter(cfg, peer)
    await adapter.send(pcm())
    peer.feed_json(result(cfg, end=5, final=True, usage=usage(5, 1)))
    finish = asyncio.create_task(adapter.finish())
    await peer.finish_sent.wait()
    adapter._finish_deadline = asyncio.get_running_loop().time() - 1
    peer.feed_json(wire(cfg, 'task-finished', usage=usage(7, 1)))
    assert (await finish)['state'] == 'unknown'
    assert adapter.billing_pending and adapter.usage == TokenUsage(5, 1)
    terminal, = just(adapter.events(), 'asr.terminal')
    assert terminal['reason'] == 'finish_timeout'
    assert_clean(peer)


@pytest.mark.parametrize('wake_writer_first', [False, True])
@offline
async def test_finish_ack_before_handoff_cannot_be_confirmed_by_a_later_write(wake_writer_first):
    class PreHandoffPeer(ScriptedPeer):
        entered = asyncio.Event()
        may_dispatch = asyncio.Event()

        async def send_json(self, message, *, on_dispatched=None):
            if message['header']['action'] == 'finish-task':
                self.entered.set()
                await self.may_dispatch.wait()
            await super().send_json(message, on_dispatched=on_dispatched)

    cfg = config()
    peer = PreHandoffPeer()
    adapter = await open_adapter(cfg, peer)
    await adapter.send(pcm())
    peer.feed_json(result(cfg, end=5, final=True, usage=usage(5, 1)))
    finish = asyncio.create_task(adapter.finish())
    await peer.entered.wait()
    if wake_writer_first:
        # The frame enters the inbound queue here, before the now runnable
        # writer can hand off finish-task, although the writer will run first.
        peer.may_dispatch.set()
    peer.feed_json(wire(cfg, 'task-finished', usage=usage(7, 1)))
    if not wake_writer_first:
        await wait_state(adapter, 'unknown')
        peer.may_dispatch.set()
    assert (await finish)['state'] == 'unknown', 'pre-handoff reply settled stream'
    assert len(peer.sent_json) == (2 if wake_writer_first else 1)
    events = adapter.events()
    terminal, = just(events, 'asr.terminal')
    assert terminal['reason'] == 'finish_ack_before_dispatch' and terminal['billing_pending']
    anomaly, = just(events, 'asr.usage_anomaly')
    assert (anomaly['reason'], anomaly['observed_input_tokens']) == ('unconfirmed_finish_dispatch', 7)
    assert adapter.usage == TokenUsage(5, 1)
    assert_clean(peer)


@pytest.mark.parametrize('write_completes', [True, False])
@offline
async def test_results_from_inflight_audio_are_tentative_until_confirmed(write_completes):
    class HeldAudioPeer(ScriptedPeer):
        sending = asyncio.Event()
        release = asyncio.Event()
        received_result = asyncio.Event()

        async def send_binary(self, frame):
            self.sending.set()
            await self.release.wait()
            if not write_completes:
                raise RuntimeError('audio dispatch failed')
            await super().send_binary(frame)

        async def receive_text(self):
            raw = await super().receive_text()
            if '"result-generated"' in raw.text:
                self.received_result.set()
            return raw

    cfg = config()
    peer = HeldAudioPeer()
    adapter = await open_adapter(cfg, peer)
    sending = asyncio.create_task(adapter.send(pcm()))
    await peer.sending.wait()
    peer.feed_json(result(cfg, end=5, final=True))
    await peer.received_result.wait()
    assert adapter.attempted_ranges and not adapter.sent_ranges
    initial = adapter.events()
    assert not just(initial, 'asr.final_candidate')
    peer.release.set()
    if write_completes:
        await sending
        candidate, = just(adapter.events(), 'asr.final_candidate')
        assert candidate['text'] == '首段'
        assert adapter.sent_ranges == ((0, 160),)
        await adapter.aclose()
    else:
        with pytest.raises(ProtocolError, match='send_failed'):
            await sending
        remaining = adapter.events()
        assert not just(remaining, 'asr.final_candidate')
        tentative, = just(remaining, 'asr.transcript_unconfirmed')
        assert tentative['candidate_type'] == 'asr.final_candidate' and tentative['text'] == '首段'
        assert adapter.state == 'unknown' and not adapter.sent_ranges
    assert_clean(peer)


@offline
async def test_cancelling_the_send_waiter_keeps_a_completed_audio_write():
    class CancelWaiterPeer(ScriptedPeer):
        async def send_binary(self, frame):
            await super().send_binary(frame)
            asyncio.get_running_loop().call_soon(writer.cancel)

    cfg = config()
    peer = CancelWaiterPeer()
    adapter = await open_adapter(cfg, peer)
    writer = asyncio.create_task(adapter.send(pcm()))
    with pytest.raises(asyncio.CancelledError):
        await writer
    assert adapter.state == 'unknown'
    assert adapter.sent_ranges == adapter.attempted_ranges == ((0, 160),)
    assert peer.sent_binary == [pcm()['pcm']]
    assert_clean(peer)


@pytest.mark.parametrize('fault,valid_final,anomaly_reason,observed', [
    ('overrun', True, 'overrun', 61),
    ('bad_sentence', False, 'overrun', 61),
    ('bad_usage', True, 'invalid_result_usage', None),
])
@offline
async def test_result_preserves_independent_transcript_and_meter_evidence(fault, valid_final, anomaly_reason, observed):
    cfg = config()
    peer = ScriptedPeer()
    adapter = await open_adapter(cfg, peer)
    await adapter.send(pcm())
    evidence = result(cfg, end=5, final=True,
                      usage={'input_tokens': 60} if fault == 'bad_usage' else usage(61, 2))
    if fault == 'bad_sentence':
        del evidence['payload']['output']['sentence']['begin_time']
    peer.feed_json(evidence)
    await wait_state(adapter, 'unknown')
    assert (await adapter.finish())['state'] == 'unknown'
    events = adapter.events()
    assert len(just(events, 'asr.final_candidate')) == int(valid_final)
    anomaly, = just(events, 'asr.usage_anomaly')
    assert (anomaly['reason'], anomaly['observed_input_tokens']) == (anomaly_reason, observed)
    assert anomaly['provider_request_id'] == 'provider-request'
    assert adapter.usage is None and adapter.billing_pending
    assert_clean(peer)


@pytest.mark.parametrize('metadata', ['time', 'duration'])
@offline
async def test_heartbeat_replay_uses_timing_and_later_rollback_stops_stream(metadata):
    cfg = config()
    peer = ScriptedPeer()
    adapter = await open_adapter(cfg, peer)

    def heartbeat(ms, tokens):
        observation = result(cfg, number=0, begin=ms if metadata == 'time' else 0,
                             text='', usage=usage(tokens, 1))
        observation['payload']['output']['sentence']['heartbeat'] = True
        if metadata == 'duration':
            observation['payload']['usage']['duration'] = ms
        return observation

    first = heartbeat(10, 5)
    for event in (first, heartbeat(20, 10), first, heartbeat(30, 5)):
        peer.feed_json(event)
    await wait_state(adapter, 'unknown')
    assert (await adapter.finish())['state'] == 'unknown'
    events = adapter.events()
    assert not just(events, 'asr.partial') and not just(events, 'asr.final_candidate')
    assert [row['input_tokens'] for row in just(events, 'asr.usage')] == [5, 10]
    anomaly, = just(events, 'asr.usage_anomaly')
    assert (anomaly['reason'], anomaly['observed_input_tokens']) == ('regression', 5)
    assert_clean(peer)


@offline
async def test_final_usage_without_provider_request_identity_keeps_billing_pending():
    cfg = config()
    peer = ScriptedPeer([wire(cfg, 'task-finished', usage=usage(5, 1))])
    adapter = await open_adapter(cfg, peer)
    await adapter.send(pcm())
    peer.feed_json(result(cfg, end=5, final=True, request=None))
    assert (await adapter.finish())['state'] == 'succeeded'
    assert adapter.billing_pending and adapter.usage == TokenUsage(5, 1)
    events = adapter.events()
    final_usage, = just(events, 'asr.usage')
    assert final_usage['final'] and final_usage['provider_request_id'] is None
    assert final_usage['provider_task_id'] == cfg['provider_task_id']
    assert just(events, 'asr.terminal')[0]['billing_pending']
    assert_clean(peer)


@offline
async def test_conflicting_terminal_usage_mirror_keeps_recognition_and_billing_pending():
    cfg = config()
    peer = ScriptedPeer([wire(cfg, 'task-finished', usage=usage(5, 1), output={'usage': usage(6, 1)})])
    adapter = await open_adapter(cfg, peer)
    await adapter.send(pcm())
    peer.feed_json(result(cfg, end=5, final=True, usage=usage(5, 1)))
    assert (await adapter.finish())['state'] == 'succeeded'
    assert adapter.billing_pending and adapter.usage == TokenUsage(5, 1)
    evidence = adapter.events()
    assert just(evidence, 'asr.usage_anomaly')[0]['reason'] == 'invalid_terminal_usage'
    assert not any(row['final'] for row in just(evidence, 'asr.usage'))
    assert_clean(peer)


@offline
async def test_disconnect_settles_the_runnable_audio_writer_before_classifying_final():
    class DisconnectPeer(ScriptedPeer):
        sending = asyncio.Event()
        release = asyncio.Event()
        received_result = asyncio.Event()
        closing = asyncio.Event()
        release_close = asyncio.Event()

        async def send_binary(self, frame):
            self.sending.set()
            await self.release.wait()
            await super().send_binary(frame)

        async def receive_text(self):
            raw = await super().receive_text()
            if '"result-generated"' in raw.text:
                self.received_result.set()
            return raw

        async def close(self):
            self.closing.set()
            await self.release_close.wait()
            await super().close()

    cfg = config()
    peer = DisconnectPeer()
    adapter = await open_adapter(cfg, peer)
    writer = asyncio.create_task(adapter.send(pcm()))
    await peer.sending.wait()
    peer.feed_json(result(cfg, end=5, final=True))
    await peer.received_result.wait()
    assert not just(adapter.events(), 'asr.final_candidate')
    peer.feed_disconnect()
    peer.release.set()
    await peer.closing.wait()
    await asyncio.sleep(0)
    assert not writer.done() and adapter.state != 'unknown'
    with pytest.raises(ProtocolError, match='not accepting frames'):
        await adapter.send(pcm(160))
    peer.release_close.set()
    assert (await writer)['state'] == 'unknown'
    assert adapter.sent_ranges == ((0, 160),)
    remaining = adapter.events()
    assert not just(remaining, 'asr.transcript_unconfirmed')
    assert [event['type'] for event in remaining] == ['asr.final_candidate', 'asr.terminal']
    assert_clean(peer)


@pytest.mark.parametrize('mode', ['independent_final', 'open_partial'])
@offline
async def test_each_transcript_uses_its_confirmed_audio_coverage(mode):
    class SecondFramePeer(ScriptedPeer):
        sending_second = asyncio.Event()
        release_second = asyncio.Event()
        read_initial = asyncio.Event()
        read_later = asyncio.Event()

        async def send_binary(self, frame):
            if self.sent_binary:
                self.sending_second.set()
                await self.release_second.wait()
                raise RuntimeError('second write failed')
            await super().send_binary(frame)

        async def receive_text(self):
            raw = await super().receive_text()
            if '"initial"' in raw.text:
                self.read_initial.set()
            if '"later"' in raw.text:
                self.read_later.set()
            return raw

    cfg = config()
    peer = SecondFramePeer()
    adapter = await open_adapter(cfg, peer)
    await adapter.send(pcm())
    peer.feed_json(result(cfg, end=3, text='initial'))
    await peer.read_initial.wait()
    initial, = just(adapter.events(), 'asr.partial')
    writer = asyncio.create_task(adapter.send(pcm(160)))
    await peer.sending_second.wait()
    if mode == 'independent_final':
        peer.feed_json(result(cfg, number=2, begin=10, text='unconfirmed-other-segment'))
        peer.feed_json(result(cfg, end=10, final=True, text='later'))
    else:
        peer.feed_json(result(cfg, text='later'))
    await peer.read_later.wait()
    visible = adapter.events()
    if mode == 'independent_final':
        final, = just(visible, 'asr.final_candidate')
        assert final['segment_id'] == initial['segment_id'] and final['revision'] == 2
    else:
        assert not just(visible, 'asr.partial')
    peer.release_second.set()
    with pytest.raises(ProtocolError, match='send_failed'):
        await writer
    tentative, = just(adapter.events(), 'asr.transcript_unconfirmed')
    assert tentative['text'] == ('unconfirmed-other-segment' if mode == 'independent_final' else 'later')
    assert adapter.sent_ranges == ((0, 160),)
    assert_clean(peer)


@offline
async def test_open_partial_uses_the_audio_attempt_at_ingress():
    class BufferedPartialPeer(ScriptedPeer):
        delayed_partial = asyncio.Event()
        second_attempt = asyncio.Event()
        release_second = asyncio.Event()
        read_partial = asyncio.Event()

        async def send_binary(self, frame):
            if self.sent_binary:
                self.second_attempt.set()
                await self.release_second.wait()
                raise RuntimeError('second frame never left')
            await super().send_binary(frame)

        async def receive_text(self):
            raw = await super().receive_text()
            if '"buffered partial"' in raw.text:
                self.delayed_partial.set()
                await self.second_attempt.wait()
                self.read_partial.set()
            return raw

    cfg = config()
    peer = BufferedPartialPeer()
    adapter = await open_adapter(cfg, peer)
    await adapter.send(pcm())
    peer.feed_json(result(cfg, text='buffered partial'))
    await peer.delayed_partial.wait()
    writer = asyncio.create_task(adapter.send(pcm(160)))
    await peer.read_partial.wait()
    partial, = just(adapter.events(), 'asr.partial')
    assert partial['text'] == 'buffered partial'
    assert adapter.attempted_ranges == ((0, 160), (160, 320))
    peer.release_second.set()
    with pytest.raises(ProtocolError, match='send_failed'):
        await writer
    assert not just(adapter.events(), 'asr.transcript_unconfirmed')
    assert_clean(peer)


@offline
async def test_failed_audio_attempt_stamp_keeps_the_frame_definitely_unsent():
    class StampFailurePeer(ScriptedPeer):
        def note_audio_attempt(self, number):
            raise RuntimeError('audio stamp unavailable')

    cfg = config()
    peer = StampFailurePeer()
    adapter = await open_adapter(cfg, peer)
    with pytest.raises(ProtocolError, match='send_failed'):
        await adapter.send(pcm())
    assert not peer.sent_binary and not adapter.attempted_ranges and not adapter.sent_ranges
    assert adapter.state == 'unknown'
    assert just(adapter.events(), 'asr.terminal')[0]['billing_pending']
    assert_clean(peer)


@offline
async def test_fake_transport_stamps_audio_attempt_before_dequeue():
    peer = FakeTransport()
    await peer.connect()
    peer.note_audio_attempt(1)
    peer.feed_text('{}')
    peer.note_audio_attempt(2)
    frame = await peer.receive_text()
    assert frame.audio_attempt == 1
    assert frame.text == '{}'
    await peer.close()
    assert_clean(peer)
