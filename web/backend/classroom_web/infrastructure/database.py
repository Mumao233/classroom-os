"""Short owner-scoped PostgreSQL transactions; no implicit database selection."""
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, make_url

from ..domain.data import InactiveOwner, OwnerContext
from ..domain.ports import CatalogRepository, TombstoneRepository


@dataclass(frozen=True)
class UnitOfWork:
    connection: Connection
    catalog: CatalogRepository
    tombstones: TombstoneRepository


class Database:
    def __init__(self, url: str, *, pool_size: int = 2, max_overflow: int = 0):
        if make_url(url).drivername != "postgresql+psycopg":
            raise ValueError("an explicit postgresql+psycopg URL is required")
        self.engine = create_engine(
            url, pool_size=pool_size, max_overflow=max_overflow,
            pool_pre_ping=True, hide_parameters=True,
            isolation_level="READ COMMITTED",
        )

    @contextmanager
    def transaction(self, context: OwnerContext):
        """Commit once; any exception, including deferred constraints, rolls back."""
        from .repositories import PgCatalogRepository, PgTombstoneRepository

        if not isinstance(context, OwnerContext):
            raise TypeError("an authenticated OwnerContext is required")
        with self.engine.connect() as connection, connection.begin():
            unsafe = connection.scalar(text("""
                SELECT r.rolsuper OR r.rolbypassrls OR EXISTS (
                    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relname IN (
                      'users','semesters','courses','course_material_versions',
                      'class_sessions','deletion_requests','deletion_tombstones')
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
            yield UnitOfWork(connection, catalog, PgTombstoneRepository(catalog))

    def dispose(self):
        self.engine.dispose()
