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
from websockets.exceptions import ConnectionClosedOK, InvalidHandshake, InvalidStatus, InvalidURI
from websockets.frames import Frame, Opcode

from ..asr_dashscope.transport import Disconnected, InboundText
from ..asr_dashscope.wire import MAX_MESSAGE_CHARS, metering_evidence, observation_fingerprint, parse_event
from ..asr_sim.simulator import ProtocolError
from .policy import (
    CLOSE_WAIT_SECONDS, ENDPOINT, LIB_VERSION, CloseAudit, ConnectAuthenticationFailure, ConnectFailure, EndOfStream, PocStopped,
    UnsafeInbound, digest, sentence_proof,
)

_wire_log = logging.Logger("pr06a.websocket")
_wire_log.addHandler(logging.NullHandler())
_wire_log.propagate = False
_wire_log.setLevel(logging.CRITICAL + 1)
_CLOSE_REASONS = frozenset(("", "normal closure", "going away", "task finished", "task-finished",
                            "connection closed", "timeout"))


def _safe_close_reason(frame):
    if frame is None:
        return None
    reason = frame.reason.strip().casefold()
    return reason if reason in _CLOSE_REASONS else "provider_reason_redacted"


def _tls_context():
    import certifi

    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tls.load_default_certs()
    tls.load_verify_locations(cafile=certifi.where())
    return tls


def _dial_failure(phase, exc):
    if isinstance(exc, InvalidStatus):
        status = exc.response.status_code
        if type(status) is int and status in (401, 403):
            return ConnectAuthenticationFailure(f"connect_authentication_http_{status}")
        if type(status) is int and 100 <= status <= 599:
            return ConnectFailure(f"connect_http_upgrade_status_{status}")
        return ConnectFailure("connect_http_upgrade_invalid_status")
    if phase != "local" and isinstance(exc, ssl.SSLCertVerificationError):
        code = getattr(exc, "verify_code", None)
        if type(code) is int and 0 <= code <= 999:
            return ConnectFailure(f"connect_tls_certificate_{code}")
        return ConnectFailure("connect_tls_certificate")
    if isinstance(exc, (TypeError, ValueError, ImportError, InvalidURI)):
        phase = "local"
    for kind, error in (
        ("gaierror", socket.gaierror), ("timeout", TimeoutError), ("ssl_error", ssl.SSLError),
        ("invalid_handshake", InvalidHandshake), ("connection_refused", ConnectionRefusedError),
        ("os_error", OSError), ("type_error", TypeError), ("value_error", ValueError),
        ("import_error", ImportError), ("invalid_uri", InvalidURI),
    ):
        if isinstance(exc, error):
            return ConnectFailure(f"connect_{phase}_{kind}")
    return ConnectFailure(f"connect_{phase}_error")


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
                         observation=observation_fingerprint(text), control=control,
                         event=event, final_sentence=final)


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
    _phase = "local"
    _opened_connection = None

    @property
    def phase(self):
        if self._opened_connection is not None:
            return "http_upgrade" if self._opened_connection.upgrade_started else "local"
        return self._phase

    async def open_tcp_connection(self):
        # Complete one numeric TCP dial before transferring the socket to the
        # pinned client's TLS setup. That client keeps ws_uri.host for SNI,
        # certificate verification and the HTTP Host header.
        loop = asyncio.get_running_loop()
        self._phase = "dns"
        answers = await loop.getaddrinfo(
            self.ws_uri.host, self.ws_uri.port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        if not answers:
            raise ConnectFailure("connect_dns_no_address")
        self._phase = "local"
        family, kind, protocol, _, address = answers[0]
        sock = socket.socket(family, kind, protocol)
        try:
            sock.setblocking(False)
            self._phase = "tcp"
            await loop.sock_connect(sock, address)
            self._phase = "tls" if self.ws_uri.secure else "local"
            self.create_connection_kwargs["sock"] = sock
            connection = await super().open_tcp_connection()
        except BaseException:
            sock.close()
            raise
        finally:
            self.create_connection_kwargs.pop("sock", None)
        self._opened_connection = connection
        return connection

    def process_redirect(self, exc):
        return exc


class _Connection(ClientConnection):
    def __init__(self, protocol, *, before_handshake, **kwargs):
        super().__init__(protocol, **kwargs)
        self._before_handshake = before_handshake
        self.upgrade_started = False
        self.control = self.audio = self.buffered_messages = 0
        self.stamps = deque()

    async def handshake(self, additional_headers=None, user_agent_header=None):
        # websockets calls this after TCP/TLS setup. No HTTP headers have gone
        # out yet. Its connect() aborts this transport if the gate rejects.
        async with self._before_handshake() as deadline:
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                raise PocStopped("handshake_deadline")
            self.upgrade_started = True
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
        self.close_audit = None

    def claim(self):
        if self._claimed or self._closed:
            raise Disconnected("transport_owned")
        self._claimed = True

    async def connect(self, *, before_handshake):
        if not self._claimed or self._started or self._closed:
            raise ConnectFailure("connect_local_transport_used")
        self._started = True
        dial = None
        try:
            if websockets.__version__ != LIB_VERSION:
                raise ConnectFailure("connect_local_version_mismatch")
            tls = _tls_context()
            dial = _SingleDial(
                ENDPOINT, additional_headers={"Authorization": f"Bearer {self._key}"}, ssl=tls, proxy=None,
                open_timeout=3, close_timeout=None, ping_interval=None,
                max_size=MAX_MESSAGE_CHARS, max_queue=8, compression=None,
                create_connection=partial(_Connection, before_handshake=before_handshake),
                logger=_wire_log, user_agent_header=None,
            )
            self._key = None
            self._socket = await dial
            if self._closed:
                self.dispose()
                raise ConnectFailure("connect_http_upgrade_transport_closed")
        except (PocStopped, ConnectFailure):
            raise
        except Exception as exc:
            raise _dial_failure(dial.phase if dial is not None else "local", exc) from None
        finally:
            self._key = None

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
            raise UnsafeInbound("binary_provider_response", digest(frame.text.encode("utf-8")), meter,
                                observation=observation_fingerprint(frame.text))
        except ConnectionClosedOK:
            raise EndOfStream("peer_closed") from None
        except UnsafeInbound:
            raise
        except Exception:
            raise Disconnected("read_failed") from None

    async def close(self):
        socket = self._socket
        if socket is None:
            self.abort()
            return
        self._closed = True
        loop = asyncio.get_running_loop()
        began = loop.time()
        status = "failed"
        try:
            # Own the timeout here so a timer expiry remains distinct from a
            # peer close code or a library abort. The runner also fences the socket.
            async with asyncio.timeout(CLOSE_WAIT_SECONDS):
                await socket.close()
                await socket.wait_closed()
            status = "clean" if isinstance(socket.protocol.close_exc, ConnectionClosedOK) else "abnormal"
        except TimeoutError:
            status = "timeout"
        except asyncio.CancelledError:
            status = "timeout"
            raise
        except Exception:
            pass
        finally:
            if status != "clean":
                self.abort()
            self._snapshot_close(status, round((loop.time() - began) * 1000))

    def snapshot_expired_close(self):
        if self._socket is not None and self.close_audit is None:
            # No orderly close was started before the connection deadline.
            self._snapshot_close("timeout", 0)

    def _snapshot_close(self, status, elapsed_ms):
        protocol = self._socket.protocol
        received, sent = protocol.close_rcvd, protocol.close_sent
        if received is not None and sent is not None:
            initiator = "server" if protocol.close_rcvd_then_sent else "client"
        else:
            initiator = "server" if received is not None else "client" if sent is not None else None
        self.close_audit = CloseAudit(status, elapsed_ms,
                                      int(received.code) if received is not None else None, _safe_close_reason(received),
                                      int(sent.code) if sent is not None else None, _safe_close_reason(sent), initiator)

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
