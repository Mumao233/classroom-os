"""Bounded question execution over the existing persistent task engine.

The release composition permits deterministic simulated providers. Every send
intent commits before provider I/O. Saved output is validated and published
locally, including after an expired lease or a process restart.
"""
from uuid import uuid4

from sqlalchemy import text

from ..domain.billing import BillingBlocked
from ..domain.data import Gone, InactiveOwner, NotFound, OwnerContext, RevisionConflict
from ..domain.qa import ProviderReply, ProviderRequest, QaRejected
from ..domain.tasks import ResultConflict, StaleFence, UnsafeRetry
from ..infrastructure.qa_repository import PgQaRepository
from .task_worker import TaskWorker


class QaWorker(TaskWorker):
    def __init__(self, database, scheduler, provider, *, worker_id,
                 billing=None, enabled=False, lease_seconds=30):
        super().__init__(database, scheduler, provider, worker_id=worker_id,
                         lease_seconds=lease_seconds, billing=billing)
        if type(enabled) is not bool:
            raise ValueError('question execution requires an explicit boolean gate')
        self.enabled = enabled

    def reconcile_saved(self):
        completed = 0
        for claim in self.scheduler.question_results():
            try:
                with self.database.transaction(OwnerContext(claim.owner_id)) as tx:
                    task = tx.tasks.get(claim.id)
                    if task.schema_version != 'pr07a-1':
                        continue
                    result = tx.tasks.reconcile_result(task.id, uuid4())
                    completed += int(result.state in ('succeeded', 'failed'))
            except (Gone, InactiveOwner, NotFound, RevisionConflict, ResultConflict, QaRejected):
                continue
            finally:
                if self.billing is not None:
                    self.billing.synchronize(claim.owner_id, claim.id)
        return completed

    def _send_intent(self, tx, task, attempt, claim):
        # This rechecks and locks all frozen sources before the send boundary.
        values = PgQaRepository(tx).worker_input(task.id)
        destination = values['destination']
        if (getattr(self.provider, 'provider', None) != destination['provider']
                or getattr(self.provider, 'model', None) != destination['model']):
            tx.tasks.fail_unsent(task.id, claim.lease_token, self.worker_id,
                                 'PROVIDER_CONFIG_CHANGED')
            return None
        tx.connection.execute(text('''UPDATE task_attempts SET provider=:provider,
            model=:model,price_revision=:price WHERE owner_id=:owner AND task_id=:task
            AND id=:attempt AND submitted_at IS NULL'''), dict(
                provider=destination['provider'], model=destination['model'],
                price=values['config']['price_version'], owner=task.owner_id,
                task=task.id, attempt=attempt.id))
        if self.billing is None:
            if values['reservation_id'] is not None:
                raise BillingBlocked('reserved questions require billing lifecycle hooks')
            tx.tasks.mark_submitting(task.id, attempt.id, claim.lease_token, self.worker_id)
        else:
            self.billing.before_submit(tx, task, attempt, claim, self.worker_id)
        return ProviderRequest(task_id=task.id, attempt_id=attempt.id,
            owner_id=task.owner_id, question=values['question'], selection=values['selection'],
            max_output_tokens=values['config']['max_output_tokens'])

    def _unknown(self, owner, claim, attempt):
        with self.database.transaction(owner) as tx:
            tx.tasks.mark_unknown(claim.id, attempt.id, claim.lease_token, self.worker_id)

    def run_once(self):
        if not self.enabled:
            return False
        self.recover_expired()
        reconciled = self.reconcile_saved()
        claims = self.scheduler.claim(self.worker_id, self.lease_seconds, 1,
                                      schema_version='pr07a-1')
        if not claims:
            return bool(reconciled)
        claim = claims[0]
        owner = OwnerContext(claim.owner_id)
        try:
            with self.database.transaction(owner) as tx:
                task = tx.tasks.get(claim.id)
                if task.schema_version != 'pr07a-1':
                    raise UnsafeRetry('question worker requires a question generation plan')
                attempt = tx.tasks.prepare_attempt(task.id, claim.lease_token, self.worker_id)
            if attempt.raw_output_ref is None:
                with self.database.transaction(owner) as tx:
                    request = self._send_intent(tx, task, attempt, claim)
                if request is None:
                    return True
                # No surrounding transaction, SDK retry, or fallback provider.
                try:
                    reply = self.provider.generate(request)
                    if not isinstance(reply, ProviderReply):
                        raise ValueError('provider returned an invalid envelope')
                except Exception:
                    self._unknown(owner, claim, attempt)
                    return True
                try:
                    try:
                        with self.database.transaction(owner) as tx:
                            result = tx.tasks.store_result(task.id, attempt.id, reply.raw_output)
                    finally:
                        # Usage belongs to the already authorized execution.
                        # Revoked source access or a failed body transaction
                        # cannot prevent the metadata-only ledger receiving it.
                        if self.billing is not None:
                            self.billing.completed(claim.owner_id, claim.id, reply.usage)
                except (ValueError, TypeError):
                    self._unknown(owner, claim, attempt)
                    return True
                if result.conflict_sha256 is not None:
                    return True
            with self.database.transaction(owner) as tx:
                tx.tasks.publish_result(task.id, attempt.id, claim.lease_token, self.worker_id)
        except (StaleFence, Gone, NotFound, InactiveOwner, UnsafeRetry,
                ResultConflict, RevisionConflict, BillingBlocked, QaRejected):
            # Committed intent is never cleared. Recovery has the authoritative
            # bytes and charge evidence even when this worker loses its fence.
            pass
        finally:
            if self.billing is not None:
                self.billing.synchronize(claim.owner_id, claim.id)
        return True
