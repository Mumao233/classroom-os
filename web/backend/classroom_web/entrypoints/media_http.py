"""Bounded media HTTP routes registered by the normal ASGI factory."""
import asyncio
from pathlib import Path
import tempfile
import threading
import time
import traceback
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..application.media import identifier
from ..domain.auth import AuthRejected
from ..infrastructure.auth_deadlines import DEADLINE


class MediaRequestLifecycle:
    """Hold admission and the ingress budget until the ASGI response finishes."""
    def __init__(self, app, *, slots, failure, budget=60):
        self.app = app
        self.slots = slots
        self.failure = failure
        self.budget = budget

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope.get('path', '').startswith('/api/web/v1/media/'):
            await self.app(scope, receive, send)
            return

        started = False
        request_id = str(uuid4())

        async def redacted_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
                scope['query_string'] = b''
            await send(message)

        entered = self.slots.acquire(False)
        deadline = time.monotonic() + self.budget
        token = DEADLINE.set(deadline)
        try:
            # This task creates and resets the token. StreamingResponse's child tasks
            # inherit its value, while send backpressure shares the same expiry.
            try:
                async with asyncio.timeout_at(deadline):
                    if entered:
                        await self.app(scope, receive, redacted_send)
                    else:
                        response = self.failure(AuthRejected('RATE_LIMITED', 429), request_id)
                        await response(scope, receive, redacted_send)
            except TimeoutError:
                if started:
                    raise
                response = self.failure(AuthRejected('RESULT_UNKNOWN', 503), request_id)
                # A pre-header timeout still gets the existing error DTO, with
                # a separate bounded send grace while admission remains held.
                async with asyncio.timeout(min(1, self.budget)):
                    await response(scope, receive, redacted_send)
        finally:
            scope['query_string'] = b''
            DEADLINE.reset(token)
            if entered:
                self.slots.release()


def register_media(app, *, media, contract, secure, request_context, check, body_for, failure, spool_directory, portable=None):
    spool = Path(spool_directory)
    if not spool.is_absolute() or not spool.is_dir():
        raise ValueError('an explicit existing media spool directory is required')
    slots = threading.BoundedSemaphore(8)
    prefix = '/api/web/v1'
    app.add_middleware(MediaRequestLifecycle, slots=slots, failure=failure)

    async def blocking(function, *args):
        # A client disconnect must not close a file still used by the storage thread.
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Further cancellation must not close an upload's temporary file or
            # release its request slot while the synchronous storage call runs.
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

    def endpoint(path, method, operation):
        async def handle(request: Request):
            request_id = str(uuid4())
            try:
                secure(request, write=method != 'get')
                ctx = request_context(request)
                params = request.path_params
                if method == 'get':
                    pairs = list(request.query_params.multi_items())
                    if len(pairs) != 1 or pairs[0][0] != 'ticket' or len(request.headers.getlist('range')) > 1:
                        raise AuthRejected('VALIDATION_FAILED', 422)
                    token = pairs[0][1]
                    source = media
                    if portable is not None:
                        try:
                            descriptor = await blocking(portable.download, ctx, params['asset_id'], token, request.headers.get('range'))
                            source = portable
                        except AuthRejected as error:
                            if error.code != 'NOT_FOUND':
                                raise
                    if source is media:
                        descriptor = await blocking(media.download, ctx, params['asset_id'], token, request.headers.get('range'))
                    first, last = descriptor['first'], descriptor['last']
                    # Read the first block before sending headers so storage errors remain typed failures.
                    initial_last = min(last, first + media.policy.block_bytes - 1)
                    initial = await blocking(source.read_block, ctx, params['asset_id'], token, first, initial_last)

                    async def chunks():
                        yield initial
                        for offset in range(initial_last + 1, last + 1, media.policy.block_bytes):
                            yield await blocking(source.read_block, ctx, params['asset_id'], token,
                                                 offset, min(last, offset + media.policy.block_bytes - 1))
                    headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                               'Accept-Ranges': 'bytes', 'Content-Length': str(last-first+1),
                               'Content-Disposition': f'attachment; filename="{params["asset_id"]}"'}
                    if descriptor['partial']:
                        headers['Content-Range'] = f'bytes {first}-{last}/{descriptor["length"]}'
                    return StreamingResponse(chunks(), status_code=206 if descriptor['partial'] else 200,
                                             media_type=descriptor['mime'], headers=headers)
                if request.url.query:
                    raise AuthRejected('VALIDATION_FAILED', 422)
                key = request.headers.get('idempotency-key')
                if identifier(key).version != 4:
                    raise AuthRejected('VALIDATION_FAILED', 422)
                extra_headers = {}
                if method == 'put':
                    descriptor = await blocking(media.descriptor, ctx, params['upload_id'])
                    if (request.headers.get('content-type') != 'application/octet-stream'
                            or request.headers.get('content-encoding', 'identity') != 'identity'):
                        raise AuthRejected('VALIDATION_FAILED', 422)
                    declared = request.headers.get('content-length')
                    if declared is not None and (not declared.isascii() or not declared.isdecimal() or int(declared) != descriptor['length']):
                        raise AuthRejected('VALIDATION_FAILED', 422)
                    with tempfile.TemporaryFile(dir=spool) as file:
                        length = 0
                        async with asyncio.timeout(30):
                            async for block in request.stream():
                                length += len(block)
                                if length > min(descriptor['length'], media.policy.max_bytes):
                                    raise AuthRejected('VALIDATION_FAILED', 422)
                                file.write(block)
                        result = await blocking(media.put, ctx, key, params['upload_id'], file)
                else:
                    schema = operation['requestBody']['content']['application/json']['schema']
                    async with asyncio.timeout(2):
                        body = await body_for(request, schema)
                    functions = {'createMediaUpload': (media.create, (ctx, key, body)),
                                 'completeMediaUpload': (media.complete, (ctx, key, params.get('upload_id'), body)),
                                 'issueMediaAccess': (media.access, (ctx, key, params.get('asset_id')))}
                    function, args = functions[operation['operationId']]
                    versions = request.headers.getlist('x-asset-version')
                    if operation['operationId'] == 'createMediaUpload' and portable is not None and body['purpose']=='import_package':
                        result = await blocking(lambda: media.create(ctx,key,body,portable_imports=portable))
                    elif operation['operationId'] == 'issueMediaAccess' and portable is not None:
                        from ..domain.portable import SHA, match
                        if len(versions) > 1 or (versions and not match(SHA, versions[0])):
                            raise AuthRejected('VALIDATION_FAILED', 422)
                        try:
                            ticket = await blocking(lambda: portable.issue_ticket(
                                ctx, params['asset_id'], expected_version=versions[0] if versions else None, key=key))
                        except AuthRejected as error:
                            if error.code != 'NOT_FOUND' or versions:
                                raise
                            result = await blocking(function, *args)
                        else:
                            asset = ticket['asset_id']
                            result = dict(download_path=f'{prefix}/media/{asset}/content?ticket={ticket["ticket"]}',
                                          expires_at=ticket['expires_at'].isoformat(), requires_session=True)
                            extra_headers = {'X-Asset-Version': ticket['asset_version'],
                                             'X-Archive-SHA256': ticket['sha256'],
                                             'X-Archive-Length': str(ticket['byte_length'])}
                    else:
                        result = await blocking(function, *args)
                success = next(k for k in operation['responses'] if k.startswith('2'))
                schema = operation['responses'][success]['content']['application/json']['schema']
                check(schema, result, response=True)
                return JSONResponse(result, status_code=int(success), headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff', **extra_headers})
            except AuthRejected as error:
                response = failure(error, request_id)
                if error.status == 416:
                    # The caller has already passed current media authorization.
                    response.headers['Content-Range'] = f'bytes */{error.length}'
                return response
            except TimeoutError:
                return failure(AuthRejected('RESULT_UNKNOWN', 503), request_id)
            except Exception as error:
                app.state.errors.append({'kind':type(error).__name__, 'stage':'media',
                    'frames':[{'function':frame.name,'line':frame.lineno}
                              for frame in traceback.extract_tb(error.__traceback__)[-5:]]})
                return failure(AuthRejected('STORAGE_UNAVAILABLE', 503), request_id)
        return handle

    for path, operations in contract['paths'].items():
        if path.startswith('/media/'):
            for method, operation in operations.items():
                if method in ('get','post','put'):
                    app.add_api_route(prefix+path, endpoint(path, method, operation), methods=[method.upper()])
