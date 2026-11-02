"""Real isolated-checkout regressions for PR-03a's narrow historical EOL rule.

The checked repository and historical review evidence remain untouched. Every
case creates its own local clone and retains command/EOL/blob/raw-byte evidence.
No database, application launcher, Provider, or remote Git operation is used.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
REVIEWED = "fa041e4c687be3d4b125f1d74024e7417ad19102"
PROTECTED_BASE = "cb5f73c288fe013763e1e3ee9ebc3eb94a44a6b0"
GUARD = "tools/pr03a/remediation/check_protected.py"
HISTORICAL = "tools/pr03a/remediation/selftest_runner.py"
HISTORICAL_CRLF_SHA256 = "c5a321b794c5cbbe9c548641c08e137bab45f24193f2a1302c5e718b4a366c44"


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class ProtectedCheckoutTests(unittest.TestCase):
    work: Path
    output: Path
    env: dict[str, str]
    scenarios: list[dict[str, object]]
    fixed_guard: bytes
    regression_sha256: str

    @classmethod
    def setUpClass(cls):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        cls.work = ROOT / "work/pr03a/checkout-remediation" / stamp
        cls.output = ROOT / "outputs/pr03a/checkout-remediation" / stamp
        cls.work.mkdir(parents=True, exist_ok=False)
        cls.output.mkdir(parents=True, exist_ok=False)
        (cls.work / "empty-gitconfig").write_bytes(b"")
        cls.env = {name: value for name, value in os.environ.items()
                   if not name.upper().startswith(("GIT_", "PG"))
                   and name.upper() not in {"PYTHONPATH", "PYTHONPYCACHEPREFIX",
                                            "PYTEST_ADDOPTS", "PYTEST_PLUGINS"}}
        cls.env.update(GIT_CONFIG_GLOBAL=str(cls.work / "empty-gitconfig"),
                       GIT_CONFIG_NOSYSTEM="1", PYTHONDONTWRITEBYTECODE="1",
                       PYTHONNOUSERSITE="1", PYTHONUTF8="1",
                       TEMP=str(cls.work), TMP=str(cls.work), TMPDIR=str(cls.work))
        cls.scenarios = []
        cls.fixed_guard = (ROOT / GUARD).read_bytes()
        cls.regression_sha256 = sha256(Path(__file__).read_bytes())
        print("Checkout regression evidence:", cls.output, flush=True)

    @classmethod
    def tearDownClass(cls):
        (cls.output / "results.json").write_text(json.dumps({
            "reviewed_commit": REVIEWED, "protected_base": PROTECTED_BASE,
            "python": sys.version, "executable": sys.executable,
            "fixed_guard_sha256": sha256(cls.fixed_guard),
            "regression_source_sha256": cls.regression_sha256,
            "work": str(cls.work), "output": str(cls.output),
            "scenarios": cls.scenarios,
            "scope": "Local clones only; no existing worktree, historical evidence, DB or remote writes",
        }, indent=2), encoding="utf-8")

    def setUp(self):
        self.label = self.id().split(".")[-1]
        self.folder = self.output / self.label
        self.folder.mkdir(exist_ok=False)
        # Keep the clone path short enough for ordinary Windows Git path
        # handling; the full descriptive case name belongs in the evidence.
        self.clone = self.work / ("case-" + self.label.split("_")[1])
        self.commands = []
        self.run_command(["git", "clone", "--local", "--no-hardlinks", "--no-checkout",
                          "--", str(ROOT), str(self.clone)], ROOT, expected=0)
        self.git("config", "core.autocrlf", "true")
        self.git("-c", "core.autocrlf=true", "checkout", "--detach", REVIEWED)
        self.assertEqual(self.git("status", "--porcelain=v2").strip(), b"")
        self.assertEqual(self.git("rev-parse", "HEAD").decode().strip(), REVIEWED)
        self.initial = self.snapshot()
        self.assertIn("i/lf", self.initial["ls_files_eol"])
        self.assertIn("w/lf", self.initial["ls_files_eol"])
        self.assertIn("attr/text eol=lf", self.initial["ls_files_eol"])
        self.assertEqual(self.initial["crlf_count"], 0)

    def tearDown(self):
        (self.folder / "commands.json").write_text(json.dumps(self.commands, indent=2), encoding="utf-8")

    def run_command(self, command, cwd, *, expected=None):
        number = len(self.commands)
        result = subprocess.run(command, cwd=cwd, env=self.env, capture_output=True, timeout=240)
        stdout = self.folder / f"command-{number:02d}.stdout.log"
        stderr = self.folder / f"command-{number:02d}.stderr.log"
        stdout.write_bytes(result.stdout)
        stderr.write_bytes(result.stderr)
        self.commands.append({"command": command, "cwd": str(cwd), "exit_code": result.returncode,
                              "stdout": str(stdout), "stderr": str(stderr)})
        if expected is not None:
            self.assertEqual(result.returncode, expected, result.stderr.decode("utf-8", "replace"))
        return result

    def git(self, *args):
        return self.run_command(["git", *args], self.clone, expected=0).stdout

    def snapshot(self, target_path=HISTORICAL):
        raw = (self.clone / target_path).read_bytes()
        canonical = self.git("cat-file", "--filters", "--path=" + target_path,
                             PROTECTED_BASE + ":" + target_path)
        return {
            "path": target_path,
            "head": self.git("rev-parse", "HEAD").decode().strip(),
            "status_porcelain_v2": self.git("status", "--porcelain=v2").decode("utf-8"),
            "core_autocrlf": self.git("config", "--get", "core.autocrlf").decode().strip(),
            "ls_files_eol": self.git("ls-files", "--eol", "--", target_path).decode().strip(),
            "expected_git_blob": self.git("rev-parse", PROTECTED_BASE + ":" + target_path).decode().strip(),
            "current_git_blob": self.git("hash-object", "--path=" + target_path,
                                         "--", str(self.clone / target_path)).decode().strip(),
            "canonical_checkout_sha256": sha256(canonical), "raw_sha256": sha256(raw),
            "lf_count": raw.count(b"\n"), "crlf_count": raw.count(b"\r\n"),
            "bare_cr_count": raw.count(b"\r") - raw.count(b"\r\n"),
        }

    def execute_guard(self, *, expected, scenario, target_path=HISTORICAL):
        before = self.snapshot(target_path)
        result = self.run_command([sys.executable, "-B", GUARD], self.clone, expected=expected)
        reports = sorted((self.clone / "outputs/pr03a").glob("*/protection/*/protected-files.json"))
        self.assertEqual(len(reports), 1)
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        (self.folder / "protected-files.json").write_bytes(reports[0].read_bytes())
        after = self.snapshot(target_path)
        self.assertEqual(before, after, "the protection command mutated the checked source")
        observation = {"scenario": scenario, "exit_code": result.returncode,
                       "initial_clean_checkout": self.initial, "before": before, "after": after,
                       "report": str(self.folder / "protected-files.json"),
                       "passed_field": report["passed"],
                       "checked_guard_sha256": sha256((self.clone / GUARD).read_bytes())}
        (self.folder / "observation.json").write_text(json.dumps(observation, indent=2), encoding="utf-8")
        self.scenarios.append(observation)
        return report

    def apply_fix(self):
        # Only this authorized adapter is overlaid; the historical tool, its
        # archived validation and .gitattributes remain exactly as checked out.
        (self.clone / GUARD).write_bytes(self.fixed_guard)

    def test_00_original_reviewed_clean_lf_reproduces_rejection(self):
        report = self.execute_guard(expected=1, scenario="before: original exact fa041e4 clean LF")
        item = report["original_tool_bytes"][HISTORICAL]
        self.assertTrue(item["raw_bytes_equal"])
        self.assertTrue(item["git_content_equal"])
        self.assertFalse(item["historical_reviewed_checkout_equal"])
        self.assertFalse(item["preserved"])
        self.assertFalse(report["passed"])

    def test_01_clean_lf_with_autocrlf_true_passes(self):
        self.apply_fix()
        report = self.execute_guard(expected=0, scenario="after: canonical LF with core.autocrlf=true")
        item = report["original_tool_bytes"][HISTORICAL]
        self.assertTrue(item["raw_bytes_equal"])
        self.assertTrue(item["git_content_equal"])
        self.assertFalse(item["historical_reviewed_checkout_equal"])
        self.assertTrue(item["preserved"])
        self.assertTrue(item["raw_checkout_allowed"])
        self.assertEqual(item["failure_reasons"], [])
        self.assertEqual(report["tool_preservation_failures"], {})
        self.assertTrue(report["passed"])

    def test_02_exact_historical_crlf_with_same_git_blob_passes(self):
        self.apply_fix()
        path = self.clone / HISTORICAL
        canonical = self.git("cat-file", "--filters", "--path=" + HISTORICAL,
                             PROTECTED_BASE + ":" + HISTORICAL)
        self.assertNotIn(b"\r", canonical)
        historical = canonical.replace(b"\n", b"\r\n")
        self.assertEqual(sha256(historical), HISTORICAL_CRLF_SHA256)
        path.write_bytes(historical)
        report = self.execute_guard(expected=0, scenario="after: exact archived historical CRLF")
        item = report["original_tool_bytes"][HISTORICAL]
        self.assertFalse(item["raw_bytes_equal"])
        self.assertTrue(item["git_content_equal"])
        self.assertTrue(item["historical_reviewed_checkout_equal"])
        self.assertTrue(item["preserved"])
        self.assertTrue(item["raw_checkout_allowed"])
        self.assertEqual(item["failure_reasons"], [])
        self.assertEqual(report["tool_preservation_failures"], {})
        self.assertTrue(report["passed"])

    def test_03_actual_code_change_is_rejected_and_located(self):
        self.apply_fix()
        path = self.clone / HISTORICAL
        original = path.read_bytes()
        self.assertEqual(original.count(b"class RunnerSafety(unittest.TestCase):"), 1)
        changed = original.replace(b"class RunnerSafety(unittest.TestCase):",
                                   b"class ChangedRunnerSafety(unittest.TestCase):", 1)
        path.write_bytes(changed.replace(b"\n", b"\r\n"))
        report = self.execute_guard(expected=1, scenario="after: real protected Python code mutation")
        item = report["original_tool_bytes"][HISTORICAL]
        self.assertFalse(item["git_content_equal"])
        self.assertFalse(item["raw_bytes_equal"])
        self.assertFalse(item["historical_reviewed_checkout_equal"])
        self.assertFalse(item["preserved"])
        self.assertIn(HISTORICAL, report["mismatches"])
        self.assertEqual(set(item["failure_reasons"]),
                         {"git_blob_mismatch", "unapproved_checkout_bytes"})
        self.assertEqual(report["tool_preservation_failures"][HISTORICAL], item["failure_reasons"])
        self.assertFalse(report["passed"])

    def test_04_same_blob_mixed_eol_is_rejected(self):
        self.apply_fix()
        path = self.clone / HISTORICAL
        canonical = path.read_bytes()
        self.assertNotIn(b"\r", canonical)
        mixed = canonical.replace(b"\n", b"\r\n", 1)
        self.assertNotEqual(sha256(mixed), HISTORICAL_CRLF_SHA256)
        path.write_bytes(mixed)
        report = self.execute_guard(expected=1, scenario="after: same Git blob but unapproved mixed EOL")
        item = report["original_tool_bytes"][HISTORICAL]
        self.assertTrue(item["git_content_equal"])
        self.assertFalse(item["raw_bytes_equal"])
        self.assertFalse(item["historical_reviewed_checkout_equal"])
        self.assertFalse(item["raw_checkout_allowed"])
        self.assertFalse(item["preserved"])
        self.assertEqual(item["failure_reasons"], ["unapproved_checkout_bytes"])
        self.assertEqual(report["tool_preservation_failures"][HISTORICAL], ["unapproved_checkout_bytes"])
        self.assertFalse(report["passed"])

    def test_05_other_protected_tool_does_not_gain_crlf_exception(self):
        self.apply_fix()
        other = "tools/pr03a/selftest_runner.py"
        path = self.clone / other
        canonical = self.git("cat-file", "--filters", "--path=" + other,
                             PROTECTED_BASE + ":" + other)
        self.assertNotIn(b"\r", canonical)
        changed = canonical.replace(b"\n", b"\r\n")
        self.assertNotEqual(changed, canonical)
        path.write_bytes(changed)
        report = self.execute_guard(expected=1, scenario="after: no CRLF compatibility for another protected tool",
                                    target_path=other)
        item = report["original_tool_bytes"][other]
        self.assertTrue(item["git_content_equal"])
        self.assertFalse(item["raw_bytes_equal"])
        self.assertFalse(item["raw_checkout_allowed"])
        self.assertFalse(item["preserved"])
        self.assertEqual(item["failure_reasons"], ["unapproved_checkout_bytes"])
        self.assertEqual(report["tool_preservation_failures"][other], ["unapproved_checkout_bytes"])
        self.assertFalse(report["passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
