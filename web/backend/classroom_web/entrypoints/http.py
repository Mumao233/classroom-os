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
from decimal import Decimal, InvalidOperation
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
ADMIN_INTEGER_FIELDS = {
    'adminUpdateUser': 'base_revision',
    'adminUpdateSwitches': 'base_revision',
    'adminAdjustQuota': 'additional_units',
}


def normalize_admin_write_integer(operation_id, body):
    """Preserve the exact wire value until its integer and range checks have passed."""
    try:
        if operation_id == 'reauthenticate':
            planned = body['operation']
            operation_id = planned['purpose']
            body = planned['candidate_parameters']['body']
        field = ADMIN_INTEGER_FIELDS[operation_id]
        number = body[field]
    except (KeyError, TypeError):
        return  # The request schema checks the operation and body structure.
    if isinstance(number, Decimal):
        if not 1 <= number <= 2**53 - 1 or number != number.to_integral_value():
            raise ValueError('expected a safe positive integer')
        body[field] = int(number)


def strict_json(raw, *, operation_id=None):
    def pairs(rows):
        value={}
        for k,v in rows:
            if k in value:raise ValueError('duplicate JSON member')
            value[k]=v
        return value
    def reject(_):raise ValueError('nonfinite JSON')
    try:
        exact = operation_id in ADMIN_INTEGER_FIELDS or operation_id == 'reauthenticate'
        value=json.loads(raw.decode('utf8'),object_pairs_hook=pairs,parse_constant=reject,
                         parse_float=Decimal if exact else float)
        if exact:
            normalize_admin_write_integer(operation_id, value)
        def scalars(node):
            if isinstance(node, Decimal):
                return float(node)  # Other numeric fields retain their ordinary JSON representation.
            if isinstance(node, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in node):
                raise ValueError('invalid Unicode')
            if isinstance(node, dict):
                for key, item in node.items():
                    scalars(key)
                    node[key] = scalars(item)
            elif isinstance(node, list):
                for index, item in enumerate(node):
                    node[index] = scalars(item)
            return node
        return scalars(value)
    except (ValueError,InvalidOperation,UnicodeError,RecursionError):raise AuthRejected('VALIDATION_FAILED',422) from None


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
    return JSONResponse(status_code=error.status,content={'error':dict(code=error.code,message=getattr(error,'public_message','Request could not be completed.'),request_id=request_id,
        retryable=error.status in (429,503) and error.code!='RESULT_UNKNOWN',retry_after_seconds=getattr(error,'retry_after_seconds',None),
        current_revision=getattr(error,'current_revision',None),conflict_id=getattr(error,'conflict_id',None),task_id=None)},
        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


def create_app(*,auth,contract,origin,identity=None,business=None,websocket_admission=None,cache=None,media=None,media_spool_directory=None,billing=None,billing_admin=None,qa=None,review=None,deletion=None,capture=None,sync=None,portable=None,portable_transfers=None,portable_jobs=None,asr=None,asr_worker=None,filetrans=None,filetrans_worker=None):
    parsed=urlsplit(origin)
    if parsed.scheme!='https' or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:raise ValueError('one exact HTTPS origin is required')
    contract_version = contract.get('info', {}).get('version')
    if contract_version not in ('0.2.0', '0.2.1', '0.3.0', '0.4.0'):
        raise ValueError('approved contract 0.2.0, 0.2.1, 0.3.0 or 0.4.0 required')
    if asr is not None and (asr.capture is not capture or asr.auth is not auth):
        raise ValueError('ASR requires the same capture and auth services')
    if asr_worker is not None and (asr is None or asr_worker.service is not asr or not asr.enabled):
        raise ValueError('ASR worker requires the explicitly enabled synthetic service')
    from ..application.filetrans import PATHS as filetrans_routes
    from ..infrastructure.filetrans_repository import OtherGeneration
    filetrans_paths = filetrans_routes if filetrans is not None else frozenset()
    if filetrans is not None and (capture is None or deletion is None or filetrans.capture is not capture
            or filetrans.auth is not auth or capture.deletion is not deletion):
        raise ValueError('Filetrans requires the shared capture, authentication and deletion gate')
    if filetrans_worker is not None and (filetrans is None or filetrans_worker.service is not filetrans):
        raise ValueError('Filetrans worker requires its explicit synthetic service')
    if billing_admin is not None and contract_version != '0.4.0' and not contract.get('x-pr11a-candidate'):
        raise ValueError('billing administration requires contract 0.4.0 or its explicit contract candidate')
    if qa is not None and contract.get('x-pr07a-candidate') != 'pr07a-1':
        raise ValueError('QA requires its explicit PR-07a contract candidate')
    if review is not None and contract.get('x-pr07b-candidate') != 'pr07b-1':
        raise ValueError('Review requires its explicit PR-07b contract candidate')
    if sync is not None and contract.get('x-pr07b-candidate') != 'pr07b-1':
        raise ValueError('Sync requires the existing PR-07b contract candidate for exact Review conflicts')
    if sync is not None and sync.enabled and (review is None or not review.config.get('enabled', False)):
        raise ValueError('Enabled Sync requires an enabled ReviewBrowser for exact source authorization')
    if portable is not None and (media is None or portable.media is not media or deletion is None):
        raise ValueError('portable preview requires shared media and deletion services')
    portable_paths = frozenset()
    if portable_jobs is not None:
        from ..application.portable_jobs import PATHS as portable_paths
        if portable_transfers is not None and portable_jobs.transfers is not portable_transfers:
            raise ValueError('portable job and transfer services must share one lifecycle')
        portable_transfers = portable_jobs.transfers
    if portable_transfers is not None and (portable_transfers.media is not media
            or deletion is None or portable_transfers.deletion is not deletion):
        raise ValueError('portable transfers require shared media and deletion services')
    qa_paths = {
        '/class-sessions/{class_session_id}/generation-plans',
        '/generation-plans/{plan_id}', '/generation-plans/{plan_id}/execute',
        '/source-snapshots/{snapshot_id}', '/questions', '/questions/{question_id}',
        '/tasks/{task_id}', '/tasks/{task_id}/cancel', '/tasks/{task_id}/reconcile',
    } if qa is not None or review is not None else set()
    review_paths = {
        '/review-documents', '/review-documents/{document_id}',
        '/review-documents/{document_id}/revisions',
        '/review-documents/{document_id}/personal-revisions',
        '/review-documents/{document_id}/adopt', '/review-revisions/{revision_id}',
        '/tasks/{task_id}/raw-output',
    } if review is not None else set()
    from ..application.sync_browser import SYNC_PATHS
    sync_paths = SYNC_PATHS if sync is not None else set()
    if billing_admin is not None and auth.admin_reauth is not None and auth.admin_reauth is not billing_admin:
        raise ValueError('an existing admin reauth delegate must be coordinated')
    workers=ThreadPoolExecutor(max_workers=64,thread_name_prefix='auth-ingress');slots=threading.BoundedSemaphore(64)
    @asynccontextmanager
    async def lifespan(app):
        stop_asr=asyncio.Event()
        controls = [asyncio.create_task(worker.serve(stop_asr, app.state.errors))
                    for worker in (asr_worker, filetrans_worker) if worker is not None]
        try:
            yield
        finally:
            try:
                if controls:
                    stop_asr.set()
                    await asyncio.gather(*controls)
            finally:
                workers.shutdown(wait=True,cancel_futures=False)
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

    async def body_for(request,schema,*,operation_id=None):
        if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json' or request.headers.get('content-encoding','identity')!='identity':raise AuthRejected('VALIDATION_FAILED',422)
        limit=32768 if request.url.path==PREFIX+'/auth/reauth' else 16384
        if review is not None and (request.url.path == PREFIX+'/review-documents'
                or re.fullmatch(re.escape(PREFIX)+r'/review-documents/[0-9a-fA-F-]{36}/personal-revisions', request.url.path)):
            limit=2_000_000
        if sync is not None and (request.url.path == PREFIX+'/sync/operations'
                or re.fullmatch(re.escape(PREFIX)+r'/sync/conflicts/[0-9a-fA-F-]{36}/resolve', request.url.path)):
            limit=8_000_000
        capacity_limited = limit == 8_000_000
        length=request.headers.get('content-length')
        if length is not None and not length.isdecimal():raise AuthRejected('VALIDATION_FAILED',422)
        if length is not None and int(length)>limit:
            raise AuthRejected('CAPACITY_EXCEEDED' if capacity_limited else 'VALIDATION_FAILED',413 if capacity_limited else 422)
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>limit:
                raise AuthRejected('CAPACITY_EXCEEDED' if capacity_limited else 'VALIDATION_FAILED',413 if capacity_limited else 422)
        result=strict_json(bytes(data),operation_id=operation_id);check(schema,result);return result

    changes={'/auth/logout-all':'logoutAll','/auth/password/change':'changePassword','/auth/email/change':'requestEmailChange',
             '/auth/mfa/enroll':'enrollMfa','/auth/mfa/confirm':'confirmMfa','/auth/mfa/disable':'disableMfa','/auth/mfa/recovery-codes':'rotateRecoveryCodes'}
    reads={'/auth/csrf':lambda c,p:auth.csrf(c),'/auth/session':lambda c,p:auth.session(c),
           '/me/sessions':lambda c,p:auth.list_contexts(c,'sessions',p.get('cursor'),p.get('limit')),
           '/me/devices':lambda c,p:auth.list_contexts(c,'devices',p.get('cursor'),p.get('limit'))}

    deletion_routes={}
    if deletion is not None:
        from ..application.deletion import ROUTES as deletion_routes

    asr_paths = {
        '/class-sessions/{class_session_id}/asr-streams', '/asr-streams/{stream_id}',
        '/asr-streams/{stream_id}/finish', '/class-sessions/{class_session_id}/transcript-versions',
        '/transcript-versions/{version_id}/segments', '/tasks/{task_id}/retry',
        '/tasks/{task_id}', '/tasks/{task_id}/cancel',
    } if asr is not None else set()

    def dispatch(path,ctx,body,key,params,method,operation_id):
        if path in filetrans_paths:
            try:
                return filetrans.dispatch(operation_id,ctx,body,key,params)
            except OtherGeneration:
                if (operation_id != 'prepareGeneration' and path not in asr_paths
                        and path not in qa_paths and path not in portable_paths
                        and not (operation_id == 'cancelTask' and billing is not None)):
                    raise AuthRejected('NOT_FOUND', 404) from None
        if path in asr_paths:
            try:
                return asr.dispatch(operation_id,ctx,body,key,params)
            except AuthRejected as error:
                shared = operation_id in ('getTask', 'cancelTask') and (
                    path in portable_paths or path in qa_paths or (operation_id == 'cancelTask' and billing is not None))
                if error.code != 'NOT_FOUND' or not shared:
                    raise
        if path in portable_paths:
            try:
                return portable_jobs.dispatch(operation_id,ctx,body,key,params)
            except AuthRejected as error:
                shared = path in qa_paths or path in review_paths or (path == '/tasks/{task_id}/cancel' and billing)
                legacy = path == '/imports/preview' and portable is not None
                if error.code != 'NOT_FOUND' or not (shared or legacy):
                    raise
        if path == '/imports/preview' and portable is not None:
            return portable.preview(ctx, key, body)
        if path in sync_paths:return sync.dispatch(operation_id,ctx,body,key,params)
        if path in deletion_routes:return deletion.handle(path,method,ctx,body,key,params)
        if path in review_paths:
            return review.dispatch(operation_id,ctx,body,key,params,billing_fallback=billing)
        if path in qa_paths:
            service = review if review is not None else qa
            return service.dispatch(operation_id,ctx,body,key,params,billing_fallback=billing)
        if cache is not None and path in ('/me/cache-unlock','/me/cache-recover'):
            return cache.release(ctx,body,key,recovery=path=='/me/cache-recover')
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
        if path=='/auth/reauth':
            if body['operation']['purpose'].startswith('admin'):
                return auth.reauthenticate(ctx,body,admin_reauth=billing_admin)
            return auth.reauthenticate(ctx,body)
        if path in changes:return auth.change(ctx,body,key,changes[path])
        if path.startswith('/me/sessions/') and path.endswith('/revoke'):return auth.change(ctx,body,key,'revokeSession',target_id=params['session_id'])
        if path.startswith('/me/devices/') and path.endswith('/revoke'):return auth.change(ctx,body,key,'revokeDevice',target_id=params['device_id'])
        if path=='/auth/password/forgot':return auth.email_action(ctx,body,'forgot',key)
        if path=='/auth/password/reset':return auth.email_action(ctx,body,'reset')
        if path=='/auth/email/confirm':return auth.email_action(ctx,body,'confirm_email')
        if path in ('/auth/recovery/enroll','/auth/recovery/confirm'):return auth.automatic(ctx,body,key,path.rsplit('/',1)[-1])
        if path=='/ws-tickets':
            if capture is not None and body['channel']=='capture':return capture.issue_ws_ticket(ctx,body)
            return auth.ws_ticket(ctx,body)
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
    selected.update(qa_paths)
    selected.update(review_paths)
    selected.update(sync_paths)
    selected.update(portable_paths)
    selected.update(asr_paths)
    selected.update(filetrans_paths)
    if portable is not None:selected.add('/imports/preview')
    if billing is not None:selected.update(('/me/quota','/tasks/{task_id}/cancel'))
    if billing_admin is not None:selected.update(('/admin/switches','/admin/usage','/admin/quota-adjustments','/admin/users/{user_id}/quota'))
    selected.update(deletion_routes)
    if cache is not None:
        selected.add('/me/cache-unlock')
        if cache.allow_recovery and contract.get('x-pr05a-candidate') is True:
            selected.add('/me/cache-recover')
    def endpoint(path,method,operation):
        success=next(k for k in operation['responses'] if k.startswith('2'))
        response_schema=operation['responses'][success]['content']['application/json']['schema']
        schema=operation.get('requestBody',{}).get('content',{}).get('application/json',{}).get('schema')
        key_validator = None
        for parameter in operation.get('parameters', []):
            if '$ref' in parameter:
                parameter = contract['components']['parameters'][parameter['$ref'].rsplit('/', 1)[-1]]
            if parameter.get('in') == 'header' and parameter['name'].lower() == 'idempotency-key':
                key_validator = validator(parameter['schema'])
                break
        async def handle(request:Request):
            request_id=str(uuid4())
            entered=time.monotonic();deadline=entered+(120 if path == '/imports/preview' and portable_jobs is not None else 5);deadline_token=DEADLINE.set(deadline);dispatched=False
            try:
                secure(request,write=method!='get');ctx=request_context(request);key=None;body={};params=dict(request.path_params)
                if (path in review_paths or path in sync_paths or path in asr_paths or path in filetrans_paths) and method == 'get':
                    query_schemas = {item['name']: item['schema'] for item in operation.get('parameters', [])
                                     if item.get('in') == 'query'}
                    pairs = list(request.query_params.multi_items())
                    if len({name for name, _ in pairs}) != len(pairs):
                        raise AuthRejected('VALIDATION_FAILED', 422)
                    for name, value in pairs:
                        if name not in query_schemas:
                            raise AuthRejected('VALIDATION_FAILED', 422)
                        query_schema = query_schemas[name]
                        if query_schema.get('type') == 'integer':
                            if not value.isascii() or not value.isdecimal() or len(value) > 16:
                                raise AuthRejected('VALIDATION_FAILED', 422)
                            value = int(value)
                        elif query_schema.get('type') == 'boolean':
                            if value not in ('true', 'false'):
                                raise AuthRejected('VALIDATION_FAILED', 422)
                            value = value == 'true'
                        check(query_schema, value)
                        params[name] = value
                    for name, query_schema in query_schemas.items():
                        required = next(item.get('required', False) for item in operation.get('parameters', [])
                                        if item.get('in') == 'query' and item['name'] == name)
                        if required and name not in params:
                            raise AuthRejected('VALIDATION_FAILED', 422)
                if path in qa_paths and method == 'get':
                    pairs = list(request.query_params.multi_items())
                    allowed = {'class_session_id', 'cursor', 'limit'} if path == '/questions' else set()
                    if len({name for name, _ in pairs}) != len(pairs) or any(name not in allowed for name, _ in pairs):
                        raise AuthRejected('VALIDATION_FAILED', 422)
                    for name, value in pairs:
                        if name == 'limit':
                            if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 100:
                                raise AuthRejected('VALIDATION_FAILED', 422)
                            params[name] = int(value)
                        elif name == 'class_session_id':
                            try:
                                if str(UUID(value)) != value:
                                    raise ValueError()
                            except ValueError:
                                raise AuthRejected('VALIDATION_FAILED', 422) from None
                            params[name] = value
                        else:
                            if not 1 <= len(value) <= 512:
                                raise AuthRejected('VALIDATION_FAILED', 422)
                            params[name] = value
                if (path in ('/me/quota','/admin/switches','/admin/users/{user_id}/quota') or path in deletion_routes) and method=='get' and request.url.query:
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
                    if key_validator is not None and not key_validator.is_valid(key):
                        raise AuthRejected('VALIDATION_FAILED', 422)
                    async with asyncio.timeout(max(0,entered+1-time.monotonic())):
                        body=await body_for(request,schema,operation_id=operation['operationId'])
                dispatched=True
                # Cancellation detaches the reply, not the actual worker/hash.
                # Cross-process slots stay held until computation really exits;
                # DB calls recheck the same absolute budget before COMMIT.
                if not slots.acquire(False):raise AuthRejected('RATE_LIMITED',429)
                context=copy_context()
                def work():
                    try:remaining();return dispatch(path,ctx,body,key,params,method,operation['operationId'])
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
            if (method in ('get','post')
                    or (path=='/admin/switches' and method=='patch' and billing_admin is not None)
                    or (path in review_paths and method=='patch')
                    or (path in sync_paths and method=='patch')):
                app.add_api_route(PREFIX+path,endpoint(path,method,operation),methods=[method.upper()])

    if deletion is not None:
        # Covers every API route, including media and the event socket.
        from .deletion_http import DeletionGate
        app.add_middleware(DeletionGate,deletion=deletion,failure=failure,prefix=PREFIX)

    if media is not None:
        from .media_http import register_media
        register_media(app,media=media,contract=contract,secure=secure,request_context=request_context,
                       check=check,body_for=body_for,failure=failure,spool_directory=media_spool_directory,
                       portable=portable_transfers,portable_preview=portable)

    if portable_jobs is not None:
        from .portable_http import register_portable
        register_portable(app,service=portable_transfers,secure=secure,request_context=request_context,
                          body_for=body_for,failure=failure)

    if capture is not None:
        from .capture_http import register_capture
        register_capture(app,capture=capture,contract=contract,secure=secure,request_context=request_context,
                         check=check,failure=failure)
        from .capture_ws import register_capture_ws
        register_capture_ws(app,capture=capture,auth=auth,admission=websocket_admission,
                            secure=secure,request_context=request_context)

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
            event_cursor, partial_cursor = (None, 0)
            connection_sequence = 0
            replay_pending = False
            if asr is not None and asr.enabled:
                event_cursor, partial_cursor = await run_in_threadpool(asr.events.watermark, ctx)
            while True:
                try:
                    message=await asyncio.wait_for(ws.receive_text(),timeout=0.25 if event_cursor is not None else 1)
                    if len(message.encode('utf8'))>4096:raise AuthRejected()
                    if event_cursor is not None:
                        frame=strict_json(message.encode('utf8'))
                        if (not isinstance(frame,dict) or set(frame)!={'type','cursor'}
                                or frame['type']!='resume.after' or not isinstance(frame['cursor'],str)
                                or not 1 <= len(frame['cursor']) <= 512):
                            raise AuthRejected()
                        event_cursor=frame['cursor']
                        replay_pending=True
                except asyncio.TimeoutError:
                    pass
                await run_in_threadpool(auth.ws_current,ctx)
                if event_cursor is not None:
                    from ..domain.tasks import ResyncRequired
                    try:
                        event_cursor,partial_cursor,durable,partial,caught_up=await run_in_threadpool(
                            asr.events.poll,ctx,event_cursor,partial_cursor)
                    except ResyncRequired:
                        connection_sequence+=1
                        await ws.send_json({'protocol_version':1,'type':'resync.required',
                            'sent_at':datetime.now(timezone.utc).isoformat(),'connection_seq':connection_sequence,
                            'data':{'reason':'cursor_expired','snapshot_path':'/api/web/v1/sync/bootstrap'}})
                        event_cursor,partial_cursor=await run_in_threadpool(asr.events.watermark,ctx)
                        replay_pending=False
                        continue
                    for message in durable:
                        await ws.send_json(message)
                    for message in partial:
                        connection_sequence+=1
                        await ws.send_json({**message,'connection_seq':connection_sequence})
                    if replay_pending and caught_up:
                        replay_pending=False
                        connection_sequence+=1
                        await ws.send_json({'protocol_version':1,'type':'replay.complete',
                            'sent_at':datetime.now(timezone.utc).isoformat(),'connection_seq':connection_sequence,
                            'data':{'cursor':event_cursor}})
        except WebSocketDisconnect:pass
        except Exception as error:
            app.state.errors.append({'kind':type(error).__name__,'stage':'websocket','code':error.code if isinstance(error,AuthRejected) else None})
            try:await ws.close(code=1008,reason='authorization_required')
            except (RuntimeError,WebSocketDisconnect):pass
        finally:
            if permit:permit.__exit__(None,None,None)
    return app
