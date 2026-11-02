"""Run unchanged Windows/Node gates with only task storage rooted in PR-03a."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = ROOT / "outputs/pr03a/third-round/legacy" / stamp
    output.mkdir(parents=True, exist_ok=False)
    work_base = ROOT / "work/pr03a/third-round/legacy"
    work = work_base / stamp
    original_path = ROOT / "tools/pr00/run_checks.py"
    original_bytes = original_path.read_bytes()
    original = original_bytes.decode("utf-8-sig")
    replacements = {
        "ROOT = Path(__file__).resolve().parents[2]": "ROOT = Path(" + repr(str(ROOT)) + ")",
        'base = (ROOT / "work" / "pr00").resolve()': "base = Path(" + repr(str(work_base)) + ").resolve()",
    }
    adapted = original
    for before, after in replacements.items():
        assert adapted.count(before) == 1, "Original legacy runner layout changed"
        adapted = adapted.replace(before, after, 1)
    source_dir = ROOT / "work/pr03a/third-round/legacy-source"
    source_dir.mkdir(parents=True, exist_ok=True)
    adapted_path = source_dir / (stamp + ".py")
    adapted_path.write_text(adapted, encoding="utf-8")
    sys.argv = [str(adapted_path), "--python", sys.executable, "--output-dir", str(work)]
    namespace: dict[str, Any] = {"__file__": str(adapted_path), "__name__": "third_round_legacy_baseline"}
    exec(compile(adapted, str(adapted_path), "exec"), namespace)
    code = namespace["main"]()
    assert original_path.read_bytes() == original_bytes, "Legacy runner changed during execution"
    copied = {}
    for source in work.iterdir():
        if source.is_file() and source.suffix in {".json", ".log", ".xml", ".md"}:
            target = output / source.name
            shutil.copyfile(source, target)
            copied[source.name] = hashlib.sha256(target.read_bytes()).hexdigest()
    record = {
        "legacy_runner": str(original_path),
        "legacy_runner_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "adapted_runner": str(adapted_path),
        "adapted_runner_sha256": hashlib.sha256(adapted.encode()).hexdigest(),
        "exact_storage_only_replacements": replacements,
        "exit_code": code,
        "python": sys.version,
        "executable": sys.executable,
        "results": str(work / "results.json"),
        "junit": str(work / "pytest-junit.xml"),
        "copied_evidence_sha256": copied,
        "scope": "Unchanged legacy 525+171, nine Node suites, guards, AST and parser gates; only ROOT/storage literals adapted",
    }
    (output / "command.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps({"evidence": str(output / "command.json"), "exit_code": code}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
