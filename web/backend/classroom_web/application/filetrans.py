"""Existing browser contract for explicitly composed synthetic Filetrans."""
from uuid import UUID

from ..domain.auth import AuthRejected, AuthReply
from ..domain.billing import BillingBlocked, QuotaExceeded
from ..domain.data import Gone, InactiveOwner, NotFound, RevisionConflict
from ..domain.filetrans import KIND
from ..domain.identity import AuthorizationDenied
from ..domain.tasks import IdempotencyMismatch, ResyncRequired, ResultConflict, StaleFence, UnsafeRetry
from ..infrastructure.filetrans_repository import OtherGeneration, PgFiletransRepository
from ..infrastructure.qa_repository import _stamp
from ..infrastructure.transcript_repository import PgTranscriptRepository
from .media import identifier
from .transcripts import read_transcripts


PATHS = frozenset((
    '/class-sessions/{class_session_id}/generation-plans',
    '/generation-plans/{plan_id}', '/generation-plans/{plan_id}/execute',
    '/tasks/{task_id}', '/tasks/{task_id}/cancel', '/tasks/{task_id}/retry',
    '/tasks/{task_id}/reconcile', '/class-sessions/{class_session_id}/transcript-versions',
    '/transcript-versions/{version_id}/segments', '/class-sessions/{class_session_id}/adopt-transcript',
    '/class-sessions/{class_session_id}',
))
OPERATIONS = frozenset(('prepareGeneration', 'getGenerationPlan', 'executeGeneration',
    'getTask', 'cancelTask', 'retryTask', 'reconcileTask', 'listTranscriptVersions',
    'listTranscriptSegments', 'adoptTranscriptVersion', 'getClassSession'))
WRITES = frozenset(('prepareGeneration', 'executeGeneration', 'retryTask', 'cancelTask',
                    'reconcileTask', 'adoptTranscriptVersion'))


class FiletransService:
    def __init__(self, capture, *, price_id, lifecycle, enabled=False):
        if not isinstance(price_id, UUID) or type(enabled) is not bool or lifecycle is None:
            raise ValueError('explicit synthetic Filetrans price, gate and billing lifecycle required')
        self.capture, self.auth, self.database = capture, capture.auth, capture.database
        self.price_id, self.lifecycle, self.enabled = price_id, lifecycle, enabled

    def require_ready(self, *, new=False):
        if (not getattr(self.capture.store, 'synthetic_loopback', False)
                or self.capture.deletion is None or not self.capture.deletion.ready
                or (new and not self.enabled)):
            raise AuthRejected('SERVICE_DISABLED', 503)
        if new:
            self.capture.available()

    def dispatch(self, operation, ctx, body, key, params):
        if operation not in OPERATIONS or (operation == 'prepareGeneration' and body['kind'] != KIND):
            raise OtherGeneration()
        try:
            if operation in WRITES:
                self.auth.require_csrf(ctx, 'session')
            principal = self.auth.principal(ctx)
            op_id = identifier(key) if key else None
            task_for_accounting = None
            with self.database.authorized_transaction(principal, activity=True) as tx:
                repo = PgFiletransRepository(tx)
                if operation in ('getGenerationPlan', 'executeGeneration'):
                    plan = repo.get_plan(identifier(params['plan_id']))
                    self.require_ready(new=operation == 'executeGeneration' and plan['consumed_task_id'] is None)
                    value = (repo.plan_view(plan) if operation == 'getGenerationPlan'
                             else repo.execute_plan(plan, body, op_id, principal, self.price_id))
                elif 'task_id' in params:
                    task_id = identifier(params['task_id'])
                    run = repo.run(task_id)
                    self.require_ready(new=operation == 'retryTask')
                    if operation == 'retryTask':
                        value = repo.retry(run, body, op_id, principal, self.price_id)
                    elif operation == 'cancelTask':
                        tx.tasks.cancel(task_id, op_id)
                        task_for_accounting = task_id
                        value = repo.task_view(task_id)
                    elif operation == 'reconcileTask':
                        task = tx.tasks.get(task_id)
                        task_for_accounting = task_id
                        value = dict(state=task.state, provider_query_supported=False,
                                     checked_at=_stamp(repo.now()), new_submission=False)
                    else:
                        value = repo.task_view(task_id)
                else:
                    self.require_ready(new=operation == 'prepareGeneration')
                    if operation == 'prepareGeneration':
                        value = repo.prepare(identifier(params['class_session_id']), body, op_id, self.price_id)
                    elif operation in ('listTranscriptVersions', 'listTranscriptSegments'):
                        value = read_transcripts(tx, operation, params)
                    else:
                        transcripts = PgTranscriptRepository(tx)
                        class_id = identifier(params['class_session_id'])
                        if operation == 'adoptTranscriptVersion':
                            value = transcripts.adopt(class_id, body, op_id)
                        else:
                            classroom = transcripts.classroom(class_id)
                            if 'revision' in params and params['revision'] != classroom.revision:
                                raise RevisionConflict(classroom.revision)
                            value = transcripts.class_view(classroom)
            if task_for_accounting is not None:
                self.lifecycle.synchronize(principal.user_id, task_for_accounting)
            return AuthReply(value)
        except NotFound:
            raise AuthRejected('NOT_FOUND', 404) from None
        except Gone:
            raise AuthRejected('GONE', 410) from None
        except (AuthorizationDenied, InactiveOwner):
            raise AuthRejected() from None
        except QuotaExceeded:
            raise AuthRejected('QUOTA_EXCEEDED', 409) from None
        except BillingBlocked:
            raise AuthRejected('BUDGET_BLOCKED', 409) from None
        except IdempotencyMismatch:
            raise AuthRejected('IDEMPOTENCY_MISMATCH', 409) from None
        except ResyncRequired:
            raise AuthRejected('CURSOR_EXPIRED', 409) from None
        except RevisionConflict as conflict:
            error = AuthRejected('REVISION_CONFLICT', 409)
            error.current_revision = conflict.current_revision
            raise error from None
        except (StaleFence, UnsafeRetry, ResultConflict):
            raise AuthRejected('REVISION_CONFLICT', 409) from None
        except (TypeError, ValueError):
            raise AuthRejected('VALIDATION_FAILED', 422) from None
