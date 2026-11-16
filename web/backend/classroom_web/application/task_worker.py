"""Restartable polling worker; PostgreSQL is the sole work/state queue.

This PR exposes simulation execution only. A real adapter requires the future
budget/authorization gate before it can reach any charged submission boundary.
"""
from dataclasses import dataclass
from sqlalchemy import text

from ..domain.data import Gone, InactiveOwner, NotFound, OwnerContext, RevisionConflict
from ..domain.task_ports import LocalResultReconciler, Scheduler, SimulationProvider
from ..domain.tasks import ResultConflict, StaleFence, UnsafeRetry
from ..domain.billing import BillingBlocked


@dataclass
class MockProvider:
    output: str = '{"synthetic":true}'
    simulation_only: bool = True
    calls: int = 0

    def submit(self, task, attempt):
        self.calls += 1
        return self.output


class TaskWorker:
    def __init__(self, database, scheduler: Scheduler, provider: SimulationProvider,
                 *, worker_id: str, lease_seconds=30,
                 reconciler: LocalResultReconciler | None = None, billing=None):
        if provider.simulation_only is not True:
            raise ValueError('PR-02b worker permits simulated providers only')
        self.database, self.scheduler, self.provider = database, scheduler, provider
        self.worker_id, self.lease_seconds = worker_id, lease_seconds
        self.reconciler = reconciler
        self.billing = billing

    def recover_expired(self):
        recovered = []
        for claim in self.scheduler.expired():
            try:
                with self.database.transaction(OwnerContext(claim.owner_id)) as tx:
                    recovered.append(tx.tasks.recover(claim.id, claim.lease_token))
            except (Gone, InactiveOwner):
                # Narrow metadata-only transition for revoked owners/lineages.
                # It cannot read inputs/results or submit anything externally.
                # SQL uses the same identity predicate as the denied owner UoW.
                # A false result can mean another worker won or access changed;
                # it is never authorization to load a body or submit a request.
                self.scheduler.recover_revoked(claim.id, claim.lease_token)
                continue
            except (StaleFence, NotFound, RevisionConflict):
                continue
            finally:
                if self.billing is not None:
                    self.billing.synchronize(claim.owner_id, claim.id)
        return recovered

    def run_once(self):
        self.recover_expired()
        reconciled = self.reconciler.run_once() if self.reconciler is not None else 0
        claims = self.scheduler.claim(self.worker_id, self.lease_seconds, 1)
        if not claims:
            return bool(reconciled)
        claim = claims[0]
        owner = OwnerContext(claim.owner_id)
        try:
            with self.database.transaction(owner) as tx:
                task = tx.tasks.get(claim.id)
                attempt = tx.tasks.prepare_attempt(claim.id, claim.lease_token, self.worker_id)
            if attempt.raw_output_ref is None:
                with self.database.transaction(owner) as tx:
                    if self.billing is None:
                        present=tx.connection.scalar(text("SELECT to_regclass('public.quota_reservations') IS NOT NULL"))
                        if present and tx.connection.scalar(text('''SELECT EXISTS(SELECT FROM public.quota_reservations
                            WHERE owner_id=:owner AND task_id=:task)'''),{'owner':claim.owner_id,'task':claim.id}):
                            raise BillingBlocked('reserved tasks require billing lifecycle hooks')
                        tx.tasks.mark_submitting(claim.id, attempt.id, claim.lease_token, self.worker_id)
                    else:
                        self.billing.before_submit(tx, task, attempt, claim, self.worker_id)
                # COMMIT above must complete before any provider boundary. No
                # transaction/automatic SDK retry surrounds the external action.
                try:
                    raw_output = self.provider.submit(task, attempt)
                except Exception:
                    with self.database.transaction(owner) as tx:
                        tx.tasks.mark_unknown(claim.id, attempt.id, claim.lease_token, self.worker_id)
                    if self.billing is not None:
                        self.billing.synchronize(claim.owner_id, claim.id)
                    return True
                with self.database.transaction(owner) as tx:
                    result = tx.tasks.store_result(claim.id, attempt.id, raw_output)
                if self.billing is not None:
                    meter = getattr(self.provider, 'usage_for', None)
                    usage = meter(task, attempt) if meter is not None else None
                    self.billing.completed(claim.owner_id, claim.id, usage)
                if result.conflict_sha256 is not None:
                    return True
            with self.database.transaction(owner) as tx:
                tx.tasks.publish_result(claim.id, attempt.id, claim.lease_token, self.worker_id)
        except (StaleFence, Gone, NotFound, InactiveOwner, UnsafeRetry, ResultConflict, RevisionConflict, BillingBlocked):
            # If raw result committed before losing the fence, recovery validates
            # it locally. If it did not commit, the intent conservatively expires
            # to unknown. Neither case calls submit again.
            pass
        finally:
            if self.billing is not None:
                self.billing.synchronize(claim.owner_id, claim.id)
        return True
