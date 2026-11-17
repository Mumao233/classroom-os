"""Bounded normal HTTP entrypoints for the frozen PR-05b capture contract."""
import asyncio
import base64
import hashlib
import threading
import time
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse

from ..application.media import identifier
from ..domain.auth import AuthRejected
from ..domain.capture import MAX_CHUNK_BYTES
from ..infrastructure.auth_deadlines import DEADLINE


def register_capture(app, *, capture, contract, secure, request_context, check, failure):
    from .http import strict_json
    slots = threading.BoundedSemaphore(8)

    async def blocking(function, *args):
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()
            raise

    def endpoint(method, operation):
        async def handle(request: Request):
            request_id = str(uuid4())
            entered = slots.acquire(False)
            token = DEADLINE.set(time.monotonic() + 30)
            try:
                if not entered:
                    raise AuthRejected('RATE_LIMITED', 429)
                secure(request, write=method != 'get')
                ctx = request_context(request)
                params = request.path_params
                name = operation['operationId']
                if method == 'get':
                    pairs = list(request.query_params.multi_items())
                    if len(pairs) != 1 or pairs[0][0] != 'capture_epoch_id':
                        raise AuthRejected('VALIDATION_FAILED', 422)
                    identifier(pairs[0][1])
                    result = await blocking(capture.manifest, ctx, params['class_session_id'], pairs[0][1])
                else:
                    if request.url.query:
                        raise AuthRejected('VALIDATION_FAILED', 422)
                    key = request.headers.get('idempotency-key')
                    if identifier(key).version != 4:
                        raise AuthRejected('VALIDATION_FAILED', 422)
                    if request.headers.get('content-encoding', 'identity') != 'identity':
                        raise AuthRejected('VALIDATION_FAILED', 422)
                    limit = MAX_CHUNK_BYTES if method == 'put' else 256_000
                    declared = request.headers.get('content-length')
                    if declared is not None and (not declared.isascii() or not declared.isdecimal() or int(declared) > limit):
                        raise AuthRejected('VALIDATION_FAILED', 422)
                    data = bytearray()
                    async with asyncio.timeout(10):
                        async for block in request.stream():
                            data.extend(block)
                            if len(data) > limit:
                                raise AuthRejected('VALIDATION_FAILED', 422)
                    if method == 'put':
                        if request.headers.get('content-type') != 'application/octet-stream':
                            raise AuthRejected('VALIDATION_FAILED', 422)
                        digest = base64.b64encode(hashlib.sha256(data).digest()).decode('ascii')
                        if request.headers.get('content-digest') != f'sha-256=:{digest}:':
                            raise AuthRejected('VALIDATION_FAILED', 422)
                        result = await blocking(capture.put, ctx, params['chunk_id'], key, bytes(data))
                    else:
                        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
                            raise AuthRejected('VALIDATION_FAILED', 422)
                        body = strict_json(bytes(data))
                        check(operation['requestBody']['content']['application/json']['schema'], body)
                        classroom = params['class_session_id']
                        if name == 'acquireCapture':
                            result = await blocking(capture.acquire, ctx, classroom, key, body)
                        elif name == 'authorizeRecovery':
                            result = await blocking(capture.recovery, ctx, classroom, key, body)
                        elif name == 'prepareAudioChunk':
                            result = await blocking(capture.prepare, ctx, classroom, key, body, request.headers.get('x-recovery-upload-token'))
                        else:
                            result = await blocking(capture.command, ctx, classroom, key, body, name)
                success = next(value for value in operation['responses'] if value.startswith('2'))
                check(operation['responses'][success]['content']['application/json']['schema'], result, response=True)
                return JSONResponse(result, status_code=int(success), headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
            except AuthRejected as error:
                return failure(error, request_id)
            except TimeoutError:
                return failure(AuthRejected('RESULT_UNKNOWN', 503), request_id)
            except Exception as error:
                import traceback
                app.state.errors.append({'kind':type(error).__name__, 'stage':'capture',
                    'frames':[{'function':frame.name,'line':frame.lineno} for frame in traceback.extract_tb(error.__traceback__)[-5:]]})
                return failure(AuthRejected('STORAGE_UNAVAILABLE', 503), request_id)
            finally:
                DEADLINE.reset(token)
                if entered:
                    slots.release()
        return handle

    for path, methods in contract['paths'].items():
        if '/capture/' in path or '/audio-chunks' in path or path.endswith('/audio-manifest'):
            for method, operation in methods.items():
                if method in ('get', 'post', 'put'):
                    app.add_api_route('/api/web/v1' + path, endpoint(method, operation), methods=[method.upper()])
