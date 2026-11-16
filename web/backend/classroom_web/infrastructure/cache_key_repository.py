"""Owner-scoped PostgreSQL cache keys and atomic existing reauth-proof consumption."""

from contextlib import contextmanager

import sqlalchemy as sa

from ..domain.auth import AuthRejected
from ..domain.data import Gone, NotFound
from ..domain.identity import AuthorizationDenied


class PgCacheKeyRepository:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def authorized(self, principal, authorization):
        import json

        phase = "executing"
        try:
            with self.database.authorized_transaction(principal) as tx:
                result = tx.connection.scalar(sa.text(
                    "SELECT public.classroom_web_authorize_cache_key(CAST(:payload AS jsonb))"),
                    {"payload": json.dumps(authorization, separators=(",", ":"))})
                if result is None:
                    raise AuthRejected()
                if "error" in result:
                    code = result["error"]
                    status = 503 if code == "SERVICE_DISABLED" else 403
                    raise AuthRejected(code, status)
                yield tx, result
                phase = "committing"
        except AuthorizationDenied:
            raise AuthRejected() from None
        except NotFound:
            raise AuthRejected("NOT_FOUND", 404) from None
        except Gone:
            raise AuthRejected("GONE", 410) from None
        except sa.exc.DBAPIError as error:
            state = getattr(error.orig, "sqlstate", None)
            if phase == "committing" and (error.connection_invalidated or state is None or state.startswith("08")):
                raise AuthRejected("RESULT_UNKNOWN", 503) from None
            raise AuthRejected("STORAGE_UNAVAILABLE", 503) from None
        except sa.exc.TimeoutError:
            raise AuthRejected("RATE_LIMITED", 429) from None

    @staticmethod
    def current(connection, metadata):
        return connection.execute(sa.text("""
            SELECT * FROM public.cache_keys WHERE owner_id=:owner_id AND device_id=:device_id
              AND purpose=:purpose AND class_session_id IS NOT DISTINCT FROM CAST(:class_session_id AS uuid)
              AND key_version=:key_version FOR UPDATE
        """), metadata).mappings().one_or_none()

    def get_or_create(self, connection, metadata, crypto):
        existing = self.current(connection, metadata)
        if existing is not None:
            return existing
        values = {**metadata, **crypto.create(metadata)}
        connection.execute(sa.text("""
            INSERT INTO public.cache_keys(id,owner_id,device_id,purpose,class_session_id,
                key_version,wrapped_dek,kek_version)
            VALUES(:id,:owner_id,:device_id,:purpose,:class_session_id,:key_version,:wrapped_dek,:kek_version)
            ON CONFLICT DO NOTHING
        """), values)
        # A competing creator can win the unique tuple. Return that exact committed key.
        return self.current(connection, metadata)

    @staticmethod
    def historical(connection, metadata, key_id):
        row = connection.execute(sa.text("""
            SELECT * FROM public.cache_keys WHERE id=:key_id AND owner_id=:owner_id
              AND device_id=:device_id AND purpose=:purpose
              AND class_session_id IS NOT DISTINCT FROM CAST(:class_session_id AS uuid) FOR UPDATE
        """), {**metadata, "key_id": key_id}).mappings().one_or_none()
        if row is None:
            raise AuthRejected("NOT_FOUND", 404)
        return row
