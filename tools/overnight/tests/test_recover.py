"""Recovery uses synthetic ledgers and mocked Git; no real run is recovered."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.overnight.budget import SubscriptionLimits
from tools.overnight import recover
from tools.overnight.state import StateError, StateStore, atomic_json, dependency_fingerprint, read_json


ROOT = Path(__file__).resolve().parents[3]
OLD, NEW = "a" * 40, "b" * 40
BASE_REF = "feature/overnight-synthetic"


class RecoverTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / "work/overnight/tests"
        root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="recover-", dir=root)
        self.source = Path(self.tmp.name) / "source"
        (self.source / "automation").mkdir(parents=True)
        self.config = self.source / "automation/pilot.json"
        self.config.write_text('{"simulation_only":true}\n', encoding="utf-8")
        config_sha = hashlib.sha256(self.config.read_bytes()).hexdigest()
        self.run = self.source / "work/overnight/runs/pilot-synthetic"
        self.workspace = self.run / "workspace"
        self.workspace.mkdir(parents=True)
        self.state = StateStore(self.run / "state.json")
        self.ledger = SubscriptionLimits(self.run / "subscription.json")
        doc = self.state.initialize(run_id="pilot-synthetic", base_sha=OLD, config_sha=config_sha,
            tasks={"00": {"depends_on": []}, "01": {"depends_on": ["00"]}, "06a": {"depends_on": ["01"]}})
        for task_id in ("00", "01"):
            doc = self.state.transition(task_id, "SKIPPED", expected_revision=doc["revision"], event_id="skip-" + task_id,
                head_sha=OLD, evidence="synthetic baseline verified")
        doc = self.state.transition("06a", "READY", expected_revision=doc["revision"], event_id="ready")
        fp = dependency_fingerprint(doc, "06a")
        doc = self.state.begin_attempt("06a", "attempt-1", expected_revision=doc["revision"], event_id="attempt",
                                      dependency_fingerprint=fp)
        self.state.transition("06a", "BLOCKED", expected_revision=doc["revision"], event_id="blocked",
                              attempt_id="attempt-1", dependency_fingerprint=fp, reason="interrupted")
        self.ledger.initialize()
        doc = self.ledger.begin_call("develop-0", "06a", expected_revision=0)
        self.ledger.block("develop-0", "interrupted", expected_revision=doc["revision"])
        atomic_json(self.run / "scope.json", {"base_sha": OLD, "config_sha": config_sha, "base_ref": BASE_REF,
            "simulation_only": True, "extra_api_spend_usd": 0, "author": "Mumao233",
            "unapproved_paid_dependencies": ["11a"], "unapproved_paid_gates": ["explicit paid scope"]})
        logs = self.run / "develop-0"
        logs.mkdir()
        (logs / "stdout.jsonl").write_bytes(b"")
        (logs / "stderr.log").write_bytes(recover.KNOWN_ERROR)
        atomic_json(logs / "process.json", {"passed": False, "exit_code": 1, "elapsed_seconds": .1, "error_reason": None})
        for name in ("result.json", "receipt.json"):
            atomic_json(logs / name, {"passed": False, "exit_code": 1, "elapsed_seconds": .2,
                                     "error_reason": "execution_failed", "usage": None})
        self.workspace_head, self.dirty, self.source_dirty, self.remote_candidate = OLD, False, False, False
        self.git_calls = []
        self.patcher = patch("tools.overnight.recover._git", side_effect=self.git)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def git(self, repo, hooks, *args):
        self.git_calls.append((repo, args))
        if args == ("rev-parse", "HEAD"):
            return NEW if repo == self.source else self.workspace_head
        if args[0] == "status":
            return " M changed.txt" if (self.source_dirty if repo == self.source else self.dirty) else ""
        if args[:2] == ("remote", "get-url"):
            return recover.REMOTE
        if args == ("branch", "--show-current"):
            return "feature/web-pr06a-pilot-synthetic"
        if args[0] == "merge-base":
            return ""
        if args[0] == "ls-remote":
            return NEW + " refs/heads/feature/web-pr06a-pilot-synthetic" if self.remote_candidate else ""
        if args[0] == "fetch":
            return ""
        if args[0] == "checkout":
            self.assertEqual(args, ("checkout", "-B", "feature/web-pr06a-pilot-synthetic", NEW))
            self.workspace_head = NEW
            return ""
        raise AssertionError("unexpected recovery Git operation")

    def reconcile(self):
        return recover.reconcile_prelaunch(self.source, self.run, NEW, BASE_REF)

    def assert_no_checkout(self):
        self.assertFalse(any(args[0] == "checkout" for repo, args in self.git_calls))

    def test_success_preserves_failed_call_reservation_history_limits_and_clock(self):
        before_state, before_ledger = self.state.snapshot(), self.ledger.snapshot()
        result = self.reconcile()
        self.assertEqual(result["status"], "reconciled")
        state, ledger = self.state.snapshot(), self.ledger.snapshot()
        self.assertEqual(state["base_sha"], NEW)
        self.assertEqual(state["tasks"]["06a"]["state"], "AUDIT")
        for key in ("attempt_id", "attempt_ids", "attempt_count", "repairs", "dependency_fingerprint"):
            self.assertEqual(state["tasks"]["06a"][key], before_state["tasks"]["06a"][key])
        for key in ("calls", "limits", "started_at_ms", "last_seen_ms", "task_started_at_ms", "repairs"):
            self.assertEqual(ledger[key], before_ledger[key])
        self.assertEqual(ledger["status"], "READY")
        self.assertTrue(self.ledger.usage_totals(ledger)["usage_unknown"])
        self.assertEqual(self.ledger.usage_totals(ledger)["calls"], 1)
        self.assertEqual(ledger["events"][:-1], before_ledger["events"])
        self.assertEqual(read_json(self.run / "scope.json")["base_sha"], NEW)
        self.assertEqual(read_json(self.run / recover.JOURNAL_NAME)["status"], "COMPLETE")

    def test_repeat_is_idempotent_and_never_refunds_the_call(self):
        self.reconcile()
        state, ledger = self.state.snapshot(), self.ledger.snapshot()
        result = self.reconcile()
        self.assertEqual(result["status"], "already_reconciled")
        self.assertEqual(self.state.snapshot(), state)
        self.assertEqual(self.ledger.snapshot(), ledger)
        self.assertEqual(sum(args[0] == "checkout" for repo, args in self.git_calls), 1)

    def test_restarts_after_state_cas_before_ledger_cas(self):
        original = recover._cas_change
        def crash(store, before, after, event):
            if store.path.name == "subscription.json":
                raise RuntimeError("synthetic process crash")
            return original(store, before, after, event)
        with patch("tools.overnight.recover._cas_change", side_effect=crash), self.assertRaises(RuntimeError):
            self.reconcile()
        self.assertEqual(self.state.snapshot()["base_sha"], NEW)
        self.assertEqual(self.ledger.snapshot()["status"], "BLOCKED")
        self.assertEqual(self.workspace_head, NEW)
        self.assertEqual(read_json(self.run / recover.JOURNAL_NAME)["status"], "PREPARED")
        self.reconcile()
        self.assertEqual(self.ledger.snapshot()["status"], "READY")
        self.assertEqual(self.ledger.usage_totals(self.ledger.snapshot())["calls"], 1)

    def test_rejects_any_model_output_or_another_stderr(self):
        stdout, stderr = self.run / "develop-0/stdout.jsonl", self.run / "develop-0/stderr.log"
        for data in (b" ", b'{"type":"thread.started"}\n', b'{"type":"turn.completed"}\n'):
            stdout.write_bytes(data)
            with self.assertRaises(StateError):
                self.reconcile()
        stdout.write_bytes(b"")
        for data in (b"quota_exhausted", b"authentication failed", recover.KNOWN_ERROR + b"more output"):
            stderr.write_bytes(data)
            with self.assertRaises(StateError):
                self.reconcile()
        self.assert_no_checkout()
        self.assertFalse((self.run / recover.JOURNAL_NAME).exists())

    def test_rejects_dirty_source_workspace_remote_candidate_and_config_drift(self):
        for flag in ("dirty", "source_dirty", "remote_candidate"):
            setattr(self, flag, True)
            with self.subTest(flag=flag), self.assertRaises(StateError):
                self.reconcile()
            setattr(self, flag, False)
        self.config.write_text('{"simulation_only":false}', encoding="utf-8")
        with self.assertRaises(StateError):
            self.reconcile()
        self.assert_no_checkout()

    def test_rejects_contradictory_process_reason_or_candidate_binding(self):
        process = self.run / "develop-0/process.json"
        original = read_json(process)
        for change in ({"error_reason": "quota_exhausted"}, {"error_reason": "authentication"}, {"exit_code": 0}):
            atomic_json(process, dict(original, **change))
            with self.subTest(change=change), self.assertRaises(StateError):
                self.reconcile()
        atomic_json(process, original)
        receipt = self.run / "develop-0/receipt.json"
        value = read_json(receipt)
        value["candidate_binding"] = "c" * 40
        atomic_json(receipt, value)
        with self.assertRaises(StateError):
            self.reconcile()
        self.assert_no_checkout()

    def test_rejects_quota_block_budget_change_and_exhausted_call_limit(self):
        original = self.ledger.snapshot()
        for problem in ("quota_exhausted", "extra_api", "call_limit"):
            current = deepcopy(original)
            if problem == "quota_exhausted":
                current["block_reason"] = "quota_exhausted"
            elif problem == "extra_api":
                current["limits"]["extra_api_spend_usd"] = 1
            else:
                current["limits"]["batch_max_calls"] = 1
            atomic_json(self.ledger.path, current)
            with self.subTest(problem=problem), self.assertRaises(StateError):
                self.reconcile()
        self.assert_no_checkout()

    def test_completed_recovery_cannot_reset_later_usage(self):
        self.reconcile()
        ledger = self.ledger.snapshot()
        self.ledger.begin_call("develop-0-attempt-2", "06a", expected_revision=ledger["revision"])
        current = self.ledger.snapshot()
        with self.assertRaises(StateError):
            self.reconcile()
        self.assertEqual(self.ledger.snapshot(), current)
        self.assertEqual(self.ledger.usage_totals(current)["calls"], 2)


if __name__ == "__main__":
    unittest.main()
