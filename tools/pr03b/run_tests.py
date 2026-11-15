"""S3 batch 1: guard selftests and plan inspection only, never DB execution."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import target_guard as guard  # noqa: E402

_AUDIT_INSTALLED = False
_DENIED_EVENTS: list[str] = []


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default unknown-argument/choice errors echo caller values.
        self.print_usage(sys.stderr)
        self.exit(2, "PR-03b guard rejected the command; no database action was authorized\n")


def install_selftest_io_guard() -> None:
    """Fail before socket/DNS/child execution; no OS/native libpq claim."""
    global _AUDIT_INSTALLED
    if _AUDIT_INSTALLED:
        return

    def deny(event, args):
        if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.sendto",
                     "subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn"}:
            _DENIED_EVENTS.append(event)
            raise PermissionError("PR-03b selftests forbid network, DNS and child execution")

    sys.addaudithook(deny)
    _AUDIT_INSTALLED = True


def _source_hashes() -> dict[str, str]:
    paths = [ROOT/"tools/pr03b"/name for name in ("target_guard.py", "run_tests.py", "selftest_runner.py")]
    paths.append(ROOT/"web/backend/classroom_web/test_support.py")
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def _selftest(output: Path) -> int:
    guard.check_environment(os.environ)
    if sys.version_info[:2] != (3, 12):
        guard._reject("selftests require the existing Python 3.12 environment")
    output = guard.contained_path(str(output), ROOT/"outputs/pr03b")
    if output.exists():
        guard._reject("output directory must be new")
    before = _source_hashes()
    install_selftest_io_guard()
    # Only this invocation's synthetic files; no DB data directory is created.
    work = ROOT/"work/pr03b/selftests"/uuid.uuid4().hex
    guard.no_reparse(work)
    work.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=False)
    os.environ.update(TEMP=str(work), TMP=str(work), TMPDIR=str(work))
    import selftest_runner
    report, log = selftest_runner.run(work)
    after = _source_hashes()
    report.update(time_utc=datetime.now(timezone.utc).isoformat(), executable=sys.executable,
                  python=sys.version, command=sys.argv, cwd=str(ROOT), work=str(work),
                  evidence_level="synthetic_guard_selftest", database_connections=0,
                  migrations_executed=False, authentication_mechanisms_executed=False,
                  target_verified=False, database_execution_allowed=False,
                  source_sha256_before=before, source_sha256_after=after,
                  sources_unchanged=before == after, denied_audit_events=sorted(set(_DENIED_EVENTS)),
                  limitations="Injected ownership/server observations are synthetic; no live PostgreSQL handshake or OS-level network isolation was verified.")
    report["passed"] = (report["tests"] > 0 and not any(report[k] for k in ("failures", "errors", "skipped")) and before == after)
    (output/"selftest.log").write_text(log, encoding="utf-8", newline="\n")
    (output/"results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: report[k] for k in ("passed", "tests", "failures", "errors", "skipped", "target_verified", "database_execution_allowed")}))
    return 0 if report["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = SafeArgumentParser(description=__doc__, prog="pr03b-guard")
    sub = parser.add_subparsers(dest="mode", required=True, parser_class=SafeArgumentParser)
    selftest = sub.add_parser("selftest", help="Only synthetic guard checks; no database")
    selftest.add_argument("--output-dir", required=True, type=Path)
    plan = sub.add_parser("validate-plan", help="Read a run-owned plan; does not verify a live target")
    plan.add_argument("--plan", required=True, type=Path)
    plan.add_argument("--admin-url", required=True, help="Synthetic target only; never supply real credentials")
    args = parser.parse_args(argv)
    try:
        guard.check_environment(os.environ)
        if args.mode == "selftest":
            return _selftest(args.output_dir)
        # Reject unsafe URLs/environment before even opening the plan file.
        target = guard.validate_test_database_target(args.admin_url, os.environ)
        if target.password != guard.SYNTHETIC_PASSWORD:
            guard._reject("only the fixed synthetic credential is accepted")
        manifest = guard.load_plan(args.plan, ROOT)
        checked = guard.validate_plan(args.admin_url, manifest, ROOT, os.environ)
        print(json.dumps(checked.public_summary()))
        return 0
    except (guard.UnsafeTestDatabaseTarget, OSError):
        # No caller URL, environment value, private path or traceback is emitted.
        parser.error("guard rejected the synthetic input; no database action was authorized")


if __name__ == "__main__":
    raise SystemExit(main())
