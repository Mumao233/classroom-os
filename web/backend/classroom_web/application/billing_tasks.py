"""Billing hooks for the existing task worker and metadata-only recovery path."""
from uuid import UUID

from ..domain.billing import BillingBlocked, PURPOSES
from .billing import reconciliation_transaction


class BillingTaskLifecycle:
    def __init__(self, reconciler_engine):
        self.engine = reconciler_engine

    def before_submit(self, tx, task, attempt, claim, worker):
        if task.kind not in PURPOSES:
            tx.tasks.mark_submitting(task.id, attempt.id, claim.lease_token, worker)
            return
        repository = tx.billing
        reservation = repository.for_task(task.id)
        if reservation is None:
            raise BillingBlocked('charged task has no reservation')
        repository.authorize_send(reservation['id'], attempt.id, claim.lease_token, worker)
        repository.consume_permit(reservation['id'], attempt.id, reservation['maximum_units'], claim.lease_token, worker)

    def synchronize(self, owner_id, task_id):
        with reconciliation_transaction(self.engine, owner_id) as repository:
            return repository.sync_execution(task_id)

    def completed(self, owner_id, task_id, usage):
        with reconciliation_transaction(self.engine, owner_id) as repository:
            row = repository.for_task(task_id)
            if row is None:
                return
            if usage is None:
                if row['state'] == 'submitted':
                    repository.mark_unknown(row['id'])
            else:
                repository.reconcile(row['id'], usage)

    def late_usage(self, owner_id, task_id, usage):
        self.completed(owner_id, task_id, usage)

    def recover_pending(self):
        # The fixed function returns routing IDs only, with bounded fair polling.
        with self.engine.begin() as connection:
            from sqlalchemy import text
            rows = connection.scalar(text('SELECT billing_api.pending_fee_tasks()'))
        for row in rows:
            self.synchronize(UUID(row['owner_id']), UUID(row['task_id']))
        return len(rows)
