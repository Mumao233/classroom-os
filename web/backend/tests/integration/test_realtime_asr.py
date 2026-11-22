"""Synthetic provider wire, actual media and PG transactions, frozen Web API."""
import asyncio
import json
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from classroom_web.application.asr import AsrService
from classroom_web.application.asr_worker import AsrWorker
from classroom_web.asr_dashscope.transport import FakeTransport
from classroom_web.entrypoints.http import create_app
from deletion_support import auth, pg, services, deploy, d, contract
from test_authentication import BASE
from test_billing_integration import fee_control
from test_deletion_lifecycle import delete
from test_capture_recovery import (
    CONTENT, acquire, capture, command, headers, prepare, put, read_manifests,
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
        self.on_finish = None
        self.fault = None
        self.task_id = str(stream['provider_task_id'])
        self.request_id = 'synthetic-' + self.task_id

    def event(self, event, **payload):
        return {'header': {'event': event, 'task_id': self.task_id}, 'payload': payload}

    async def send_json(self, message, *, on_dispatched=None):
        await super().send_json(message, on_dispatched=on_dispatched)
        if message['header']['action'] == 'run-task':
            self.feed_json(self.event('task-started'))
        else:
            if self.on_finish:
                await asyncio.to_thread(self.on_finish)
            sample_count = sum(len(frame) // 2 for frame in self.sent_binary)
            if self.fault == 'confirmed_suffix':
                for index, split, end, final in ((1, 0, None, False), (2, 500, 1000, True)):
                    sentence = {'sentence_id': index, 'begin_time': split, 'end_time': end,
                                'sentence_end': final, 'text': '合成片段。'}
                    self.feed_json(self.event('result-generated',
                        output=dict(request_id=self.request_id, sentence=sentence)))
                self.feed_disconnect()
                return
            if sample_count:
                sentence = {'sentence_id': 1, 'begin_time': 0, 'end_time': sample_count // 16,
                            'sentence_end': True, 'text': '合成字幕。'}
                final = self.event('result-generated', output=dict(request_id=self.request_id, sentence=sentence))
                self.feed_json(final)
                self.feed_json(final)
            tokens = 20_000 if self.fault == 'overrun' else 8
            self.feed_json(self.event('task-finished',
                usage={'input_tokens': tokens, 'output_tokens': 3, 'total_tokens': tokens + 3}))

    async def send_binary(self, frame):
        await super().send_binary(frame)
        if self.first_frame and len(self.sent_binary) == 1:
            await asyncio.to_thread(self.first_frame)
        if self.disconnect:
            self.feed_disconnect()
            await asyncio.sleep(0)


def run(m, *, disconnect=False, first_frame=None, on_finish=None, fault=None, handoff=None, mint_fault=False):
    peers = []
    def factory(stream):
        peer = Peer(stream, disconnect=disconnect)
        peer.first_frame = first_frame
        peer.on_finish, peer.fault = on_finish, fault
        peers.append(peer)
        return peer
    worker = AsrWorker(m.asr, m.fees.scheduler, m.fees.lifecycle, worker_id='synthetic-asr', make_transport=factory)
    if handoff is not None:
        worker._handoff = handoff
    if mint_fault:
        from sqlalchemy.exc import DBAPIError
        mint = worker._mint_frame
        def lost_commit_ack(claim, stream, frame):
            receipt = mint(claim, stream, frame)
            if frame.samples.start == 1600:
                raise DBAPIError(None, None, ConnectionError('synthetic COMMIT response lost'), connection_invalidated=True)
            return receipt
        worker._mint_frame = lost_commit_ack
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
        source = c.execute(sa.text("""SELECT s.origin, s.object_id, v.text FROM qa_sources s
            JOIN qa_source_versions v ON (s.owner_id,s.id,s.current_revision_id)=(v.owner_id,v.source_id,v.id)
            WHERE s.owner_id=:owner AND s.id=:id AND v.id=:revision"""),
            {'owner': m.uid, 'id': segment['id'], 'revision': segment['segment_revision_id']}).one()
        assert (source.origin, str(source.object_id), source.text) == ('live_asr', segment['id'], segment['text'])
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
    first._mint_frame(claim, stream, frame)
    with m.pg.admin.begin() as c:
        c.execute(sa.text("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:task"),
                  {'task': claim.id})
    assert not asyncio.run(first.run_once())
    stored = get(m, response.json())
    assert stored['state'] == 'unknown' and stored['submitted_end_sample'] == frame.samples.end
    single = read_manifests(m, {'capture_epoch_id': lease['capture_epoch_id']}, single=True)
    page = read_manifests(m)
    assert single.status_code == page.status_code == 200
    assert page.json() == {'items': [single.json()], 'next_cursor': None}
    assert single.json()['unknown_provider_ranges'] == [{'start_sample': 0, 'end_sample': 1600}]
    schema = {'$ref': '#/components/schemas/AudioManifestPage', 'components': contract()['components']}
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(page.json())
    fresh = create(m, lease, [chunk['id']], mode='catch_up')
    assert fresh.status_code == 201
    assert b''.join(run(m)[0].sent_binary) == CONTENT[3200:]
    assert get(m, response.json())['state'] == 'unknown'


def test_no_second_audio_write_when_committed_frame_permit_loses_ack(live):
    m = live
    lease = acquire(m)
    chunk = prepare(m, lease)
    assert put(m, chunk).status_code == 200
    opened = create(m, lease, [chunk['id']], mode='catch_up')
    assert opened.status_code == 201
    peer, = run(m, mint_fault=True)
    assert peer.sent_binary == [CONTENT[:3200]]
    stream = get(m, opened.json())
    assert stream['state'] == 'unknown'
    assert (stream['submitted_end_sample'], stream['confirmed_end_sample']) == (3200, 1600)
    original_task = m.client.get(BASE + '/tasks/' + stream['task_id']).json()
    retry = m.client.post(BASE + '/tasks/' + stream['task_id'] + '/retry', headers=headers(m),
        json=dict(confirm_duplicate_billing=True, reason='合成确认丢失', original_attempt_id=original_task['attempt_id']))
    assert retry.status_code == 202
    assert b''.join(run(m)[0].sent_binary) == CONTENT[3200:6400]


def test_uncommitted_earlier_chunk_blocks_first_live_frame(live):
    m = live
    lease = acquire(m)
    earlier = prepare(m, lease, seq=0)
    later = prepare(m, lease, seq=1)
    assert put(m, later).status_code == 200
    opened = create(m, lease)
    assert opened.status_code == 201
    worker = AsrWorker(m.asr, m.fees.scheduler, m.fees.lifecycle, worker_id='synthetic-predecessor',
                      make_transport=Peer)
    claim, = m.fees.scheduler.claim('synthetic-predecessor', schema_version='pr06b-1')
    _, _, candidate, _ = worker._candidate(claim)
    assert candidate is None
    assert put(m, earlier).status_code == 200
    assert finish(m, opened.json()).status_code == 200
    asyncio.run(worker.run_claim(claim))
    assert get(m, opened.json())['submitted_end_sample'] == 32000


def test_handoff_write_error_preserves_unknown_and_continues_usage(live):
    from sqlalchemy.exc import DBAPIError
    m = live
    lease = acquire(m)
    chunk = prepare(m, lease)
    assert put(m, chunk).status_code == 200
    opened = create(m, lease, [chunk['id']], mode='catch_up')
    assert opened.status_code == 201

    def fail(*args):
        raise DBAPIError(None, None, ConnectionError('synthetic handoff fault'), connection_invalidated=True)

    peer, = run(m, handoff=fail)
    assert peer.sent_binary == [CONTENT[:3200]]
    stream = get(m, opened.json())
    assert stream['state'] == 'unknown' and stream['confirmed_end_sample'] == 0
    with m.pg.admin.connect() as c:
        evidence = c.execute(sa.text("""SELECT settled_microyuan FROM quota_reservations
            WHERE task_id=:task"""), {'task': stream['task_id']}).scalar_one()
        assert evidence == 11


def test_deleted_classroom_blocks_final_and_keeps_original_accounting(live):
    m = live
    lease = acquire(m)
    chunk = prepare(m, lease)
    assert put(m, chunk).status_code == 200
    opened = create(m, lease, [chunk['id']], mode='catch_up')
    assert opened.status_code == 201

    revoked = []
    def revoke_classroom():
        revoked.append(delete(m, 'class_session', m.classroom.id))

    run(m, on_finish=revoke_classroom)
    assert len(revoked) == 1
    with m.pg.admin.connect() as c:
        amount = c.execute(sa.text("SELECT settled_microyuan FROM quota_reservations WHERE task_id=:task"),
            {'task': opened.json()['task_id']}).scalar_one()
        assert amount == 11
        assert c.execute(sa.text("""SELECT count(*) FROM outbox_events
            WHERE owner_id=:owner AND event_type='transcript.final'"""), {'owner': m.uid}).scalar_one() == 0


def test_known_suffix_does_not_mask_earlier_unknown_pcm(live):
    m = live
    lease = acquire(m)
    chunk = prepare(m, lease)
    assert put(m, chunk).status_code == 200
    opened = create(m, lease, [chunk['id']], mode='catch_up')
    assert opened.status_code == 201
    run(m, fault='confirmed_suffix')
    original = get(m, opened.json())
    assert original['state'] == 'unknown' and original['confirmed_end_sample'] == 16000
    task = m.client.get(BASE + '/tasks/' + original['task_id']).json()
    result = m.client.post(BASE + '/tasks/' + task['id'] + '/retry', headers=headers(m),
        json=dict(confirm_duplicate_billing=True, reason='合成区间验证', original_attempt_id=task['attempt_id']))
    assert result.status_code == 202, (result.json(), list(m.client.app.state.errors))
    peer, = run(m)
    assert b''.join(peer.sent_binary) == CONTENT[:16000]


def test_overrun_is_durable_exposure_even_with_successful_transcript(live):
    m = live
    lease = acquire(m)
    chunk = prepare(m, lease)
    assert put(m, chunk).status_code == 200
    opened = create(m, lease, [chunk['id']], mode='catch_up')
    assert opened.status_code == 201
    run(m, fault='overrun')
    assert get(m, opened.json())['state'] == 'succeeded'
    with m.pg.admin.connect() as c:
        state = c.execute(sa.text("""SELECT state,settled_microyuan FROM quota_reservations
            WHERE task_id=:task"""), {'task': opened.json()['task_id']}).one()
        assert (state.state, state.settled_microyuan) == ('unknown', 20003)


def test_takeover_cannot_reactivate_an_old_paused_live_stream(live):
    m = live
    old = acquire(m)
    chunk = prepare(m, old)
    assert put(m, chunk).status_code == 200
    stream = create(m, old)
    assert stream.status_code == 201
    assert command(m, old, 'pause').status_code == 200
    acquire(m, confirm_takeover=True, expected_lease_id=old['id'])
    grant = m.client.post(BASE + f'/class-sessions/{m.classroom.id}/capture/recovery-authorize',
        headers=headers(m), json=dict(capture_epoch_id=old['capture_epoch_id'], manifest_sha256='1'*64,
            known_last_seq=0, known_last_sample=16000, confirm_retired_epoch=True))
    assert grant.status_code == 200
    assert not run(m)
    assert get(m, stream.json())['submitted_end_sample'] == 0
    explicit = create(m, old, [chunk['id']], mode='catch_up')
    assert explicit.status_code == 201
    assert b''.join(run(m)[0].sent_binary) == CONTENT


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
