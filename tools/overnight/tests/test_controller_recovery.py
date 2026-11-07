"""Controller crash/recovery regressions with real state stores and no model calls."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.overnight import controller, recover
from tools.overnight.state import StateError, atomic_json, dependency_fingerprint, load_dag, process_lock, read_json
from tools.overnight.tests import test_recover


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

        def fail_tests(round_number):
            output = self.pilot.root / ("tests-" + str(round_number))
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
                patch.object(self.pilot, "run_candidate_tests", side_effect=fail_tests) as tests, \
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


class PrelaunchRecoveryInterleavingTests(unittest.TestCase):
    @contextmanager
    def recovery_case(self):
        case = test_recover.RecoverTests(methodName="test_repeat_is_idempotent_and_never_refunds_the_call")
        case.setUp()
        try:
            yield case
        finally:
            case.tearDown()

    def pilot_for(self, case):
        pilot = object.__new__(controller.Pilot)
        pilot.source, pilot.root, pilot.workspace = case.source, case.run, case.workspace
        pilot.base, pilot.base_ref = test_recover.NEW, test_recover.BASE_REF
        pilot.run_id, pilot.branch = case.run.name, "feature/web-pr06a-" + case.run.name
        pilot.state, pilot.limits = case.state, case.ledger
        pilot.config_sha = hashlib.sha256(case.config.read_bytes()).hexdigest()
        pilot.config = {"max_repairs": 3, "limits": case.ledger.snapshot()["limits"]}
        pilot.codex, pilot.deps, pilot.publish = "never-executed-codex", case.source, False
        return pilot

    def ordinary_resume(self, case, *, unrelated_failure=False):
        pilot = self.pilot_for(case)
        output, calls = io.StringIO(), {}
        with ExitStack() as stack:
            if unrelated_failure:
                stack.enter_context(patch.object(pilot, "execute", side_effect=RuntimeError("synthetic unrelated failure")))
            stack.enter_context(patch.object(controller, "Pilot", return_value=pilot))
            stack.enter_context(patch.object(controller, "git", side_effect=lambda repo, *args, **kwargs:
                case.git(repo, case.run / "recovery-empty-hooks", *args)))
            for name in ("run_codex", "run_sandbox_command", "authentication_status", "sandbox_probe", "prepare_workspace"):
                calls[name] = stack.enter_context(patch.object(controller, name,
                    side_effect=AssertionError("No external execution allowed: " + name)))
            stack.enter_context(patch.object(sys, "argv", ["synthetic-resume", "--source", str(case.source),
                "--base", test_recover.NEW, "--base-ref", test_recover.BASE_REF,
                "--run-id", case.run.name, "--deps", str(case.source), "--codex", pilot.codex]))
            with redirect_stdout(output):
                exit_code = controller.main()
        self.assertEqual(exit_code, 1)
        self.assertIn("tools.overnight.recover", json.loads(output.getvalue())["error"])
        for call in calls.values():
            call.assert_not_called()

    def interrupt_after(self, case, stage):
        original_write, original_cas = recover.atomic_json, recover._cas_change

        def write(path, value):
            original_write(path, value)
            if ((stage == "journal" and path.name == recover.JOURNAL_NAME and value["status"] == "PREPARED")
                    or (stage == "complete" and path.name == recover.JOURNAL_NAME and value["status"] == "COMPLETE")
                    or (stage == "scope" and path.name == "scope.json")):
                raise RuntimeError("synthetic interruption after " + stage)

        def cas(store, before, after, event):
            original_cas(store, before, after, event)
            if (stage, store.path.name) in {("state", "state.json"), ("ledger", "subscription.json")}:
                raise RuntimeError("synthetic interruption after " + stage)

        def git(repo, hooks, *args):
            result = case.git(repo, hooks, *args)
            if stage == "workspace" and args[0] == "checkout":
                raise RuntimeError("synthetic interruption after workspace")
            return result

        with patch.object(recover, "atomic_json", side_effect=write), \
                patch.object(recover, "_cas_change", side_effect=cas), \
                patch.object(recover, "_git", side_effect=git), \
                self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
            case.reconcile()

    def checkpoint(self, case):
        paths = [case.state.path, case.ledger.path, case.run / "scope.json", case.run / recover.JOURNAL_NAME,
                 case.run / "report.json", *(case.run / "develop-0").iterdir()]
        return {str(path.relative_to(case.run)): path.read_bytes() for path in paths}

    def test_each_interrupted_write_survives_ordinary_resume_then_dedicated_recovery(self):
        for stage in ("journal", "workspace", "state", "ledger", "scope"):
            with self.subTest(stage=stage), self.recovery_case() as case:
                before_ledger = case.ledger.snapshot()
                original_logs = {p.name: p.read_bytes() for p in (case.run / "develop-0").iterdir()}
                self.interrupt_after(case, stage)
                self.assertEqual(read_json(case.run / recover.JOURNAL_NAME)["status"], "PREPARED")
                atomic_json(case.run / "report.json", {"synthetic": "existing report must remain unchanged"})
                checkpoint, head = self.checkpoint(case), case.workspace_head
                self.ordinary_resume(case)
                self.assertEqual(self.checkpoint(case), checkpoint)
                self.assertEqual(case.workspace_head, head)
                result = case.reconcile()
                self.assertEqual(result["status"], "reconciled")
                self.assertEqual(case.state.snapshot()["tasks"]["06a"]["state"], "AUDIT")
                self.assertEqual(case.state.snapshot()["base_sha"], test_recover.NEW)
                self.assertEqual(read_json(case.run / "scope.json")["base_sha"], test_recover.NEW)
                self.assertEqual(read_json(case.run / recover.JOURNAL_NAME)["status"], "COMPLETE")
                after_ledger = case.ledger.snapshot()
                self.assertEqual(after_ledger["status"], "READY")
                for key in ("calls", "limits", "started_at_ms", "last_seen_ms", "task_started_at_ms", "repairs"):
                    self.assertEqual(after_ledger[key], before_ledger[key])
                self.assertEqual({p.name: p.read_bytes() for p in (case.run / "develop-0").iterdir()}, original_logs)
                self.assertEqual(case.reconcile()["status"], "already_reconciled")

    def test_exception_guard_preserves_pending_checkpoint_even_for_unrelated_failure(self):
        with self.recovery_case() as case:
            self.interrupt_after(case, "state")
            atomic_json(case.run / "report.json", {"synthetic": "unchanged"})
            before = self.checkpoint(case)
            self.ordinary_resume(case, unrelated_failure=True)
            self.assertEqual(self.checkpoint(case), before)
            self.assertEqual(case.reconcile()["status"], "reconciled")

    def test_complete_marker_allows_resume_and_recovery_cannot_reset_later_attempt(self):
        with self.recovery_case() as case:
            self.interrupt_after(case, "complete")
            self.assertIsNone(recover.pending_recovery_reason(case.run))
            before_ledger = case.ledger.snapshot()
            pilot = self.pilot_for(case)
            dag = {"00": {"depends_on": []}, "01": {"depends_on": ["00"]},
                   "06a": {"depends_on": ["01"], "gates": ["explicit paid scope"], "paid_depends_on": ["11a"]}}
            with process_lock(case.run.parent / "controller.lock", timeout_seconds=1), \
                    patch.object(controller, "git", side_effect=lambda repo, *args, **kwargs:
                        case.git(repo, case.run / "recovery-empty-hooks", *args)), \
                    patch.object(controller, "authentication_status", return_value={"ready": True}), \
                    patch.object(controller, "sandbox_probe", return_value={"passed": True}), \
                    patch.object(controller.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
                    patch.object(controller, "load_dag", return_value=dag), \
                    patch.object(controller, "run_codex", side_effect=AssertionError("No model call allowed")) as model:
                pilot.initialize()
                model.assert_not_called()
            state = case.state.snapshot()
            self.assertEqual(state["tasks"]["06a"]["state"], "DEVELOPING")
            self.assertEqual(state["tasks"]["06a"]["attempt_count"], 2)
            self.assertEqual(case.ledger.snapshot(), before_ledger)
            with self.assertRaisesRegex(StateError, "checkpoint drifted"):
                case.reconcile()
            self.assertEqual(case.state.snapshot(), state)
            self.assertEqual(case.ledger.snapshot(), before_ledger)


if __name__ == "__main__":
    unittest.main()
