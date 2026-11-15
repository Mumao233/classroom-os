"""State tests use only a task-owned workspace directory and synthetic values."""
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.overnight.state import (DuplicateEvent, RevisionConflict, StateError, StateStore,
    atomic_json, dependency_fingerprint, load_dag, process_lock, ready_tasks)


ROOT = Path(__file__).resolve().parents[3]
SHA = "a" * 40
CONFIG = "b" * 64


def _competing_writer(path, go, results, label):
    go.wait(10)
    try:
        StateStore(path).transition("base", "SKIPPED", expected_revision=0, event_id=label,
                                    head_sha=SHA, evidence="verified synthetic baseline")
        results.put("committed")
    except RevisionConflict:
        results.put("conflict")


def _hold_lock(path, acquired):
    with process_lock(path):
        acquired.set()
        # The parent terminates this process: no Python finally is run.
        acquired.wait(60)
        import time
        time.sleep(60)


class StateTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / "work/overnight/tests"
        root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="state-", dir=root)
        self.path = Path(self.tmp.name) / "state.json"
        self.store = StateStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def initialize(self, tasks=None):
        return self.store.initialize(run_id="synthetic", base_sha=SHA, config_sha=CONFIG,
                                     tasks=tasks or {"base": {"depends_on": []}})

    def transition(self, task, state, **kwargs):
        document = self.store.snapshot()
        return self.store.transition(task, state, expected_revision=document["revision"],
                                     event_id=f"event-{document['revision']}", **kwargs)

    def start(self, task="base", attempt="attempt-1"):
        self.transition(task, "READY")
        document = self.store.snapshot()
        fingerprint = dependency_fingerprint(document, task)
        self.store.begin_attempt(task, attempt, expected_revision=document["revision"],
                                 event_id="start-" + attempt, dependency_fingerprint=fingerprint)
        return {"attempt_id": attempt, "dependency_fingerprint": fingerprint}

    def complete(self, task="base", attempt="attempt-1"):
        identity = self.start(task, attempt)
        self.transition(task, "TESTING", **identity)
        self.transition(task, "REVIEWING", **identity)
        return self.transition(task, "READY_FOR_HUMAN_REVIEW", **identity, head_sha="c" * 40,
                               evidence="synthetic CI and independent Codex review")

    def test_real_dag_preserves_hard_integration_paid_and_human_gates(self):
        tasks = load_dag(ROOT / "docs/web-alpha/architecture/contracts/pr-dependencies.json")
        self.assertEqual(len(tasks), 28)
        self.assertEqual(tasks["05a"]["integration_depends_on"], ["03b"])
        self.assertIn("11a", tasks["06a"]["paid_depends_on"])
        self.assertEqual(tasks["03b"]["gates"], ["TBD-08"])
        with self.assertRaises(StateError):
            load_dag(ROOT / "docs/web-alpha/architecture/contracts/pr-dependencies.json", ["05a"])

    def test_join_waits_for_all_hard_dependencies_and_human_gate(self):
        self.initialize({"base": {"depends_on": []}, "left": {"depends_on": ["base"]},
            "right": {"depends_on": ["base"]}, "join": {"depends_on": ["left", "right"], "gates": ["decision"]}})
        self.transition("base", "SKIPPED", head_sha=SHA, evidence="approved historical source hashes")
        self.assertEqual(set(ready_tasks(self.store.snapshot())), {"left", "right"})
        self.transition("left", "SKIPPED", head_sha=SHA, evidence="approved")
        self.assertNotIn("join", ready_tasks(self.store.snapshot(), {"decision": True}))
        self.transition("right", "SKIPPED", head_sha=SHA, evidence="approved")
        self.assertNotIn("join", ready_tasks(self.store.snapshot()))
        self.assertIn("join", ready_tasks(self.store.snapshot(), {"decision": True}))

    def test_skip_requires_evidence_and_init_does_not_reset_state(self):
        self.initialize()
        with self.assertRaises(StateError):
            self.transition("base", "SKIPPED", head_sha=SHA)
        self.transition("base", "SKIPPED", head_sha=SHA, evidence="historical hashes match")
        with self.assertRaises(StateError):
            self.initialize()
        self.assertEqual(StateStore(self.path).snapshot()["tasks"]["base"]["state"], "SKIPPED")

    def test_duplicate_late_and_wrong_dependency_events_are_rejected(self):
        self.initialize()
        identity = self.start()
        revision = self.store.snapshot()["revision"]
        self.store.transition("base", "TESTING", expected_revision=revision, event_id="tested", **identity)
        with self.assertRaises(DuplicateEvent):
            self.store.transition("base", "TESTING", expected_revision=revision, event_id="tested", **identity)
        with self.assertRaises(StateError):
            self.transition("base", "REVIEWING", attempt_id="old-attempt", dependency_fingerprint=identity["dependency_fingerprint"])
        with self.assertRaises(StateError):
            self.transition("base", "REVIEWING", attempt_id=identity["attempt_id"], dependency_fingerprint="0" * 64)

    def test_single_agent_and_three_repairs_survive_restart(self):
        self.initialize({"base": {"depends_on": []}, "other": {"depends_on": []}})
        identity = self.start()
        self.transition("other", "READY")
        document = self.store.snapshot()
        with self.assertRaises(StateError):
            self.store.begin_attempt("other", "parallel", expected_revision=document["revision"], event_id="parallel",
                dependency_fingerprint=dependency_fingerprint(document, "other"))
        for repair in range(3):
            self.transition("base", "TESTING", **identity)
            document = self.store.snapshot()
            self.store = StateStore(self.path)
            self.store.request_repair("base", expected_revision=document["revision"], event_id=f"repair-{repair}",
                                      reason="synthetic failure", **identity)
        self.transition("base", "TESTING", **identity)
        document = self.store.snapshot()
        with self.assertRaises(StateError):
            self.store.request_repair("base", expected_revision=document["revision"], event_id="repair-4",
                                      reason="still failing", **identity)
        self.assertEqual(self.store.snapshot()["tasks"]["base"]["repairs"], 3)

    def test_publish_intent_survives_unknown_remote_result_and_confirms_once(self):
        self.initialize()
        document = self.complete()
        document = self.store.record_publish_intent("base", expected_revision=document["revision"], event_id="intent",
            intent_id="stable-id", branch="codex/synthetic", base_ref="main", base_sha=SHA,
            head_sha="c" * 40, artifact_sha256="d" * 64)
        resumed = StateStore(self.path)
        self.assertIsNone(resumed.snapshot()["tasks"]["base"]["publication"]["pr_number"])
        result = resumed.confirm_publication("base", expected_revision=document["revision"], event_id="published",
            intent_id="stable-id", head_sha="c" * 40, pr_number=7, pr_url="https://github.com/example/test/pull/7")
        self.assertEqual(result["tasks"]["base"]["publication"]["pr_number"], 7)
        with self.assertRaises(DuplicateEvent):
            resumed.confirm_publication("base", expected_revision=document["revision"], event_id="published",
                intent_id="stable-id", head_sha="c" * 40, pr_number=7, pr_url="https://github.com/example/test/pull/7")

    def test_process_compare_and_swap_has_exactly_one_winner(self):
        self.initialize()
        context = multiprocessing.get_context("spawn")
        go, results = context.Event(), context.Queue()
        workers = [context.Process(target=_competing_writer, args=(str(self.path), go, results, f"writer-{i}")) for i in range(2)]
        for worker in workers:
            worker.start()
        go.set()
        outcomes = [results.get(timeout=20) for _ in workers]
        for worker in workers:
            worker.join(20)
            self.assertEqual(worker.exitcode, 0)
        self.assertCountEqual(outcomes, ["committed", "conflict"])
        self.assertEqual(self.store.snapshot()["revision"], 1)

    def test_killed_process_releases_lock_and_failed_replace_preserves_state(self):
        initial = self.initialize()
        with patch("tools.overnight.state.os.replace", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                self.transition("base", "READY")
        self.assertEqual(self.store.snapshot(), initial)
        context = multiprocessing.get_context("spawn")
        acquired = context.Event()
        worker = context.Process(target=_hold_lock, args=(str(self.store.lock_path), acquired))
        worker.start()
        self.assertTrue(acquired.wait(15))
        worker.terminate()
        worker.join(15)
        self.transition("base", "READY")
        self.assertEqual(self.store.snapshot()["revision"], 1)

    def test_strict_json_rejects_nonfinite_duplicate_unknown_bool_and_cycle(self):
        initial = self.initialize()
        for text in ('{"version":1,"version":1}', '{"revision":NaN}', '{"revision":Infinity}'):
            self.path.write_text(text, encoding="utf-8")
            with self.assertRaises(StateError):
                self.store.snapshot()
        for change in ({"revision": True}, {"unknown": "no"}):
            self.path.write_text(json.dumps(dict(initial, **change)), encoding="utf-8")
            with self.assertRaises(StateError):
                self.store.snapshot()
        atomic_json(self.path, initial)
        with self.assertRaises(StateError):
            StateStore(Path(self.tmp.name) / "cycle.json").initialize(run_id="cycle", base_sha=SHA,
                config_sha=CONFIG, tasks={"a": {"depends_on": ["b"]}, "b": {"depends_on": ["a"]}})


if __name__ == "__main__":
    unittest.main()
