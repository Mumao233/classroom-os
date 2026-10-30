"""Approved internal processing of saved results; never an authentication port.

The fixed database functions return queue metadata/booleans only. Neither this
adapter nor the scheduler receives raw output from an unavailable account.
"""
from contextlib import contextmanager

import sqlalchemy as sa

from ..domain.tasks import Claim


class PgLocalResultReconciler:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def _transaction(self, owner_id=None):
        with self.database.engine.begin() as connection:
            safe = connection.scalar(sa.text("""
                SELECT NOT (r.rolsuper OR r.rolbypassrls OR r.rolcreaterole OR r.rolcreatedb)
                  AND pg_has_role(current_user,'classroom_web_runtime','USAGE')
                  AND NOT pg_has_role(current_user,'classroom_web_scheduler','USAGE')
                  AND NOT pg_has_role(current_user,'classroom_web_identity','USAGE')
                  AND NOT EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relkind IN ('r','p')
                      AND pg_has_role(current_user,c.relowner,'USAGE'))
                FROM pg_roles r WHERE r.rolname=current_user
            """))
            if safe is not True:
                raise PermissionError('local reconciliation requires the restricted runtime identity')
            connection.execute(sa.text('SET LOCAL search_path=public,pg_temp'))
            connection.execute(sa.text("SET LOCAL timezone='UTC'"))
            connection.execute(sa.text("SET LOCAL statement_timeout='15s'"))
            connection.execute(sa.text("SET LOCAL lock_timeout='5s'"))
            # Owner comes exclusively from the trusted DB candidate projection.
            # This method is not exposed through a request owner field.
            connection.execute(sa.text("SELECT set_config('app.owner_id',:owner,true)"),
                               {'owner': str(owner_id) if owner_id is not None else ''})
            yield connection

    def candidates(self, batch_size=100):
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError('local reconciliation batch must be 1..100')
        with self._transaction() as connection:
            rows = connection.execute(sa.text(
                'SELECT * FROM classroom_web_local_reconcile_candidates(:batch)'),
                {'batch': batch_size}).mappings()
            return [Claim(**row) for row in rows]

    def reconcile(self, claim: Claim):
        if not isinstance(claim, Claim):
            raise TypeError('a trusted database recovery candidate is required')
        with self._transaction(claim.owner_id) as connection:
            return connection.scalar(sa.text(
                'SELECT classroom_web_reconcile_saved_result(:task,:token)'),
                {'task': claim.id, 'token': claim.lease_token})

    def run_once(self, batch_size=100):
        finished = 0
        for claim in self.candidates(batch_size):
            try:
                finished += int(bool(self.reconcile(claim)))
            except sa.exc.DBAPIError as error:
                # Retryable DB contention is retried by the next bounded poll.
                # It cannot repeat a Provider call: none exists in this adapter.
                if getattr(error.orig, 'sqlstate', None) not in ('55P03', '40001', '40P01'):
                    raise
        return finished
