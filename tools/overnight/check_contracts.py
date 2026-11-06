"""General CI checks derived from PR-01's nine static contract checks.

The historical tools/pr01/validate_architecture.py remains unchanged. Its
PR-01-specific HEAD, original-source-directory and edit-scope checks are not
appropriate to later commits; candidate scope is checked by validation.py.
This entrypoint does not execute application code, perform DB/HTTP validation,
or claim that Mermaid syntax has been checked.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def pointer(document: Any, reference: str) -> Any:
    require(reference.startswith("#/"), "Nonlocal references require explicit review")
    for part in reference[2:].split("/"):
        document = document[part.replace("~1", "/").replace("~0", "~")]
    return document


def check_dag(document: dict[str, Any]) -> dict[str, list[str]]:
    prs = document["prs"]
    by_id = {item["id"]: item for item in prs}
    require(len(prs) == len(by_id) == 28, "Expected all 28 unique work packages")
    orders = {}
    for mode, keys in (("hard", ("depends_on",)),
                       ("all", ("depends_on", "integration_depends_on", "paid_depends_on"))):
        pending = {name: {dependency for key in keys for dependency in item.get(key, [])}
                   for name, item in by_id.items()}
        require(all(dependency in by_id for dependencies in pending.values() for dependency in dependencies),
                "Unknown dependency")
        order = []
        while pending:
            ready = sorted(name for name, dependencies in pending.items() if not dependencies)
            require(bool(ready), "Dependency cycle")
            order.extend(ready)
            for name in ready:
                del pending[name]
            for dependencies in pending.values():
                dependencies.difference_update(ready)
        orders[mode] = order
    require(all("11a" in by_id[name]["depends_on"] for name in ("06b", "07a")),
            "Paid execution must retain its budget dependency")
    require(by_id["09a"]["depends_on"] == ["01"], "PR-09a must retain its independent design dependency")
    return orders


def validate_contracts(root: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1, "at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0], "passed": False, "checks": [],
        "scope": "Static contracts/documentation only; no runtime, DB, paid service, browser or Mermaid validation",
    }
    checks = report["checks"]

    def run(name, function):
        try:
            checks.append({"name": name, "status": "passed", "details": function()})
        except Exception as error:
            checks.append({"name": name, "status": "failed", "error": str(error)})

    try:
        from jsonschema import Draft202012Validator, FormatChecker
        from openapi_spec_validator import validate
        root = root.resolve(strict=True)
        arch = root / "docs/web-alpha/architecture"
        contracts = arch / "contracts"
        api = read_json(contracts / "openapi.json")
        events = read_json(contracts / "events.schema.json")
        report["contract_sha256"] = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(contracts.glob("*.json"))
        }
    except Exception as error:
        checks.append({"name": "Load dependencies and contracts", "status": "failed", "error": str(error)})
        return report

    def api_check():
        validate(api)
        operations = [value for path in api["paths"].values() for method, value in path.items()
                      if method in ("get", "post", "put", "patch", "delete")]
        ids = [operation["operationId"] for operation in operations]
        require(len(ids) == len(set(ids)), "Duplicate operationId")
        return {"operations": len(operations), "paths": len(api["paths"]),
                "schemas": len(api["components"]["schemas"])}

    def event_check():
        Draft202012Validator.check_schema(events)
        return {"event_types": len(events["oneOf"])}

    def references():
        count = 0
        for document in (api, events):
            def walk(value):
                nonlocal count
                if isinstance(value, dict):
                    if "$ref" in value:
                        pointer(document, value["$ref"])
                        count += 1
                    for nested in value.values():
                        walk(nested)
                elif isinstance(value, list):
                    for nested in value:
                        walk(nested)
            walk(document)
        return count

    run("All local JSON refs resolve", references)
    # Reject remote references before validators can attempt resolution.
    if checks[-1]["status"] != "passed":
        return report
    run("OpenAPI3.1 + unique operationId", api_check)
    run("Events JSON Schema 2020-12", event_check)

    def examples():
        results = []
        for case in read_json(contracts / "examples.json")["cases"]:
            schema_path = (contracts / case["schema_file"]).resolve()
            require(contracts.resolve() in schema_path.parents, "Fixture schema escapes contracts directory")
            document = read_json(schema_path)
            schema = {"$schema": "https://json-schema.org/draft/2020-12/schema",
                      "$ref": case["schema_pointer"],
                      **{key: value for key, value in document.items() if key in ("components", "$defs")}}
            actual = Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(case["value"])
            require(actual == case["expected_valid"], "Fixture expectation mismatch: " + case["name"])
            results.append({"name": case["name"], "expected_valid": actual})
        return results

    run("Synthetic positive and negative contract fixtures", examples)

    def transitions():
        data = read_json(contracts / "state-machines.json")
        for name, machine in data["machines"].items():
            states = machine["states"]
            require(len(states) == len(set(states)), "Duplicate state: " + name)
            require(set(states) == set(api["components"]["schemas"]["FlowStates"]["properties"][name]["enum"]),
                    "State enum drift: " + name)
            for transition in machine["transitions"]:
                starts = transition["from"] if isinstance(transition["from"], list) else [transition["from"]]
                require(all(state in states for state in starts) and transition["to"] in states,
                        "Unknown state: " + name)
        for case in data["cases"]:
            matches = [transition for transition in data["machines"][case["machine"]]["transitions"]
                       if case["from"] in (transition["from"] if isinstance(transition["from"], list) else [transition["from"]])
                       and transition["to"] == case["to"] and transition["event"] == case["event"]]
            allowed = any(set(transition["requires"]) <= set(case["facts"]) for transition in matches)
            require(allowed == case["allowed"], "Guard expectation mismatch: " + case["name"])
        return {"machines": len(data["machines"]), "guard_cases": len(data["cases"]),
                "scope": "Design guards only; runtime race/transaction behavior not tested"}

    run("Five workflow transition guards and negative traces", transitions)

    def model():
        data = read_json(contracts / "domain-model.json")
        tables = {table["name"]: table for table in data["tables"]}
        require(len(tables) == len(data["tables"]), "Duplicate table")
        foreign_keys = 0
        for table in tables.values():
            columns = {column["name"]: column for column in table["columns"]}
            require(len(columns) == len(table["columns"]), "Duplicate column: " + table["name"])
            require(all(column["type"] and isinstance(column["nullable"], bool) for column in columns.values()),
                    "Invalid column type/nullability")
            for key in [table["primary_key"], *table["unique_keys"], *table["indexes"]]:
                require(all(column in columns for column in key), "Unknown indexed/key column")
            for foreign in table["foreign_keys"]:
                target = tables[foreign["references"]["table"]]
                remote, local = foreign["references"]["columns"], foreign["columns"]
                target_columns = {column["name"]: column for column in target["columns"]}
                require(len(local) == len(remote) and all(column in columns for column in local), "Invalid FK columns")
                require(all(column in target_columns for column in remote), "Unknown FK target column")
                require(remote in [target["primary_key"], *target["unique_keys"]], "FK target is not unique")
                require(all(columns[left]["type"] == target_columns[right]["type"] for left, right in zip(local, remote)),
                        "FK type mismatch")
                if table["tenant_scope"] == target["tenant_scope"] == "owner":
                    require("owner_id" in local and "owner_id" in remote, "Unscoped private FK")
                foreign_keys += 1
            if table["tenant_scope"] == "owner":
                require("owner_id" in columns and ["owner_id", "id"] in table["unique_keys"], "Missing owner key")
                require(not columns["owner_id"]["nullable"] and "FORCE RLS" in table["row_security"],
                        "Missing owner/RLS protection")
        return {"tables": len(tables), "columns": sum(len(table["columns"]) for table in tables.values()),
                "foreign_keys": foreign_keys, "scope": "Structural model only; SQL/RLS not executed"}

    run("Domain model keys, FK targets/types and owner scope", model)
    run("28 PR DAG hard + integration + paid dependencies", lambda: check_dag(read_json(contracts / "pr-dependencies.json")))

    def traceability():
        source = (root / "docs/web-alpha/REQUIREMENTS_MATRIX.md").read_text(encoding="utf-8")
        ids = set(re.findall(r"^\| ((?:[A-Z]+(?:-[A-Z]+)*-\d{2}|A(?:[1-9]|10))) \|", source, re.M))
        rows = read_json(contracts / "requirements-traceability.json")["requirements"]
        require(len(rows) == len(ids) == 118 and {row["requirement_id"] for row in rows} == ids,
                "Requirement coverage mismatch")
        require(all((arch / row["design"]).is_file() for row in rows), "Missing requirement design document")
        return {"requirements": 118}

    run("118 requirement IDs fully mapped", traceability)

    def documents():
        files = [*arch.rglob("*.md"), root / "AGENTS.md", root / "CLAUDE.md"]
        links = diagrams = 0
        for path in files:
            source = path.read_text(encoding="utf-8")
            require(len(re.findall(r"^```", source, re.M)) % 2 == 0, "Unbalanced Markdown fences: " + path.name)
            require(not any(line.rstrip() != line for line in source.splitlines()), "Trailing whitespace: " + path.name)
            diagrams += source.count("```mermaid")
            for link in re.findall(r"\]\(([^)]+)\)", source):
                link = link.strip("<>").split("#")[0]
                if not link or re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", link):
                    continue
                require((path.parent / link).resolve().exists(), "Broken local link: " + path.name + ": " + link)
                links += 1
        return {"markdown_files": len(files), "local_links": links, "mermaid_blocks_not_parsed": diagrams}

    run("Markdown local links, fences and whitespace", documents)
    report["passed"] = bool(checks) and all(check["status"] == "passed" for check in checks)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--deps", type=Path, help="Optional explicitly isolated validation dependencies")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.deps:
        sys.path.insert(0, str(args.deps.resolve()))
    report = validate_contracts(args.repo)
    data = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(data, encoding="utf-8")
    print(data, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
