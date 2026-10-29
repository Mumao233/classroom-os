"""Check PR-02b scope, frozen history/contracts and sibling frontend snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
BASE = "a76558ac64a95ae28874eaecabb71c5a4381a7ad"
OUT = ROOT / "outputs/pr02b"
SIBLINGS = (
    (ROOT.parent / "web-pr09a-aurora", "b134db9e1eee54c18fd3147accc39932a91e3186"),
    (ROOT.parents[1] / "classroom-os/.claude/worktrees/web-alpha-aurora-design-28bac7",
     "43fa23c141e3350e90d72ed7768e3b8de7d68337"),
)


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root)


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def baseline_entries():
    result = {}
    for entry in git(ROOT, "ls-tree", "-r", "-z", BASE).split(b"\0"):
        if not entry:
            continue
        header, encoded_name = entry.split(b"\t", 1)
        _, kind, object_id = header.split()
        if kind != b"blob":
            raise RuntimeError("unexpected non-file baseline entry")
        result[encoded_name.decode("utf-8")] = object_id.decode("ascii")
    return result


def sibling_snapshot(root, expected_head):
    head = git(root, "rev-parse", "HEAD").decode().strip()
    names = [name.decode("utf-8") for name in
             git(root, "ls-files", "-z", "web/frontend").split(b"\0") if name]
    files = {}
    mismatch = []
    for name in names:
        file = root / name
        if not file.is_file():
            mismatch.append(name)
            files[name] = {"sha256": None, "git_object": None}
            continue
        actual = git(root, "hash-object", "--path=" + name, "--", str(file)).decode().strip()
        expected = git(root, "rev-parse", expected_head + ":" + name).decode().strip()
        files[name] = {"sha256": sha256(file.read_bytes()), "git_object": actual}
        if actual != expected:
            mismatch.append(name)
    return {"root": str(root), "head": head, "expected_head": expected_head,
            "status": git(root, "status", "--short").decode("utf-8"),
            "frontend_files": files, "frontend_git_mismatches": mismatch,
            "head_matches": head == expected_head}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    entries = baseline_entries()
    protected, migrations, contracts = {}, {}, {}
    mismatches = []
    for name, expected_object in entries.items():
        is_migration = name.startswith("web/backend/migrations/versions/")
        is_contract = name.startswith("docs/web-alpha/architecture/contracts/")
        if name.startswith("web/backend/") and not is_migration:
            continue
        file = ROOT / name
        baseline = git(ROOT, "show", BASE + ":" + name)
        baseline_checkout = git(ROOT, "cat-file", "--filters", "--path=" + name, BASE + ":" + name)
        actual = file.read_bytes() if file.is_file() else None
        actual_object = (git(ROOT, "hash-object", "--path=" + name, "--", str(file)).decode().strip()
                         if actual is not None else None)
        raw_equal = actual == baseline_checkout
        item = {"baseline_git_object": expected_object, "current_git_object": actual_object,
                "baseline_git_blob_sha256": sha256(baseline),
                "baseline_checkout_sha256": sha256(baseline_checkout),
                "current_sha256": sha256(actual) if actual is not None else None,
                "raw_bytes_equal": raw_equal, "git_content_equal": actual_object == expected_object}
        protected[name] = item
        if is_migration:
            migrations[name] = item
        if is_contract:
            contracts[name] = item
        # Check physical bytes against Git's baseline checkout representation:
        # .sql uses autocrlf and .ps1/.vbs explicitly use CRLF on this Windows
        # checkout, while Git stores LF. This preserves literal history checks.
        if actual_object != expected_object or ((is_migration or is_contract) and not raw_equal):
            mismatches.append(name)

    changed = [name.decode("utf-8") for name in
               git(ROOT, "diff", "--name-only", "-z", BASE).split(b"\0") if name]
    untracked = [name.decode("utf-8") for name in
                 git(ROOT, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if name]
    allowed = ("web/backend/", "docs/web-alpha/pr02b/", "tools/pr02b/")
    scope_violations = [name for name in changed + untracked if not name.startswith(allowed)]

    siblings = [sibling_snapshot(root, expected_head) for root, expected_head in SIBLINGS]
    first_path = OUT / "sibling-frontend-before.json"
    if not first_path.exists():
        first_path.write_text(json.dumps(siblings, ensure_ascii=False, indent=2), encoding="utf-8")
    first = json.loads(first_path.read_text("utf-8"))
    siblings_unchanged = siblings == first
    sibling_heads_valid = all(item["head_matches"] and not item["frontend_git_mismatches"]
                              for item in siblings)
    report = {
        "base": BASE, "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_files": len(entries), "protected_files_checked": len(protected),
        "comparison": "Git-clean content for protected files; literal bytes additionally required for frozen contracts/prior migrations against Git baseline checkout filters (preserves CRLF attributes)",
        "protected": protected, "prior_migration_files": migrations, "frozen_contract_files": contracts,
        "mismatches": mismatches, "scope_violations": scope_violations,
        "changed_tracked_files": changed, "new_untracked_files": untracked,
        "sibling_snapshots": siblings,
        "sibling_snapshot_baseline": str(first_path),
        "siblings_unchanged_since_snapshot": siblings_unchanged,
        "sibling_expected_heads_and_committed_frontend_match": sibling_heads_valid,
        "limitations": "Sibling first snapshot is collected during PR-02b work; committed frontend content is also checked against fixed expected HEAD. Ignored dependencies/output and private .claude contents are not read.",
        "passed": not mismatches and not scope_violations and siblings_unchanged and sibling_heads_valid,
    }
    output = OUT / "protected-files.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "base", "baseline_files", "protected_files_checked", "mismatches", "scope_violations",
        "siblings_unchanged_since_snapshot", "sibling_expected_heads_and_committed_frontend_match", "passed")}, indent=2))
    print("Evidence:", output)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
