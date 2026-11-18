"""Exercise the pinned WSS driver against a loopback-only protocol peer."""
import asyncio
import json
import logging
from http import HTTPStatus
import ssl

import pytest
from websockets.asyncio.server import serve

from classroom_web.asr_dashscope.transport import AuthenticationRejected, Disconnected
from classroom_web.pr06a_real_poc import websocket
from classroom_web.pr06a_real_poc.policy import ENDPOINT

FAKE_KEY = "synthetic-pr06a-test-key"


def test_pinned_driver_protocol_and_logging(monkeypatch, caplog):
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
            await socket.wait_closed()

        async with serve(peer, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            def loopback(endpoint, **options):
                assert endpoint == ENDPOINT and options["proxy"] is None
                tls = options.pop("ssl")
                assert tls.check_hostname and tls.verify_mode == ssl.CERT_REQUIRED
                return dial(f"ws://127.0.0.1:{port}/", **options)

            monkeypatch.setattr(websocket, "_SingleDial", loopback)
            transport = websocket._WebSocket(FAKE_KEY)
            transport.claim()
            await transport.connect()
            sent = []
            await transport.send_json({"action": "run-task"}, on_dispatched=sent.append)
            assert (await transport.receive_text()).control_sequence == 1
            transport.note_audio_attempt(1)
            await transport.send_binary(b"\x00\x00")
            await transport.send_json({"action": "finish-task"}, on_dispatched=sent.append)
            terminal = await transport.receive_text()
            assert (terminal.control_sequence, terminal.audio_attempt) == (2, 1)
            await transport.close()
            transport.abort()
            assert transport._socket is None
            with pytest.raises(Disconnected):
                await transport.connect()
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

        async with serve(lambda _: None, "127.0.0.1", 0, process_request=reject) as server:
            port = server.sockets[0].getsockname()[1]

            def loopback(endpoint, **options):
                assert endpoint == ENDPOINT
                options.pop("ssl")
                return dial(f"ws://127.0.0.1:{port}/initial", **options)

            monkeypatch.setattr(websocket, "_SingleDial", loopback)
            transport = websocket._WebSocket(FAKE_KEY)
            transport.claim()
            with pytest.raises(error) as exc:
                await transport.connect()
            assert FAKE_KEY not in str(exc.value) and visits == ["/initial"]
            assert transport._socket is None
            transport.abort()

    with caplog.at_level(logging.DEBUG):
        asyncio.run(exercise())
    assert FAKE_KEY not in caplog.text
