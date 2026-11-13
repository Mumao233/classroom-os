"""PR-03b server use cases. Secrets never enter a generic success receipt."""
from datetime import datetime,timezone
from contextlib import contextmanager
from dataclasses import replace
import hmac
from uuid import UUID,uuid4
import pyotp

from ..domain.auth import AuthPolicy,AuthRejected,AuthReply
from ..domain.identity import Principal,normalize_email
from ..infrastructure.auth_crypto import digest,random_token,verify_receiver


def stamp(value):return datetime.fromisoformat(value.replace('Z','+00:00'))


class AuthService:
    def __init__(self,repository,credentials,password_policy,*,policy=AuthPolicy(),mail=None,hash_gate=None,control_deployment_id=None):
        self.repository=repository;self.crypto=credentials;self.password_policy=password_policy
        self.policy=policy;self.mail=mail;self.hash_gate=hash_gate
        self.control_deployment_id=control_deployment_id

    def _call(self,operation,payload,**options):return self.repository.call(operation,payload,**options)

    @staticmethod
    def _need(value):
        if value is None:raise AuthRejected()
        return value

    def budget(self,kind,value,increment=False):
        key=self.crypto.commitment('budget:'+kind,value)
        if not self._need(self._call('authentication_budget',dict(kind=kind,key=key,increment=increment)))['allowed']:
            raise AuthRejected('RATE_LIMITED',429)

    def _verify_password(self,row,password):
        if self.hash_gate is None:raise AuthRejected('SERVICE_DISABLED',503)
        return self.hash_gate(lambda:self.crypto.verify(row['password_hash'] if row else None,password))

    def _hash(self,password):
        if self.hash_gate is None:raise AuthRejected('SERVICE_DISABLED',503)
        return self.hash_gate(lambda:self.crypto.hash(password))

    @contextmanager
    def failure_budget(self,*budgets):
        """Count a credential attempt against each (kind, value) budget before verifying it.

        A check that precedes verification and an increment that follows it let every
        concurrent attempt pass the check. Reserving first closes that gap. The reservation
        is kept when the attempt fails as UNAUTHENTICATED and returned otherwise, so a
        success or an unrelated rejection leaves the count where it was. If the process
        dies in between, the reservation stays counted until the window expires.
        """
        held=[]
        def release():
            for kind,key,window in held:
                try:self._call('authentication_budget',dict(kind=kind,key=key,release=True,window=window))
                except Exception:pass
        try:
            for kind,value in budgets:
                key=self.crypto.commitment('budget:'+kind,value)
                reply=self._need(self._call('authentication_budget',dict(kind=kind,key=key,increment=True)))
                if not reply['allowed']:raise AuthRejected('RATE_LIMITED',429)
                held.append((kind,key,reply['window']))
            yield
        except AuthRejected as error:
            if error.code!='UNAUTHENTICATED':release()
            raise
        except BaseException:
            release();raise
        else:release()

    def check_current_password(self,row,password,source,*,account_reference=None):
        reference=account_reference if account_reference is not None else row['owner_id']
        with self.failure_budget(('account_fail',reference),('source_fail',source)):
            if not self._verify_password(row,password):raise AuthRejected()

    def context(self,ctx,*,touch=False):
        if ctx.session is None:raise AuthRejected()
        return self._need(self._call('validate_session',dict(token_hash=digest('session',ctx.session),touch=touch)))

    def ws_current(self,ctx):return self.context(ctx,touch=False)

    def principal(self,ctx):
        s=self.context(ctx,touch=False)
        return Principal(user_id=UUID(s['owner_id']),role=s['role'],security_epoch=s['epoch'],session_id=UUID(s['session_id']),
                         device_id=UUID(s['device_id']),mfa_level=s['mfa_level'],reauthenticated_at=None,expires_at=stamp(s['expires_at']))

    def require_csrf(self,ctx,kind):
        token=getattr(ctx,kind)
        if token is None or ctx.csrf is None or not hmac.compare_digest(self.crypto.csrf(kind,token),ctx.csrf):raise AuthRejected('FORBIDDEN',403)
        if kind=='session':return self.context(ctx)
        return self._need(self._call('protocol_context',dict(action='read',kind='auto_recovery' if kind=='recovery' else 'preauth',token_hash=digest(kind,token))))

    def csrf(self,ctx):
        clear_session=False;clear_recovery=False
        if ctx.recovery:
            try:
                c=self._need(self._call('protocol_context',dict(action='read',kind='auto_recovery',token_hash=digest('recovery',ctx.recovery))))
                return AuthReply(dict(csrf_token=self.crypto.csrf('recovery',ctx.recovery),expires_at=c['expires_at'],receiver_challenge=c['scope']['nonce']))
            except AuthRejected as error:
                if error.status!=401:raise
                clear_recovery=True;ctx=replace(ctx,recovery=None)
        if ctx.session:
            try:
                s=self.context(ctx)
                return AuthReply(dict(csrf_token=self.crypto.csrf('session',ctx.session),expires_at=s['expires_at']),clear_recovery=clear_recovery)
            except AuthRejected as error:
                if error.status!=401:raise
                clear_session=True;ctx=replace(ctx,session=None)
        if ctx.preauth:
            try:c=self._call('protocol_context',dict(action='read',kind='preauth',token_hash=digest('preauth',ctx.preauth)))
            except AuthRejected as error:
                if error.status!=401:raise
                c=None
            if c:return AuthReply(dict(csrf_token=self.crypto.csrf('preauth',ctx.preauth),expires_at=c['expires_at']),clear_session=clear_session,clear_recovery=clear_recovery)
        self.budget('preauth_source',ctx.source,True);self.budget('preauth_global','deployment',True)
        token=random_token();c=self._need(self._call('protocol_context',dict(action='create',id=str(uuid4()),token_hash=digest('preauth',token))))
        return AuthReply(dict(csrf_token=self.crypto.csrf('preauth',token),expires_at=c['expires_at']),preauth=token,clear_session=clear_session,clear_recovery=clear_recovery)

    def _attest(self,row,mode,*,binding=None,code=None):
        payload=dict(owner_id=row['owner_id'],epoch=row['epoch'],password_hash=row['password_hash'],mode=mode,
                     proof_id=str(uuid4()),proof_hash=digest('verified',random_token()),binding=binding)
        if mode not in ('login','automatic') and (row['factor_id'] or row['role']=='admin'):
            if not row['factor_id']:raise AuthRejected('MFA_REQUIRED',403)
            seed=self.crypto.open(row['owner_id'],row['factor_id'],row['encrypted_seed'])
            payload.update(factor_id=row['factor_id'],encrypted_seed=row['encrypted_seed'],step=self.crypto.totp_step(seed,code,stamp(row['now'])))
        result=self._need(self._call('verify_factor' if mode=='login_mfa' else 'verify_auth',payload,verifier=True))
        return payload['proof_hash'],result

    def login(self,ctx,body):
        self.require_csrf(ctx,'preauth');email=normalize_email(body['email'])
        row=self._call('locate_auth',dict(lookup='email',email=email),verifier=True)
        self.check_current_password(row,body['password'],ctx.source,account_reference=row['owner_id'] if row else email)
        proof,_=self._attest(row,'login')
        return self._establish(ctx,body,proof)

    def _establish(self,ctx,body,proof,challenge_id=None):
        token=random_token();payload=dict(proof_hash=proof,preauth_hash=digest('preauth',ctx.preauth),session_hash=digest('session',token),
            session_id=str(uuid4()),device_id=str(uuid4()),device_name=body['device_name'],remember_me=body['remember_me'],
            audit_id=str(uuid4()),challenge_new_id=str(uuid4()),challenge_hash=digest('challenge',random_token()),nonce=random_token())
        if challenge_id:payload['challenge_id']=challenge_id
        value=self._need(self._call('establish_session',payload))
        if value['state']=='mfa_required':
            return AuthReply(dict(state='mfa_required',challenge_id=value['challenge_id'],user=None,device_id=None,csrf_token=None,receiver_challenge=value['receiver_challenge']))
        s=self._need(self._call('validate_session',dict(token_hash=payload['session_hash'])))
        persistent=30*86400 if s['role']=='student' and body['remember_me'] else None
        return AuthReply(dict(state='authenticated',challenge_id=None,user=s['user'],device_id=s['device_id'],csrf_token=self.crypto.csrf('session',token)),session=token,persistent_seconds=persistent)

    def login_mfa(self,ctx,body,key):
        self.require_csrf(ctx,'preauth')
        row=self._need(self._call('locate_auth',dict(lookup='login_mfa',challenge_id=body['challenge_id'],preauth_hash=digest('preauth',ctx.preauth)),verifier=True))
        with self.failure_budget(('mfa_fail',row['owner_id'])):
            if body['factor_type']=='totp':
                proof,_=self._attest(row,'login_mfa',code=body['code'])
                return self._establish(ctx,row['scope'],proof,body['challenge_id'])
            verify_receiver(body['receiver_public_key'],body['receiver_proof'],method='POST',path='/api/web/v1/auth/login/mfa',key=key,
                            kind='login_challenge',context=body['challenge_id'],challenge=row['scope']['nonce'],body=body)
            token=random_token();nonce=random_token()
            value=self._need(self._call('redeem_auto_recovery',dict(action='begin',challenge_id=body['challenge_id'],preauth_hash=digest('preauth',ctx.preauth),
                nonce=row['scope']['nonce'],code_hash=digest('recovery_code',body['code']),flow_id=str(uuid4()),recovery_hash=digest('recovery',token),
                next_nonce=nonce,receiver=body['receiver_public_key'],audit_id=str(uuid4()))))
            recovery=dict(flow_id=value['flow_id'],scope='mfa_rebind',state='pending',expires_at=value['expires_at'],csrf_token=self.crypto.csrf('recovery',token),receiver_challenge=nonce)
            return AuthReply(dict(state='recovery_required',challenge_id=None,user=None,device_id=None,csrf_token=None,recovery=recovery),recovery=token)

    def session(self,ctx):
        s=self.context(ctx)
        return AuthReply(dict(user=s['user'],device_id=s['device_id'],session_id=s['session_id'],csrf_token=self.crypto.csrf('session',ctx.session)))

    def logout(self,ctx):
        if ctx.session is None and ctx.recovery is not None:
            # Leaving a pending automatic recovery: without this the HttpOnly recovery
            # cookie keeps the browser in recovery scope until the flow expires.
            self.require_csrf(ctx,'recovery')
            self._need(self._call('protocol_context',dict(action='abandon',kind='auto_recovery',token_hash=digest('recovery',ctx.recovery))))
            return AuthReply({},clear_recovery=True)
        self.require_csrf(ctx,'session')
        self._need(self._call('revoke_session',dict(token_hash=digest('session',ctx.session),audit_id=str(uuid4()))))
        return AuthReply({},clear_session=True)

    def reauthenticate(self,ctx,body):
        s=self.require_csrf(ctx,'session');operation=body['operation'];purpose=operation['purpose']
        # Unsupported cross-package/control operations remain explicitly closed.
        if purpose in ('trustDevice','deleteAccount') or (purpose.startswith('student_recovery_') and self.control_deployment_id is None):raise AuthRejected('SERVICE_DISABLED',503)
        row=self._need(self._call('locate_auth',dict(lookup='session',token_hash=digest('session',ctx.session)),verifier=True))
        self.check_current_password(row,body['password'],ctx.source)
        commitment=self.crypto.commitment('operation',operation)
        needs_mfa=bool(row['factor_id'] or row['role']=='admin')
        with self.failure_budget(*([('mfa_fail',row['owner_id'])] if needs_mfa else [])):
            proof,_=self._attest(row,'reauth',binding=commitment,code=body.get('mfa_code'))
        if purpose.startswith('student_recovery_'):
            token=random_token();value=self._need(self._call('issue_control_reauth',dict(token_hash=digest('session',ctx.session),proof_hash=proof,id=str(uuid4()),
                reauth_hash=digest('reauth',token),purpose=purpose,case_id=operation['target']['id'],deployment_id=self.control_deployment_id,commitment=commitment,
                op_id=operation['candidate_parameters']['op_id'],plan_hash=operation['candidate_parameters']['plan_hash'])))
            return AuthReply(dict(reauth_token=token,expires_at=value['expires_at']))
        token=random_token();value=self._need(self._call('issue_reauth',dict(token_hash=digest('session',ctx.session),proof_hash=proof,id=str(uuid4()),reauth_hash=digest('reauth',token),
            purpose=purpose,target_kind=operation['target']['kind'],target_id=operation['target']['id'],commitment=commitment,operation_key=operation['candidate_parameters']['idempotency_key'])))
        return AuthReply(dict(reauth_token=token,expires_at=value['expires_at']))

    def change(self,ctx,body,key,purpose,*,target_id=None):
        s=self.require_csrf(ctx,'session')
        target_id=target_id or (body['enrollment_id'] if purpose=='confirmMfa' else s['owner_id'])
        kind={'revokeSession':'session','revokeDevice':'device','confirmMfa':'mfa_enrollment'}.get(purpose,'account')
        operation=dict(purpose=purpose,target=dict(kind=kind,id=target_id),candidate_parameters=dict(idempotency_key=key,body=body))
        if not ctx.reauth:raise AuthRejected('REAUTH_REQUIRED',403)
        payload=dict(action=purpose,target_id=target_id,token_hash=digest('session',ctx.session),reauth_hash=digest('reauth',ctx.reauth),
                     operation_key=key,commitment=self.crypto.commitment('operation',operation),audit_id=str(uuid4()))
        approval=None;secret_codes=None;seed=None
        if purpose=='changePassword':
            approval=self.password_policy.validate(body['new_password'],s['role']);payload['new_hash']=self._hash(body['new_password'])
        elif purpose=='requestEmailChange':
            if self.mail is None:raise AuthRejected('SERVICE_DISABLED',503)
            email=normalize_email(body['new_email']);token=random_token()
            action_id=str(uuid4());payload.update(new_email=email,action_id=action_id,action_hash=digest('email_action',token),encrypted_mail=self.mail.seal(action_id,email,token,'change_email'))
        elif purpose=='enrollMfa':
            seed=self.crypto.new_seed();fid=str(uuid4());payload.update(factor_id=fid,encrypted_seed=self.crypto.seal(s['owner_id'],fid,seed))
        elif purpose=='confirmMfa':
            row=self._need(self._call('locate_auth',dict(lookup='session',token_hash=digest('session',ctx.session),factor_id=body['enrollment_id']),verifier=True))
            if not row['factor_id']:raise AuthRejected()
            pending=self.crypto.open(row['owner_id'],row['factor_id'],row['encrypted_seed'])
            payload.update(encrypted_seed=row['encrypted_seed'],step=self.crypto.totp_step(pending,body['code'],stamp(row['now'])))
        if purpose in ('confirmMfa','rotateRecoveryCodes'):
            secret_codes=[random_token() for _ in range(10)];payload.update(group_id=str(uuid4()),codes=[dict(id=str(uuid4()),hash=digest('recovery_code',c)) for c in secret_codes])
        value=self._need(self._call('apply_account_change',payload,before_commit=(lambda:self.password_policy.current(approval)) if approval else None))
        if purpose=='enrollMfa':
            return AuthReply(dict(enrollment_id=value['enrollment_id'],expires_at=value['expires_at'],otpauth_uri=pyotp.TOTP(seed).provisioning_uri(s['user']['email'],issuer_name='Classroom OS')))
        if secret_codes:return AuthReply(dict(codes=secret_codes,shown_once=True),clear_session=purpose=='confirmMfa')
        return AuthReply({},clear_session=purpose in ('changePassword','logoutAll','disableMfa') or (purpose=='revokeDevice' and target_id==s['device_id']))

    def email_action(self,ctx,body,action,key=None):
        self.require_csrf(ctx,'preauth');approval=None
        if action=='forgot':
            if self.mail is None or key is None:raise AuthRejected('SERVICE_DISABLED',503)
            email=normalize_email(body['email']);token=random_token()
            action_id=str(uuid4())
            # One transaction decides replay, quota and enqueue: a resubmission with the same
            # context, key and body returns the first acknowledgement without spending the
            # hourly mail quota or issuing another token.
            self._need(self._call('queue_auth_mail',dict(id=action_id,email=email,action_hash=digest('email_action',token),encrypted_mail=self.mail.seal(action_id,email,token,'reset'),
                context_hash=digest('preauth',ctx.preauth),operation_key=key,commitment=self.crypto.commitment('operation',dict(purpose='forgotPassword',email=email)),
                budget_key=self.crypto.commitment('budget:mail',email))))
            return AuthReply({})
        payload=dict(action=action,action_hash=digest('email_action',body['token']),audit_id=str(uuid4()))
        if action=='reset':
            row=self._need(self._call('locate_auth',dict(lookup='reset',token_hash=payload['action_hash']),verifier=True))
            approval=self.password_policy.validate(body['new_password'],row['role']);payload['new_hash']=self._hash(body['new_password'])
        self._need(self._call('manage_email_action',payload,before_commit=(lambda:self.password_policy.current(approval)) if approval else None))
        return AuthReply({},clear_session=True)

    def automatic(self,ctx,body,key,action):
        c=self.require_csrf(ctx,'recovery')
        if c['id']!=body['flow_id']:raise AuthRejected()
        row=self._need(self._call('locate_auth',dict(lookup='auto_recovery',token_hash=digest('recovery',ctx.recovery),**({'factor_id':body['enrollment_id']} if action=='confirm' else {})),verifier=True))
        self.check_current_password(row,body['password'],ctx.source)
        verify_receiver(c['scope']['receiver'],body['receiver_proof'],method='POST',path='/api/web/v1/auth/recovery/'+action,key=key,
                        kind='automatic_recovery',context=body['flow_id'],challenge=c['scope']['nonce'],body=body)
        binding=self.crypto.commitment('automatic:'+action,dict(body=body,key=key));proof,_=self._attest(row,'automatic',binding=binding)
        nonce=random_token();payload=dict(action=action,flow_id=body['flow_id'],token_hash=digest('recovery',ctx.recovery),proof_hash=proof,nonce=c['scope']['nonce'],
                                         next_nonce=nonce,binding=binding,operation_key=key,audit_id=str(uuid4()))
        if action=='enroll':
            seed=self.crypto.new_seed();fid=str(uuid4());payload.update(factor_id=fid,encrypted_seed=self.crypto.seal(row['owner_id'],fid,seed))
        else:
            if not row['factor_id']:raise AuthRejected()
            seed=self.crypto.open(row['owner_id'],row['factor_id'],row['encrypted_seed'])
            payload.update(factor_id=body['enrollment_id'],encrypted_seed=row['encrypted_seed'],step=self.crypto.totp_step(seed,body['code'],stamp(row['now'])))
        value=self._need(self._call('redeem_auto_recovery',payload))
        if action=='confirm':return AuthReply(value,clear_recovery=True,clear_session=True)
        return AuthReply(dict(flow_id=body['flow_id'],enrollment_id=value['enrollment_id'],expires_at=value['expires_at'],receiver_challenge=nonce,
                              otpauth_uri=pyotp.TOTP(seed).provisioning_uri('Account',issuer_name='Classroom OS')))

    def list_contexts(self,ctx,kind):
        return AuthReply(self._need(self._call('list_security_context',dict(token_hash=digest('session',ctx.session),kind=kind))))

    def ws_ticket(self,ctx,body):
        self.require_csrf(ctx,'session');token=random_token()
        value=self._need(self._call('manage_ws_ticket',dict(action='issue',token_hash=digest('session',ctx.session),id=str(uuid4()),ticket_hash=digest('ws',token),scope=body)))
        return AuthReply(dict(ticket=token,expires_at=value['expires_at'],path='/api/web/v1/ws/'+body['channel'],subprotocol='classroom.v1'))

    def ws_authenticate(self,ctx,ticket,channel,first_deadline):
        return self._need(self._call('manage_ws_ticket',dict(action='consume',token_hash=digest('session',ctx.session),ticket_hash=digest('ws',ticket),channel=channel,first_deadline=first_deadline)))

    def control_bridge(self,*,session_token,proof_token,plan):
        return self._need(self._call('verify_control_bridge',dict(token_hash=digest('session',session_token),reauth_hash=digest('reauth',proof_token),
            deployment_id=plan['deployment_id'],case_id=plan['case_id'],op_id=plan['op_id'],action=plan['action'],plan_hash=self.crypto_control_hash(plan))))

    @staticmethod
    def crypto_control_hash(plan):
        from ..infrastructure.recovery_authority import control_hash
        return control_hash(plan)
