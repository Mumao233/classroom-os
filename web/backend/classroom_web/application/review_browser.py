"""Session-bound Review API and explicit routing of shared generation plans."""
from uuid import UUID

from ..domain.auth import AuthRejected, AuthReply
from ..domain.data import Gone, InactiveOwner, NotFound, RevisionConflict
from ..domain.identity import AuthorizationDenied
from ..domain.qa import QaRejected
from ..domain.tasks import IdempotencyMismatch
from ..infrastructure.qa_repository import PgQaRepository
from ..infrastructure.review_generation import PgReviewGenerationRepository, PgReviewBatchRepository
from ..infrastructure.review_repository import PgReviewRepository
from .qa_browser import QaBrowser


REVIEW_OPERATIONS = frozenset(('listReviewDocuments', 'createPersonalNote',
    'getReviewDocument', 'updateReviewDocument', 'listReviewRevisions',
    'writePersonalRevision', 'adoptReviewRevision', 'getReviewRevision', 'getRawProviderOutput'))


class ReviewBrowser(QaBrowser):
    def __init__(self, *, auth, database, config, billing=None, qa=None):
        super().__init__(auth=auth, database=database, config=config, billing=billing)
        self.qa = qa

    def billing_for(self, repository):
        if repository.schema_version == 'pr07a-1' and self.qa is not None:
            return self.qa.billing
        return self.billing

    def repository(self, tx, operation, body, params):
        shared = PgQaRepository(tx)
        if operation == 'prepareGeneration':
            kind = body.get('kind')
        elif 'plan_id' in params:
            kind = shared._one('generation_plans', UUID(params['plan_id']))['kind']
            batch = shared._rows('''SELECT batch_index FROM review_batches WHERE owner_id=:owner
                AND child_plan_id=:plan''', plan=UUID(params['plan_id'])).one_or_none()
            if batch and batch['batch_index'] > 0:
                return PgReviewBatchRepository(tx), self.config
        elif 'task_id' in params:
            task = tx.tasks.get(UUID(params['task_id']))
            if operation == 'cancelTask' and task.schema_version == '1':
                return shared, self.config
            if task.schema_version == 'pr07b-batch-1':
                return PgReviewBatchRepository(tx), self.config
            kind = task.kind
        elif operation in ('getQuestion', 'listQuestions'):
            kind = 'ai_question'
        else:
            kind = 'review_summary'
        if kind == 'review_summary':
            if not self.config.get('enabled', False):
                raise AuthRejected('SERVICE_DISABLED', 503)
            return PgReviewGenerationRepository(tx), self.config
        if self.qa is None or not self.qa.config.get('enabled', False):
            raise AuthRejected('SERVICE_DISABLED', 503)
        return shared, self.qa.config

    def dispatch(self, operation, ctx, body, key, params, *, billing_fallback=None):
        if operation not in REVIEW_OPERATIONS:
            if not self.config.get('enabled', False) and self.qa is not None:
                if operation == 'prepareGeneration' and body.get('kind') == 'review_summary':
                    raise AuthRejected('SERVICE_DISABLED', 503)
                return self.qa.dispatch(operation, ctx, body, key, params, billing_fallback=billing_fallback)
            return super().dispatch(operation, ctx, body, key, params, billing_fallback=billing_fallback)
        try:
            if not self.config.get('enabled', False):
                raise AuthRejected('SERVICE_DISABLED', 503)
            if operation in ('createPersonalNote', 'updateReviewDocument',
                             'writePersonalRevision', 'adoptReviewRevision'):
                self.auth.require_csrf(ctx, 'session')
            principal = self.auth.principal(ctx)
            operation_key = UUID(key) if key else None
            with self.database.authorized_transaction(principal, activity=True) as tx:
                repository = PgReviewRepository(tx)
                document_id = UUID(params['document_id']) if 'document_id' in params else None
                if operation == 'listReviewDocuments':
                    filters = dict(params)
                    for name in ('semester_id', 'course_id', 'class_session_id'):
                        if name in filters:
                            filters[name] = UUID(filters[name])
                    value = repository.documents(**filters)
                elif operation == 'createPersonalNote':
                    value = repository.create_note(body, operation_key)
                elif operation == 'getReviewDocument':
                    value = repository.document(document_id, params.get('revision'))
                elif operation == 'updateReviewDocument':
                    value = repository.update_document(document_id, body, operation_key)
                elif operation == 'listReviewRevisions':
                    value = repository.revisions(document_id, params.get('limit', 50), params.get('cursor'))
                elif operation == 'writePersonalRevision':
                    value = repository.write_personal(document_id, body, operation_key)
                elif operation == 'adoptReviewRevision':
                    value = repository.adopt(document_id, body, operation_key)
                elif operation == 'getReviewRevision':
                    value = repository.revision(UUID(params['revision_id']))
                else:
                    generation, _ = self.repository(tx, operation, body, params)
                    value = generation.raw_output(UUID(params['task_id']))
            # A conflicting personal branch has committed before reporting 409.
            if operation == 'writePersonalRevision' and 'conflict_id' in value:
                error = AuthRejected('REVISION_CONFLICT', 409)
                error.conflict_id = value['conflict_id']
                error.current_revision = value['current_revision']
                raise error
            return AuthReply(value)
        except (NotFound, Gone):
            raise AuthRejected('NOT_FOUND', 404) from None
        except (AuthorizationDenied, InactiveOwner):
            raise AuthRejected() from None
        except QaRejected as error:
            raise AuthRejected(error.code, error.status) from None
        except IdempotencyMismatch:
            raise AuthRejected('IDEMPOTENCY_MISMATCH', 409) from None
        except RevisionConflict:
            raise AuthRejected('REVISION_CONFLICT', 409) from None
        except (ValueError, TypeError):
            raise AuthRejected('VALIDATION_FAILED', 422) from None
