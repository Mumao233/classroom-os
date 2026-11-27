"""Fixed-function billing gateways. No caller receives secret-table grants."""
from contextlib import contextmanager
import json
import os

import sqlalchemy as sa
from sqlalchemy.engine import make_url

from ..domain.auth import AuthRejected
from .auth_deadlines import remaining


class PgBillingAdminRepository:
    OPERATIONS = frozenset(('issue_admin_proof', 'apply_admin', 'read_admin'))
    SQL_REVISION = 'classroom-admin-0.4.0-sql-2'

    def __init__(self, url):
        parsed = make_url(url)
        if (parsed.drivername != 'postgresql+psycopg' or parsed.query
                or not all((parsed.host, parsed.port, parsed.database, parsed.username, parsed.password))
                or any(key.upper().startswith('PG') for key in os.environ)):
            raise ValueError('an explicit restricted billing gateway URL is required')
        self.engine = sa.create_engine(parsed, pool_size=2, max_overflow=0, pool_timeout=1,
                                       hide_parameters=True, pool_pre_ping=True, connect_args={'connect_timeout': 5})

    @contextmanager
    def _connection(self):
        with self.engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL statement_timeout='5s'"))
            connection.execute(sa.text("SET LOCAL lock_timeout='1s'"))
            safe = connection.scalar(sa.text('''SELECT NOT(rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolinherit)
                AND NOT EXISTS(SELECT FROM pg_auth_members WHERE member=r.oid)
                AND NOT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                  WHERE n.nspname IN ('public','identity_control','billing_control') AND c.relkind IN ('r','p')
                    AND pg_has_role(current_user,c.relowner,'MEMBER'))
                FROM pg_roles r WHERE rolname=current_user'''))
            if safe is not True:
                raise PermissionError('billing gateway requires a separate restricted NOINHERIT login')
            yield connection

    def require_current_schema(self):
        """Both SQL assets must be installed before any HTTP admin consumer is mounted."""
        try:
            with self._connection() as connection:
                current = connection.scalar(sa.text("""
                    SELECT pg_catalog.obj_description(pg_catalog.to_regprocedure(
                        'billing_api.adjust_quota(uuid,uuid,uuid,bigint,text)'), 'pg_proc') = :revision
                       AND pg_catalog.obj_description(pg_catalog.to_regprocedure(
                        'billing_api.apply_admin(jsonb)'), 'pg_proc') = :revision
                """), {'revision': self.SQL_REVISION})
        except sa.exc.SQLAlchemyError:
            current = False
        if current is not True:
            raise ValueError(f'Billing admin requires installed SQL revision {self.SQL_REVISION}. See docs/web-alpha/pr11a/README.md')

    def call(self, operation, payload):
        if operation not in self.OPERATIONS:
            raise ValueError('unsupported billing gateway operation')
        committing = False
        try:
            with self._connection() as connection:
                result = connection.scalar(sa.text(f'SELECT billing_api.{operation}(cast(:payload AS jsonb))'),
                                           {'payload': json.dumps(payload)})
                remaining()
                if result is None:
                    raise AuthRejected('REAUTH_REQUIRED', 403)
                if 'error' in result and result['error'] != 'SECRET_ALREADY_ISSUED':
                    code = result['error']
                    status = {'FORBIDDEN':403, 'REAUTH_REQUIRED':403, 'NOT_FOUND':404, 'VALIDATION_FAILED':422,
                              'IDEMPOTENCY_MISMATCH':409, 'REVISION_CONFLICT':409, 'INVITATION_UNAVAILABLE':409,
                              'GONE':410, 'SERVICE_DISABLED':503}.get(code, 503)
                    error = AuthRejected(code, status)
                    error.current_revision = result.get('current_revision')
                    raise error
                committing = True
            # A secret replay commits consumption of any fresh proof before answering 409.
            if result.get('error') == 'SECRET_ALREADY_ISSUED':
                raise AuthRejected('SECRET_ALREADY_ISSUED', 409)
            return result
        except sa.exc.DBAPIError as error:
            state = getattr(error.orig, 'sqlstate', '') or ''
            message = getattr(getattr(error.orig, 'diag', None), 'message_primary', '')
            if message == 'IDEMPOTENCY_MISMATCH':
                raise AuthRejected('IDEMPOTENCY_MISMATCH', 409) from None
            if state == '22003' and message == 'QUOTA_LIMIT_OUT_OF_RANGE':
                raise AuthRejected('VALIDATION_FAILED', 422) from None
            if state == '42501' and message == 'reauth expired':
                raise AuthRejected('REAUTH_REQUIRED', 403) from None
            if state == 'P0001':
                raise AuthRejected('REVISION_CONFLICT', 409) from None
            unknown = state.startswith('08') or (committing and (error.connection_invalidated or not state))
            raise AuthRejected('RESULT_UNKNOWN' if unknown else 'STORAGE_UNAVAILABLE', 503) from None

    def dispose(self):
        self.engine.dispose()


class BillingMaintenance:
    """Explicit scheduler invocation and idempotent notification delivery port."""
    def __init__(self, operator_engine, actor_id, lifecycle):
        self.engine, self.actor_id, self.lifecycle = operator_engine, actor_id, lifecycle

    def _call(self, query, parameters):
        with self.engine.begin() as connection:
            safe = connection.scalar(sa.text('''SELECT NOT rolsuper AND NOT rolbypassrls
                AND pg_has_role(current_user,'classroom_billing_operator','USAGE')
                AND NOT pg_has_role(current_user,'classroom_web_runtime','USAGE')
                AND NOT pg_has_role(current_user,'classroom_billing_control','USAGE') FROM pg_roles WHERE rolname=current_user'''))
            if safe is not True:
                raise PermissionError('a restricted billing operator login is required')
            return connection.scalar(sa.text(query), parameters)

    def run_once(self):
        cycle = self._call('SELECT billing_api.maintain_period(:actor)', {'actor': self.actor_id})
        synchronized = self.lifecycle.recover_pending()
        notifications = self._call('SELECT billing_api.threshold_messages(NULL)', {})
        return dict(cycle=cycle, synchronized=synchronized, notifications=notifications)

    def acknowledge(self, notification_id):
        return self._call('SELECT billing_api.threshold_messages(:id)', {'id': notification_id})
