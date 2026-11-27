"""Session-bound HTTP use cases, including the explicit admin contract candidate."""
from uuid import UUID, uuid4

from sqlalchemy import text

from ..domain.auth import AuthRejected, AuthReply
from ..domain.billing import BillingBlocked
from ..domain.data import NotFound, Gone, InactiveOwner
from ..domain.identity import AuthorizationDenied, normalize_email
from ..domain.tasks import IdempotencyMismatch
from ..infrastructure.auth_crypto import digest, random_token
from ..infrastructure.billing_repository import PgBillingRepository
from .identity import token_digest


class BillingBrowser:
    def __init__(self, auth, database, lifecycle):
        self.auth, self.database, self.lifecycle = auth, database, lifecycle

    def quota(self, ctx):
        try:
            with self.database.authorized_transaction(self.auth.principal(ctx)) as tx:
                return AuthReply(tx.billing.quota())
        except AuthorizationDenied:
            raise AuthRejected() from None
        except BillingBlocked:
            raise AuthRejected('STORAGE_UNAVAILABLE', 503) from None

    def cancel(self, ctx, task_id, key):
        self.auth.require_csrf(ctx, 'session')
        principal = self.auth.principal(ctx)
        try:
            with self.database.authorized_transaction(principal) as tx:
                tx.tasks.get(UUID(task_id))
                if tx.billing.for_task(UUID(task_id)) is None:
                    raise AuthRejected('SERVICE_DISABLED', 503)
                task = tx.tasks.cancel(UUID(task_id), UUID(key))
            self.lifecycle.synchronize(principal.user_id, task.id)
            with self.database.authorized_transaction(self.auth.principal(ctx)) as tx:
                task = tx.tasks.get(task.id)
                attempt = tx.tasks._attempt(task.id)
                reservation = tx.billing.for_task(task.id)
                if reservation is None:
                    raise AuthRejected('STORAGE_UNAVAILABLE', 503)
                value = {name: getattr(task, name) for name in ('revision', 'kind', 'state', 'error_code', 'may_have_charged')}
                for name in ('id', 'class_session_id', 'parent_task_id'):
                    item = getattr(task, name)
                    value[name] = str(item) if item else None
                value.update(created_at=task.created_at.isoformat(), updated_at=task.updated_at.isoformat(),
                    attempt_id=str(attempt.id) if attempt else None, completed_units=0, total_units=None,
                    result_id=None, source_snapshot_id=None,
                    provider_outcome=attempt.provider_outcome if attempt else 'not_submitted',
                    maximum_cost_microyuan=str(reservation['reserved_microyuan']),
                    settled_cost_microyuan=str(reservation['settled_microyuan']) if reservation and reservation['settled_microyuan'] is not None else None)
                return AuthReply(value)
        except (NotFound, Gone):
            raise AuthRejected('NOT_FOUND', 404) from None
        except (InactiveOwner, AuthorizationDenied):
            raise AuthRejected() from None
        except IdempotencyMismatch:
            raise AuthRejected('IDEMPOTENCY_MISMATCH', 409) from None
        except ValueError:
            raise AuthRejected('VALIDATION_FAILED', 422) from None


class BillingAdmin:
    TARGET_KINDS = {
        'adminUpdateSwitches': 'account',
        'adminAdjustQuota': 'account',
        'adminCreateInvitation': 'account',
        'adminUpdateUser': 'user',
        'adminRevokeInvitation': 'invitation',
        'adminReissueInvitation': 'invitation',
    }
    PURPOSES = frozenset(TARGET_KINDS)

    def __init__(self, auth, database, repository):
        self.auth, self.database, self.repository = auth, database, repository

    def require_current_schema(self):
        self.repository.require_current_schema()

    def _principal(self, ctx, *, write=False):
        if write:
            self.auth.require_csrf(ctx, 'session')
        principal = self.auth.principal(ctx)
        if principal.role != 'admin' or principal.mfa_level != 'totp':
            raise AuthRejected('FORBIDDEN', 403)
        return principal

    def validate_operation(self, owner_id, operation):
        target = operation['target']
        purpose = operation['purpose']
        if (target['kind'] != self.TARGET_KINDS[purpose]
                or (target['kind'] == 'account' and target['id'] != str(owner_id))):
            raise AuthRejected('FORBIDDEN', 403)
        body = operation['candidate_parameters']['body']
        if purpose == 'adminCreateInvitation' and body['kind'] == 'email_bound':
            # Registration applies IDNA and local-part rules beyond the JSON email format.
            normalize_email(body['email'])

    def issue_reauth(self, ctx, operation, proof_hash):
        self._principal(ctx, write=True)
        token = random_token()
        value = self.repository.call('issue_admin_proof', dict(token_hash=digest('session', ctx.session),
            proof_hash=proof_hash, id=str(uuid4()), reauth_hash=digest('reauth', token), purpose=operation['purpose'],
            target_id=operation['target']['id'], commitment=self.auth.crypto.commitment('operation', operation),
            operation_key=operation['candidate_parameters']['idempotency_key']))
        return AuthReply(dict(reauth_token=token, expires_at=value['expires_at']))

    def read(self, ctx, kind, period_start=None):
        self._principal(ctx)
        return AuthReply(self.repository.call('read_admin', dict(token_hash=digest('session', ctx.session),
            kind=kind, period_start=period_start)))

    def _quota(self, ctx, owner_id):
        principal = self._principal(ctx)
        with self.database.authorized_transaction(principal) as tx:
            tx.connection.execute(text("SELECT set_config('app.owner_id',:owner,true)"), {'owner': str(owner_id)})
            if not tx.connection.scalar(text('SELECT classroom_web_owner_active()')):
                raise AuthRejected('NOT_FOUND', 404)
            tx.connection.execute(text('SELECT classroom_web_lock_owner()'))
            from types import SimpleNamespace
            scoped = SimpleNamespace(connection=tx.connection, catalog=SimpleNamespace(owner_id=owner_id))
            repository = PgBillingRepository(scoped)
            return repository.quota()

    def user_quota(self, ctx, user_id):
        try:
            owner_id = UUID(user_id)
        except ValueError:
            raise AuthRejected('VALIDATION_FAILED', 422) from None
        return AuthReply(self._quota(ctx, owner_id))

    def apply(self, ctx, purpose, body, key, *, target_id=None):
        principal = self._principal(ctx, write=True)
        if not ctx.reauth:
            raise AuthRejected('REAUTH_REQUIRED', 403)
        if not body.get('reason', '').strip():
            raise AuthRejected('VALIDATION_FAILED', 422)
        kind = self.TARGET_KINDS[purpose]
        target_id = str(principal.user_id) if kind == 'account' else target_id
        operation = dict(purpose=purpose, target=dict(kind=kind, id=target_id),
                         candidate_parameters=dict(idempotency_key=key, body=body))
        payload = dict(token_hash=digest('session', ctx.session), reauth_hash=digest('reauth', ctx.reauth),
            purpose=purpose, target_id=target_id, body=body, operation_key=key,
            commitment=self.auth.crypto.commitment('operation', operation))
        token = None
        if purpose in ('adminCreateInvitation', 'adminReissueInvitation'):
            token = random_token()
            payload['invitation_hash'] = token_digest(token).hex()
            if purpose == 'adminCreateInvitation' and body['kind'] == 'email_bound':
                payload['email_binding'] = normalize_email(body['email'])
        result = self.repository.call('apply_admin', payload)
        if token is not None:
            return AuthReply(dict(invitation=result, one_time_token=token))
        return AuthReply(self._quota(ctx, UUID(result['user_id'])) if purpose == 'adminAdjustQuota' else result)
