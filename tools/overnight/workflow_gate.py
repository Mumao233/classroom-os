"""Generic unittest completion protocol; independent trusted tests stay in control.

Completion records reject early exit and incomplete discovery. They are not an
isolation boundary against arbitrary hostile Python within the child process.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import unittest

MARKER = "OVERNIGHT_TEST_RESULT "
_COUNTERS = ("discovered", "tests_run", "completed", "failures", "errors", "skipped",
             "expected_failures", "unexpected_successes")


def core_binding(binding):
    return {key: binding[key] for key in ("head_sha", "patch_sha256")}


def parse_completion(stdout, *, marker, stage, nonce, binding, min_tests):
    if (not isinstance(marker, str) or not marker or "\n" in marker or "\r" in marker
            or type(min_tests) is not int or min_tests < 1):
        raise ValueError("invalid trusted completion configuration")
    records = [line[len(marker):] for line in stdout.splitlines() if line.startswith(marker)]
    if len(records) != 1:
        raise ValueError("missing or duplicate test completion record")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate completion field")
            result[key] = value
        return result
    try:
        value = json.loads(records[0], object_pairs_hook=pairs)
    except (TypeError, ValueError) as error:
        raise ValueError("malformed or truncated test completion record") from error
    keys = {"schema_version", "stage", "nonce", "candidate_binding", "complete", *_COUNTERS}
    if (not isinstance(value, dict) or set(value) != keys
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["stage"] != stage or value["nonce"] != nonce
            or value["candidate_binding"] not in (core_binding(binding), binding)
            or value["complete"] is not True
            or any(type(value[key]) is not int for key in _COUNTERS)):
        raise ValueError("test completion identity or types do not match")
    if (value["discovered"] < min_tests
            or value["discovered"] != value["tests_run"] or value["tests_run"] != value["completed"]
            or any(value[key] != 0 for key in _COUNTERS[3:])):
        raise ValueError("test session did not finish all required tests without failures or skips")
    return value


def _ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _ids(test)
        else:
            yield test.id()


class CompletionResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.completed_ids = []

    def stopTest(self, test):
        self.completed_ids.append(test.id())
        super().stopTest(test)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("candidate", "trusted"), required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--deps", type=Path, required=True)
    parser.add_argument("--start", type=Path, required=True)
    parser.add_argument("--top", type=Path)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--patch", required=True)
    args = parser.parse_args()
    workspace, start = args.workspace.resolve(strict=True), args.start.resolve(strict=True)
    control_tools = Path(__file__).resolve().parents[1]
    allowed = control_tools if args.stage == "trusted" else workspace
    if start != allowed and allowed not in start.parents:
        raise ValueError("discovery directory is outside the approved stage source")
    top = args.top.resolve(strict=True) if args.top else None
    if top and top != allowed and allowed not in top.parents:
        raise ValueError("discovery top is outside the approved stage source")
    sys.path[:0] = [str(workspace / "web/backend"), str(args.deps.resolve(strict=True))]
    suite = unittest.defaultTestLoader.discover(str(start), pattern=args.pattern,
                                              top_level_dir=str(top) if top else None)
    expected = list(_ids(suite))
    result = unittest.TextTestRunner(verbosity=2, resultclass=CompletionResult).run(suite)
    value = dict(schema_version=1, stage=args.stage, nonce=args.nonce,
                 candidate_binding={"head_sha": args.head, "patch_sha256": args.patch},
                 complete=len(expected) == len(set(expected)) and sorted(expected) == sorted(result.completed_ids),
                 discovered=len(expected), tests_run=result.testsRun, completed=len(result.completed_ids),
                 failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
                 expected_failures=len(result.expectedFailures), unexpected_successes=len(result.unexpectedSuccesses))
    line = MARKER + json.dumps(value, sort_keys=True)
    print(line, flush=True)
    try:
        parse_completion(line, marker=MARKER, stage=args.stage, nonce=args.nonce,
                         binding=value["candidate_binding"], min_tests=1)
    except ValueError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
