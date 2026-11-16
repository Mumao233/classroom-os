"""Billing composition using existing identity/task transactions.

HTTP, scheduler wiring and the shared Alembic chain are coordinator-owned.
"""
from contextlib import contextmanager
from types import SimpleNamespace

from sqlalchemy import text

from ..domain.billing import BillingBlocked
from ..infrastructure.billing_repository import PgBillingRepository


class BillingService:
    def __init__(self, database):
        self.database = database

    def reserve(self, principal, request):
        with self.database.authorized_transaction(principal) as transaction:
            return dict(PgBillingRepository(transaction).reserve(request))

    def quota(self, principal):
        with self.database.authorized_transaction(principal) as transaction:
            return PgBillingRepository(transaction).quota()


@contextmanager
def reconciliation_transaction(engine, owner_id):
    """Metadata-only account-independent fee collection after revocation.

    The supplied engine must use a dedicated reconciler login without runtime,
    operator, table-owner or superuser privileges. It cannot hydrate task bodies.
    """
    with engine.connect() as connection, connection.begin():
        safe = connection.scalar(text('''SELECT NOT r.rolsuper AND NOT r.rolbypassrls
            AND pg_has_role(current_user,'classroom_billing_reconciler','USAGE')
            AND NOT pg_has_role(current_user,'classroom_web_runtime','USAGE')
            AND NOT pg_has_role(current_user,'classroom_billing_control','USAGE')
            AND NOT pg_has_role(current_user,'classroom_billing_operator','USAGE')
            AND NOT EXISTS(SELECT FROM pg_class WHERE relnamespace='public'::regnamespace
                AND pg_has_role(current_user,relowner,'USAGE'))
            FROM pg_roles r WHERE r.rolname=current_user'''))
        if safe is not True:
            raise BillingBlocked('a dedicated metadata-only reconciler login is required')
        connection.execute(text("SELECT set_config('app.owner_id',:owner,true)"), {'owner': str(owner_id)})
        connection.execute(text("SET LOCAL statement_timeout='15s'"))
        connection.execute(text("SET LOCAL lock_timeout='5s'"))
        transaction = SimpleNamespace(connection=connection, catalog=SimpleNamespace(owner_id=owner_id))
        yield PgBillingRepository(transaction)
