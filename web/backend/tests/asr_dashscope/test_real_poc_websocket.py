"""Exercise the pinned WSS driver against a loopback-only protocol peer."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
import json
import logging
from http import HTTPStatus
import socket
import ssl

import certifi
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest
from websockets.asyncio.server import serve

from classroom_web.asr_dashscope.transport import AuthenticationRejected, Disconnected, InboundText
from classroom_web.asr_dashscope.wire import metering_evidence, observation_fingerprint
from classroom_web.pr06a_real_poc import websocket
from classroom_web.pr06a_real_poc.policy import ConnectFailure, ENDPOINT, PocStopped, UnsafeInbound, digest

FAKE_KEY = "synthetic-pr06a-test-key"
PEER_NAME = "pr06a-loopback.invalid"


@asynccontextmanager
async def _allow():
    yield None


def test_escaped_credential_is_never_returned_as_evidence():
    encoded = "\\u0073" + FAKE_KEY[1:]
    text = '{"duplicate":"nothing","duplicate":{"text":"' + encoded + '"}}'
    assert FAKE_KEY not in text
    with pytest.raises(UnsafeInbound, match="credential_echo") as exc:
        websocket._safe_evidence(InboundText(text, 1, 1), FAKE_KEY)
    assert exc.value.checksum == digest(text.encode("utf-8"))
    assert FAKE_KEY not in str(exc.value)


def test_credential_echo_retains_only_attributable_cost_evidence():
    encoded = "\\u0073" + FAKE_KEY[1:]
    task = "075d00fe-4cde-47dc-b05f-f24b31a94ab7"
    text = ('{"header":{"task_id":"' + task + '"},"payload":{"usage":{"input_tokens":200000,'
            '"output_tokens":5},"output":{"request_id":"approved-request","sentence":"' + encoded + '"}}}')
    with pytest.raises(UnsafeInbound, match="credential_echo") as exc:
        websocket._safe_evidence(InboundText(text, 1, 1), FAKE_KEY)
    assert exc.value.metering.task_id == task
    assert exc.value.metering.request_id == "approved-request"
    assert exc.value.metering.usage.input_tokens == 200000
    assert not hasattr(exc.value, "text")
    secret_request = text.replace("approved-request", encoded)
    with pytest.raises(UnsafeInbound) as exc:
        websocket._safe_evidence(InboundText(secret_request, 1, 1), FAKE_KEY)
    assert exc.value.metering.request_id is None
    assert exc.value.metering.request_fingerprint == digest(FAKE_KEY.encode("utf-8"))
    assert exc.value.metering.usage.input_tokens == 200000


def test_unrelated_duplicate_keys_do_not_discard_meter_evidence():
    task = "075d00fe-4cde-47dc-b05f-f24b31a94ab7"
    raw = ('{"header":{"task_id":"' + task + '"},"payload":{"usage":{"input_tokens":200000,'
           '"output_tokens":1},"output":{"sentence":{"x":0,"x":1},"request_id":"r"}}}')
    assert metering_evidence(raw).usage.input_tokens == 200000
    assert metering_evidence(raw.replace('"request_id":"r"', '"request_id":"r","request_id":"s"')).attribution_conflict
    split = metering_evidence(raw.replace('"output_tokens":1', '"output_tokens":1,"output_tokens":2')).usage
    assert split.input_tokens == 200000 and split.output_tokens == 1
    ambiguous_mirror = raw.replace('"sentence":{"x":0,"x":1}', '"output":{},"output":{}')
    assert metering_evidence(ambiguous_mirror).usage.input_tokens == 200000
    out_of_range = raw.replace('"x":1', '"x":1e999')
    assert metering_evidence(out_of_range).usage.input_tokens == 200000
    huge_integer = raw.replace('"x":1', '"x":' + "9" * 5000)
    assert websocket._safe_evidence(InboundText(huge_integer, 1, 1), FAKE_KEY).text == huge_integer
    assert metering_evidence(huge_integer).usage.input_tokens == 200000
    out_of_range_duplicate = raw.replace('"input_tokens":200000',
                                         '"input_tokens":200000,"input_tokens":9007199254740992')
    assert metering_evidence(out_of_range_duplicate).usage.input_tokens == 200000
    assert metering_evidence(out_of_range_duplicate).metering_conflict
    exponent_meter = raw.replace('"input_tokens":200000', '"input_tokens":2e5')
    assert metering_evidence(exponent_meter).usage.input_tokens == 200000
    assert metering_evidence(exponent_meter).metering_conflict
    long_decimal = raw.replace('"input_tokens":200000', '"input_tokens":200000.' + "0" * 70)
    assert metering_evidence(long_decimal).usage.input_tokens == 200000
    implied_input = raw.replace('"input_tokens":200000,"output_tokens":1',
                                '"total_tokens":180000,"output_tokens":0')
    assert metering_evidence(implied_input).usage.input_tokens == 180000
    mirrored = {"header": {"task_id": task, "event": "task-started"},
                "payload": {"usage": {"total_tokens": 180000}, "output": {"usage": {"output_tokens": 0}}}}
    assert metering_evidence(json.dumps(mirrored)).usage.input_tokens == 180000
    mirrored["payload"]["usage"]["input_tokens"] = None
    inferred = metering_evidence(json.dumps(mirrored))
    assert inferred.usage.input_tokens == 180000 and inferred.metering_conflict
    non_object = raw.replace('{"input_tokens":200000,"output_tokens":1}', '[]')
    assert metering_evidence(non_object).metering_conflict
    parsed = {"header": {"task_id": task, "event": "task-started"},
              "payload": {"usage": {"input_tokens": 100, "total_tokens": 5}}}
    assert metering_evidence(json.dumps(parsed)).metering_conflict
    assert observation_fingerprint(json.dumps(parsed)) == observation_fingerprint(json.dumps(parsed, sort_keys=True, indent=2))
    conflict = {"header": {"task_id": task, "event": "task-started"},
                "payload": {"usage": {"input_tokens": 100}, "output": {"usage": {"input_tokens": 80}}}}
    issue = observation_fingerprint(json.dumps(conflict))
    conflict["header"]["attributes"] = {"harmless": True}
    assert observation_fingerprint(json.dumps(conflict)) == issue
    conflict["payload"]["output"]["usage"]["input_tokens"] = 50
    assert observation_fingerprint(json.dumps(conflict)) != issue
    conflict["header"]["event"] = "synthetic-unknown-event"
    assert observation_fingerprint(json.dumps(conflict)) == observation_fingerprint(json.dumps(conflict, indent=2))


def _loopback_certificate(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PR-06a synthetic peer")])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([
                x509.IPAddress(ip_address("127.0.0.1")), x509.DNSName(PEER_NAME),
            ]), critical=False)
            .sign(key, hashes.SHA256()))
    crt, pem = tmp_path / "synthetic-cert.pem", tmp_path / "synthetic-key.pem"
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ssl.load_cert_chain(crt, pem)
    return crt, server_ssl


@pytest.mark.parametrize("close_mode", ["delayed", "going-away", "abnormal", "timeout", "watchdog"])
def test_pinned_driver_protocol_and_logging(monkeypatch, caplog, tmp_path, close_mode):
    crt, server_ssl = _loopback_certificate(tmp_path)
    monkeypatch.setattr(certifi, "where", lambda: str(crt))
    if close_mode == "timeout":
        monkeypatch.setattr(websocket, "CLOSE_WAIT_SECONDS", 0.1)

    async def exercise():
        seen = []
        sni = []
        dns = []
        dial = websocket._SingleDial
        server_ssl.set_servername_callback(lambda _socket, name, _: sni.append(name))
        loop = asyncio.get_running_loop()
        resolve = loop.getaddrinfo

        async def resolve_peer(host, port, **options):
            if host == PEER_NAME:
                dns.append(host)
                host = "127.0.0.1"
            return await resolve(host, port, **options)

        monkeypatch.setattr(loop, "getaddrinfo", resolve_peer)

        async def peer(socket):
            seen.append(socket.request.headers["Host"])
            seen.append(socket.request.headers["Authorization"])
            seen.append(json.loads(await socket.recv()))
            await socket.send(['{"event":', '"task-started"}'])
            seen.append(await socket.recv())
            seen.append(json.loads(await socket.recv()))
            await socket.send('{"event":"task-finished"}')
            if close_mode == "abnormal":
                await socket.close(1011, FAKE_KEY)
            elif close_mode in ("going-away", "watchdog"):
                await socket.close(1001, "going away")
            else:
                # Hold the actual close reply past the old 250 ms limit.
                socket.transport.pause_reading()
                loop.call_later(1.047 if close_mode == "delayed" else 0.4,
                                socket.transport.resume_reading)
            await socket.wait_closed()

        async with serve(peer, "127.0.0.1", 0, ssl=server_ssl, logger=websocket._wire_log) as server:
            port = server.sockets[0].getsockname()[1]

            def loopback(endpoint, **options):
                assert endpoint == ENDPOINT and options["proxy"] is None
                tls = options["ssl"]
                assert tls.check_hostname and tls.verify_mode == ssl.CERT_REQUIRED
                return dial(f"wss://{PEER_NAME}:{port}/", **options)

            monkeypatch.setattr(websocket, "_SingleDial", loopback)
            transport = websocket._WebSocket(FAKE_KEY)
            transport.claim()
            await transport.connect(before_handshake=_allow)
            sent = []
            await transport.send_json({"action": "run-task"}, on_dispatched=sent.append)
            assert (await transport.receive_text()).control_sequence == 1
            transport.note_audio_attempt(1)
            await transport.send_binary(b"\x00\x00")
            await transport.send_json({"action": "finish-task"}, on_dispatched=sent.append)
            async with asyncio.timeout(2):
                while not transport._socket.stamps:
                    await asyncio.sleep(0)
            transport.note_audio_attempt(2)
            terminal = await transport.receive_text()
            assert (terminal.control_sequence, terminal.audio_attempt) == (2, 1)
            if close_mode in ("abnormal", "going-away", "watchdog"):
                async with asyncio.timeout(2):
                    await transport._socket.wait_closed()
            if close_mode == "watchdog":
                transport.abort()
                transport.snapshot_expired_close()
            else:
                await transport.close()
            close = transport.close_audit
            if close_mode == "abnormal":
                assert (close.status, close.initiator, close.received_code, close.received_reason) == (
                    "abnormal", "server", 1011, "provider_reason_redacted")
            elif close_mode == "going-away":
                assert (close.status, close.initiator, close.received_code, close.received_reason) == (
                    "clean", "server", 1001, "going away")
            elif close_mode == "watchdog":
                assert (close.status, close.elapsed_ms, close.initiator, close.received_code, close.received_reason) == (
                    "timeout", 0, "server", 1001, "going away")
            elif close_mode == "timeout":
                assert (close.status, close.initiator, close.sent_code, close.received_code) == (
                    "timeout", "client", 1000, None)
            else:
                assert (close.status, close.initiator, close.sent_code, close.received_code) == (
                    "clean", "client", 1000, 1000)
                assert 250 < close.elapsed_ms < 3000
            assert FAKE_KEY not in repr(close)
            transport.dispose()
            assert transport._socket is None
            with pytest.raises(Disconnected):
                await transport.connect(before_handshake=_allow)
            refused = websocket._WebSocket(FAKE_KEY)
            refused.claim()

            def stop_before_bearer():
                raise PocStopped("synthetic_revocation_before_bearer")

            with pytest.raises(PocStopped, match="synthetic_revocation_before_bearer"):
                await refused.connect(before_handshake=stop_before_bearer)
            assert refused._socket is None

            expired = websocket._WebSocket(FAKE_KEY)
            expired.claim()

            @asynccontextmanager
            async def stale_commit():
                yield asyncio.get_running_loop().time() - 0.01

            with pytest.raises(PocStopped, match="handshake_deadline"):
                await expired.connect(before_handshake=stale_commit)
            assert expired._socket is None
        assert sent == [1, 2]
        assert dns == sni == [PEER_NAME] * 3
        assert seen == [f"{PEER_NAME}:{port}", f"Bearer {FAKE_KEY}", {"action": "run-task"},
                        b"\0\0", {"action": "finish-task"}]

    with caplog.at_level(logging.DEBUG):
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(exercise())
    assert FAKE_KEY not in caplog.text and "Authorization" not in caplog.text


@pytest.mark.parametrize("status,error,code", [
    (HTTPStatus.UNAUTHORIZED, AuthenticationRejected, "connect_authentication_http_401"),
    (HTTPStatus.FORBIDDEN, AuthenticationRejected, "connect_authentication_http_403"),
    (HTTPStatus.FOUND, Disconnected, "connect_http_upgrade_status_302"),
    (HTTPStatus.BAD_REQUEST, Disconnected, "connect_http_upgrade_status_400"),
])
def test_handshake_rejection_does_not_redirect_or_log(monkeypatch, status, error, code, caplog):
    async def exercise():
        visits = []
        dial = websocket._SingleDial

        def reject(connection, request):
            visits.append(request.path)
            response = connection.respond(status, FAKE_KEY)
            response.headers["Location"] = "/should-never-follow"
            return response

        async with serve(lambda _: None, "127.0.0.1", 0, process_request=reject, logger=websocket._wire_log) as server:
            port = server.sockets[0].getsockname()[1]

            def loopback(endpoint, **options):
                assert endpoint == ENDPOINT
                options.pop("ssl")
                return dial(f"ws://127.0.0.1:{port}/initial", **options)

            monkeypatch.setattr(websocket, "_SingleDial", loopback)
            transport = websocket._WebSocket(FAKE_KEY)
            transport.claim()
            with pytest.raises(error, match=f"^{code}$") as exc:
                await transport.connect(before_handshake=_allow)
            assert FAKE_KEY not in str(exc.value) and visits == ["/initial"]
            assert transport._socket is None
            transport.abort()

    with caplog.at_level(logging.DEBUG):
        asyncio.run(exercise())
    assert FAKE_KEY not in caplog.text


@pytest.mark.parametrize("stage,code", [
    ("local", "connect_local_value_error"),
    ("dns", "connect_dns_gaierror"),
    ("tcp", "connect_tcp_connection_refused"),
])
def test_pre_tls_failure_code_and_socket_cleanup(monkeypatch, stage, code, caplog):
    def reject_local():
        raise ValueError(FAKE_KEY)

    async def exercise():
        opened = []
        loop = asyncio.get_running_loop()

        async def resolve(*_args, **_kwargs):
            assert stage != "local"
            if stage == "dns":
                raise socket.gaierror(socket.EAI_NONAME, FAKE_KEY)
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 1))]

        async def connect_tcp(sock, _address):
            assert stage == "tcp"
            opened.append(sock)
            raise ConnectionRefusedError(FAKE_KEY)

        if stage == "local":
            monkeypatch.setattr(websocket, "_tls_context", reject_local)
        monkeypatch.setattr(loop, "getaddrinfo", resolve)
        monkeypatch.setattr(loop, "sock_connect", connect_tcp)
        transport = websocket._WebSocket(FAKE_KEY)
        transport.claim()
        with pytest.raises(ConnectFailure, match=f"^{code}$"):
            await transport.connect(before_handshake=lambda: pytest.fail("HTTP upgrade gate ran"))
        assert transport._socket is None and transport._key is None
        assert [sock.fileno() for sock in opened] == ([-1] if stage == "tcp" else [])
        transport.dispose()

    with caplog.at_level(logging.DEBUG):
        asyncio.run(exercise())
    assert FAKE_KEY not in caplog.text


def test_tls_certificate_rejection_precedes_http_upgrade_intent(monkeypatch, tmp_path, caplog):
    _, server_ssl = _loopback_certificate(tmp_path)

    async def exercise():
        dial = websocket._SingleDial

        async with serve(lambda _: pytest.fail("HTTP upgrade succeeded"), "127.0.0.1", 0,
                         ssl=server_ssl, logger=websocket._wire_log) as server:
            port = server.sockets[0].getsockname()[1]
            monkeypatch.setattr(websocket, "_SingleDial", lambda _, **opts: dial(f"wss://127.0.0.1:{port}/", **opts))
            transport = websocket._WebSocket(FAKE_KEY)
            transport.claim()
            with pytest.raises(ConnectFailure, match=r"^connect_tls_certificate_\d+$"):
                await transport.connect(before_handshake=lambda: pytest.fail("HTTP upgrade gate ran"))
            assert transport._socket is None
            transport.dispose()

    with caplog.at_level(logging.DEBUG):
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(exercise())
    assert FAKE_KEY not in caplog.text and "Authorization" not in caplog.text
