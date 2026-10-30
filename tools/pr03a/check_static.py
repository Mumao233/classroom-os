"""Validate backend syntax/migrations and the unchanged frozen contract package."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import sys

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "web/backend"
ARCH = ROOT / "docs/web-alpha/architecture"
OUT = ROOT / "outputs/pr03a"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-deps", type=Path, required=True,
                        help="Existing isolated jsonschema/openapi-spec-validator dependencies")
    args = parser.parse_args()
    sys.path.insert(0, str(args.contract_deps.resolve()))
    from jsonschema import Draft202012Validator, FormatChecker
    from openapi_spec_validator import validate
    OUT.mkdir(parents=True, exist_ok=True)
    checks = []

    def run(name, operation):
        try:
            detail = operation()
            checks.append({"name": name, "passed": True, "details": detail})
        except Exception as error:
            checks.append({"name": name, "passed": False, "error": str(error)})

    def read(name):
        return json.loads((ARCH / "contracts" / name).read_text("utf-8"))

    api, events = read("openapi.json"), read("events.schema.json")

    def syntax():
        paths = sorted(set(BACKEND.rglob("*.py")) | set((ROOT / "tools/pr03a").glob("*.py")))
        for path in paths:
            ast.parse(path.read_text("utf-8-sig"), filename=str(path))
        return {"python_files": len(paths)}
    run("Python AST", syntax)

    def api_check():
        validate(api)
        operations = [value for path in api["paths"].values() for method, value in path.items()
                      if method in {"get", "post", "put", "patch", "delete"}]
        ids = [operation["operationId"] for operation in operations]
        assert len(ids) == len(set(ids))
        return {"operations": len(ids), "paths": len(api["paths"])}
    run("OpenAPI 3.1 and unique operationId", api_check)
    run("Events JSON Schema", lambda: Draft202012Validator.check_schema(events))

    def refs():
        count = 0
        for doc in (api, events):
            def walk(node):
                nonlocal count
                if isinstance(node, dict):
                    if "$ref" in node:
                        ref = node["$ref"]
                        assert ref.startswith("#/"), "unexpected nonlocal reference"
                        current = doc
                        for part in ref[2:].split("/"):
                            current = current[part.replace("~1", "/").replace("~0", "~")]
                        count += 1
                    for value in node.values():
                        walk(value)
                elif isinstance(node, list):
                    for value in node:
                        walk(value)
            walk(doc)
        return {"resolved_refs": count}
    run("All contract local refs resolve", refs)

    def examples():
        cases = read("examples.json")["cases"]
        for case in cases:
            doc = read(case["schema_file"])
            schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$ref": case["schema_pointer"],
                      **{key: value for key, value in doc.items() if key in {"components", "$defs"}}}
            actual = Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(case["value"])
            assert actual == case["expected_valid"], case["name"]
        return {"positive_negative_cases": len(cases)}
    run("Frozen positive and negative examples", examples)

    def model():
        domain = read("domain-model.json")
        tables = {table["name"]: table for table in domain["tables"]}
        assert len(tables) == len(domain["tables"])
        foreign_keys = 0
        for table in tables.values():
            columns = {column["name"]: column for column in table["columns"]}
            assert len(columns) == len(table["columns"])
            if table["tenant_scope"] == "owner":
                assert "owner_id" in columns and not columns["owner_id"]["nullable"]
                assert ["owner_id", "id"] in table["unique_keys"] and "FORCE RLS" in table["row_security"]
            for foreign in table["foreign_keys"]:
                target = tables[foreign["references"]["table"]]
                source_columns, target_columns = foreign["columns"], foreign["references"]["columns"]
                assert target_columns in [target["primary_key"], *target["unique_keys"]]
                assert len(source_columns) == len(target_columns)
                target_by_name = {column["name"]: column for column in target["columns"]}
                assert all(columns[left]["type"] == target_by_name[right]["type"]
                           for left, right in zip(source_columns, target_columns))
                if table["tenant_scope"] == target["tenant_scope"] == "owner":
                    assert "owner_id" in source_columns and "owner_id" in target_columns
                foreign_keys += 1
        return {"tables": len(tables), "foreign_keys": foreign_keys}
    run("Frozen model keys and owner composite FKs", model)

    def migrations():
        buffer = io.StringIO()
        config = Config(str(BACKEND / "alembic.ini"), output_buffer=buffer)
        config.set_main_option("script_location", str(BACKEND / "migrations"))
        script = ScriptDirectory.from_config(config)
        heads = script.get_heads()
        assert len(heads) == 1
        assert script.get_revision("0003_persistent_tasks").down_revision == "0002_pr02a_review"
        assert script.get_revision("0002_pr02a_review").down_revision == "0001_data_foundation"
        assert script.get_revision(heads[0]).down_revision == "0003_persistent_tasks"
        command.upgrade(config, "head", sql=True)
        rendered = buffer.getvalue()
        assert "COMMIT;" in rendered and "FORCE ROW LEVEL SECURITY" in rendered
        (OUT / "migration-offline-head.sql").write_text(rendered, encoding="utf-8")
        return {"heads": heads, "history_retained": ["0001_data_foundation", "0002_pr02a_review", "0003_persistent_tasks"],
                "offline_rendered": True}
    run("Incremental single migration head and offline SQL", migrations)

    def limited_scan():
        matches = []
        paths = [*BACKEND.rglob("*.py"), *BACKEND.rglob("*.sql"), *(ROOT / "tools/pr03a").glob("*.py")]
        patterns = {"private key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
                    "AWS key": r"AKIA[0-9A-Z]{16}", "long API key": r"sk-[A-Za-z0-9_-]{32,}"}
        for path in paths:
            value = path.read_text("utf-8-sig")
            for label, pattern in patterns.items():
                if re.search(pattern, value):
                    matches.append({"path": str(path.relative_to(ROOT)), "pattern": label})
        assert not matches, matches
        return {"files": len(paths), "matches": matches, "scope": "Limited credential-shaped pattern scan; not a security audit"}
    run("Limited source credential-pattern scan", limited_scan)
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version,
              "checks": checks, "passed": all(item["passed"] for item in checks),
              "limitations": "Syntax, frozen contract structure and offline SQL only. Runtime concurrency/RLS use separate PostgreSQL integration gates."}
    path = OUT / "static-architecture.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
