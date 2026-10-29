"""Run PG integration tests against an explicit disposable synthetic cluster."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
# Direct CLI execution must import the same repository-owned guard as pytest.
sys.path.insert(0, str(ROOT / "web/backend"))
from classroom_web.test_support import UnsafeTestDatabaseTarget, validate_test_database_target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-url", required=True,
                        help="Literal 127.0.0.1, non-5432 port, pr02a_ database/login; no URL query or PG* environment")
    args = parser.parse_args()
    try:
        url = validate_test_database_target(args.admin_url)
    except UnsafeTestDatabaseTarget as error:
        parser.error(str(error))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    work = ROOT / "work/pr02a/runs" / stamp
    output = ROOT / "outputs/pr02a" / stamp
    for folder in (work / "tmp", work / "cache", output):
        folder.mkdir(parents=True, exist_ok=False)
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("PG")
           and not any(part in key.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "KEY"))
           and key.upper() not in {"PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH",
                                   "PYTHONPYCACHEPREFIX", "CLASSROOM_WEB_DATABASE_URL"}}
    env.update(PR02A_TEST_ADMIN_URL=url.render_as_string(hide_password=False),
               TEMP=str(work / "tmp"), TMP=str(work / "tmp"), TMPDIR=str(work / "tmp"),
               PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", PYTHONUTF8="1",
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    command = [sys.executable, "-m", "pytest", "-q", "--basetemp=" + str(work / "tmp/pytest"),
               "--junitxml=" + str(output / "pytest.xml"), "-o", "cache_dir=" + str(work / "cache")]
    began = time.monotonic()
    result = subprocess.run(command, cwd=ROOT / "web/backend", env=env,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    log = result.stdout + result.stderr
    (output / "pytest.log").write_text(log, encoding="utf-8")
    report = {"python": sys.version, "executable": sys.executable, "command": command,
              "exit_code": result.returncode, "seconds": round(time.monotonic() - began, 3),
              "target": {"host": url.host, "port": url.port, "synthetic_database": url.database},
              "isolation": "fixture creates random pr02a_test database and restricted login, then drops both",
              "limitations": "PostgreSQL data foundation only; no HTTP/frontend/worker/provider or production migration"}
    (output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(log)
    print(f"Report: {output / 'results.json'}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
