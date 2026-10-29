"""Short owner-scoped PostgreSQL transactions; no implicit database selection."""
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, make_url

from ..domain.data import InactiveOwner, OwnerContext
from ..domain.ports import CatalogRepository, TombstoneRepository

if TYPE_CHECKING:
    from .task_events import PgEventRepository, PgReceiptRepository
    from .task_repository import PgTaskRepository


@dataclass(frozen=True)
class UnitOfWork:
    connection: Connection
    catalog: CatalogRepository
    tombstones: TombstoneRepository
    tasks: 'PgTaskRepository'
    events: 'PgEventRepository'
    receipts: 'PgReceiptRepository'


class Database:
    def __init__(self, url: str, *, pool_size: int = 2, max_overflow: int = 0,
                 cursor_secret: bytes | None = None):
        if make_url(url).drivername != "postgresql+psycopg":
            raise ValueError("an explicit postgresql+psycopg URL is required")
        self.engine = create_engine(
            url, pool_size=pool_size, max_overflow=max_overflow,
            pool_pre_ping=True, hide_parameters=True,
            isolation_level="READ COMMITTED",
        )
        if cursor_secret is not None and (not isinstance(cursor_secret, bytes) or len(cursor_secret) < 32):
            raise ValueError('cursor secret must contain at least 32 bytes')
        self.cursor_secret = cursor_secret

    @contextmanager
    def transaction(self, context: OwnerContext, *, isolation_level='READ COMMITTED'):
        """Commit once; any exception, including deferred constraints, rolls back."""
        from .repositories import PgCatalogRepository, PgTombstoneRepository
        from .task_events import PgEventRepository, PgReceiptRepository
        from .task_repository import PgTaskRepository

        if not isinstance(context, OwnerContext):
            raise TypeError("an authenticated OwnerContext is required")
        if isolation_level not in ('READ COMMITTED', 'REPEATABLE READ'):
            raise ValueError('unsupported transaction isolation')
        with self.engine.connect().execution_options(isolation_level=isolation_level) as connection, connection.begin():
            unsafe = connection.scalar(text("""
                SELECT r.rolsuper OR r.rolbypassrls OR EXISTS (
                    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relname IN (
                      'users','semesters','courses','course_material_versions',
                      'class_sessions','deletion_requests','deletion_tombstones',
                      'tasks','task_attempts','provider_results','operation_receipts',
                      'owner_event_counters','outbox_events')
                    AND pg_has_role(current_user, c.relowner, 'USAGE')
                ) FROM pg_roles r WHERE r.rolname=current_user
            """))
            if unsafe is not False:
                raise PermissionError("runtime must not be superuser, BYPASSRLS or table owner")
            connection.execute(text("SET LOCAL search_path = public, pg_temp"))
            connection.execute(text("SET LOCAL timezone = 'UTC'"))
            connection.execute(text("SET LOCAL statement_timeout = '15s'"))
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SELECT set_config('app.owner_id', :owner, true)"),
                               {"owner": str(context.owner_id)})
            if not connection.scalar(text("SELECT classroom_web_owner_active()")):
                raise InactiveOwner("principal is missing or inactive")
            connection.execute(text("SELECT classroom_web_lock_owner()"))
            catalog = PgCatalogRepository(connection, context.owner_id)
            events = PgEventRepository(connection, context.owner_id, self.cursor_secret)
            receipts = PgReceiptRepository(connection, context.owner_id)
            yield UnitOfWork(connection, catalog, PgTombstoneRepository(catalog),
                             PgTaskRepository(connection, context.owner_id, catalog, events, receipts),
                             events, receipts)

    def dispose(self):
        self.engine.dispose()
