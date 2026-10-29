"""Adversarial entrypoint checks never connect, migrate or launch real children."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

import pytest

from classroom_web.entrypoints import worker
from classroom_web.test_support import UnsafeTestDatabaseTarget, validate_test_database_target


ROOT = Path(__file__).resolve().parents[4]
SAFE = "postgresql+psycopg://pr02a_runner@127.0.0.1:4981/pr02a_runner"
UNSAFE = (
    "postgresql+psycopg://pr02a_runner@192.0.2.1:4981/pr02a_runner",
    "postgresql+psycopg://pr02a_runner@localhost:4981/pr02a_runner",
    "postgresql+psycopg://pr02a_runner@127.0.0.1:5432/pr02a_runner",
    "postgresql+psycopg://pr02a_runner@127.0.0.1/pr02a_runner",
    "postgresql+psycopg://pr02a_runner@127.0.0.1:4981/real_database",
    "postgresql+psycopg://real_login@127.0.0.1:4981/pr02a_runner",
    "postgresql+psycopg2://pr02a_runner@127.0.0.1:4981/pr02a_runner",
    SAFE + "?hostaddr=192.0.2.1",
    SAFE + "?service=production",
    SAFE + "?options=-c%20search_path=private",
    SAFE + "?sslmode=disable",
    SAFE + "\n",
)
PG_ENVIRONMENTS = (
    {"PGHOSTADDR": "192.0.2.1"}, {"PGSERVICE": "production"},
    {"PGPASSFILE": "synthetic-unread-file"}, {"PGOPTIONS": "-c search_path=private"},
    {"PGHOST": ""}, {"pghostaddr": "192.0.2.1"},
)


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("pr02b_runner_under_test", ROOT / "tools/pr02b/run_tests.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_synthetic_entrypoints_reuse_unchanged_shared_validator(runner):
    assert runner.validate_test_database_target is validate_test_database_target
    assert worker.validate_test_database_target is validate_test_database_target


@pytest.mark.parametrize("value", UNSAFE)
def test_runner_rejects_unsafe_url_before_engine_file_or_subprocess(runner, value, monkeypatch, capsys):
    engine = MagicMock(side_effect=AssertionError("must not create engine"))
    process = MagicMock(side_effect=AssertionError("must not launch test child"))
    mkdir = MagicMock(side_effect=AssertionError("must not create test folders"))
    monkeypatch.setattr(runner.sa, "create_engine", engine)
    monkeypatch.setattr(runner.subprocess, "run", process)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(sys, "argv", ["run_tests.py", "--admin-url", value])
    with patch.dict(os.environ, {}, clear=True), pytest.raises(SystemExit) as stopped:
        runner.main()
    assert stopped.value.code == 2
    engine.assert_not_called()
    process.assert_not_called()
    mkdir.assert_not_called()
    assert "tests require" in capsys.readouterr().err or value.endswith("\n")


@pytest.mark.parametrize("environment", PG_ENVIRONMENTS)
def test_runner_rejects_inherited_libpq_override_before_io(runner, environment, monkeypatch):
    engine = MagicMock(side_effect=AssertionError("must not create engine"))
    process = MagicMock(side_effect=AssertionError("must not launch test child"))
    monkeypatch.setattr(runner.sa, "create_engine", engine)
    monkeypatch.setattr(runner.subprocess, "run", process)
    monkeypatch.setattr(Path, "mkdir", MagicMock(side_effect=AssertionError("must not create folders")))
    monkeypatch.setattr(sys, "argv", ["run_tests.py", "--admin-url", SAFE])
    with patch.dict(os.environ, environment, clear=True), pytest.raises(SystemExit) as stopped:
        runner.main()
    assert stopped.value.code == 2
    engine.assert_not_called()
    process.assert_not_called()


@pytest.mark.parametrize("change", (
    {"data_directory": "J:/synthetic-other-cluster"}, {"host": "192.0.2.1"},
    {"port": 4982}, {"listen_addresses": "*"},
    {"database": "pr02a_other"}, {"login": "pr02a_other"},
))
def test_runner_rejects_wrong_actual_server_identity_before_test_child(runner, change, monkeypatch):
    identity = {"login": "pr02a_runner", "database": "pr02a_runner", "host": "127.0.0.1",
                "port": 4981, "listen_addresses": "127.0.0.1", "version": "synthetic PG identity",
                "data_directory": str(ROOT / "work/pr02b/pg/data")} | change
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.one.return_value = identity
    process = MagicMock(side_effect=AssertionError("must not launch test child"))
    monkeypatch.setattr(runner.sa, "create_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(runner.subprocess, "run", process)
    monkeypatch.setattr(Path, "mkdir", MagicMock(side_effect=AssertionError("must not create folders")))
    # Avoid even filesystem/symlink resolution for this fake server response.
    monkeypatch.setattr(Path, "resolve", lambda self: self)
    monkeypatch.setattr(sys, "argv", ["run_tests.py", "--admin-url", SAFE])
    with patch.dict(os.environ, {}, clear=True), pytest.raises(SystemExit) as stopped:
        runner.main()
    assert stopped.value.code == 2
    process.assert_not_called()
    engine.dispose.assert_called_once_with()


@pytest.mark.parametrize("position", ("runtime", "scheduler"))
@pytest.mark.parametrize("value", (UNSAFE[0], UNSAFE[7], UNSAFE[8]))
def test_worker_cli_rejects_unsafe_targets_before_any_adapter(position, value, monkeypatch):
    factories = [MagicMock(side_effect=AssertionError("must not construct adapter")) for _ in range(3)]
    for name, factory in zip(("Database", "PgTaskScheduler", "TaskWorker"), factories):
        monkeypatch.setattr(worker, name, factory)
    args = ["worker", "--runtime-url", value if position == "runtime" else SAFE,
            "--scheduler-url", value if position == "scheduler" else SAFE,
            "--worker-id", "synthetic", "--once"]
    monkeypatch.setattr(sys, "argv", args)
    with patch.dict(os.environ, {}, clear=True), pytest.raises(UnsafeTestDatabaseTarget):
        worker.main()
    for factory in factories:
        factory.assert_not_called()


@pytest.mark.parametrize("environment", PG_ENVIRONMENTS)
def test_worker_cli_rejects_libpq_environment_before_any_adapter(environment, monkeypatch):
    database = MagicMock(side_effect=AssertionError("must not construct adapter"))
    scheduler = MagicMock(side_effect=AssertionError("must not construct scheduler"))
    monkeypatch.setattr(worker, "Database", database)
    monkeypatch.setattr(worker, "PgTaskScheduler", scheduler)
    monkeypatch.setattr(sys, "argv", ["worker", "--runtime-url", SAFE, "--scheduler-url", SAFE,
                                      "--worker-id", "synthetic", "--once"])
    with patch.dict(os.environ, environment, clear=True), pytest.raises(UnsafeTestDatabaseTarget):
        worker.main()
    database.assert_not_called()
    scheduler.assert_not_called()


@pytest.mark.parametrize("scheduler_url", (
    SAFE.replace(":4981/", ":4982/"), SAFE.replace("/pr02a_runner", "/pr02a_other"),
))
def test_worker_cli_rejects_distinct_databases_before_adapter_creation(scheduler_url, monkeypatch):
    database = MagicMock(side_effect=AssertionError("must not construct adapter"))
    scheduler = MagicMock(side_effect=AssertionError("must not construct scheduler"))
    monkeypatch.setattr(worker, "Database", database)
    monkeypatch.setattr(worker, "PgTaskScheduler", scheduler)
    monkeypatch.setattr(sys, "argv", ["worker", "--runtime-url", SAFE, "--scheduler-url", scheduler_url,
                                      "--worker-id", "synthetic", "--once"])
    with patch.dict(os.environ, {}, clear=True), pytest.raises(SystemExit) as stopped:
        worker.main()
    assert stopped.value.code == 2
    database.assert_not_called()
    scheduler.assert_not_called()


def test_worker_cli_valid_target_constructs_only_mock_provider_and_disposes(monkeypatch):
    database, scheduler, execution = MagicMock(), MagicMock(), MagicMock()
    execution.run_once.return_value = False
    worker_factory = MagicMock(return_value=execution)
    monkeypatch.setattr(worker, "Database", MagicMock(return_value=database))
    monkeypatch.setattr(worker, "PgTaskScheduler", MagicMock(return_value=scheduler))
    monkeypatch.setattr(worker, "TaskWorker", worker_factory)
    monkeypatch.setattr(worker.time, "sleep", MagicMock(side_effect=AssertionError("--once must not loop")))
    monkeypatch.setattr(sys, "argv", ["worker", "--runtime-url", SAFE, "--scheduler-url", SAFE,
                                      "--worker-id", "synthetic", "--once"])
    with patch.dict(os.environ, {}, clear=True):
        worker.main()
    assert isinstance(worker_factory.call_args.args[2], worker.MockProvider)
    assert worker_factory.call_args.args[2].simulation_only is True
    execution.run_once.assert_called_once_with()
    database.dispose.assert_called_once_with()
    scheduler.dispose.assert_called_once_with()
