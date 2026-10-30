"""Protect the reviewed PR-03a commit while permitting isolated remediation files."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[3]
BASE = "088984fbd7c9f36f4250c9af340d6a73a0c57960"
OUTPUT_ROOT = ROOT / "outputs/pr03a/remediation"
AUTHORIZED_DOCUMENTS = {
    "docs/web-alpha/pr03a/ACCEPTANCE.md",
    "docs/web-alpha/pr03a/CHANGES.md",
    "docs/web-alpha/pr03a/VALIDATION.json",
}


def main():
    spec = importlib.util.spec_from_file_location("original_pr03a_protection", ROOT / "tools/pr03a/check_protected.py")
    assert spec is not None and spec.loader is not None
    # This adapter intentionally configures a dynamically loaded tool module;
    # ModuleType alone cannot describe that module's configurable attributes.
    guard = cast(Any, importlib.util.module_from_spec(spec))
    spec.loader.exec_module(guard)
    guard.BASE = BASE
    all_entries = guard.baseline_entries()
    # The user expressly requested updating these three documents. Their
    # original baseline checkout bytes are independently archived below.
    guard.baseline_entries = lambda: {name: object_id for name, object_id in all_entries.items()
                                      if name not in AUTHORIZED_DOCUMENTS}
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
    result = guard.main()
    output = guard.OUT / "protected-files.json"
    report = json.loads(output.read_text("utf-8"))
    # Original reviewed tooling is also kept byte-identical to its baseline
    # checkout representation, beyond the inherited Git-content comparison.
    tool_bytes = {}
    for name in all_entries:
        if name.startswith("tools/pr03a/"):
            baseline = guard.git(ROOT, "cat-file", "--filters", "--path=" + name, BASE + ":" + name)
            actual = (ROOT / name).read_bytes() if (ROOT / name).is_file() else None
            tool_bytes[name] = {"baseline_checkout_sha256": guard.sha256(baseline),
                                "current_sha256": guard.sha256(actual) if actual is not None else None,
                                "raw_bytes_equal": actual == baseline}
    original_tools_unchanged = all(item["raw_bytes_equal"] for item in tool_bytes.values())
    document_changes = {}
    original_documents_preserved = True
    for name in sorted(AUTHORIZED_DOCUMENTS):
        original = guard.git(ROOT, "cat-file", "--filters", "--path=" + name, BASE + ":" + name)
        archived = OUTPUT_ROOT / "before-documents" / Path(name).name
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
    report.update(reviewed_pr03a_base=BASE, minimum_preserved_backend_cases=358,
                  reviewed_test_evolution="No new exceptions: every reviewed backend-test module AST is preserved; remediation regressions are additive modules",
                  original_tool_bytes=tool_bytes, original_tool_bytes_unchanged=original_tools_unchanged,
                  original_baseline_file_count=len(all_entries),
                  authorized_document_updates=document_changes,
                  original_documents_preserved=original_documents_preserved,
                  immutable_migration_history="0001,0002,0003,0004 .py/.sql remain exact baseline checkout bytes and Git content",
                  sibling_initial_snapshot=str(before_path),
                  passed=report["passed"] and original_tools_unchanged and original_documents_preserved)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"reviewed_pr03a_base": BASE, "minimum_preserved_backend_cases": 358,
                      "original_tool_bytes_unchanged": original_tools_unchanged,
                      "passed": report["passed"], "evidence": str(output)}, indent=2))
    return 0 if result == 0 and report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
