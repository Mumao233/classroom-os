"""Session-bound PR-07a operations using the existing authenticated UoW."""
from copy import deepcopy
from uuid import UUID

from sqlalchemy import text

from ..domain.auth import AuthRejected, AuthReply
from ..domain.billing import BillingBlocked, QuotaExceeded
from ..domain.data import Gone, InactiveOwner, NotFound, RevisionConflict
from ..domain.identity import AuthorizationDenied
from ..domain.qa import QaRejected
from ..domain.tasks import IdempotencyMismatch, ResultConflict, StaleFence, UnsafeRetry
from ..infrastructure.qa_repository import PgQaRepository


class QaBrowser:
    def __init__(self, *, auth, database, config, billing=None):
        self.auth = auth
        self.database = database
        self.config = deepcopy(config)
        self.billing = billing

    def repository(self, tx, operation, body, params):
        return PgQaRepository(tx), self.config

    def billing_for(self, repository):
        return self.billing

    def dispatch(self, operation, ctx, body, key, params, *, billing_fallback=None):
        """No caller can supply an owner or select an execution provider."""
        try:
            if (not self.config.get('enabled', False)
                    and not (operation == 'cancelTask' and billing_fallback is not None)):
                raise AuthRejected('SERVICE_DISABLED', 503)
            writes = {'prepareGeneration', 'executeGeneration', 'cancelTask', 'reconcileTask'}
            if operation in writes:
                self.auth.require_csrf(ctx, 'session')
            principal = self.auth.principal(ctx)
            operation_key = UUID(key) if key else None
            synchronize = None
            synchronization_ids = []
            fallback = False
            with self.database.authorized_transaction(principal, activity=True) as tx:
                repository, config = self.repository(tx, operation, body, params)
                lifecycle = self.billing_for(repository)
                if operation == 'prepareGeneration':
                    value = repository.prepare(UUID(params['class_session_id']), body,
                                               operation_key, config)
                elif operation == 'getGenerationPlan':
                    value = repository.plan(UUID(params['plan_id']))
                elif operation == 'executeGeneration':
                    task = repository.execute(UUID(params['plan_id']), body,
                                              operation_key, config)
                    value = repository.task_view(task.id)
                elif operation == 'getSourceSnapshot':
                    value = repository.snapshot(UUID(params['snapshot_id']))
                elif operation == 'getQuestion':
                    value = repository.question(UUID(params['question_id']))
                elif operation == 'listQuestions':
                    class_id = params.get('class_session_id')
                    value = repository.questions(UUID(class_id) if class_id else None,
                                                 params.get('limit', 50), params.get('cursor'))
                elif operation == 'getTask':
                    value = repository.task_view(UUID(params['task_id']))
                elif operation == 'cancelTask':
                    task_id = UUID(params['task_id'])
                    task = tx.tasks.get(task_id)
                    if task.schema_version not in ('pr07a-1', 'pr07b-1', 'pr07b-batch-1') and billing_fallback is not None:
                        fallback = True
                        value = None
                    else:
                        if not self.config.get('enabled', False):
                            raise AuthRejected('SERVICE_DISABLED', 503)
                        repository.task_view(task_id)
                        synchronization_ids = repository.synchronization_ids(task_id)
                        repository.cancel(task_id, operation_key)
                        synchronize = task_id
                        value = repository.task_view(task_id)
                elif operation == 'reconcileTask':
                    task_id = UUID(params['task_id'])
                    repository.task_view(task_id)
                    synchronization_ids = repository.synchronization_ids(task_id)
                    task = repository.reconcile(task_id, operation_key)
                    synchronize = task_id
                    value = dict(state=task.state, provider_query_supported=False,
                                 checked_at=tx.connection.scalar(text('SELECT clock_timestamp()')).isoformat(),
                                 new_submission=False)
                else:
                    raise AuthRejected('SERVICE_DISABLED', 503)
            if fallback:
                return billing_fallback.cancel(ctx, params['task_id'], key)
            if synchronize is not None and lifecycle is not None:
                for identity in synchronization_ids:
                    lifecycle.synchronize(principal.user_id, identity)
            return AuthReply(value)
        except (NotFound, Gone):
            raise AuthRejected('NOT_FOUND', 404) from None
        except (AuthorizationDenied, InactiveOwner):
            raise AuthRejected() from None
        except QaRejected as error:
            raise AuthRejected(error.code, error.status) from None
        except QuotaExceeded:
            raise AuthRejected('QUOTA_EXCEEDED', 409) from None
        except BillingBlocked:
            raise AuthRejected('BUDGET_BLOCKED', 409) from None
        except IdempotencyMismatch:
            raise AuthRejected('IDEMPOTENCY_MISMATCH', 409) from None
        except RevisionConflict:
            raise AuthRejected('REVISION_CONFLICT', 409) from None
        except (StaleFence, UnsafeRetry, ResultConflict):
            raise AuthRejected('TASK_CONFLICT', 409) from None
        except (ValueError, TypeError):
            raise AuthRejected('VALIDATION_FAILED', 422) from None
