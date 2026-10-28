"""Run the unchanged local-product regression baseline with isolated outputs."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
NODE_TESTS = ("audio", "ai-settings", "input-limits", "study-ui", "study-cache",
              "review-render", "review-ui", "review-mount", "portable-reader")
JS_FILES = ("app.js", "pcm-worklet.js", "study-cache.js", "review-render.js", "review.js", "portable-reader.js")
BASELINE_PYTHON = """ai_api.py ai_settings.py app.py asr.py classroom_features.py codex_bridge.py
course_catalog.py input_limits.py key_store.py portable_package.py portable_routes.py review_schema.py
review_sources.py review_workflow.py session_order.py slide_context.py slide_history.py study_workflow.py
version.py tests/manual_browser.py tests/review_fixture.py tests/test_ai_api.py tests/test_ai_integration.py
tests/test_app.py tests/test_asr.py tests/test_bugfix_adjacent.py tests/test_codex_bridge.py
tests/test_features.py tests/test_http_regression.py tests/test_input_limits.py tests/test_key_store.py
tests/test_portable_package.py tests/test_request_safety_v121.py tests/test_review_bridges.py
tests/test_review_schema.py tests/test_review_workflow.py tests/test_session_order.py tests/test_slide_context.py
tests/test_slide_history.py tests/test_study_edge_cases.py tests/test_study_http.py tests/test_study_workflow.py
tests/test_v121_acceptance.py tests/test_visual_bridges.py""".split()


def junit_result(path):
    """pytest 9 counts subtests in suite totals, not as testcase elements."""
    tree = ET.parse(path)
    suites = list(tree.iter("testsuite"))
    counts = {key: sum(int(suite.get(key, "0")) for suite in suites)
              for key in ("tests", "failures", "errors", "skipped")}
    counts["parent_testcases"] = len(list(tree.iter("testcase")))
    counts["subtests"] = counts["tests"] - counts["parent_testcases"]
    valid = bool(suites) and counts["parent_testcases"] >= 525 and counts["subtests"] >= 171
    valid = valid and not any(counts[key] for key in ("failures", "errors", "skipped"))
    return counts, valid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Python environment containing original test dependencies")
    parser.add_argument("--node", default="node", help="Node executable; no npm install is performed")
    parser.add_argument("--powershell", default="powershell", help="PowerShell executable for parse-only checks")
    parser.add_argument("--output-dir", type=Path, help="New directory under repository work/pr00")
    args = parser.parse_args()
    selected_python = shutil.which(args.python) or args.python
    try:
        same_python = os.path.samefile(selected_python, sys.executable)
    except OSError:
        parser.error("--python must resolve to an existing Python executable")
    if not same_python:
        # Relaunch so dependency metadata and every Python check use one runtime.
        return subprocess.call([selected_python, str(Path(__file__).resolve()), *sys.argv[1:]])
    base = (ROOT / "work" / "pr00").resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = (args.output_dir or base / "runs" / stamp).resolve()
    if base not in output.parents or output.exists():
        parser.error("--output-dir must be a new child directory under repository work/pr00")
    output.mkdir(parents=True)
    for name in ("tmp", "cache", "hf"):
        (output / name).mkdir()

    # Never dump the inherited environment or private values to the report.
    env = {name: value for name, value in os.environ.items()
           if not any(word in name.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "KEY"))
           and name.upper() not in ("CODEX_HOME", "PYTHONPATH", "NODE_OPTIONS", "PYTHONPYCACHEPREFIX",
                                    "PYTEST_ADDOPTS", "PYTEST_PLUGINS")}
    env.update(TEMP=str(output / "tmp"), TMP=str(output / "tmp"), TMPDIR=str(output / "tmp"),
               PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               HF_HOME=str(output / "hf"), HF_HUB_CACHE=str(output / "hf" / "hub"),
               XDG_CACHE_HOME=str(output / "cache"), PIP_CACHE_DIR=str(output / "cache" / "pip"),
               PYTHONNOUSERSITE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               CLASSROOM_PR00_TEST_GUARD="1", CLASSROOM_PR00_REPO_ROOT=str(ROOT))
    python_env = dict(env, PYTHONPATH=str(ROOT / "tools" / "pr00" / "runtime_guard"))
    node_guard = str(ROOT / "tools" / "pr00" / "node_guard.cjs")
    steps = []
    report = {"schema_version": 1, "started_at": datetime.now(timezone.utc).isoformat(),
              "repository": str(ROOT), "output": str(output), "python": sys.version,
              "python_executable": sys.executable, "platform": platform.platform(),
              "baseline_commit": "43fa23c141e3350e90d72ed7768e3b8de7d68337",
              "network_policy": "test children: loopback only; real subprocesses denied; HF offline",
              "scope": "existing synthetic tests; no microphone, model inference, deployment or Web E2E",
              "steps": steps}
    packages = {}
    for name in ("pytest", "fastapi", "uvicorn", "httpx", "faster-whisper", "Pillow"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    report["dependencies"] = packages

    def save():
        (output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def run(name, command, child_env=env, timeout=900):
        began = time.monotonic()
        log = output / (name + ".log")
        print(f"RUN {name}", flush=True)
        try:
            result = subprocess.run(command, cwd=ROOT, env=child_env, capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=timeout)
            code, contents = result.returncode, result.stdout + result.stderr
        except (OSError, subprocess.TimeoutExpired) as error:
            code, contents = 1, f"{type(error).__name__}: {error}"
        log.write_text(contents, encoding="utf-8")
        step = {"name": name, "command": command, "exit_code": code,
                "seconds": round(time.monotonic() - began, 3), "log": log.name}
        steps.append(step)
        save()
        print(f"{'PASS' if code == 0 else 'FAIL'} {name} ({step['seconds']}s)", flush=True)
        if code:
            print(contents[-4000:], flush=True)
        return code

    run("git-head", ["git", "rev-parse", "HEAD"])
    run("git-status", ["git", "status", "--short", "--branch"])
    run("node-version", [args.node, "--version"])
    run("powershell-version", [args.powershell, "-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.ToString()"])
    guards_ok = run("python-guard", [sys.executable, str(ROOT / "tools/pr00/selftest_guards.py")], python_env) == 0
    guards_ok = (run("node-guard", [args.node, "--require", node_guard, "tools/pr00/selftest_guards.cjs"]) == 0) and guards_ok
    run("junit-gate-selftest", [sys.executable, "tools/pr00/selftest_junit.py"], python_env)
    if guards_ok:
        run("pytest", [sys.executable, "-m", "pytest", "tests", "-q",
                       "--basetemp=" + str(output / "tmp" / "pytest"),
                       "--junitxml=" + str(output / "pytest-junit.xml"),
                       "-o", "cache_dir=" + str(output / "cache" / "pytest")], python_env)
        for name in NODE_TESTS:
            run("node-" + name, [args.node, "--require", node_guard, "tests/" + name + ".cjs"])
        try:
            counts, valid = junit_result(output / "pytest-junit.xml")
            report["pytest_junit"] = counts
            steps.append({"name": "pytest-baseline-gate", "exit_code": 0 if valid else 1,
                          "detail": "At least 525 testcase nodes plus 171 subtests; zero failures/errors/skips. Count is not a substitute for test preservation."})
        except (OSError, ET.ParseError, ValueError) as error:
            steps.append({"name": "pytest-baseline-gate", "exit_code": 1, "detail": str(error)})
    else:
        steps.append({"name": "guarded-tests-not-started", "exit_code": 1,
                      "detail": "Refused to run regressions without working guards; this is a failure, not a skipped pass."})

    ast_errors = []
    python_paths = set(ROOT / name for name in BASELINE_PYTHON)
    python_paths.update((ROOT / "tools" / "pr00").rglob("*.py"))
    for path in sorted(python_paths):
        try:
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (OSError, SyntaxError) as error:
            ast_errors.append(str(error))
    steps.append({"name": "python-ast", "exit_code": int(bool(ast_errors)),
                  "baseline_files": len(BASELINE_PYTHON), "total_files": len(python_paths), "errors": ast_errors})
    for name in JS_FILES:
        run("js-syntax-" + name, [args.node, "--check", "static/" + name])
    # Parser::ParseFile inspects syntax only; it never executes launch scripts.
    ps_command = "$failed = $false; foreach ($name in @('setup.ps1','start.ps1','stop.ps1')) { $tokens = $null; $errors = $null; [void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path (Get-Location) $name), [ref]$tokens, [ref]$errors); if ($errors.Count) { $failed = $true; $errors | ForEach-Object { Write-Output ($name + ': ' + $_.Message) } } else { Write-Output ($name + ': parsed') } }; if ($failed) { exit 1 }"
    run("powershell-parse", [args.powershell, "-NoProfile", "-NonInteractive", "-Command", ps_command])
    run("git-diff-check", ["git", "diff", "--check"])
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["passed"] = all(step["exit_code"] == 0 for step in steps)
    save()
    summary = ["# PR-00 check result", "", f"Overall: {'PASS' if report['passed'] else 'FAIL'}", "",
               f"Python: {platform.python_version()} ({sys.executable})", f"Output: {output}", "",
               "| Check | Exit |", "|---|---|"]
    summary.extend(f"| {step['name']} | {step['exit_code']} |" for step in steps)
    summary += ["", "Synthetic/offline checks only. Not a Web E2E, real microphone, ASR quality or deployment acceptance.",
                "See results.json, pytest-junit.xml and individual logs; environment values and secrets are not dumped."]
    (output / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print(f"{'PASS' if report['passed'] else 'FAIL'}: {output / 'summary.md'}", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
