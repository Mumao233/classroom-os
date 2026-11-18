"""The asynchronous boundary for one DashScope WebSocket connection."""
import asyncio
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
import json
from threading import Lock
from typing import Protocol


class AuthenticationRejected(ConnectionError):
    """The handshake was rejected before run-task could be sent."""


class Disconnected(ConnectionError):
    """No more frames can be read from the connection."""


@dataclass(frozen=True)
class InboundText:
    text: str = field(repr=False)
    control_sequence: int
    audio_attempt: int


class Transport(Protocol):
    """One owner, one reader and at most one writer on the same event loop.

    The caller supplies a configured transport. Its I/O must cooperate with
    cancellation. For a control write with on_dispatched, invoke the callback
    synchronously with its increasing control sequence when that frame has been
    handed to the underlying connection. Every inbound text frame carries the
    latest such sequence and audio attempt at ingress, before handing it to a
    consumer. The adapter records a new attempt synchronously before each
    binary write. A successful return confirms completion. After a partial
    connect or a failed close, abort must immediately release the connection.
    """

    offline: bool

    def claim(self) -> None: ...
    async def connect(self) -> None: ...
    async def send_json(self, message: dict, *, on_dispatched: Callable[[int], None] | None = None) -> None: ...
    def note_audio_attempt(self, number: int) -> None: ...
    async def send_binary(self, frame: bytes) -> None: ...
    async def receive_text(self) -> InboundText: ...
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
        self._control_sequence = self._audio_attempt = 0
        self._scheduled_text = {}

    @property
    def sent_json(self):
        return [value for kind, value in self.sent if kind == 'json']

    @property
    def sent_binary(self):
        return [value for kind, value in self.sent if kind == 'binary']

    def feed_json(self, value, *, after_control=None):
        self.feed_text(json.dumps(value, ensure_ascii=False), after_control=after_control)

    def feed_text(self, value, *, after_control=None):
        if after_control is not None:
            self._scheduled_text.setdefault(after_control, []).append(value)
            return
        self._inbound.put_nowait(InboundText(value, self._control_sequence, self._audio_attempt))

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
        self._control_sequence += 1
        if on_dispatched is not None:
            on_dispatched(self._control_sequence)
        for text in self._scheduled_text.pop(message['header']['action'], ()):
            self.feed_text(text)

    def note_audio_attempt(self, number):
        self._audio_attempt = number

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
        self._scheduled_text.clear()
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
