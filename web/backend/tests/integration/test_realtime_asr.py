"""Synthetic provider wire, actual media and PG transactions, frozen Web API."""
import asyncio
import json
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from classroom_web.application.asr import AsrService
from classroom_web.application.asr_worker import AsrWorker
from classroom_web.asr_dashscope.transport import FakeTransport
from classroom_web.entrypoints.http import create_app
from deletion_support import auth, pg, services, deploy, d, contract
from test_authentication import BASE
from test_billing_integration import fee_control
from test_capture_recovery import (
    CONTENT, acquire, capture, command, headers, prepare, put,
)


@pytest.fixture
def live(capture, fee_control):
    m, fees = capture, fee_control
    flags = {'billing': True, 'asr_streaming': True}
    price = uuid4()
    rates = {key: {'microyuan': rate, 'units': 1} for key, rate in
             (('audio_duration_ms', 0), ('input_tokens', 1), ('output_tokens', 1))}
    with m.pg.admin.begin() as c:
        revision = c.scalar(sa.text('SELECT revision FROM billing_control.switches'))
        c.execute(sa.text("""SELECT billing_api.update_switches(:actor,:op,:revision,
            CAST(:flags AS jsonb),'synthetic ASR integration')"""),
            {'actor': fees.actor, 'op': uuid4(), 'revision': revision, 'flags': json.dumps(flags)})
        c.execute(sa.text("""INSERT INTO price_catalog(id,provider,model,region,revision,
            currency,rates,minimum_microyuan,synthetic,effective_at)
            VALUES(:id,'aliyun','qwen-audio-3.1-asr-flash-streaming','cn-beijing',:revision,
            'CNY',CAST(:rates AS jsonb),0,true,'2020-01-01T00:00:00Z')"""),
            {'id': price, 'revision': str(price), 'rates': json.dumps(rates)})
    m.asr = AsrService(m.capture, enabled=True, price_id=price)
    app = create_app(auth=m.a.service, contract=contract(), origin='https://testserver',
        capture=m.capture, deletion=m.deletion, asr=m.asr, websocket_admission=m.a.ws_admission)
    with TestClient(app, base_url='https://testserver') as client:
        client.cookies.update(m.client.cookies)
        m.client = m.a.client = client
        m.fees = fees
        yield m


def create(m, lease, ids=None, *, mode='live', key=None):
    return m.client.post(BASE + f'/class-sessions/{m.classroom.id}/asr-streams', headers=headers(m, key),
        json=dict(capture_epoch_id=lease['capture_epoch_id'], fence_token=lease['fence_token'],
                  consent_version='synthetic-v1', source_chunk_ids=ids or [], mode=mode))


def get(m, stream):
    return m.client.get(BASE + '/asr-streams/' + stream['id'], headers={'Origin': 'https://testserver'}).json()


def finish(m, stream, key=None):
    return m.client.post(BASE + '/asr-streams/' + stream['id'] + '/finish', headers=headers(m, key), json={})


class Peer(FakeTransport):
    def __init__(self, stream, *, disconnect=False):
        super().__init__()
        self.disconnect = disconnect
        self.first_frame = None
        self.task_id = str(stream['provider_task_id'])
        self.request_id = 'synthetic-' + self.task_id

    def event(self, event, **payload):
        return {'header': {'event': event, 'task_id': self.task_id}, 'payload': payload}

    async def send_json(self, message, *, on_dispatched=None):
        await super().send_json(message, on_dispatched=on_dispatched)
        if message['header']['action'] == 'run-task':
            self.feed_json(self.event('task-started'))
        else:
            sample_count = sum(len(frame) // 2 for frame in self.sent_binary)
            if sample_count:
                sentence = {'sentence_id': 1, 'begin_time': 0, 'end_time': sample_count // 16,
                            'sentence_end': True, 'text': '合成字幕。'}
                final = self.event('result-generated', output=dict(request_id=self.request_id, sentence=sentence))
                self.feed_json(final)
                self.feed_json(final)
            self.feed_json(self.event('task-finished', usage={'input_tokens': 8, 'output_tokens': 3, 'total_tokens': 11}))

    async def send_binary(self, frame):
        await super().send_binary(frame)
        if self.first_frame and len(self.sent_binary) == 1:
            await asyncio.to_thread(self.first_frame)
        if self.disconnect:
            self.feed_disconnect()
            await asyncio.sleep(0)


def run(m, *, disconnect=False, first_frame=None):
    peers = []
    def factory(stream):
        peer = Peer(stream, disconnect=disconnect)
        peer.first_frame = first_frame
        peers.append(peer)
        return peer
    worker = AsrWorker(m.asr, m.fees.scheduler, m.fees.lifecycle, worker_id='synthetic-asr', make_transport=factory)
    assert asyncio.run(worker.run_once())
    return peers


def test_from_capture_through_billing_and_durable_final(live):
    m = live
    lease = acquire(m)
    pending = prepare(m, lease)
    assert create(m, lease, [pending['id']], mode='catch_up').status_code == 404
    durable = put(m, pending)
    assert durable.status_code == 200
    key = str(uuid4())
    opened = create(m, lease, key=key)
    assert opened.status_code == 201, (opened.json(), list(m.client.app.state.errors))
    stream = opened.json()
    assert create(m, lease, key=key).json() == stream
    assert create(m, lease).status_code == 409
    principal = m.a.service.principal(m.ctx)
    with m.pg.db.authorized_transaction(principal) as tx:
        cursor = tx.events.watermark()
    assert finish(m, stream).status_code == 200
    peers = run(m)
    assert len(peers) == 1 and b''.join(peers[0].sent_binary) == CONTENT
    completed = get(m, stream)
    assert completed['state'] == 'succeeded', completed
    assert completed['submitted_end_sample'] == completed['confirmed_end_sample'] == 16000
    assert finish(m, stream).json()['state'] == 'succeeded'
    page = m.client.get(BASE + f'/class-sessions/{m.classroom.id}/transcript-versions')
    assert page.status_code == 200
    version, = page.json()['items']
    segments = m.client.get(BASE + f'/transcript-versions/{version["id"]}/segments').json()['items']
    segment, = segments
    assert (segment['text'], segment['start_ms'], segment['end_ms']) == ('合成字幕。', 0, 1000)
    assert segment['source_chunk_ids'] == [pending['id']]
    next_cursor, _, events, _, _ = m.asr.events.poll(m.ctx, cursor, 0)
    assert next_cursor != cursor
    final, = (event for event in events if event['type'] == 'transcript.final')
    assert final['data']['text'] == segment['text']
    response = m.client.post(BASE+'/ws-tickets', headers=headers(m), json=dict(channel='events', class_session_id=None, capture_epoch_id=None, fence_token=None))
    assert response.status_code == 201
    with m.client.websocket_connect('wss://testserver'+BASE+'/ws/events',
            subprotocols=['classroom.v1'], headers={'Origin':'https://testserver'}) as ws:
        ws.send_json({'type':'authenticate','ticket':response.json()['ticket']})
        ws.send_json({'type':'resume.after','cursor':cursor})
        replayed = []
        for _ in range(12):
            event = ws.receive_json()
            replayed.append(event)
            if event['type'] == 'replay.complete':
                break
        assert [event for event in replayed if event['type'] == 'transcript.final'] == [final]
    with m.pg.admin.connect() as c:
        payload = c.execute(sa.text("""SELECT payload FROM outbox_events
            WHERE owner_id=:owner AND event_type='transcript.final'"""), {'owner': m.uid}).scalar_one()
        assert payload == {'segment_revision_id': segment['segment_revision_id']}
        state = c.execute(sa.text("""SELECT state,settled_microyuan FROM quota_reservations
            WHERE task_id=:task"""), {'task': stream['task_id']}).one()
        assert (state.state, state.settled_microyuan) == ('settled', 11)
    assert create(m, lease, [pending['id']], mode='catch_up').status_code == 409
    later = prepare(m, lease, seq=1)
    assert put(m, later).status_code == 200
    successor = create(m, lease)
    assert successor.status_code == 201 and successor.json()['id'] != stream['id']
    assert finish(m, stream).json()['id'] == stream['id']
    assert get(m, successor.json())['state'] == 'idle'
    assert finish(m, successor.json()).status_code == 200
    assert b''.join(run(m)[0].sent_binary) == CONTENT
    assert get(m, successor.json())['confirmed_end_sample'] == 32000


def test_switch_blocks_next_frame_but_allows_final_and_usage(live):
    m = live
    lease = acquire(m)
    chunk = prepare(m, lease)
    assert put(m, chunk).status_code == 200
    response = create(m, lease, [chunk['id']], mode='catch_up')
    assert response.status_code == 201
    def close_switch():
        with m.pg.admin.begin() as c:
            revision = c.scalar(sa.text('SELECT revision FROM billing_control.switches'))
            c.execute(sa.text("""SELECT billing_api.update_switches(:actor,:op,:revision,
                CAST(:flags AS jsonb),'synthetic interruption')"""),
                {'actor': m.fees.actor, 'op': uuid4(), 'revision': revision,
                 'flags': json.dumps({'billing': True, 'asr_streaming': False})})
    peer, = run(m, first_frame=close_switch)
    assert peer.sent_binary == [CONTENT[:3200]]
    stream = get(m, response.json())
    assert stream['state'] == 'succeeded'
    assert stream['submitted_end_sample'] == stream['confirmed_end_sample'] == 1600
    assert create(m, lease).status_code == 409


def test_retired_recovery_upload_requires_independent_catch_up(live):
    m = live
    original = acquire(m)
    pending = prepare(m, original)
    assert command(m, original, 'stop', manifest_sha256='a'*64, known_gaps=[]).status_code == 200
    result = m.client.post(BASE + f'/class-sessions/{m.classroom.id}/capture/recovery-authorize',
        headers=headers(m), json=dict(capture_epoch_id=original['capture_epoch_id'], manifest_sha256='a'*64,
            known_last_seq=0, known_last_sample=16000, confirm_retired_epoch=True))
    assert result.status_code == 200 and result.json()['asr_authorized'] is False
    recovered = prepare(m, original, token=result.json()['recovery_upload_token'])
    assert recovered['id'] == pending['id']
    assert put(m, recovered).status_code == 200
    worker = AsrWorker(m.asr, m.fees.scheduler, m.fees.lifecycle, worker_id='no-auto',
                       make_transport=lambda _: pytest.fail('upload started ASR'))
    assert not asyncio.run(worker.run_once())
    assert create(m, original).status_code == 409
    response = create(m, original, [recovered['id']], mode='catch_up')
    assert response.status_code == 201, (response.json(), list(m.client.app.state.errors))
    assert b''.join(run(m)[0].sent_binary) == CONTENT
    assert get(m, response.json())['state'] == 'succeeded'


def test_worker_restart_retains_committed_frame_intent(live):
    from classroom_web.domain.asr import source_frames
    m = live
    lease = acquire(m)
    chunk = prepare(m, lease)
    assert put(m, chunk).status_code == 200
    response = create(m, lease, [chunk['id']], mode='catch_up')
    assert response.status_code == 201
    first = AsrWorker(m.asr, m.fees.scheduler, m.fees.lifecycle, worker_id='interrupted',
                      make_transport=lambda _: pytest.fail('unexpected dispatch after restart'))
    claim, = m.fees.scheduler.claim('interrupted', schema_version='pr06b-1')
    _, _, (source, _, samples), _ = first._candidate(claim)
    stream, _, _, _ = first._start(claim, source)
    frame = source_frames(source, CONTENT, samples)[0]
    first._mint_frame(claim, stream, frame, 0)
    with m.pg.admin.begin() as c:
        c.execute(sa.text("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:task"),
                  {'task': claim.id})
    assert not asyncio.run(first.run_once())
    stored = get(m, response.json())
    assert stored['state'] == 'unknown' and stored['submitted_end_sample'] == frame.samples.end
    assert create(m, lease, [chunk['id']], mode='catch_up').status_code == 409


def test_unknown_requires_explicit_new_attempt_and_preserves_original(live):
    m = live
    lease = acquire(m)
    chunk = prepare(m, lease)
    assert put(m, chunk).status_code == 200
    response = create(m, lease, [chunk['id']], mode='catch_up')
    assert response.status_code == 201, (response.json(), list(m.client.app.state.errors))
    original = response.json()
    peers = run(m, disconnect=True)
    assert len(peers) == 1 and peers[0].sent_binary
    assert get(m, original)['state'] == 'unknown'
    assert create(m, lease, [chunk['id']], mode='catch_up').status_code == 409
    task = m.client.get(BASE + '/tasks/' + original['task_id']).json()
    assert task['state'] == 'unknown' and task['may_have_charged']
    retry_url = BASE + '/tasks/' + original['task_id'] + '/retry'
    key = str(uuid4())
    body = dict(reason='合成重试', original_attempt_id=task['attempt_id'], confirm_duplicate_billing=False)
    assert m.client.post(retry_url, headers=headers(m), json=body).status_code == 422
    body['confirm_duplicate_billing'] = True
    new = m.client.post(retry_url, headers=headers(m, key), json=body)
    assert new.status_code == 202, (new.json(), list(m.client.app.state.errors))
    new_task = new.json()
    assert new_task['parent_task_id'] == original['task_id']
    assert m.client.post(retry_url, headers=headers(m, key), json=body).json()['id'] == new_task['id']
    assert m.client.post(retry_url, headers=headers(m), json=body).status_code == 409
    assert m.client.get(BASE + '/tasks/' + new_task['id']).json()['state'] == 'queued'
    cancelled = m.client.post(BASE + '/tasks/' + new_task['id'] + '/cancel', headers=headers(m), json={})
    assert cancelled.status_code == 200
    assert cancelled.json()['state'] == 'cancelled'
    assert get(m, original)['state'] == 'unknown'
