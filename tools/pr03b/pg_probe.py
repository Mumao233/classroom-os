"""Fixed, read-only PostgreSQL identity probe for the owned Batch-2 launcher.

There is no CLI, SQL argument, connection URL or snapshot input. The caller must
freshly verify local ownership and the listening port before/after this call.
While the connection is live, its required callback verifies the returned
backend PID's native parent and run Job membership. A successful probe alone
does not authorize a database action or prove the provenance of supplied Python
objects/test callbacks.

The sole bootstrap exception is the new cluster's built-in ``postgres`` DB;
it is an internal bool, never a relaxation of the existing URL guard. This
module does not create that cluster, a database, a role, an application table,
or an application row. It always rolls back and closes its connection.

Server/connect timeouts are not a client hard deadline if a backend is hung.
The launcher must enforce its separate bounded watchdog and abort the entire
owned run/Job on expiry; it must never reuse a timed-out worker or connection.
"""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import re
import time

import psycopg
from psycopg.rows import dict_row

if __package__:
    from . import target_guard as guard
else:
    import target_guard as guard


CONNECT_TIMEOUT_SECONDS = 2
SOFT_TOTAL_SECONDS = 5
CONNECT_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=2000 "
    "-c lock_timeout=1000 "
    "-c idle_in_transaction_session_timeout=3000 "
    "-c search_path=pg_catalog"
)
BASELINE_KEYS = (
    "login", "database", "data_directory", "host", "port", "listen_addresses",
    "server_version_num", "postmaster_pid", "postmaster_start_epoch",
)

# Both queries are constants. No user-controlled identifiers, SQL, literals or
# search_path values are interpolated, and no password-bearing catalog is read.
IDENTITY_SQL = """
SELECT current_user::text AS login,
       session_user::text AS session_user,
       pg_catalog.current_database() AS database,
       pg_catalog.current_setting('data_directory') AS data_directory,
       pg_catalog.host(pg_catalog.inet_server_addr()) AS host,
       pg_catalog.inet_server_port() AS port,
       pg_catalog.current_setting('listen_addresses') AS listen_addresses,
       pg_catalog.current_setting('server_version_num')::integer AS server_version_num,
       pg_catalog.floor(EXTRACT(epoch FROM pg_catalog.pg_postmaster_start_time()))::bigint
           AS postmaster_start_epoch,
       pg_catalog.pg_backend_pid() AS backend_pid,
       (SELECT system_identifier::text FROM pg_catalog.pg_control_system()) AS system_identifier,
       (pg_catalog.current_setting('transaction_read_only') = 'on') AS current_tx_read_only,
       pg_catalog.current_setting('config_file') AS config_file,
       pg_catalog.current_setting('hba_file') AS hba_file,
       pg_catalog.current_setting('ident_file') AS ident_file,
       pg_catalog.current_setting('default_transaction_read_only') AS default_transaction_read_only,
       pg_catalog.current_setting('unix_socket_directories') AS unix_socket_directories,
       pg_catalog.current_setting('shared_preload_libraries') AS shared_preload_libraries,
       pg_catalog.current_setting('session_preload_libraries') AS session_preload_libraries,
       pg_catalog.current_setting('local_preload_libraries') AS local_preload_libraries,
       pg_catalog.current_setting('archive_mode') AS archive_mode,
       pg_catalog.pg_is_in_recovery() AS in_recovery,
       r.rolsuper AS rolsuper,
       r.rolcanlogin AS rolcanlogin
FROM pg_catalog.pg_roles AS r
WHERE r.rolname = current_user
"""

CATALOG_SQL = """
SELECT pg_catalog.jsonb_build_object(
  'databases', (
    SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
      'name', d.datname, 'owner', pg_catalog.pg_get_userbyid(d.datdba),
      'is_template', d.datistemplate, 'allow_connections', d.datallowconn)
      ORDER BY d.datname)
    FROM pg_catalog.pg_database AS d),
  'roles', (
    SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
      'name', r.rolname, 'superuser', r.rolsuper, 'login', r.rolcanlogin,
      'createdb', r.rolcreatedb, 'createrole', r.rolcreaterole,
      'replication', r.rolreplication, 'bypassrls', r.rolbypassrls)
      ORDER BY r.rolname)
    FROM pg_catalog.pg_roles AS r),
  'schemas', (
    SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
      'name', n.nspname, 'owner', pg_catalog.pg_get_userbyid(n.nspowner))
      ORDER BY n.nspname), '[]'::jsonb)
    FROM pg_catalog.pg_namespace AS n
    WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'),
  'relations', (
    SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
      'schema', n.nspname, 'name', c.relname, 'kind', c.relkind::text)
      ORDER BY n.nspname, c.relname), '[]'::jsonb)
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'),
  'extensions', (
    SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
      'name', e.extname, 'version', e.extversion, 'schema', n.nspname)
      ORDER BY e.extname), '[]'::jsonb)
    FROM pg_catalog.pg_extension AS e
    JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace)
) AS catalog_snapshot
"""

_IDENTITY_KEYS = frozenset((
    "login", "session_user", "database", "data_directory", "host", "port",
    "listen_addresses", "server_version_num", "postmaster_start_epoch", "backend_pid",
    "system_identifier", "current_tx_read_only", "in_recovery", "rolsuper", "rolcanlogin",
    "config_file", "hba_file", "ident_file", "default_transaction_read_only",
    "unix_socket_directories", "shared_preload_libraries", "session_preload_libraries",
    "local_preload_libraries", "archive_mode",
))
_SYSTEM_ID = re.compile(r"[1-9][0-9]{0,19}\Z")


class ProbeRejected(guard.UnsafeTestDatabaseTarget):
    """A sanitized fail-closed probe result; never contains libpq diagnostics."""


def _reject(reason: str) -> None:
    raise ProbeRejected("PR-03b read-only probe: " + reason)


def _connection_kwargs(plan: guard.RunPlan, bootstrap: bool) -> dict:
    # The internal plan has already passed Batch-1 ownership checks, but these
    # fixed connection fields must also be exact if a Python caller forges one.
    if not isinstance(plan, guard.RunPlan) or type(bootstrap) is not bool:
        _reject("validated run plan and fixed probe purpose required")
    if (type(plan.run_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", plan.run_id)
            or plan.database != "pr02a_" + plan.run_id
            or plan.login != "pr02a_" + plan.run_id + "_bootstrap"
            or type(plan.port) is not int or not 1 <= plan.port <= 65535 or plan.port == 5432):
        _reject("connection differs from the exact run identity")
    return {
        "host": "127.0.0.1", "hostaddr": "127.0.0.1", "port": plan.port,
        "dbname": "postgres" if bootstrap else plan.database, "user": plan.login,
        "password": guard.SYNTHETIC_PASSWORD,
        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
        "sslmode": "disable", "gssencmode": "disable",
        "application_name": "pr03b_owned_readonly_probe",
        "options": CONNECT_OPTIONS, "autocommit": False, "row_factory": dict_row,
    }


def _identity(plan: guard.RunPlan, local: guard.LocalObservation,
              row: object, bootstrap: bool) -> dict:
    if not isinstance(row, dict) or set(row) != _IDENTITY_KEYS:
        _reject("identity response shape differs")
    expected = {
        "login": plan.login, "session_user": plan.login,
        "database": "postgres" if bootstrap else plan.database,
        "host": "127.0.0.1", "port": plan.port, "listen_addresses": "127.0.0.1",
        "server_version_num": plan.server_version_num,
        "postmaster_start_epoch": local.postmaster_start_epoch,
        "current_tx_read_only": True, "in_recovery": False,
        "default_transaction_read_only": "on",
        "rolsuper": True, "rolcanlogin": True,
    }
    if any(type(row[key]) is not type(value) or row[key] != value
           for key, value in expected.items()):
        _reject("server identity or read-only state differs")
    # PostgreSQL uses forward slashes on Windows. Normalize lexical path
    # spelling only, never resolve/stat an arbitrary server-supplied path.
    try:
        directory = guard._absolute(row["data_directory"])
    except (guard.UnsafeTestDatabaseTarget, TypeError, ValueError):
        _reject("server data directory differs")
    if directory != plan.data_directory:
        _reject("server data directory differs")
    if (type(row["backend_pid"]) is not int or not 1 <= row["backend_pid"] <= 0xFFFFFFFF
            or not isinstance(row["system_identifier"], str)
            or not _SYSTEM_ID.fullmatch(row["system_identifier"])
            or int(row["system_identifier"]) > 0xFFFFFFFFFFFFFFFF):
        _reject("invalid backend or system identifier")
    result = dict(row)
    result["data_directory"] = str(directory)
    for key in ("config_file", "hba_file", "ident_file"):
        try:
            result[key] = str(guard._absolute(row[key]))
        except (guard.UnsafeTestDatabaseTarget, TypeError, ValueError):
            _reject("invalid managed configuration path")
    for key in ("unix_socket_directories", "shared_preload_libraries", "session_preload_libraries",
                "local_preload_libraries", "archive_mode"):
        if type(row[key]) is not str:
            _reject("invalid configuration response")
    # There is no SQL postmaster-PID function. The actual independent evidence
    # is the required native backend-parent/Job callback, not this copied field.
    result["postmaster_pid"] = local.process.pid
    return result


def _catalog(plan: guard.RunPlan, row: object) -> dict:
    if (not isinstance(row, dict) or set(row) != {"catalog_snapshot"}
            or not isinstance(row["catalog_snapshot"], dict)):
        _reject("catalog response shape differs")
    snapshot = row["catalog_snapshot"]
    keys = {"databases", "roles", "schemas", "relations", "extensions"}
    if set(snapshot) != keys or any(not isinstance(snapshot[k], list) for k in keys):
        _reject("catalog response shape differs")
    db_keys = {"name", "owner", "is_template", "allow_connections"}
    db_names = []
    for database in snapshot["databases"]:
        if (not isinstance(database, dict) or set(database) != db_keys
                or type(database["name"]) is not str or database["owner"] != plan.login
                or type(database["is_template"]) is not bool
                or type(database["allow_connections"]) is not bool):
            _reject("database catalog differs from the owned bootstrap")
        db_names.append(database["name"])
    builtin = {"postgres", "template0", "template1"}
    if (len(db_names) != len(set(db_names)) or not builtin <= set(db_names)
            or not set(db_names) <= builtin | {plan.database}):
        _reject("unexpected database in the private cluster")
    role_keys = {"name", "superuser", "login", "createdb", "createrole", "replication", "bypassrls"}
    role_names = []
    for role in snapshot["roles"]:
        if (not isinstance(role, dict) or set(role) != role_keys or type(role["name"]) is not str
                or any(type(role[k]) is not bool for k in role_keys - {"name"})
                or not (role["name"] == plan.login or role["name"].startswith("pg_"))):
            _reject("unexpected role in the private cluster")
        role_names.append(role["name"])
    if len(role_names) != len(set(role_names)) or plan.login not in role_names:
        _reject("bootstrap role catalog differs")
    if (len(snapshot["schemas"]) != 1 or snapshot["schemas"][0] != {
            "name": "public", "owner": "pg_database_owner"}
            or snapshot["relations"]
            or len(snapshot["extensions"]) != 1
            or not isinstance(snapshot["extensions"][0], dict)
            or set(snapshot["extensions"][0]) != {"name", "version", "schema"}
            or snapshot["extensions"][0]["name"] != "plpgsql"
            or snapshot["extensions"][0]["schema"] != "pg_catalog"
            or type(snapshot["extensions"][0]["version"]) is not str):
        _reject("application objects or unexpected bootstrap catalog found")
    return snapshot


def probe(plan: guard.RunPlan, local: guard.LocalObservation, *,
          bootstrap: bool = False, verify_backend: Callable[[int], None]) -> dict:
    """Read two fixed catalog statements inside one rollback-only transaction.

    ``verify_backend`` runs while the actual connection is open, before the
    catalog query. It must raise unless native parent PID, creation identities,
    executable and the launcher-owned Job all match. It receives only an int,
    not a connection. Test injection is not actual native verification.
    """
    began = time.monotonic()
    guard.check_environment(os.environ)
    if not isinstance(local, guard.LocalObservation) or not callable(verify_backend):
        _reject("trusted local observation and native backend verifier required")
    kwargs = _connection_kwargs(plan, bootstrap)
    connection = None
    result = None
    try:
        connection = psycopg.connect(**kwargs)
        # Options force the initial state; these client settings also make the
        # actual implicit BEGIN explicitly READ ONLY/REPEATABLE READ.
        connection.read_only = True
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with connection.cursor() as cursor:
            cursor.execute(IDENTITY_SQL)
            rows = cursor.fetchall()
            if len(rows) != 1:
                _reject("identity response row count differs")
            identity = _identity(plan, local, rows[0], bootstrap)
            if verify_backend(identity["backend_pid"]) is not None:
                _reject("native backend verifier returned an invalid result")
            if time.monotonic() - began >= SOFT_TOTAL_SECONDS:
                _reject("probe time budget expired")
            cursor.execute(CATALOG_SQL)
            rows = cursor.fetchall()
            if len(rows) != 1:
                _reject("catalog response row count differs")
            snapshot = _catalog(plan, rows[0])
            result = identity | {
                "catalog_snapshot": snapshot,
                "catalog_sha256": hashlib.sha256(json.dumps(
                    snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                ).encode("ascii")).hexdigest(),
                "catalog_non_system_relation_count": len(snapshot["relations"]),
                "probe_purpose": "bootstrap_postgres" if bootstrap else "final_run_database",
                "postmaster_pid_source": "pinned_local_OS_identity_with_required_live_backend_parent_callback",
                "backend_callback_passed": True,
                "target_verified": False,
                "database_execution_allowed": False,
            }
    except ProbeRejected:
        raise
    except Exception:
        # Never chain libpq diagnostics, connection kwargs, a callback's native
        # process detail or SQL parameters into the public evidence path.
        raise ProbeRejected("PR-03b read-only probe failed closed") from None
    finally:
        if connection is not None:
            try:
                try:
                    connection.rollback()
                finally:
                    connection.close()
            except Exception:
                raise ProbeRejected("PR-03b read-only probe cleanup failed closed") from None
    if time.monotonic() - began >= SOFT_TOTAL_SECONDS:
        _reject("probe time budget expired")
    if result is None:
        _reject("missing probe result")
    return result
