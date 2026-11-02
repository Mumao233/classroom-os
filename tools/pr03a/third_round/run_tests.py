"""Use the guarded PR-03a runner with third-round PostgreSQL log evidence."""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import sys
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-url", required=True)
    parser.add_argument("--postgres-log", required=True, type=Path)
    parser.add_argument("--suite", choices=("probes", "targeted", "full"), default="full")
    parser.add_argument("--test-path", action="append", default=[],
                        help="Explicit backend tests path; only with --suite targeted")
    args = parser.parse_args()
    path = args.postgres_log.resolve()
    expected = (ROOT / "outputs/pr03a/third-round").resolve()
    if expected not in path.parents or path.name != "postgres.log" or not path.is_file():
        parser.error("PostgreSQL log must be an existing synthetic third-round evidence postgres.log")
    spec = importlib.util.spec_from_file_location("pr03a_guarded_runner", ROOT / "tools/pr03a/remediation/run_tests.py")
    assert spec is not None and spec.loader is not None
    runner = cast(Any, importlib.util.module_from_spec(spec))
    spec.loader.exec_module(runner)
    if args.test_path and args.suite != "targeted":
        parser.error("--test-path is allowed only for targeted runs")
    if args.suite == "probes":
        runner.PYTEST_TARGETS = ("tests/integration/test_pr03a_second_review_regressions.py",)
        runner.MINIMUM_PREVIOUS_BACKEND_CASES = 7
    elif args.suite == "targeted":
        targets = args.test_path or [
            "tests/integration/test_local_reconciler_third_round.py",
            "tests/integration/test_local_reconciler_migrations.py",
            "tests/integration/test_identity_recovery.py",
        ]
        checked = []
        test_root = (ROOT / "web/backend/tests").resolve()
        for target in targets:
            resolved = (ROOT / "web/backend" / target).resolve()
            if test_root not in resolved.parents or not resolved.is_file() or not resolved.name.startswith("test_"):
                parser.error("targeted paths must be existing backend test modules")
            checked.append(str(resolved))
        runner.PYTEST_TARGETS = tuple(checked)
        runner.MINIMUM_PREVIOUS_BACKEND_CASES = 1
    runner.SUITE = args.suite
    runner.EVIDENCE_CATEGORY = "third-round/" + args.suite
    # Validate before any connection/evidence creation, then preserve the
    # existing runner's actual server identity verification and network guard.
    runner.validate_test_database_target(args.admin_url)
    os.environ["PR03A_THIRD_ROUND_PG_LOG"] = str(path)
    sys.argv = [str(ROOT / "tools/pr03a/remediation/run_tests.py"), "--admin-url", args.admin_url]
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
