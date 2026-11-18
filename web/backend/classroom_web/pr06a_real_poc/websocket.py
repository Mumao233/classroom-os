"""Single-use WSS client, private to the isolated PR-06a executor."""
import asyncio
from collections import deque
from functools import partial
import json
import logging
import ssl

import websockets
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import InvalidStatus
from websockets.frames import Frame, Opcode

from ..asr_dashscope.transport import AuthenticationRejected, Disconnected, InboundText
from ..asr_dashscope.wire import MAX_MESSAGE_CHARS
from .policy import ENDPOINT, LIB_VERSION, PocStopped

_wire_log = logging.Logger("pr06a.websocket")
_wire_log.addHandler(logging.NullHandler())
_wire_log.propagate = False
_wire_log.setLevel(logging.CRITICAL + 1)


class _SingleDial(connect):
    def process_redirect(self, exc):
        return exc


class _Connection(ClientConnection):
    def __init__(self, protocol, *, before_handshake, **kwargs):
        super().__init__(protocol, **kwargs)
        self._before_handshake = before_handshake
        self.control = self.audio = 0
        self.stamps = deque()

    async def handshake(self, additional_headers=None, user_agent_header=None):
        # websockets calls this after TCP/TLS setup. No HTTP headers have gone
        # out yet. Its connect() aborts this transport if the gate rejects.
        await self._before_handshake()
        await super().handshake(additional_headers, user_agent_header)

    def process_event(self, event):
        if isinstance(event, Frame) and event.opcode in (Opcode.TEXT, Opcode.BINARY):
            self.stamps.append((self.control, self.audio))
        super().process_event(event)

    async def stamped_text(self):
        message = await self.recv()
        control, audio = self.stamps.popleft()
        if not isinstance(message, str):
            raise Disconnected("expected_text_frame")
        return InboundText(message, control, audio)

    async def send_control(self, message, on_dispatched):
        # In the pinned library send_context writes the queued data before its
        # first suspension. Stamp the dispatch in the same event-loop turn.
        async with self.send_context():
            self.protocol.send_text(message.encode("utf-8"))
            self.control += 1
            if on_dispatched is not None:
                on_dispatched(self.control)


class _WebSocket:
    offline = False

    def __init__(self, api_key):
        self._key = api_key
        self._redaction_key = api_key
        self._socket = None
        self._claimed = False
        self._started = False
        self._closed = False

    def claim(self):
        if self._claimed or self._closed:
            raise Disconnected("transport_owned")
        self._claimed = True

    async def connect(self, *, before_handshake):
        if not self._claimed or self._started or self._closed:
            raise Disconnected("transport_used")
        self._started = True
        if websockets.__version__ != LIB_VERSION:
            raise Disconnected("websocket_version_mismatch")
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        tls.load_default_certs()
        headers = {"Authorization": f"Bearer {self._key}"}
        self._key = None
        try:
            self._socket = await _SingleDial(
                ENDPOINT, additional_headers=headers, ssl=tls, proxy=None,
                open_timeout=3, close_timeout=0.25, ping_interval=None,
                max_size=MAX_MESSAGE_CHARS, max_queue=8, compression=None,
                create_connection=partial(_Connection, before_handshake=before_handshake),
                logger=_wire_log, user_agent_header=None,
            )
            if self._closed:
                self.abort()
                raise Disconnected("handshake_after_close")
        except PocStopped:
            raise
        except InvalidStatus as exc:
            if exc.response.status_code in (401, 403):
                raise AuthenticationRejected("authentication_rejected") from None
            raise Disconnected("handshake_rejected") from None
        except Exception:
            raise Disconnected("handshake_failed") from None

    def _connected(self):
        if self._socket is None:
            raise Disconnected("socket_unavailable")
        return self._socket

    async def send_json(self, message, *, on_dispatched=None):
        try:
            await self._connected().send_control(json.dumps(message, separators=(",", ":")), on_dispatched)
        except Exception:
            raise Disconnected("control_write_failed") from None

    def note_audio_attempt(self, number):
        self._connected().audio = number

    async def send_binary(self, frame):
        try:
            await self._connected().send(frame)
        except Exception:
            raise Disconnected("audio_write_failed") from None

    async def receive_text(self):
        try:
            frame = await self._connected().stamped_text()
            text = frame.text.replace(self._redaction_key, "[redacted]")
            return InboundText(text, frame.control_sequence, frame.audio_attempt)
        except Exception:
            raise Disconnected("read_failed") from None

    async def close(self):
        if self._socket is not None:
            try:
                async with asyncio.timeout(0.25):
                    await self._socket.close()
                    if self._socket.close_code != 1000:
                        raise Disconnected("abnormal_close")
            except Exception:
                raise Disconnected("close_failed") from None
            finally:
                self.abort()
        else:
            self.abort()

    def abort(self):
        self._closed = True
        self._key = self._redaction_key = None
        if self._socket is not None:
            socket, self._socket = self._socket, None
            socket.transport.abort()
