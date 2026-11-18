"""Exercise the pinned WSS driver against a loopback-only protocol peer."""
import asyncio
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

from classroom_web.asr_dashscope.transport import AuthenticationRejected, Disconnected
from classroom_web.pr06a_real_poc import websocket
from classroom_web.pr06a_real_poc.policy import ENDPOINT, PocStopped

FAKE_KEY = "synthetic-pr06a-test-key"


async def _allow():
    pass


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
            transport.abort()
            assert transport._socket is None
            with pytest.raises(Disconnected):
                await transport.connect(before_handshake=_allow)
            refused = websocket._WebSocket(FAKE_KEY)
            refused.claim()

            async def stop_before_bearer():
                raise PocStopped("synthetic_revocation_before_bearer")

            with pytest.raises(PocStopped, match="synthetic_revocation_before_bearer"):
                await refused.connect(before_handshake=stop_before_bearer)
            assert refused._socket is None
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
