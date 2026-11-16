"""Guest-side PR-05a service and real Chromium experiment with durable evidence."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import secrets
import subprocess
import sys
import time
import unittest
from pathlib import Path

from key_service import KeyService
from server import serve
from test_key_service import KeyServiceTests


def source_manifest():
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(__file__).parent.iterdir()) if path.is_file()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--playwright-module", type=Path, required=True)
    parser.add_argument("--browser-path", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Windows" or os.environ.get("PR05A_VMWARE_GUEST") != "1":
        raise SystemExit("Run through guest-run.ps1 in the VMware Windows guest")
    args.run_dir.mkdir(parents=True, exist_ok=False)
    start = time.time()
    report = {
        "package": "PR-05a", "mode": "synthetic-only", "started_utc_epoch": start,
        "environment": {"computer": platform.node(), "platform": platform.platform(),
                        "python": sys.version, "cryptography": importlib.metadata.version("cryptography"),
                        "node": subprocess.check_output([str(args.node), "--version"], text=True).strip()},
        "source_sha256": source_manifest(),
        "formal_identity_integration": "pending coordination",
        "physical_disk_exhaustion": "not exercised; native Chromium quota exhaustion and SQLite full are tested",
        "product_gate": "TBD-03 and security approval remain open",
    }
    KeyServiceTests.work = str(args.run_dir)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(KeyServiceTests)
    with (args.run_dir / "service-tests.log").open("w", encoding="utf-8") as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    report["service_tests"] = {"run": result.testsRun, "failures": len(result.failures),
                               "errors": len(result.errors), "skipped": len(result.skipped), "passed": result.wasSuccessful()}
    print(json.dumps({"service_tests": report["service_tests"]}), flush=True)
    control_token = secrets.token_urlsafe(32)
    service = KeyService(args.run_dir / "browser-keys.sqlite")
    other = KeyService(args.run_dir / "unpinned-keys.sqlite")
    try:
        with serve(service, args.run_dir / "tls", control_token=control_token) as (origin, pin), serve(other, args.run_dir / "unpinned-tls") as (unpinned, _):
            report["origin"] = origin
            environment = {**os.environ, "PR05A_ORIGIN": origin, "PR05A_UNPINNED_ORIGIN": unpinned,
                           "PR05A_SPKI_PIN": pin, "PR05A_CONTROL_TOKEN": control_token,
                           "PR05A_RUN_DIR": str(args.run_dir), "PR05A_PLAYWRIGHT_MODULE": str(args.playwright_module),
                           "PLAYWRIGHT_BROWSERS_PATH": str(args.browser_path)}
            command = [str(args.node), str(Path(__file__).with_name("browser_tests.mjs"))]
            with (args.run_dir / "browser-tests.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(command, env=environment, stdout=log, stderr=subprocess.STDOUT)
                try:
                    code = process.wait(timeout=240)
                except subprocess.TimeoutExpired:
                    # A guest-side timeout is a failure with explicit process cleanup.
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
                    process.wait(timeout=20)
                    code = 124
            report["browser_exit_code"] = code
            browser_file = args.run_dir / "browser-results.json"
            report["browser_tests"] = json.loads(browser_file.read_text(encoding="utf-8")) if browser_file.exists() else {"passed": False, "error": "missing report"}
    except Exception as error:
        report["runner_error"] = type(error).__name__
        report["browser_exit_code"] = 1
        report.setdefault("browser_tests", {"passed": False})
    report["elapsed_seconds"] = round(time.time() - start, 3)
    report["passed"] = result.wasSuccessful() and report["browser_exit_code"] == 0 and report["browser_tests"]["passed"]
    (args.run_dir / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "browser_exit_code": report["browser_exit_code"],
                      "elapsed_seconds": report["elapsed_seconds"]}), flush=True)
    print((args.run_dir / "service-tests.log").read_text(encoding="utf-8"), flush=True)
    browser_log = args.run_dir / "browser-tests.log"
    if browser_log.exists():
        print(browser_log.read_text(encoding="utf-8"), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
