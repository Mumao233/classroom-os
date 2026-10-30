"""Check PR-03a scope, immutable history/contracts and legacy test retention."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE = "3b3881540519c27075119f99bb1dbff35d369e35"
OUT = ROOT / "outputs/pr03a"
SIBLINGS = (
    ROOT.parent / "web-pr09a-aurora",
    ROOT.parents[1] / "classroom-os/.claude/worktrees/web-alpha-aurora-design-28bac7",
)
ADDITIVE_SCHEMA_TEST = "web/backend/tests/integration/test_data_foundation.py"
LEGACY_ROLLBACK_TEST = "web/backend/tests/integration/test_task_migrations.py"


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root)


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def baseline_entries():
    entries = {}
    for entry in git(ROOT, "ls-tree", "-r", "-z", BASE).split(b"\0"):
        if not entry:
            continue
        header, name = entry.split(b"\t", 1)
        _, kind, object_id = header.split()
        if kind != b"blob":
            raise RuntimeError("unexpected non-file baseline entry")
        entries[name.decode("utf-8")] = object_id.decode("ascii")
    return entries


def test_functions(source):
    module = ast.parse(source)
    return {node.name: node for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")}


def retain_backend_tests(name, old_source, new_source):
    """Keep all old tests; allow only the additive table-set assertion to grow."""
    before, after = test_functions(old_source), test_functions(new_source)
    errors = []
    expected_module = ast.parse(old_source)
    if name == ADDITIVE_SCHEMA_TEST:
        expected_module.body.insert(next(index for index, node in enumerate(expected_module.body)
                                        if isinstance(node, ast.ImportFrom)
                                        and node.module == "classroom_web.infrastructure.task_schema") + 1,
                                    ast.parse("from classroom_web.infrastructure.identity_schema import tables as identity_tables").body[0])
        for node in expected_module.body:
            if isinstance(node, ast.FunctionDef) and node.name == "test_only_required_tables_and_contract_columns_exist":
                first_assert = next(index for index, statement in enumerate(node.body) if isinstance(statement, ast.Assert))
                node.body[first_assert] = ast.parse(
                    'assert set(inspector.get_table_names()) == expected | set(task_tables) | set(identity_tables) | {"alembic_version"}'
                ).body[0]
    if name == LEGACY_ROLLBACK_TEST:
        replacements = 0
        for function in expected_module.body:
            if isinstance(function, ast.FunctionDef) and function.name == "test_failed_later_migration_rolls_back_task_data_ddl_and_version":
                for node in ast.walk(function):
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "upgrade"
                            and len(node.args) == 3 and isinstance(node.args[1], ast.Constant) and node.args[1].value == "head"
                            and isinstance(node.args[2], ast.Name) and node.args[2].id == "scratch"):
                        node.args[1] = ast.Constant(value="ffff_task_failure")
                        replacements += 1
        if replacements != 1:
            errors.append(f"{name}:unexpected_baseline_rollback_fixture")
    if ast.dump(expected_module) != ast.dump(ast.parse(new_source)):
        errors.append(f"{name}:module_changed_beyond_exact_additive_schema_import_and_table_expectation")
    for function_name, original in before.items():
        current = after.get(function_name)
        if current is None:
            errors.append(f"{name}:{function_name}:removed")
            continue
        if (name, function_name) == (ADDITIVE_SCHEMA_TEST, "test_only_required_tables_and_contract_columns_exist"):
            # The sole table-name assertion must include the new PR-03a tables.
            # Every remaining assertion, including exact existing columns,
            # nullability and metadata equivalence, remains bytecode-equivalent.
            old_asserts = [node for node in ast.walk(original) if isinstance(node, ast.Assert)]
            new_asserts = [node for node in ast.walk(current) if isinstance(node, ast.Assert)]
            if (len(new_asserts) < len(old_asserts)
                    or [ast.dump(node) for node in old_asserts[1:]] != [ast.dump(node) for node in new_asserts[1:]]
                    or ast.dump(original.args) != ast.dump(current.args)
                    or [ast.dump(node) for node in original.decorator_list] != [ast.dump(node) for node in current.decorator_list]):
                errors.append(f"{name}:{function_name}:original_assertions_changed")
        elif (name, function_name) == (LEGACY_ROLLBACK_TEST, "test_failed_later_migration_rolls_back_task_data_ddl_and_version"):
            expected_function = next(node for node in expected_module.body if isinstance(node, ast.FunctionDef) and node.name == function_name)
            if ast.dump(expected_function) != ast.dump(current):
                errors.append(f"{name}:{function_name}:changed_beyond_explicit_failure_revision_target")
        elif ast.dump(original) != ast.dump(current):
            errors.append(f"{name}:{function_name}:changed")
    return {"old_test_functions": len(before), "current_test_functions": len(after), "errors": errors}


def sibling_snapshot(root):
    """Observe public frontend source only, without touching sibling worktrees."""
    names = [name.decode("utf-8") for name in git(root, "ls-files", "-z", "web/frontend").split(b"\0") if name]
    files = {name: sha256((root / name).read_bytes()) if (root / name).is_file() else None for name in names}
    return {"root": str(root), "head": git(root, "rev-parse", "HEAD").decode().strip(),
            "status": git(root, "status", "--short").decode("utf-8"), "tracked_frontend_hashes": files}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    entries = baseline_entries()
    protected, migrations, contracts, tests = {}, {}, {}, {}
    mismatches = []
    for name, expected_object in entries.items():
        is_migration = name.startswith("web/backend/migrations/versions/")
        is_contract = name.startswith("docs/web-alpha/architecture/contracts/")
        is_backend_test = name.startswith("web/backend/tests/") and name.endswith(".py")
        if name.startswith("web/backend/") and not is_migration and not is_backend_test:
            continue
        original = git(ROOT, "show", BASE + ":" + name)
        file = ROOT / name
        actual = file.read_bytes() if file.is_file() else None
        if is_backend_test and actual is not None:
            tests[name] = retain_backend_tests(name, original.decode("utf-8-sig"), actual.decode("utf-8-sig"))
            mismatches.extend(tests[name]["errors"])
            # Whole-module AST equality also preserves old helpers, fixtures,
            # global values and parametrization. Only the explicit additive
            # table import/expectation and existing failure-branch target may
            # change. The rollback assertions and SQL failure remain intact.
            continue
        checkout = git(ROOT, "cat-file", "--filters", "--path=" + name, BASE + ":" + name)
        object_id = git(ROOT, "hash-object", "--path=" + name, "--", str(file)).decode().strip() if actual is not None else None
        item = {"baseline_git_object": expected_object, "current_git_object": object_id,
                "baseline_checkout_sha256": sha256(checkout),
                "current_sha256": sha256(actual) if actual is not None else None,
                "raw_bytes_equal": actual == checkout, "git_content_equal": object_id == expected_object}
        protected[name] = item
        if is_migration:
            migrations[name] = item
        if is_contract:
            contracts[name] = item
        if object_id != expected_object or ((is_migration or is_contract) and actual != checkout):
            mismatches.append(name)
    changed = [name.decode("utf-8") for name in git(ROOT, "diff", "--name-only", "-z", BASE).split(b"\0") if name]
    untracked = [name.decode("utf-8") for name in git(ROOT, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if name]
    allowed = ("web/backend/", "docs/web-alpha/pr03a/", "tools/pr03a/")
    scope_violations = [name for name in changed + untracked if not name.startswith(allowed)]
    siblings = [sibling_snapshot(root) for root in SIBLINGS]
    first_path = OUT / "sibling-frontend-before.json"
    if not first_path.exists():
        first_path.write_text(json.dumps(siblings, ensure_ascii=False, indent=2), encoding="utf-8")
    first = json.loads(first_path.read_text("utf-8"))
    report = {"base": BASE, "checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "baseline_files": len(entries), "protected_files_checked": len(protected),
              "prior_migration_files": migrations, "frozen_contract_files": contracts,
              "protected": protected, "backend_test_retention": tests,
              "reviewed_test_evolution": {
                  ADDITIVE_SCHEMA_TEST: "Add identity table import/union; retain all original column/nullability/metadata assertions",
                  LEGACY_ROLLBACK_TEST: "Select ffff_task_failure explicitly because copied 0004 adds another scratch head; retain all SQL failure/DDL/data/version rollback assertions"},
              "mismatches": mismatches, "scope_violations": scope_violations,
              "changed_tracked_files": changed, "new_untracked_files": untracked,
              "sibling_snapshots": siblings, "sibling_snapshot_baseline": str(first_path),
              "siblings_unchanged_since_snapshot": siblings == first,
              "sibling_observation": "Read-only snapshots; sibling dirty state and concurrent work belong to their owning agents. Sibling drift is reported separately and never repaired or overwritten here.",
              "limitations": "Scope/test preservation guard, not independent code review or proof of behavior. Ignored sibling files and private .claude content are not read.",
              "passed": not mismatches and not scope_violations}
    output = OUT / "protected-files.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("base", "baseline_files", "protected_files_checked",
                     "mismatches", "scope_violations", "siblings_unchanged_since_snapshot", "passed")}, indent=2))
    print("Evidence:", output)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
