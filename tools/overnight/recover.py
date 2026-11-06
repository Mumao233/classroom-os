"""Explicit operator recovery for one proven Codex pre-launch config failure.

This is not a general retry command. It never refunds a reservation or resets a
clock, counter, or failed call. All old records and logs remain available, and a
prepared journal makes the workspace/state/ledger/scope changes restartable.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from .budget import SubscriptionLimits
from .state import StateError, StateStore, atomic_json, process_lock, read_json, require_keys


REMOTE = "https://github.com/Mumao233/classroom-os.git"
KNOWN_ERROR = b'Error loading config.toml: invalid transport\nin `mcp_servers."codex_app"`\n\n'
JOURNAL_NAME = "prelaunch-recovery.json"
SHA = re.compile(r"[0-9a-f]{40}\Z")


def _git(repo, hooks, *args):
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1", GCM_INTERACTIVE="never", GCM_GUI_PROMPT="0")
    try:
        result = subprocess.run(["git", "--no-optional-locks", "-C", str(repo), "-c", "core.fsmonitor=false",
            "-c", "core.hooksPath=" + str(hooks), *args], env=env, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        raise StateError("recovery Git operation failed") from None
    if result.returncode:
        raise StateError("recovery Git operation failed")
    return result.stdout.decode("utf-8").strip()


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence(run_root):
    folder = run_root / "develop-0"
    paths = {name: folder / name for name in ("stdout.jsonl", "stderr.log", "process.json", "result.json", "receipt.json")}
    if not all(path.is_file() and not path.is_symlink() for path in paths.values()):
        raise StateError("original prelaunch evidence is missing")
    if paths["stdout.jsonl"].read_bytes() != b"" or paths["stderr.log"].read_bytes() != KNOWN_ERROR:
        raise StateError("failure is not the exact known no-output configuration error")
    for name in ("process.json", "result.json", "receipt.json"):
        value = read_json(paths[name])
        if (not isinstance(value, dict) or value.get("passed") is not False
                or type(value.get("exit_code")) is not int or value["exit_code"] != 1):
            raise StateError("original process did not fail before model execution")
        if name == "process.json" and value.get("error_reason") is not None:
            raise StateError("original process contains a different failure reason")
        if name != "process.json" and ("usage" not in value or value["usage"] is not None or value.get("error_reason") != "execution_failed"):
            raise StateError("usage or failure reason does not prove a prelaunch failure")
        if name == "receipt.json" and value.get("candidate_binding") is not None:
            raise StateError("a candidate-bound call is not a first development prelaunch failure")
    return {name: _digest(path) for name, path in paths.items()}


def _validate_original(state, ledger, scope, run_id, base_ref, config_sha):
    if (state["run_id"] != run_id or state["config_sha"] != config_sha
            or set(state["tasks"]) != {"00", "01", "06a"}):
        raise StateError("recovery authorization does not match the original run")
    task = state["tasks"]["06a"]
    if (task["state"] != "BLOCKED" or task["head_sha"] is not None or task["publication"] is not None
            or task["attempt_count"] != 1 or task["repairs"] != 0 or task["evidence"] is not None):
        raise StateError("only the first prelaunch-blocked attempt is recoverable")
    for task_id in ("00", "01"):
        baseline = state["tasks"][task_id]
        if baseline["state"] != "SKIPPED" or baseline["head_sha"] != state["base_sha"] or baseline["publication"] is not None:
            raise StateError("baseline acceptance mapping changed")
    if (ledger["status"] != "BLOCKED" or ledger["block_reason"] != "interrupted"
            or set(ledger["calls"]) != {"develop-0"} or ledger["limits"]["extra_api_spend_usd"] != 0):
        raise StateError("quota, authentication, budget or other failures are not recoverable here")
    call = ledger["calls"]["develop-0"]
    if (call["status"] != "BLOCKED" or call["block_reason"] != "interrupted" or call["usage"] is not None
            or call["task_id"] != "06a" or call["reserved_turns"] != 1 or ledger["repairs"]):
        raise StateError("original reservation or usage is inconsistent")
    if (scope.get("base_sha") != state["base_sha"] or scope.get("config_sha") != config_sha
            or scope.get("base_ref") != base_ref or scope.get("simulation_only") is not True
            or type(scope.get("extra_api_spend_usd")) is not int or scope["extra_api_spend_usd"] != 0
            or scope.get("author") != "Mumao233"):
        raise StateError("original scope does not authorize this recovery")


def _after_state(before, new_base, event):
    value = deepcopy(before)
    value["base_sha"] = new_base
    for task_id in ("00", "01"):
        value["tasks"][task_id]["head_sha"] = new_base
    value["tasks"]["06a"]["state"] = "AUDIT"
    value["tasks"]["06a"]["reason"] = "Operator reconciled exact prelaunch configuration failure; original call remains reserved"
    value["revision"] += 1
    value["events"].append(event)
    return value


def _after_ledger(before, event):
    value = deepcopy(before)
    value["status"], value["block_reason"] = "READY", None
    value["revision"] += 1
    value["events"].append(event)
    return value


def _cas_change(store, before, after, event):
    current = store.snapshot()
    if current == after:
        return
    if current != before:
        raise StateError("recovery state changed outside its journal")
    def update(document):
        if document != before:
            raise StateError("recovery state changed during compare-and-swap")
        # JsonStore owns the revision/event append; all other data comes from
        # the deterministically constructed after-image.
        for key in after.keys() - {"revision", "events"}:
            document[key] = deepcopy(after[key])
    result = store._mutate(before["revision"], event, update)
    if result != after:
        raise StateError("recovery compare-and-swap result mismatch")


def reconcile_prelaunch(source, run_root, new_base, base_ref):
    """Reconcile exactly the recorded failure, preserving the same run budget."""
    source, run_root = Path(source).resolve(strict=True), Path(run_root).resolve(strict=True)
    expected_runs = (source / "work/overnight/runs").resolve()
    if (run_root.parent != expected_runs or not re.fullmatch(r"[a-z0-9-]{1,60}", run_root.name)
            or not isinstance(new_base, str) or SHA.fullmatch(new_base) is None
            or not isinstance(base_ref, str) or re.fullmatch(r"feature/overnight-[a-z0-9-]+", base_ref) is None):
        raise StateError("invalid recovery scope")
    run_id = run_root.name
    branch = "feature/web-pr06a-" + run_id
    workspace = (run_root / "workspace").resolve(strict=True)
    if workspace.parent != run_root:
        raise StateError("recovery workspace escapes its run")
    hooks = run_root / "recovery-empty-hooks"
    hooks.mkdir(exist_ok=True)
    if hooks.is_symlink() or any(hooks.iterdir()):
        raise StateError("recovery hooks directory is not empty")
    state_store = StateStore(run_root / "state.json")
    ledger_store = SubscriptionLimits(run_root / "subscription.json")
    scope_path, journal_path = run_root / "scope.json", run_root / JOURNAL_NAME
    with process_lock(expected_runs / "controller.lock", timeout_seconds=1), process_lock(run_root / "recovery.lock", timeout_seconds=1):
        if _git(source, hooks, "rev-parse", "HEAD") != new_base or _git(source, hooks, "status", "--porcelain", "--untracked-files=all"):
            raise StateError("recovery source must be clean at the exact new base")
        if _git(source, hooks, "remote", "get-url", "origin") != REMOTE:
            raise StateError("recovery source is not the approved repository")
        config_sha = _digest(source / "automation/pilot.json")
        evidence = _evidence(run_root)
        if journal_path.exists():
            journal = read_json(journal_path)
            require_keys(journal, {"version", "status", "run_id", "old_base", "new_base", "base_ref", "config_sha",
                "branch", "before_state", "before_ledger", "before_scope", "evidence"})
            if (type(journal["version"]) is not int or journal["version"] != 1
                    or journal["status"] not in {"PREPARED", "COMPLETE"}
                    or journal["run_id"] != run_id or journal["new_base"] != new_base or journal["base_ref"] != base_ref
                    or journal["config_sha"] != config_sha or journal["branch"] != branch or journal["evidence"] != evidence):
                raise StateError("recovery journal does not match the requested reconciliation")
            before_state, before_ledger, before_scope = (journal["before_state"], journal["before_ledger"], journal["before_scope"])
        else:
            before_state, before_ledger, before_scope = state_store.snapshot(), ledger_store.snapshot(), read_json(scope_path)
            journal = {"version": 1, "status": "PREPARED", "run_id": run_id, "old_base": before_state["base_sha"],
                "new_base": new_base, "base_ref": base_ref, "config_sha": config_sha, "branch": branch,
                "before_state": before_state, "before_ledger": before_ledger, "before_scope": before_scope, "evidence": evidence}
        state_store.validate(before_state)
        ledger_store.validate(before_ledger)
        _validate_original(before_state, before_ledger, before_scope, run_id, base_ref, config_sha)
        old_base = before_state["base_sha"]
        if journal["old_base"] != old_base or new_base == old_base:
            raise StateError("recovery requires an exact new descendant infrastructure commit")
        _git(source, hooks, "merge-base", "--is-ancestor", old_base, new_base)
        reason = ledger_store._limit_reason(before_ledger, "06a", int(time.time() * 1000), new_call=True, reserved_turns=1)
        if reason:
            raise StateError("original subscription limits still block recovery: " + reason)
        event = "operator-prelaunch-recovery:" + new_base
        after_state, after_ledger = _after_state(before_state, new_base, event), _after_ledger(before_ledger, event)
        after_scope = dict(before_scope, base_sha=new_base)
        state_store.validate(after_state)
        ledger_store.validate(after_ledger)
        for current, before, after in ((state_store.snapshot(), before_state, after_state),
                (ledger_store.snapshot(), before_ledger, after_ledger), (read_json(scope_path), before_scope, after_scope)):
            if current != before and current != after:
                raise StateError("recovery journal checkpoint drifted")
        current_head = _git(workspace, hooks, "rev-parse", "HEAD")
        if (current_head not in {old_base, new_base}
                or _git(workspace, hooks, "status", "--porcelain", "--untracked-files=all")
                or _git(workspace, hooks, "branch", "--show-current") != branch
                or _git(workspace, hooks, "remote", "get-url", "origin") != REMOTE):
            raise StateError("workspace has candidate changes or an unexpected source mapping")
        if _git(source, hooks, "ls-remote", "--heads", "origin", "refs/heads/" + branch):
            raise StateError("a remote candidate already exists; prelaunch recovery is forbidden")
        if journal["status"] == "COMPLETE":
            if current_head != new_base or state_store.snapshot() != after_state or ledger_store.snapshot() != after_ledger or read_json(scope_path) != after_scope:
                raise StateError("completed recovery changed; never reset later work")
            return {"status": "already_reconciled", "run_id": run_id, "old_base": old_base, "new_base": new_base,
                    "reserved_calls": len(after_ledger["calls"]), "journal": str(journal_path)}
        if not journal_path.exists():
            atomic_json(journal_path, journal)
        if current_head == old_base:
            _git(workspace, hooks, "fetch", "--no-tags", str(source), new_base)
            # Recheck immediately before the explicitly authorized local reset.
            if _git(workspace, hooks, "rev-parse", "HEAD") != old_base or _git(workspace, hooks, "status", "--porcelain", "--untracked-files=all"):
                raise StateError("workspace changed before recovery checkout")
            _git(workspace, hooks, "checkout", "-B", branch, new_base)
        if _git(workspace, hooks, "rev-parse", "HEAD") != new_base or _git(workspace, hooks, "status", "--porcelain", "--untracked-files=all"):
            raise StateError("workspace recovery did not reach the exact clean new base")
        _cas_change(state_store, before_state, after_state, event)
        _cas_change(ledger_store, before_ledger, after_ledger, event)
        if read_json(scope_path) == before_scope:
            atomic_json(scope_path, after_scope)
        elif read_json(scope_path) != after_scope:
            raise StateError("scope changed during recovery")
        journal["status"] = "COMPLETE"
        atomic_json(journal_path, journal)
        return {"status": "reconciled", "run_id": run_id, "old_base": old_base, "new_base": new_base,
                "reserved_calls": len(after_ledger["calls"]), "journal": str(journal_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--new-base", required=True)
    parser.add_argument("--base-ref", required=True)
    args = parser.parse_args()
    print(json.dumps(reconcile_prelaunch(args.source, args.run_root, args.new_base, args.base_ref)))


if __name__ == "__main__":
    main()
