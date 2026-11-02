"""Protect the reviewed PR-03a commit while permitting isolated remediation files."""
from __future__ import annotations

import ast
from contextlib import redirect_stdout
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[3]
BASE = "cb5f73c288fe013763e1e3ee9ebc3eb94a44a6b0"
OUTPUT_ROOT = ROOT / "outputs/pr03a/checkout-remediation"
AUTHORIZED_DOCUMENTS = {
    "docs/web-alpha/pr03a/ACCEPTANCE.md",
    "docs/web-alpha/pr03a/CHANGES.md",
    "docs/web-alpha/pr03a/VALIDATION.json",
}
AUTHORIZED_TOOL_ADAPTERS = {
    "tools/pr03a/remediation/run_tests.py",
    "tools/pr03a/remediation/check_static.py",
    "tools/pr03a/remediation/check_protected.py",
}
HISTORICAL_CRLF_TOOL = "tools/pr03a/remediation/selftest_runner.py"


def main():
    spec = importlib.util.spec_from_file_location("original_pr03a_protection", ROOT / "tools/pr03a/check_protected.py")
    assert spec is not None and spec.loader is not None
    # This adapter intentionally configures a dynamically loaded tool module;
    # ModuleType alone cannot describe that module's configurable attributes.
    guard = cast(Any, importlib.util.module_from_spec(spec))
    spec.loader.exec_module(guard)
    guard.BASE = BASE
    # This round does not inspect or operate other worktrees. Immutable product
    # files and frozen frontend contracts in this checkout remain protected.
    guard.SIBLINGS = ()
    all_entries = guard.baseline_entries()
    # The user expressly requested updating these three documents. Their
    # original baseline checkout bytes are independently archived below.
    guard.baseline_entries = lambda: {name: object_id for name, object_id in all_entries.items()
                                      if name not in AUTHORIZED_DOCUMENTS | AUTHORIZED_TOOL_ADAPTERS}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    guard.OUT = OUTPUT_ROOT / "protection" / stamp
    guard.OUT.mkdir(parents=True, exist_ok=False)

    def retain_reviewed_test_modules(name, old_source, new_source):
        # Whole-module AST preserves fixtures, helpers, global parameters,
        # decorators, assertions and every original test. New regression tests
        # belong in additive modules, not replacements of reviewed assertions.
        unchanged = ast.dump(ast.parse(old_source)) == ast.dump(ast.parse(new_source))
        return {"old_test_functions": len(guard.test_functions(old_source)),
                "current_test_functions": len(guard.test_functions(new_source)),
                "whole_module_ast_unchanged": unchanged,
                "errors": [] if unchanged else [f"{name}:reviewed_test_module_changed"]}

    guard.retain_backend_tests = retain_reviewed_test_modules
    before_path = OUTPUT_ROOT / "sibling-frontend-before.json"
    if not before_path.exists():
        snapshots = [guard.sibling_snapshot(root) for root in guard.SIBLINGS]
        before_path.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2), encoding="utf-8")
    (guard.OUT / "sibling-frontend-before.json").write_bytes(before_path.read_bytes())
    # The inherited guard is one phase, not the final checkout verdict. Retain
    # its complete output without printing an intermediate green summary.
    base_output = io.StringIO()
    with redirect_stdout(base_output):
        result = guard.main()
    (guard.OUT / "base-guard.log").write_text(base_output.getvalue(), encoding="utf-8")
    output = guard.OUT / "protected-files.json"
    report = json.loads(output.read_text("utf-8"))
    # Original reviewed tooling is also kept byte-identical to its baseline
    # checkout representation, beyond the inherited Git-content comparison.
    tool_bytes = {}
    adapted_tool_bytes = {}
    reviewed_validation = json.loads(guard.git(ROOT, "show", BASE + ":docs/web-alpha/pr03a/VALIDATION.json"))
    for name in all_entries:
        if name.startswith("tools/pr03a/"):
            baseline = guard.git(ROOT, "cat-file", "--filters", "--path=" + name, BASE + ":" + name)
            actual = (ROOT / name).read_bytes() if (ROOT / name).is_file() else None
            item = {"baseline_checkout_sha256": guard.sha256(baseline),
                    "current_sha256": guard.sha256(actual) if actual is not None else None,
                    "raw_bytes_equal": actual == baseline}
            if name in AUTHORIZED_TOOL_ADAPTERS:
                archive = OUTPUT_ROOT / "before-tools" / Path(name).name
                archive.parent.mkdir(parents=True, exist_ok=True)
                if not archive.exists():
                    archive.write_bytes(baseline)
                item["original_archive"] = str(archive)
                item["original_archive_equal"] = archive.read_bytes() == baseline
                adapted_tool_bytes[name] = item
            else:
                current_object = (guard.git(ROOT, "hash-object", "--path=" + name, "--", str(ROOT / name)).decode().strip()
                                  if actual is not None else None)
                item["baseline_git_object"] = all_entries[name]
                item["current_git_object"] = current_object
                item["git_content_equal"] = current_object == all_entries[name]
                if name == HISTORICAL_CRLF_TOOL:
                    # Its existing CRLF worktree bytes were already recorded
                    # by the reviewed cb5 validation, while .gitattributes now
                    # renders LF via cat-file --filters. Either exact checkout
                    # form is allowed, but both must retain the original blob.
                    historical = reviewed_validation["source_sha256"][name]
                    item["historical_reviewed_checkout_sha256"] = historical
                    item["historical_reviewed_checkout_equal"] = item["current_sha256"] == historical
                    item["raw_checkout_allowed"] = item["raw_bytes_equal"] or item["historical_reviewed_checkout_equal"]
                else:
                    item["raw_checkout_allowed"] = item["raw_bytes_equal"]
                item["preserved"] = item["git_content_equal"] and item["raw_checkout_allowed"]
                item["failure_reasons"] = (
                    (["git_blob_mismatch"] if not item["git_content_equal"] else [])
                    + (["unapproved_checkout_bytes"] if not item["raw_checkout_allowed"] else []))
                tool_bytes[name] = item
    original_tools_unchanged = all(item["preserved"] for item in tool_bytes.values())
    tool_failures = {name: item["failure_reasons"] for name, item in tool_bytes.items()
                     if not item["preserved"]}
    base_guard_passed = report["passed"]
    report["mismatches"] = list(dict.fromkeys([*report["mismatches"], *tool_failures]))
    document_changes = {}
    original_documents_preserved = True
    for name in sorted(AUTHORIZED_DOCUMENTS):
        original = guard.git(ROOT, "cat-file", "--filters", "--path=" + name, BASE + ":" + name)
        archived = OUTPUT_ROOT / "before-documents" / Path(name).name
        archived.parent.mkdir(parents=True, exist_ok=True)
        if not archived.exists():
            archived.write_bytes(original)
        archive_bytes = archived.read_bytes() if archived.is_file() else None
        current = (ROOT / name).read_bytes() if (ROOT / name).is_file() else None
        preserved = archive_bytes == original
        original_documents_preserved = original_documents_preserved and preserved
        document_changes[name] = {"baseline_checkout_sha256": guard.sha256(original),
                                  "original_archive": str(archived),
                                  "archive_sha256": guard.sha256(archive_bytes) if archive_bytes is not None else None,
                                  "current_sha256": guard.sha256(current) if current is not None else None,
                                  "original_baseline_archived_exactly": preserved,
                                  "current_document_exists": current is not None}
        original_documents_preserved = original_documents_preserved and current is not None
    adapted_originals_preserved = all(item["original_archive_equal"] for item in adapted_tool_bytes.values())
    report.update(reviewed_pr03a_base=BASE, minimum_preserved_backend_cases=423,
                  base_guard_passed=base_guard_passed,
                  tool_preservation_failures=tool_failures,
                  reviewed_test_evolution="No new exceptions: every reviewed backend-test module AST is preserved; remediation regressions are additive modules",
                  original_tool_bytes=tool_bytes, original_tool_bytes_unchanged=original_tools_unchanged,
                  authorized_tool_adapters=adapted_tool_bytes,
                  original_baseline_file_count=len(all_entries),
                  authorized_document_updates=document_changes,
                  original_documents_preserved=original_documents_preserved,
                  immutable_migration_history="0001-0005 ten .py/.sql remain exact baseline checkout bytes and Git content",
                  sibling_initial_snapshot=str(before_path),
                  sibling_observation="No sibling worktree accessed in this round; this checkout's immutable protected files checked",
                  passed=result == 0 and base_guard_passed and original_tools_unchanged and original_documents_preserved and adapted_originals_preserved)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"reviewed_pr03a_base": BASE, "minimum_preserved_backend_cases": 423,
                      "original_tool_bytes_unchanged": original_tools_unchanged,
                      "base_guard_passed": base_guard_passed,
                      "tool_preservation_failures": tool_failures,
                      "mismatches": report["mismatches"],
                      "passed": report["passed"], "evidence": str(output)}, indent=2))
    return 0 if result == 0 and report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
