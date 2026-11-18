"""Single-use WSS client, private to the isolated PR-06a executor."""
import asyncio
from collections import deque
from dataclasses import replace
from functools import partial
import json
import logging
import socket
import ssl

import websockets
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosedOK, InvalidStatus
from websockets.frames import Frame, Opcode

from ..asr_dashscope.transport import AuthenticationRejected, Disconnected, InboundText
from ..asr_dashscope.wire import MAX_MESSAGE_CHARS, metering_evidence, parse_event
from ..asr_sim.simulator import ProtocolError
from .policy import ENDPOINT, LIB_VERSION, EndOfStream, PocStopped, UnsafeInbound, digest, sentence_proof

_wire_log = logging.Logger("pr06a.websocket")
_wire_log.addHandler(logging.NullHandler())
_wire_log.propagate = False
_wire_log.setLevel(logging.CRITICAL + 1)


def _credential_echo(text, key, control):
    meter = metering_evidence(text)
    if meter is not None:
        if key in meter.task_id:
            meter = None
        elif meter.request_id and key in meter.request_id:
            meter = replace(meter, request_id=None, request_fingerprint=digest(meter.request_id.encode("utf-8")))
    event = final = None
    if meter is not None and not meter.attribution_conflict:
        try:
            observed = parse_event(text, meter.task_id)
        except ProtocolError:
            pass
        else:
            event = observed.kind
            if observed.sentence is not None and observed.sentence.final:
                final = sentence_proof(observed.sentence)
    return UnsafeInbound("credential_echo", digest(text.encode("utf-8")), meter,
                         control=control, event=event, final_sentence=final)


def _ignore_number(_value):
    return None


def _safe_evidence(frame, key):
    text = frame.text
    if key in text:
        raise _credential_echo(text, key, frame.control_sequence)
    try:
        # Keep duplicate keys intact in the original wire text for parse_event.
        # Inspect values AND keys as decoded strings before retaining that text.
        pending = [json.loads(text, object_pairs_hook=list, parse_int=_ignore_number,
                              parse_float=_ignore_number, parse_constant=_ignore_number)]
        while pending:
            value = pending.pop()
            if isinstance(value, (list, tuple)):
                pending.extend(value)
            elif isinstance(value, str) and key in value:
                raise _credential_echo(text, key, frame.control_sequence)
    except (ValueError, RecursionError):
        raise UnsafeInbound("undecodable_provider_response", digest(text.encode("utf-8"))) from None
    return frame


class _SingleDial(connect):
    async def open_tcp_connection(self):
        # Resolve once and select one numeric address. The pinned client
        # preserves ws_uri.host for TLS SNI, certificate checks and HTTP Host.
        answers = await asyncio.get_running_loop().getaddrinfo(
            self.ws_uri.host, self.ws_uri.port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        family, _, _, _, address = answers[0]
        self.create_connection_kwargs.update(host=address[0], family=family, flags=socket.AI_NUMERICHOST)
        return await super().open_tcp_connection()

    def process_redirect(self, exc):
        return exc


class _Connection(ClientConnection):
    def __init__(self, protocol, *, before_handshake, **kwargs):
        super().__init__(protocol, **kwargs)
        self._before_handshake = before_handshake
        self.control = self.audio = self.buffered_messages = 0
        self.stamps = deque()

    async def handshake(self, additional_headers=None, user_agent_header=None):
        # websockets calls this after TCP/TLS setup. No HTTP headers have gone
        # out yet. Its connect() aborts this transport if the gate rejects.
        async with self._before_handshake() as deadline:
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                raise PocStopped("handshake_deadline")
            await super().handshake(additional_headers, user_agent_header)

    def process_event(self, event):
        if isinstance(event, Frame) and event.opcode in (Opcode.TEXT, Opcode.BINARY):
            self.stamps.append((self.control, self.audio, event.opcode == Opcode.TEXT))
        if isinstance(event, Frame) and event.fin and event.opcode in (Opcode.TEXT, Opcode.BINARY, Opcode.CONT):
            self.buffered_messages += 1
        super().process_event(event)

    async def stamped_text(self):
        message = await self.recv(decode=False)
        control, audio, text_frame = self.stamps.popleft()
        self.buffered_messages -= 1
        try:
            text = message.decode("utf-8")
        except UnicodeDecodeError:
            raise UnsafeInbound("invalid_provider_utf8", digest(message)) from None
        return InboundText(text, control, audio), text_frame

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
                self.dispose()
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

    def has_pending(self):
        return self._socket is not None and bool(self._socket.stamps)

    def has_buffered(self):
        return self._socket is not None and self._socket.buffered_messages > 0

    async def receive_text(self):
        try:
            frame, text_frame = await self._connected().stamped_text()
            if text_frame:
                return _safe_evidence(frame, self._redaction_key)
            try:
                _safe_evidence(frame, self._redaction_key)
            except UnsafeInbound as exc:
                meter = exc.metering
            else:
                meter = metering_evidence(frame.text)
            raise UnsafeInbound("binary_provider_response", digest(frame.text.encode("utf-8")), meter)
        except ConnectionClosedOK:
            raise EndOfStream("peer_closed") from None
        except UnsafeInbound:
            raise
        except Exception:
            raise Disconnected("read_failed") from None

    async def close(self):
        if self._socket is not None:
            self._closed = True
            try:
                async with asyncio.timeout(0.25):
                    await self._socket.close()
                    if self._socket.close_code != 1000:
                        raise Disconnected("abnormal_close")
            except BaseException as exc:
                self.abort()
                if not isinstance(exc, Exception):
                    raise
                raise Disconnected("close_failed") from None
        else:
            self.abort()

    def abort(self):
        self._closed = True
        self._key = None
        if self._socket is not None:
            # The assembler can still yield complete frames buffered before
            # connection_lost. Keep it and the redaction key until accounting joins.
            self._socket.transport.abort()

    def dispose(self):
        self.abort()
        self._socket = self._redaction_key = None
