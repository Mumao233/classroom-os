"""The asynchronous boundary for one DashScope WebSocket connection."""
import asyncio
from collections.abc import Callable
from copy import deepcopy
import json
from threading import Lock
from typing import Protocol


class AuthenticationRejected(ConnectionError):
    """The handshake was rejected before run-task could be sent."""


class Disconnected(ConnectionError):
    """No more frames can be read from the connection."""


class Transport(Protocol):
    """One owner, one reader and at most one writer on the same event loop.

    The caller supplies a configured transport. Its I/O must cooperate with
    cancellation. For a control write with on_dispatched, invoke the callback
    synchronously when the frame has been handed to the underlying connection,
    before yielding to a reader that could observe its acknowledgement.
    A successful return confirms completion of the local write. After a partial
    connect or a failed close, abort must immediately release the connection.
    """

    offline: bool

    def claim(self) -> None: ...
    async def connect(self) -> None: ...
    async def send_json(self, message: dict, *, on_dispatched: Callable[[], None] | None = None) -> None: ...
    async def send_binary(self, frame: bytes) -> None: ...
    async def receive_text(self) -> str: ...
    async def close(self) -> None: ...
    def abort(self) -> None: ...


class FakeTransport:
    """An offline scriptable peer with ordered outbound and cleanup evidence."""

    offline = True

    def __init__(self):
        self._claim_lock = Lock()
        self._claimed = False
        self.connect_count = 0
        self.close_count = 0
        self.abort_count = 0
        self.connected = False
        self.closed = False
        self.sent = []
        self._inbound = asyncio.Queue()

    @property
    def sent_json(self):
        return [value for kind, value in self.sent if kind == 'json']

    @property
    def sent_binary(self):
        return [value for kind, value in self.sent if kind == 'binary']

    def feed_json(self, value):
        self.feed_text(json.dumps(value, ensure_ascii=False))

    def feed_text(self, value):
        self._inbound.put_nowait(value)

    def feed_disconnect(self):
        self._inbound.put_nowait(Disconnected('connection closed'))

    def feed_failure(self, error: Exception):
        self._inbound.put_nowait(error)

    def claim(self):
        with self._claim_lock:
            if self._claimed or self.connect_count or self.closed:
                raise Disconnected('connection already owned')
            self._claimed = True

    async def connect(self):
        if self.connect_count or self.closed:
            raise Disconnected('connection already used')
        self.connect_count += 1
        self.connected = True

    def _require_connection(self):
        if not self.connected or self.closed:
            raise Disconnected('connection unavailable')

    async def send_json(self, message, *, on_dispatched=None):
        self._require_connection()
        self.sent.append(('json', deepcopy(message)))
        if on_dispatched is not None:
            on_dispatched()

    async def send_binary(self, frame):
        self._require_connection()
        self.sent.append(('binary', frame))

    async def receive_text(self):
        self._require_connection()
        value = await self._inbound.get()
        if isinstance(value, Exception):
            raise value
        self._require_connection()
        return value

    def _release(self):
        self.connected = False
        self.closed = True
        while not self._inbound.empty():
            self._inbound.get_nowait()
        self.feed_disconnect()

    async def close(self):
        if not self.closed:
            self.close_count += 1
            self._release()

    def abort(self):
        if not self.closed:
            self.abort_count += 1
            self._release()
