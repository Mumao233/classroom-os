"""Review execution shares the committed-intent and recovery engine with QA."""
from .qa_worker import QaWorker
from ..domain.data import Gone, InactiveOwner, NotFound, OwnerContext, RevisionConflict
from ..domain.qa import QaRejected
from ..domain.tasks import StaleFence
from ..infrastructure.review_generation import PgReviewGenerationRepository, PgReviewBatchRepository


class ReviewBatchWorker(QaWorker):
    schema_version = 'pr07b-batch-1'
    purpose = 'review_summary'
    repository_type = PgReviewBatchRepository


class ReviewWorker(QaWorker):
    schema_version = 'pr07b-1'
    purpose = 'review_summary'
    repository_type = PgReviewGenerationRepository

    def __init__(self, database, scheduler, provider, *, worker_id,
                 billing=None, enabled=False, lease_seconds=30):
        super().__init__(database, scheduler, provider, worker_id=worker_id,
                         billing=billing, enabled=enabled, lease_seconds=lease_seconds)
        self.batch_worker = ReviewBatchWorker(database, scheduler, provider, worker_id=worker_id,
            billing=billing, enabled=enabled, lease_seconds=lease_seconds)

    def advance_pending(self):
        changed = False
        for claim in self.scheduler.review_pending():
            identities = []
            try:
                with self.database.transaction(OwnerContext(claim.owner_id)) as tx:
                    repository = PgReviewGenerationRepository(tx)
                    identities = repository.synchronization_ids(claim.id)
                    changed |= repository.advance(claim.id)
            except (Gone, InactiveOwner, NotFound, QaRejected):
                self.scheduler.recover_revoked(claim.id, claim.lease_token)
            except (StaleFence, RevisionConflict):
                continue
            finally:
                if self.billing is not None:
                    for identity in identities:
                        self.billing.synchronize(claim.owner_id, identity)
        return changed

    def run_once(self):
        if not self.enabled:
            return False
        changed = self.advance_pending()
        executed = self.batch_worker.run_once()
        if not executed:
            executed = super().run_once()
        advanced = self.advance_pending()
        return changed or executed or advanced
