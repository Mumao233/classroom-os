"""Run the unchanged full backend gate on a fresh Ubuntu PostgreSQL 17 cluster.

Only synthetic loopback data is used. Existing data directories are refused,
the original target/network guards remain active, and cluster/log evidence is
preserved. This tool installs no software and invokes no paid service.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
from typing import Any
from uuid import uuid4


def run_process(command: list[str], repo: Path, env: dict[str, str], timeout: int) -> tuple[int, str]:
    """Bound the whole Linux subprocess group, including pytest's workers."""
    with subprocess.Popen(command, cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace", start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return process.returncode, stdout + stderr
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate(timeout=5)
            return 124, "Command group exceeded its bounded timeout.\n" + stdout + stderr
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
            raise


def clean_environment(source: dict[str, str]) -> dict[str, str]:
    env = {key: value for key, value in source.items()
           if not key.upper().startswith(("PG", "GIT_"))
           and not any(part in key.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "KEY"))
           and key.upper() not in {"PYTHONPATH", "PYTHONSTARTUP", "PYTHONPYCACHEPREFIX",
                                   "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "CLASSROOM_WEB_DATABASE_URL", "CODEX_HOME"}}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", PYTHONUTF8="1",
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    return env


def cluster_plan(repo: Path, pg_bin: Path, port: int, run_id: str) -> dict[str, Any]:
    if type(port) is not int or not 1024 <= port <= 65535 or port == 5432:
        raise ValueError("Use an explicit non-5432 unprivileged port")
    if not re.fullmatch(r"[a-z0-9_]{1,32}", run_id):
        raise ValueError("Invalid synthetic run identifier")
    repo = repo.resolve(strict=True)
    data = repo / "work/pr03a/pg/data"
    # Neither a symlinked parent nor a pre-existing cluster may be adopted.
    if data.exists() or data.is_symlink():
        raise ValueError("Refusing an existing PostgreSQL data directory")
    if data.resolve() != data:
        raise ValueError("PostgreSQL data path traverses a symlink")
    output = repo / "outputs/pr03a/third-round" / ("overnight_" + run_id)
    if output.exists() or output.resolve() != output:
        raise ValueError("Evidence path must be fresh and must not traverse a symlink")
    identity = "pr02a_overnight_" + run_id
    return {"repo": repo, "pg_bin": pg_bin.resolve(strict=True), "data": data,
            "output": output, "postgres_log": output / "postgres.log",
            "port": port, "login": identity, "database": identity,
            "admin_url": f"postgresql+psycopg://{identity}@127.0.0.1:{port}/{identity}"}


def run_backend(repo: Path, pg_bin: Path, port: int = 55461, *, timeout_seconds: int = 2400) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1, "started_at": datetime.now(timezone.utc).isoformat(),
        "passed": False, "steps": [], "cluster_stopped": False,
        "scope": "Real disposable PostgreSQL; synthetic mail/provider only; no production or paid services",
    }
    if not sys.platform.startswith("linux") or not hasattr(os, "geteuid") or os.geteuid() == 0:
        raise ValueError("This CI entrypoint requires an unprivileged Linux user")
    if not 1 <= timeout_seconds <= 7200:
        raise ValueError("Backend time limit must be between 1 and 7200 seconds")
    run_id = uuid4().hex[:16]
    plan = cluster_plan(repo, pg_bin, port, run_id)
    repo, pg_bin, data = plan["repo"], plan["pg_bin"], plan["data"]
    runner = repo / "tools/pr03a/third_round/run_tests.py"
    if not runner.is_file():
        raise ValueError("The unchanged third-round backend runner is required")
    for name in ("postgres", "initdb", "pg_ctl", "createdb"):
        executable = pg_bin / name
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError("Missing PostgreSQL executable: " + name)
    env = clean_environment(dict(os.environ))
    version = subprocess.run([str(pg_bin / "postgres"), "--version"], env=env,
                             capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    if not re.search(r"\bPostgreSQL\) 17(?:\.|\b)", version):
        raise ValueError("Only installed PostgreSQL 17 binaries are permitted")
    # Detect an occupied port without connecting to any pre-existing database.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", port))
    output = plan["output"]
    output.mkdir(parents=True, exist_ok=False)
    data.parent.mkdir(parents=True, exist_ok=True)
    tmp = output / "tmp"
    tmp.mkdir()
    env.update(TEMP=str(tmp), TMP=str(tmp), TMPDIR=str(tmp))
    report.update(postgresql_version=version, data_directory=str(data), port=port,
                  login=plan["login"], database=plan["database"], postgres_log=str(plan["postgres_log"]))

    def execute(name: str, command: list[str], timeout: int) -> int:
        start = time.monotonic()
        try:
            code, log = run_process(command, repo, env, timeout)
        except subprocess.TimeoutExpired as error:
            code, log = 124, "Command group did not exit after bounded termination.\n"
            for part in (error.stdout, error.stderr):
                if part:
                    log += part.decode("utf-8", errors="replace") if isinstance(part, bytes) else part
        except OSError as error:
            code, log = 1, str(error)
        (output / (name + ".log")).write_text(log, encoding="utf-8")
        report["steps"].append({"name": name, "command": command, "exit_code": code,
                                "seconds": round(time.monotonic() - start, 3), "log": name + ".log"})
        return code

    initialized = start_attempted = False
    try:
        if execute("initdb", [str(pg_bin / "initdb"), "-D", str(data), "-U", plan["login"],
                              "--auth=trust", "--encoding=UTF8", "--no-locale"], 60):
            raise RuntimeError("Synthetic initdb failed")
        initialized = True
        with (data / "postgresql.conf").open("a", encoding="utf-8") as config:
            config.write("\n# Isolated overnight CI only.\nlisten_addresses = '127.0.0.1'\n"
                         f"port = {port}\nunix_socket_directories = ''\n"
                         "logging_collector = off\nlog_destination = 'stderr'\n"
                         "log_line_prefix = '%m [%p] db=%d user=%u app=%a '\n"
                         "log_lock_waits = on\ndeadlock_timeout = '1s'\n")
        start_attempted = True
        if execute("pg-start", [str(pg_bin / "pg_ctl"), "-D", str(data), "-l", str(plan["postgres_log"]),
                                "-w", "-t", "30", "start"], 40):
            raise RuntimeError("Synthetic PostgreSQL start failed")
        if execute("createdb", [str(pg_bin / "createdb"), "--host=127.0.0.1", "--port=" + str(port),
                                "--username=" + plan["login"], "--no-password", plan["database"]], 30):
            raise RuntimeError("Synthetic database creation failed")
        result = execute("backend-full", [sys.executable, str(runner), "--suite", "full",
                                           "--admin-url", plan["admin_url"],
                                           "--postgres-log", str(plan["postgres_log"])], timeout_seconds)
        report["passed"] = result == 0
    except Exception as error:
        report["error"] = str(error)
    finally:
        if initialized and start_attempted and (data / "postmaster.pid").exists():
            if data.resolve() != data or not (data / "PG_VERSION").is_file():
                report["error"] = "Cluster path identity changed; refused unsafe shutdown"
                report["passed"] = False
            else:
                stopped = execute("pg-stop", [str(pg_bin / "pg_ctl"), "-D", str(data),
                                              "-m", "fast", "-w", "-t", "30", "stop"], 40) == 0
                report["cluster_stopped"] = stopped and not (data / "postmaster.pid").exists()
                report["passed"] = report["passed"] and report["cluster_stopped"]
        else:
            report["cluster_stopped"] = True
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (output / "orchestration.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("--port", type=int, default=55461)
    parser.add_argument("--timeout-seconds", type=int, default=2400)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = run_backend(args.repo, args.pg_bin, args.port, timeout_seconds=args.timeout_seconds)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        report = {"schema_version": 1, "passed": False, "error": str(error)}
    data = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(data, encoding="utf-8")
    print(data, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
