"""Receiver-only TLS bridge hosted by the independently configured broker.

The reverse proxy exposes this app only at the exact approved HTTPS origin.
No control/maintenance message, session creation or manual completion is routed.
"""
import asyncio,threading,time,hmac
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import copy_context
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse

from ..application.recovery_control import utc
from ..domain.auth import AuthRejected
from ..infrastructure.auth_crypto import digest,random_token
from ..infrastructure.auth_deadlines import DEADLINE,remaining
from .http import strict_json,request_context

COOKIE='__Host-classroom_receiver'


def create_receiver_app(*,broker,origin):
    parsed=urlsplit(origin)
    if parsed.scheme!='https' or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('exact configured HTTPS receiver origin required')
    workers=ThreadPoolExecutor(max_workers=16,thread_name_prefix='receiver');slots=threading.BoundedSemaphore(16)
    sessions={};lock=threading.RLock()

    @asynccontextmanager
    async def lifespan(_):
        try:yield
        finally:
            workers.shutdown(wait=True,cancel_futures=False)
            with lock:sessions.clear()

    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)

    async def work(function):
        if not slots.acquire(False):raise AuthRejected('RATE_LIMITED',429)
        context=copy_context()
        def run():
            try:remaining();return function()
            finally:slots.release()
        try:future=workers.submit(context.run,run)
        except BaseException:slots.release();raise
        async with asyncio.timeout(5):return await asyncio.shield(asyncio.wrap_future(future))

    @app.post('/identity-receiver')
    async def receiver(request:Request):
        waiter=None;token=None;deadline_token=DEADLINE.set(time.monotonic()+5)
        try:
            if (request.url.scheme!='https' or request.headers.get('host')!=parsed.netloc or request.url.query
                    or request.headers.get('origin')!=origin or request.headers.get('sec-fetch-site')!='same-origin'):
                raise AuthRejected('FORBIDDEN',403)
            if request.headers.get('content-type','').split(';')[0].strip()!='application/json' or request.headers.get('content-encoding','identity')!='identity':raise AuthRejected('VALIDATION_FAILED',422)
            raw=bytearray()
            async with asyncio.timeout(1):
                async for part in request.stream():
                    raw.extend(part)
                    if len(raw)>16384:raise AuthRejected('VALIDATION_FAILED',422)
            body=strict_json(bytes(raw))
            if not isinstance(body,dict):raise AuthRejected('VALIDATION_FAILED',422)
            kind=body.get('kind');ctx=request_context(request)
            if kind=='receiver.register.request':
                def register():
                    broker.auth.require_csrf(ctx,'session' if ctx.session else 'preauth')
                    broker.auth.budget('preauth_source',ctx.source,True);broker.auth.budget('preauth_global','deployment',True)
                    return broker.register_receiver(body)
                result=await work(register);token=random_token()
                with lock:
                    for k in list(sessions):
                        if utc()>=sessions[k]['expires_at']:sessions.pop(k)
                    if len(sessions)>=200:raise AuthRejected('RATE_LIMITED',429)
                    sessions[digest('receiver_cookie',token)]=dict(receiver_id=result['receiver_id'],expires_at=utc()+timedelta(minutes=10))
            else:
                # Reject duplicate cookie names before selecting the bound key.
                pieces=[p.strip().split('=',1)[1] for p in request.headers.get('cookie','').split(';') if p.strip().startswith(COOKIE+'=')]
                if len(pieces)!=1:raise AuthRejected('FORBIDDEN',403)
                token=pieces[0]
                csrf=request.headers.get('x-csrf-token')
                if csrf is None or not hmac.compare_digest(csrf,broker.crypto.csrf('receiver',token)):raise AuthRejected('FORBIDDEN',403)
                with lock:session=sessions.get(digest('receiver_cookie',token))
                receiver_auth=body.get('receiver_auth')
                if not session or utc()>=session['expires_at'] or not isinstance(receiver_auth,dict) or receiver_auth.get('receiver_id')!=session['receiver_id']:raise AuthRejected('FORBIDDEN',403)
                if kind=='receiver.await.request':
                    waiter=await work(lambda:broker.begin_wait(body))
                    # This wait owns only a bounded in-memory receiver handle,
                    # never a PG transaction or a worker/hash slot.
                    while not waiter.event.is_set():
                        if utc()>=waiter.expires_at or await request.is_disconnected():raise AuthRejected('FORBIDDEN',403)
                        await asyncio.sleep(.02)
                    DEADLINE.reset(deadline_token);deadline_token=DEADLINE.set(time.monotonic()+5)
                    result=await work(lambda:broker.finish_wait(waiter))
                else:
                    action={'receiver.factor.start.request':'factor_start','receiver.factor.confirm.request':'factor_confirm','receiver.abandon.request':'abandon'}.get(kind) if isinstance(kind,str) else None
                    if action is None:raise AuthRejected('FORBIDDEN',403)
                    result=await work(lambda:broker.receiver(body,action,source=ctx.source))
            response=JSONResponse(result,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Receiver-CSRF':broker.crypto.csrf('receiver',token)})
            if kind=='receiver.register.request':response.set_cookie(COOKIE,token,secure=True,httponly=True,samesite='strict',path='/')
            return response
        except (AuthRejected,TimeoutError) as error:
            code='result_unknown' if isinstance(error,TimeoutError) or getattr(error,'code',None)=='RESULT_UNKNOWN' else 'unavailable' if getattr(error,'status',0)==503 else 'denied'
            return JSONResponse(dict(protocol_version=1,kind='identity_control.error',code=code,retryable=False,correlation_id=str(uuid4())),status_code=getattr(error,'status',503),headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
        finally:
            if waiter:broker.cancel_wait(waiter)
            DEADLINE.reset(deadline_token)

    return app
