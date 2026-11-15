"""Synthetic Batch-2 probe/native/launcher checks; importing creates no files.

No PostgreSQL connection, child creation, Job creation or process termination is
allowed in these tests. Process, listener, connection and catalog facts are
explicit injected fixtures. Actual owned-cluster evidence is a separate run.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import hashlib
import importlib.util
import io
import ipaddress
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch


_CANARY = "synthetic-secret-marker-must-not-appear"
_RUN = "a1" * 16


def _load(name, filename):
    path = Path(__file__).with_name(filename)
    for module in tuple(sys.modules.values()):
        if getattr(module, "__file__", None) == str(path):
            return module
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Batch-2 source unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _Fixture:
    def __init__(self, work, guard):
        self.root = work / "checkout"
        self.run_root = self.root / "work/pr03b/runs" / _RUN
        self.data = self.run_root / "pg/data"
        self.data.mkdir(parents=True, exist_ok=False)
        self.exe = work / "bin/postgres.exe"
        self.exe.parent.mkdir()
        self.exe.write_bytes(b"INERT BATCH2 UNIT FIXTURE; NEVER EXECUTE\n")
        self.manifest = {
            "schema_version": 1, "kind": "pr03b.synthetic_run_plan", "run_id": _RUN,
            "instance_nonce": "c3" * 32, "worktree": str(self.root),
            "data_directory": str(self.data), "port": 4987, "database": "pr02a_" + _RUN,
            "login": "pr02a_" + _RUN + "_bootstrap", "postgres_executable": str(self.exe),
            "postgres_sha256": hashlib.sha256(self.exe.read_bytes()).hexdigest(),
            "server_version_num": 170011,
        }
        url = "postgresql+psycopg://{}@127.0.0.1:4987/{}".format(
            self.manifest["login"], self.manifest["database"])
        self.plan = guard.validate_plan(url, self.manifest, self.root, {})
        self.primary = guard.ProcessIdentity(42424, 133444700000000000, self.exe)
        self.parent = guard.ProcessIdentity(31337, 133444600000000000, Path(sys.executable))
        self.local = guard.LocalObservation(self.primary, 1700000000, "a" * 64, "b" * 64)

    def identity(self, bootstrap=False):
        return {
            "login": self.plan.login, "session_user": self.plan.login,
            "database": "postgres" if bootstrap else self.plan.database,
            "data_directory": str(self.data), "host": "127.0.0.1", "port": self.plan.port,
            "listen_addresses": "127.0.0.1", "server_version_num": 170011,
            "postmaster_start_epoch": self.local.postmaster_start_epoch,
            "backend_pid": 42425, "system_identifier": "7654321098765432101",
            "current_tx_read_only": True, "in_recovery": False,
            "rolsuper": True, "rolcanlogin": True,
            "config_file": str(self.data / "postgresql.conf"),
            "hba_file": str(self.data / "pg_hba.conf"),
            "ident_file": str(self.data / "pg_ident.conf"),
            "default_transaction_read_only": "on", "unix_socket_directories": "",
            "shared_preload_libraries": "", "session_preload_libraries": "",
            "local_preload_libraries": "", "archive_mode": "off",
        }

    def catalog(self, bootstrap=False):
        databases = [
            {"name": name, "owner": self.plan.login, "is_template": name.startswith("template"),
             "allow_connections": name != "template0"}
            for name in ["postgres", "template0", "template1"] + ([] if bootstrap else [self.plan.database])
        ]
        return {"catalog_snapshot": {
            "databases": databases,
            "roles": [{"name": self.plan.login, "superuser": True, "login": True,
                       "createdb": True, "createrole": True, "replication": True, "bypassrls": True}],
            "schemas": [{"name": "public", "owner": "pg_database_owner"}],
            "relations": [],
            "extensions": [{"name": "plpgsql", "version": "1.0", "schema": "pg_catalog"}],
        }}


class _Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.index = -1

    def __enter__(self):
        self.connection.trace.append(("cursor_enter",))
        return self

    def __exit__(self, *args):
        self.connection.trace.append(("cursor_exit",))

    def execute(self, sql):
        self.index += 1
        self.connection.trace.append(("execute", sql))
        if self.connection.execute_error == self.index:
            raise RuntimeError(_CANARY)

    def fetchall(self):
        self.connection.trace.append(("fetch", self.index))
        return self.connection.rows[self.index]


class _Connection:
    def __init__(self, identity, catalog):
        self.trace = []
        self.rows = [[identity], [catalog]]
        self.closed = False
        self.execute_error = None
        self.rollback_error = False
        self.close_error = False
        self._read_only = None
        self._isolation_level = None

    @property
    def read_only(self):
        return self._read_only

    @read_only.setter
    def read_only(self, value):
        self.trace.append(("read_only", value))
        self._read_only = value

    @property
    def isolation_level(self):
        return self._isolation_level

    @isolation_level.setter
    def isolation_level(self, value):
        self.trace.append(("isolation_level", value))
        self._isolation_level = value

    def cursor(self):
        return _Cursor(self)

    def rollback(self):
        self.trace.append(("rollback",))
        if self.rollback_error:
            raise RuntimeError(_CANARY)

    def close(self):
        self.trace.append(("close",))
        self.closed = True
        if self.close_error:
            raise RuntimeError(_CANARY)

    def commit(self):
        raise AssertionError("probe must never commit")


class _Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []

    def startTest(self, test):
        self.started = time.monotonic()
        super().startTest(test)

    def _record(self, test, outcome):
        self.cases.append({"id": test.id().split(".")[-1], "outcome": outcome,
                           "seconds": round(time.monotonic() - self.started, 6)})

    def addSuccess(self, test):
        self._record(test, "passed")
        super().addSuccess(test)

    def addFailure(self, test, error):
        self._record(test, "failed")
        super().addFailure(test, error)

    def addError(self, test, error):
        self._record(test, "error")
        super().addError(test, error)

    def addSkip(self, test, reason):
        self._record(test, "skipped")
        super().addSkip(test, reason)


def run(work: Path) -> tuple[dict, str]:
    probe = _load("_batch2_probe_selftest", "pg_probe.py")
    native = _load("_batch2_native_selftest", "native_process.py")
    launcher = _load("_batch2_launcher_selftest", "pg_launcher.py")
    guard = probe.guard
    work = guard.contained_path(str(work), guard.ROOT / "work/pr03b")
    if not work.is_dir():
        raise ValueError("caller-created owned Batch-2 selftest directory required")

    class Safety(unittest.TestCase):
        def setUp(self):
            self.fx = _Fixture(work / self._testMethodName.split("_", 2)[1], guard)
            self.connect = Mock(side_effect=AssertionError("unexpected live database connection"))
            self.native_api = Mock(side_effect=AssertionError("unexpected native side effect"))
            self.child = Mock(side_effect=AssertionError("unexpected real child"))
            for target, name, value in ((probe.psycopg, "connect", self.connect),
                                        (native, "_Api", self.native_api),
                                        (subprocess, "Popen", self.child)):
                patcher = patch.object(target, name, value)
                patcher.start()
                self.addCleanup(patcher.stop)

        def fake_connection(self, bootstrap=False):
            connection = _Connection(self.fx.identity(bootstrap), self.fx.catalog(bootstrap))
            self.connect.side_effect = None
            self.connect.return_value = connection
            return connection

        def callback(self, connection):
            def verify(pid):
                self.assertEqual(pid, 42425)
                self.assertFalse(connection.closed)
                connection.trace.append(("backend_native_callback", pid))
            return Mock(side_effect=verify)

        def rejected(self, call):
            with self.assertRaises(guard.UnsafeTestDatabaseTarget) as caught:
                call()
            self.assertNotIn(_CANARY, str(caught.exception))
            self.assertNotIn(guard.SYNTHETIC_PASSWORD, str(caught.exception))

        def invoke(self, connection, *, bootstrap=False, callback=None):
            return probe.probe(self.fx.plan, self.fx.local, bootstrap=bootstrap,
                               verify_backend=self.callback(connection) if callback is None else callback)

        def cleanup_assertions(self, connection):
            self.assertEqual(connection.trace[-2:], [("rollback",), ("close",)])
            self.assertTrue(connection.closed)
            self.child.assert_not_called()
            self.native_api.assert_not_called()

    serial = 0
    def add(label, body):
        nonlocal serial
        serial += 1
        name = "test_{:03d}_{}".format(serial, label)
        def check(self):
            body(self)
        check.__name__ = name
        setattr(Safety, name, check)

    for bootstrap in (False, True):
        def positive(t, bootstrap=bootstrap):
            connection = t.fake_connection(bootstrap)
            result = t.invoke(connection, bootstrap=bootstrap)
            kwargs = t.connect.call_args.kwargs
            t.assertEqual(t.connect.call_count, 1)
            t.assertEqual(t.connect.call_args.args, ())
            t.assertEqual(kwargs["host"], "127.0.0.1")
            t.assertEqual(kwargs["hostaddr"], "127.0.0.1")
            t.assertEqual(kwargs["port"], t.fx.plan.port)
            t.assertEqual(kwargs["dbname"], "postgres" if bootstrap else t.fx.plan.database)
            t.assertEqual(kwargs["user"], t.fx.plan.login)
            t.assertEqual(kwargs["password"], guard.SYNTHETIC_PASSWORD)
            t.assertEqual(kwargs["options"], probe.CONNECT_OPTIONS)
            t.assertEqual(kwargs["options"].split(), [
                "-c", "default_transaction_read_only=on", "-c", "statement_timeout=2000",
                "-c", "lock_timeout=1000", "-c", "idle_in_transaction_session_timeout=3000",
                "-c", "search_path=pg_catalog"])
            t.assertEqual(kwargs["connect_timeout"], 2)
            t.assertIs(kwargs["autocommit"], False)
            t.assertEqual(kwargs["sslmode"], "disable")
            t.assertEqual(kwargs["gssencmode"], "disable")
            t.assertIn("default_transaction_read_only=on", kwargs["options"])
            t.assertIn("statement_timeout=2000", kwargs["options"])
            executed = [row[1] for row in connection.trace if row[0] == "execute"]
            t.assertEqual(executed, [probe.IDENTITY_SQL, probe.CATALOG_SQL])
            first_query = connection.trace.index(("execute", probe.IDENTITY_SQL))
            callback_at = connection.trace.index(("backend_native_callback", 42425))
            second_query = connection.trace.index(("execute", probe.CATALOG_SQL))
            t.assertLess(connection.trace.index(("read_only", True)), first_query)
            t.assertEqual(connection.isolation_level, probe.psycopg.IsolationLevel.REPEATABLE_READ)
            t.assertLess(first_query, callback_at)
            t.assertLess(callback_at, second_query)
            t.assertTrue(result["current_tx_read_only"])
            t.assertTrue(result["backend_callback_passed"])
            t.assertFalse(result["target_verified"])
            t.assertFalse(result["database_execution_allowed"])
            t.assertEqual(result["catalog_non_system_relation_count"], 0)
            t.assertEqual(len(result["catalog_sha256"]), 64)
            t.cleanup_assertions(connection)
        add("probe_positive_" + ("internal_postgres_bootstrap" if bootstrap else "strict_run_target"), positive)

    for number, env in enumerate((
        {"PGHOST": ""}, {"pghostaddr": _CANARY}, {"PGSERVICE": _CANARY},
        {"PGPASSFILE": _CANARY}, {"PGUNKNOWN_FUTURE_SETTING": ""},
        {"CLASSROOM_WEB_DATABASE_URL": _CANARY}, {"classroom_web_database_url": _CANARY},
    )):
        def environment(t, env=env):
            with patch.dict(os.environ, env, clear=True):
                t.rejected(lambda: probe.probe(t.fx.plan, t.fx.local, verify_backend=Mock()))
            t.connect.assert_not_called()
        add("probe_environment_{:02d}_before_connect".format(number), environment)

    def shared_bootstrap_not_allowed(t):
        url = "postgresql+psycopg://{}@127.0.0.1:4987/postgres".format(t.fx.plan.login)
        t.rejected(lambda: guard.validate_test_database_target(url, {}))
        t.connect.assert_not_called()
    add("postgres_exception_never_weakens_shared_url_guard", shared_bootstrap_not_allowed)
    for label, changes in (("wrong_run_database", {"database": "pr02a_other"}),
                           ("same_prefix_login", {"login": "pr02a_" + _RUN + "_other"}),
                           ("default_port", {"port": 5432}), ("bool_port", {"port": True}),
                           ("wrong_run_id_type", {"run_id": 1})):
        def bad_plan(t, changes=changes):
            t.rejected(lambda: probe.probe(replace(t.fx.plan, **changes), t.fx.local, verify_backend=Mock()))
            t.connect.assert_not_called()
        add("probe_forged_plan_" + label, bad_plan)
    add("probe_requires_backend_callback_before_connect", lambda t: (
        t.rejected(lambda: probe.probe(t.fx.plan, t.fx.local, verify_backend=None)), t.connect.assert_not_called()))
    add("probe_requires_bool_purpose_before_connect", lambda t: (
        t.rejected(lambda: probe.probe(t.fx.plan, t.fx.local, bootstrap=1, verify_backend=Mock())), t.connect.assert_not_called()))
    def no_sql_argument(t):
        with t.assertRaises(TypeError):
            probe.probe(t.fx.plan, t.fx.local, verify_backend=Mock(), sql=_CANARY)
        t.connect.assert_not_called()
    add("probe_has_no_sql_or_connection_options_api", no_sql_argument)

    identity_changes = (
        ("login", "pr02a_other"), ("session_user", "pr02a_other"), ("database", "postgres"),
        ("data_directory", "J:/old-pr03a-cluster/pg/data"), ("host", "127.0.0.2"),
        ("port", 4988), ("listen_addresses", "*"), ("server_version_num", 170012),
        ("postmaster_start_epoch", 1700000001), ("backend_pid", 0),
        ("backend_pid", True), ("backend_pid", 2**32 + 1),
        ("system_identifier", "0"), ("system_identifier", "x"),
        ("system_identifier", str(2**64)), ("current_tx_read_only", False),
        ("default_transaction_read_only", "off"), ("in_recovery", True),
        ("rolsuper", False), ("rolcanlogin", False), ("config_file", r"\\host\share\postgresql.conf"),
        ("session_preload_libraries", None),
    )
    for number, (key, value) in enumerate(identity_changes):
        def wrong_identity(t, key=key, value=value):
            connection = t.fake_connection()
            connection.rows[0][0][key] = value
            callback = Mock()
            t.rejected(lambda: t.invoke(connection, callback=callback))
            callback.assert_not_called()
            t.assertEqual([row[1] for row in connection.trace if row[0] == "execute"], [probe.IDENTITY_SQL])
            t.cleanup_assertions(connection)
        add("probe_identity_{:02d}_{}".format(number, key), wrong_identity)
    def missing_identity(t):
        connection = t.fake_connection()
        del connection.rows[0][0]["system_identifier"]
        t.rejected(lambda: t.invoke(connection))
        t.cleanup_assertions(connection)
    add("probe_identity_missing_field", missing_identity)
    def extra_identity(t):
        connection = t.fake_connection()
        connection.rows[0][0]["extra"] = _CANARY
        t.rejected(lambda: t.invoke(connection))
        t.cleanup_assertions(connection)
    add("probe_identity_unknown_field", extra_identity)
    for bootstrap in (False, True):
        def wrong_database_mode(t, bootstrap=bootstrap):
            connection = t.fake_connection(not bootstrap)
            t.rejected(lambda: t.invoke(connection, bootstrap=bootstrap))
            t.cleanup_assertions(connection)
        add("probe_no_database_mode_substitution_" + str(int(bootstrap)), wrong_database_mode)

    def catalog_case(t, mutate):
        connection = t.fake_connection()
        mutate(connection.rows[1][0]["catalog_snapshot"])
        t.rejected(lambda: t.invoke(connection))
        t.cleanup_assertions(connection)
    catalog_changes = [
        ("extra_database", lambda s: s["databases"].append(s["databases"][0] | {"name": "pr02a_other"})),
        ("wrong_database_owner", lambda s: s["databases"][0].update(owner="other")),
        ("missing_template", lambda s: s["databases"].pop(1)),
        ("duplicate_database", lambda s: s["databases"].append(dict(s["databases"][0]))),
        ("extra_role", lambda s: s["roles"].append(s["roles"][0] | {"name": "other_login"})),
        ("role_boolean_type", lambda s: s["roles"][0].update(login="true")),
        ("duplicate_role", lambda s: s["roles"].append(dict(s["roles"][0]))),
        ("extra_schema", lambda s: s["schemas"].append({"name": "application", "owner": "other"})),
        ("public_relation", lambda s: s["relations"].append({"schema": "public", "name": "business", "kind": "r"})),
        ("extra_extension", lambda s: s["extensions"].append({"name": "other", "version": "1", "schema": "public"})),
        ("no_plpgsql", lambda s: s.update(extensions=[])),
        ("unknown_catalog_field", lambda s: s.update(extra=_CANARY)),
    ]
    for label, mutate in catalog_changes:
        add("probe_catalog_" + label, lambda t, mutate=mutate: catalog_case(t, mutate))
    for position in (0, 1):
        for count in (0, 2):
            def rows_count(t, position=position, count=count):
                connection = t.fake_connection()
                row = connection.rows[position][0]
                connection.rows[position] = [row] * count
                t.rejected(lambda: t.invoke(connection))
                t.cleanup_assertions(connection)
            add("probe_row_count_{}_{}".format(position, count), rows_count)

    def callback_failure(t):
        connection = t.fake_connection()
        callback = Mock(side_effect=RuntimeError(_CANARY))
        t.rejected(lambda: t.invoke(connection, callback=callback))
        t.assertNotIn(("execute", probe.CATALOG_SQL), connection.trace)
        t.cleanup_assertions(connection)
    add("probe_backend_failure_prevents_catalog_and_redacts", callback_failure)
    def callback_false(t):
        connection = t.fake_connection()
        t.rejected(lambda: t.invoke(connection, callback=Mock(return_value=False)))
        t.assertNotIn(("execute", probe.CATALOG_SQL), connection.trace)
        t.cleanup_assertions(connection)
    add("probe_backend_false_is_not_success", callback_false)
    for position in (0, 1):
        def query_failure(t, position=position):
            connection = t.fake_connection()
            connection.execute_error = position
            t.rejected(lambda: t.invoke(connection))
            t.cleanup_assertions(connection)
        add("probe_fixed_query_failure_" + str(position), query_failure)
    for label in ("rollback", "close"):
        def cleanup_failure(t, label=label):
            connection = t.fake_connection()
            setattr(connection, label + "_error", True)
            t.rejected(lambda: t.invoke(connection))
            t.cleanup_assertions(connection)
        add("probe_" + label + "_failure_is_rejection", cleanup_failure)
    def connect_failure(t):
        t.connect.side_effect = RuntimeError(_CANARY)
        t.rejected(lambda: probe.probe(t.fx.plan, t.fx.local, verify_backend=Mock()))
        t.assertEqual(t.connect.call_count, 1)
    add("probe_connect_failure_redacts_diagnostics", connect_failure)
    for label, ticks in (("before_catalog", [0.0, 6.0]), ("after_cleanup", [0.0, 0.0, 6.0])):
        def soft_expiry(t, ticks=ticks):
            connection = t.fake_connection()
            with patch.object(probe.time, "monotonic", side_effect=ticks):
                t.rejected(lambda: t.invoke(connection))
            t.cleanup_assertions(connection)
        add("probe_soft_budget_" + label, soft_expiry)

    def ipv4_table(port=4987, pid=42424, address="127.0.0.1", state=2, extra_port=0):
        local = int.from_bytes(ipaddress.IPv4Address(address).packed, "little")
        word = int.from_bytes(port.to_bytes(2, "big"), "little") | extra_port
        return struct.pack("<I", 1) + struct.pack("<IIIIII", state, local, word, 0, 0, pid)
    def ipv6_table():
        word = int.from_bytes((4987).to_bytes(2, "big"), "little")
        row = (ipaddress.IPv6Address("::1").packed, 0, word, b"\0" * 16, 0, 0, 2, 42424)
        return struct.pack("<I", 1) + struct.pack("<16sII16sIIII", *row)
    add("native_ipv4_listener_decode", lambda t: t.assertEqual(
        native._parse_listener_table(ipv4_table(), 2), (native.Listener("ipv4", "127.0.0.1", 4987, 42424),)))
    add("native_ipv6_listener_decode", lambda t: t.assertEqual(
        native._parse_listener_table(ipv6_table(), 23), (native.Listener("ipv6", "::1", 4987, 42424),)))
    # Windows may include transitional rows and does not initialize the upper
    # port-word bits. These are decoder facts, not weaker ownership criteria.
    add("native_nonlisten_row_is_filtered", lambda t: t.assertEqual(
        native._parse_listener_table(ipv4_table(state=5), 2), ()))
    add("native_port_upper_garbage_preserves_exact_low16_port", lambda t: t.assertEqual(
        native._parse_listener_table(ipv4_table(extra_port=0xABCD0000), 2),
        (native.Listener("ipv4", "127.0.0.1", 4987, 42424),)))
    for label, raw, family in (
        ("short", b"", 2), ("truncated", ipv4_table()[:-1], 2),
        ("wrong_family", ipv4_table(), 99), ("count_budget", struct.pack("<I", 250001), 2),
        ("invalid_state", ipv4_table(state=13), 2), ("pid_zero", ipv4_table(pid=0), 2),
        ("port_zero", ipv4_table(port=0), 2),
        ("low16_port_zero_despite_upper_garbage", ipv4_table(port=0, extra_port=0xABCD0000), 2),
    ):
        add("native_listener_" + label, lambda t, raw=raw, family=family: t.rejected(
            lambda: native._parse_listener_table(raw, family)))
    def fixed_arguments(t):
        with patch.object(native.guard, "ROOT", t.fx.root), patch.dict(os.environ, {}, clear=True):
            args = native.fixed_postgres_arguments(t.fx.plan)
        t.assertEqual(args, (str(t.fx.exe), "-D", str(t.fx.data), "-h", "127.0.0.1", "-p", "4987"))
        t.native_api.assert_not_called()
    add("native_fixed_argv_no_shell_or_arbitrary_option", fixed_arguments)
    for label, arguments in (("duplicate", ("postgres.exe", "-D", "J:/a", "-D", "J:/b")),
                             ("missing", ("postgres.exe", "-D")),
                             ("traversal", ("postgres.exe", "-D", "J:/a/../b"))):
        add("native_data_argument_" + label, lambda t, arguments=arguments: t.rejected(lambda: native._data_argument(arguments)))
    for label, env in (("pg", {"PGHOST": ""}), ("duplicate_case", {"Path": "x", "PATH": "y"}),
                       ("nul", {"SAFE": "a\x00b"}), ("key_equal", {"A=B": "x"})):
        add("native_environment_" + label, lambda t, env=env: t.rejected(lambda: native._environment_block(env)))
    def native_environment_positive(t):
        block = native._environment_block({"TEMP": str(t.fx.run_root), "SAFE_FLAG": "synthetic"})
        t.assertIn("SAFE_FLAG=synthetic\x00", block[:])
        t.assertIn("TEMP=" + str(t.fx.run_root) + "\x00", block[:])
        t.native_api.assert_not_called()
    add("native_environment_positive_memory_only", native_environment_positive)

    def endpoint(t, *, changed=None):
        arguments = (str(t.fx.exe), "-D", str(t.fx.data), "-h", "127.0.0.1", "-p", "4987")
        details = native.ProcessDetails(t.fx.primary, t.fx.parent.pid, arguments, t.fx.data)
        rows = (native.Listener("ipv4", "127.0.0.1", 4987, t.fx.primary.pid),)
        all_rows = (native.NetworkRow("ipv4", "127.0.0.1", 4987, "0.0.0.0", 0, 2, t.fx.primary.pid),)
        job = object.__new__(native.NativeJob)
        job._lock = threading.RLock()
        job.plan, job.identity = t.fx.plan, t.fx.primary
        job.contains = Mock(return_value=True)
        job.process_ids = Mock(return_value=(t.fx.primary.pid,))
        detail_sequence, row_sequence = [details, details], [rows, rows]
        all_sequence = [all_rows, all_rows]
        parent_sequence = [t.fx.parent, t.fx.parent]
        if changed == "parent":
            detail_sequence = [replace(details, parent_pid=t.fx.parent.pid + 1)] * 2
        elif changed == "argv":
            detail_sequence = [replace(details, arguments=arguments + ("-c", "unsafe=on"))] * 2
        elif changed == "data":
            detail_sequence = [replace(details, data_directory=t.fx.data.parent)] * 2
        elif changed == "wildcard":
            row_sequence = [(native.Listener("ipv4", "0.0.0.0", 4987, t.fx.primary.pid),)] * 2
        elif changed == "extra_ipv6":
            row_sequence = [rows + (native.Listener("ipv6", "::", 4987, t.fx.primary.pid),)] * 2
        elif changed == "listener_pid":
            row_sequence = [(native.Listener("ipv4", "127.0.0.1", 4987, t.fx.primary.pid + 1),)] * 2
        elif changed == "wrong_listener_port":
            row_sequence = [(native.Listener("ipv4", "127.0.0.1", 4988, t.fx.primary.pid),)] * 2
        elif changed == "only_filtered_nonlisten":
            row_sequence = [native._parse_listener_table(ipv4_table(state=5), 2)] * 2
        elif changed in ("listener_state_zero", "listener_state_closed"):
            row_sequence = [native._parse_listener_table(ipv4_table(state=0 if changed == "listener_state_zero" else 1), 2)] * 2
        elif changed in ("all_state_zero", "all_state_closed", "all_state_established"):
            state = {"all_state_zero": 0, "all_state_closed": 1, "all_state_established": 5}[changed]
            all_sequence = [(replace(all_rows[0], state=state),)] * 2
        elif changed == "all_missing":
            all_sequence = [(), ()]
        elif changed == "all_duplicate":
            all_sequence = [all_rows + all_rows] * 2
        elif changed == "all_extra_unknown":
            all_sequence = [all_rows + (replace(all_rows[0], state=0),)] * 2
        elif changed == "all_state_drift":
            all_sequence[1] = (replace(all_rows[0], state=0),)
        elif changed == "process_drift":
            detail_sequence[1] = replace(details, identity=replace(t.fx.primary, creation_token=t.fx.primary.creation_token + 1))
        elif changed == "listener_drift":
            row_sequence[1] = ()
        elif changed == "parent_drift":
            parent_sequence[1] = replace(t.fx.parent, creation_token=t.fx.parent.creation_token + 1)
        elif changed == "outside_job":
            job.contains.return_value = False
        with patch.object(native.guard, "ROOT", t.fx.root), patch.dict(os.environ, {}, clear=True), \
                patch.object(native, "observe_process_details", side_effect=detail_sequence), \
                patch.object(native, "observe_listeners", side_effect=row_sequence), \
                patch.object(native, "tcp_rows", side_effect=all_sequence), \
                patch.object(native.guard, "observe_windows_process", side_effect=parent_sequence):
            call = lambda: native.verify_owned_endpoint(t.fx.plan, t.fx.primary, t.fx.parent, job)
            if changed:
                t.rejected(call)
            else:
                result = call()
                t.assertTrue(result["job_membership"])
                t.assertFalse(result["target_verified"])
                t.assertFalse(result["database_execution_allowed"])
                t.assertFalse(result["os_network_isolation_verified"])
                t.assertTrue(result["listener_views"]["listen_state_confirmed_in_both_views"])
        t.native_api.assert_not_called()
        t.child.assert_not_called()
    add("native_endpoint_positive_injected_not_live", endpoint)
    for label in ("parent", "argv", "data", "wildcard", "extra_ipv6", "listener_pid",
                  "wrong_listener_port", "only_filtered_nonlisten", "listener_state_zero", "listener_state_closed",
                  "all_state_zero", "all_state_closed",
                  "all_state_established", "all_missing", "all_duplicate", "all_extra_unknown", "all_state_drift",
                  "process_drift", "listener_drift", "parent_drift", "outside_job"):
        add("native_endpoint_" + label, lambda t, label=label: endpoint(t, changed=label))

    def helper_dispatch(t, helper="initdb", changed=None):
        job = object.__new__(native.NativeJob)
        job._lock = threading.RLock()
        job.plan = t.fx.plan
        job._start = Mock(return_value=t.fx.primary)
        data = str(t.fx.data)
        password_file = t.fx.run_root / "synthetic.pw"
        password_file.write_bytes((guard.SYNTHETIC_PASSWORD + "\n").encode("ascii"))
        expected = (("-D", data, "-U", t.fx.plan.login, "--pwfile", str(password_file),
                     "--encoding=UTF8", "--locale=C", "--auth-local=reject",
                     "--auth-host=scram-sha-256", "--no-instructions")
                    if helper == "initdb" else ("-D", data))
        arguments = expected
        if changed == "data":
            arguments = ("-D", str(t.fx.root / "outside_data"), *expected[2:])
        elif changed == "password_path":
            values = list(expected)
            values[values.index("--pwfile") + 1] = str(t.fx.root / "outside.pw")
            arguments = tuple(values)
        elif changed == "extra_option":
            arguments += ("-c", "unapproved=on")
        elif changed == "password_content":
            password_file.write_bytes(_CANARY.encode("ascii"))
        environment = {"SYNTHETIC_TEST_FLAG": "1"}
        out, err = t.fx.run_root / "helper.out", t.fx.run_root / "helper.err"
        with patch.object(native.guard, "ROOT", t.fx.root), patch.dict(os.environ, {}, clear=True):
            call = lambda: job.start_helper(helper, arguments, "d" * 64, environment, out, err)
            if changed or helper not in {"initdb", "pg_controldata"}:
                t.rejected(call)
                job._start.assert_not_called()
            else:
                t.assertEqual(call(), t.fx.primary)
                helper_path = t.fx.exe.parent / (helper + ".exe")
                job._start.assert_called_once_with(
                    (str(helper_path), *expected), helper_path, "d" * 64, environment, out, err)
        t.native_api.assert_not_called()
        t.child.assert_not_called()
        t.connect.assert_not_called()
    add("native_helper_initdb_exact_same_bin_dispatch", helper_dispatch)
    add("native_helper_controldata_exact_same_bin_dispatch", lambda t: helper_dispatch(t, "pg_controldata"))
    for name in ("pg_ctl", "psql", "arbitrary"):
        add("native_helper_reject_" + name, lambda t, name=name: helper_dispatch(t, name))
    for label in ("data", "password_path", "extra_option", "password_content"):
        add("native_helper_reject_" + label, lambda t, label=label: helper_dispatch(t, changed=label))

    def unconfirmed_job_termination(t):
        job = object.__new__(native.NativeJob)
        job._lock = threading.RLock()
        job.plan = t.fx.plan
        job._job, job._process = object(), object()
        owned_job, owned_process = job._job, job._process
        job.cleanup_confirmed = False
        job._unassigned_cleanup_unconfirmed = False
        job.process_ids = Mock(return_value=(t.fx.primary.pid,))
        kernel = Mock()
        kernel.TerminateJobObject.return_value = False
        job.api = type("FakeNativeAPI", (), {"k": kernel})()
        job.wait = Mock(side_effect=AssertionError("failed Job termination reached success wait"))
        job.exit_code = Mock(side_effect=AssertionError("failed Job termination reached exit success"))
        t.rejected(job.close)
        t.assertIs(job.cleanup_confirmed, False)
        kernel.TerminateJobObject.assert_called_once_with(owned_job, 1)
        t.assertEqual([call.args[0] for call in kernel.CloseHandle.call_args_list], [owned_process, owned_job])
        job.wait.assert_not_called()
        job.exit_code.assert_not_called()
        t.native_api.assert_not_called()
        t.child.assert_not_called()
        t.connect.assert_not_called()
    add("native_failed_job_termination_never_claims_confirmed_cleanup", unconfirmed_job_termination)

    def query_close_serialized(t, method):
        # Real Python threads exercise the NativeJob lock; all WinAPI calls are
        # inert fakes. A NULL Job would inspect the caller's enclosing Job.
        job = object.__new__(native.NativeJob)
        job._lock = threading.RLock()
        owned_job, query_handle = object(), object()
        job._job, job._process = owned_job, None
        job.cleanup_confirmed = False
        job._unassigned_cleanup_unconfirmed = False
        job._last_exit_code = None
        rendezvous = threading.Barrier(2, timeout=2)
        release, close_started, job_closed = (threading.Event() for _ in range(3))
        outcomes, failures, trace = [], [], []
        query_thread = None

        def hold_query():
            trace.append("query_entered")
            rendezvous.wait()
            if not release.wait(2):
                raise AssertionError("synthetic query was not released")
            t.assertFalse(job_closed.is_set(), "owned Job closed during an active query")
            trace.append("query_released")

        def open_process(access, inherit, pid):
            t.assertEqual((access, inherit, pid), (0x1000, False, t.fx.primary.pid))
            hold_query()
            return query_handle

        def membership(handle, job_handle, inside):
            t.assertIs(handle, query_handle)
            t.assertIs(job_handle, owned_job, "NULL/foreign Job must never reach IsProcessInJob")
            inside._obj.value = True
            trace.append("owned_membership")
            return True

        def process_list(job_handle, information_class, buffer, size, returned):
            t.assertIs(job_handle, owned_job, "NULL/foreign Job must never reach QueryInformationJobObject")
            t.assertEqual(information_class, 3)
            is_query = threading.current_thread() is query_thread
            if is_query:
                hold_query()
                struct.pack_into("<IIQ", buffer, 0, 1, 1, t.fx.primary.pid)
            else:
                struct.pack_into("<II", buffer, 0, 0, 0)
            return True

        def close_handle(handle):
            t.assertIn(handle, (query_handle, owned_job))
            if handle is owned_job:
                t.assertTrue(release.is_set(), "CloseHandle preceded query release")
                trace.append("owned_job_closed")
                job_closed.set()
            return True

        kernel = Mock()
        kernel.OpenProcess.side_effect = open_process
        kernel.GetProcessId.return_value = t.fx.primary.pid
        kernel.IsProcessInJob.side_effect = membership
        kernel.QueryInformationJobObject.side_effect = process_list
        kernel.CloseHandle.side_effect = close_handle
        job.api = type("FakeNativeAPI", (), {"k": kernel})()

        def query():
            try:
                outcomes.append(job.contains(t.fx.primary.pid) if method == "contains" else job.process_ids())
            except BaseException as exc:
                failures.append(exc)

        def close():
            try:
                rendezvous.wait()
                close_started.set()
                job.close()
            except BaseException as exc:
                failures.append(exc)

        query_thread = threading.Thread(target=query, name="synthetic-owned-job-query", daemon=True)
        close_thread = threading.Thread(target=close, name="synthetic-owned-job-close", daemon=True)
        try:
            query_thread.start()
            close_thread.start()
            t.assertTrue(close_started.wait(2), "close worker did not reach the query barrier")
            t.assertFalse(job_closed.wait(0.1), "close bypassed the active method's Job lock")
            t.assertEqual(kernel.CloseHandle.call_count, 0)
        finally:
            release.set()
            query_thread.join(2)
            close_thread.join(2)
        t.assertFalse(query_thread.is_alive(), "synthetic query worker remained pending")
        t.assertFalse(close_thread.is_alive(), "synthetic close worker remained pending")
        t.assertEqual(failures, [])
        t.assertEqual(outcomes, [True] if method == "contains" else [(t.fx.primary.pid,)])
        t.assertTrue(job_closed.is_set())
        t.assertTrue(job.cleanup_confirmed)
        t.assertIsNone(job._job)
        t.assertLess(trace.index("query_released"), trace.index("owned_job_closed"))
        kernel.TerminateJobObject.assert_not_called()
        t.native_api.assert_not_called()
        t.child.assert_not_called()
        t.connect.assert_not_called()
    for method in ("contains", "process_ids"):
        add("native_{}_close_thread_serialized_no_outer_job".format(method),
            lambda t, method=method: query_close_serialized(t, method))

    for method in ("contains", "process_ids"):
        for closed_handle in (None, 0):
            def closed_query(t, method=method, closed_handle=closed_handle):
                job = object.__new__(native.NativeJob)
                job._lock = threading.RLock()
                job._job, job._process = closed_handle, None
                kernel = Mock()
                job.api = type("FakeNativeAPI", (), {"k": kernel})()
                t.rejected(lambda: job.contains(t.fx.primary.pid) if method == "contains" else job.process_ids())
                t.assertEqual(kernel.mock_calls, [], "closed query reached native/current enclosing Job")
                t.native_api.assert_not_called()
                t.child.assert_not_called()
                t.connect.assert_not_called()
            add("native_{}_closed_{}_refuses_before_native".format(method, "none" if closed_handle is None else "zero"),
                closed_query)

    def rejected_cli(t, arguments, environment=None):
        output, errors = io.StringIO(), io.StringIO()
        live = Mock(side_effect=AssertionError("unsafe CLI reached live launch"))
        tests = Mock(side_effect=AssertionError("unsafe CLI reached selftest execution"))
        mkdir = Mock(side_effect=AssertionError("unsafe CLI created output/work directory"))
        with patch.dict(os.environ, environment or {}, clear=True), \
                patch.object(launcher, "live_verify", live), patch.object(launcher, "selftest", tests), \
                patch.object(Path, "mkdir", mkdir), redirect_stdout(output), redirect_stderr(errors), \
                t.assertRaises(SystemExit) as stopped:
            launcher.main(arguments)
        t.assertEqual(stopped.exception.code, 2)
        for sentinel in (live, tests, mkdir, t.connect, t.native_api, t.child):
            sentinel.assert_not_called()
        t.assertNotIn(_CANARY, output.getvalue() + errors.getvalue())
    add("launcher_unknown_mode_is_redacted", lambda t: rejected_cli(t, [_CANARY]))
    for option in ("--admin-url", "--sql", "--plan", "--server-observation", "--resume"):
        add("launcher_rejects_" + option[2:].replace("-", "_"), lambda t, option=option: rejected_cli(t, [
            "verify-live", "--output-dir", str(t.fx.run_root / "output"), option, _CANARY]))
    for index, environment in enumerate(({"PGHOST": ""}, {"pgservice": _CANARY},
                                         {"CLASSROOM_WEB_DATABASE_URL": _CANARY},
                                         {"classroom_web_database_url": _CANARY})):
        add("launcher_environment_{}".format(index), lambda t, environment=environment: rejected_cli(t, [
            "verify-live", "--output-dir", str(t.fx.run_root / "output")], environment))
    def outside_output(t):
        before = Mock(side_effect=AssertionError("invalid output reached toolchain read"))
        reserve = Mock(side_effect=AssertionError("invalid output reached port reservation"))
        mkdir = Mock(side_effect=AssertionError("invalid output created directory"))
        output, errors = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {}, clear=True), patch.object(launcher, "_toolchain", before), \
                patch.object(launcher, "_reserve_port", reserve), patch.object(Path, "mkdir", mkdir), \
                redirect_stdout(output), redirect_stderr(errors), t.assertRaises(SystemExit) as stopped:
            launcher.main(["verify-live", "--output-dir", str(t.fx.root / _CANARY)])
        t.assertEqual(stopped.exception.code, 2)
        for sentinel in (before, reserve, mkdir, t.connect, t.native_api, t.child):
            sentinel.assert_not_called()
        t.assertNotIn(_CANARY, output.getvalue() + errors.getvalue())
    add("launcher_outside_output_before_tool_or_socket", outside_output)
    for helper_number, helper in enumerate(("psql.exe", "cmd.exe", "postgres.exe --single", "../postgres.exe")):
        def helper_not_allowed(t, helper=helper):
            provenance = Mock(side_effect=AssertionError("unapproved helper reached provenance file"))
            with patch.object(launcher, "_toolchain", provenance):
                t.rejected(lambda: launcher._tool(helper))
            provenance.assert_not_called()
            t.child.assert_not_called()
        add("launcher_fixed_helper_allowlist_{:02d}".format(helper_number), helper_not_allowed)

    def bounded_positive(t):
        job = Mock()
        result = launcher._bounded(lambda: {"readonly_fixture": True}, job, seconds=0.5)
        t.assertEqual(result, {"readonly_fixture": True})
        job.close.assert_not_called()
    add("watchdog_completed_worker_does_not_stop_job", bounded_positive)
    def bounded_failure(t):
        job = Mock()
        def operation():
            raise RuntimeError(_CANARY)
        t.rejected(lambda: launcher._bounded(operation, job, seconds=0.5))
        t.connect.assert_not_called()
    add("watchdog_worker_error_never_returns_partial_result", bounded_failure)
    def bounded_timeout(t):
        release, finished = threading.Event(), threading.Event()
        job = Mock()
        job.close.side_effect = release.set
        def operation():
            try:
                if not release.wait(1):
                    raise AssertionError("fake owned Job close did not release worker")
                return {"partial_result_must_not_escape": True}
            finally:
                finished.set()
        try:
            t.rejected(lambda: launcher._bounded(operation, job, seconds=0.01))
            job.close.assert_called_once_with()
            t.assertTrue(finished.wait(0.5), "synthetic worker was left pending")
            t.connect.assert_not_called()
            t.native_api.assert_not_called()
        finally:
            release.set()
            finished.wait(1)
    add("watchdog_timeout_closes_only_supplied_fake_job_and_refuses_result", bounded_timeout)
    def create_database_not_repeatable(t):
        job = Mock()
        lease = launcher._LiveLease(t.fx.plan, job, t.fx.primary, {}, "7654321098765432101")
        lease.created = True
        boundary = Mock(side_effect=AssertionError("repeated create reached local/SQL boundary"))
        with patch.object(lease, "_local", boundary):
            t.rejected(lease.create_empty_database)
        boundary.assert_not_called()
        t.connect.assert_not_called()
    add("single_database_bootstrap_cannot_repeat", create_database_not_repeatable)

    def live_control_refusal(t, *, acceptconn=1):
        # Exercise the actual live entry's failure ordering with inert fixtures.
        # No socket, Job, helper or database is created by this test.
        owner = os.getpid()
        rows = (native.Listener("ipv4", "127.0.0.1", 4987, owner),)
        all_rows = (native.NetworkRow("ipv4", "127.0.0.1", 4987, "0.0.0.0", 0, 0, owner),)
        reservation = Mock()
        reservation.getsockopt.return_value = acceptconn
        helper = Mock(side_effect=AssertionError("observer refusal reached initdb"))
        new_job = Mock(side_effect=AssertionError("observer refusal created a Job"))
        output = t.fx.root / "outputs/pr03b/control-refusal"
        with patch.object(launcher, "ROOT", t.fx.root), patch.object(guard, "ROOT", t.fx.root), \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(launcher, "_toolchain", return_value=({}, t.fx.exe.parent)), \
                patch.object(launcher, "_tool", return_value=t.fx.exe), \
                patch.object(launcher, "_source_hashes", return_value={}), \
                patch.object(launcher, "_reserve_port", return_value=(reservation, 4987)), \
                patch.object(launcher, "_helper", helper), patch.object(native, "NativeJob", new_job), \
                patch.object(native, "observe_listeners", return_value=rows), \
                patch.object(native, "tcp_rows", return_value=all_rows), redirect_stdout(io.StringIO()):
            t.assertEqual(launcher.live_verify(output), 1)
        result = json.loads((output / "results.json").read_text(encoding="utf-8"))
        t.assertEqual(result["failed_phase"], "native_listener_control")
        t.assertFalse(result["passed"])
        t.assertFalse(result["target_verified"])
        t.assertFalse(result["target_active"])
        t.assertFalse(result["database_execution_allowed"])
        t.assertEqual(result["commands"], [])
        t.assertEqual(result["bootstrap_catalog_ddl"], [])
        t.assertFalse((Path(result["work"]) / "pg/data").exists())
        t.assertFalse((Path(result["work"]) / "synthetic.pw").exists())
        reservation.listen.assert_called_once_with(1)
        reservation.close.assert_called_once_with()
        helper.assert_not_called()
        new_job.assert_not_called()
        t.connect.assert_not_called()
        t.child.assert_not_called()
        t.native_api.assert_not_called()
    add("live_entry_observer_disagreement_refuses_before_initdb_job_or_sql", live_control_refusal)
    add("live_entry_socket_not_listening_refuses_before_initdb_job_or_sql",
        lambda t: live_control_refusal(t, acceptconn=0))

    def lease_read(t, changed=None):
        configuration = t.fx.data / "postgresql.conf"
        configuration.write_bytes(b"# inert synthetic configuration\n")
        pins = {configuration.name: hashlib.sha256(configuration.read_bytes()).hexdigest()}
        job = Mock()
        job.contains.side_effect = lambda pid: pid == t.fx.primary.pid
        lease = launcher._LiveLease(t.fx.plan, job, t.fx.primary, pins, "7654321098765432101")
        identity = t.fx.identity() | {"catalog_snapshot": t.fx.catalog()["catalog_snapshot"],
                                     "target_verified": False, "database_execution_allowed": False}
        if changed == "system_id":
            identity["system_identifier"] = "7654321098765432102"
        elif changed == "preload":
            identity["shared_preload_libraries"] = "unexpected-library"
        elif changed == "config":
            configuration.write_bytes(b"# replaced before connection\n")
        observations = [t.fx.local, t.fx.local]
        if changed == "local_drift":
            observations[1] = replace(t.fx.local, marker_sha256="f" * 64)
        read = Mock(return_value=identity)
        with patch.object(launcher.pg_probe, "probe", read), \
                patch.object(launcher.native, "verify_owned_endpoint", return_value={"injected": True}), \
                patch.object(launcher.native, "job_network", return_value={
                    "loopback_only_observed": True, "non_loopback_row_count": 0}), \
                patch.object(launcher.guard, "verify_local_ownership", side_effect=observations):
            if changed:
                t.rejected(lease.read)
            else:
                result = lease.read()
                t.assertFalse(result["target_verified"])
                t.assertFalse(result["database_execution_allowed"])
            if changed == "config":
                read.assert_not_called()
            else:
                read.assert_called_once()
        t.connect.assert_not_called()
        t.native_api.assert_not_called()
        t.child.assert_not_called()
    add("launcher_lease_positive_injected_never_live_attestation", lease_read)
    for label in ("system_id", "preload", "config", "local_drift"):
        add("launcher_fresh_boundary_" + label, lambda t, label=label: lease_read(t, changed=label))

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Safety)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=_Result).run(suite)
    log = stream.getvalue().replace(_CANARY, "[redacted synthetic marker]").replace(
        guard.SYNTHETIC_PASSWORD, "[redacted synthetic credential]")
    return {"tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
            "skipped": len(result.skipped), "cases": result.cases,
            "database_connections": 0, "real_children_created": 0,
            "target_verified": False, "database_execution_allowed": False,
            "evidence_level": "batch2_synthetic_selftest",
            "limitations": "Injected DB/native observations and fixed-call tests only; no live SQL, OS Job/process/listener attestation, migration, authentication or cleanup was executed."}, log
