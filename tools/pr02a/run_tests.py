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

from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-url", required=True,
                        help="Loopback, non-default port, database name starting pr02a_ only")
    args = parser.parse_args()
    url = make_url(args.admin_url)
    if (url.drivername != "postgresql+psycopg" or url.host not in {"127.0.0.1", "localhost"}
            or not url.port or url.port == 5432 or not (url.database or "").startswith("pr02a_")
            or url.query):
        parser.error("refusing a non-synthetic PostgreSQL target")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    work = ROOT / "work/pr02a/runs" / stamp
    output = ROOT / "outputs/pr02a" / stamp
    for folder in (work / "tmp", work / "cache", output):
        folder.mkdir(parents=True, exist_ok=False)
    env = {key: value for key, value in os.environ.items()
           if not any(part in key.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "KEY"))
           and key.upper() not in {"PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH",
                                   "PYTHONPYCACHEPREFIX", "CLASSROOM_WEB_DATABASE_URL"}}
    env.update(PR02A_TEST_ADMIN_URL=args.admin_url,
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
