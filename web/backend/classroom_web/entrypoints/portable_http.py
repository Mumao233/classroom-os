"""Explicitly enabled multipart endpoints for the tbd06-v2 candidate."""
import asyncio
from functools import partial
import threading
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse

from ..application.portable_transfers import operation_key
from ..domain.auth import AuthRejected
from ..domain.portable import PORTABLE_V21
from ..infrastructure.auth_deadlines import DEADLINE

PREFIX = '/api/web/v1/imports/uploads'
SHA = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}


def integer_header(value, maximum):
    if not value or not value.isascii() or not value.isdecimal() or len(value) > 10:
        raise AuthRejected('VALIDATION_FAILED', 422)
    result = int(value)
    if result > maximum:
        raise AuthRejected('CAPACITY_EXCEEDED', 413)
    return result


def upload_dto(upload, parts):
    size = PORTABLE_V21.part_bytes
    return dict(upload_id=str(upload['id']), policy_version=upload['policy_version'],
                byte_length=upload['reserved_bytes'], archive_sha256=upload['archive_sha256'],
                part_bytes=size, part_count=(upload['reserved_bytes'] + size - 1)//size,
                expires_at=upload['access_until'].isoformat(), state=upload['state'],
                parts=[dict(part_number=row['part_number'], offset=row['part_number']*size,
                            byte_length=row['byte_length'], sha256=row['sha256']) for row in parts])


def register_portable(app, *, service, secure, request_context, body_for, failure):
    slots = threading.BoundedSemaphore(8)

    def endpoint(operation, status):
        async def handle(request: Request):
            trace_id = str(uuid4())
            if not slots.acquire(blocking=False):
                return failure(AuthRejected('RATE_LIMITED', 429), trace_id)
            token = DEADLINE.set(asyncio.get_running_loop().time() + 120)
            try:
                secure(request, write=operation != 'status')
                if request.url.query:
                    raise AuthRejected('VALIDATION_FAILED', 422)
                ctx = request_context(request)
                upload = request.path_params.get('upload_id')
                key = operation_key(request.headers.get('idempotency-key')) if operation != 'status' else None
                if operation == 'put':
                    if (request.headers.get('content-type') != 'application/octet-stream'
                            or request.headers.get('content-encoding', 'identity') != 'identity'):
                        raise AuthRejected('VALIDATION_FAILED', 422)
                    length = integer_header(request.headers.get('content-length'), PORTABLE_V21.part_bytes)
                    offset = integer_header(request.headers.get('x-part-offset'), PORTABLE_V21.archive_bytes)
                    number = integer_header(request.path_params['part_number'], 31)
                    digest = request.headers.get('x-part-sha256')
                    await blocking(service.authorize_part, ctx, upload, number, offset, length, digest)
                    with service._spool() as spool:
                        read_length = 0
                        async with asyncio.timeout(30):
                            async for chunk in request.stream():
                                read_length += len(chunk)
                                if read_length > length:
                                    raise AuthRejected('VALIDATION_FAILED', 422)
                                spool.write(chunk)
                        if read_length != length:
                            raise AuthRejected('VALIDATION_FAILED', 422)
                        spool.seek(0)
                        result = await blocking(service.put_part, ctx, key, upload, number, offset, length, digest, spool)
                elif operation == 'status':
                    result = upload_dto(*await blocking(service.upload_status, ctx, upload))
                else:
                    properties = {
                        'begin': dict(byte_length={'type':'integer','minimum':1},
                                      archive_sha256=SHA, mime_type={'enum':['application/zip','application/json']}),
                        'seal': dict(archive_sha256=SHA),
                        'cancel': {},
                    }[operation]
                    async with asyncio.timeout(2):
                        body = await body_for(request, dict(type='object', properties=properties,
                                                required=list(properties), additionalProperties=False))
                    if operation == 'begin':
                        row = await blocking(service.begin_upload, ctx, key, body['byte_length'],
                                             body['archive_sha256'], body['mime_type'])
                        result = upload_dto(*await blocking(service.upload_status, ctx, row['id']))
                    elif operation == 'seal':
                        result = await blocking(service.seal, ctx, key, upload, body['archive_sha256'])
                        row, _ = await blocking(service.upload_status, ctx, upload)
                        result = result | {'expires_at': row['access_until'].isoformat()}
                    else:
                        row = await blocking(partial(service.cancel, key=key), ctx, upload)
                        result = dict(upload_id=str(row['id']), state='cancelled', expires_at=row['access_until'].isoformat())
                return JSONResponse(result, status_code=status, headers={
                    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
            except AuthRejected as error:
                return failure(error, trace_id)
            except TimeoutError:
                return failure(AuthRejected('RESULT_UNKNOWN', 503), trace_id)
            finally:
                DEADLINE.reset(token)
                slots.release()
        return handle

    async def blocking(function, *args):
        # Hold the spool and slot until an already-started storage call really exits.
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

    for path, method, name, status in (
        ('', 'POST', 'begin', 201), ('/{upload_id}', 'GET', 'status', 200),
        ('/{upload_id}/parts/{part_number}', 'PUT', 'put', 200),
        ('/{upload_id}/seal', 'POST', 'seal', 202), ('/{upload_id}/cancel', 'POST', 'cancel', 200),
    ):
        app.add_api_route(PREFIX+path, endpoint(name, status), methods=[method])
