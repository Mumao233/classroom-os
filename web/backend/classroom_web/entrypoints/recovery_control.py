"""ACL-limited local control transport; never mounted on the public HTTP app."""
import json,threading,time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from ..application.recovery_maintenance import apply_change
from ..domain.auth import AuthRejected
from ..infrastructure.recovery_peer_windows import NativePipePeer
from ..infrastructure.recovery_transport_windows import PipeListener,connect_pipe
from .http import strict_json


def control_error(error):
    return dict(protocol_version=1,kind='identity_control.error',
        code='result_unknown' if getattr(error,'code',None)=='RESULT_UNKNOWN' else 'unavailable' if getattr(error,'status',503)==503 else 'denied',
        retryable=False,correlation_id=str(uuid4()))


class LocalControlServer:
    """Caller supervises this in the same process that owns the broker lease.

    Maintenance uses a different pipe name and separately approved SID ACL.
    Supplied maintenance adapters are unavailable unless explicitly configured.
    Shutdown drains actual workers before the supervisor closes the broker.
    """
    def __init__(self,*,broker,name,allowed_sids,maintenance=None,maintenance_only=False,resume_existing=False):
        if maintenance_only and maintenance is None:raise ValueError('independent maintenance adapter required')
        self.broker=broker;self.maintenance=maintenance;self.maintenance_only=maintenance_only
        if resume_existing and not maintenance_only:raise ValueError('forward-only resume belongs to the separate maintenance endpoint')
        self.resume_existing=resume_existing
        self.listener=PipeListener(name,allowed_sids=allowed_sids)
        self.stop_event=threading.Event();self.slots=threading.BoundedSemaphore(16)
        self.workers=ThreadPoolExecutor(max_workers=16,thread_name_prefix='identity-control')

    def serve_once(self,timeout=1):
        self.broker.lease.check()
        channel=self.listener.accept(timeout)
        if channel is None:return
        if not self.slots.acquire(False):channel.close();return
        try:self.workers.submit(self._serve,channel)
        except BaseException:self.slots.release();channel.close();raise

    def serve_forever(self):
        try:
            while not self.stop_event.is_set():self.serve_once(.25)
        finally:
            self.listener.close();self.workers.shutdown(wait=True,cancel_futures=False)

    def stop(self):self.stop_event.set()

    def close(self):
        self.stop();self.listener.close();self.workers.shutdown(wait=True,cancel_futures=False)

    def _serve(self,channel):
        peer=None;connection=None
        try:
            # The kernel needs a client write/server read before impersonation.
            raw=channel.receive();peer=NativePipePeer(channel.handle)
            if self.maintenance_only:
                request=strict_json(raw)
                response=apply_change(self.broker,peer,request,external=self.maintenance,resume=self.resume_existing)
                channel.send(json.dumps(response,separators=(',',':')).encode('utf8'));return
            connection=self.broker.connect(peer)
            while not self.stop_event.is_set():
                request=strict_json(raw)
                if not isinstance(request,dict):raise AuthRejected('FORBIDDEN',403)
                kind=request.get('kind');b=self.broker
                methods={'identity_control.hello.request':b.hello,'identity_control.authenticate.request':b.authenticate,
                    'recovery.case.open.request':b.open_case,'recovery.proof.challenge.request':b.challenge,
                    'recovery.proof.verify.request':b.verify_proof,'recovery.issue.request':b.execute,'recovery.complete.request':b.execute}
                if kind=='recovery.status.request':response=b.execute(connection,request,status=True)
                elif kind in methods:response=methods[kind](connection,request)
                else:raise AuthRejected('FORBIDDEN',403)
                channel.send(json.dumps(response,separators=(',',':')).encode('utf8'))
                # Idle pipes use no database locks and are strictly bounded.
                raw=channel.receive(timeout=120)
        except Exception as error:
            try:channel.send(json.dumps(control_error(error),separators=(',',':')).encode('utf8'))
            except Exception:pass
        finally:
            if connection:self.broker.disconnect(connection)
            elif peer:peer.close()
            channel.close();self.slots.release()


class LocalControlClient:
    """A tool embeds this with protected prompts/FDs and an explicit plan signer.

    Server PID/SID pins come from trusted deployment configuration. Session
    evidence and signatures are exchanged only through these private handles;
    this client has no credential-file discovery, retries, logging or tracing.
    """
    def __init__(self,*,name,server_pid,server_sid,schema):
        self.channel=connect_pipe(name,expected_server_pid=server_pid,expected_server_sid=server_sid);self.schema=schema

    def exchange(self,request):
        raw=json.dumps(request,separators=(',',':'),ensure_ascii=True).encode('utf8')
        # Root validation rejects unknown properties and message kinds too.
        from jsonschema import Draft202012Validator,FormatChecker
        if not Draft202012Validator(self.schema.schema,format_checker=FormatChecker()).is_valid(request):raise AuthRejected('FORBIDDEN',403)
        self.channel.send(raw);response=strict_json(self.channel.receive())
        if not Draft202012Validator(self.schema.schema,format_checker=FormatChecker()).is_valid(response):raise AuthRejected('FORBIDDEN',403)
        return response

    def approve_authority(self,challenge,*,confirm_plan,signer):
        """No signing happens until the operator approves this exact plan."""
        self.schema.check('ChallengeBody',challenge)
        if confirm_plan(challenge['plan']) is not True:raise AuthRejected('FORBIDDEN',403)
        signature=signer('classroom.identity.recovery-proof.v1',challenge)
        return self.exchange(dict(protocol_version=1,kind='recovery.proof.verify.request',challenge_id=challenge['challenge_id'],evidence=dict(evidence_type='authority_signature',signature=signature)))

    def close(self):self.channel.close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
