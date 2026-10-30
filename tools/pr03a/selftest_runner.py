"""Adversarial runner checks: unsafe targets cannot reach engine or filesystem."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("pr03a_runner", ROOT / "tools/pr03a/run_tests.py")
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
SAFE = "postgresql+psycopg://pr02a_runner@127.0.0.1:55439/pr02a_runner"


class RunnerSafety(unittest.TestCase):
    def rejects_before_io(self, value, environment):
        engine = MagicMock(side_effect=AssertionError("engine must not be created"))
        child = MagicMock(side_effect=AssertionError("child must not be launched"))
        mkdir = MagicMock(side_effect=AssertionError("directories must not be created"))
        with patch.dict(os.environ, environment, clear=True), patch.object(sys, "argv", ["run_tests.py", "--admin-url", value]), \
                patch.object(runner.sa, "create_engine", engine), patch.object(runner.subprocess, "run", child), \
                patch.object(Path, "mkdir", mkdir), self.assertRaises(SystemExit) as stopped:
            runner.main()
        self.assertEqual(stopped.exception.code, 2)
        engine.assert_not_called()
        child.assert_not_called()
        mkdir.assert_not_called()

    def test_unsafe_urls_are_rejected_before_io(self):
        for value in (
            SAFE.replace("127.0.0.1", "192.0.2.1"), SAFE.replace("127.0.0.1", "localhost"),
            SAFE.replace(":55439/", ":5432/"), SAFE.replace(":55439/", "/"),
            SAFE.replace("/pr02a_runner", "/real_database"), SAFE.replace("pr02a_runner@", "real_login@"),
            SAFE.replace("postgresql+psycopg", "postgresql+psycopg2"),
            SAFE + "?service=production", SAFE + "?hostaddr=192.0.2.1", SAFE + "?options=-c%20search_path=private",
            SAFE + "?sslmode=disable", SAFE + "\n",
        ):
            with self.subTest(value=value):
                self.rejects_before_io(value, {})

    def test_inherited_libpq_overrides_are_rejected_before_io(self):
        for environment in ({"PGHOST": ""}, {"PGHOSTADDR": "192.0.2.1"}, {"PGSERVICE": "production"},
                            {"PGPASSFILE": "private-file-must-not-be-read"}, {"PGOPTIONS": "-c search_path=private"},
                            {"pghostaddr": "192.0.2.1"}):
            with self.subTest(names=list(environment)):
                self.rejects_before_io(SAFE, environment)

    def test_wrong_actual_cluster_identity_stops_before_child_or_evidence(self):
        original = {"login": "pr02a_runner", "database": "pr02a_runner", "host": "127.0.0.1",
                    "port": 55439, "listen_addresses": "127.0.0.1", "version": "synthetic test fixture",
                    "data_directory": str(ROOT / "work/pr03a/pg/data")}
        for changed in ({"data_directory": "J:/different-synthetic-cluster"}, {"host": "192.0.2.1"},
                        {"port": 55440}, {"listen_addresses": "*"}, {"login": "pr02a_other"},
                        {"database": "pr02a_other"}):
            with self.subTest(changed=changed):
                engine = MagicMock()
                engine.connect.return_value.__enter__.return_value.execute.return_value.mappings.return_value.one.return_value = original | changed
                child = MagicMock(side_effect=AssertionError("child must not be launched"))
                mkdir = MagicMock(side_effect=AssertionError("evidence must not be created"))
                with patch.dict(os.environ, {}, clear=True), patch.object(sys, "argv", ["run_tests.py", "--admin-url", SAFE]), \
                        patch.object(runner.sa, "create_engine", return_value=engine), patch.object(runner.subprocess, "run", child), \
                        patch.object(Path, "resolve", lambda self: self), patch.object(Path, "mkdir", mkdir), \
                        self.assertRaises(SystemExit) as stopped:
                    runner.main()
                self.assertEqual(stopped.exception.code, 2)
                child.assert_not_called()
                mkdir.assert_not_called()
                engine.dispose.assert_called_once_with()


if __name__ == "__main__":
    unittest.main(verbosity=2)
