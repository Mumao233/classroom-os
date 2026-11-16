"""R1 through the real ASGI application with controlled response backpressure."""
import asyncio
import json
import threading
import time
from urllib.parse import urlsplit

import pytest
import sqlalchemy as sa
from starlette.requests import ClientDisconnect

from test_private_media import media, available, access
from test_authentication import auth
from classroom_web.domain.auth import AuthRejected
from classroom_web.domain.media import ObjectUnavailable
from classroom_web.entrypoints.media_http import MediaRequestLifecycle
from classroom_web.infrastructure.auth_deadlines import DEADLINE


class Admission(threading.BoundedSemaphore):
    def __init__(self):
        super().__init__(8)
        self.acquired = 0
        self.released = 0

    def acquire(self, blocking=True, timeout=None):
        entered = super().acquire(blocking, timeout)
        if entered:
            self.acquired += 1
        return entered

    def release(self, n=1):
        super().release(n)
        self.released += n


def lifecycle(app, budget=3):
    node = app.middleware_stack
    while not isinstance(node, MediaRequestLifecycle):
        node = node.app
    node.budget = budget
    node.slots = Admission()
    return node


class RequestStream:
    def __init__(self, m, path, *, hold=False, spec='2.4', hold_statuses=(200,206)):
        parsed = urlsplit(path)
        self.app = m.client.app
        self.scope = dict(type='http', asgi=dict(version='3.0', spec_version=spec),
            http_version='1.1', method='GET', scheme='https', root_path='',
            path=parsed.path, raw_path=parsed.path.encode(), query_string=parsed.query.encode(),
            headers=[(b'host',b'testserver'),(b'cookie',f'__Host-classroom_session={m.ctx.session}'.encode())],
            client=('127.0.0.1',12345),server=('testserver',443))
        self.messages = []
        self.blocked = asyncio.Event()
        self.gate = asyncio.Event()
        if not hold:
            self.gate.set()
        self.incoming = asyncio.Queue()
        self.incoming.put_nowait(dict(type='http.request',body=b'',more_body=False))
        self.send_error = None
        self.hold_statuses = hold_statuses
        self.status = None
        self.task = None

    async def send(self, message):
        self.messages.append(dict(message))
        if message['type'] == 'http.response.start':
            self.status = message['status']
        if message['type'] == 'http.response.body' and self.status in self.hold_statuses:
            self.blocked.set()
            await self.gate.wait()
            if self.send_error:
                raise self.send_error

    async def run(self):
        inherited = DEADLINE.get()
        try:
            await self.app(self.scope, self.incoming.get, self.send)
        finally:
            assert DEADLINE.get() == inherited

    def start(self):
        self.task = asyncio.create_task(self.run())
        return self


def prepared(m, budget=3):
    _, asset = available(m)
    path = access(m, asset)
    owner = lifecycle(m.client.app, budget)
    return path, owner


def test_eight_open_streams_hold_slots_and_releasing_one_admits_next(media):
    m = media
    path, owner = prepared(m)

    async def scenario():
        sentinel = time.monotonic() + 100
        token = DEADLINE.set(sentinel)
        streams = [RequestStream(m,path,hold=True).start() for _ in range(8)]
        try:
            await asyncio.wait_for(asyncio.gather(*(stream.blocked.wait() for stream in streams)),2)
            assert all(stream.status == 200 and not stream.task.done() for stream in streams)
            ninth = RequestStream(m,path).start()
            await asyncio.wait_for(ninth.task,1)
            assert ninth.status == 429 and owner.slots.released == 0
            streams[0].gate.set()
            await asyncio.wait_for(streams[0].task,1)
            replacement = RequestStream(m,path,hold=True).start()
            streams.append(replacement)
            await asyncio.wait_for(replacement.blocked.wait(),1)
            assert replacement.status == 200 and owner.slots.released == 1
        finally:
            for stream in streams:
                stream.gate.set()
            await asyncio.gather(*(stream.task for stream in streams))
            assert DEADLINE.get() == sentinel
            DEADLINE.reset(token)
        assert owner.slots.acquired == owner.slots.released == 9

    asyncio.run(scenario())


def test_read_blocks_keep_original_deadline_and_normal_completion_releases_once(media,monkeypatch):
    m = media
    path, owner = prepared(m)
    observed = []
    original = m.service.read_block

    def read(*args):
        observed.append(DEADLINE.get())
        return original(*args)
    monkeypatch.setattr(m.service,'read_block',read)

    async def scenario():
        request = RequestStream(m,path).start()
        await asyncio.wait_for(request.task,2)
        assert request.status == 200
        assert len(observed) >= 3 and observed[0] is not None
        assert all(value == observed[0] for value in observed)
        assert owner.slots.acquired == owner.slots.released == 1
    asyncio.run(scenario())


def test_deadline_after_first_block_waits_for_the_active_storage_thread(media,monkeypatch):
    m = media
    path, owner = prepared(m,.2)
    original = m.service.read_block
    observed = []
    completed = threading.Event()

    def read(*args):
        observed.append(DEADLINE.get())
        if len(observed) == 2:
            assert observed[1] == observed[0] and observed[1] is not None
            time.sleep(max(0,observed[1]-time.monotonic())+.02)
            try:
                return original(*args)
            finally:
                completed.set()
        return original(*args)
    monkeypatch.setattr(m.service,'read_block',read)

    async def scenario():
        start = time.monotonic()
        request = RequestStream(m,path).start()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(request.task,1)
        assert request.status == 200 and completed.is_set()
        assert .15 <= time.monotonic()-start < 1
        assert len(observed) == 2
        assert owner.slots.acquired == owner.slots.released == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('spec',['2.0','2.4'])
def test_slow_send_expires_and_frees_admission(media,spec):
    m = media
    path, owner = prepared(m,.15)

    async def scenario():
        start = time.monotonic()
        request = RequestStream(m,path,hold=True,spec=spec).start()
        await asyncio.wait_for(request.blocked.wait(),1)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(request.task,1)
        assert time.monotonic()-start < .7
        assert len([message for message in request.messages if message['type']=='http.response.start']) == 1
        assert owner.slots.acquired == owner.slots.released == 1
        owner.budget = 2
        probe = RequestStream(m,path).start()
        await asyncio.wait_for(probe.task,1)
        assert probe.status == 200 and owner.slots.acquired == owner.slots.released == 2
    asyncio.run(scenario())


@pytest.mark.parametrize('stage',['download','read_block'])
def test_pre_header_expiry_returns_bounded_contract_error(media,monkeypatch,stage):
    m = media
    path, owner = prepared(m,.15)
    original = getattr(m.service,stage)

    def delayed(*args):
        time.sleep(max(0,DEADLINE.get()-time.monotonic())+.02)
        return original(*args)
    monkeypatch.setattr(m.service,stage,delayed)

    async def scenario():
        request = RequestStream(m,path).start()
        await asyncio.wait_for(request.task,1)
        starts = [message for message in request.messages if message['type']=='http.response.start']
        body = b''.join(message['body'] for message in request.messages if message['type']=='http.response.body')
        assert len(starts) == 1 and starts[0]['status'] == 503
        assert dict(starts[0]['headers'])[b'cache-control'] == b'no-store'
        assert json.loads(body)['error']['code'] == 'RESULT_UNKNOWN'
        assert request.scope['query_string'] == b''
        assert owner.slots.acquired == owner.slots.released == 1
    asyncio.run(scenario())


def test_pre_header_error_send_is_also_bounded(media,monkeypatch):
    m = media
    path, owner = prepared(m,.15)
    original = m.service.download

    def delayed(*args):
        time.sleep(max(0,DEADLINE.get()-time.monotonic())+.02)
        return original(*args)
    monkeypatch.setattr(m.service,'download',delayed)

    async def scenario():
        started = time.monotonic()
        request = RequestStream(m,path,hold=True,hold_statuses=(503,)).start()
        await asyncio.wait_for(request.blocked.wait(),1)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(request.task,1)
        assert time.monotonic()-started < .7
        assert len([message for message in request.messages if message['type']=='http.response.start']) == 1
        assert request.status == 503 and owner.slots.acquired == owner.slots.released == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('spec',['2.0','2.4'])
def test_disconnect_releases_the_stream_once(media,spec):
    m = media
    path, owner = prepared(m)

    async def scenario():
        request = RequestStream(m,path,hold=True,spec=spec).start()
        await asyncio.wait_for(request.blocked.wait(),1)
        if spec == '2.0':
            request.incoming.put_nowait(dict(type='http.disconnect'))
            await asyncio.wait_for(request.task,1)
        else:
            request.send_error = OSError('synthetic disconnect')
            request.gate.set()
            with pytest.raises(ClientDisconnect):
                await asyncio.wait_for(request.task,1)
        assert owner.slots.acquired == owner.slots.released == 1
    asyncio.run(scenario())


def test_later_storage_error_releases_the_stream_once(media,monkeypatch):
    m = media
    path, owner = prepared(m)
    original = m.store.read
    calls = 0

    def read(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ObjectUnavailable('synthetic later block failure')
        return original(*args)
    monkeypatch.setattr(m.store,'read',read)

    async def scenario():
        request = RequestStream(m,path).start()
        with pytest.raises(AuthRejected) as error:
            await asyncio.wait_for(request.task,1)
        assert request.status == 200 and error.value.status == 503
        assert owner.slots.acquired == owner.slots.released == 1
    asyncio.run(scenario())


def test_http_stream_stops_after_revocation_between_blocks(media):
    m = media
    path, owner = prepared(m)
    asset_id = path.split('/media/',1)[1].split('/',1)[0]

    async def scenario():
        request = RequestStream(m,path,hold=True).start()
        await asyncio.wait_for(request.blocked.wait(),1)
        with m.a.pg.admin.begin() as connection:
            connection.execute(sa.text('UPDATE media_assets SET access_revoked_at=clock_timestamp() WHERE id=:id'),dict(id=asset_id))
        request.gate.set()
        with pytest.raises(AuthRejected) as error:
            await asyncio.wait_for(request.task,1)
        assert error.value.status == 410
        assert len([message for message in request.messages if message['type']=='http.response.body' and message['body']]) == 1
        assert owner.slots.acquired == owner.slots.released == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('kind',['cancel','disconnect'])
def test_cancellation_keeps_slot_until_storage_finishes(media,monkeypatch,kind):
    m = media
    path, owner = prepared(m)
    original = m.service.read_block
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def read(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            assert release.wait(2)
        return original(*args)
    monkeypatch.setattr(m.service,'read_block',read)

    async def scenario():
        request = RequestStream(m,path,spec='2.0' if kind == 'disconnect' else '2.4').start()
        try:
            assert await asyncio.to_thread(entered.wait,1)
            if kind == 'disconnect':
                request.incoming.put_nowait(dict(type='http.disconnect'))
            else:
                request.task.cancel()
            await asyncio.sleep(.01)
            if kind == 'cancel':
                request.task.cancel()
            await asyncio.sleep(.01)
            assert not request.task.done() and owner.slots.released == 0
        finally:
            release.set()
        if kind == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(request.task,1)
        else:
            await asyncio.wait_for(request.task,1)
        assert owner.slots.acquired == owner.slots.released == 1
    asyncio.run(scenario())
