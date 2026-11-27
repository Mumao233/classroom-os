"""Authenticated search, notification and feedback use cases."""
from uuid import UUID

from ..domain.auth import AuthRejected, AuthReply
from ..domain.data import Gone, NotFound, RevisionConflict
from ..domain.identity import AuthorizationDenied
from ..domain.tasks import IdempotencyMismatch
from ..infrastructure.operations_repository import PgOperationsRepository
from .media import identifier


class OperationsService:
    def __init__(self, *, auth, database):
        self.auth = auth
        self.database = database

    def watermark(self, ctx):
        principal = self.auth.principal(ctx)
        with self.database.authorized_transaction(principal) as tx:
            return tx.events.watermark(), 0

    def poll(self, ctx, cursor, _partial_cursor):
        principal = self.auth.principal(ctx)
        with self.database.authorized_transaction(principal) as tx:
            page = tx.events.replay(cursor)
            events = PgOperationsRepository(tx).visible_notification_events(page.events)
            return page.cursor, 0, events, [], len(page.events) < 100

    def dispatch(self, operation, ctx, body, key, params, readers):
        if key is not None:
            self.auth.require_csrf(ctx, 'session')
        principal = self.auth.principal(ctx)
        try:
            with self.database.authorized_transaction(principal, activity=operation != 'listNotifications') as tx:
                repo = PgOperationsRepository(tx)
                if operation == 'search':
                    return AuthReply(repo.search(params, readers))
                if operation == 'listNotifications':
                    return AuthReply(repo.notifications(params))
                op_id = UUID(key)
                if op_id.version != 4:
                    raise AuthRejected('VALIDATION_FAILED', 422)
                if operation == 'readNotification':
                    target = identifier(params['notification_id'])
                    digest = self.auth.crypto.commitment('pr11c:notification', str(target))
                    return AuthReply(repo.read_notification(target, op_id, digest))
                if operation != 'submitFeedback':
                    raise RuntimeError('Unknown operations route')
                if body['confirmed_excerpt'] and not body['confirm_send_excerpt']:
                    raise AuthRejected('CONSENT_REQUIRED', 422)
                if not body['description'].strip() or any(
                        '\x00' in body[name] for name in ('description', 'build_id', 'confirmed_excerpt')
                        if body[name] is not None):
                    raise AuthRejected('VALIDATION_FAILED', 422)
                digest = self.auth.crypto.commitment('pr11c:feedback', body)
                return AuthReply(repo.feedback(body, op_id, digest))
        except (Gone, NotFound):
            raise AuthRejected('NOT_FOUND', 404) from None
        except RevisionConflict:
            raise AuthRejected('REVISION_CONFLICT', 409) from None
        except AuthorizationDenied:
            raise AuthRejected() from None
        except IdempotencyMismatch:
            raise AuthRejected('IDEMPOTENCY_MISMATCH', 409) from None
