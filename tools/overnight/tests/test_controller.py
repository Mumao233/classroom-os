import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.overnight.controller import validate_config, validate_request, commit_candidate, Pilot
from tools.overnight.state import StateError
from tools.overnight.budget import SubscriptionLimits

ROOT = Path(__file__).resolve().parents[3]


class ControllerTests(unittest.TestCase):
    def test_only_explicit_simulation_subscription_scope(self):
        config = json.loads((ROOT / "automation/pilot.json").read_text())
        validate_config(config)
        for field, value in [("enabled_tasks", ["06b"]), ("simulation_only", False),
                             ("extra_api_spend_usd", 50), ("max_repairs", 4),
                             ("author_login", "codex"), ("call_timeout_seconds", 99999)]:
            modified = dict(config, **{field: value})
            with self.subTest(field=field), self.assertRaises(StateError):
                validate_config(modified)

    def test_request_cannot_smuggle_commands_or_different_base(self):
        request = dict(version=1, task="06a", simulation_only=True,
                       repository="Mumao233/classroom-os", base_sha="a" * 40, request_id="123")
        validate_request(request, "a" * 40)
        for extra in ({"command": "touch file"}, {"base_sha": "b" * 40}, {"task": "05a"},
                      {"simulation_only": False}, {"request_id": "../../evil"}):
            with self.assertRaises(StateError):
                validate_request(dict(request, **extra), "a" * 40)

    def test_complete_call_resume_does_not_invoke_model_again(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "work") as directory:
            pilot = object.__new__(Pilot)
            pilot.root = Path(directory)
            pilot.limits = SubscriptionLimits(pilot.root / "limits.json")
            pilot.limits.initialize()
            doc = pilot.limits.begin_call("develop-0", "06a", expected_revision=0)
            pilot.limits.finish_call("develop-0", {"turns": 1, "input_tokens": 10, "output_tokens": 5},
                                     expected_revision=doc["revision"])
            output = pilot.root / "develop-0"
            output.mkdir()
            receipt = {"passed": True, "elapsed_seconds": 1}
            (output / "receipt.json").write_text(json.dumps(receipt))
            with patch("tools.overnight.controller.run_codex") as invoke:
                result, _ = pilot.model("develop", 0, "prompt")
                self.assertEqual(result, receipt)
                invoke.assert_not_called()

    def test_uncertain_call_resume_cannot_spend_again(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "work") as directory:
            pilot = object.__new__(Pilot)
            pilot.root = Path(directory)
            pilot.limits = SubscriptionLimits(pilot.root / "limits.json")
            pilot.limits.initialize()
            pilot.limits.begin_call("develop-0", "06a", expected_revision=0)
            with patch("tools.overnight.controller.run_codex") as invoke, self.assertRaises(StateError):
                pilot.model("develop", 0, "prompt")
            invoke.assert_not_called()

    def test_failed_execution_keeps_observed_usage(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "work") as directory:
            pilot = object.__new__(Pilot)
            pilot.root = Path(directory)
            pilot.workspace, pilot.codex = pilot.root / "workspace", "synthetic-codex"
            pilot.config = {"call_timeout_seconds": 30, "model": "gpt-6.1-sol"}
            pilot.limits = SubscriptionLimits(pilot.root / "limits.json")
            pilot.limits.initialize()
            (pilot.root / "develop-0").mkdir()
            usage = {"turns": 1, "input_tokens": 20, "output_tokens": 7}
            result = {"passed": False, "elapsed_seconds": 12, "usage": usage, "error_reason": "timeout"}
            with patch("tools.overnight.controller.run_codex", return_value=result), self.assertRaises(StateError):
                pilot.model("develop", 0, "prompt")
            ledger = pilot.limits.snapshot()
            self.assertEqual(ledger["calls"]["develop-0"]["usage"], usage)
            self.assertEqual(ledger["status"], "BLOCKED")
            self.assertEqual(pilot.limits.usage_totals(ledger)["output_tokens"], 7)

    def test_uncommitted_control_edits_rejected(self):
        with patch("tools.overnight.controller.git", side_effect=["", "a" * 40, " M AGENTS.md"]), \
             patch("pathlib.Path.exists", return_value=False), \
             patch("tools.overnight.controller.validate_candidate", return_value={"valid": True, "errors": []}):
            _, report = commit_candidate(Path("."), "b" * 40, 0)
            self.assertFalse(report["valid"])

    def test_clean_but_moved_head_cannot_reuse_evidence(self):
        pilot = object.__new__(Pilot)
        pilot.root, pilot.workspace, pilot.base = Path("."), Path("."), "a" * 40
        with patch("tools.overnight.controller.read_json", return_value={"head_sha": "b" * 40}), \
             patch("tools.overnight.controller.git", return_value="c" * 40), self.assertRaises(StateError):
            pilot.verify_candidate()

    def test_complete_review_cannot_be_reused_for_other_candidate(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "work") as directory:
            pilot = object.__new__(Pilot)
            pilot.root = Path(directory)
            pilot.limits = SubscriptionLimits(pilot.root / "limits.json")
            pilot.limits.initialize()
            doc = pilot.limits.begin_call("review-0", "06a", expected_revision=0)
            pilot.limits.finish_call("review-0", {"turns": 1, "input_tokens": 1, "output_tokens": 1},
                                     expected_revision=doc["revision"])
            output = pilot.root / "review-0"
            output.mkdir()
            (output / "receipt.json").write_text(json.dumps({"candidate_binding": {"head_sha": "a" * 40}}))
            with patch("tools.overnight.controller.run_codex") as invoke, self.assertRaises(StateError):
                pilot.model("review", 0, "prompt", binding={"head_sha": "b" * 40})
            invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
