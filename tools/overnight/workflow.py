"""Trusted, serial, configuration-driven development workflow.

The v1 pilot remains a historical adapter. This entrypoint never merges branches,
enables services, approves human gates, or chooses a new authorization on resume.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import time
import uuid

from .budget import SubscriptionLimits, DEFAULT_LIMITS, LimitExceeded
from .state import (StateStore, StateError, atomic_json, read_json, process_lock,
                    dependency_fingerprint, ACTIVE)
from .task_config import load_plan, plan_hash


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


class TaskBlocked(StateError):
    """Only this task is blocked; independent authorized tasks may continue."""


class GlobalStop(StateError):
    """No more model execution is safe in this authorization."""


class Workflow:
    """Internal trusted API. CLI supplies a validated pinned plan and adapter."""
    def __init__(self, source, control_sha, plan, dag, adapter, *, approvals=None,
                 publish=False, clock=time.time):
        self.source = Path(source).resolve()
        self.control_sha, self.plan, self.dag = control_sha, deepcopy(plan), deepcopy(dag)
        self.config_sha = plan_hash(plan)
        self.adapter, self.publish, self.clock = adapter, publish, clock
        self.root = self.source / "work/overnight/workflows" / plan["authorization_id"]
        self.state = StateStore(self.root / "state.json")
        self.ledger = SubscriptionLimits(self.root / "subscription.json", clock=clock)
        self.approvals_path = None if approvals is None else Path(approvals).resolve()
        self.approvals = {"version": 1, "authorization_id": plan["authorization_id"],
                          "gates": [], "dependencies": [], "bases": []}
        self.errors = {}
        self._verifying_dependencies = set()

    def _now(self):
        return int(self.clock() * 1000)

    def _taskdir(self, task_id):
        path = self.root / "tasks" / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _task(self, task_id):
        return self.state.snapshot()["tasks"][task_id]

    def _transition(self, task_id, target, **kwargs):
        document = self.state.snapshot()
        task = document["tasks"][task_id]
        if kwargs.get("reason"):
            kwargs["reason"] = " ".join(str(kwargs["reason"]).split())[:1000]
        return self.state.transition(task_id, target, expected_revision=document["revision"],
            event_id=task_id + "-" + target + "-" + str(document["revision"]),
            attempt_id=task["attempt_id"], dependency_fingerprint=task["dependency_fingerprint"], **kwargs)

    def _required(self, spec):
        dependencies = list(spec["depends_on"])
        gates = list(spec["gates"]["development"])
        if spec["phase"] in {"integration", "paid", "activation"}:
            dependencies += spec["integration_depends_on"]
        if spec["phase"] in {"paid", "activation"}:
            dependencies += spec["paid_depends_on"]
            gates += spec["gates"]["paid"]
        if spec["phase"] == "activation":
            gates += spec["gates"]["enablement"]
        return sorted(set(dependencies)), sorted(set(gates))

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        identity_path = self.root / "identity.json"
        identity = {"version": 2, "authorization_id": self.plan["authorization_id"],
                    "control_sha": self.control_sha, "config_sha": self.config_sha,
                    "contract_sha": digest(self.dag), "source": str(self.source), "initialized": False}
        if identity_path.exists():
            saved = read_json(identity_path)
            if type(saved.get("initialized")) is not bool or dict(saved, initialized=False) != identity:
                raise GlobalStop("resume identity/configuration/contract changed; no limits were reset")
            initialized = saved["initialized"]
        else:
            if self.state.path.exists() or self.ledger.path.exists():
                raise GlobalStop("existing state lacks its initialization identity")
            atomic_json(identity_path, identity)
            initialized = False
        limits = dict(DEFAULT_LIMITS, **self.plan["limits"])
        if self.ledger.path.exists():
            if self.ledger.snapshot()["limits"] != limits:
                raise GlobalStop("persistent limits differ from approved configuration")
        else:
            if initialized or self.state.path.exists():
                raise GlobalStop("ledger is missing; refusing to recreate spent allowance")
            self.ledger.initialize(self.plan["limits"])
        if not self.state.path.exists():
            if initialized:
                raise GlobalStop("workflow state missing; refusing to restart")
            runtime = deepcopy(self.dag)
            for task_id, spec in self.plan["tasks"].items():
                runtime[task_id]["gates"] = self._required(spec)[1]
            self.state.initialize(run_id=self.plan["authorization_id"], base_sha=self.control_sha,
                                  config_sha=self.config_sha, tasks=runtime)
        else:
            document = self.state.snapshot()
            if (document["run_id"] != self.plan["authorization_id"] or document["base_sha"] != self.control_sha
                    or document["config_sha"] != self.config_sha or set(document["tasks"]) != set(self.dag)):
                raise GlobalStop("workflow checkpoint does not match its trusted identity")
        atomic_json(identity_path, dict(identity, initialized=True))
        self._load_approvals()

    def _evidence(self, records):
        if not isinstance(records, list) or not records:
            raise StateError("human approval requires explicit evidence")
        for record in records:
            if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
                raise StateError("invalid approval evidence")
            name = record["path"]
            if (not isinstance(name, str) or not name or "\\" in name or ":" in name
                    or any(p in {"", ".", ".."} for p in name.split("/"))):
                raise StateError("invalid approval evidence path")
            path = self.source.joinpath(*name.split("/"))
            if not name.startswith(("automation/", "docs/", "work/overnight/approvals/")):
                raise StateError("approval evidence must be in a controller-owned evidence directory")
            if any(p == path or p in path.parents for p in
                   (self.source / "work/overnight/workflows", self.source / "work/overnight/runs")):
                raise StateError("worker/run output cannot grant human approval")
            if not (path.is_file() and path.resolve() == path and
                    hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]):
                raise StateError("human approval evidence missing or changed")

    def _load_approvals(self):
        empty = {"version": 1, "authorization_id": self.plan["authorization_id"],
                 "gates": [], "dependencies": [], "bases": []}
        if self.approvals_path is not None:
            permitted = (self.source / "work/overnight/approvals", self.source / "automation",
                         self.source / "docs/web-alpha/overnight/approvals")
            if not any(parent in self.approvals_path.parents for parent in permitted):
                raise StateError("approval input must be in a controller-owned approval directory")
            if self.approvals_path.exists():
                if self.approvals_path.resolve() != self.approvals_path or not self.approvals_path.is_file():
                    raise StateError("approval input is not a regular isolated file")
                empty = read_json(self.approvals_path)
        if (not isinstance(empty, dict) or set(empty) != set(self.approvals)
                or type(empty["version"]) is not int or empty["version"] != 1
                or empty["authorization_id"] != self.plan["authorization_id"]):
            raise StateError("approval document belongs to a different authorization")
        fields = {"gates": {"task_id", "gate", "base_sha", "actor", "evidence"},
                  "dependencies": {"task_id", "head_sha", "actor", "evidence"},
                  "bases": {"task_id", "ref", "sha", "dependency_heads", "actor", "evidence"}}
        for kind, expected in fields.items():
            if not isinstance(empty[kind], list):
                raise StateError("invalid approval records")
            seen = set()
            for record in empty[kind]:
                if (not isinstance(record, dict) or set(record) != expected or record["actor"] != "Mumao233"
                        or record["task_id"] not in self.dag):
                    raise StateError("approval must be an explicit trusted human record")
                hashes = [record[key] for key in ("base_sha", "head_sha", "sha") if key in record]
                if any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{40}", v) for v in hashes):
                    raise StateError("approval must bind exact source SHAs")
                if kind == "gates" and record["gate"] not in self.dag[record["task_id"]]["gates"]:
                    raise StateError("approval names an unknown gate")
                if kind == "bases":
                    heads = record["dependency_heads"]
                    if (not isinstance(heads, dict) or any(k not in self.dag or not isinstance(v, str)
                        or not re.fullmatch(r"[0-9a-f]{40}", v) for k, v in heads.items())):
                        raise StateError("invalid common-base approval heads")
                self._evidence(record["evidence"])
                key = digest(record)
                if key in seen:
                    raise StateError("duplicate human approval")
                seen.add(key)
        snapshot = self.root / "approvals-snapshot.json"
        if snapshot.exists():
            old = read_json(snapshot)
            for kind in fields:
                if not {digest(x) for x in old[kind]} <= {digest(x) for x in empty[kind]}:
                    raise GlobalStop("human approval history was removed or rewritten")
        atomic_json(snapshot, empty)
        self.approvals = empty

    def _audit_baselines(self):
        for task_id, record in self.plan["baselines"].items():
            if record.get("approved") is not True:
                continue
            audit = self.adapter.audit(task_id, {"audit": record, "base": {"sha": record["head_sha"]}})
            if audit["status"] != "COMPLETE":
                raise StateError("accepted baseline lacks complete verified evidence")
            path = self._taskdir(task_id) / "audit.json"
            value = {"config_sha": self.config_sha, "audit": audit}
            if path.exists() and read_json(path) != value:
                raise StateError("accepted baseline audit receipt changed")
            if not path.exists():
                atomic_json(path, value)
            task = self._task(task_id)
            if task["state"] == "AUDIT":
                self._transition(task_id, "SKIPPED", head_sha=audit["head_sha"], evidence=str(path))
            elif task["state"] != "SKIPPED" or task["head_sha"] != audit["head_sha"]:
                raise StateError("baseline completion identity changed")

    def _accepted_head(self, task_id):
        if task_id in self.errors:
            raise TaskBlocked("prerequisite evidence is invalid: " + task_id)
        task = self._task(task_id)
        if task["state"] in {"FAILED", "BLOCKED"}:
            raise TaskBlocked("prerequisite is " + task["state"] + ": " + task_id)
        baseline = self.plan["baselines"].get(task_id)
        if baseline and baseline.get("approved") is True and task["state"] == "SKIPPED":
            return task["head_sha"], digest(baseline)
        if task["state"] not in {"SKIPPED", "READY_FOR_HUMAN_REVIEW"} or not task["head_sha"]:
            raise TaskBlocked("prerequisite not accepted: " + task_id)
        matches = [x for x in self.approvals["dependencies"]
                   if x["task_id"] == task_id and x["head_sha"] == task["head_sha"]]
        if len(matches) != 1:
            raise TaskBlocked("machine review/Draft is not human acceptance: " + task_id)
        if task_id in self._verifying_dependencies:
            raise TaskBlocked("cyclic dependency evidence")
        spec = self.plan["tasks"].get(task_id)
        if spec is None:
            raise TaskBlocked("accepted dependency lacks its preserved task specification")
        self._verifying_dependencies.add(task_id)
        try:
            if task["state"] == "READY_FOR_HUMAN_REVIEW":
                context = self._context(task_id, spec)
                self._final_receipts(task_id, spec, context)
            else:
                audit = self.adapter.audit(task_id, spec)
                receipt = read_json(self._taskdir(task_id) / "audit.json")
                if (audit["status"] != "COMPLETE" or audit["head_sha"] != task["head_sha"]
                        or receipt != {"config_sha": self.config_sha, "audit": audit}):
                    raise TaskBlocked("accepted dependency audit evidence changed")
        finally:
            self._verifying_dependencies.remove(task_id)
        return task["head_sha"], digest(matches[0])

    def _context(self, task_id, spec):
        required, gates = self._required(spec)
        heads, consumed = {}, []
        for dep in required:
            head, approval = self._accepted_head(dep)
            heads[dep] = head
            consumed.append(approval)
        base = deepcopy(spec["base"])
        if base["sha"] is None or not base["confirmed"]:
            matches = [x for x in self.approvals["bases"] if x["task_id"] == task_id
                       and x["ref"] == base["ref"] and x["dependency_heads"] == heads]
            if len(matches) != 1:
                raise TaskBlocked("human-confirmed common base is required")
            chosen = matches[0]
            base.update(sha=chosen["sha"], confirmed=True, dependency_heads=heads)
            consumed.append(digest(chosen))
        if base["dependency_heads"] != heads:
            raise TaskBlocked("approved common base has different prerequisite heads")
        for dep, head in heads.items():
            if not self.adapter.is_ancestor(head, base["sha"]):
                raise TaskBlocked("common base does not contain " + dep + "; no automatic merge")
        for gate in gates:
            matches = [x for x in self.approvals["gates"] if x["task_id"] == task_id
                       and x["gate"] == gate and x["base_sha"] == base["sha"]]
            if len(matches) != 1:
                raise TaskBlocked("human gate not approved for this base: " + gate)
            consumed.append(digest(matches[0]))
        context = {"control_sha": self.control_sha, "config_sha": self.config_sha,
                   "task_id": task_id, "spec_sha": digest(spec), "base_sha": base["sha"],
                   "base_ref": base["ref"], "branch": spec["branch"], "dependency_heads": heads,
                   "approval_hashes": sorted(consumed)}
        path = self._taskdir(task_id) / "context.json"
        if path.exists():
            prior = read_json(path)
            if {k: v for k, v in prior.items() if k != "started_at_ms"} != context:
                raise TaskBlocked("started task base/dependency/approval identity changed")
            return prior
        if self._task(task_id)["attempt_count"]:
            raise TaskBlocked("started task context is missing; refusing to reset its cumulative clock")
        context["started_at_ms"] = self._now()
        atomic_json(path, context)
        return context

    def _local_limits(self, task_id, spec, context, *, new_call=False):
        ledger = self.ledger.snapshot()
        totals = self.ledger.usage_totals(ledger, task_id)
        if self._now() - context["started_at_ms"] >= spec["limits"]["wall_seconds"] * 1000:
            raise TaskBlocked("task wall-time limit reached; cumulative clock retained")
        if totals["calls"] + int(new_call) > spec["limits"]["max_calls"]:
            raise TaskBlocked("task call limit reached; reservations retained")

    def _receipt(self, path, context, binding=None):
        if not path.exists():
            return None
        saved = read_json(path)
        if (not isinstance(saved, dict) or set(saved) != {"version", "identity_sha", "binding", "result"}
                or type(saved["version"]) is not int or saved["version"] != 1
                or saved.get("identity_sha") != digest(context) or saved.get("binding") != binding):
            raise TaskBlocked("saved action evidence belongs to a different source or attempt")
        return saved["result"]

    def _save(self, path, context, binding, result):
        atomic_json(path, {"version": 1, "identity_sha": digest(context), "binding": binding, "result": result})

    def _model(self, kind, task_id, spec, context, round_dir, binding=None, tests=None):
        task = self._task(task_id)
        call_id = f"{self.plan['authorization_id']}-{task_id}-a{task['attempt_count']}-{kind}-{task['repairs']}"
        output = round_dir / kind
        receipt = output / "receipt.json"
        ledger = self.ledger.snapshot()
        call = ledger["calls"].get(call_id)
        saved = self._receipt(receipt, context, binding)
        if call is not None:
            if saved is None or call["status"] == "BLOCKED":
                raise GlobalStop("unresolved prior model call; no duplicate invocation: " + call_id)
            if call["status"] == "RUNNING":
                self._settle(call_id, saved)
            elif call["status"] != "COMPLETE":
                raise GlobalStop("invalid prior model reservation")
            elif call["usage"] != saved.get("usage"):
                raise GlobalStop("completed model receipt contradicts its settled ledger")
            if saved.get("passed") is not True:
                raise TaskBlocked("completed model evidence was invalid; explicit reconciliation required")
            if kind == "review" and saved.get("final_sha256") is not None:
                final = output / "final.json"
                if not final.is_file() or hashlib.sha256(final.read_bytes()).hexdigest() != saved["final_sha256"]:
                    raise TaskBlocked("independent review output changed since completion")
            if kind == "develop" and hasattr(self.adapter, "verify_development"):
                self.adapter.verify_development(task_id, spec, saved)
            return saved, output
        if saved is not None or output.exists():
            raise GlobalStop("unreserved model output exists; explicit reconciliation required")
        self._local_limits(task_id, spec, context, new_call=True)
        ledger = self.ledger.begin_call(call_id, task_id, expected_revision=ledger["revision"])
        if ledger["status"] != "READY" or call_id not in ledger["calls"]:
            raise GlobalStop("batch subscription limit reached; no fallback or reset")
        deadline = min(ledger["calls"][call_id]["deadline_ms"],
                       context["started_at_ms"] + spec["limits"]["wall_seconds"] * 1000)
        timeout = min(spec["limits"]["call_timeout_seconds"], max(1, (deadline - self._now()) // 1000))
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            if kind == "develop":
                result = self.adapter.develop(task_id, spec, context["base_sha"], call_id, output,
                                              timeout, task.get("reason") or "")
            else:
                result = self.adapter.review(task_id, spec, binding, call_id, output, timeout, tests)
                final = output / "final.json"
                if final.is_file():
                    result["final_sha256"] = hashlib.sha256(final.read_bytes()).hexdigest()
            self._save(receipt, context, binding, result)
        except Exception as error:
            # No complete receipt exists: leave reservation unresolved. Do not
            # turn an execution/transport uncertainty into another model call.
            raise GlobalStop("model outcome is unresolved: " + str(error)[:200]) from error
        self._settle(call_id, result)
        self._local_limits(task_id, spec, context)
        return result, output

    def _settle(self, call_id, result):
        ledger = self.ledger.snapshot()
        if not isinstance(result, dict):
            raise GlobalStop("model result has no complete structured receipt")
        elapsed = max(0, int(result.get("elapsed_seconds", 0)))
        if result.get("passed") is not True:
            reason = result.get("error_reason")
            usage = result.get("usage")
            if (reason in {"review_evidence_invalid", "development_evidence_invalid"}
                    and result.get("model_completion_verified") is True and isinstance(usage, dict)
                    and all(usage.get(k) is not None for k in ("turns", "input_tokens", "output_tokens"))):
                settled = self.ledger.finish_call(call_id, usage, elapsed_seconds=elapsed,
                                                  expected_revision=ledger["revision"])
                if settled["status"] != "READY":
                    raise GlobalStop("hard subscription limit reached after settled evidence failure")
                raise TaskBlocked("model completed with known usage, but its validation evidence was invalid")
            if reason not in {"rate_limit", "quota_exhausted", "authentication", "timeout"}:
                reason = "interrupted"
            self.ledger.block(call_id, reason, expected_revision=ledger["revision"],
                              usage=result.get("usage"), elapsed_seconds=elapsed)
            raise GlobalStop("model transport/auth/quota failure: " + reason)
        ledger = self.ledger.finish_call(call_id, result.get("usage"), elapsed_seconds=elapsed,
                                         expected_revision=ledger["revision"])
        if ledger["status"] != "READY":
            raise GlobalStop("cumulative subscription limits reached after completed call")

    def _repair(self, task_id, spec, reason):
        document = self.state.snapshot()
        task = document["tasks"][task_id]
        if task["repairs"] >= min(3, spec["limits"]["max_repairs"]):
            self._transition(task_id, "FAILED", reason="Shared repair limit exhausted: " + reason)
            return False
        self.state.request_repair(task_id, expected_revision=document["revision"],
            event_id=task_id + "-repair-" + str(task["repairs"] + 1),
            attempt_id=task["attempt_id"], dependency_fingerprint=task["dependency_fingerprint"],
            reason=" ".join(reason.split())[:1000])
        return True

    @staticmethod
    def _check_binding(binding):
        if (not isinstance(binding, dict) or set(binding) != {"head_sha", "tree_sha", "patch_sha256", "policy_sha256"}
                or any(not isinstance(binding[k], str) or not re.fullmatch(r"[0-9a-f]{" + str(n) + "}", binding[k])
                       for k, n in (("head_sha", 40), ("tree_sha", 40), ("patch_sha256", 64), ("policy_sha256", 64)))):
            raise StateError("candidate lacks exact immutable source/policy binding")

    def _round(self, task_id):
        return self._taskdir(task_id) / "rounds" / str(self._task(task_id)["repairs"])

    def _restore_active(self, task_id, context):
        checkpoint = self._taskdir(task_id) / "invalidated.json"
        if not checkpoint.exists():
            return False
        saved = read_json(checkpoint)
        target = saved.get("resume_state")
        if target not in ACTIVE:
            return False
        if saved.get("context_sha") != digest(context):
            raise TaskBlocked("blocked checkpoint belongs to a different task identity")
        document = self.state.snapshot()
        def restore(updated):
            task = updated["tasks"][task_id]
            if task["state"] != "BLOCKED" or task["attempt_count"] == 0:
                raise StateError("active recovery checkpoint drifted")
            self.state._check_attempt(updated, task_id, task["attempt_id"], task["dependency_fingerprint"])
            task["state"], task["reason"] = target, "Resumed original attempt checkpoint"
        self.state._mutate(document["revision"], task_id + "-resume-" + str(document["revision"]), restore)
        return True

    def _check_tests(self, result, binding, spec):
        if not isinstance(result, dict) or result.get("candidate_binding") != binding:
            raise TaskBlocked("test receipt belongs to a different candidate")
        if result.get("passed") is not True:
            return False
        sessions = result.get("sessions")
        if not isinstance(sessions, dict) or set(sessions) != {test["id"] for test in spec["tests"]}:
            raise TaskBlocked("test receipt lacks complete passing sessions")
        for check in spec["tests"]:
            session = sessions[check["id"]]
            if not isinstance(session, dict) or not isinstance(session.get("summary"), dict):
                raise TaskBlocked("mandatory test session is missing structured completion")
            summary = session.get("summary", {})
            count = summary.get("discovered")
            core = {key: binding[key] for key in ("head_sha", "patch_sha256")}
            if (session.get("passed") is not True or type(session.get("exit_code")) is not int
                    or session["exit_code"] != 0 or type(summary.get("schema_version")) is not int
                    or summary["schema_version"] != 1 or summary.get("candidate_binding") not in (core, binding)
                    or summary.get("stage") != check["completion"]["stage"] or summary.get("complete") is not True
                    or type(count) is not int or count < check["completion"]["min_tests"]
                    or type(summary.get("tests_run")) is not int or summary["tests_run"] != count
                    or type(summary.get("completed")) is not int or summary["completed"] != count
                    or any(type(summary.get(k)) is not int or summary[k] != 0 for k in
                           ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"))):
                raise TaskBlocked("mandatory test session has incomplete or inconsistent evidence")
        return True

    def _final_receipts(self, task_id, spec, context):
        round_dir = self._round(task_id)
        candidate = self._receipt(round_dir / "candidate.json", context)
        self._check_binding(candidate)
        self.adapter.verify(task_id, spec, context["base_sha"], candidate)
        tests = self._receipt(round_dir / "tests.json", context, candidate)
        if not self._check_tests(tests, candidate, spec):
            raise TaskBlocked("completed task no longer has passing tests")
        if hasattr(self.adapter, "verify_test_evidence"):
            self.adapter.verify_test_evidence(task_id, spec, candidate, tests)
        review = self._receipt(round_dir / "review/receipt.json", context, candidate)
        if not isinstance(review, dict) or review.get("passed") is not True or not self._approved(review):
            raise TaskBlocked("completed task lacks exact passing independent review")
        call_id = f"{self.plan['authorization_id']}-{task_id}-a{self._task(task_id)['attempt_count']}-review-{self._task(task_id)['repairs']}"
        settled = self.ledger.snapshot()["calls"].get(call_id)
        if settled is None or settled["status"] != "COMPLETE" or settled["usage"] != review.get("usage"):
            raise TaskBlocked("final review is not backed by its settled model ledger")
        final = round_dir / "review/final.json"
        if review.get("final_sha256") is not None and (
                not final.is_file() or hashlib.sha256(final.read_bytes()).hexdigest() != review["final_sha256"]):
            raise TaskBlocked("independent review output changed after acceptance")
        if hasattr(self.adapter, "verify_review_evidence"):
            self.adapter.verify_review_evidence(task_id, spec, candidate, review, tests)
        return candidate, tests, review

    @staticmethod
    def _approved(review):
        value = review.get("reviewed")
        return (isinstance(value, dict) and set(value) == {"verdict", "findings", "limitations"}
                and value["verdict"] == "approve" and value["findings"] == []
                and isinstance(value["limitations"], list)
                and all(isinstance(x, str) for x in value["limitations"]))

    def _process(self, task_id, spec):
        task = self._task(task_id)
        if task["state"] in {"AUDIT", "BLOCKED", "SKIPPED"}:
            audit = self.adapter.audit(task_id, spec)
            path = self._taskdir(task_id) / "audit.json"
            value = {"config_sha": self.config_sha, "audit": audit}
            if path.exists() and read_json(path) != value:
                raise TaskBlocked("existing implementation audit evidence changed")
            if not path.exists():
                atomic_json(path, value)
            if audit["status"] == "COMPLETE":
                if task["state"] == "SKIPPED" and task["head_sha"] != audit["head_sha"]:
                    raise TaskBlocked("completed audit source changed")
                if task["state"] == "BLOCKED":
                    self._transition(task_id, "AUDIT", reason="verified existing implementation")
                if self._task(task_id)["state"] != "SKIPPED":
                    self._transition(task_id, "SKIPPED", head_sha=audit["head_sha"], evidence=str(path))
                return
            if task["state"] == "SKIPPED":
                raise TaskBlocked("completed audit evidence is no longer valid")
            if audit["status"] == "BLOCKED":
                raise TaskBlocked(audit.get("reason") or "audit is externally blocked")
        context = self._context(task_id, spec)
        task = self._task(task_id)
        if task["state"] == "FAILED":
            return
        if task["state"] == "READY_FOR_HUMAN_REVIEW":
            self._final_receipts(task_id, spec, context)
            if self.publish:
                self._publish(task_id, spec, context)
            return
        if task["state"] not in {"DEVELOPING", "REVIEWING"}:
            self._local_limits(task_id, spec, context)
        if task["state"] == "BLOCKED":
            if not self._restore_active(task_id, context):
                self._transition(task_id, "AUDIT", reason="same task identity reconsidered with current human approvals")
        if self._task(task_id)["state"] == "AUDIT":
            self._transition(task_id, "READY", gate_results={x: True for x in self._required(spec)[1]})
        if self._task(task_id)["state"] == "READY":
            document = self.state.snapshot()
            task = document["tasks"][task_id]
            self.state.begin_attempt(task_id, self.plan["authorization_id"] + "-" + task_id + "-a" + str(task["attempt_count"] + 1),
                expected_revision=document["revision"], event_id=task_id + "-begin-" + str(task["attempt_count"] + 1),
                dependency_fingerprint=dependency_fingerprint(document, task_id))
        self.adapter.prepare(task_id, spec, context["base_sha"])
        while self._task(task_id)["state"] in ACTIVE:
            round_dir = self._round(task_id)
            task = self._task(task_id)
            if task["state"] == "DEVELOPING":
                development, _ = self._model("develop", task_id, spec, context, round_dir)
                self._transition(task_id, "TESTING")
                if development.get("needs_repair"):
                    if not self._repair(task_id, spec, "Development reported an incomplete implementation"):
                        return
                    continue
            if self._task(task_id)["state"] == "TESTING":
                candidate = self._receipt(round_dir / "candidate.json", context)
                if candidate is None:
                    try:
                        candidate = self.adapter.candidate(task_id, spec, context["base_sha"], self._task(task_id)["repairs"])
                    except StateError as error:
                        if getattr(error, "scope_violation", False):
                            self._transition(task_id, "FAILED", reason="Protected/out-of-scope modification: " + str(error))
                            return
                        if getattr(error, "stage_failure", False):
                            if not self._repair(task_id, spec, str(error)):
                                return
                            continue
                        raise
                    self._check_binding(candidate)
                    self._save(round_dir / "candidate.json", context, None, candidate)
                self.adapter.verify(task_id, spec, context["base_sha"], candidate)
                tests = self._receipt(round_dir / "tests.json", context, candidate)
                if tests is None:
                    # Pure read-only tests may be repeated after an interrupted
                    # transport. Preserve its partial logs in a unique attempt;
                    # this never reserves another development/review model call.
                    tests = self.adapter.test(task_id, spec, candidate, round_dir / "tests" / uuid.uuid4().hex)
                    self._save(round_dir / "tests.json", context, candidate, tests)
                if not self._check_tests(tests, candidate, spec):
                    if not self._repair(task_id, spec, "Configured mandatory tests failed: " + json.dumps(tests)[-700:]):
                        return
                    continue
                self._transition(task_id, "REVIEWING")
            if self._task(task_id)["state"] == "REVIEWING":
                candidate = self._receipt(round_dir / "candidate.json", context)
                self.adapter.verify(task_id, spec, context["base_sha"], candidate)
                tests = self._receipt(round_dir / "tests.json", context, candidate)
                if not self._check_tests(tests, candidate, spec):
                    raise TaskBlocked("cannot review without matching complete tests")
                if hasattr(self.adapter, "verify_test_evidence"):
                    self.adapter.verify_test_evidence(task_id, spec, candidate, tests)
                review, _ = self._model("review", task_id, spec, context, round_dir, candidate, tests)
                self.adapter.verify(task_id, spec, context["base_sha"], candidate)
                if not self._approved(review):
                    if not self._repair(task_id, spec, "Independent review requested changes: " + json.dumps(review.get("reviewed"))[-700:]):
                        return
                    continue
                if hasattr(self.adapter, "verify_review_evidence"):
                    self.adapter.verify_review_evidence(task_id, spec, candidate, review, tests)
                self._transition(task_id, "READY_FOR_HUMAN_REVIEW", head_sha=candidate["head_sha"],
                    evidence=str(round_dir / "review/receipt.json"))
        if self.publish and self._task(task_id)["state"] == "READY_FOR_HUMAN_REVIEW":
            self._publish(task_id, spec, context)

    def _publish(self, task_id, spec, context):
        candidate, _, _ = self._final_receipts(task_id, spec, context)
        task = self._task(task_id)
        if task["publication"] and task["publication"]["pr_number"] is not None:
            return
        if task["publication"] is None:
            document = self.state.snapshot()
            self.state.record_publish_intent(task_id, expected_revision=document["revision"],
                event_id=task_id + "-publish-intent", intent_id=self.plan["authorization_id"] + "-" + task_id,
                branch=spec["branch"], base_ref=context["base_ref"], base_sha=context["base_sha"],
                head_sha=candidate["head_sha"], artifact_sha256=candidate["patch_sha256"])
        path = self._taskdir(task_id) / "publication.json"
        saved = self._receipt(path, context, candidate)
        receipt = self.adapter.publish(task_id, spec, context["base_sha"], candidate, self.report())
        if saved is None:
            self._save(path, context, candidate, receipt)
        else:
            # Reconcile the remote mapping after a crash before confirmation,
            # retaining the original creation receipt and all later observations.
            if ({k: v for k, v in saved.items() if k != "status"}
                    != {k: v for k, v in receipt.items() if k != "status"}):
                raise TaskBlocked("publication mapping differs from its first saved receipt")
            reconciliation_dir = self._taskdir(task_id) / "publication-reconciliations"
            reconciliation_dir.mkdir(exist_ok=True)
            self._save(reconciliation_dir / (uuid.uuid4().hex + ".json"),
                       context, candidate, receipt)
        document = self.state.snapshot()
        self.state.confirm_publication(task_id, expected_revision=document["revision"], event_id=task_id + "-published",
            intent_id=document["tasks"][task_id]["publication"]["intent_id"], head_sha=candidate["head_sha"],
            pr_number=receipt["pr_number"], pr_url=receipt["pr_url"])

    def report(self, global_reason=None):
        document, ledger = self.state.snapshot(), self.ledger.snapshot()
        usage = self.ledger.usage_totals(ledger)
        unresolved = [key for key, value in ledger["calls"].items() if value["status"] == "RUNNING"]
        usage["usage_unknown"] = usage["usage_unknown"] or bool(unresolved)
        tasks = {}
        for task_id, task in document["tasks"].items():
            tasks[task_id] = dict(task, enabled=task_id in self.plan["enabled_tasks"],
                effective_state="BLOCKED" if task_id in self.errors else task["state"],
                controller_error=self.errors.get(task_id), contract=self.dag[task_id])
        report = {"version": 2, "authorization_id": self.plan["authorization_id"],
            "control_sha": self.control_sha, "config_sha": self.config_sha, "contract_sha": digest(self.dag),
            "status": "GLOBAL_STOP" if global_reason or ledger["status"] == "BLOCKED" else "STOPPED_NO_READY_TASKS",
            "global_reason": global_reason or ledger["block_reason"], "tasks": tasks,
            "enabled_tasks": self.plan["enabled_tasks"], "subscription_usage": usage,
            "unresolved_calls": unresolved,
            "elapsed_wall_seconds": max(0, (self._now() - ledger["started_at_ms"]) // 1000),
            "extra_api_spend_usd": 0, "publication_requested": self.publish,
            "human_approval_inferred": False,
            "limitations": ["Subscription monetary cost is not measured", "No automatic merge or common-base integration",
                            "Disabled packages are not authorized by loading the full DAG"]}
        atomic_json(self.root / "report.json", report)
        return report

    def run(self):
        # The same process-wide lock as v1 prevents a legacy and generic worker
        # from invoking models concurrently in this repository.
        with process_lock(self.source / "work/overnight/runs/controller.lock", timeout_seconds=1):
            self.errors = {}
            self.initialize()  # identity/approval errors do not rewrite task checkpoints
            if self.ledger.snapshot()["status"] == "BLOCKED":
                return self.report("existing blocked subscription ledger; no new calls")
            try:
                try:
                    self._audit_baselines()
                except StateError as error:
                    raise GlobalStop("accepted baseline evidence invalid: " + str(error)) from error
                active = [key for key, value in self.state.snapshot()["tasks"].items() if value["state"] in ACTIVE]
                running = [call for call in self.ledger.snapshot()["calls"].values() if call["status"] == "RUNNING"]
                if running and (len(active) != 1 or running[0]["task_id"] != active[0]):
                    raise GlobalStop("unresolved model reservation has no matching active checkpoint")
                ordered, visited = [], set()
                def visit(key):
                    if key in visited:
                        return
                    visited.add(key)
                    if key in self.plan["enabled_tasks"]:
                        for parent in self._required(self.plan["tasks"][key])[0]:
                            visit(parent)
                        ordered.append(key)
                for key in self.plan["enabled_tasks"]:
                    visit(key)
                ordered = active + [key for key in ordered if key not in active]
                if any(key not in self.plan["enabled_tasks"] for key in active):
                    raise GlobalStop("active task was removed from explicit authorization")
                for task_id in ordered:
                    if self._task(task_id)["state"] == "FAILED":
                        continue
                    try:
                        self._process(task_id, self.plan["tasks"][task_id])
                    except (StateError, OSError) as error:
                        if isinstance(error, (GlobalStop, LimitExceeded)):
                            raise
                        if any(call["status"] == "RUNNING" for call in self.ledger.snapshot()["calls"].values()):
                            raise GlobalStop("evidence failure with an unresolved model reservation: " + str(error)) from error
                        self.errors[task_id] = str(error)
                        task = self._task(task_id)
                        path = self._taskdir(task_id) / "invalidated.json"
                        previous = read_json(path) if path.exists() else {}
                        context_file = self._taskdir(task_id) / "context.json"
                        checkpoint = {"reason": str(error), "at_ms": self._now(),
                            "resume_state": task["state"] if task["state"] in ACTIVE else previous.get("resume_state"),
                            "context_sha": digest(read_json(context_file)) if context_file.exists() else None}
                        # Persist the recovery intent BEFORE changing the state.
                        # A crash in either order must retain the original phase.
                        atomic_json(path, checkpoint)
                        history_path = self._taskdir(task_id) / "blocked-history.json"
                        history = read_json(history_path) if history_path.exists() else []
                        atomic_json(history_path, history + [checkpoint])
                        if task["state"] in ACTIVE | {"AUDIT", "READY"}:
                            self._transition(task_id, "BLOCKED", reason=str(error))
                return self.report()
            except (GlobalStop, LimitExceeded) as error:
                return self.report(str(error))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--control-sha", required=True)
    parser.add_argument("--plan", type=Path, default=Path("automation/workflow.json"))
    parser.add_argument("--deps", type=Path, required=True)
    parser.add_argument("--approvals", type=Path)
    parser.add_argument("--execute", action="store_true", help="Execute enabled tasks; otherwise only validate the trusted plan")
    parser.add_argument("--publish", action="store_true", help="Allow validated personal-account Draft PRs only")
    parser.add_argument("--codex", default=shutil.which("codex"))
    args = parser.parse_args()
    if args.source.resolve() != Path(__file__).resolve().parents[2]:
        raise StateError("execute this entrypoint from its exact pinned control repository")
    plan, dag, config_sha = load_plan(args.source, args.plan, args.control_sha)
    if not args.execute:
        print(json.dumps({"status": "validated_not_executed", "packages": len(dag),
                          "enabled": plan["enabled_tasks"], "config_sha": config_sha, "model_calls": 0}))
        return 0
    from .workflow_adapter import ProductionAdapter
    root = args.source.resolve() / "work/overnight/workflows" / plan["authorization_id"]
    adapter = ProductionAdapter(args.source, args.control_sha, root, args.deps, args.codex, args.publish)
    report = Workflow(args.source, args.control_sha, plan, dag, adapter, approvals=args.approvals,
                      publish=args.publish).run()
    print(json.dumps({"status": report["status"], "report": str(root / "report.json")}))
    if report["status"] == "GLOBAL_STOP":
        return 1
    return 0 if all(report["tasks"][key]["effective_state"] in {"SKIPPED", "READY_FOR_HUMAN_REVIEW"}
                    for key in plan["enabled_tasks"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
