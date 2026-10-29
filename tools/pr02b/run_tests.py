"""Run all backend gates in the verified PR-02b synthetic PostgreSQL cluster."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web/backend"))

import sqlalchemy as sa

from classroom_web.test_support import (
    UnsafeTestDatabaseTarget, synthetic_connect_args, validate_test_database_target,
)


NETWORK_GUARD = '''"""Synthetic worker tests may connect only to literal loopback addresses."""
import ipaddress
import sys

def _loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False

def _audit(event, args):
    if event == "socket.connect":
        address = args[1]
        if not isinstance(address, tuple) or not _loopback(address[0]):
            raise PermissionError("PR-02b tests deny non-loopback network connections")
    elif event == "socket.getaddrinfo":
        if not _loopback(args[0]):
            raise PermissionError("PR-02b tests deny DNS/non-loopback resolution")
    elif event == "socket.bind":
        address = args[1]
        if not isinstance(address, tuple) or not _loopback(address[0]):
            raise PermissionError("PR-02b tests may bind only literal loopback addresses")

sys.addaudithook(_audit)
'''


def verify_cluster(url):
    """Reject a different cluster even if its URL happens to look synthetic."""
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool,
                              connect_args=synthetic_connect_args())
    try:
        with engine.connect() as connection:
            identity = dict(connection.execute(sa.text("""
                SELECT current_user AS login, current_database() AS database,
                       current_setting('data_directory') AS data_directory,
                       host(inet_server_addr()) AS host, inet_server_port() AS port,
                       current_setting('listen_addresses') AS listen_addresses,
                       version() AS version
            """)).mappings().one())
    finally:
        engine.dispose()
    expected = ROOT / "work/pr02b/pg/data"
    if (Path(identity["data_directory"]).resolve() != expected.resolve()
            or identity["host"] != "127.0.0.1" or identity["port"] != url.port
            or identity["listen_addresses"] != "127.0.0.1"
            or identity["database"] != url.database or identity["login"] != url.username):
        raise UnsafeTestDatabaseTarget("server identity differs from this worktree's synthetic PR-02b cluster")
    return identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-url", required=True,
                        help="Explicit guarded PR-02b cluster URL; pr02a_ names reuse PR-02a safety validator")
    args = parser.parse_args()
    try:
        url = validate_test_database_target(args.admin_url)
        identity = verify_cluster(url)
    except UnsafeTestDatabaseTarget as error:
        parser.error(str(error))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    work = ROOT / "work/pr02b/runs" / stamp
    output = ROOT / "outputs/pr02b" / stamp
    for folder in (work / "tmp", work / "cache", work / "guard", output):
        folder.mkdir(parents=True, exist_ok=False)
    (work / "guard/sitecustomize.py").write_text(NETWORK_GUARD, encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("PG")
           and not any(part in key.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "KEY"))
           and key.upper() not in {"PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH", "NODE_OPTIONS",
                                   "PYTHONPYCACHEPREFIX", "CLASSROOM_WEB_DATABASE_URL"}}
    env.update(PR02A_TEST_ADMIN_URL=url.render_as_string(hide_password=False),
               TEMP=str(work / "tmp"), TMP=str(work / "tmp"), TMPDIR=str(work / "tmp"),
               PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", PYTHONUTF8="1",
               PYTHONPATH=os.pathsep.join((str(work / "guard"), str(ROOT / "web/backend"))),
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               HF_HOME=str(work / "cache/hf"), XDG_CACHE_HOME=str(work / "cache"),
               PIP_CACHE_DIR=str(work / "cache/pip"))
    # Prove the injected guard before allowing a worker-capable test process.
    probe = subprocess.run([sys.executable, "-c", "import socket; socket.create_connection(('198.51.100.1', 443), timeout=1)"],
                           cwd=ROOT, env=env, capture_output=True, text=True)
    guard_passed = probe.returncode != 0 and "PR-02b tests deny" in probe.stderr
    (output / "network-guard.log").write_text(probe.stdout + probe.stderr, encoding="utf-8")
    if not guard_passed:
        raise RuntimeError("PR-02b loopback network guard self-test failed")
    command = [sys.executable, "-m", "pytest", "-q", "--basetemp=" + str(work / "tmp/pytest"),
               "--junitxml=" + str(output / "pytest.xml"), "-o", "cache_dir=" + str(work / "cache/pytest")]
    began = time.monotonic()
    result = subprocess.run(command, cwd=ROOT / "web/backend", env=env,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    log = result.stdout + result.stderr
    (output / "pytest.log").write_text(log, encoding="utf-8")
    counts = {}
    try:
        suites = list(ET.parse(output / "pytest.xml").iter("testsuite"))
        counts = {key: sum(int(suite.get(key, "0")) for suite in suites)
                  for key in ("tests", "failures", "errors", "skipped")}
        junit_ok = bool(suites) and counts["tests"] > 0 and not any(
            counts[key] for key in ("failures", "errors", "skipped"))
    except (OSError, ET.ParseError, ValueError):
        junit_ok = False
    report = {"python": sys.version, "executable": sys.executable, "command": command,
              "exit_code": result.returncode, "seconds": round(time.monotonic() - began, 3),
              "cluster_identity": identity, "junit": counts,
              "network_guard_selftest": guard_passed,
              "passed": result.returncode == 0 and junit_ok,
              "isolation": "Verified worktree-owned loopback cluster; fixture creates/drops random database and restricted login",
              "limitations": "Synthetic PostgreSQL and simulated provider only; no paid model, HTTP/frontend, deployment or production migration"}
    (output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(log)
    print(f"Report: {output / 'results.json'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
