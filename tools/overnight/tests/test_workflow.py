"""Workflow fault injection: fake workers, real locked state/usage files, no models."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.overnight import workflow
from tools.overnight.state import StateError, atomic_json, read_json, load_dag
from tools.overnight.budget import DEFAULT_LIMITS

ROOT = Path(__file__).resolve().parents[3]
SCRATCH = ROOT / "work/overnight/workflow-tests"
BASE, TREE, CONTROL = "a" * 40, "b" * 40, "c" * 40


class Crash(BaseException):
    pass


class FakeAdapter:
    def __init__(self):
        self.calls, self.test_calls, self.publish_calls = [], [], []
        self.heads, self.stamps, self.remote = {}, {}, {}
        self.develop_repairs, self.test_failures, self.review_failures = {}, {}, {}
        self.external_test_blocks = set()
        self.unknown, self.auth_failure, self.scope_failure = set(), set(), set()
        self.known_evidence_failure = set()
        self.crash_publish = False
        self.created_prs = 0
        self.ancestry = True

    def audit(self, task_id, spec):
        return deepcopy(spec["audit"])

    def is_ancestor(self, head, base):
        return self.ancestry

    def prepare(self, task_id, spec, base_sha):
        self.heads.setdefault(task_id, base_sha)

    def develop(self, task_id, spec, base_sha, call_id, output, timeout_seconds, feedback):
        self.calls.append((task_id, "develop", call_id))
        output.mkdir(parents=True)
        if task_id in self.unknown:
            raise OSError("synthetic lost terminal outcome")
        self.stamps[task_id] = self.stamps.get(task_id, 0) + 1
        return {"passed": task_id not in self.auth_failure,
                "error_reason": "authentication" if task_id in self.auth_failure else None,
                "usage": {"turns": 1, "input_tokens": 10, "output_tokens": 3},
                "elapsed_seconds": 1, "workspace_stamp": self.stamps[task_id],
                "needs_repair": self._consume(self.develop_repairs, task_id)}

    @staticmethod
    def _consume(counters, task_id):
        value = counters.get(task_id, 0)
        counters[task_id] = max(0, value - 1)
        return value > 0

    def verify_development(self, task_id, spec, result):
        if result["workspace_stamp"] != self.stamps[task_id]:
            raise StateError("development workspace drifted")

    def candidate(self, task_id, spec, base_sha, round_number):
        if task_id in self.scope_failure:
            error = StateError("synthetic forbidden control modification")
            error.scope_violation = True
            raise error
        head = hashlib.sha1((task_id + str(round_number)).encode()).hexdigest()
        self.heads[task_id] = head
        return dict(head_sha=head, tree_sha=TREE, patch_sha256="d" * 64, policy_sha256="e" * 64)

    def verify(self, task_id, spec, base_sha, binding):
        if self.heads[task_id] != binding["head_sha"]:
            raise StateError("candidate HEAD changed")

    def test(self, task_id, spec, binding, output):
        self.test_calls.append(task_id)
        if task_id in self.external_test_blocks:
            raise StateError("synthetic temporary test dependency unavailable")
        output.mkdir(parents=True, exist_ok=True)
        failed = self._consume(self.test_failures, task_id)
        core = {key: binding[key] for key in ("head_sha", "patch_sha256")}
        sessions = {}
        for check in spec["tests"]:
            count = check["completion"]["min_tests"]
            summary = dict(schema_version=1, candidate_binding=core, stage=check["completion"]["stage"],
                nonce="synthetic", complete=True, discovered=count, tests_run=count, completed=count,
                failures=int(failed), errors=0, skipped=0, expected_failures=0, unexpected_successes=0)
            sessions[check["id"]] = dict(passed=not failed, exit_code=int(failed), summary=summary)
        return dict(passed=not failed, candidate_binding=binding, sessions=sessions)

    def review(self, task_id, spec, binding, call_id, output, timeout_seconds, test_receipt):
        self.calls.append((task_id, "review", call_id))
        output.mkdir(parents=True)
        failed = self._consume(self.review_failures, task_id)
        reviewed = dict(verdict="request_changes" if failed else "approve",
                        findings=["synthetic real issue"] if failed else [], limitations=[])
        atomic_json(output / "final.json", reviewed)
        return dict(passed=task_id not in self.known_evidence_failure, reviewed=reviewed,
            model_completion_verified=True,
            error_reason="review_evidence_invalid" if task_id in self.known_evidence_failure else None,
            usage={"turns": 1, "input_tokens": 10, "output_tokens": 3}, elapsed_seconds=1)

    def publish(self, task_id, spec, base_sha, binding, report):
        self.publish_calls.append(task_id)
        if task_id not in self.remote:
            self.created_prs += 1
            self.remote[task_id] = dict(pr_number=self.created_prs,
                pr_url="https://github.com/Mumao233/classroom-os/pull/" + str(self.created_prs))
        if self.crash_publish:
            self.crash_publish = False
            raise Crash("after remote PR creation before confirmation")
        return self.remote[task_id]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name)
        evidence = self.source / "docs/approval.md"
        evidence.parent.mkdir()
        evidence.write_text("Synthetic human approval fixture; never business authorization.\n", encoding="utf-8")
        self.evidence = [{"path": "docs/approval.md", "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}]
        self.now = 1000.0
        # Small synthetic graph for fault isolation; the real28 graph is tested separately.
        self.dag = {key: dict(depends_on=parents, integration_depends_on=[], paid_depends_on=[], gates=[])
                    for key, parents in (("00", []), ("01", ["00"]), ("05a", ["01"]),
                                         ("06a", ["01"]), ("05b", ["05a"]))}
        self.plan = dict(version=2, authorization_id="synthetic-workflow", repository="Mumao233/classroom-os",
            author_login="Mumao233", dag_path="unused-in-internal-fixture.json", enabled_tasks=["05a", "06a"],
            limits=dict(DEFAULT_LIMITS), baselines={key: dict(self.audit("COMPLETE"), approved=True) for key in ("00", "01")},
            tasks={key: self.spec(key) for key in ("05a", "06a", "05b")})
        self.adapter = FakeAdapter()
        self.approvals = self.source / "work/overnight/approvals/synthetic.json"

    def audit(self, status="PARTIAL"):
        return dict(status=status, head_sha=BASE if status == "COMPLETE" else None,
                    tree_sha=TREE if status == "COMPLETE" else None,
                    evidence=self.evidence if status == "COMPLETE" else [], reason="Synthetic fixture")

    def spec(self, key):
        return dict(goal="Incrementally repair synthetic task", acceptance=["synthetic acceptance"],
            review_criteria=["independent read-only review"], phase="poc", title="Synthetic " + key,
            base=dict(ref="main", sha=BASE, confirmed=True, dependency_heads={"01": BASE} if key != "05b" else {}),
            branch="feature/workflow-synthetic-" + key, depends_on=self.dag[key]["depends_on"],
            integration_depends_on=[], paid_depends_on=[], gates=dict(development=[], enablement=[], paid=[]),
            scope=dict(add_prefixes=["synthetic/" + key + "/"], modify_files=[], delete_files=[],
                       deny_prefixes=[], max_files=10, max_bytes=50000),
            model=dict(name="gpt-6.1-sol", reasoning="high"), review_model=dict(name="gpt-6.1-sol", reasoning="high"),
            limits=dict(wall_seconds=7200, max_calls=8, max_repairs=3, call_timeout_seconds=1200),
            tests=[dict(id=stage, kind=stage, command=["{python}", "synthetic-" + stage], timeout_seconds=10,
                completion=dict(marker="OVERNIGHT_TEST_RESULT ", stage=stage, min_tests=1)) for stage in ("candidate", "trusted")],
            audit=self.audit())

    def engine(self, **kwargs):
        return workflow.Workflow(self.source, CONTROL, self.plan, self.dag, self.adapter,
                                 approvals=self.approvals, clock=lambda: self.now, **kwargs)

    def approve(self, *, gates=(), dependencies=(), bases=()):
        self.approvals.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(self.approvals, dict(version=1, authorization_id=self.plan["authorization_id"],
                                        gates=list(gates), dependencies=list(dependencies), bases=list(bases)))

    def state(self, report, key):
        return report["tasks"][key]["effective_state"]

    def test_complete_audit_skips_without_any_model_or_publication(self):
        for key in self.plan["enabled_tasks"]:
            self.plan["tasks"][key]["audit"] = self.audit("COMPLETE")
        engine = self.engine(publish=True)
        report = engine.run()
        self.assertEqual([self.state(report, key) for key in self.plan["enabled_tasks"]], ["SKIPPED", "SKIPPED"])
        self.assertEqual(report["subscription_usage"]["calls"], 0)
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.adapter.publish_calls, [])
        before = engine.ledger.path.read_bytes()
        self.assertEqual(self.engine().run()["subscription_usage"]["calls"], 0)
        self.assertEqual(before, engine.ledger.path.read_bytes())

    def test_real28_dag_loading_does_not_authorize_28_packages(self):
        real = load_dag(ROOT / "docs/web-alpha/architecture/contracts/pr-dependencies.json")
        self.dag = real
        self.plan["enabled_tasks"] = ["06a"]
        self.plan["tasks"] = {"06a": self.spec("06a")}
        self.plan["tasks"]["06a"]["audit"] = self.audit("COMPLETE")
        report = self.engine().run()
        self.assertEqual(len(report["tasks"]), 28)
        self.assertEqual([key for key, value in report["tasks"].items() if value["enabled"]], ["06a"])
        self.assertEqual(report["tasks"]["05a"]["state"], "AUDIT")
        self.assertEqual(self.adapter.calls, [])

    def test_external_block_continues_independent_task_and_stops_without_loop(self):
        self.plan["tasks"]["05a"]["audit"]["status"] = "BLOCKED"
        report = self.engine().run()
        self.assertEqual(self.state(report, "05a"), "BLOCKED")
        self.assertEqual(self.state(report, "06a"), "READY_FOR_HUMAN_REVIEW")
        self.assertEqual([x[:2] for x in self.adapter.calls], [("06a", "develop"), ("06a", "review")])
        self.assertEqual(report["status"], "STOPPED_NO_READY_TASKS")

    def test_scope_violation_fails_task_blocks_downstream_but_not_independent(self):
        self.plan["enabled_tasks"] = ["05b", "05a", "06a"]
        self.adapter.scope_failure.add("05a")
        report = self.engine().run()
        self.assertEqual(self.state(report, "05a"), "FAILED")
        self.assertEqual(self.state(report, "05b"), "BLOCKED")
        self.assertEqual(self.state(report, "06a"), "READY_FOR_HUMAN_REVIEW")
        self.assertNotIn("05b", [x[0] for x in self.adapter.calls])

    def test_ready_review_is_not_human_acceptance(self):
        self.plan["enabled_tasks"] = ["05a", "05b"]
        report = self.engine(publish=True).run()
        self.assertEqual(self.state(report, "05a"), "READY_FOR_HUMAN_REVIEW")
        self.assertEqual(self.state(report, "05b"), "BLOCKED")
        self.assertIn("not human acceptance", report["tasks"]["05b"]["controller_error"])
        self.assertEqual(self.adapter.created_prs, 1)
        self.assertFalse(report["human_approval_inferred"])

    def test_gate_approval_can_arrive_without_resetting_existing_calls(self):
        self.dag["05a"]["gates"] = ["human-gate"]
        self.plan["tasks"]["05a"]["gates"]["development"] = ["human-gate"]
        first = self.engine().run()
        self.assertEqual(self.state(first, "05a"), "BLOCKED")
        calls = list(self.adapter.calls)
        self.approve(gates=[dict(task_id="05a", gate="human-gate", base_sha=BASE, actor="Mumao233", evidence=self.evidence)])
        second = self.engine().run()
        self.assertEqual(self.state(second, "05a"), "READY_FOR_HUMAN_REVIEW")
        self.assertEqual(second["subscription_usage"]["calls"], 4)
        self.assertEqual(self.adapter.calls[:len(calls)], calls)

    def test_wrong_gate_base_does_not_approve_task(self):
        self.dag["05a"]["gates"] = ["human-gate"]
        self.plan["tasks"]["05a"]["gates"]["development"] = ["human-gate"]
        self.approve(gates=[dict(task_id="05a", gate="human-gate", base_sha="f" * 40, actor="Mumao233", evidence=self.evidence)])
        report = self.engine().run()
        self.assertEqual(self.state(report, "05a"), "BLOCKED")
        self.assertEqual(self.state(report, "06a"), "READY_FOR_HUMAN_REVIEW")

    def test_gate_history_cannot_be_removed_or_rewritten(self):
        self.approve(dependencies=[dict(task_id="01", head_sha=BASE, actor="Mumao233", evidence=self.evidence)])
        engine = self.engine()
        engine.run()
        before = engine.ledger.path.read_bytes(), engine.state.path.read_bytes()
        self.approve()
        with self.assertRaises(workflow.GlobalStop):
            self.engine().run()
        self.assertEqual(before, (engine.ledger.path.read_bytes(), engine.state.path.read_bytes()))

    def test_human_acceptance_still_needs_explicit_common_base_containing_parent(self):
        self.plan["enabled_tasks"] = ["05a", "05b"]
        self.plan["tasks"]["05b"]["base"].update(sha=None, confirmed=False)
        first = self.engine().run()
        parent = first["tasks"]["05a"]["head_sha"]
        self.approve(dependencies=[dict(task_id="05a", head_sha=parent, actor="Mumao233", evidence=self.evidence)])
        blocked = self.engine().run()
        self.assertIn("common base", blocked["tasks"]["05b"]["controller_error"])
        self.approve(dependencies=[dict(task_id="05a", head_sha=parent, actor="Mumao233", evidence=self.evidence)],
            bases=[dict(task_id="05b", ref="main", sha=BASE, dependency_heads={"05a": parent}, actor="Mumao233", evidence=self.evidence)])
        complete = self.engine().run()
        self.assertEqual(self.state(complete, "05b"), "READY_FOR_HUMAN_REVIEW")
        self.assertEqual(len([x for x in self.adapter.calls if x[0] == "05a"]), 2)

    def test_common_base_never_automatically_merges_missing_heads(self):
        self.adapter.ancestry = False
        report = self.engine().run()
        self.assertEqual(self.state(report, "05a"), "BLOCKED")
        self.assertIn("no automatic merge", report["tasks"]["05a"]["controller_error"])
        self.assertEqual(self.adapter.calls, [])

    def test_activation_requires_integration_paid_dependencies_and_gates(self):
        spec = self.plan["tasks"]["05a"]
        spec.update(phase="activation", integration_depends_on=["06a"], paid_depends_on=["05b"])
        spec["gates"] = dict(development=["dev"], enablement=["enable"], paid=["paid"])
        self.assertEqual(self.engine()._required(spec), (["01", "05b", "06a"], ["dev", "enable", "paid"]))

    def test_active_child_revalidates_accepted_parent_before_continuing(self):
        self.plan["enabled_tasks"] = ["05a", "05b"]
        self.plan["tasks"]["05b"]["base"].update(sha=None, confirmed=False)
        parent = self.engine().run()["tasks"]["05a"]["head_sha"]
        self.approve(dependencies=[dict(task_id="05a", head_sha=parent, actor="Mumao233", evidence=self.evidence)],
            bases=[dict(task_id="05b", ref="main", sha=BASE, dependency_heads={"05a": parent}, actor="Mumao233", evidence=self.evidence)])
        engine = self.engine()
        transition = engine._transition
        def crash(task_id, target, **kwargs):
            if task_id == "05b" and target == "TESTING":
                raise Crash("child active after development")
            return transition(task_id, target, **kwargs)
        with patch.object(engine, "_transition", side_effect=crash), self.assertRaises(Crash):
            engine.run()
        (engine._round("05a") / "review/final.json").write_text("{}", encoding="utf-8")
        calls = list(self.adapter.calls)
        report = self.engine(publish=True).run()
        self.assertEqual(self.state(report, "05b"), "BLOCKED")
        self.assertEqual(self.adapter.calls, calls)
        self.assertEqual(self.adapter.created_prs, 0)

    def test_task_wall_limit_keeps_global_ledger_available_for_independent_task(self):
        self.plan["tasks"]["05a"]["limits"]["wall_seconds"] = 1
        original = self.adapter.test
        def elapsed(task_id, *args):
            value = original(task_id, *args)
            if task_id == "05a":
                self.now += 2
            return value
        with patch.object(self.adapter, "test", side_effect=elapsed):
            report = self.engine().run()
        self.assertEqual(self.state(report, "05a"), "BLOCKED")
        self.assertEqual(self.state(report, "06a"), "READY_FOR_HUMAN_REVIEW")
        self.assertEqual(report["subscription_usage"]["calls"], 3)
        self.assertEqual(report["status"], "STOPPED_NO_READY_TASKS")

    def test_missing_started_context_does_not_restart_task_clock(self):
        self.plan["enabled_tasks"] = ["05a"]
        engine = self.engine()
        engine.run()
        before = engine.ledger.path.read_bytes()
        (engine._taskdir("05a") / "context.json").unlink()
        report = self.engine().run()
        self.assertEqual(self.state(report, "05a"), "BLOCKED")
        self.assertEqual(before, engine.ledger.path.read_bytes())
        self.assertFalse((engine._taskdir("05a") / "context.json").exists())

    def test_development_test_and_review_share_three_repairs(self):
        self.plan["enabled_tasks"] = ["05a"]
        self.adapter.develop_repairs["05a"] = 1
        self.adapter.test_failures["05a"] = 1
        self.adapter.review_failures["05a"] = 1
        report = self.engine().run()
        self.assertEqual(self.state(report, "05a"), "READY_FOR_HUMAN_REVIEW")
        self.assertEqual(report["tasks"]["05a"]["repairs"], 3)
        self.assertEqual(len(self.adapter.calls), 6)
        self.assertEqual(len({x[2] for x in self.adapter.calls}), 6)

    def test_fourth_failure_stops_after_three_repairs_and_keeps_independent_work(self):
        self.adapter.review_failures["05a"] = 99
        report = self.engine().run()
        self.assertEqual(self.state(report, "05a"), "FAILED")
        self.assertEqual(report["tasks"]["05a"]["repairs"], 3)
        self.assertEqual(len([x for x in self.adapter.calls if x[0] == "05a"]), 8)
        self.assertEqual(self.state(report, "06a"), "READY_FOR_HUMAN_REVIEW")

    def test_task_call_limit_keeps_reservations_but_allows_other_task(self):
        self.plan["tasks"]["05a"]["limits"]["max_calls"] = 2
        self.adapter.review_failures["05a"] = 1
        report = self.engine().run()
        self.assertEqual(self.state(report, "05a"), "BLOCKED")
        self.assertEqual(self.state(report, "06a"), "READY_FOR_HUMAN_REVIEW")
        self.assertEqual(report["subscription_usage"]["calls"], 4)
        self.assertEqual(report["status"], "STOPPED_NO_READY_TASKS")

    def test_batch_call_limit_globally_stops_without_new_allowance(self):
        self.plan["limits"]["batch_max_calls"] = 1
        engine = self.engine()
        report = engine.run()
        self.assertEqual(report["status"], "GLOBAL_STOP")
        self.assertEqual(report["subscription_usage"]["calls"], 1)
        calls = list(self.adapter.calls)
        second = self.engine().run()
        self.assertEqual(second["status"], "GLOBAL_STOP")
        self.assertEqual(self.adapter.calls, calls)

    def test_auth_failure_is_global_with_observed_usage_and_no_fallback(self):
        self.adapter.auth_failure.add("05a")
        report = self.engine().run()
        self.assertEqual(report["status"], "GLOBAL_STOP")
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(report["subscription_usage"]["input_tokens"], 10)
        self.assertEqual(report["extra_api_spend_usd"], 0)

    def test_uncertain_model_never_repeats_on_resume_or_switches_task(self):
        self.adapter.unknown.add("05a")
        first = self.engine().run()
        self.assertEqual(first["status"], "GLOBAL_STOP")
        self.adapter.unknown.clear()
        second = self.engine().run()
        self.assertEqual(second["status"], "GLOBAL_STOP")
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertTrue(second["subscription_usage"]["usage_unknown"])

    def test_receipt_written_before_ledger_settlement_recovers_without_new_model(self):
        self.plan["enabled_tasks"] = ["05a"]
        engine = self.engine()
        original = engine._settle
        with patch.object(engine, "_settle", side_effect=Crash("after receipt before ledger")):
            with self.assertRaises(Crash):
                engine.run()
        self.assertEqual(len(self.adapter.calls), 1)
        report = self.engine().run()
        self.assertEqual(self.state(report, "05a"), "READY_FOR_HUMAN_REVIEW")
        self.assertEqual(len(self.adapter.calls), 2)
        self.assertEqual(report["subscription_usage"]["calls"], 2)

    def test_settled_development_reused_after_state_transition_crash(self):
        self.plan["enabled_tasks"] = ["05a"]
        engine = self.engine()
        transition = engine._transition
        def crash(task_id, state, **kwargs):
            if state == "TESTING":
                raise Crash("after model accounting before testing state")
            return transition(task_id, state, **kwargs)
        with patch.object(engine, "_transition", side_effect=crash), self.assertRaises(Crash):
            engine.run()
        report = self.engine().run()
        self.assertEqual(self.state(report, "05a"), "READY_FOR_HUMAN_REVIEW")
        self.assertEqual(len([x for x in self.adapter.calls if x[1] == "develop"]), 1)

    def test_completed_tests_reused_after_state_transition_crash(self):
        self.plan["enabled_tasks"] = ["05a"]
        engine = self.engine()
        transition = engine._transition
        def crash(task_id, state, **kwargs):
            if state == "REVIEWING":
                raise Crash("after test receipt")
            return transition(task_id, state, **kwargs)
        with patch.object(engine, "_transition", side_effect=crash), self.assertRaises(Crash):
            engine.run()
        report = self.engine().run()
        self.assertEqual(self.state(report, "05a"), "READY_FOR_HUMAN_REVIEW")
        self.assertEqual(self.adapter.test_calls, ["05a"])

    def test_external_test_block_resumes_original_attempt_without_redevelopment(self):
        self.adapter.external_test_blocks.add("05a")
        first = self.engine().run()
        self.assertEqual(self.state(first, "05a"), "BLOCKED")
        self.assertEqual(self.state(first, "06a"), "READY_FOR_HUMAN_REVIEW")
        self.adapter.external_test_blocks.clear()
        second = self.engine().run()
        self.assertEqual(self.state(second, "05a"), "READY_FOR_HUMAN_REVIEW")
        self.assertEqual(second["tasks"]["05a"]["attempt_count"], 1)
        self.assertEqual(len([x for x in self.adapter.calls if x[:2] == ("05a", "develop")]), 1)

    def test_partial_test_transport_evidence_is_preserved_on_resume(self):
        self.plan["enabled_tasks"] = ["05a"]
        original = self.adapter.test
        outputs = []
        def interrupted(task_id, spec, binding, output):
            outputs.append(output)
            if len(outputs) == 1:
                output.mkdir(parents=True)
                (output / "partial.log").write_text("preserve interrupted test output", encoding="utf-8")
                raise StateError("synthetic interrupted test transport")
            return original(task_id, spec, binding, output)
        with patch.object(self.adapter, "test", side_effect=interrupted):
            self.assertEqual(self.state(self.engine().run(), "05a"), "BLOCKED")
            result = self.engine().run()
        self.assertEqual(self.state(result, "05a"), "READY_FOR_HUMAN_REVIEW")
        self.assertNotEqual(outputs[0], outputs[1])
        self.assertTrue((outputs[0] / "partial.log").exists())
        self.assertEqual(len([x for x in self.adapter.calls if x[1] == "develop"]), 1)

    def test_block_checkpoint_is_durable_before_blocked_state_transition(self):
        self.plan["enabled_tasks"] = ["05a"]
        self.adapter.external_test_blocks.add("05a")
        engine = self.engine()
        transition = engine._transition
        def crash(task_id, state, **kwargs):
            if state == "BLOCKED":
                self.assertTrue((engine._taskdir(task_id) / "invalidated.json").exists())
                raise Crash("after blocked intent before state")
            return transition(task_id, state, **kwargs)
        with patch.object(engine, "_transition", side_effect=crash), self.assertRaises(Crash):
            engine.run()
        self.adapter.external_test_blocks.clear()
        report = self.engine().run()
        self.assertEqual(self.state(report, "05a"), "READY_FOR_HUMAN_REVIEW")
        self.assertEqual(report["tasks"]["05a"]["attempt_count"], 1)
        self.assertEqual(len([x for x in self.adapter.calls if x[1] == "develop"]), 1)

    def test_known_completed_review_evidence_failure_is_task_local_and_settled(self):
        self.adapter.known_evidence_failure.add("05a")
        report = self.engine().run()
        self.assertEqual(self.state(report, "05a"), "BLOCKED")
        self.assertEqual(self.state(report, "06a"), "READY_FOR_HUMAN_REVIEW")
        self.assertEqual(report["status"], "STOPPED_NO_READY_TASKS")
        self.assertFalse(report["subscription_usage"]["usage_unknown"])

    def test_candidate_drift_invalidates_ready_evidence_without_model_or_publish(self):
        self.plan["enabled_tasks"] = ["05a"]
        engine = self.engine()
        engine.run()
        calls = list(self.adapter.calls)
        self.adapter.heads["05a"] = "f" * 40
        report = self.engine(publish=True).run()
        self.assertEqual(self.state(report, "05a"), "BLOCKED")
        self.assertEqual(self.adapter.calls, calls)
        self.assertEqual(self.adapter.created_prs, 0)

    def test_missing_trusted_session_cannot_authorize_publication(self):
        self.plan["enabled_tasks"] = ["05a"]
        engine = self.engine()
        engine.run()
        path = engine._round("05a") / "tests.json"
        receipt = read_json(path)
        del receipt["result"]["sessions"]["trusted"]
        atomic_json(path, receipt)
        report = self.engine(publish=True).run()
        self.assertEqual(self.state(report, "05a"), "BLOCKED")
        self.assertEqual(self.adapter.created_prs, 0)

    def test_review_final_drift_invalidates_ready_publication(self):
        self.plan["enabled_tasks"] = ["05a"]
        engine = self.engine()
        engine.run()
        (engine._round("05a") / "review/final.json").write_text("{}", encoding="utf-8")
        report = self.engine(publish=True).run()
        self.assertEqual(self.state(report, "05a"), "BLOCKED")
        self.assertEqual(self.adapter.created_prs, 0)

    def test_publication_crash_reconciles_one_draft_without_duplicate_pr(self):
        self.plan["enabled_tasks"] = ["05a"]
        self.adapter.crash_publish = True
        with self.assertRaises(Crash):
            self.engine(publish=True).run()
        self.assertEqual(self.adapter.created_prs, 1)
        report = self.engine(publish=True).run()
        self.assertEqual(report["tasks"]["05a"]["publication"]["pr_number"], 1)
        self.assertEqual(self.adapter.created_prs, 1)
        self.assertEqual(len(self.adapter.calls), 2)
        self.engine(publish=True).run()
        self.assertEqual(len(self.adapter.publish_calls), 2)

    def test_saved_publication_receipt_survives_confirmation_crash(self):
        self.plan["enabled_tasks"] = ["05a"]
        engine = self.engine(publish=True)
        publish = self.adapter.publish
        def observed(*args):
            return dict(publish(*args), status="created" if len(self.adapter.publish_calls) == 1 else "existing")
        with patch.object(self.adapter, "publish", side_effect=observed):
            with patch.object(engine.state, "confirm_publication", side_effect=Crash("receipt before state")), self.assertRaises(Crash):
                engine.run()
            path = engine._taskdir("05a") / "publication.json"
            original = path.read_bytes()
            report = self.engine(publish=True).run()
        self.assertEqual(report["tasks"]["05a"]["publication"]["pr_number"], 1)
        self.assertEqual(path.read_bytes(), original)
        reconciliations = list((path.parent / "publication-reconciliations").glob("*.json"))
        self.assertEqual(len(reconciliations), 1)
        self.assertEqual(read_json(reconciliations[0])["result"]["status"], "existing")
        self.assertEqual(self.adapter.created_prs, 1)
        self.assertEqual(len(self.adapter.calls), 2)

    def test_changed_publication_mapping_blocks_without_overwriting_first_receipt(self):
        self.plan["enabled_tasks"] = ["05a"]
        engine = self.engine(publish=True)
        with patch.object(engine.state, "confirm_publication", side_effect=Crash("receipt before state")), self.assertRaises(Crash):
            engine.run()
        path = engine._taskdir("05a") / "publication.json"
        original = path.read_bytes()
        self.adapter.remote["05a"] = dict(pr_number=2, pr_url="https://github.com/Mumao233/classroom-os/pull/2")
        report = self.engine(publish=True).run()
        self.assertEqual(self.state(report, "05a"), "BLOCKED")
        self.assertEqual(path.read_bytes(), original)
        self.assertIsNone(report["tasks"]["05a"]["publication"]["pr_number"])
        self.assertEqual(len(self.adapter.calls), 2)

    def test_review_recovery_revalidates_raw_evidence_before_ready(self):
        self.plan["enabled_tasks"] = ["05a"]
        engine = self.engine()
        transition = engine._transition
        def crash(task_id, state, **kwargs):
            if state == "READY_FOR_HUMAN_REVIEW":
                raise Crash("review settled before ready")
            return transition(task_id, state, **kwargs)
        with patch.object(engine, "_transition", side_effect=crash), self.assertRaises(Crash):
            engine.run()
        with patch.object(self.adapter, "verify_review_evidence", create=True,
                          side_effect=StateError("review raw log drift")) as verify:
            report = self.engine().run()
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(self.state(report, "05a"), "BLOCKED")
        self.assertEqual(len(self.adapter.calls), 2)

    def test_test_evidence_drift_before_review_does_not_reserve_unknown_call(self):
        self.plan["enabled_tasks"] = ["05a"]
        engine = self.engine()
        model = engine._model
        def crash(kind, *args, **kwargs):
            if kind == "review":
                raise Crash("before review reservation")
            return model(kind, *args, **kwargs)
        with patch.object(engine, "_model", side_effect=crash), self.assertRaises(Crash):
            engine.run()
        with patch.object(self.adapter, "verify_test_evidence", create=True,
                          side_effect=StateError("test raw log drift")):
            report = self.engine().run()
        self.assertEqual(self.state(report, "05a"), "BLOCKED")
        self.assertNotEqual(report["status"], "GLOBAL_STOP")
        self.assertEqual(report["subscription_usage"]["calls"], 1)
        self.assertFalse(report["unresolved_calls"])
        self.assertEqual(len(self.adapter.calls), 1)

    def test_changed_plan_or_control_identity_cannot_reset_state_or_ledger(self):
        self.plan["enabled_tasks"] = ["05a"]
        engine = self.engine()
        engine.run()
        before = engine.state.path.read_bytes(), engine.ledger.path.read_bytes()
        self.plan["tasks"]["05a"]["limits"]["max_calls"] = 99
        with self.assertRaises(workflow.GlobalStop):
            self.engine().run()
        self.assertEqual(before, (engine.state.path.read_bytes(), engine.ledger.path.read_bytes()))

    def test_missing_initialized_ledger_is_not_recreated(self):
        engine = self.engine()
        engine.initialize()
        engine.ledger.path.unlink()
        with self.assertRaises(workflow.GlobalStop):
            self.engine().run()
        self.assertFalse(engine.ledger.path.exists())

    def test_initialization_crash_preserves_existing_clock_and_allowance(self):
        engine = self.engine()
        with patch.object(engine.state, "initialize", side_effect=Crash("after ledger creation")), self.assertRaises(Crash):
            engine.initialize()
        before = engine.ledger.path.read_bytes()
        self.now += 3
        self.engine().initialize()
        self.assertEqual(before, engine.ledger.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
