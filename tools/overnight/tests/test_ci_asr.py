"""Exercise the hosted parent gate with real early-exit child processes."""
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
import io

from tools.overnight import ci_asr, controller

ROOT = Path(__file__).resolve().parents[3]
SCRATCH = ROOT / "work/overnight/ci-gate-tests"


class HostedGateTests(unittest.TestCase):
    def execute(self, test_source):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / "source"
        source.mkdir()
        controller.git(source, "init", "--quiet", "--template=")
        controller.git(source, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "Synthetic baseline")
        gate = source / "tools/overnight/asr_gate.py"
        gate.parent.mkdir(parents=True)
        gate.write_bytes((ROOT / "tools/overnight/asr_gate.py").read_bytes())
        tests = source / "web/backend/tests/asr_sim/test_probe.py"
        tests.parent.mkdir(parents=True)
        tests.write_text(test_source, encoding="utf-8")
        controller.git(source, "add", "--all")
        controller.git(source, "-c", "commit.gpgsign=false", "commit", "-m", "Synthetic candidate")
        output = root / "evidence/asr.json"
        with redirect_stdout(io.StringIO()):
            passed = ci_asr.run(source, output)
        return passed, json.loads(output.read_text(encoding="utf-8"))

    def test_exit_zero_without_completion_cannot_pass_hosted_gate(self):
        passed, report = self.execute("import os\nos._exit(0)\n")
        self.assertFalse(passed)
        candidate = report["sessions"]["candidate"]
        self.assertEqual(candidate["exit_code"], 0)
        self.assertFalse(candidate["passed"])
        self.assertIn("missing", candidate["error"])
        self.assertIn("trusted", report["sessions"])

    def test_trivial_candidate_tests_do_not_replace_trusted_assertions(self):
        passed, report = self.execute("import unittest\nclass T(unittest.TestCase):\n def test_ok(self): pass\n")
        self.assertFalse(passed)
        self.assertTrue(report["sessions"]["candidate"]["passed"])
        self.assertFalse(report["sessions"]["trusted"]["passed"])


if __name__ == "__main__":
    unittest.main()
