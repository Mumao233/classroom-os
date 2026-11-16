"""Explicit ASGI factory; importing it never starts a service or reads secrets."""
import asyncio,json,re,time,threading
from functools import partial
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from contextlib import asynccontextmanager
from collections import deque
import traceback
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from http.cookies import SimpleCookie,CookieError
from urllib.parse import urlsplit
from uuid import UUID,uuid4

from fastapi import FastAPI,Request,WebSocket
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect
from jsonschema import Draft202012Validator,FormatChecker

from ..domain.auth import AuthRejected,AuthReply,BrowserContext
from ..domain.identity import IdentityRejected
from ..infrastructure.auth_deadlines import DEADLINE,remaining

PREFIX='/api/web/v1'
# Opaque to clients: rank, creation time in microseconds and row id of the last item served.
PAGE_CURSOR=re.compile(r'[012]\.[0-9]{1,18}\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
COOKIES={'session':'__Host-classroom_session','preauth':'__Host-classroom_preauth','recovery':'__Host-classroom_recovery'}


def strict_json(raw):
    def pairs(rows):
        value={}
        for k,v in rows:
            if k in value:raise ValueError('duplicate JSON member')
            value[k]=v
        return value
    def reject(_):raise ValueError('nonfinite JSON')
    try:
        value=json.loads(raw.decode('utf8'),object_pairs_hook=pairs,parse_constant=reject)
        def scalars(v):
            if isinstance(v,str) and any(0xD800<=ord(c)<=0xDFFF for c in v):raise ValueError('invalid Unicode')
            if isinstance(v,dict):
                for k,w in v.items():scalars(k);scalars(w)
            if isinstance(v,list):
                for w in v:scalars(w)
        scalars(value)
        return value
    except (ValueError,UnicodeError,RecursionError):raise AuthRejected('VALIDATION_FAILED',422) from None


def request_context(request):
    values={};raw=request.headers.get('cookie','')
    if len(raw)>4096:raise AuthRejected('FORBIDDEN',403)
    for part in raw.split(';'):
        if '=' not in part:continue
        name,value=part.strip().split('=',1)
        if name in values:raise AuthRejected('FORBIDDEN',403)
        values[name]=value
    return BrowserContext(**{k:values.get(v) for k,v in COOKIES.items()},csrf=request.headers.get('x-csrf-token'),
                          reauth=request.headers.get('x-reauth-token'),source=request.client.host if request.client else '')


def failure(error,request_id):
    return JSONResponse(status_code=error.status,content={'error':dict(code=error.code,message='Request could not be completed.',request_id=request_id,
        retryable=error.status in (429,503) and error.code!='RESULT_UNKNOWN',retry_after_seconds=None,current_revision=None,conflict_id=None,task_id=None)},
        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


def create_app(*,auth,contract,origin,identity=None,business=None,websocket_admission=None,billing=None,billing_admin=None):
    parsed=urlsplit(origin)
    if parsed.scheme!='https' or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:raise ValueError('one exact HTTPS origin is required')
    if contract.get('info',{}).get('version')!='0.2.0':raise ValueError('approved contract 0.2.0 required')
    if billing_admin is not None and not contract.get('x-pr11a-candidate'):
        raise ValueError('billing administration requires its explicit contract candidate')
    if billing_admin is not None:
        if auth.admin_reauth is not None and auth.admin_reauth is not billing_admin:
            raise ValueError('an existing admin reauth delegate must be coordinated')
        auth.admin_reauth=billing_admin
    workers=ThreadPoolExecutor(max_workers=64,thread_name_prefix='auth-ingress');slots=threading.BoundedSemaphore(64)
    @asynccontextmanager
    async def lifespan(_):
        try:yield
        finally:workers.shutdown(wait=True,cancel_futures=False)
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan);app.state.auth=auth;app.state.errors=deque(maxlen=32)
    def validator(schema):
        root=dict(schema);root['components']=contract['components']
        return Draft202012Validator(root,format_checker=FormatChecker())
    def check(schema,value,*,response=False):
        if next(validator(schema).iter_errors(value),None) is not None:
            raise AuthRejected('OUTPUT_INVALID' if response else 'VALIDATION_FAILED',503 if response else 422)

    def secure(request,*,write=False,websocket=False):
        if request.url.scheme not in ('https','wss') or request.headers.get('host')!=parsed.netloc:raise AuthRejected('FORBIDDEN',403)
        sites=(None,'same-origin') if websocket else ('same-origin','none')
        if write and (request.headers.get('origin')!=origin or request.headers.get('sec-fetch-site') not in sites):raise AuthRejected('FORBIDDEN',403)
        if request.headers.get('origin') not in (None,origin):raise AuthRejected('FORBIDDEN',403)

    async def body_for(request,schema):
        if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json' or request.headers.get('content-encoding','identity')!='identity':raise AuthRejected('VALIDATION_FAILED',422)
        limit=32768 if request.url.path==PREFIX+'/auth/reauth' else 16384
        length=request.headers.get('content-length')
        if length is not None and (not length.isdecimal() or int(length)>limit):raise AuthRejected('VALIDATION_FAILED',422)
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>limit:raise AuthRejected('VALIDATION_FAILED',422)
        result=strict_json(bytes(data));check(schema,result);return result

    changes={'/auth/logout-all':'logoutAll','/auth/password/change':'changePassword','/auth/email/change':'requestEmailChange',
             '/auth/mfa/enroll':'enrollMfa','/auth/mfa/confirm':'confirmMfa','/auth/mfa/disable':'disableMfa','/auth/mfa/recovery-codes':'rotateRecoveryCodes'}
    reads={'/auth/csrf':lambda c,p:auth.csrf(c),'/auth/session':lambda c,p:auth.session(c),
           '/me/sessions':lambda c,p:auth.list_contexts(c,'sessions',p.get('cursor'),p.get('limit')),
           '/me/devices':lambda c,p:auth.list_contexts(c,'devices',p.get('cursor'),p.get('limit'))}

    def dispatch(path,ctx,body,key,params,method):
        if path=='/me/quota' and billing is not None:return billing.quota(ctx)
        if path=='/tasks/{task_id}/cancel' and billing is not None:return billing.cancel(ctx,params['task_id'],key)
        if billing_admin is not None:
            if path=='/admin/users/{user_id}/quota':return billing_admin.user_quota(ctx,params['user_id'])
            if path=='/admin/usage':return billing_admin.read(ctx,'usage',params.get('period_start'))
            if path=='/admin/switches':
                return billing_admin.read(ctx,'switches') if method=='get' else billing_admin.apply(ctx,'adminUpdateSwitches',body,key)
            if path=='/admin/quota-adjustments':return billing_admin.apply(ctx,'adminAdjustQuota',body,key)
        if path in reads:return reads[path](ctx,params)
        if path=='/auth/login':return auth.login(ctx,body)
        if path=='/auth/login/mfa':return auth.login_mfa(ctx,body,key)
        if path=='/auth/logout':return auth.logout(ctx)
        if path=='/auth/reauth':return auth.reauthenticate(ctx,body)
        if path in changes:return auth.change(ctx,body,key,changes[path])
        if path.startswith('/me/sessions/') and path.endswith('/revoke'):return auth.change(ctx,body,key,'revokeSession',target_id=params['session_id'])
        if path.startswith('/me/devices/') and path.endswith('/revoke'):return auth.change(ctx,body,key,'revokeDevice',target_id=params['device_id'])
        if path=='/auth/password/forgot':return auth.email_action(ctx,body,'forgot',key)
        if path=='/auth/password/reset':return auth.email_action(ctx,body,'reset')
        if path=='/auth/email/confirm':return auth.email_action(ctx,body,'confirm_email')
        if path in ('/auth/recovery/enroll','/auth/recovery/confirm'):return auth.automatic(ctx,body,key,path.rsplit('/',1)[-1])
        if path=='/ws-tickets':return auth.ws_ticket(ctx,body)
        if path=='/courses/{course_id}' and business is not None:return business.course(ctx,params['course_id'],params.get('revision'))
        if path in ('/auth/register','/auth/verify-email','/auth/resend-verification'):
            if identity is None:raise AuthRejected('SERVICE_DISABLED',503)
            auth.require_csrf(ctx,'preauth')
            # The same context, key and body is answered with the first acknowledgement and is
            # not applied again; the receipt commits in the same transaction as the change.
            receipt=auth.preauth_receipt(ctx,{'/auth/register':'register','/auth/verify-email':'verifyEmail'}.get(path,'resendVerification'),key,body)
            if path=='/auth/register':
                approval=auth.password_policy.validate(body['password'],'student')
                if auth.hash_gate is None:raise AuthRejected('SERVICE_DISABLED',503)
                auth.hash_gate(lambda:identity.register(body['email'],body['password'],body['invitation_token'],body.get('nickname'),password_approval=approval,receipt=receipt))
            elif path=='/auth/verify-email':identity.activate(body['token'],receipt=receipt)
            else:
                # Eligible pending registration, missing recipient, stale
                # invitation and resend interval all have one public receipt.
                try:identity.resend(body['email'],receipt=receipt)
                except IdentityRejected as error:
                    if error.code=='IDEMPOTENCY_MISMATCH':raise
                    # Nothing changed. Keep the acknowledgement, so repeating this key after
                    # the resend interval does not start sending mail.
                    identity.record_receipt(receipt)
            return AuthReply({})
        raise AuthRejected('SERVICE_DISABLED',503)

    selected=set(reads)|set(changes)|{'/auth/login','/auth/login/mfa','/auth/logout','/auth/reauth','/auth/register','/auth/verify-email','/auth/resend-verification',
        '/auth/password/forgot','/auth/password/reset','/auth/email/confirm','/auth/recovery/enroll','/auth/recovery/confirm','/ws-tickets',
        '/me/sessions/{session_id}/revoke','/me/devices/{device_id}/revoke'}
    if business is not None:selected.add('/courses/{course_id}')
    if billing is not None:selected.update(('/me/quota','/tasks/{task_id}/cancel'))
    if billing_admin is not None:selected.update(('/admin/switches','/admin/usage','/admin/quota-adjustments','/admin/users/{user_id}/quota'))
    def endpoint(path,method,operation):
        success=next(k for k in operation['responses'] if k.startswith('2'))
        response_schema=operation['responses'][success]['content']['application/json']['schema']
        schema=operation.get('requestBody',{}).get('content',{}).get('application/json',{}).get('schema')
        async def handle(request:Request):
            request_id=str(uuid4())
            entered=time.monotonic();deadline=entered+5;deadline_token=DEADLINE.set(deadline);dispatched=False
            try:
                secure(request,write=method!='get');ctx=request_context(request);key=None;body={};params=dict(request.path_params)
                if path in ('/me/quota','/admin/switches','/admin/users/{user_id}/quota') and method=='get' and request.url.query:
                    raise AuthRejected('VALIDATION_FAILED',422)
                if path=='/admin/usage':
                    pairs=list(request.query_params.multi_items())
                    if pairs:
                        if len(pairs)!=1 or pairs[0][0]!='period_start' or not re.fullmatch(r'\d{4}-\d{2}-01',pairs[0][1]):raise AuthRejected('VALIDATION_FAILED',422)
                        try:
                            from zoneinfo import ZoneInfo
                            params['period_start']=datetime.fromisoformat(pairs[0][1]).replace(tzinfo=ZoneInfo('Asia/Shanghai')).isoformat()
                        except ValueError:raise AuthRejected('VALIDATION_FAILED',422) from None
                if path=='/courses/{course_id}':
                    pairs=list(request.query_params.multi_items())
                    if pairs:
                        if len(pairs)!=1 or pairs[0][0]!='revision' or not pairs[0][1].isascii() or not pairs[0][1].isdecimal() or not 1<=int(pairs[0][1])<=9007199254740991:raise AuthRejected('VALIDATION_FAILED',422)
                        params['revision']=int(pairs[0][1])
                if path in ('/me/sessions','/me/devices'):
                    pairs=list(request.query_params.multi_items())
                    if len({name for name,_ in pairs})!=len(pairs) or any(name not in ('cursor','limit') for name,_ in pairs):raise AuthRejected('VALIDATION_FAILED',422)
                    for name,value in pairs:
                        if name=='cursor':
                            if not PAGE_CURSOR.fullmatch(value):raise AuthRejected('VALIDATION_FAILED',422)
                            params['cursor']=value
                        else:
                            if not value.isascii() or not value.isdecimal() or len(value)>3 or not 1<=int(value)<=100:raise AuthRejected('VALIDATION_FAILED',422)
                            params['limit']=int(value)
                if method!='get':
                    if request.url.query:raise AuthRejected('VALIDATION_FAILED',422)
                    key=request.headers.get('idempotency-key')
                    try:
                        if str(UUID(key))!=key:raise ValueError()
                    except (ValueError,TypeError,AttributeError):raise AuthRejected('VALIDATION_FAILED',422) from None
                    async with asyncio.timeout(max(0,entered+1-time.monotonic())):body=await body_for(request,schema)
                dispatched=True
                # Cancellation detaches the reply, not the actual worker/hash.
                # Cross-process slots stay held until computation really exits;
                # DB calls recheck the same absolute budget before COMMIT.
                if not slots.acquire(False):raise AuthRejected('RATE_LIMITED',429)
                context=copy_context()
                def work():
                    try:remaining();return dispatch(path,ctx,body,key,params,method)
                    finally:slots.release()
                try:future=workers.submit(context.run,work)
                except BaseException:slots.release();raise
                async with asyncio.timeout(max(0,deadline-time.monotonic())):
                    reply=await asyncio.shield(asyncio.wrap_future(future))
                value=reply.body
                if response_schema.get('$ref','').endswith('/Acknowledged'):value=dict(accepted=True,request_id=request_id)
                check(response_schema,value,response=True)
                response=JSONResponse(value,status_code=int(success),headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
                for kind in COOKIES:
                    token=getattr(reply,kind)
                    if token:response.set_cookie(COOKIES[kind],token,secure=True,httponly=True,samesite='lax',path='/',max_age=reply.persistent_seconds if kind=='session' else None)
                for kind in ('session','recovery'):
                    if getattr(reply,'clear_'+kind):response.delete_cookie(COOKIES[kind],path='/',secure=True,httponly=True,samesite='lax')
                return response
            except AuthRejected as error:return failure(error,request_id)
            except TimeoutError:return failure(AuthRejected('RESULT_UNKNOWN' if dispatched else 'RATE_LIMITED',503 if dispatched else 429),request_id)
            except IdentityRejected as error:return failure(AuthRejected('IDEMPOTENCY_MISMATCH',409) if error.code=='IDEMPOTENCY_MISMATCH' else AuthRejected('VALIDATION_FAILED',422),request_id)
            except Exception as error:
                app.state.errors.append({'kind':type(error).__name__,'frames':[{'function':f.name,'line':f.lineno} for f in traceback.extract_tb(error.__traceback__)[-5:]]})
                return failure(AuthRejected('STORAGE_UNAVAILABLE',503),request_id)
            finally:DEADLINE.reset(deadline_token)
        return handle
    for path in selected:
        if path not in contract['paths']:raise ValueError('route absent from approved contract: '+path)
        for method,operation in contract['paths'][path].items():
            if method in ('get','post') or (path=='/admin/switches' and method=='patch' and billing_admin is not None):
                app.add_api_route(PREFIX+path,endpoint(path,method,operation),methods=[method.upper()])

    @app.websocket(PREFIX+'/ws/events')
    async def events(ws:WebSocket):
        permit=None
        try:
            # WebSocket handshakes do not consistently carry Fetch Metadata.
            # Exact Origin and the current HttpOnly session remain mandatory;
            # any supplied cross-site metadata is still rejected.
            secure(ws,write=True,websocket=True)
            if ws.url.query or ws.headers.get('sec-websocket-protocol')!='classroom.v1':raise AuthRejected('FORBIDDEN',403)
            ctx=request_context(ws)
            entered=asyncio.get_running_loop().time()
            first_deadline=(datetime.now(timezone.utc)+timedelta(seconds=auth.policy.ws_first_frame)).isoformat()
            if websocket_admission is None:raise AuthRejected('SERVICE_DISABLED',503)
            permit=websocket_admission.connection(ctx.source);authenticated=permit.__enter__()
            async with asyncio.timeout(auth.policy.ws_first_frame):await run_in_threadpool(auth.ws_current,ctx)
            await ws.accept(subprotocol='classroom.v1')
            raw=await asyncio.wait_for(ws.receive_text(),timeout=max(0,entered+auth.policy.ws_first_frame-asyncio.get_running_loop().time()))
            if len(raw.encode('utf8'))>1024:raise AuthRejected()
            frame=strict_json(raw.encode('utf8'))
            if type(frame) is not dict or set(frame)!={'type','ticket'} or frame['type']!='authenticate':raise AuthRejected()
            if asyncio.get_running_loop().time()>=entered+auth.policy.ws_first_frame:raise AuthRejected()
            await run_in_threadpool(auth.ws_authenticate,ctx,frame['ticket'],'events',first_deadline)
            if asyncio.get_running_loop().time()>=entered+auth.policy.ws_first_frame:raise AuthRejected()
            authenticated()
            # No event body is accepted/published by this identity package.
            # A consumer must supply the owner-scoped event adapter explicitly.
            while True:
                try:
                    message=await asyncio.wait_for(ws.receive_text(),timeout=1)
                    if len(message.encode('utf8'))>4096:raise AuthRejected()
                except asyncio.TimeoutError:pass
                await run_in_threadpool(auth.ws_current,ctx)
        except WebSocketDisconnect:pass
        except Exception as error:
            app.state.errors.append({'kind':type(error).__name__,'stage':'websocket','code':error.code if isinstance(error,AuthRejected) else None})
            try:await ws.close(code=1008,reason='authorization_required')
            except (RuntimeError,WebSocketDisconnect):pass
        finally:
            if permit:permit.__exit__(None,None,None)
    return app
