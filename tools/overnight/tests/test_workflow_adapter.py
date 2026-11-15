"""Generic actions against synthetic local repositories; never invoke a model."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.overnight import executor, validation
from tools.overnight.budget import DEFAULT_LIMITS
from tools.overnight.state import StateError, read_json
from tools.overnight.workflow import Workflow
from tools.overnight.workflow_adapter import ProductionAdapter, ScopeViolation, StageFailure
from tools.overnight.workflow_gate import MARKER, parse_completion


ROOT = Path(__file__).resolve().parents[3]
SCRATCH = ROOT / "work/overnight/workflow-adapter-tests"
GATE = ROOT / "tools/overnight/workflow_gate.py"
BINDING = {"head_sha": "1" * 40, "tree_sha": "2" * 40,
           "patch_sha256": "3" * 64, "policy_sha256": "4" * 64}


def summary(**updates):
    value = dict(schema_version=1, stage="candidate", nonce="a" * 32,
                 candidate_binding={key: BINDING[key] for key in ("head_sha", "patch_sha256")},
                 complete=True, discovered=1, tests_run=1, completed=1,
                 failures=0, errors=0, skipped=0, expected_failures=0, unexpected_successes=0)
    value.update(updates)
    return value


class CompletionTests(unittest.TestCase):
    def parse(self, output, **options):
        return parse_completion(output, marker=MARKER, stage="candidate", nonce="a" * 32,
                                binding=BINDING, min_tests=1, **options)

    def test_complete_core_and_full_binding_records(self):
        for binding in (summary()["candidate_binding"], BINDING):
            expected = summary(candidate_binding=binding)
            self.assertEqual(self.parse("noise\n" + MARKER + json.dumps(expected)), expected)

    def test_rejects_zero_incomplete_counts_failures_skips_and_booleans(self):
        changes = [dict(discovered=0, tests_run=0, completed=0), dict(discovered=2),
                   dict(completed=0), dict(complete=False), dict(schema_version=True),
                   dict(discovered=True), dict(tests_run=True), dict(errors=True),
                   dict(failures=1), dict(errors=1), dict(skipped=1),
                   dict(expected_failures=1), dict(unexpected_successes=1),
                   dict(stage="trusted"), dict(nonce="b" * 32), dict(candidate_binding=BINDING | {"head_sha": "f" * 40})]
        for update in changes:
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.parse(MARKER + json.dumps(summary(**update)))

    def test_rejects_duplicate_truncated_extra_and_missing_completion(self):
        valid = MARKER + json.dumps(summary())
        for output in ("", "ordinary passing-looking text", valid + "\n" + valid,
                       valid[:-2], MARKER + json.dumps(summary(extra=True)),
                       valid.replace('"complete": true', '"complete": false, "complete": true')):
            with self.subTest(output=output[:80]), self.assertRaises(ValueError):
                self.parse(output)

    def test_trusted_minimum_is_configuration_not_legacy_four(self):
        result = parse_completion(MARKER + json.dumps(summary(stage="trusted")), marker=MARKER,
            stage="trusted", nonce="a" * 32, binding=BINDING, min_tests=1)
        self.assertEqual(result["discovered"], 1)
        with self.assertRaises(ValueError):
            parse_completion(MARKER + json.dumps(summary(stage="trusted")), marker=MARKER,
                stage="trusted", nonce="a" * 32, binding=BINDING, min_tests=2)

    def test_real_child_zero_tests_and_early_exit_never_pass(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="completion_", dir=SCRATCH))
        workspace, deps = root / "workspace", root / "deps"
        workspace.mkdir()
        deps.mkdir()
        directory = workspace / "tests"
        directory.mkdir()
        command = [sys.executable, "-I", "-B", str(GATE), "--stage", "candidate",
                   "--workspace", str(workspace), "--deps", str(deps), "--start", str(directory),
                   "--nonce", "a" * 32, "--head", BINDING["head_sha"], "--patch", BINDING["patch_sha256"]]
        bodies = [None, "import os\nos._exit(0)\n",
                  "import unittest\nclass Check(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n"]
        for index, body in enumerate(bodies):
            if body:
                (directory / "test_case.py").write_text(body, encoding="utf-8")
            child = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=20)
            if index == 2:
                self.assertEqual(child.returncode, 0, child.stderr)
                self.assertEqual(self.parse(child.stdout)["completed"], 1)
            else:
                with self.assertRaises(ValueError):
                    self.parse(child.stdout)
                if index == 1:
                    self.assertEqual(child.returncode, 0)  # Exit code alone is insufficient.

    def test_trusted_gate_cannot_discover_candidate_tests(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="stage_", dir=SCRATCH))
        child = subprocess.run([sys.executable, "-I", "-B", str(GATE), "--stage", "trusted",
            "--workspace", str(root), "--deps", str(root), "--start", str(root),
            "--nonce", "a" * 32, "--head", BINDING["head_sha"], "--patch", BINDING["patch_sha256"]],
            capture_output=True, text=True, timeout=20)
        self.assertNotEqual(child.returncode, 0)
        self.assertNotIn(MARKER, child.stdout)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="adapter_", dir=SCRATCH))
        self.source, self.deps = self.root / "source", self.root / "deps"
        self.source.mkdir()
        self.deps.mkdir()
        (self.deps / "fixed.txt").write_text("fixed dependency bytes", encoding="utf-8")
        self.run = self.source / "outputs/run"
        self.env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Synthetic Test", GIT_AUTHOR_EMAIL="synthetic@example.invalid",
                        GIT_COMMITTER_NAME="Synthetic Test", GIT_COMMITTER_EMAIL="synthetic@example.invalid")
        self.git("init", "--quiet", "--template=")
        self.git("config", "core.autocrlf", "false")
        self.write(self.source, ".gitignore", "outputs/\nhelper.py\n")
        self.write(self.source, "README.md", "Pinned baseline\n")
        self.write(self.source, "web/backend/classroom_web/__init__.py", "")
        self.write(self.source, "web/backend/classroom_web/existing.py", "VALUE = 0\n")
        self.write(self.source, "automation/review.schema.json", "{}\n")
        self.write(self.source, "docs/evidence.json", '{"observed":true}\n')
        self.write(self.source, "tools/overnight/workflow_gate.py", GATE.read_text(encoding="utf-8"))
        self.write(self.source, "tools/overnight/acceptance/synthetic/test_trusted.py",
                   "import unittest\nfrom classroom_web.synthetic.impl import VALUE\n"
                   "class Trusted(unittest.TestCase):\n def test_actual_value(self): self.assertEqual(VALUE, 42)\n")
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", "Synthetic pinned control")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.spec = dict(goal="Implement a synthetic isolated constant", acceptance=["VALUE equals 42"],
            review_criteria=["verify trusted test"], phase="poc", base={"ref": "main", "sha": self.base, "confirmed": True},
            branch="feature/workflow-synthetic", title="Synthetic test candidate",
            scope={"add_prefixes": ["web/backend/classroom_web/synthetic/", "web/backend/tests/synthetic/"],
                   "modify_files": [], "delete_files": [], "deny_prefixes": [], "max_files": 20, "max_bytes": 300000},
            model={"name": "gpt-6.1-sol", "reasoning": "high"},
            review_model={"name": "gpt-6.1-sol", "reasoning": "xhigh"},
            tests=[self._test_spec("candidate"), self._test_spec("trusted")],
            audit={"status": "COMPLETE", "head_sha": self.base,
                   "tree_sha": self.git("rev-parse", "HEAD^{tree}").strip(),
                   "evidence": [{"path": "docs/evidence.json", "sha256": hashlib.sha256(
                       (self.source / "docs/evidence.json").read_bytes()).hexdigest()}], "reason": "Synthetic evidence"})
        self.adapter = ProductionAdapter(self.source, self.base, self.run, self.deps, codex="never-invoke-codex")
        self.sandbox_calls = []

    def git(self, *args, repo=None):
        return subprocess.run(["git", "--no-optional-locks", "-C", str(repo or self.source),
            "-c", "core.hooksPath=", "-c", "commit.gpgsign=false", *args], env=self.env,
            capture_output=True, text=True, check=True, timeout=30).stdout

    @staticmethod
    def write(root, relative, body):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    @staticmethod
    def _test_spec(kind):
        start = ("{workspace}/web/backend/tests/synthetic" if kind == "candidate"
                 else "{control}/tools/overnight/acceptance/synthetic")
        return dict(id=kind, kind=kind, command=["{python}", "-I", "-B",
            "{control}/tools/overnight/workflow_gate.py", "--stage", kind, "--workspace", "{workspace}",
            "--deps", "{deps}", "--nonce", "{nonce}", "--head", "{head_sha}", "--patch", "{patch_sha256}",
            "--start", start], timeout_seconds=20,
            completion=dict(marker=MARKER, stage=kind, min_tests=1))

    def worker(self, value=42):
        worker = self.adapter.prepare("synthetic", self.spec, self.base)
        self.write(worker, "web/backend/classroom_web/synthetic/__init__.py", "")
        self.write(worker, "web/backend/classroom_web/synthetic/impl.py", f"VALUE = {value}\n")
        self.write(worker, "web/backend/tests/synthetic/test_candidate.py",
                   "import unittest\nclass Candidate(unittest.TestCase):\n def test_smoke(self): self.assertTrue(True)\n")
        return worker

    def bound(self, value=42):
        worker = self.worker(value)
        return worker, self.adapter.candidate("synthetic", self.spec, self.base, 0)

    def sandbox(self, codex, checkout, command, output, timeout_seconds, **kwargs):
        self.assertEqual(codex, "never-invoke-codex")
        self.assertIs(kwargs["readonly"], True)
        self.sandbox_calls.append(dict(checkout=checkout, command=command, output=output, options=kwargs))
        output.mkdir(parents=True)
        child = subprocess.run(command, cwd=checkout, capture_output=True, text=True, timeout=timeout_seconds)
        (output / "stdout.jsonl").write_text(child.stdout, encoding="utf-8")
        (output / "stderr.log").write_text(child.stderr, encoding="utf-8")
        return dict(passed=child.returncode == 0, exit_code=child.returncode, timed_out=False)

    def _tested(self, value=42):
        worker, binding = self.bound(value)
        with patch("tools.overnight.workflow_adapter.executor.run_sandbox_command", side_effect=self.sandbox):
            receipt = self.adapter.test("synthetic", self.spec, binding, self.run / "actions/test")
        return worker, binding, receipt

    @staticmethod
    def fake_model(*args, verdict="request_changes", final_body=None, **kwargs):
        output = args[2]
        output.mkdir(parents=True, exist_ok=True)
        final_body = final_body or json.dumps({"verdict": verdict,
            "findings": ["Please revise"] if verdict == "request_changes" else [], "limitations": []})
        (output / "final.json").write_text(final_body, encoding="utf-8")
        events = [{"type": "thread.started", "thread_id": "synthetic-review"},
                  {"type": "item.completed", "item": {"id": "answer", "type": "agent_message", "text": final_body}},
                  {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}}]
        (output / "stdout.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
        (output / "stderr.log").write_text("", encoding="utf-8")
        evidence = executor.verify_completed_output(output)
        return {"passed": True, "exit_code": 0, "usage": evidence["usage"],
                "model_completion_verified": True, "completion_evidence": evidence}

    def test_control_outside_source_pinned_clean_worker_and_candidate_reuse(self):
        self.write(self.source, "helper.py", "hidden dependency\n")
        worker, binding = self.bound()
        control, _ = self.adapter._control()
        self.assertNotIn(self.source, control.parents)
        self.assertFalse((control / "helper.py").exists())
        self.assertFalse((worker / "helper.py").exists())
        self.assertEqual(self.adapter.prepare("synthetic", self.spec, self.base), worker)
        self.adapter.verify("synthetic", self.spec, self.base, binding)
        self.assertTrue(self.adapter.is_ancestor(self.base, self.base))

    def test_empty_diff_and_out_of_scope_have_distinct_failure_classes(self):
        worker = self.adapter.prepare("synthetic", self.spec, self.base)
        with self.assertRaises(StageFailure):
            self.adapter.candidate("synthetic", self.spec, self.base, 0)
        self.write(worker, "README.md", "out of scope\n")
        with self.assertRaises(ScopeViolation):
            self.adapter.candidate("synthetic", self.spec, self.base, 0)

    def test_protected_or_existing_file_edit_cannot_use_add_directory_grant(self):
        self.spec["scope"]["add_prefixes"].append("web/backend/classroom_web/newarea/")
        worker = self.worker()
        self.write(worker, "web/backend/classroom_web/newarea/AGENTS.md", "forged authority\n")
        with self.assertRaises(ScopeViolation):
            self.adapter.candidate("synthetic", self.spec, self.base, 0)

    def test_output_reuse_and_worker_overlap_are_rejected(self):
        worker = self.adapter.prepare("synthetic", self.spec, self.base)
        output = self.run / "actions/used"
        self.adapter._empty_output(output)
        self.write(output, "receipt.json", "original")
        for bad in (output, worker / "logs", worker.parent, self.source / "outside"):
            with self.subTest(path=bad), self.assertRaises(StateError):
                self.adapter._empty_output(bad)
        self.assertEqual((output / "receipt.json").read_text(), "original")

    def test_development_stamp_configuration_and_repair_write_grants(self):
        self.spec["scope"]["modify_files"].append("web/backend/classroom_web/synthetic/impl.py")
        worker, _ = self.bound()
        self.write(worker, "helper.py", "ignored worker data\n")
        with patch("tools.overnight.workflow_adapter.executor.run_codex", side_effect=self.fake_model) as model:
            result = self.adapter.develop("synthetic", self.spec, self.base, "call1", self.run / "actions/develop", 30, [])
        self.adapter.verify_development("synthetic", self.spec, result)
        options = model.call_args.kwargs
        self.assertEqual(options["model"], self.spec["model"]["name"])
        self.assertEqual(options["reasoning_effort"], "high")
        self.assertIn(worker / "web/backend/classroom_web/synthetic/impl.py", options["write_paths"])
        self.assertEqual(len(options["write_paths"]), len(set(options["write_paths"])))
        self.assertNotIn(worker / "helper.py", options["write_paths"])
        self.assertEqual(result["usage"]["output_tokens"], 20)
        self.write(worker, "helper.py", "changed ignored bytes\n")
        with self.assertRaises(StateError):
            self.adapter.verify_development("synthetic", self.spec, result)
        with self.assertRaises(StateError):
            self.adapter.verify_development("synthetic", self.spec, dict(result, workspace_stamp=None))

    def test_development_post_validation_failure_preserves_usage(self):
        self.worker()
        with patch("tools.overnight.workflow_adapter.executor.run_codex", side_effect=self.fake_model), \
                patch.object(self.adapter, "_stamp", side_effect=StateError("changed")):
            result = self.adapter.develop("synthetic", self.spec, self.base, "call1", self.run / "actions/develop", 30, [])
        self.assertFalse(result["passed"])
        self.assertEqual(result["usage"]["input_tokens"], 100)

    def test_real_candidate_and_trusted_tests_use_pinned_readonly_snapshot(self):
        worker, binding, receipt = self._tested()
        self.assertTrue(receipt["passed"], receipt)
        self.assertEqual(set(receipt["sessions"]), {"candidate", "trusted"})
        self.assertNotEqual(Path(receipt["checkout_path"]), worker)
        self.assertEqual(receipt["candidate_binding"], binding)
        self.assertEqual(len(self.sandbox_calls), 2)
        self.assertNotEqual(receipt["sessions"]["candidate"]["nonce"], receipt["sessions"]["trusted"]["nonce"])
        self.assertEqual(len(self.adapter._test_evidence("synthetic", self.spec, binding, receipt)), 4)

    def test_trivial_candidate_tests_cannot_substitute_for_trusted_assertions(self):
        _, _, receipt = self._tested(value=41)
        self.assertTrue(receipt["sessions"]["candidate"]["passed"])
        self.assertFalse(receipt["sessions"]["trusted"]["passed"])
        self.assertFalse(receipt["passed"])

    def test_review_uses_new_snapshot_and_semantic_rejection_preserves_transport_usage(self):
        _, binding, receipt = self._tested()
        with patch("tools.overnight.workflow_adapter.executor.run_codex", side_effect=self.fake_model) as model:
            result = self.adapter.review("synthetic", self.spec, binding, "review1", self.run / "actions/review", 30, receipt)
        self.assertTrue(result["passed"])
        self.assertFalse(result["approved"])
        self.assertEqual(result["reviewed"]["verdict"], "request_changes")
        self.assertEqual(result["usage"]["output_tokens"], 20)
        self.assertNotEqual(result["checkout_path"], receipt["checkout_path"])
        self.assertIs(model.call_args.kwargs["readonly"], True)
        self.assertEqual(model.call_args.kwargs["reasoning_effort"], "xhigh")
        self.assertIn('"test_logs"', model.call_args.args[3])
        self.assertEqual(model.call_args.kwargs["schema"].parent.name, "automation")

    def test_changed_logs_dependencies_and_receipt_command_block_review_before_model(self):
        _, binding, receipt = self._tested()
        mutated = deepcopy(receipt)
        mutated["sessions"]["trusted"]["command"][0] = "untrusted"
        with patch("tools.overnight.workflow_adapter.executor.run_codex") as model:
            for evidence in (None, mutated):
                with self.assertRaises(StateError):
                    self.adapter.review("synthetic", self.spec, binding, "review1", self.run / "actions/review", 30, evidence)
            log = Path(receipt["sessions"]["candidate"]["logs"][0]["path"])
            original = log.read_bytes()
            log.write_bytes(original + b"tamper")
            with self.assertRaises(StateError):
                self.adapter.review("synthetic", self.spec, binding, "review1", self.run / "actions/review", 30, receipt)
            log.write_bytes(original)
            (self.deps / "fixed.txt").write_text("changed dependency")
            with self.assertRaises(StateError):
                self.adapter.review("synthetic", self.spec, binding, "review1", self.run / "actions/review", 30, receipt)
            model.assert_not_called()

    def test_changed_candidate_and_test_snapshot_block_review_before_model(self):
        worker, binding, receipt = self._tested()
        with patch("tools.overnight.workflow_adapter.executor.run_codex") as model:
            self.write(worker, "web/backend/classroom_web/synthetic/impl.py", "VALUE = 40\n")
            with self.assertRaises(StateError):
                self.adapter.review("synthetic", self.spec, binding, "review1", self.run / "actions/review", 30, receipt)
            self.write(worker, "web/backend/classroom_web/synthetic/impl.py", "VALUE = 42\n")
            self.write(Path(receipt["checkout_path"]), "helper.py", "ignored extra file\n")
            with self.assertRaises(StateError):
                self.adapter.review("synthetic", self.spec, binding, "review1", self.run / "actions/review", 30, receipt)
            model.assert_not_called()

    def test_review_post_validation_failure_preserves_usage(self):
        _, binding, receipt = self._tested()
        def model(*args, **kwargs):
            result = self.fake_model(*args, verdict="approve", **kwargs)
            self.write(args[1], "helper.py", "review checkout drift\n")
            return result
        with patch("tools.overnight.workflow_adapter.executor.run_codex", side_effect=model):
            result = self.adapter.review("synthetic", self.spec, binding, "review1", self.run / "actions/review", 30, receipt)
        self.assertFalse(result["passed"])
        self.assertFalse(result["approved"])
        self.assertEqual(result["usage"]["input_tokens"], 100)

    def test_completed_review_reuse_rechecks_raw_output_and_snapshot(self):
        _, binding, receipt = self._tested()
        with patch("tools.overnight.workflow_adapter.executor.run_codex", side_effect=lambda *a, **k:
                   self.fake_model(*a, verdict="approve", **k)):
            result = self.adapter.review("synthetic", self.spec, binding, "review1", self.run / "actions/review", 30, receipt)
        self.assertTrue(result["approved"])
        self.adapter.verify_review_evidence("synthetic", self.spec, binding, result, receipt)
        final = Path(result["review_output_path"]) / "final.json"
        original = final.read_bytes()
        final.write_bytes(original + b"\n")
        with self.assertRaises(StateError):
            self.adapter.verify_review_evidence("synthetic", self.spec, binding, result, receipt)
        final.write_bytes(original)
        self.write(Path(result["checkout_path"]), "helper.py", "changed review snapshot\n")
        with self.assertRaises(StateError):
            self.adapter.verify_review_evidence("synthetic", self.spec, binding, result, receipt)

    def test_malformed_review_semantics_preserve_successful_transport_for_repair(self):
        _, binding, receipt = self._tested()
        with patch("tools.overnight.workflow_adapter.executor.run_codex", side_effect=lambda *a, **k:
                   self.fake_model(*a, final_body='{"verdict": [], "findings": [], "limitations": []}', **k)):
            result = self.adapter.review("synthetic", self.spec, binding, "review1", self.run / "actions/review", 30, receipt)
        self.assertTrue(result["passed"])
        self.assertFalse(result["approved"])
        self.assertTrue(result["needs_repair"])
        self.assertIsNone(result["reviewed"])
        self.assertEqual(result["usage"]["output_tokens"], 20)

    def test_trusted_entrypoint_must_be_actual_script_and_placeholders_exact(self):
        worker, binding = self.bound()
        for command in (["{python}", "-c", "pass", "{control}/tools/overnight/workflow_gate.py"],
                        ["{python}", "{workspace}/web/backend/classroom_web/synthetic/impl.py"],
                        ["{python}", "{control.__class__}/tools/overnight/workflow_gate.py"],
                        ["{python}", "{control!r}/tools/overnight/workflow_gate.py"]):
            bad = dict(self.spec["tests"][1], command=command)
            with self.subTest(command=command), self.assertRaises(StateError):
                self.adapter._command(bad, worker, binding, "a" * 32)

    def test_audit_requires_pinned_tracked_evidence_hash_and_source_identity(self):
        self.assertEqual(self.adapter.audit("synthetic", self.spec), self.spec["audit"])
        self.write(self.source, "docs/evidence.json", "working tree drift does not change pinned blob\n")
        self.assertEqual(self.adapter.audit("synthetic", self.spec), self.spec["audit"])
        for change in ({"tree_sha": "0" * 40}, {"evidence": [{"path": "docs/evidence.json", "sha256": "0" * 64}]}):
            bad = deepcopy(self.spec)
            bad["audit"].update(change)
            with self.subTest(change=change), self.assertRaises(StateError):
                self.adapter.audit("synthetic", bad)

    def test_complete_audit_allows_newer_base_when_only_outside_scope_changed(self):
        self.write(self.source, "README.md", "New control documentation outside this task\n")
        self.git("add", "README.md")
        self.git("commit", "--quiet", "-m", "Synthetic unrelated baseline advance")
        newer = self.git("rev-parse", "HEAD").strip()
        self.spec["base"]["sha"] = newer
        self.adapter = ProductionAdapter(self.source, newer, self.run, self.deps, codex="never-invoke-codex")
        with patch("tools.overnight.workflow_adapter.executor.run_codex") as model:
            self.assertEqual(self.adapter.audit("synthetic", self.spec), self.spec["audit"])
            model.assert_not_called()

    def test_complete_audit_rejects_changed_scope_or_unconfirmed_base(self):
        self.write(self.source, "web/backend/classroom_web/synthetic/impl.py", "VALUE = 99\n")
        self.git("add", "web/backend/classroom_web/synthetic/impl.py")
        self.git("commit", "--quiet", "-m", "Synthetic task content changed after audit")
        newer = self.git("rev-parse", "HEAD").strip()
        self.spec["base"]["sha"] = newer
        self.adapter = ProductionAdapter(self.source, newer, self.run, self.deps, codex="never-invoke-codex")
        with patch("tools.overnight.workflow_adapter.executor.run_codex") as model:
            with self.assertRaisesRegex(StateError, "scope changed"):
                self.adapter.audit("synthetic", self.spec)
            for update in ({"sha": None}, {"confirmed": False}):
                bad = deepcopy(self.spec)
                bad["base"].update(update)
                with self.subTest(update=update), self.assertRaisesRegex(StateError, "explicit confirmed base"):
                    self.adapter.audit("synthetic", bad)
            # Approved baselines without a task scope keep their historical rule.
            baseline = {"audit": self.spec["audit"], "base": {"sha": newer}}
            self.assertEqual(self.adapter.audit("00", baseline), self.spec["audit"])
            model.assert_not_called()

    def test_publication_disabled_before_credentials_and_enabled_passes_exact_policy(self):
        _, binding = self.bound()
        with patch("tools.overnight.publish.GitHubClient.from_git_credentials") as credentials, \
                patch("tools.overnight.publish.Publisher") as publisher:
            with self.assertRaises(StateError):
                self.adapter.publish("synthetic", self.spec, self.base, binding, {"passed": True})
            credentials.assert_not_called()
            self.adapter.allow_publish = True
            publisher.return_value.publish.return_value = {"draft": True}
            self.assertEqual(self.adapter.publish("synthetic", self.spec, self.base, binding, {"passed": True}), {"draft": True})
            args = publisher.return_value.publish.call_args.kwargs
            self.assertEqual(args["policy"], validation.make_policy("synthetic", self.spec))
            self.assertEqual(args["head_sha"], binding["head_sha"])
            self.assertEqual(args["expected_patch_sha256"], binding["patch_sha256"])

    def test_engine_real_git_and_python_resume_complete_receipts_without_duplicate_calls(self):
        """Production actions interoperate with durable engine state across two crashes."""
        class Crash(BaseException):
            pass

        authorization = "synthetic-production-adapter"
        self.run = self.source / "work/overnight/workflows" / authorization
        baseline = dict(deepcopy(self.spec["audit"]), approved=True)
        self.spec.update(depends_on=["01"], integration_depends_on=[], paid_depends_on=[],
            gates=dict(development=[], enablement=[], paid=[]),
            limits=dict(wall_seconds=7200, max_calls=8, max_repairs=3, call_timeout_seconds=1200),
            audit=dict(status="NOT_STARTED", head_sha=None, tree_sha=None, evidence=[], reason="Synthetic new task"))
        self.spec["base"].update(confirmed=True, dependency_heads={"01": self.base})
        dag = {task_id: dict(depends_on=parents, integration_depends_on=[], paid_depends_on=[], gates=[])
               for task_id, parents in (("00", []), ("01", ["00"]), ("synthetic", ["01"]))}
        # The constructor is an internal trusted API. CLI plan pinning/shape is
        # separately tested; this fixture supplies an already approved tiny DAG.
        plan = dict(version=2, authorization_id=authorization, repository="Mumao233/classroom-os",
            author_login="Mumao233", dag_path="unused-in-internal-fixture.json", enabled_tasks=["synthetic"],
            limits=dict(DEFAULT_LIMITS), baselines={key: deepcopy(baseline) for key in ("00", "01")},
            tasks={"synthetic": self.spec})
        model_calls = []

        def engine():
            # Recreate the adapter too; recovery cannot depend on Python memory.
            self.adapter = ProductionAdapter(self.source, self.base, self.run, self.deps, codex="never-invoke-codex")
            return Workflow(self.source, self.base, plan, dag, self.adapter, publish=False, clock=lambda: 1000.0)

        def model(*args, **kwargs):
            if kwargs.get("readonly"):
                model_calls.append("review")
                result = self.fake_model(*args, verdict="approve", **kwargs)
            else:
                model_calls.append("develop")
                self.assertEqual(self.worker(), args[1])
                result = self.fake_model(*args, final_body="Synthetic implementation complete.", **kwargs)
            return dict(result, elapsed_seconds=1)

        first = engine()
        first_transition = first._transition

        def stop_before_tests(task_id, target, **kwargs):
            if task_id == "synthetic" and target == "TESTING":
                raise Crash("development receipt and ledger persisted, before state transition")
            return first_transition(task_id, target, **kwargs)

        with patch("tools.overnight.workflow_adapter.executor.run_codex", side_effect=model), \
                patch("tools.overnight.workflow_adapter.executor.run_sandbox_command", side_effect=self.sandbox):
            with patch.object(first, "_transition", side_effect=stop_before_tests), self.assertRaises(Crash):
                first.run()
            self.assertEqual(first.state.snapshot()["tasks"]["synthetic"]["state"], "DEVELOPING")
            round_dir = self.run / "tasks/synthetic/rounds/0"
            development = read_json(round_dir / "develop/receipt.json")["result"]
            self.assertIn("workspace_stamp", development)
            self.assertEqual(model_calls, ["develop"])

            second = engine()
            second_transition = second._transition

            def stop_before_review(task_id, target, **kwargs):
                if task_id == "synthetic" and target == "REVIEWING":
                    raise Crash("real test receipt persisted, before review state transition")
                return second_transition(task_id, target, **kwargs)

            with patch.object(self.adapter, "verify_development", wraps=self.adapter.verify_development) as stamp_check, \
                    patch.object(second, "_transition", side_effect=stop_before_review), self.assertRaises(Crash):
                second.run()
            stamp_check.assert_called_once()
            self.assertEqual(second.state.snapshot()["tasks"]["synthetic"]["state"], "TESTING")
            tests = read_json(round_dir / "tests.json")["result"]
            self.assertTrue(tests["passed"])
            self.assertEqual(set(tests["sessions"]), {"candidate", "trusted"})
            self.assertEqual(len(self.sandbox_calls), 2)
            self.assertEqual(model_calls, ["develop"])

            third = engine()
            with patch.object(self.adapter, "test", wraps=self.adapter.test) as rerun_tests, \
                    patch.object(self.adapter, "publish") as publish:
                completed = third.run()
                rerun_tests.assert_not_called()
                publish.assert_not_called()
            task = completed["tasks"]["synthetic"]
            self.assertEqual(task["effective_state"], "READY_FOR_HUMAN_REVIEW", completed)
            self.assertEqual(task["attempt_count"], 1)
            self.assertEqual(task["repairs"], 0)
            self.assertEqual(model_calls, ["develop", "review"])
            self.assertEqual(len(self.sandbox_calls), 2)
            self.assertEqual(completed["subscription_usage"]["calls"], 2)
            self.assertEqual(completed["subscription_usage"]["turns"], 2)
            self.assertEqual(completed["subscription_usage"]["input_tokens"], 200)
            self.assertEqual(completed["subscription_usage"]["output_tokens"], 40)
            self.assertEqual([completed["tasks"][key]["state"] for key in ("00", "01")], ["SKIPPED", "SKIPPED"])
            self.assertFalse(completed["human_approval_inferred"])
            self.assertFalse(completed["publication_requested"])
            review = read_json(round_dir / "review/receipt.json")["result"]
            self.assertTrue(review["approved"])
            self.assertNotEqual(review["checkout_path"], tests["checkout_path"])
            settled = third.ledger.path.read_bytes()

            fourth = engine()
            with patch.object(self.adapter, "verify_test_evidence", wraps=self.adapter.verify_test_evidence) as test_check, \
                    patch.object(self.adapter, "verify_review_evidence", wraps=self.adapter.verify_review_evidence) as review_check, \
                    patch.object(self.adapter, "publish") as publish:
                resumed = fourth.run()
                self.assertGreaterEqual(test_check.call_count, 1)
                review_check.assert_called_once()
                publish.assert_not_called()
            self.assertEqual(resumed["tasks"]["synthetic"]["effective_state"], "READY_FOR_HUMAN_REVIEW", resumed)
            self.assertEqual(fourth.ledger.path.read_bytes(), settled)
            self.assertEqual(model_calls, ["develop", "review"])
            self.assertEqual(len(self.sandbox_calls), 2)


if __name__ == "__main__":
    unittest.main()
