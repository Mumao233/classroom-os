"""Controller crash/recovery regressions with real state stores and no model calls."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.overnight import controller
from tools.overnight.state import StateError, dependency_fingerprint, load_dag, read_json


ROOT = Path(__file__).resolve().parents[3]
SCRATCH = ROOT / "work/overnight/controller-recovery-tests"
BASE = "a" * 40


class ControllerRecoveryTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.source = Path(tempfile.mkdtemp(prefix="case_", dir=SCRATCH)).resolve()
        for relative in ("automation/pilot.json", "automation/prompts/06a.md",
                         "docs/web-alpha/architecture/contracts/pr-dependencies.json"):
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
        self.deps = self.source / "synthetic-deps"
        self.deps.mkdir()
        self.pilot = controller.Pilot(self.source, BASE, "feature/overnight-synthetic", "synthetic",
                                      self.deps, "never-executed-codex", publish=False)

    @contextmanager
    def preflight(self):
        def fake_git(repo, *args, **kwargs):
            if args == ("rev-parse", "HEAD"):
                return BASE
            if args and args[0] == "status":
                return ""
            if args == ("remote", "get-url", "origin"):
                return controller.REPOSITORY
            raise AssertionError("Unexpected Git operation in mocked initialization: " + repr(args))

        def fake_prepare(source, workspace, base, branch):
            self.assertEqual(base, BASE)
            self.assertFalse(workspace.exists())
            workspace.mkdir()

        with ExitStack() as stack:
            stack.enter_context(patch.object(controller, "git", side_effect=fake_git))
            auth = stack.enter_context(patch.object(controller, "authentication_status", return_value={"ready": True}))
            stack.enter_context(patch.object(controller, "sandbox_probe", return_value={"passed": True}))
            stack.enter_context(patch.object(controller.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)))
            prepare = stack.enter_context(patch.object(controller, "prepare_workspace", side_effect=fake_prepare))
            model = stack.enter_context(patch.object(controller, "run_codex", side_effect=AssertionError("No model call allowed")))
            yield prepare, auth, model

    def initialize_state(self, stage="AUDIT", *, skip_first_only=False):
        dag = load_dag(self.source / "docs/web-alpha/architecture/contracts/pr-dependencies.json", ["00", "01", "06a"])
        dag["06a"]["gates"] = []
        state = self.pilot.state.initialize(run_id=self.pilot.run_id, base_sha=BASE,
                                            config_sha=self.pilot.config_sha, tasks=dag)
        if stage != "AUDIT" or skip_first_only:
            for task in (["00"] if skip_first_only else ["00", "01"]):
                state = self.pilot.state.transition(task, "SKIPPED", expected_revision=state["revision"],
                    event_id="existing-audit-" + task, head_sha=BASE, evidence="Synthetic prior audit")
        if stage in {"READY", "DEVELOPING", "TESTING"}:
            self.pilot.transition("READY")
        if stage in {"DEVELOPING", "TESTING"}:
            state = self.pilot.state.snapshot()
            self.pilot.state.begin_attempt("06a", "synthetic-attempt", expected_revision=state["revision"],
                event_id="existing-attempt", dependency_fingerprint=dependency_fingerprint(state, "06a"))
        if stage == "TESTING":
            self.pilot.transition("TESTING")

    def assert_developing_once(self):
        state = self.pilot.state.snapshot()
        self.assertEqual(state["tasks"]["06a"]["state"], "DEVELOPING")
        self.assertEqual(state["tasks"]["06a"]["attempt_count"], 1)
        self.assertEqual([state["tasks"][task]["state"] for task in ("00", "01")], ["SKIPPED", "SKIPPED"])

    def test_ledger_only_recovery_preserves_recorded_usage_and_start_time(self):
        ledger = self.pilot.limits.initialize(self.pilot.config["limits"])
        ledger = self.pilot.limits.begin_call("preexisting-complete-call", "06a", expected_revision=ledger["revision"])
        self.pilot.limits.finish_call("preexisting-complete-call", {"turns": 1, "input_tokens": 25, "output_tokens": 7},
                                     expected_revision=ledger["revision"])
        before = self.pilot.limits.snapshot()
        self.assertFalse(self.pilot.state.path.exists())
        with self.preflight() as (prepare, _, model):
            self.pilot.initialize()
            prepare.assert_called_once()
            model.assert_not_called()
        self.assertEqual(before, self.pilot.limits.snapshot())
        self.assert_developing_once()

    def test_audit_recovery_finishes_partial_dependency_checkpoint(self):
        self.pilot.limits.initialize(self.pilot.config["limits"])
        before = self.pilot.limits.snapshot()
        self.initialize_state(skip_first_only=True)
        with self.preflight() as (prepare, _, model):
            self.pilot.initialize()
            prepare.assert_called_once()
            model.assert_not_called()
        self.assert_developing_once()
        self.assertEqual(before, self.pilot.limits.snapshot())
        self.assertNotIn("audited-00", self.pilot.state.snapshot()["events"])

    def test_ready_recovery_reuses_clean_pinned_workspace(self):
        self.pilot.limits.initialize(self.pilot.config["limits"])
        before = self.pilot.limits.snapshot()
        self.initialize_state("READY")
        self.pilot.workspace.mkdir()
        marker = self.pilot.workspace / "preserve-marker.txt"
        marker.write_text("existing checkout", encoding="utf-8")
        with self.preflight() as (prepare, _, model):
            self.pilot.initialize()
            prepare.assert_not_called()
            model.assert_not_called()
        self.assertEqual(marker.read_text(encoding="utf-8"), "existing checkout")
        self.assertEqual(before, self.pilot.limits.snapshot())
        self.assert_developing_once()

    def test_reinitializing_developing_state_does_not_repeat_attempt_or_preflight(self):
        with self.preflight():
            self.pilot.initialize()
        before_state, before_ledger = self.pilot.state.snapshot(), self.pilot.limits.snapshot()
        with self.preflight() as (prepare, auth, model):
            self.pilot.initialize()
            prepare.assert_not_called()
            auth.assert_not_called()
            model.assert_not_called()
        self.assertEqual(before_state, self.pilot.state.snapshot())
        self.assertEqual(before_ledger, self.pilot.limits.snapshot())

    def test_changed_limits_do_not_reset_existing_ledger(self):
        self.pilot.limits.initialize(dict(self.pilot.config["limits"], task_max_calls=1))
        before = self.pilot.limits.snapshot()
        with self.preflight() as (prepare, _, model), self.assertRaisesRegex(StateError, "reset or change"):
            self.pilot.initialize()
        prepare.assert_not_called()
        model.assert_not_called()
        self.assertEqual(before, self.pilot.limits.snapshot())

    def test_multiline_failure_repairs_use_one_authority_and_stop_after_three(self):
        self.pilot.limits.initialize(self.pilot.config["limits"])
        before_ledger = self.pilot.limits.snapshot()
        self.initialize_state("DEVELOPING")
        self.pilot.workspace.mkdir()
        failure = "FAIL: synthetic invariant\nTraceback:\nAssertionError: expected durable final\n"

        def fail_tests(codex, workspace, command, output, timeout, **kwargs):
            output.mkdir(parents=True)
            (output / "stderr.log").write_text(failure, encoding="utf-8")
            return {"passed": False, "exit_code": 1}

        def fake_candidate(workspace, base, round_number):
            head = format(round_number + 1, "040x")
            return head, {"valid": True, "errors": [], "head_sha": head, "patch_sha256": "c" * 64}

        with patch.object(self.pilot, "initialize"), \
                patch.object(self.pilot, "verify_candidate", return_value={"head_sha": "a" * 40, "patch_sha256": "c" * 64}), \
                patch.object(self.pilot, "model", return_value=({"passed": True}, self.pilot.root)) as model, \
                patch.object(controller, "commit_candidate", side_effect=fake_candidate), \
                patch.object(controller, "run_sandbox_command", side_effect=fail_tests) as tests, \
                patch.object(controller, "git", return_value=""):
            self.assertFalse(self.pilot.execute())
        task = self.pilot.state.snapshot()["tasks"]["06a"]
        self.assertEqual(task["state"], "FAILED")
        self.assertEqual(task["repairs"], 3)
        self.assertEqual(tests.call_count, 4)  # Initial attempt, then three repairs.
        self.assertEqual(model.call_count, 4)
        self.assertEqual([call.args[:2] for call in model.call_args_list], [("develop", number) for number in range(4)])
        self.assertNotIn("\n", task["reason"])
        self.assertEqual(read_json(self.pilot.root / "test-failure.json")["detail"], failure)
        self.assertEqual(before_ledger, self.pilot.limits.snapshot())
        self.assertEqual(self.pilot.limits.snapshot()["repairs"], {})
        self.assertEqual(read_json(self.pilot.root / "report.json")["task"]["state"], "FAILED")

    def test_missing_optional_allowed_directory_does_not_break_git_staging(self):
        workspace = self.pilot.workspace
        workspace.mkdir()
        controller.git(workspace, "init")
        (workspace / "README.md").write_text("Synthetic baseline\n", encoding="utf-8")
        controller.git(workspace, "add", "README.md")
        controller.git(workspace, "-c", "commit.gpgsign=false", "commit", "-m", "Synthetic baseline")
        base = controller.git(workspace, "rev-parse", "HEAD")
        for relative, body in (
            ("web/backend/classroom_web/asr_sim/protocol.py", "class Protocol: pass\n"),
            ("web/backend/tests/asr_sim/test_protocol.py", "# Synthetic staging fixture\n"),
            ("docs/web-alpha/pr06a/README.md", "Synthetic staging fixture\n"),
        ):
            path = workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        self.assertFalse((workspace / "tools/pr06a").exists())
        # Disable synthetic fixture signing without changing any real Git config.
        controller.git(workspace, "config", "commit.gpgsign", "false")
        head, report = controller.commit_candidate(workspace, base, 0)
        self.assertTrue(report["valid"], report)
        self.assertNotEqual(head, base)
        self.assertEqual(len(report["files"]), 3)
        self.assertEqual(controller.git(workspace, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
