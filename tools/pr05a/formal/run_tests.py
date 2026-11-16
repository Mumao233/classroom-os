"""Owned VMware PostgreSQL run for this change and its direct identity regressions."""

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "nt" or os.environ.get("PR05A_VMWARE_GUEST") != "1":
        raise SystemExit("VMware guest entrypoint required")
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "web/backend"))
    from classroom_web.test_support import SYNTHETIC_PASSWORD, validate_test_database_target
    import psycopg

    args.output.mkdir(parents=True, exist_ok=False)
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith("PG") and key.upper() not in
                   ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH", "PYTHONPYCACHEPREFIX")}
    environment.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    environment["PR05A_REVIEW_OUTPUT"] = str(args.output)
    marker = uuid4().hex[:12]
    admin = "pr02a_admin_" + marker
    data = args.output / "pg-data"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    commands = []

    def execute(command, name, *, cwd=None, timeout=60):
        commands.append([str(value) for value in command])
        with (args.output / (name + ".log")).open("wb") as log:
            process = subprocess.Popen(command, cwd=cwd, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                return process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
                process.wait(timeout=20)
                return 124

    started = False
    test_code = None
    stop_code = None
    try:
        if execute([str(args.pg_bin / "initdb.exe"), "-D", str(data), "-U", admin, "-A", "trust", "--encoding=UTF8", "--locale=C"], "initdb"):
            raise RuntimeError("owned PostgreSQL init failed")
        options = f"-h 127.0.0.1 -p {port} -c log_statement=none -c log_min_error_statement=panic -c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0"
        if execute([str(args.pg_bin / "pg_ctl.exe"), "-D", str(data), "-l", str(args.output / "postgres.log"), "-o", options, "-w", "-t", "20", "start"], "start"):
            raise RuntimeError("owned PostgreSQL start failed")
        started = True
        with psycopg.connect(host="127.0.0.1", port=port, user=admin, password=SYNTHETIC_PASSWORD,
                             dbname="postgres", autocommit=True) as connection:
            connection.execute("CREATE DATABASE pr02a_control")
        url = validate_test_database_target(f"postgresql+psycopg://{admin}@127.0.0.1:{port}/pr02a_control", environ=environment)
        environment["PR02A_TEST_ADMIN_URL"] = url.render_as_string(hide_password=False)
        selected = ["tests/integration/test_cache_keys.py",
                    "tests/integration/test_authentication.py::test_real_login_cookie_context_logout_and_revoked_cookie",
                    "tests/integration/test_authentication.py::test_epoch_revocation_and_deadline_are_current_database_facts",
                    "tests/integration/test_authentication.py::test_runtime_and_auth_logins_cannot_read_secret_tables_or_call_other_service",
                    "tests/integration/test_authentication.py::test_bound_reauth_changes_password_and_revokes_existing_session",
                    "tests/integration/test_authentication.py::test_lists_match_contract_and_no_capture_or_trust_is_enabled"]
        test_code = execute([sys.executable, "-B", "-m", "pytest", *selected, "-q", "-rA",
                             "--basetemp=" + str(args.output / "pytest"), "-o", "cache_dir=" + str(args.output / "pytest-cache"),
                             "--junitxml=" + str(args.output / "junit.xml")], "pytest", cwd=root / "web/backend", timeout=180)
    finally:
        if started:
            stop_code = execute([str(args.pg_bin / "pg_ctl.exe"), "-D", str(data), "-m", "fast", "-w", "-t", "20", "stop"], "stop")
        result = dict(test_exit_code=test_code, owned_postgres_stop_exit_code=stop_code, port=port,
                      synthetic_only=True, commands=commands)
        (args.output / "run.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result), flush=True)
        if (args.output / "pytest.log").exists():
            print((args.output / "pytest.log").read_text(encoding="utf-8", errors="replace"), flush=True)
    return 0 if test_code == 0 and stop_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
