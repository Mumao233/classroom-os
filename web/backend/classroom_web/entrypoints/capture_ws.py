"""One bounded pending chunk per authenticated capture socket, with durable outbox ACK."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

from fastapi import WebSocket
from jsonschema import Draft202012Validator, FormatChecker
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect

from ..domain.auth import AuthRejected
from ..domain.capture import MAX_CHUNK_BYTES


def register_capture_ws(app, *, capture, auth, admission, secure, request_context):
    import json
    from .http import strict_json
    # The immutable schema lives with the versioned producer/consumer contract.
    root=Path(__file__).resolve().parents[4]
    schema=json.loads((root/'docs/web-alpha/architecture/contracts/events.schema.json').read_text(encoding='utf8'))
    validators={name:Draft202012Validator({'$ref':'#/$defs/'+name,'$defs':schema['$defs']},format_checker=FormatChecker())
                for name in ('CaptureChunkStart','FrameHeader','Heartbeat')}

    @app.websocket('/api/web/v1/ws/capture')
    async def socket(ws:WebSocket):
        permit=None
        try:
            secure(ws,write=True,websocket=True)
            if ws.url.query or ws.headers.get('sec-websocket-protocol')!='classroom.v1':
                raise AuthRejected('FORBIDDEN',403)
            capture.available()
            if admission is None or capture.database.cursor_secret is None:
                raise AuthRejected('SERVICE_DISABLED',503)
            ctx=request_context(ws)
            permit=admission.connection(ctx.source);authenticated=permit.__enter__()
            entered=asyncio.get_running_loop().time()
            first_deadline=(datetime.now(timezone.utc)+timedelta(seconds=auth.policy.ws_first_frame)).isoformat()
            await run_in_threadpool(auth.ws_current,ctx)
            await ws.accept(subprotocol='classroom.v1')
            raw=await asyncio.wait_for(ws.receive_text(),max(0,entered+auth.policy.ws_first_frame-asyncio.get_running_loop().time()))
            if len(raw.encode())>1024:raise AuthRejected('FORBIDDEN',403)
            frame=strict_json(raw.encode())
            if type(frame) is not dict or set(frame)!={'type','ticket'} or frame['type']!='authenticate':
                raise AuthRejected('FORBIDDEN',403)
            ticket=await run_in_threadpool(auth.ws_authenticate,ctx,frame['ticket'],'capture',first_deadline)
            scope=ticket['scope']
            await run_in_threadpool(capture.ws_scope,ctx,scope)
            authenticated()
            # WSS has already checked exact Origin, current HttpOnly Cookie and a single-use ticket.
            # Reuse the same application methods and their CSRF-bound transaction path.
            ctx=replace(ctx,csrf=auth.crypto.csrf('session',ctx.session))
            output_seq=0
            async def transient(kind,data):
                nonlocal output_seq
                await run_in_threadpool(capture.ws_scope,ctx,scope)
                await ws.send_json(dict(protocol_version=1,type=kind,sent_at=datetime.now(timezone.utc).isoformat(),
                                        data=data,connection_seq=output_seq))
                output_seq+=1
            await transient('capture.ready',dict(class_session_id=scope['class_session_id'],capture_epoch_id=scope['capture_epoch_id'],
                fence_token=scope['fence_token'],max_frame_bytes=3200,inflight_window_bytes=MAX_CHUNK_BYTES))
            expected_connection=0;pending=None;header=None;content=bytearray();frame_seq=0
            while True:
                try:
                    message=await asyncio.wait_for(ws.receive(),1)
                except asyncio.TimeoutError:
                    await run_in_threadpool(capture.ws_scope,ctx,scope)
                    continue
                if message['type']=='websocket.disconnect':break
                await run_in_threadpool(capture.ws_scope,ctx,scope)
                if message.get('bytes') is not None:
                    block=message['bytes']
                    if header is None or pending is None or len(block)!=header['byte_length'] or hashlib.sha256(block).hexdigest()!=header['sha256']:
                        raise AuthRejected('VALIDATION_FAILED',422)
                    if len(content)+len(block)>pending['descriptor']['byte_length']:
                        raise AuthRejected('CAPACITY_EXCEEDED',409)
                    content.extend(block)
                    await transient('capture.frame_received',dict(chunk_id=pending['id'],frame_seq=header['frame_seq'],durable=False))
                    if header['chunk_final_frame']:
                        if len(content)!=pending['descriptor']['byte_length']:
                            raise AuthRejected('VALIDATION_FAILED',422)
                        await run_in_threadpool(capture.put,ctx,pending['id'],pending['put_key'],bytes(content))
                        await run_in_threadpool(capture.ws_scope,ctx,scope)
                        event=await run_in_threadpool(capture.durable_event,ctx,pending['id'])
                        await ws.send_json(event)
                        pending=None;content=bytearray();frame_seq=0
                    else:
                        if len(content)==pending['descriptor']['byte_length']:
                            raise AuthRejected('VALIDATION_FAILED',422)
                        frame_seq+=1
                    header=None
                    continue
                if header is not None or len((message.get('text') or '').encode())>8192:
                    raise AuthRejected('VALIDATION_FAILED',422)
                frame=strict_json((message.get('text') or '').encode())
                kind=frame.get('type') if isinstance(frame,dict) else None
                model={'capture.chunk_start':'CaptureChunkStart','capture.frame':'FrameHeader','heartbeat':'Heartbeat'}.get(kind)
                if model is None or not validators[model].is_valid(frame) or frame['connection_seq']!=expected_connection:
                    raise AuthRejected('VALIDATION_FAILED',422)
                expected_connection+=1;data=frame['data']
                if kind=='heartbeat':
                    continue
                if any(data[field]!=scope[field] for field in ('class_session_id','capture_epoch_id','fence_token')):
                    raise AuthRejected('STALE_FENCE',409)
                if kind=='capture.chunk_start':
                    if pending is not None:raise AuthRejected('CAPACITY_EXCEEDED',409)
                    body={field:data[field] for field in ('capture_epoch_id','fence_token','seq','start_sample','end_sample','byte_length','sha256')}
                    pending=await run_in_threadpool(capture.prepare,ctx,scope['class_session_id'],data['op_id'],body,None,data['chunk_id'])
                    if pending['id']!=data['chunk_id']:raise AuthRejected('IDEMPOTENCY_MISMATCH',409)
                    pending['put_key']=data['op_id']
                else:
                    if (pending is None or data['chunk_id']!=pending['id'] or data['frame_seq']!=frame_seq
                            or data['byte_length']>3200 or data['byte_length']!=data['sample_count']*2
                            or data['start_sample']!=pending['descriptor']['start_sample']+len(content)//2):
                        raise AuthRejected('VALIDATION_FAILED',422)
                    header=data
        except WebSocketDisconnect:
            pass
        except Exception as error:
            code=error.code if isinstance(error,AuthRejected) else 'STORAGE_UNAVAILABLE'
            close={'UNAUTHENTICATED':4401,'STALE_FENCE':4409,'RATE_LIMITED':4429,
                   'SERVICE_DISABLED':1013,'STORAGE_UNAVAILABLE':1013,'RESULT_UNKNOWN':1013}.get(code,4403)
            app.state.errors.append({'kind':type(error).__name__,'stage':'capture_websocket','code':code})
            try:await ws.close(code=close,reason=code)
            except (RuntimeError,WebSocketDisconnect):pass
        finally:
            if permit:permit.__exit__(None,None,None)
