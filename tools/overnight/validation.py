"""Validate an immutable PR-06a candidate without executing candidate code.

The publisher must run its approved copy of this module and independently
recompute the report. A report supplied by the development job is not authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import unicodedata
from typing import Any


SHA = re.compile(r"[0-9a-f]{40}\Z")
ALLOWLISTS = {
    "06a": (
        "web/backend/classroom_web/asr_sim/",
        "web/backend/tests/asr_sim/",
        "tools/pr06a/",
        "docs/web-alpha/pr06a/",
    ),
}
PROTECTED_NAMES = {"agents.md", "claude.md", ".git", ".gitmodules", ".gitattributes"}
WINDOWS_RESERVED = {"con", "prn", "aux", "nul"} | {
    prefix + str(number) for prefix in ("com", "lpt") for number in range(1, 10)
}
SCOPE_FIELDS = {"add_prefixes", "modify_files", "delete_files", "deny_prefixes", "max_files", "max_bytes"}
# Generic workers never acquire authority over the controller, frozen inputs,
# prior test harnesses, local product, or durable acceptance evidence.
HARD_DENY_PREFIXES = (
    ".git", ".github", ".codex", ".agents", ".claude", "automation", "tools/overnight",
    "tools/pr00", "tools/pr01", "tools/pr02a", "tools/pr02b", "tools/pr03a",
    "docs/web-alpha/architecture/contracts", "docs/web-alpha/overnight",
    "docs/web-alpha/evidence", "docs/web-alpha/archive", "docs/web-alpha/repository-migration",
    "web/backend/migrations", "web/backend/alembic.ini", "web/backend/requirements.lock",
    "web/backend/pyproject.toml", "tests", "static", "templates", "data", "models",
)
WORKFLOW_BRANCH = re.compile(r"feature/workflow-[A-Za-z0-9][A-Za-z0-9._-]{0,119}\Z")


class ValidationError(ValueError):
    """The candidate cannot be safely published."""


def _covers(prefix, path):
    prefix, path = prefix.rstrip("/").casefold(), path.rstrip("/").casefold()
    return path == prefix or path.startswith(prefix + "/")


def hard_protected_path(path):
    """Non-overridable generic protections, independent of task-supplied scope."""
    parts = path.rstrip("/").split("/")
    if any(part.casefold() in PROTECTED_NAMES | {".github", ".codex", ".agents", ".claude"}
           or part.casefold().startswith(".env") for part in parts):
        return True
    if any(_covers(prefix, path) for prefix in HARD_DENY_PREFIXES):
        return True
    if len(parts) == 1:
        name = parts[0].casefold()
        return (name == "readme.md" or name.startswith(("config", "requirements", "setup", "start", "stop"))
                or name.endswith((".py", ".ps1", ".bat", ".cmd", ".vbs", ".json")))
    return False


def test_path(path):
    parts = path.casefold().split("/")
    name = parts[-1]
    return (any(part in {"tests", "test", "__tests__"} for part in parts[:-1])
            or name.startswith("test_") or name.endswith("_test.py")
            or ".test." in name or ".spec." in name)


def _scope_path(value, *, directory=False, allow_protected=False):
    if (not isinstance(value, str) or not value or (directory and not value.endswith("/"))
            or (not directory and value.endswith("/"))):
        raise ValidationError("scope requires explicit relative directories/files")
    path = value[:-1] if directory else value
    structural = (not path.startswith("/") and "\\" not in path and ":" not in path
                  and not any(ord(char) < 32 or ord(char) == 127 for char in path)
                  and all(part not in ("", ".", "..") and part == part.rstrip(" .")
                          and part.split(".", 1)[0].casefold() not in WINDOWS_RESERVED
                          for part in path.split("/")))
    if (not structural or (not allow_protected and not safe_path(path)) or any(char in path for char in '*?"<>|')
            or unicodedata.normalize("NFC", path) != path):
        raise ValidationError("unsafe scope path")
    return path


def validate_scope(scope):
    """Validate/copy a trusted scope. Denials cannot be carved out of a write grant.

    Disjoint grants keep the filesystem allowlist and immutable-object policy
    consistent without granting a parent directory containing a forbidden child.
    Existing tests cannot be modified/deleted; task tests may be newly added.
    """
    if not isinstance(scope, dict) or set(scope) != SCOPE_FIELDS:
        raise ValidationError("missing or unknown scope fields")
    result = {}
    for key in ("max_files", "max_bytes"):
        if type(scope[key]) is not int or scope[key] <= 0:
            raise ValidationError("scope limits must be positive integers")
        result[key] = scope[key]
    for key in ("add_prefixes", "modify_files", "delete_files", "deny_prefixes"):
        values = scope[key]
        if not isinstance(values, list):
            raise ValidationError("scope paths must be lists")
        checked = []
        for value in values:
            directory = key == "add_prefixes" or key == "deny_prefixes" and isinstance(value, str) and value.endswith("/")
            path = _scope_path(value, directory=directory, allow_protected=key == "deny_prefixes")
            if key != "deny_prefixes":
                if hard_protected_path(path) or (key == "add_prefixes" and
                        any(_covers(path, protected) for protected in HARD_DENY_PREFIXES)):
                    raise ValidationError("scope overlaps protected control, baseline or contract paths")
                if key in {"modify_files", "delete_files"} and test_path(path):
                    raise ValidationError("existing tests cannot be modified or deleted")
            checked.append(value)
        if len({item.casefold().rstrip("/") for item in checked}) != len(checked):
            raise ValidationError("duplicate or case-colliding scope paths")
        result[key] = sorted(checked)
    if {p.casefold() for p in result["modify_files"]} & {p.casefold() for p in result["delete_files"]}:
        raise ValidationError("one file cannot be both modified and deleted")
    grants = result["add_prefixes"] + result["modify_files"] + result["delete_files"]
    if not grants:
        raise ValidationError("scope has no authorized changes")
    if any(_covers(deny, grant) or _covers(grant, deny)
           for grant in grants for deny in result["deny_prefixes"]):
        raise ValidationError("write grants and deny paths must be disjoint")
    return result


def _valid_ref(ref):
    return (isinstance(ref, str) and (ref == "main" or re.fullmatch(
        r"feature/(?:workflow|overnight|web-pr06a)-[A-Za-z0-9][A-Za-z0-9._-]{0,119}", ref))
        and ".." not in ref and not ref.endswith((".", ".lock")))


def make_policy(task_id, spec):
    """Only the trusted controller may construct/pass this task authority."""
    if not isinstance(task_id, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,59}", task_id) is None:
        raise ValidationError("invalid policy task identity")
    if not isinstance(spec, dict) or not isinstance(spec.get("base"), dict):
        raise ValidationError("policy requires a task specification and base")
    branch, base_ref = spec.get("branch"), spec["base"].get("ref")
    if (not isinstance(branch, str) or WORKFLOW_BRANCH.fullmatch(branch) is None
            or not _valid_ref(branch) or not _valid_ref(base_ref) or branch == base_ref):
        raise ValidationError("policy requires distinct approved workflow head/base branches")
    result = {"id": task_id, "scope": validate_scope(spec.get("scope")), "branch": branch, "base_ref": base_ref}
    result["sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False,
                                                separators=(",", ":")).encode("utf-8")).hexdigest()
    return result


def validate_policy(policy, task_id=None):
    if not isinstance(policy, dict) or set(policy) != {"id", "scope", "branch", "base_ref", "sha256"}:
        raise ValidationError("invalid trusted policy fields")
    expected = make_policy(policy["id"], {"scope": policy["scope"], "branch": policy["branch"],
                                         "base": {"ref": policy["base_ref"]}})
    if policy != expected or task_id is not None and policy["id"] != task_id:
        raise ValidationError("policy digest or task binding differs")
    return expected


def git(repo: Path, *args: str) -> bytes:
    # Inherited GIT_DIR, replacement objects and config injection must not
    # redirect object reads. No checkout/filter/hooks/candidate tools are run.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_")}
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repo), "-c", "core.fsmonitor=false",
         *args], env=env, capture_output=True, check=False,
    )
    if result.returncode:
        # Git diagnostics may include local URLs; do not echo inherited data.
        raise ValidationError("Git object inspection failed: " + args[0])
    return result.stdout


def safe_path(path: str) -> bool:
    if (not path or path.startswith("/") or "\\" in path or ":" in path
            or any(ord(char) < 32 or ord(char) == 127 for char in path)):
        return False
    parts = path.split("/")
    return all(
        part not in ("", ".", "..")
        and part == part.rstrip(" .")
        and part.casefold() not in PROTECTED_NAMES
        and part.split(".", 1)[0].casefold() not in WINDOWS_RESERVED
        for part in parts
    )


def candidate_patch(repo: Path, base: str, head: str) -> bytes:
    return git(repo, "diff", "--binary", "--full-index", "--no-renames",
               "--no-ext-diff", "--no-textconv", "--src-prefix=a/",
               "--dst-prefix=b/", base, head, "--")


def validate_candidate(
    repo: Path, base: str, head: str, task: str = "06a", *,
    max_bytes: int = 500_000, max_files: int = 30, policy=None,
) -> dict[str, Any]:
    """Return a fail-closed, content-addressed manifest from Git objects only."""
    report: dict[str, Any] = {
        "schema_version": 1, "task": task, "base_sha": base, "head_sha": head,
        "valid": False, "errors": [], "files": [],
        "limits": {"max_bytes": max_bytes, "max_files": max_files},
        "patch_format": "git-diff-binary-full-index-v1",
    }
    errors: list[str] = report["errors"]
    try:
        scope = None
        if policy is not None:
            policy = validate_policy(policy, task)
            scope = policy["scope"]
            max_bytes, max_files = scope["max_bytes"], scope["max_files"]
            report["limits"] = {"max_bytes": max_bytes, "max_files": max_files}
            report["policy_sha256"] = policy["sha256"]
        elif task not in ALLOWLISTS:
            raise ValidationError("Task has no approved publication allowlist")
        if not SHA.fullmatch(base) or not SHA.fullmatch(head):
            raise ValidationError("base/head must be complete lowercase 40-character commit SHAs")
        if type(max_bytes) is not int or type(max_files) is not int or min(max_bytes, max_files) <= 0:
            raise ValidationError("Limits must be positive integers")
        repo = repo.resolve(strict=True)
        for name, commit in (("base", base), ("head", head)):
            if git(repo, "cat-file", "-t", commit).strip() != b"commit":
                raise ValidationError(name + " is not a commit object")
            report[name + "_tree"] = git(repo, "rev-parse", commit + "^{tree}").decode().strip()
        git(repo, "merge-base", "--is-ancestor", base, head)
        if git(repo, "rev-list", "--merges", base + ".." + head).strip():
            raise ValidationError("Candidate includes a merge commit; automatic integration is not permitted")
        raw = git(repo, "diff", "--raw", "-z", "--no-abbrev", "--no-renames",
                  "--no-ext-diff", "--no-textconv", base, head, "--")
        tokens = raw.split(b"\0")
        if tokens[-1] != b"" or (len(tokens) - 1) % 2:
            raise ValidationError("Malformed Git raw diff")
        records = list(zip(tokens[0:-1:2], tokens[1:-1:2]))
        if not records:
            raise ValidationError("Empty diff cannot produce a development PR")
        if len(records) > max_files:
            raise ValidationError("Changed file count exceeds the configured limit")
        base_paths = {path.decode("utf-8").casefold(): path.decode("utf-8")
                      for path in git(repo, "ls-tree", "-r", "-z", "--name-only", base).split(b"\0") if path}
        changed_casefold: set[str] = set()
        total = 0
        for metadata, raw_path in records:
            path = raw_path.decode("utf-8", errors="strict")
            if scope is not None:
                _scope_path(path)
            fields = metadata.decode("ascii").split()
            if len(fields) != 5 or not fields[0].startswith(":"):
                raise ValidationError("Malformed Git raw diff metadata")
            old_mode, mode, old_blob, blob, status = fields
            item: dict[str, Any] = {"path": path, "status": status, "mode": mode, "blob_sha": blob}
            report["files"].append(item)
            if path.casefold() in changed_casefold or (path.casefold() in base_paths and base_paths[path.casefold()] != path):
                errors.append("Path collides on a case-insensitive checkout: " + path)
            changed_casefold.add(path.casefold())
            if not safe_path(path):
                errors.append("Unsafe or protected path: " + repr(path))
            elif scope is None:
                if not any(path.startswith(prefix) for prefix in ALLOWLISTS[task]):
                    errors.append("Path is outside the approved task: " + path)
            elif (hard_protected_path(path) or any(_covers(deny, path) for deny in scope["deny_prefixes"])
                  or status != "A" and test_path(path)):
                errors.append("Task policy cannot change a protected path: " + path)
            if scope is None:
                if status != "A" or old_mode != ":000000" or old_blob != "0" * 40:
                    errors.append("Only new isolated files are permitted: " + path)
            else:
                item.update(old_mode=old_mode[1:], old_blob_sha=old_blob)
                allowed = ((status == "A" and old_mode == ":000000" and old_blob == "0" * 40
                            and any(path.startswith(prefix) for prefix in scope["add_prefixes"]))
                           or (status == "M" and path in scope["modify_files"] and old_mode[1:] in {"100644", "100755"})
                           or (status == "D" and path in scope["delete_files"] and old_mode[1:] in {"100644", "100755"}
                               and mode == "000000" and blob == "0" * 40))
                if not allowed:
                    errors.append("Change is outside its approved add/modify/delete policy: " + path)
            deleted = scope is not None and status == "D" and old_mode[1:] in {"100644", "100755"}
            if not deleted and mode not in ("100644", "100755"):
                errors.append("Only regular files are permitted: " + path)
                continue
            inspected_blob = old_blob if deleted else blob
            size = int(git(repo, "cat-file", "-s", inspected_blob).strip())
            item["old_size_bytes" if deleted else "size_bytes"] = size
            total += size
            if total > max_bytes:
                raise ValidationError("Added content exceeds the configured byte limit")
            body = git(repo, "cat-file", "blob", inspected_blob)
            if len(body) != size:
                raise ValidationError("Blob size changed during inspection")
            item["old_sha256" if deleted else "sha256"] = hashlib.sha256(body).hexdigest()
        report["content_bytes"] = total
        if errors:
            return report
        patch = candidate_patch(repo, base, head)
        report["patch_bytes"] = len(patch)
        if len(patch) > max_bytes:
            raise ValidationError("Patch exceeds the configured byte limit")
        report["patch_sha256"] = hashlib.sha256(patch).hexdigest()
        report["valid"] = True
    except (ValidationError, OSError, UnicodeError, ValueError) as error:
        errors.append(str(error))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--task", default="06a")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-bytes", type=int, default=500_000)
    parser.add_argument("--max-files", type=int, default=30)
    args = parser.parse_args()
    report = validate_candidate(args.repo, args.base, args.head, args.task,
                                max_bytes=args.max_bytes, max_files=args.max_files)
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
