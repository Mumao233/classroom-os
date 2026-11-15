"""Synthetic-only network-control checks. Never calls real sockets or native APIs.

The production module is imported under I/O tripwires. WinSock, psql and CLI
collaborators are replaced with explicit Python fakes. PowerShell coordination
is reviewed separately using an AST-extracted, pure-function harness.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from copy import deepcopy
import ctypes
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROBE = HERE / "net_isolation_probe.py"
PS = HERE / "guest_net_isolation.ps1"
CASES = []
BLOCKED_IO = []
MODULE = None
FIXTURES = None


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def deny(*args, **kwargs):
    BLOCKED_IO.append("forbidden real native/network/process call")
    raise AssertionError("real network/native/process calls are forbidden in this selftest")


def add(name, function):
    assert name not in {label for label, _ in CASES}, name
    CASES.append((name, function))


def expect(value, wanted):
    assert type(value) is bool and value is wanted, {"actual": value, "expected": wanted}


def rejection(function):
    try:
        function()
    except MODULE.ProbeFailure:
        return
    raise AssertionError("invalid operation was not rejected")


def native_record(family=4, protocol="tcp", baseline=False):
    target = MODULE.IPV4_TARGET if family == 4 else MODULE.IPV6_TARGET
    return {"family": family, "protocol": protocol, "operation": "connect" if protocol == "tcp" else "sendto",
            "host": target[0], "port": target[1], "success": baseline, "operation_attempted": True,
            "socket_closed": True, "pending": False, "timed_out": False, "application_bytes_sent": 0,
            "wsa_error": None if baseline else 10051, "error_source": None if baseline else ("connect" if protocol == "tcp" else "sendto"),
            "elapsed_seconds": 0.001}


def client_record(family=4, loopback=False):
    host = ("127.0.0.1" if family == 4 else "::1") if loopback else (MODULE.IPV4_TARGET if family == 4 else MODULE.IPV6_TARGET)[0]
    port = 17654 if loopback else 443
    passfile = "C:/Users/Administrator/PR03bNetworkIsolation/cases/run-" + "a" * 32 + "/isolated-passfile.empty"
    text = ("psql: error: server does not support SSL, but SSL was required\n" if loopback else
            f'psql: error: connection to server at "{host}", port {port} failed: Network is unreachable (0x00002743/10051)\n')
    return {"host": host, "port": port, "passfile": passfile, "argv": MODULE.build_psql_argv(host, port, passfile),
            "pid": 321, "started": True, "exit_code": 2, "timed_out": False, "terminated_by_us": False,
            "cleanup_confirmed": True, "stdin_devnull": True, "shell": False,
            "stdout": "", "stderr": text, "stdout_truncated": False, "stderr_truncated": False,
            "elapsed_seconds": 0.01, "executable_sha256": MODULE.PSQL_SHA256}


def capture_record(family=4):
    return {"family": family, "port": 17654, "accepted": True, "request_hex": MODULE.SSL_REQUEST.hex(),
            "exact_request": True, "reply_hex": "4e", "extra_bytes_hex": "", "peer_closed": True,
            "thread_cleaned": True, "listener_closed": True, "connection_closed": True, "error": None}


def mutation_cases(prefix, predicate, original, changes):
    for name, key, value in changes:
        def execute(key=key, value=value):
            record = deepcopy(original)
            record[key] = value
            expect(predicate(record), False)
        add(prefix + "." + name, execute)


def pure_cases():
    for family in (4, 6):
        for protocol in ("tcp", "udp"):
            record = native_record(family, protocol)
            prefix = f"native.{protocol}{family}"
            add(prefix + ".allowed_immediate_error", lambda r=record: expect(MODULE.native_isolation_ok(r), True))
            for error in sorted(MODULE.ISOLATION_ERRORS[family]):
                add(prefix + f".allowed_{error}", lambda r=record, e=error: expect(MODULE.native_isolation_ok(dict(r, wsa_error=e)), True))
            changes = [("success", "success", True), ("success_integer", "success", 0),
                       ("not_attempted", "operation_attempted", False), ("attempted_integer", "operation_attempted", 1),
                       ("socket_open", "socket_closed", False), ("socket_closed_integer", "socket_closed", 1),
                       ("pending", "pending", True), ("pending_integer", "pending", 0),
                       ("timed_out", "timed_out", True), ("timeout_integer", "timed_out", 0),
                       ("application_byte", "application_bytes_sent", 1), ("application_bool", "application_bytes_sent", False),
                       ("foreign_host", "host", "127.0.0.1"), ("wrong_port", "port", 5432), ("port_bool", "port", True),
                       ("family_bool", "family", True), ("family_unknown", "family", 5),
                       ("wrong_protocol", "protocol", "icmp"), ("wrong_operation", "operation", "recv"),
                       ("error_from_socket", "error_source", "socket"), ("error_from_select", "error_source", "select"),
                       ("error_from_getsockopt", "error_source", "SO_ERROR"), ("late", "elapsed_seconds", 1.000001),
                       ("nan", "elapsed_seconds", float("nan")), ("inf", "elapsed_seconds", float("inf")),
                       ("negative_time", "elapsed_seconds", -0.1), ("bool_time", "elapsed_seconds", False)]
            changes += [(f"reject_error_{e}", "wsa_error", e) for e in (None, True, 0, 10035, 10036, 10037, 10060, 10061, 10054, "10051")]
            if family == 4:
                changes.append(("ipv6_only_error", "wsa_error", 10049))
            mutation_cases(prefix, MODULE.native_isolation_ok, record, changes)
            for key in record:
                def missing(key=key, r=record):
                    changed = dict(r); changed.pop(key)
                    expect(MODULE.native_isolation_ok(changed), False)
                add(prefix + ".missing_" + key, missing)
        record = native_record(family, baseline=True)
        add(f"baseline.tcp{family}.positive", lambda r=record: expect(MODULE.native_baseline_ok(r), True))
        mutation_cases(f"baseline.tcp{family}", MODULE.native_baseline_ok, record,
                       [("not_connected", "success", False), ("native_error", "wsa_error", 10051),
                        ("bytes_sent", "application_bytes_sent", 1), ("late", "elapsed_seconds", 3.250001)])
        for key in record:
            if key == "error_source":
                continue
            def missing(key=key, r=record):
                changed = dict(r); changed.pop(key)
                expect(MODULE.native_baseline_ok(changed), False)
            add(f"baseline.tcp{family}.missing_" + key, missing)

    for family in (4, 6):
        record = client_record(family)
        predicate = lambda r, family=family: MODULE.psql_external_ok(r, family)
        prefix = f"psql.external{family}"
        add(prefix + ".positive", lambda p=predicate, r=record: expect(p(r), True))
        changes = [("not_started", "started", False), ("fake_pid", "pid", 0), ("bool_pid", "pid", True),
                   ("timeout", "timed_out", True), ("killed", "terminated_by_us", True),
                   ("cleanup_unknown", "cleanup_confirmed", False), ("stdin_not_null", "stdin_devnull", False),
                   ("shell", "shell", True), ("stderr_truncated", "stderr_truncated", True),
                   ("stdout_truncated", "stdout_truncated", True), ("wrong_binary", "executable_sha256", "0" * 64),
                   ("foreign_argv", "argv", ["psql.exe", "-c", "SELECT 1"]), ("late", "elapsed_seconds", 5.000001),
                   ("nan", "elapsed_seconds", float("nan")), ("inf", "elapsed_seconds", float("inf"))]
        changes += [("exit_" + str(code), "exit_code", code) for code in (0, 1, 3, -9, 3221225477, True, "2")]
        changes += [("stderr_" + str(i), "stderr", text) for i, text in enumerate((
            "permission denied", "connection to server: Network is unreachable", "connection to server: refused 10061",
            "connection to server: Network is unreachable 10051 timed out", "connection to server: Network is unreachable 10051 password",
            "connection to server: Network is unreachable 10051 certificate", "connection to server: Network is unreachable 10051 10061",
            "connection to server: Network is unreachable 100511", "x" * 8193,
        ))]
        mutation_cases(prefix, predicate, record, changes)
        for key in record:
            if key == "stdout":
                continue
            def missing(key=key, r=record, p=predicate):
                changed = dict(r); changed.pop(key)
                expect(p(changed), False)
            add(prefix + ".missing_" + key, missing)
        add(prefix + ".wrong_family", lambda r=record, f=family: expect(MODULE.psql_external_ok(r, 6 if f == 4 else 4), False))

        client, capture = client_record(family, True), capture_record(family)
        add(f"loopback{family}.positive", lambda r=client, c=capture: expect(MODULE.psql_loopback_ok(r, c), True))
        alterations = {"accepted": False, "request_hex": "0000000804d21630", "exact_request": False,
                       "reply_hex": "53", "extra_bytes_hex": "16", "peer_closed": False,
                       "thread_cleaned": False, "listener_closed": False, "connection_closed": False,
                       "error": "TimeoutError", "port": 17655, "family": True}
        for key, value in alterations.items():
            add(f"loopback{family}.bad_{key}", lambda k=key, v=value, r=client, c=capture: expect(MODULE.psql_loopback_ok(r, dict(c, **{k: v})), False))
        for key in capture:
            def missing(key=key, r=client, c=capture):
                changed = dict(c); changed.pop(key)
                expect(MODULE.psql_loopback_ok(r, changed), False)
            add(f"loopback{family}.missing_{key}", missing)
        for name, raw in (("gss_request", "0000000804d21630"), ("startup_packet", "0000000800030000"),
                          ("tls_record", "1603010003010000"), ("short_request", "00000008"),
                          ("request_plus_byte", MODULE.SSL_REQUEST.hex() + "00")):
            add(f"loopback{family}.{name}", lambda raw=raw, r=client, c=capture: expect(MODULE.psql_loopback_ok(r, dict(c, request_hex=raw)), False))

    identity = {"os_name": "nt", "pointer_bits": 64, "windows_build": "26100", "manufacturer": "VMware, Inc.", "model": "VMware Virtual Platform"}
    add("guest.platform.positive", lambda: expect(MODULE.guest_platform_ok(identity, MODULE.SUPPLEMENT_HOME), True))
    mutation_cases("guest.platform", lambda r: MODULE.guest_platform_ok(r, MODULE.SUPPLEMENT_HOME), identity,
                   [("physical_host", "manufacturer", "ASUSTeK"), ("wrong_model", "model", "Desktop"),
                    ("host_build", "windows_build", "26300"), ("linux", "os_name", "posix"),
                    ("32bit", "pointer_bits", 32), ("bool_bits", "pointer_bits", True)])
    for key in identity:
        def missing(key=key):
            changed = dict(identity); changed.pop(key)
            expect(MODULE.guest_platform_ok(changed, MODULE.SUPPLEMENT_HOME), False)
        add("guest.platform.missing_" + key, missing)
    add("guest.platform.host_source_path", lambda: expect(MODULE.guest_platform_ok(identity, HERE), False))

    for phase in ("preflight", "baseline", "postflight"):
        add("cli.filename." + phase, lambda p=phase: unittest.TestCase().assertEqual(MODULE.result_filename(p), p + "-probe.json"))
        add("cli.forbid_test." + phase, lambda p=phase: rejection(lambda: MODULE.result_filename(p, "tcp4")))
    add("cli.isolated_requires_test", lambda: rejection(lambda: MODULE.result_filename("isolated")))
    for test in MODULE.TESTS:
        add("cli.single_test." + test, lambda t=test: unittest.TestCase().assertEqual(MODULE.result_filename("isolated", t), "isolated-" + t + "-probe.json"))
    for host, port, path in (("example.com", 443, "C:/pass.empty"), ("1.1.1.1", 5432, "C:/pass.empty"),
                             ("1.1.1.1", 444, "C:/pass.empty"), ("::1", 5432, "C:/pass.empty"),
                             ("127.0.0.1", True, "C:/pass.empty"), ("1.1.1.1", 443, "../pass.empty"),
                             ("1.1.1.1", 443, "C:/pass.empty:stream"), ("1.1.1.1", 443, "//host/share/p.empty")):
        add("argv.reject." + str(len(CASES)), lambda h=host, p=port, f=path: rejection(lambda: MODULE.build_psql_argv(h, p, f)))
    add("argv.no_SQL_or_auth_startup", assert_argv)


def assert_argv():
    for host in (MODULE.IPV4_TARGET[0], MODULE.IPV6_TARGET[0], "127.0.0.1", "::1"):
        argv = MODULE.build_psql_argv(host, 443 if host in (MODULE.IPV4_TARGET[0], MODULE.IPV6_TARGET[0]) else 17654, "C:/pass.empty")
        assert argv[1:5] == ["-X", "-w", "-c", "\\quit"] and argv[5] == "--dbname"
        assert "sslmode='require'" in argv[6] and "gssencmode='disable'" in argv[6]
        assert "password=" not in argv[6] and "SELECT" not in " ".join(argv)


class FakeWinsock:
    def __init__(self, error=10051, close_ok=True, pending_ready=False):
        self.error, self.close_ok, self.pending_ready = error, close_ok, pending_ready
        self.calls = []
    def socket(self, *args): self.calls.append("socket"); return 71
    def ioctlsocket(self, *args): self.calls.append("ioctlsocket"); return 0
    def connect(self, *args): self.calls.append("connect"); return 0 if self.error is None else -1
    def sendto(self, *args): self.calls.append("sendto"); return 1 if self.error is None else -1
    def WSAGetLastError(self): self.calls.append("WSAGetLastError"); return self.error
    def closesocket(self, *args): self.calls.append("closesocket"); return 0 if self.close_ok else -1
    def select(self, *args):
        self.calls.append("select")
        assert self.pending_ready, "isolated tests must never wait for async network completion"
        return 1
    def getsockopt(self, handle, level, option, value, size):
        self.calls.append("getsockopt")
        ctypes.cast(value, ctypes.POINTER(ctypes.c_int32))[0] = 0
        ctypes.cast(size, ctypes.POINTER(ctypes.c_int32))[0] = 4
        return 0


def native_flow_cases():
    for family in (4, 6):
        for protocol in ("tcp", "udp"):
            for error in (10051, 10035, 10060, 10061, None):
                def execute(family=family, protocol=protocol, error=error):
                    native = object.__new__(MODULE.NativeWinsock)
                    native.ws, native.socket_type = FakeWinsock(error), ctypes.c_size_t
                    record = native.attempt(family, protocol, allow_pending=False)
                    expect(MODULE.native_isolation_ok(record), error == 10051)
                    assert "select" not in native.ws.calls and native.ws.calls[-1] == "closesocket"
                    if error is not None:
                        operation = "connect" if protocol == "tcp" else "sendto"
                        index = native.ws.calls.index(operation)
                        assert native.ws.calls[index + 1] == "WSAGetLastError"
                add(f"fake_native.{protocol}{family}.{error}", execute)
    def pending_baseline():
        native = object.__new__(MODULE.NativeWinsock)
        native.ws, native.socket_type = FakeWinsock(10035, pending_ready=True), ctypes.c_size_t
        record = native.attempt(4, "tcp", allow_pending=True)
        expect(MODULE.native_baseline_ok(record), True)
        assert "select" in native.ws.calls and "getsockopt" in native.ws.calls
    add("fake_native.baseline_pending_SO_ERROR", pending_baseline)


def cli_cases():
    def host_refused():
        case_fn, runner = Mock(side_effect=AssertionError("case write before guest guard")), Mock(side_effect=deny)
        with patch.object(MODULE, "verify_guest_platform", side_effect=MODULE.ProbeFailure("dedicated_vmware_guest_required")), \
                patch.object(MODULE, "_case_directory", case_fn), patch.object(MODULE, "execute_phase", runner), redirect_stderr(io.StringIO()):
            assert MODULE.main(["--phase", "baseline", "--case", "C:/not-created"]) == 2
        case_fn.assert_not_called(); runner.assert_not_called()
    add("cli.host_refused_before_case_or_network", host_refused)
    def cli_result(phase="preflight", failure=False, pin_drift=False, reuse=False):
        case = FIXTURES / ("cli-" + str(len(list(FIXTURES.iterdir())))); case.mkdir()
        identity = {"synthetic": True}; pins = {"synthetic": True, "manifest": "pinned"}
        verify = Mock(side_effect=[pins, dict(pins, changed=True)] if pin_drift else None, return_value=pins)
        runner = Mock(side_effect=MODULE.ProbeFailure("synthetic_probe_failure") if failure else None, return_value={"passed": True})
        with patch.object(MODULE, "verify_guest_platform", return_value=identity), patch.object(MODULE, "_case_directory", return_value=case), \
                patch.object(MODULE, "_file_sha", return_value="synthetic-helper-pin"), patch.object(MODULE, "verify_original", verify), \
                patch.object(MODULE, "execute_phase", runner), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = MODULE.main(["--phase", phase, "--case", str(case)])
            path = case / (phase + "-probe.json")
            report = json.loads(path.read_text(encoding="utf-8"))
            assert code == (1 if failure or pin_drift else 0)
            assert report["passed"] is (not failure and not pin_drift)
            for key in ("batch2_acceptance", "target_verified", "database_execution_allowed", "postgres_server_started", "SQL_executed", "OS_interface_boundary_verified_by_this_probe"):
                assert report[key] is False
            if reuse:
                raw, calls = path.read_bytes(), runner.call_count
                assert MODULE.main(["--phase", phase, "--case", str(case)]) == 2
                assert path.read_bytes() == raw and runner.call_count == calls
    for phase in ("preflight", "baseline", "postflight"):
        add("cli.synthetic_success." + phase, lambda p=phase: cli_result(p))
    add("cli.failed_probe_is_STOP", lambda: cli_result(failure=True))
    add("cli.changed_original_payload_is_STOP", lambda: cli_result(pin_drift=True))
    add("cli.old_PASS_never_overwritten_or_rerun", lambda: cli_result(reuse=True))


class Case(unittest.TestCase):
    def __init__(self, name, function):
        super().__init__("runTest"); self.label, self.function = name, function
    def id(self): return "synthetic." + self.label
    def shortDescription(self): return self.id()
    def runTest(self): self.function()


def main():
    global MODULE, FIXTURES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.relative_to(ROOT)
    assert not output.exists(), "new output directory required; never overwrite"
    before = {path.relative_to(ROOT).as_posix(): digest(path) for path in (PROBE, PS, Path(__file__))}
    output.mkdir(parents=True, exist_ok=False); FIXTURES = output / "fixtures"; FIXTURES.mkdir()
    began = datetime.now(timezone.utc).isoformat()
    log = io.StringIO()
    with ExitStack() as stack:
        stack.enter_context(patch.object(socket, "socket", side_effect=deny))
        stack.enter_context(patch.object(subprocess, "Popen", side_effect=deny))
        stack.enter_context(patch.object(subprocess, "run", side_effect=deny))
        stack.enter_context(patch.object(ctypes, "WinDLL", side_effect=deny, create=True))
        spec = importlib.util.spec_from_file_location("_synthetic_net_isolation_under_test", PROBE)
        MODULE = importlib.util.module_from_spec(spec); spec.loader.exec_module(MODULE)
        pure_cases(); native_flow_cases(); cli_cases()
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.TestSuite(Case(name, fn) for name, fn in CASES))
    after = {path.relative_to(ROOT).as_posix(): digest(path) for path in (PROBE, PS, Path(__file__))}
    passed = result.wasSuccessful() and not result.skipped and not BLOCKED_IO and before == after
    report = {"scope": "synthetic_network_isolation_checks_only", "passed": passed, "tests": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
              "forbidden_IO_attempts": BLOCKED_IO, "real_network_calls": 0, "real_native_calls": 0,
              "real_child_processes": 0, "real_adapter_operations": 0, "VM_or_VIX_operations": 0,
              "PG_server_or_SQL_executed": False, "batch2_acceptance": False, "database_execution_allowed": False,
              "source_sha256_before": before, "source_sha256_after": after,
              "began_at_utc": began, "completed_at_utc": datetime.now(timezone.utc).isoformat(),
              "cases": [name for name, _ in CASES],
              "failed_cases": [{"id": case.id(), "traceback": trace} for case, trace in result.failures + result.errors],
              "limitations": ["Pure/fake checks do not prove guest network isolation or rollback",
                              "PowerShell pure-function checks and real guest execution are separate evidence",
                              "The production native DLLs, sockets, psql and guest platform verifier were never executed"]}
    with (output / "results.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False); stream.write("\n")
    with (output / "selftest.log").open("x", encoding="utf-8", newline="\n") as stream: stream.write(log.getvalue())
    print(json.dumps({key: report[key] for key in ("scope", "passed", "tests", "failures", "errors", "skipped", "real_network_calls", "real_child_processes", "batch2_acceptance")}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
