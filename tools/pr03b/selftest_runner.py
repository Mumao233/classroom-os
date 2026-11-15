"""Independent synthetic checks for the PR-03b guard, never a database run.

Importing this module creates no files. ``run`` uses only its caller-created,
run-owned selftest directory. Fake postgres files are inert test data, not a
cluster. Injected process/server facts never establish a live target identity.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


_CANARY = "synthetic-secret-marker-must-not-appear"
_RUN_ID = "a1" * 16
_OTHER_RUN = "b2" * 16
_INSTANCE = "c3" * 32


def _module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    for module in tuple(sys.modules.values()):
        if getattr(module, "__file__", None) == str(path):
            return module
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("selftest source unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _stat_copy(value, **changes):
    names = ("st_mode", "st_nlink", "st_size", "st_dev", "st_ino", "st_mtime_ns")
    fields = {name: getattr(value, name) for name in names}
    fields["st_file_attributes"] = getattr(value, "st_file_attributes", 0)
    return SimpleNamespace(**(fields | changes))


class _Fixture:
    """Only synthetic files under one independent unittest case directory."""

    def __init__(self, case_root: Path, guard, omit=None):
        self.guard = guard
        self.case_root = case_root
        self.root = case_root / "checkout"
        self.run_root = self.root / "work/pr03b/runs" / _RUN_ID
        self.data = self.run_root / "pg/data"
        # Outside the fake checkout, but inside this selftest's owned tree.
        # A pinned existing tool need not live inside the cluster data tree.
        self.exe = case_root / "approved_bin/postgres.exe"
        self.exe_bytes = b"PR03B INERT SYNTHETIC FILE; NEVER EXECUTE\n"
        self.data.mkdir(parents=True, exist_ok=False)
        self.exe.parent.mkdir(parents=True, exist_ok=False)
        self.exe.write_bytes(self.exe_bytes)
        self.pid = 42424
        self.created = 133444700000000000
        self.started = 1700000000
        self.manifest = {
            "schema_version": 1,
            "kind": "pr03b.synthetic_run_plan",
            "run_id": _RUN_ID,
            "instance_nonce": _INSTANCE,
            "worktree": str(self.root),
            "data_directory": str(self.data),
            "port": 4987,
            "database": "pr02a_" + _RUN_ID,
            "login": "pr02a_" + _RUN_ID + "_bootstrap",
            "postgres_executable": str(self.exe),
            "postgres_sha256": hashlib.sha256(self.exe_bytes).hexdigest(),
            "server_version_num": 170011,
        }
        self.url = self.make_url()
        self.plan_path = self.run_root / "plan.json"
        if omit != "plan":
            self.write_plan()
        self.owner = self.manifest | {
            "kind": "pr03b.synthetic_cluster_owner",
            "pid": self.pid,
            "creation_token": self.created,
            "postmaster_start_epoch": self.started,
        }
        self.owner_path = self.run_root / "owner.json"
        self.pid_path = self.data / "postmaster.pid"
        if omit != "owner":
            self.write_owner()
        if omit != "postmaster":
            self.write_pid()

    def make_url(self, **changes):
        values = {"driver": "postgresql+psycopg", "login": self.manifest["login"],
                  "host": "127.0.0.1", "port": ":4987", "database": self.manifest["database"]}
        values.update(changes)
        return "{driver}://{login}@{host}{port}/{database}".format(**values)

    def write_plan(self):
        self.plan_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def write_owner(self):
        self.owner_path.write_text(json.dumps(self.owner), encoding="utf-8")

    def pid_lines(self):
        return [str(self.pid), str(self.data), str(self.started), "4987", "",
                "127.0.0.1", "0 0", "ready"]

    def write_pid(self, lines=None):
        self.pid_path.write_text("\n".join(self.pid_lines() if lines is None else lines) + "\n", encoding="utf-8")

    def plan(self):
        return self.guard.validate_plan(self.url, self.manifest, self.root, {})

    def process(self):
        return self.guard.ProcessIdentity(self.pid, self.created, self.exe)

    def observer(self):
        return Mock(return_value=self.process())

    def local(self):
        return self.guard.verify_local_ownership(self.plan(), observe_process=self.observer())

    def server(self):
        return {"login": self.manifest["login"], "database": self.manifest["database"],
                "data_directory": str(self.data), "host": "127.0.0.1", "port": 4987,
                "listen_addresses": "127.0.0.1", "server_version_num": 170011,
                "postmaster_pid": self.pid, "postmaster_start_epoch": self.started}


class _Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []
        self.started = 0.0

    def startTest(self, test):
        self.started = time.monotonic()
        super().startTest(test)

    def _record(self, test, outcome):
        self.cases.append({"id": test.id().split(".")[-1], "outcome": outcome,
                           "seconds": round(time.monotonic() - self.started, 6)})

    def addSuccess(self, test):
        self._record(test, "passed")
        super().addSuccess(test)

    def addFailure(self, test, err):
        self._record(test, "failed")
        super().addFailure(test, err)

    def addError(self, test, err):
        self._record(test, "error")
        super().addError(test, err)

    def addSkip(self, test, reason):
        self._record(test, "skipped")
        super().addSkip(test, reason)


def run(work: Path) -> tuple[dict, str]:
    """Run separate unittest cases; no sockets, engine, migration or child may run."""
    guard = _module("_pr03b_selftest_guard", "target_guard.py")
    work = guard.contained_path(str(work), guard.ROOT / "work/pr03b/selftests")
    if (len(work.relative_to(guard.ROOT / "work/pr03b/selftests").parts) != 1
            or not re.fullmatch(r"[0-9a-f]{32}", work.name) or not work.is_dir()):
        raise ValueError("caller-created selftest directory required")
    entry = _module("_pr03b_selftest_entry", "run_tests.py")
    native = {"attempted": False, "platform": os.name,
              "evidence_scope": "current_python_process_only"}

    class Safety(unittest.TestCase):
        def setUp(self):
            # Keep Windows physical paths short; the full descriptive case ID
            # remains in unittest output and the per-case report.
            label = self._testMethodName.split("_", 2)[1]
            self.fx = _Fixture(work / label, guard)

        def reject(self, call):
            with self.assertRaises(guard.UnsafeTestDatabaseTarget) as caught:
                call()
            self.assertNotIn(_CANARY, str(caught.exception))

        def reject_local_before_observer(self):
            observer = Mock(side_effect=AssertionError("process observer reached before local rejection"))
            self.reject(lambda: guard.verify_local_ownership(self.fx.plan(), observe_process=observer))
            observer.assert_not_called()

        def reject_entry(self, url, environment=None, argv=None):
            # These guards inspect control flow, not the predicate under test.
            load = Mock(side_effect=AssertionError("plan file read before CLI rejection"))
            child = Mock(side_effect=AssertionError("test child reached"))
            mkdir = Mock(side_effect=AssertionError("runner created a directory"))
            engine = Mock(side_effect=AssertionError("database engine reached"))
            import sqlalchemy as sa
            arguments = argv or ["validate-plan", "--plan", str(self.fx.plan_path), "--admin-url", url]
            output, errors = io.StringIO(), io.StringIO()
            denied_before = list(entry._DENIED_EVENTS)
            with patch.dict(os.environ, environment or {}, clear=True), \
                    patch.object(entry.guard, "load_plan", load), \
                    patch.object(entry.subprocess if hasattr(entry, "subprocess") else subprocess, "run", child), \
                    patch.object(sa, "create_engine", engine), patch.object(Path, "mkdir", mkdir), \
                    redirect_stdout(output), redirect_stderr(errors), self.assertRaises(SystemExit) as stopped:
                entry.main(arguments)
            self.assertEqual(stopped.exception.code, 2)
            for sentinel in (load, child, mkdir, engine):
                sentinel.assert_not_called()
            self.assertEqual(entry._DENIED_EVENTS, denied_before,
                             "unsafe entry attempted an audit-denied side effect")
            self.assertNotIn(_CANARY, output.getvalue() + errors.getvalue())

    serial = 0

    def add(label, body):
        nonlocal serial
        serial += 1
        name = "test_{:03d}_{}".format(serial, label)
        def check(self):
            body(self)
        check.__name__ = name
        setattr(Safety, name, check)

    def valid_plan(t):
        plan = t.fx.plan()
        summary = plan.public_summary()
        t.assertEqual(plan.run_root, t.fx.run_root)
        t.assertEqual(plan.postgres_executable, t.fx.exe)
        t.assertFalse(summary["target_verified"])
        t.assertFalse(summary["database_execution_allowed"])
        t.assertEqual(summary["evidence_kind"], "plan_only")
        with t.assertRaises(FrozenInstanceError):
            plan.port = 5432
    add("plan_positive_is_not_execution_permission", valid_plan)

    def shared_guard(t):
        from classroom_web.test_support import validate_test_database_target
        t.assertIs(guard.validate_test_database_target, validate_test_database_target)
    add("unchanged_shared_url_validator_is_reused", shared_guard)

    queries = [("query_" + key, lambda f, q=query: f.url + q) for key, query in (
        ("hostaddr", "?hostaddr=203.0.113.77"), ("host", "?host=203.0.113.77"),
        ("repeated_host", "?host=127.0.0.1&host=203.0.113.77"),
        ("service", "?service=synthetic_redirect"), ("options", "?options=-csearch_path%3Duntrusted"),
        ("database", "?dbname=postgres"), ("port", "?port=5432"), ("user", "?user=postgres"),
        ("sslmode", "?sslmode=disable"), ("timeout", "?connect_timeout=5"))]
    urls = queries + [
        ("implicit_driver", lambda f: f.make_url(driver="postgresql")),
        ("other_driver", lambda f: f.make_url(driver="postgresql+psycopg2")),
        ("sqlite", lambda f: "sqlite:///pr02a_synthetic"),
        ("dns_localhost", lambda f: f.make_url(host="localhost")),
        ("remote_host", lambda f: f.make_url(host="203.0.113.77")),
        ("multi_host", lambda f: f.make_url(host="127.0.0.1,203.0.113.77")),
        ("dns_suffix", lambda f: f.make_url(host="127.0.0.1.")),
        ("socket_path", lambda f: f.make_url(host="%2Fsynthetic_socket")),
        ("default_port", lambda f: f.make_url(port=":5432")),
        ("missing_port", lambda f: f.make_url(port="")),
        ("zero_port", lambda f: f.make_url(port=":0")),
        ("invalid_port", lambda f: f.make_url(port=":65536")),
        ("non_test_database", lambda f: f.make_url(database="postgres")),
        ("database_conninfo", lambda f: f.make_url(database="pr02a_test%20host%3D203.0.113.77")),
        ("database_uri", lambda f: f.make_url(database="pr02a_postgresql%3A%2F%2F203.0.113.77")),
        ("empty_test_name", lambda f: f.make_url(database="pr02a_")),
        ("non_test_login", lambda f: f.make_url(login="postgres")),
        ("encoded_login_control", lambda f: f.make_url(login="pr02a_test%00")),
        ("encoded_password_control", lambda f: f.make_url(login=f.manifest["login"] + ":%00")),
        ("raw_control", lambda f: f.url + "\n"),
        ("malformed", lambda f: "not-a-database-url"),
        ("other_loopback", lambda f: f.make_url(host="127.0.0.2")),
        ("ipv6_loopback", lambda f: f.make_url(host="[::1]")),
        ("ipv4_mapped_ipv6", lambda f: f.make_url(host="[::ffff:127.0.0.1]")),
        ("encoded_host", lambda f: f.make_url(host="127%2E0%2E0%2E1")),
        ("explicit_private_credential", lambda f: f.make_url(login=f.manifest["login"] + ":" + _CANARY)),
    ]
    for label, make in urls:
        def reject_url(t, make=make):
            url = make(t.fx)
            with patch.object(guard, "no_reparse", Mock(side_effect=AssertionError("path metadata reached"))):
                t.reject(lambda: guard.validate_plan(url, t.fx.manifest, t.fx.root, {}))
            t.reject_entry(url)
        add("url_" + label + "_before_plan_io", reject_url)

    pg_values = [
        ("PGHOST", "203.0.113.77"), ("PGHOSTADDR", "203.0.113.77"), ("PGPORT", "5432"),
        ("PGDATABASE", "postgres"), ("PGUSER", "postgres"), ("PGPASSWORD", _CANARY),
        ("PGSERVICE", _CANARY), ("PGSERVICEFILE", _CANARY), ("PGSYSCONFDIR", _CANARY),
        ("PGPASSFILE", _CANARY), ("PGOPTIONS", _CANARY), ("PGSSLMODE", "require"),
        ("PGSSLROOTCERT", _CANARY), ("PGAPPNAME", "synthetic-app"),
        ("PGTARGETSESSIONATTRS", "read-write"), ("PGLOADBALANCEHOSTS", "random"),
        ("PGCONNECT_TIMEOUT", "1"), ("PGCLIENTENCODING", "UTF8"),
        ("PGHOSTADDR", ""), ("pgservice", _CANARY), ("PGUNKNOWN_FUTURE_SETTING", _CANARY),
        ("pGhOsT", ""), ("CLASSROOM_WEB_DATABASE_URL", _CANARY),
        ("classroom_web_database_url", _CANARY),
    ]
    for number, (key, value) in enumerate(pg_values):
        def reject_environment(t, key=key, value=value):
            environment = {key: value}
            with patch.object(guard, "no_reparse", Mock(side_effect=AssertionError("path metadata reached"))):
                t.reject(lambda: guard.validate_plan(t.fx.url, t.fx.manifest, t.fx.root, environment))
            t.reject_entry(t.fx.url, environment)
        add("environment_{:02d}_before_plan_io".format(number), reject_environment)

    for key in sorted(guard.PLAN_KEYS):
        def missing_manifest(t, key=key):
            del t.fx.manifest[key]
            t.reject(lambda: t.fx.plan())
        add("manifest_missing_" + key, missing_manifest)
    changes = [
        ("unknown_field", "extra", True), ("bool_schema", "schema_version", True),
        ("wrong_schema", "schema_version", 2), ("wrong_kind", "kind", "other.run"),
        ("upper_run", "run_id", _RUN_ID.upper()), ("short_run", "run_id", "a" * 31),
        ("invalid_nonce", "instance_nonce", "z" * 64), ("short_nonce", "instance_nonce", "a" * 63),
        ("bool_port", "port", True), ("string_port", "port", "4987"), ("wrong_port", "port", 4988),
        ("default_port", "port", 5432), ("old_major", "server_version_num", 160011),
        ("future_major", "server_version_num", 180000), ("bool_version", "server_version_num", True),
        ("invalid_hash", "postgres_sha256", "z" * 64), ("wrong_database", "database", "pr02a_other"),
        ("same_prefix_login", "login", "pr02a_" + _RUN_ID + "_other"),
    ]
    for label, key, value in changes:
        def manifest_change(t, key=key, value=value):
            t.fx.manifest[key] = value
            t.reject(lambda: t.fx.plan())
        add("manifest_" + label, manifest_change)
    for label, path in [
        ("other_checkout", lambda f: f.case_root / "other_checkout"),
        ("old_pr03a", lambda f: f.root / "work/pr03a/pg/data"),
        ("old_pr02a", lambda f: f.root / "work/pr02a/pg/data"),
        ("other_run", lambda f: f.root / "work/pr03b/runs" / _OTHER_RUN / "pg/data"),
        ("prefix_sibling", lambda f: f.run_root.with_name(_RUN_ID + "_other") / "pg/data"),
        ("run_root_itself", lambda f: f.run_root),
    ]:
        def data_path_change(t, path=path):
            t.fx.manifest["data_directory"] = str(path(t.fx))
            t.reject(lambda: t.fx.plan())
        add("manifest_path_" + label, data_path_change)
    add("manifest_other_worktree", lambda t: (
        t.fx.manifest.update(worktree=str(t.fx.case_root / "other_checkout")), t.reject(lambda: t.fx.plan())))
    add("manifest_executable_not_postgres", lambda t: (
        t.fx.manifest.update(postgres_executable=str(t.fx.exe.with_name("python.exe"))), t.reject(lambda: t.fx.plan())))
    add("manifest_url_database_must_match_exactly", lambda t: t.reject(lambda: guard.validate_plan(
        t.fx.make_url(database="pr02a_" + _RUN_ID + "_other"), t.fx.manifest, t.fx.root, {})))
    add("manifest_url_login_must_match_exactly", lambda t: t.reject(lambda: guard.validate_plan(
        t.fx.make_url(login="pr02a_" + _RUN_ID + "_other"), t.fx.manifest, t.fx.root, {})))

    def load_positive(t):
        t.assertEqual(guard.load_plan(t.fx.plan_path, t.fx.root), t.fx.manifest)
    add("load_plan_positive", load_positive)
    def missing_plan(t):
        absent = _Fixture(t.fx.case_root / "missing_plan", guard, omit="plan")
        t.reject(lambda: guard.load_plan(absent.plan_path, absent.root))
    add("load_plan_missing_file", missing_plan)
    for label, make in [
        ("outside", lambda f: f.case_root / "plan.json"),
        ("run_root", lambda f: f.run_root),
        ("wrong_name", lambda f: f.run_root / "owner.json"),
        ("too_deep", lambda f: f.run_root / "nested/plan.json"),
        ("legacy", lambda f: f.root / "work/pr03a/plan.json"),
    ]:
        def load_wrong_path(t, make=make):
            read = Mock(side_effect=AssertionError("invalid plan path was opened"))
            with patch.object(guard, "_read_file", read):
                t.reject(lambda: guard.load_plan(make(t.fx), t.fx.root))
            read.assert_not_called()
        add("load_plan_path_" + label, load_wrong_path)
    for label, raw in [
        ("duplicate", b'{"run_id":"a","run_id":"b"}'),
        ("nested_duplicate", b'{"x":{"kind":1,"kind":2}}'),
        ("invalid_utf8", b'\xff'), ("array", b'[]'), ("null", b'null'),
        ("number", b'1'), ("truncated", b'{'), ("nan", b'{"x":NaN}'),
        ("infinity", b'{"x":Infinity}'), ("negative_infinity", b'{"x":-Infinity}'),
    ]:
        add("strict_json_" + label, lambda t, raw=raw: t.reject(lambda: guard.strict_json(raw)))
    def wrong_plan_run(t):
        t.fx.manifest["run_id"] = _OTHER_RUN
        t.fx.write_plan()
        t.reject(lambda: guard.load_plan(t.fx.plan_path, t.fx.root))
    add("load_plan_stale_path_identity", wrong_plan_run)
    def oversized_plan(t):
        t.fx.plan_path.write_bytes(b" " * 32769)
        t.reject(lambda: guard.load_plan(t.fx.plan_path, t.fx.root))
    add("load_plan_oversize_before_json", oversized_plan)

    lexical = [
        ("relative", "relative/pg/data"), ("traversal", lambda f: str(f.data) + "/../data"),
        ("unc", r"\\synthetic-host\share\data"), ("device", r"\\?\C:\synthetic\data"),
        ("forward_unc", "//synthetic-host/share/data"), ("drive_relative", "J:synthetic"),
        ("control", lambda f: str(f.data) + "\x00"),
    ]
    if os.name == "nt":
        lexical += [("alternate_stream", lambda f: str(f.data) + ":stream"),
                    ("trailing_dot", lambda f: str(f.data) + "."),
                    ("trailing_space", lambda f: str(f.data) + " ")]
    for label, value in lexical:
        def lexical_reject(t, value=value):
            value = value(t.fx) if callable(value) else value
            metadata = Mock(side_effect=AssertionError("ambiguous path reached metadata IO"))
            with patch.object(Path, "lstat", metadata):
                t.reject(lambda: guard.contained_path(value, t.fx.run_root))
            metadata.assert_not_called()
        add("lexical_path_" + label, lexical_reject)

    for label, on_ancestor, symbolic in [
        ("ancestor_reparse", True, False), ("leaf_reparse", False, False),
        ("ancestor_symlink", True, True), ("leaf_symlink", False, True),
    ]:
        def injected_reparse(t, on_ancestor=on_ancestor, symbolic=symbolic):
            bad = t.fx.run_root if on_ancestor else t.fx.data
            original = Path.lstat
            def observe(path, *args, **kwargs):
                value = original(path, *args, **kwargs)
                if path == bad:
                    return _stat_copy(value, st_mode=stat.S_IFLNK | 0o777) if symbolic else _stat_copy(value, st_file_attributes=0x400)
                return value
            with patch.object(Path, "lstat", observe):
                t.reject(lambda: guard.contained_path(str(t.fx.data), t.fx.run_root))
        add("injected_metadata_" + label, injected_reparse)

    def valid_local(t):
        observer = t.fx.observer()
        result = guard.verify_local_ownership(t.fx.plan(), observe_process=observer)
        t.assertEqual(observer.call_count, 2)
        t.assertEqual(result.process, t.fx.process())
        t.assertEqual(result.postmaster_start_epoch, t.fx.started)
        t.assertEqual(len(result.marker_sha256), 64)
        t.assertEqual(len(result.postmaster_sha256), 64)
    add("local_positive_synthetic_observer_only", valid_local)
    for missing in ("owner", "postmaster"):
        def missing_local_file(t, missing=missing):
            t.fx = _Fixture(t.fx.case_root / ("missing_" + missing), guard, omit=missing)
            t.reject_local_before_observer()
        add("local_missing_" + missing + "_before_observer", missing_local_file)
    def marker_is_directory(t):
        t.fx = _Fixture(t.fx.case_root / "directory_marker", guard, omit="owner")
        t.fx.owner_path.mkdir()
        t.reject_local_before_observer()
    add("local_marker_must_be_regular_file", marker_is_directory)
    for key in sorted(guard.PLAN_KEYS):
        def stale_marker(t, key=key):
            value = t.fx.owner[key]
            if type(value) is int:
                t.fx.owner[key] = value + 1
            else:
                t.fx.owner[key] = str(value) + "_stale"
            t.fx.write_owner()
            t.reject_local_before_observer()
        add("marker_mismatch_" + key, stale_marker)
    for label, key, value in [
        ("pid_zero", "pid", 0), ("pid_negative", "pid", -1), ("pid_bool", "pid", True),
        ("pid_string", "pid", "42424"), ("pid_dword_overflow", "pid", 2**32 + 42424),
        ("creation_zero", "creation_token", 0), ("creation_bool", "creation_token", True),
        ("start_zero", "postmaster_start_epoch", 0), ("start_bool", "postmaster_start_epoch", True),
    ]:
        def invalid_process_marker(t, key=key, value=value):
            t.fx.owner[key] = value
            t.fx.write_owner()
            t.reject_local_before_observer()
        add("marker_" + label, invalid_process_marker)
    def extra_marker(t):
        t.fx.owner["extra"] = _CANARY
        t.fx.write_owner()
        t.reject_local_before_observer()
    add("marker_unknown_field", extra_marker)
    def missing_marker_field(t):
        del t.fx.owner["creation_token"]
        t.fx.write_owner()
        t.reject_local_before_observer()
    add("marker_missing_process_field", missing_marker_field)
    def marker_duplicate(t):
        t.fx.owner_path.write_bytes(b'{"kind":1,"kind":2}')
        t.reject_local_before_observer()
    add("marker_duplicate_json", marker_duplicate)
    def marker_oversize(t):
        t.fx.owner_path.write_bytes(b" " * 32769)
        t.reject_local_before_observer()
    add("marker_oversize", marker_oversize)

    for label, index, value in [
        ("pid", 0, "42425"), ("data_dir", 1, "J:/not-this-run/pg/data"),
        ("start", 2, "1700000001"), ("port", 3, "4988"),
        ("listen", 5, "*"), ("not_ready", 7, "standby"),
    ]:
        def wrong_pid_file(t, index=index, value=value):
            lines = t.fx.pid_lines()
            lines[index] = value
            t.fx.write_pid(lines)
            t.reject_local_before_observer()
        add("postmaster_" + label, wrong_pid_file)
    add("postmaster_truncated", lambda t: (t.fx.write_pid(["42424"]), t.reject_local_before_observer()))
    add("postmaster_bad_utf8", lambda t: (t.fx.pid_path.write_bytes(b"\xff"), t.reject_local_before_observer()))

    for label, changed in [
        ("pid", lambda f: guard.ProcessIdentity(f.pid + 1, f.created, f.exe)),
        ("creation_reused_pid", lambda f: guard.ProcessIdentity(f.pid, f.created + 1, f.exe)),
        ("executable", lambda f: guard.ProcessIdentity(f.pid, f.created, f.exe.with_name("other.exe"))),
        ("unavailable", lambda f: None),
    ]:
        def wrong_process(t, changed=changed):
            observer = Mock(return_value=changed(t.fx))
            t.reject(lambda: guard.verify_local_ownership(t.fx.plan(), observe_process=observer))
        add("process_observation_" + label, wrong_process)
    def wrong_exe_hash(t):
        t.fx.exe.write_bytes(t.fx.exe_bytes + b"CHANGED")
        t.reject(lambda: guard.verify_local_ownership(t.fx.plan(), observe_process=t.fx.observer()))
    add("executable_hash_mismatch", wrong_exe_hash)

    for label in ("marker", "postmaster", "executable", "process"):
        def changed_mid_check(t, label=label):
            calls = 0
            def observe(pid):
                nonlocal calls
                calls += 1
                if calls == 2:
                    if label == "marker":
                        t.fx.owner["instance_nonce"] = "d4" * 32
                        t.fx.write_owner()
                    elif label == "postmaster":
                        lines = t.fx.pid_lines()
                        lines[7] = "standby"
                        t.fx.write_pid(lines)
                    elif label == "executable":
                        t.fx.exe.write_bytes(t.fx.exe_bytes + b"REPLACED AFTER FIRST HASH")
                    else:
                        return guard.ProcessIdentity(pid, t.fx.created + 1, t.fx.exe)
                return t.fx.process()
            t.reject(lambda: guard.verify_local_ownership(t.fx.plan(), observe_process=observe))
            t.assertEqual(calls, 2)
        add("deterministic_recheck_drift_" + label, changed_mid_check)

    def read_open_identity_race(t):
        original = os.fstat
        def opened(fd):
            return _stat_copy(original(fd), st_ino=original(fd).st_ino + 1)
        with patch.object(guard.os, "fstat", opened):
            t.reject(lambda: guard._read_file(t.fx.owner_path, 32768))
    add("evidence_open_file_identity_mismatch", read_open_identity_race)
    def multiple_links(t):
        original = Path.stat
        def observation(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            return _stat_copy(value, st_nlink=2) if path == t.fx.owner_path else value
        with patch.object(Path, "stat", observation):
            t.reject(lambda: guard._read_file(t.fx.owner_path, 32768))
    add("injected_evidence_multiple_hardlinks", multiple_links)

    def server_positive(t):
        observed = t.fx.server()
        before = dict(observed)
        t.assertIsNone(guard.validate_server_observation(t.fx.plan(), t.fx.local(), observed))
        t.assertEqual(observed, before)
    add("server_tuple_positive_still_no_execution_grant", server_positive)
    for label, key, value in [
        ("login_same_prefix", "login", "pr02a_" + _RUN_ID + "_other"),
        ("database_same_prefix", "database", "pr02a_" + _RUN_ID + "_other"),
        ("old_data_dir", "data_directory", "J:/old-pr03a-cluster/pg/data"),
        ("host", "host", "127.0.0.2"), ("port", "port", 4988),
        ("bool_port", "port", True), ("listen", "listen_addresses", "*"),
        ("version_patch", "server_version_num", 170012), ("version_major", "server_version_num", 180000),
        ("bool_version", "server_version_num", True), ("pid", "postmaster_pid", 42425),
        ("start", "postmaster_start_epoch", 1700000001),
    ]:
        def wrong_server(t, key=key, value=value):
            observed = t.fx.server() | {key: value}
            t.reject(lambda: guard.validate_server_observation(t.fx.plan(), t.fx.local(), observed))
        add("server_tuple_" + label, wrong_server)
    for key in ("login", "database", "data_directory", "host", "port", "listen_addresses",
                "server_version_num", "postmaster_pid", "postmaster_start_epoch"):
        def missing_server(t, key=key):
            observed = t.fx.server()
            del observed[key]
            t.reject(lambda: guard.validate_server_observation(t.fx.plan(), t.fx.local(), observed))
        add("server_tuple_missing_" + key, missing_server)
    def extra_server(t):
        observed = t.fx.server() | {"extra": _CANARY}
        t.reject(lambda: guard.validate_server_observation(t.fx.plan(), t.fx.local(), observed))
    add("server_tuple_unknown_field", extra_server)
    add("server_tuple_not_a_mapping", lambda t: t.reject(lambda: guard.validate_server_observation(
        t.fx.plan(), t.fx.local(), None)))

    def plan_cli_positive(t):
        output = io.StringIO()
        local = Mock(side_effect=AssertionError("plan inspection became process/live verification"))
        server = Mock(side_effect=AssertionError("plan inspection became server verification"))
        import sqlalchemy as sa
        engine = Mock(side_effect=AssertionError("plan inspection created engine"))
        with patch.dict(os.environ, {}, clear=True), patch.object(entry, "ROOT", t.fx.root), \
                patch.object(entry.guard, "verify_local_ownership", local), \
                patch.object(entry.guard, "validate_server_observation", server), \
                patch.object(sa, "create_engine", engine), redirect_stdout(output):
            result = entry.main(["validate-plan", "--plan", str(t.fx.plan_path), "--admin-url", t.fx.url])
        t.assertEqual(result, 0)
        summary = json.loads(output.getvalue())
        t.assertFalse(summary["target_verified"])
        t.assertFalse(summary["database_execution_allowed"])
        for sentinel in (local, server, engine):
            sentinel.assert_not_called()
    add("cli_plan_positive_never_runs_or_probes_database", plan_cli_positive)
    add("cli_unknown_mode_redacts_values", lambda t: t.reject_entry(t.fx.url, argv=[_CANARY]))
    add("cli_unknown_option_redacts_values", lambda t: t.reject_entry(t.fx.url, argv=[
        "validate-plan", "--plan", str(t.fx.plan_path), "--admin-url", t.fx.url, "--unknown", _CANARY]))
    for command in ("run", "execute", "migrate", "cleanup", "start", "stop"):
        add("cli_has_no_" + command + "_mode", lambda t, command=command: t.reject_entry(t.fx.url, argv=[command]))

    for label, pid in (("zero", 0), ("negative", -1), ("bool", True),
                       ("string", "42424"), ("dword_overflow", 2**32 + 42424)):
        def native_invalid(t, pid=pid):
            dll = Mock(side_effect=AssertionError("invalid PID reached native process query"))
            with patch.object(guard.ctypes, "WinDLL", dll, create=True):
                t.reject(lambda: guard.observe_windows_process(pid))
            dll.assert_not_called()
        add("native_pid_" + label + "_before_openprocess", native_invalid)
    def native_current(t):
        native["attempted"] = True
        if os.name != "nt":
            t.reject(lambda: guard.observe_windows_process(os.getpid()))
            native["supported"] = False
            return
        observed = guard.observe_windows_process(os.getpid())
        t.assertEqual(observed.pid, os.getpid())
        t.assertGreater(observed.creation_token, 0)
        # Windows venv python.exe is a redirector; the OS image is the base
        # interpreter, while sys.executable intentionally identifies the venv.
        expected_image = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        t.assertEqual(observed.executable.resolve(), expected_image)
        native["expected_image_source"] = "CPython _base_executable (Windows venv redirector)"
        native["supported"] = True
        native["verified_own_python_process"] = True
    add("native_observer_current_python_only_once", native_current)

    audit_events = [
        ("socket_connect", "socket.connect", (None, ("127.0.0.1", 9))),
        ("socket_bind", "socket.bind", (None, ("127.0.0.1", 0))),
        ("dns", "socket.getaddrinfo", ("selftest.invalid", 443, 0, 0, 0)),
        ("socket_sendto", "socket.sendto", (None, ("127.0.0.1", 9))),
        ("child", "subprocess.Popen", (sys.executable, [], None, None)),
        ("system", "os.system", (b"never-execute",)),
        ("spawn", "os.posix_spawn", (sys.executable, [], {})),
    ]
    for label, event, arguments in audit_events:
        def audited_denial(t, event=event, arguments=arguments):
            with t.assertRaises(PermissionError) as caught:
                sys.audit(event, *arguments)
            t.assertIn("PR-03b selftests forbid", str(caught.exception))
        add("audit_sentinel_" + label + "_before_real_io", audited_denial)

    def actual_blocked_dns(t):
        with t.assertRaises(PermissionError):
            sys.audit("socket.getaddrinfo", "selftest.invalid", 443, 0, 0, 0)
        with t.assertRaises(PermissionError):
            socket.getaddrinfo("selftest.invalid", 443)
    add("audit_blocks_native_getaddrinfo_before_resolution", actual_blocked_dns)
    def actual_blocked_connect(t):
        with t.assertRaises(PermissionError):
            sys.audit("socket.connect", None, ("127.0.0.1", 9))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            with t.assertRaises(PermissionError):
                sock.connect(("127.0.0.1", 9))
    add("audit_blocks_native_connect_before_connection", actual_blocked_connect)
    def actual_blocked_child(t):
        with t.assertRaises(PermissionError):
            sys.audit("subprocess.Popen", sys.executable, [], None, None)
        with t.assertRaises(PermissionError):
            subprocess.run([sys.executable, "-c", "raise SystemExit(99)"], capture_output=True, timeout=1)
    add("audit_blocks_native_popen_before_child_creation", actual_blocked_child)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Safety)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=_Result).run(suite)
    log = stream.getvalue().replace(_CANARY, "[redacted synthetic marker]")
    report = {"tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skipped": len(result.skipped), "cases": result.cases,
              "native_process_observation": native,
              "limitations": ["No live SQL probe, database, migration, authentication or cleanup executor.",
                              "All cluster/process/server ownership checks except own-Python native metadata use synthetic injected observations.",
                              "Reparse/hardlink race cases inject OS metadata; they do not prove native filesystem ACL or OS network isolation.",
                              "Audit-blocked standard-library calls prove the Python hook rejected before DNS/connection/child execution; they are not live network tests."]}
    return report, log
