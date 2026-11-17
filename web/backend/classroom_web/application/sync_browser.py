"""Authenticated sync entry points, with an independent transaction per operation."""
from uuid import UUID

from ..domain.auth import AuthRejected, AuthReply
from ..domain.data import Gone, InactiveOwner, NotFound, RevisionConflict
from ..domain.identity import AuthorizationDenied
from ..domain.qa import QaRejected
from ..domain.tasks import IdempotencyMismatch, ResyncRequired
from ..infrastructure.qa_repository import _hash
from ..infrastructure.sync_repository import PgSyncRepository


SYNC_PATHS = frozenset(('/sync/operations', '/sync/changes', '/sync/bootstrap',
    '/operations/{op_id}', '/sync/conflicts/{conflict_id}', '/sync/conflicts/{conflict_id}/resolve',
    '/me/preferences', '/marks/{mark_id}', '/class-sessions/{class_session_id}/marks'))


class SyncBrowser:
    def __init__(self, *, auth, database, enabled=False):
        if enabled and (not isinstance(database.cursor_secret,bytes) or len(database.cursor_secret)<32):
            raise ValueError('sync requires a stable server cursor secret of at least 32 bytes')
        self.auth, self.database, self.enabled = auth, database, enabled

    def dispatch(self, operation, ctx, body, key, params):
        try:
            if not self.enabled:
                raise AuthRejected('SERVICE_DISABLED', 503)
            if operation in ('syncOperations', 'resolveConflict', 'createMark', 'updateMark', 'updatePreferences'):
                self.auth.require_csrf(ctx, 'session')
            principal = self.auth.principal(ctx)
            if operation == 'syncOperations':
                return AuthReply(self.batch(principal, body, UUID(key)))
            snapshot = operation in ('bootstrapSync', 'getChanges')
            with self.database.authorized_transaction(principal, activity=not snapshot,
                    isolation_level='REPEATABLE READ' if snapshot else 'READ COMMITTED') as tx:
                repo = PgSyncRepository(tx)
                if operation == 'bootstrapSync':
                    value = repo.bootstrap()
                elif operation == 'getChanges':
                    value = repo.changes(params.get('after'), params.get('limit', 100))
                elif operation == 'getOperationReceipt':
                    value = repo.receipt(params['operation'], UUID(params['op_id']))
                elif operation == 'getConflict':
                    value = repo.conflict(UUID(params['conflict_id']))
                elif operation == 'resolveConflict':
                    value = repo.resolve(UUID(params['conflict_id']), body, UUID(key))
                elif operation == 'getPreferences':
                    value = repo.preferences()
                    if params.get('revision', value['revision']) != value['revision']:
                        raise RevisionConflict(value['revision'])
                elif operation == 'getMark':
                    value = repo.mark(UUID(params['mark_id']))
                    if params.get('revision', value['revision']) != value['revision']:
                        raise RevisionConflict(value['revision'])
                elif operation == 'listMarks':
                    value = repo.marks(UUID(params['class_session_id']), params.get('cursor'), params.get('limit',50))
                elif operation == 'createMark':
                    value = repo.create_mark(UUID(params['class_session_id']),body,UUID(key))
                elif operation in ('updateMark', 'updatePreferences'):
                    kind = 'mark' if operation == 'updateMark' else 'preferences'
                    target = params['mark_id'] if kind == 'mark' else str(principal.user_id)
                    command = dict(op_id=key,object_type=kind,object_id=target,draft_id=key,
                                   base_revision=body['base_revision'],payload=body)
                    receipt = repo.apply(command, operation_name=operation)
                    value = repo.mark(UUID(target)) if kind=='mark' else repo.preferences()
                else:
                    raise AuthRejected('SERVICE_DISABLED', 503)
            if operation in ('updateMark','updatePreferences') and receipt['status'] != 'applied':
                rejection = AuthRejected('REVISION_CONFLICT',409)
                rejection.conflict_id = receipt['conflict_id']
                rejection.current_revision = receipt['revision']
                raise rejection
            return AuthReply(value)
        except QaRejected as error:
            raise AuthRejected(error.code, error.status) from None
        except Gone:
            raise AuthRejected('GONE', 410) from None
        except NotFound:
            raise AuthRejected('NOT_FOUND', 404) from None
        except (AuthorizationDenied, InactiveOwner):
            raise AuthRejected() from None
        except IdempotencyMismatch:
            raise AuthRejected('IDEMPOTENCY_MISMATCH', 409) from None
        except ResyncRequired:
            raise AuthRejected('CURSOR_EXPIRED', 409) from None
        except RevisionConflict as error:
            rejection = AuthRejected('REVISION_CONFLICT', 409)
            rejection.current_revision = error.current_revision
            raise rejection from None
        except (ValueError, TypeError, KeyError):
            raise AuthRejected('VALIDATION_FAILED', 422) from None

    def batch(self, principal, body, key):
        digest = _hash(body)
        with self.database.authorized_transaction(principal, activity=True) as tx:
            old = tx.receipts.lock_and_get('syncOperations', key, digest)
            if old is None:
                PgSyncRepository(tx).review.sources._remember('syncOperations', key, digest, 'sync_batch', key, 200)
        receipts = []
        for operation in body['operations']:
            # Commit independently so a later rejected operation or lost response
            # cannot roll back an acknowledged predecessor.
            try:
                with self.database.authorized_transaction(principal, activity=True) as tx:
                    result = PgSyncRepository(tx).apply(operation)
            except (NotFound, QaRejected, RevisionConflict) as error:
                if isinstance(error,QaRejected) and error.status >= 500:
                    raise
                # Schema-valid domain rejections are durable per-operation facts.
                # Mismatched existing keys remain a top-level 409 and never rewrite
                # an already committed receipt.
                with self.database.authorized_transaction(principal, activity=True) as tx:
                    repo = PgSyncRepository(tx)
                    inner_key = UUID(operation['op_id'])
                    inner_hash = _hash(operation)
                    old = tx.receipts.lock_and_get('syncOperation', inner_key, inner_hash)
                    result = repo.receipt('syncOperation', inner_key) if old else repo.remember(
                        'syncOperation',inner_key,inner_hash,operation['object_type'],UUID(operation['object_id']),None,'rejected')
            receipts.append(result)
        with self.database.authorized_transaction(principal) as tx:
            cursor = tx.events.watermark()
        return dict(receipts=receipts, cursor=cursor)
