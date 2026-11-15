"""Durable, compare-and-swap state for the trusted overnight controller.

Repository and model output are not state authority. Only the controller calls
these methods, with an exact revision and a unique event id. File locks are OS
locks (including on Windows), so a crashed process releases its lock naturally.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time


class StateError(ValueError):
    pass


class RevisionConflict(StateError):
    pass


class DuplicateEvent(StateError):
    pass


STATES = frozenset({"AUDIT", "READY", "DEVELOPING", "TESTING", "REVIEWING",
                    "READY_FOR_HUMAN_REVIEW", "BLOCKED", "FAILED", "SKIPPED"})
ACTIVE = frozenset({"DEVELOPING", "TESTING", "REVIEWING"})
SATISFIED = frozenset({"SKIPPED", "READY_FOR_HUMAN_REVIEW"})
TRANSITIONS = {
    "AUDIT": {"READY", "BLOCKED", "FAILED", "SKIPPED"},
    "READY": {"DEVELOPING", "BLOCKED", "FAILED"},
    "DEVELOPING": {"TESTING", "BLOCKED", "FAILED"},
    "TESTING": {"REVIEWING", "BLOCKED", "FAILED"},
    "REVIEWING": {"READY_FOR_HUMAN_REVIEW", "BLOCKED", "FAILED"},
    "BLOCKED": {"AUDIT"}, "FAILED": {"AUDIT"},
    "READY_FOR_HUMAN_REVIEW": set(), "SKIPPED": set(),
}


def strict_int(value, name, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise StateError(f"invalid {name}")
    return value


def require_text(value, name, maximum=1024):
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise StateError(f"invalid {name}")
    return value


def require_keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise StateError("missing or unknown JSON fields")


def _sha(value, name="sha", length=40):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value) is None:
        raise StateError(f"invalid {name}")
    return value


def _strings(value, name):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise StateError(f"invalid {name}")
    for item in value:
        require_text(item, name)
    if len(value) != len(set(value)):
        raise StateError(f"invalid {name}")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StateError("duplicate JSON key")
        result[key] = value
    return result


def read_json(path):
    def invalid_constant(_):
        raise StateError("nonfinite JSON number")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_pairs,
                          parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise StateError("invalid JSON state") from error


@contextmanager
def process_lock(path, timeout_seconds=10):
    """Lock an adjacent permanent file; never unlink a possibly locked inode."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise StateError("state lock timeout")
                time.sleep(0.02)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_json(path, document):
    path = Path(path)
    payload = json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True,
                         indent=2).encode("utf-8") + b"\n"
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class JsonStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def validate(self, document):
        raise NotImplementedError

    def snapshot(self):
        with process_lock(self.lock_path):
            document = read_json(self.path)
            self.validate(document)
            return document

    def _create(self, document):
        self.validate(document)
        with process_lock(self.lock_path):
            if self.path.exists():
                current = read_json(self.path)
                self.validate(current)
                raise StateError("state already exists; resume instead of initialize")
            atomic_json(self.path, document)
        return deepcopy(document)

    def _mutate(self, expected_revision, event_id, mutate):
        strict_int(expected_revision, "expected_revision")
        require_text(event_id, "event_id", 200)
        with process_lock(self.lock_path):
            current = read_json(self.path)
            self.validate(current)
            if event_id in current["events"]:
                raise DuplicateEvent("event already applied")
            if current["revision"] != expected_revision:
                raise RevisionConflict("state revision changed")
            updated = deepcopy(current)
            mutate(updated)
            updated["revision"] += 1
            updated["events"].append(event_id)
            self.validate(updated)
            atomic_json(self.path, updated)
            return updated


def load_dag(path, scope=None):
    """Read the actual repository DAG; preserve non-hard dependencies and gates."""
    document = read_json(path)
    require_keys(document, {"version", "status", "prs"})
    if not isinstance(document["prs"], list):
        raise StateError("prs must be a list")
    tasks = {}
    for item in document["prs"]:
        require_keys(item, {"id", "name", "owner", "depends_on"},
                     {"integration_depends_on", "paid_depends_on", "gates"})
        task_id = require_text(item["id"], "task id", 80)
        if task_id in tasks:
            raise StateError("duplicate task id")
        tasks[task_id] = {key: _strings(item.get(key, []), key) for key in
                          ("depends_on", "integration_depends_on", "paid_depends_on", "gates")}
    _validate_dag(tasks)
    if scope is None:
        return tasks
    requested = _strings(list(scope), "scope")
    if set(requested) - tasks.keys():
        raise StateError("unknown scope task")
    selected = {key: tasks[key] for key in requested}
    _validate_dag(selected)
    return selected


def _validate_dag(tasks):
    visited, visiting = set(), set()
    def visit(task_id):
        if task_id in visiting:
            raise StateError("dependency cycle")
        if task_id in visited:
            return
        if task_id not in tasks:
            raise StateError("missing hard dependency")
        visiting.add(task_id)
        for dep in tasks[task_id]["depends_on"]:
            visit(dep)
        visiting.remove(task_id)
        visited.add(task_id)
    for task_id in tasks:
        visit(task_id)


def dependency_fingerprint(document, task_id):
    task = document["tasks"][task_id]
    value = {"base_sha": document["base_sha"], "config_sha": document["config_sha"],
             "dependencies": {dep: document["tasks"][dep]["head_sha"] for dep in sorted(task["depends_on"])}}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _dependency_ready(document, task):
    return all(document["tasks"][dep]["state"] in SATISFIED and
               document["tasks"][dep]["head_sha"] is not None for dep in task["depends_on"])


def ready_tasks(document, gate_results=None):
    gates = {} if gate_results is None else gate_results
    if not isinstance(gates, dict) or any(type(value) is not bool for value in gates.values()):
        raise StateError("gate results must be explicit booleans")
    return [task_id for task_id, task in document["tasks"].items()
            if task["state"] in {"AUDIT", "READY"} and _dependency_ready(document, task)
            and all(gates.get(gate) is True for gate in task["gates"])]


class StateStore(JsonStore):
    def initialize(self, *, run_id, base_sha, config_sha, tasks):
        require_text(run_id, "run_id", 200)
        _sha(base_sha)
        _sha(config_sha, "config_sha", 64)
        if not isinstance(tasks, dict) or not tasks:
            raise StateError("tasks must be nonempty mapping")
        normalized = {}
        for task_id, definition in tasks.items():
            require_text(task_id, "task_id", 80)
            require_keys(definition, {"depends_on"}, {"integration_depends_on", "paid_depends_on", "gates"})
            normalized[task_id] = {key: _strings(definition.get(key, []), key) for key in
                                   ("depends_on", "integration_depends_on", "paid_depends_on", "gates")}
            normalized[task_id].update(state="AUDIT", attempt_id=None, attempt_count=0, attempt_ids=[],
                repairs=0, dependency_fingerprint=None, head_sha=None, reason=None,
                evidence=None, approved_gates=[], publication=None)
        return self._create({"version": 1, "revision": 0, "events": [], "run_id": run_id,
                            "base_sha": base_sha, "config_sha": config_sha, "tasks": normalized})

    def validate(self, document):
        require_keys(document, {"version", "revision", "events", "run_id", "base_sha", "config_sha", "tasks"})
        strict_int(document["version"], "version", 1, 1)
        strict_int(document["revision"], "revision")
        _strings(document["events"], "events")
        if document["revision"] != len(document["events"]):
            raise StateError("revision/event history mismatch")
        require_text(document["run_id"], "run_id", 200)
        _sha(document["base_sha"])
        _sha(document["config_sha"], "config_sha", 64)
        tasks = document["tasks"]
        if not isinstance(tasks, dict) or not tasks:
            raise StateError("invalid tasks")
        for task_id, task in tasks.items():
            require_text(task_id, "task_id", 80)
            require_keys(task, {"depends_on", "integration_depends_on", "paid_depends_on", "gates", "state",
                "attempt_id", "attempt_count", "attempt_ids", "repairs", "dependency_fingerprint", "head_sha", "reason",
                "evidence", "approved_gates", "publication"})
            for key in ("depends_on", "integration_depends_on", "paid_depends_on", "gates", "approved_gates"):
                _strings(task[key], key)
            if not isinstance(task["state"], str) or task["state"] not in STATES or set(task["approved_gates"]) - set(task["gates"]):
                raise StateError("invalid task state/gates")
            strict_int(task["attempt_count"], "attempt_count")
            _strings(task["attempt_ids"], "attempt_ids")
            if task["attempt_count"] != len(task["attempt_ids"]) or (task["attempt_ids"] and task["attempt_id"] != task["attempt_ids"][-1]):
                raise StateError("attempt history mismatch")
            strict_int(task["repairs"], "repairs", 0, 3)
            for key in ("attempt_id", "reason", "evidence"):
                if task[key] is not None:
                    require_text(task[key], key)
            if task["dependency_fingerprint"] is not None:
                _sha(task["dependency_fingerprint"], "dependency_fingerprint", 64)
            if task["head_sha"] is not None:
                _sha(task["head_sha"])
            if task["state"] in ACTIVE | {"READY_FOR_HUMAN_REVIEW"} and (
                    not task["attempt_id"] or not task["dependency_fingerprint"]):
                raise StateError("active task lacks attempt identity")
            if task["state"] in SATISFIED and (not task["head_sha"] or not task["evidence"]):
                raise StateError("completion requires source and acceptance evidence")
            publication = task["publication"]
            if publication is not None:
                require_keys(publication, {"intent_id", "branch", "base_ref", "base_sha", "head_sha",
                    "artifact_sha256", "pr_number", "pr_url"})
                for key in ("intent_id", "branch", "base_ref"):
                    require_text(publication[key], key, 200)
                _sha(publication["base_sha"])
                _sha(publication["head_sha"])
                _sha(publication["artifact_sha256"], "artifact_sha256", 64)
                if publication["head_sha"] != task["head_sha"] or task["state"] != "READY_FOR_HUMAN_REVIEW":
                    raise StateError("publication source mismatch")
                if publication["pr_number"] is not None:
                    strict_int(publication["pr_number"], "pr_number", 1)
                    require_text(publication["pr_url"], "pr_url")
                elif publication["pr_url"] is not None:
                    raise StateError("incomplete publication confirmation")
        _validate_dag(tasks)
        if sum(task["state"] in ACTIVE for task in tasks.values()) > 1:
            raise StateError("only one active Codex task is allowed")

    def transition(self, task_id, to_state, *, expected_revision, event_id, attempt_id=None,
                   dependency_fingerprint=None, head_sha=None, reason=None, evidence=None, gate_results=None):
        def update(document):
            if task_id not in document["tasks"] or to_state not in STATES:
                raise StateError("unknown task or state")
            task = document["tasks"][task_id]
            if to_state not in TRANSITIONS[task["state"]]:
                raise StateError("invalid transition")
            if task["state"] in ACTIVE:
                self._check_attempt(document, task_id, attempt_id, dependency_fingerprint)
            if to_state == "READY":
                if task_id not in ready_tasks(document, gate_results):
                    raise StateError("dependencies or human gates not satisfied")
                task["approved_gates"] = list(task["gates"])
            if to_state == "DEVELOPING":
                require_text(attempt_id, "attempt_id", 200)
                if attempt_id in task["attempt_ids"]:
                    raise StateError("attempt identity must be new")
                if not _dependency_ready(document, task) or set(task["approved_gates"]) != set(task["gates"]):
                    raise StateError("dependencies or human gates not satisfied")
                actual = globals()["dependency_fingerprint"](document, task_id)
                if dependency_fingerprint != actual:
                    raise StateError("dependency fingerprint changed")
                task["attempt_id"] = attempt_id
                task["attempt_count"] += 1
                task["attempt_ids"].append(attempt_id)
                task["dependency_fingerprint"] = actual
            if to_state in {"BLOCKED", "FAILED", "AUDIT"}:
                require_text(reason, "reason")
            if to_state in SATISFIED:
                _sha(head_sha)
                require_text(evidence, "evidence")
                task["head_sha"], task["evidence"] = head_sha, evidence
            task["state"], task["reason"] = to_state, reason
        return self._mutate(expected_revision, event_id, update)

    def _check_attempt(self, document, task_id, attempt_id, fingerprint):
        task = document["tasks"][task_id]
        if attempt_id != task["attempt_id"] or fingerprint != task["dependency_fingerprint"]:
            raise StateError("late attempt or dependency event")
        if fingerprint != dependency_fingerprint(document, task_id):
            raise StateError("dependency heads changed")

    def begin_attempt(self, task_id, attempt_id, *, expected_revision, event_id, dependency_fingerprint):
        return self.transition(task_id, "DEVELOPING", expected_revision=expected_revision, event_id=event_id,
                               attempt_id=attempt_id, dependency_fingerprint=dependency_fingerprint)

    def request_repair(self, task_id, *, expected_revision, event_id, attempt_id, dependency_fingerprint, reason):
        def update(document):
            task = document["tasks"][task_id]
            self._check_attempt(document, task_id, attempt_id, dependency_fingerprint)
            require_text(reason, "reason")
            if task["state"] not in {"TESTING", "REVIEWING"} or task["repairs"] >= 3:
                raise StateError("repair not allowed or three-round limit reached")
            task["repairs"] += 1
            task["state"], task["reason"] = "DEVELOPING", reason
        return self._mutate(expected_revision, event_id, update)

    def record_publish_intent(self, task_id, *, expected_revision, event_id, intent_id,
                              branch, base_ref, base_sha, head_sha, artifact_sha256):
        def update(document):
            task = document["tasks"][task_id]
            if task["state"] != "READY_FOR_HUMAN_REVIEW" or task["publication"] is not None:
                raise StateError("publication requires completed review and no existing intent")
            task["publication"] = dict(intent_id=intent_id, branch=branch, base_ref=base_ref,
                base_sha=base_sha, head_sha=head_sha, artifact_sha256=artifact_sha256, pr_number=None, pr_url=None)
        return self._mutate(expected_revision, event_id, update)

    def confirm_publication(self, task_id, *, expected_revision, event_id, intent_id, head_sha, pr_number, pr_url):
        def update(document):
            intent = document["tasks"][task_id]["publication"]
            if (intent is None or intent["intent_id"] != intent_id or intent["head_sha"] != head_sha
                    or intent["pr_number"] is not None):
                raise StateError("unknown, stale, or already confirmed publication")
            intent["pr_number"], intent["pr_url"] = pr_number, pr_url
        return self._mutate(expected_revision, event_id, update)
