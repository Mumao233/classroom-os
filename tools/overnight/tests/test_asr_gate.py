"""Real subprocess regressions for incomplete/early-exit test sessions."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.overnight import asr_gate

ROOT = Path(__file__).resolve().parents[3]
SCRATCH = ROOT / "work/overnight/test-gates"
BINDING = {"head_sha": "a" * 40, "patch_sha256": "b" * 64}


def summary(stage="candidate", count=1):
    return dict(schema_version=1, stage=stage, nonce="fixture", candidate_binding=BINDING,
                complete=True, discovered=count, tests_run=count, completed=count,
                failures=0, errors=0, skipped=0, expected_failures=0, unexpected_successes=0)


def evidence():
    return dict(passed=True, candidate_binding=BINDING, verified_checkout={"head_sha": BINDING["head_sha"]},
                sessions={stage: dict(passed=True, exit_code=0, summary=summary(stage, count))
                          for stage, count in (("candidate", 1), ("trusted", asr_gate.TRUSTED_TESTS))})


class GateTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.tests = self.repo / "web/backend/tests/asr_sim"
        self.tests.mkdir(parents=True)
        self.deps = self.repo / "deps"
        self.deps.mkdir()

    def execute(self, source, stage="candidate"):
        (self.tests / "test_probe.py").write_text(source, encoding="utf-8")
        return subprocess.run([sys.executable, "-I", "-B", str(ROOT / "tools/overnight/asr_gate.py"),
            "--stage", stage, "--workspace", str(self.repo), "--deps", str(self.deps),
            "--nonce", "fixture", "--head", BINDING["head_sha"], "--patch", BINDING["patch_sha256"]],
            cwd=self.repo, capture_output=True, text=True, encoding="utf-8", timeout=20)

    def parse(self, process, stage="candidate"):
        return asr_gate.parse_summary(process.stdout, stage=stage, nonce="fixture", binding=BINDING)

    def test_normal_discovery_has_valid_completion(self):
        process = self.execute("import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(self.parse(process)["tests_run"], 1)

    def test_import_exit_zero_has_no_completion_and_cannot_authorize_review(self):
        process = self.execute("import os\nos._exit(0)\n")
        self.assertEqual(process.returncode, 0)
        with self.assertRaisesRegex(ValueError, "missing"):
            self.parse(process)
        with self.assertRaises(ValueError):
            asr_gate.require_evidence(dict(passed=True, candidate_binding=BINDING), BINDING)

    def test_exit_zero_inside_running_test_has_no_completion(self):
        process = self.execute("import os,unittest\nclass T(unittest.TestCase):\n def test_exit(self): os._exit(0)\n")
        self.assertEqual(process.returncode, 0)
        with self.assertRaisesRegex(ValueError, "missing"):
            self.parse(process)

    def test_zero_skipped_systemexit_and_assertion_failure_all_fail_closed(self):
        for source in ("# zero tests\n", "raise SystemExit(0)\n",
            "import unittest\nclass T(unittest.TestCase):\n @unittest.skip('synthetic')\n def test_skip(self): pass\n",
            "import unittest\nclass T(unittest.TestCase):\n def test_bad(self): self.fail('synthetic')\n"):
            with self.subTest(source=source):
                process = self.execute(source)
                self.assertNotEqual(process.returncode, 0)
                with self.assertRaises(ValueError):
                    self.parse(process)

    def test_candidate_tests_cannot_replace_trusted_acceptance(self):
        # Candidate asserts nothing about its broken implementation and passes;
        # the independently launched controller-owned checks still reject it.
        module = self.repo / "web/backend/classroom_web/asr_sim"
        module.mkdir(parents=True)
        (module / "__init__.py").write_text("class Simulator: pass\nclass ProtocolError(ValueError): pass\n", encoding="utf-8")
        process = self.execute("import unittest\nclass T(unittest.TestCase):\n def test_ok(self): pass\n")
        self.assertEqual(process.returncode, 0)
        self.parse(process)
        trusted = self.execute("# tests are not imported by trusted stage\n", stage="trusted")
        self.assertNotEqual(trusted.returncode, 0)
        with self.assertRaises(ValueError):
            self.parse(trusted, "trusted")

    def test_duplicate_truncated_wrong_binding_and_incomplete_completion_rejected(self):
        valid = asr_gate.MARKER + json.dumps(summary())
        for text in (valid + "\n" + valid, valid[:-4], "Ran 1 test\nOK", asr_gate.MARKER + "{}"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                asr_gate.parse_summary(text, stage="candidate", nonce="fixture", binding=BINDING)
        for change in ({"tests_run": 0}, {"completed": 0}, {"complete": False},
                       {"tests_run": True}, {"completed": True}, {"schema_version": True},
                       {"skipped": 1}, {"nonce": "old-session"}, {"candidate_binding": {"head_sha": "c" * 40}}):
            text = asr_gate.MARKER + json.dumps(dict(summary(), **change))
            with self.subTest(change=change), self.assertRaises(ValueError):
                asr_gate.parse_summary(text, stage="candidate", nonce="fixture", binding=BINDING)

    def test_review_requires_both_sessions_not_just_exit_zero(self):
        valid = evidence()
        asr_gate.require_evidence(valid, BINDING)
        for stage in ("candidate", "trusted"):
            changed = copy.deepcopy(valid)
            del changed["sessions"][stage]
            with self.assertRaises(ValueError):
                asr_gate.require_evidence(changed, BINDING)
        changed = copy.deepcopy(valid)
        changed["sessions"]["candidate"]["summary"]["complete"] = False
        with self.assertRaises(ValueError):
            asr_gate.require_evidence(changed, BINDING)


if __name__ == "__main__":
    unittest.main()
