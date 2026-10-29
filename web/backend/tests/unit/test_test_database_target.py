"""Guard both real test entry points without opening sockets or spawning pytest."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import sqlalchemy as sa

from classroom_web.test_support import (
    SYNTHETIC_PASSWORD, UnsafeTestDatabaseTarget, validate_test_database_target,
)


ROOT = Path(__file__).resolve().parents[4]
VALID = "postgresql+psycopg://pr02a_migrator@127.0.0.1:4979/pr02a_synthetic"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def entries(monkeypatch):
    # Start from a known environment; tests never read inherited libpq values.
    for key in list(os.environ):
        if key.upper().startswith("PG"):
            monkeypatch.delenv(key)
    fixture = load_module("pr02a_target_fixture", ROOT / "web/backend/tests/integration/conftest.py")
    runner = load_module("pr02a_target_runner", ROOT / "tools/pr02a/run_tests.py")
    engine = Mock(side_effect=AssertionError("engine creation was reached before target rejection"))
    connect = Mock(side_effect=AssertionError("connection was reached before target rejection"))
    migrate = Mock(side_effect=AssertionError("migration was reached before target rejection"))
    process = Mock(side_effect=AssertionError("subprocess was reached before target rejection"))
    monkeypatch.setattr(fixture.sa, "create_engine", engine)
    monkeypatch.setattr(sa.engine.Engine, "connect", connect)
    monkeypatch.setattr(fixture.command, "upgrade", migrate)
    monkeypatch.setattr(runner.subprocess, "run", process)
    return SimpleNamespace(fixture=fixture, runner=runner, engine=engine, connect=connect,
                           migrate=migrate, process=process)


def assert_both_entries_reject(entries, monkeypatch, url):
    with pytest.raises(UnsafeTestDatabaseTarget):
        validate_test_database_target(url)
    monkeypatch.setenv("PR02A_TEST_ADMIN_URL", url)
    with pytest.raises(pytest.fail.Exception):
        next(entries.fixture.pg.__wrapped__())
    monkeypatch.setattr(sys, "argv", ["run_tests.py", "--admin-url", url])
    with pytest.raises(SystemExit) as rejected:
        entries.runner.main()
    assert rejected.value.code == 2
    entries.engine.assert_not_called()
    entries.connect.assert_not_called()
    entries.migrate.assert_not_called()
    entries.process.assert_not_called()


@pytest.mark.parametrize("url", [
    VALID + "?hostaddr=203.0.113.77",
    VALID + "?host=203.0.113.77",
    VALID + "?host=127.0.0.1&host=203.0.113.77",
    VALID + "?service=synthetic_redirect",
    VALID + "?options=-csearch_path%3Duntrusted",
    VALID + "?dbname=postgres",
    VALID + "?port=5432",
    VALID + "?user=postgres",
    VALID + "?sslmode=disable",
    VALID + "?connect_timeout=5",
    VALID.replace("postgresql+psycopg:", "postgresql:"),
    VALID.replace("postgresql+psycopg:", "postgresql+psycopg2:"),
    "sqlite:///pr02a_synthetic",
    VALID.replace("127.0.0.1", "localhost"),
    VALID.replace("127.0.0.1", "203.0.113.77"),
    VALID.replace("127.0.0.1", "127.0.0.1,203.0.113.77"),
    VALID.replace("127.0.0.1", "127.0.0.1."),
    VALID.replace("127.0.0.1", "%2Fsynthetic_socket"),
    VALID.replace(":4979", ":5432"),
    VALID.replace(":4979", ""),
    VALID.replace(":4979", ":0"),
    VALID.replace(":4979", ":65536"),
    VALID.replace("pr02a_synthetic", "postgres"),
    VALID.replace("pr02a_synthetic", "pr02a_synthetic%20host%3D203.0.113.77"),
    VALID.replace("pr02a_synthetic", "pr02a_postgresql%3A%2F%2F203.0.113.77"),
    VALID.replace("pr02a_synthetic", "pr02a_"),
    VALID.replace("pr02a_migrator", "postgres"),
    VALID.replace("pr02a_migrator", "pr02a_migrator%00"),
    VALID.replace("pr02a_migrator@", "pr02a_migrator:%00@"),
    VALID + "\n",
    "not-a-database-url",
], ids=[
    "query-hostaddr", "query-host", "query-repeated-host", "query-service", "query-options",
    "query-dbname", "query-port", "query-user", "query-sslmode", "query-timeout",
    "implicit-driver", "other-driver", "sqlite", "dns-localhost", "remote-host", "multi-host",
    "dns-suffix", "socket-path", "default-port", "missing-port", "zero-port", "invalid-port",
    "non-test-database", "dbname-conninfo", "dbname-uri", "empty-test-name", "non-test-login",
    "encoded-login-control", "encoded-password-control", "raw-control", "malformed",
])
def test_malicious_url_is_rejected_before_engine_or_process(entries, monkeypatch, url):
    assert_both_entries_reject(entries, monkeypatch, url)


@pytest.mark.parametrize("name,value", [
    ("PGHOST", "203.0.113.77"),
    ("PGHOSTADDR", "203.0.113.77"),
    ("PGPORT", "5432"),
    ("PGDATABASE", "postgres"),
    ("PGUSER", "postgres"),
    ("PGPASSWORD", "synthetic-only"),
    ("PGSERVICE", "synthetic-redirect"),
    ("PGSERVICEFILE", "synthetic-service.conf"),
    ("PGSYSCONFDIR", "synthetic-config-directory"),
    ("PGPASSFILE", "synthetic-passfile"),
    ("PGOPTIONS", "-c search_path=untrusted"),
    ("PGSSLMODE", "require"),
    ("PGSSLROOTCERT", "synthetic-root.crt"),
    ("PGAPPNAME", "synthetic-app"),
    ("PGTARGETSESSIONATTRS", "read-write"),
    ("PGLOADBALANCEHOSTS", "random"),
    ("PGCONNECT_TIMEOUT", "1"),
    ("PGCLIENTENCODING", "UTF8"),
    ("PGHOSTADDR", ""),
    ("pgservice", "case-insensitive-windows"),
    ("PGUNKNOWN_FUTURE_SETTING", "future-libpq-extension"),
])
def test_libpq_environment_is_rejected_before_engine_or_process(entries, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    assert_both_entries_reject(entries, monkeypatch, VALID)


def test_guard_does_not_expose_url_or_environment_values_in_errors():
    with pytest.raises(UnsafeTestDatabaseTarget) as caught:
        validate_test_database_target(VALID + "?password=private-value", {"PGSERVICE": "private-file"})
    assert "private-value" not in str(caught.value)
    assert "private-file" not in str(caught.value)


def test_valid_explicit_target_has_no_pgpass_fallback():
    url = validate_test_database_target(VALID, {})
    assert url.drivername == "postgresql+psycopg"
    assert url.host == "127.0.0.1" and url.port == 4979 and url.database == "pr02a_synthetic"
    assert url.username == "pr02a_migrator" and url.password == SYNTHETIC_PASSWORD
    assert not url.query
    authenticated = validate_test_database_target(VALID.replace("migrator@", "migrator:synthetic-password@"), {})
    assert authenticated.password == "synthetic-password"


def test_runner_passes_canonical_target_and_sanitized_environment(entries, monkeypatch, tmp_path):
    monkeypatch.setattr(entries.runner, "ROOT", tmp_path / "synthetic-runner")
    monkeypatch.setattr(sys, "argv", ["run_tests.py", "--admin-url", VALID])
    monkeypatch.setenv("CLASSROOM_WEB_DATABASE_URL", "must-not-be-inherited")
    entries.process.side_effect = None
    entries.process.return_value = SimpleNamespace(returncode=0, stdout="synthetic pytest result", stderr="")
    assert entries.runner.main() == 0
    entries.process.assert_called_once()
    environment = entries.process.call_args.kwargs["env"]
    assert not any(key.upper().startswith("PG") for key in environment)
    assert "CLASSROOM_WEB_DATABASE_URL" not in environment
    url = validate_test_database_target(environment["PR02A_TEST_ADMIN_URL"], environment)
    assert url.password == SYNTHETIC_PASSWORD
    entries.engine.assert_not_called()
    entries.connect.assert_not_called()
    entries.migrate.assert_not_called()
