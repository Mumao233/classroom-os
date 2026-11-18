"""Exercise the pinned WSS driver against a loopback-only protocol peer."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
import json
import logging
from http import HTTPStatus
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest
from websockets.asyncio.server import serve

from classroom_web.asr_dashscope.transport import AuthenticationRejected, Disconnected, InboundText
from classroom_web.asr_dashscope.wire import metering_evidence, observation_fingerprint
from classroom_web.pr06a_real_poc import websocket
from classroom_web.pr06a_real_poc.policy import ENDPOINT, PocStopped, UnsafeInbound, digest

FAKE_KEY = "synthetic-pr06a-test-key"


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
    parsed = {"header": {"task_id": task}, "payload": {"usage": {"input_tokens": 100, "total_tokens": 5}}}
    assert metering_evidence(json.dumps(parsed)).metering_conflict
    assert observation_fingerprint(json.dumps(parsed)) == observation_fingerprint(json.dumps(parsed, sort_keys=True, indent=2))


def _loopback_certificate(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PR-06a synthetic peer")])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    crt, pem = tmp_path / "synthetic-cert.pem", tmp_path / "synthetic-key.pem"
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ssl.load_cert_chain(crt, pem)
    return crt, server_ssl


@pytest.mark.parametrize("abnormal", [False, True])
def test_pinned_driver_protocol_and_logging(monkeypatch, caplog, tmp_path, abnormal):
    crt, server_ssl = _loopback_certificate(tmp_path)
    async def exercise():
        seen = []
        dial = websocket._SingleDial

        async def peer(socket):
            seen.append(socket.request.headers["Authorization"])
            seen.append(json.loads(await socket.recv()))
            await socket.send(['{"event":', '"task-started"}'])
            seen.append(await socket.recv())
            seen.append(json.loads(await socket.recv()))
            await socket.send('{"event":"task-finished"}')
            if abnormal:
                await socket.close(1011, FAKE_KEY)
            await socket.wait_closed()

        async with serve(peer, "127.0.0.1", 0, ssl=server_ssl, logger=websocket._wire_log) as server:
            port = server.sockets[0].getsockname()[1]

            def loopback(endpoint, **options):
                assert endpoint == ENDPOINT and options["proxy"] is None
                tls = options["ssl"]
                assert tls.check_hostname and tls.verify_mode == ssl.CERT_REQUIRED
                tls.load_verify_locations(cafile=str(crt))
                return dial(f"wss://127.0.0.1:{port}/", **options)

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
            if abnormal:
                async with asyncio.timeout(2):
                    await transport._socket.wait_closed()
                with pytest.raises(Disconnected, match="close_failed"):
                    await transport.close()
            else:
                await transport.close()
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
        assert seen == [f"Bearer {FAKE_KEY}", {"action": "run-task"}, b"\0\0", {"action": "finish-task"}]

    with caplog.at_level(logging.DEBUG):
        asyncio.run(exercise())
    assert FAKE_KEY not in caplog.text and "Authorization" not in caplog.text


@pytest.mark.parametrize("status,error", [(HTTPStatus.UNAUTHORIZED, AuthenticationRejected),
                                          (HTTPStatus.FORBIDDEN, AuthenticationRejected),
                                          (HTTPStatus.FOUND, Disconnected)])
def test_handshake_rejection_does_not_redirect_or_log(monkeypatch, status, error, caplog):
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
            with pytest.raises(error) as exc:
                await transport.connect(before_handshake=_allow)
            assert FAKE_KEY not in str(exc.value) and visits == ["/initial"]
            assert transport._socket is None
            transport.abort()

    with caplog.at_level(logging.DEBUG):
        asyncio.run(exercise())
    assert FAKE_KEY not in caplog.text
