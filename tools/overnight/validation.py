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


class ValidationError(ValueError):
    """The candidate cannot be safely published."""


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
    max_bytes: int = 500_000, max_files: int = 30,
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
        if task not in ALLOWLISTS:
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
            elif not any(path.startswith(prefix) for prefix in ALLOWLISTS[task]):
                errors.append("Path is outside the approved task: " + path)
            if status != "A" or old_mode != ":000000" or old_blob != "0" * 40:
                errors.append("Only new isolated files are permitted: " + path)
            if mode not in ("100644", "100755"):
                errors.append("Only regular files are permitted: " + path)
                continue
            size = int(git(repo, "cat-file", "-s", blob).strip())
            item["size_bytes"] = size
            total += size
            if total > max_bytes:
                raise ValidationError("Added content exceeds the configured byte limit")
            body = git(repo, "cat-file", "blob", blob)
            if len(body) != size:
                raise ValidationError("Blob size changed during inspection")
            item["sha256"] = hashlib.sha256(body).hexdigest()
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
