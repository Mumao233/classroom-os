"""Guest-only network controls beside immutable kit03; importing does no probing.

This supplement never invokes guest.main, a PostgreSQL server, SQL, selftests or
the PG launcher. A PowerShell coordinator must independently attest the OS
interface boundary immediately before and after every isolated single test.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import importlib.util
import ipaddress
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import socket
import stat
import struct
import subprocess
import threading
import time

KIT_BASE = Path("C:/Users/Administrator/PR03bLabBasic")
SUPPLEMENT_HOME = Path("C:/Users/Administrator/PR03bNetworkIsolation")
RUNTIME_RELATIVE = "checkout/work/pr03b/toolchains/pg17.11-win64-7409a40dc2bda548/runtime/Library/bin"
PSQL_EXE = KIT_BASE / RUNTIME_RELATIVE / "psql.exe"
MANIFEST_SHA256 = "4c34bea1b0dcb4f5f10c158e50461da397fc369e8f3825f5f34f5e4309f43074"
GUEST_SHA256 = "f7bd89b74e9f3ed396d1bba672848778d8e2ff3b92a2c13a7ae91b2e2ab9dd2f"
PYTHON_SHA256 = "3c48fbdcc2ed5c55290e39bef50bd6b1da76f43f3069a4a2624d0b8876a605f5"
PYTHON_DLL_SHA256 = "69b91d62f73a21a0aac361c1ffe2e970bf5762756073e8e2acd36e6e2f516133"
PSQL_SHA256 = "24e6b46960371c7586aaa7a0475709b3a8b8d59ce833fa965aad05c47bcd93e7"
LIBPQ_SHA256 = "3fc0a20a9978fc9c1433e4dca7be502538ab2a215a9e8c33b8d5cbea418546f4"
IPV4_TARGET = ("1.1.1.1", 443)
IPV6_TARGET = ("2606:4700:4700::1111", 443)
SSL_REQUEST = bytes.fromhex("0000000804d2162f")
ISOLATION_ERRORS = {4: frozenset((10050, 10051, 10065)), 6: frozenset((10049, 10050, 10051, 10065))}
TESTS = ("tcp4", "tcp6", "udp4", "udp6", "psql4", "psql6", "loop4", "loop6")
MAX_CAPTURE = 8192
PSQL_TIMEOUT = 5.0
IMMEDIATE_SECONDS = 1.0


class ProbeFailure(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise ProbeFailure(code)


def _finite(value, maximum):
    return type(value) in (int, float) and 0 <= value <= maximum and math.isfinite(value)


def _native_shape(record):
    try:
        family, protocol = record["family"], record["protocol"]
        expected = IPV4_TARGET if family == 4 else IPV6_TARGET
        return (type(family) is int and family in (4, 6) and protocol in ("tcp", "udp")
            and record["operation"] == ("connect" if protocol == "tcp" else "sendto")
            and record["host"] == expected[0] and type(record["port"]) is int and record["port"] == 443
            and type(record["success"]) is bool and record["operation_attempted"] is True
            and record["socket_closed"] is True and type(record["pending"]) is bool
            and record["timed_out"] is False and type(record["application_bytes_sent"]) is int
            and record["application_bytes_sent"] == 0)
    except (KeyError, TypeError):
        return False


def native_baseline_ok(record):
    """Pure predicate: actual TCP connection, no application bytes sent."""
    try:
        return (_native_shape(record) and record["protocol"] == "tcp" and record["success"] is True
            and record["wsa_error"] is None and _finite(record["elapsed_seconds"], 3.25))
    except (KeyError, TypeError):
        return False


def native_isolation_ok(record):
    """Pure predicate: connect/sendto itself immediately returned allowed error."""
    try:
        return (_native_shape(record) and record["success"] is False and record["pending"] is False
            and type(record["wsa_error"]) is int and record["wsa_error"] in ISOLATION_ERRORS[record["family"]]
            and record["error_source"] == record["operation"]
            and _finite(record["elapsed_seconds"], IMMEDIATE_SECONDS))
    except (KeyError, TypeError):
        return False


def _absolute_windows(value):
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        return False
    path = PureWindowsPath(value)
    return (bool(re.fullmatch(r"[A-Za-z]:", path.drive)) and path.root == "\\"
        and ":" not in value[2:] and all(part not in (".", "..") for part in value.replace("\\", "/").split("/")[1:]))


def _connvalue(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def build_psql_argv(host, port, passfile):
    """Pure, fixed-target argv. The sole command is the psql meta-command quit."""
    if (host not in (IPV4_TARGET[0], IPV6_TARGET[0], "127.0.0.1", "::1")
            or type(port) is not int or not 1 <= port <= 65535 or port == 5432
            or (host in (IPV4_TARGET[0], IPV6_TARGET[0]) and port != 443)
            or not _absolute_windows(str(passfile))):
        _fail("invalid_fixed_psql_target")
    fields = {"hostaddr": host, "host": host, "port": str(port), "user": "pr03b_network_probe",
        "dbname": "pr03b_network_probe", "connect_timeout": "3", "sslmode": "require",
        "gssencmode": "disable", "passfile": str(passfile)}
    conninfo = " ".join(key + "=" + _connvalue(value) for key, value in fields.items())
    return [str(PSQL_EXE), "-X", "-w", "-c", "\\quit", "--dbname", conninfo]


def _psql_shape(record):
    try:
        return (record["started"] is True and type(record["pid"]) is int and record["pid"] > 0
            and type(record["exit_code"]) is int and record["exit_code"] == 2
            and record["timed_out"] is False and record["terminated_by_us"] is False
            and record["cleanup_confirmed"] is True and record["stdin_devnull"] is True and record["shell"] is False
            and record["stderr_truncated"] is False and record["stdout_truncated"] is False
            and isinstance(record["stderr"], str) and len(record["stderr"].encode("utf-8")) <= MAX_CAPTURE
            and record["executable_sha256"] == PSQL_SHA256
            and record["argv"] == build_psql_argv(record["host"], record["port"], record["passfile"])
            and _finite(record["elapsed_seconds"], PSQL_TIMEOUT))
    except (KeyError, TypeError, ValueError, ProbeFailure):
        return False


def psql_external_ok(record, family):
    """Pure predicate: numeric network-unreachable error, never generic failure."""
    if type(family) is not int or family not in ISOLATION_ERRORS or not _psql_shape(record):
        return False
    expected = IPV4_TARGET if family == 4 else IPV6_TARGET
    if (record["host"], record["port"]) != expected:
        return False
    error = record["stderr"].casefold()
    forbidden = ("timed out", "timeout", "refused", "10060", "10061", "does not support ssl",
                 "protocol", "password", "authentication", "certificate", "could not translate host")
    if any(word in error for word in forbidden) or "connection to server" not in error:
        return False
    numbers = {int(value) for value in re.findall(r"(?<![0-9])(100[0-9]{2})(?![0-9])", error)}
    return bool(numbers) and numbers <= ISOLATION_ERRORS[family] and any(word in error for word in (
        "network is unreachable", "network is down", "no route to host", "not available", "dead network",
        "unreachable host", "host is unreachable", "cannot assign requested address", "can't assign requested address",
        "requested address is not valid"))


def psql_loopback_ok(record, capture):
    """Pure predicate: only the exact SSLRequest and N reply, followed by EOF."""
    try:
        family = capture["family"]
        return (_psql_shape(record) and type(family) is int and family in (4, 6)
            and record["host"] == ("127.0.0.1" if family == 4 else "::1")
            and type(capture["port"]) is int and record["port"] == capture["port"]
            and capture["accepted"] is True and capture["request_hex"] == SSL_REQUEST.hex()
            and capture["exact_request"] is True and capture["reply_hex"] == "4e"
            and capture["extra_bytes_hex"] == "" and capture["peer_closed"] is True
            and capture["thread_cleaned"] is True and capture["listener_closed"] is True
            and capture["connection_closed"] is True and capture["error"] is None
            and "server does not support ssl, but ssl was required" in record["stderr"].casefold())
    except (KeyError, TypeError):
        return False


def result_filename(phase, test=None):
    if phase not in ("preflight", "baseline", "isolated", "postflight"):
        _fail("invalid_phase")
    if phase == "isolated":
        if test not in TESTS:
            _fail("isolated_test_required")
        return "isolated-" + test + "-probe.json"
    if test is not None:
        _fail("test_only_allowed_for_isolated")
    return phase + "-probe.json"


def _ordinary(path):
    for ancestor in (*reversed(path.parents), path):
        if ancestor.exists():
            info = ancestor.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                _fail("reparse_path_refused")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _fail("ordinary_single_link_file_required")


def _file_sha(path):
    _ordinary(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def guest_platform_ok(identity, supplement_directory):
    """Pure host-refusal predicate; registry values alone are not VM attestation."""
    try:
        return (identity["os_name"] == "nt" and type(identity["pointer_bits"]) is int and identity["pointer_bits"] == 64
            and identity["windows_build"] == "26100" and isinstance(identity["manufacturer"], str)
            and isinstance(identity["model"], str) and identity["manufacturer"].casefold().startswith("vmware")
            and identity["model"].casefold().startswith("vmware")
            and Path(supplement_directory) == SUPPLEMENT_HOME)
    except (KeyError, TypeError, ValueError):
        return False


def verify_guest_platform():
    """Read only current guest BIOS/OS metadata before any case write/probe."""
    if os.name != "nt" or struct.calcsize("P") != 8:
        _fail("dedicated_vmware_guest_required")
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS", 0, winreg.KEY_READ) as key:
            manufacturer = winreg.QueryValueEx(key, "SystemManufacturer")[0]
            model = winreg.QueryValueEx(key, "SystemProductName")[0]
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", 0, winreg.KEY_READ) as key:
            build = winreg.QueryValueEx(key, "CurrentBuildNumber")[0]
    except Exception:
        _fail("guest_platform_metadata_unavailable")
    identity = {"os_name": os.name, "pointer_bits": 8 * struct.calcsize("P"), "manufacturer": manufacturer,
                "model": model, "windows_build": build}
    if not guest_platform_ok(identity, Path(__file__).resolve().parent):
        _fail("dedicated_vmware_guest_or_supplement_path_mismatch")
    return identity


def verify_original():
    """Read-only import of the exact old wrapper; call verify(), never main()."""
    if os.name != "nt" or struct.calcsize("P") != 8 or sys.version_info[:3] != (3, 12, 14):
        _fail("private_windows_x64_python_31214_required")
    if sys.flags.isolated != 1 or sys.flags.no_site != 1:
        _fail("isolated_no_site_python_flags_required")
    if Path(sys.executable).resolve() != (KIT_BASE / "python/python.exe").resolve() or Path(sys.base_prefix).resolve() != (KIT_BASE / "python").resolve():
        _fail("exact_private_base_python_required")
    pins = {"PAYLOAD-MANIFEST.json": MANIFEST_SHA256, "guest.py": GUEST_SHA256,
        "python/python.exe": PYTHON_SHA256, "python/python312.dll": PYTHON_DLL_SHA256,
        RUNTIME_RELATIVE + "/psql.exe": PSQL_SHA256, RUNTIME_RELATIVE + "/libpq.dll": LIBPQ_SHA256}
    for name, expected in pins.items():
        if _file_sha(KIT_BASE / name) != expected:
            _fail("original_component_pin_mismatch")
    spec = importlib.util.spec_from_file_location("_pr03b_original_guest_verify_only", KIT_BASE / "guest.py")
    if spec is None or spec.loader is None:
        _fail("original_verify_loader_unavailable")
    guest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guest)
    try:
        result = guest.verify()
    except Exception:
        _fail("original_readonly_verify_failed")
    if result.get("manifest_sha256") != MANIFEST_SHA256 or result.get("verified_files") != 4513 or len(result.get("frozen_sources", {})) != 9:
        _fail("original_payload_coverage_mismatch")
    return {"manifest_sha256": MANIFEST_SHA256, "verified_files": 4513, "frozen_sources": result["frozen_sources"],
        "original_component_pins": pins, "python": sys.version, "executable": sys.executable,
        "base_prefix": sys.base_prefix, "original_verify_only": True}


class NativeWinsock:
    """Direct native sockets; isolated calls never wait for pending completion."""
    def __init__(self):
        if os.name != "nt" or struct.calcsize("P") != 8:
            _fail("native_windows_x64_required")
        self.ws = ctypes.WinDLL("ws2_32.dll", use_last_error=True)
        u32, integer, pointer = ctypes.c_uint32, ctypes.c_int32, ctypes.c_void_p
        self.socket_type = ctypes.c_size_t
        bindings = {
            "WSAStartup": ([ctypes.c_uint16, pointer], integer), "WSACleanup": ([], integer),
            "WSAGetLastError": ([], integer), "socket": ([integer, integer, integer], self.socket_type),
            "closesocket": ([self.socket_type], integer), "ioctlsocket": ([self.socket_type, u32, ctypes.POINTER(u32)], integer),
            "connect": ([self.socket_type, pointer, integer], integer),
            "sendto": ([self.socket_type, pointer, integer, integer, pointer, integer], integer),
            "select": ([integer, pointer, pointer, pointer, pointer], integer),
            "getsockopt": ([self.socket_type, integer, integer, pointer, ctypes.POINTER(integer)], integer),
        }
        for name, (arguments, result) in bindings.items():
            function = getattr(self.ws, name)
            function.argtypes, function.restype = arguments, result
        data = ctypes.create_string_buffer(512)
        if self.ws.WSAStartup(0x0202, data) != 0 or data.raw[:2] != b"\x02\x02":
            _fail("winsock_startup_failed")
        self.started = True

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        if self.started:
            self.started = False
            if self.ws.WSACleanup() != 0:
                _fail("winsock_cleanup_failed")

    def attempt(self, family, protocol, *, allow_pending=False):
        if type(family) is not int or family not in (4, 6) or protocol not in ("tcp", "udp"):
            _fail("invalid_native_fixed_target")
        host, port = IPV4_TARGET if family == 4 else IPV6_TARGET
        af = 2 if family == 4 else 23
        raw_address = (struct.pack("<H", af) + port.to_bytes(2, "big") + ipaddress.ip_address(host).packed + b"\0" * 8
            if family == 4 else struct.pack("<H", af) + port.to_bytes(2, "big") + b"\0" * 4 + ipaddress.ip_address(host).packed + b"\0" * 4)
        address = ctypes.create_string_buffer(raw_address, len(raw_address))
        record = {"family": family, "protocol": protocol, "operation": "connect" if protocol == "tcp" else "sendto",
            "host": host, "port": port, "success": False, "wsa_error": None, "error_source": None,
            "operation_attempted": False, "socket_closed": False, "pending": False, "timed_out": False,
            "application_bytes_sent": 0, "application_payload": "none" if protocol == "tcp" else "one fixed zero byte"}
        began = time.monotonic()
        handle = self.ws.socket(af, 1 if protocol == "tcp" else 2, 6 if protocol == "tcp" else 17)
        invalid = (1 << (8 * ctypes.sizeof(self.socket_type))) - 1
        if handle == invalid:
            record.update(wsa_error=self.ws.WSAGetLastError(), error_source="socket", socket_closed=True,
                          elapsed_seconds=round(time.monotonic() - began, 6))
            return record
        try:
            nonblocking = ctypes.c_uint32(1)
            if self.ws.ioctlsocket(handle, 0x8004667E, ctypes.byref(nonblocking)) != 0:
                record.update(wsa_error=self.ws.WSAGetLastError(), error_source="ioctlsocket")
                return record
            record["operation_attempted"] = True
            if protocol == "udp":
                payload = ctypes.create_string_buffer(b"\0", 1)
                status = self.ws.sendto(handle, payload, 1, 0, address, len(raw_address))
                record["success"] = status == 1
                record["application_bytes_sent"] = max(0, status)
            else:
                status = self.ws.connect(handle, address, len(raw_address))
                record["success"] = status == 0
            if status == -1:
                record.update(wsa_error=self.ws.WSAGetLastError(), error_source=record["operation"])
                if protocol == "tcp" and record["wsa_error"] in (10035, 10036, 10037):
                    record["pending"] = True
                    if allow_pending:
                        class FdSet(ctypes.Structure):
                            _fields_ = [("count", ctypes.c_uint32), ("items", ctypes.c_size_t * 64)]
                        class Timeval(ctypes.Structure):
                            _fields_ = [("seconds", ctypes.c_int32), ("microseconds", ctypes.c_int32)]
                        writable, errors = FdSet(), FdSet()
                        writable.count = errors.count = 1
                        writable.items[0] = errors.items[0] = handle
                        budget = max(0.0, 3.0 - (time.monotonic() - began))
                        timeout = Timeval(int(budget), int((budget % 1) * 1000000))
                        ready = self.ws.select(0, None, ctypes.byref(writable), ctypes.byref(errors), ctypes.byref(timeout))
                        if ready == 0:
                            record.update(timed_out=True, error_source="select")
                        elif ready < 0:
                            record.update(wsa_error=self.ws.WSAGetLastError(), error_source="select")
                        else:
                            error, length = ctypes.c_int32(), ctypes.c_int32(4)
                            if self.ws.getsockopt(handle, 0xFFFF, 0x1007, ctypes.byref(error), ctypes.byref(length)) != 0 or length.value != 4:
                                record.update(wsa_error=self.ws.WSAGetLastError(), error_source="getsockopt")
                            else:
                                record.update(success=error.value == 0, wsa_error=error.value or None, error_source="SO_ERROR")
            return record
        finally:
            record["socket_closed"] = self.ws.closesocket(handle) == 0
            record["elapsed_seconds"] = round(time.monotonic() - began, 6)


def _passfile(case, test):
    path = case / ("isolated-" + test + "-passfile.empty")
    with path.open("xb"):
        pass
    _ordinary(path)
    if path.stat().st_size != 0:
        _fail("empty_passfile_required")
    return path


def run_psql(host, port, case, passfile):
    """The only subprocess entry: pinned psql, fixed quit, five-second timeout."""
    if _file_sha(PSQL_EXE) != PSQL_SHA256 or _file_sha(PSQL_EXE.parent / "libpq.dll") != LIBPQ_SHA256:
        _fail("psql_component_pin_mismatch")
    _ordinary(passfile)
    if passfile.parent != case or passfile.stat().st_size != 0:
        _fail("case_owned_empty_passfile_required")
    argv = build_psql_argv(host, port, passfile)
    system = Path(os.environ.get("SystemRoot", "C:/Windows"))
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PROCESSOR_ARCHITECTURE"}}
    env.update(PATH=str(PSQL_EXE.parent) + os.pathsep + str(system / "System32"),
        TEMP=str(case), TMP=str(case), TMPDIR=str(case), LC_ALL="C", LANG="C")
    record = {"host": host, "port": port, "passfile": str(passfile), "argv": argv, "pid": None,
        "executable_sha256": PSQL_SHA256, "started": False, "exit_code": None, "timed_out": False,
        "terminated_by_us": False, "cleanup_confirmed": False, "stdin_devnull": True, "shell": False,
        "stdout": "", "stderr": "", "stdout_truncated": False, "stderr_truncated": False}
    began, process = time.monotonic(), None
    stdout, stderr = b"", b""
    try:
        process = subprocess.Popen(argv, cwd=case, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        record.update(started=True, pid=process.pid)
        try:
            stdout, stderr = process.communicate(timeout=PSQL_TIMEOUT)
        except subprocess.TimeoutExpired as expired:
            record.update(timed_out=True, terminated_by_us=True)
            stdout, stderr = expired.output or b"", expired.stderr or b""
            process.kill()  # Exact Popen object created above; never a supplied PID.
            stdout, stderr = process.communicate(timeout=2)
        process.wait(timeout=2)
        record.update(exit_code=process.returncode, cleanup_confirmed=process.poll() is not None)
    except BaseException as exc:
        record["safe_error_type"] = type(exc).__name__
        if process is not None:
            try:
                if process.poll() is None:
                    record["terminated_by_us"] = True
                    process.kill()
                process.wait(timeout=2)
                record.update(exit_code=process.returncode, cleanup_confirmed=process.poll() is not None)
            except BaseException:
                record["cleanup_confirmed"] = False
    finally:
        record.update(elapsed_seconds=round(time.monotonic() - began, 6),
            stdout=stdout[:MAX_CAPTURE].decode("utf-8", "replace"), stderr=stderr[:MAX_CAPTURE].decode("utf-8", "replace"),
            stdout_truncated=len(stdout) > MAX_CAPTURE, stderr_truncated=len(stderr) > MAX_CAPTURE)
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        record["cleanup_confirmed"] = False
    return record


def loopback_control(family, case, passfile):
    if type(family) is not int or family not in (4, 6):
        _fail("invalid_loopback_family")
    host = "127.0.0.1" if family == 4 else "::1"
    listener = socket.socket(socket.AF_INET if family == 4 else socket.AF_INET6, socket.SOCK_STREAM)
    capture = {"family": family, "port": None, "accepted": False, "request_hex": "", "exact_request": False,
        "reply_hex": "", "extra_bytes_hex": "", "peer_closed": False, "thread_cleaned": False,
        "listener_closed": False, "connection_closed": False, "error": None}
    holder, thread = {}, None
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        if family == 6:
            listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        listener.bind((host, 0))
        capture["port"] = listener.getsockname()[1]
        if capture["port"] == 5432:
            _fail("reserved_loopback_port_selected")
        listener.listen(1)
        listener.settimeout(5)
        def receive():
            connection = None
            try:
                connection, unused = listener.accept()
                holder["connection"] = connection
                capture["accepted"] = True
                connection.settimeout(2)
                request = b""
                while len(request) < 8:
                    chunk = connection.recv(8 - len(request))
                    if not chunk:
                        break
                    request += chunk
                capture.update(request_hex=request.hex(), exact_request=request == SSL_REQUEST)
                if request == SSL_REQUEST:
                    connection.sendall(b"N")
                    capture["reply_hex"] = "4e"
                    extra = connection.recv(1)
                    capture.update(extra_bytes_hex=extra.hex(), peer_closed=extra == b"")
            except Exception as exc:
                capture["error"] = type(exc).__name__
            finally:
                if connection is not None:
                    connection.close()
                    capture["connection_closed"] = connection.fileno() == -1
        thread = threading.Thread(target=receive, name="pr03b-synthetic-ssl-request", daemon=True)
        thread.start()
        client = run_psql(host, capture["port"], case, passfile)
    finally:
        if thread is not None:
            try:
                thread.join(2.5)
            except BaseException:
                capture["error"] = "listener_join_interrupted"
        listener.close()
        capture["listener_closed"] = listener.fileno() == -1
        connection = holder.get("connection")
        if connection is not None:
            connection.close()
            capture["connection_closed"] = connection.fileno() == -1
        if thread is not None:
            try:
                thread.join(2.5)
            except BaseException:
                capture["error"] = "listener_join_interrupted"
            capture["thread_cleaned"] = not thread.is_alive()
    return {"client": client, "capture": capture}


def _case_directory(value):
    if not _absolute_windows(value):
        _fail("absolute_local_case_required")
    case = Path(value)
    if case.parent != SUPPLEMENT_HOME / "cases" or not re.fullmatch(r"run-[0-9a-f]{32}", case.name):
        _fail("coordinator_case_path_required")
    if case == KIT_BASE or KIT_BASE in case.parents:
        _fail("case_must_be_outside_immutable_kit")
    if not case.is_dir():
        _fail("coordinator_created_case_directory_required")
    for path in (*reversed(case.parents), case):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            _fail("case_reparse_refused")
    return case.resolve(strict=True)


def execute_phase(phase, test, case):
    """Called only after original kit verification; injectable in synthetic tests."""
    if phase in ("preflight", "postflight"):
        return {"passed": True, "pins_and_platform_only": True}
    if phase == "baseline":
        with NativeWinsock() as winsock:
            ipv4 = winsock.attempt(4, "tcp", allow_pending=True)
            ipv6 = winsock.attempt(6, "tcp", allow_pending=True)
        return {"passed": native_baseline_ok(ipv4), "tcp4": ipv4, "tcp6": ipv6,
                "ipv6_positive_control": native_baseline_ok(ipv6), "no_application_bytes_sent": True}
    family = int(test[-1])
    kind = test[:-1]
    if kind in ("tcp", "udp"):
        with NativeWinsock() as winsock:
            record = winsock.attempt(family, kind, allow_pending=False)
        return {"passed": native_isolation_ok(record), "native": record}
    passfile = _passfile(case, test)
    if kind == "psql":
        host, port = IPV4_TARGET if family == 4 else IPV6_TARGET
        record = run_psql(host, port, case, passfile)
        return {"passed": psql_external_ok(record, family), "client": record}
    if kind == "loop":
        record = loopback_control(family, case, passfile)
        return {"passed": psql_loopback_ok(record["client"], record["capture"]), **record}
    _fail("invalid_isolated_test")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=("preflight", "baseline", "isolated", "postflight"))
    parser.add_argument("--case", required=True)
    parser.add_argument("--test", choices=TESTS)
    args = parser.parse_args(argv)
    try:
        filename = result_filename(args.phase, args.test)
    except ProbeFailure:
        parser.error("isolated requires exactly one --test; other phases forbid --test")
    try:
        platform = verify_guest_platform()
        case = _case_directory(args.case)
        output = case / filename
        stream = output.open("x", encoding="utf-8", newline="\n")
    except Exception:
        print("STOP: dedicated guest, fixed supplement and new ordinary case/result required", file=sys.stderr)
        return 2
    report = {"schema_version": 1, "scope": "single_guest_network_control_only", "phase": args.phase,
        "test": args.test, "case": str(case), "passed": False, "time_utc": datetime.now(timezone.utc).isoformat(),
        "guest_platform_guard": platform,
        "kit_base": str(KIT_BASE), "batch2_acceptance": False, "target_verified": False,
        "database_execution_allowed": False, "postgres_server_started": False, "SQL_executed": False,
        "original_guest_main_selftest_or_launcher_called": False, "requires_external_OS_boundary_evidence": True,
        "OS_interface_boundary_verified_by_this_probe": False}
    verified, helper_before = None, None
    try:
        helper_before = _file_sha(Path(__file__).resolve())
        verified = verify_original()
        report.update(pins_before=verified, helper_sha256_before=helper_before)
        detail = execute_phase(args.phase, args.test, case)
        report["result"] = detail
        report["passed"] = detail.get("passed") is True
        if not report["passed"]:
            report["safe_error"] = "network_control_requirement_not_met"
    except BaseException as exc:
        report.update(passed=False, safe_error=exc.code if isinstance(exc, ProbeFailure) else "probe_failed_closed",
                      safe_error_type=type(exc).__name__)
    finally:
        if verified is not None:
            try:
                after = verify_original()
                report.update(pins_after=after, original_payload_unchanged=after == verified)
                if after != verified:
                    report.update(passed=False, safe_error="original_payload_changed")
            except Exception:
                report.update(passed=False, original_payload_unchanged=False, safe_error="post_operation_pin_check_failed")
        if helper_before is not None:
            try:
                helper_after = _file_sha(Path(__file__).resolve())
                report["helper_sha256_after"] = helper_after
                if helper_after != helper_before:
                    report.update(passed=False, safe_error="supplement_helper_changed")
            except Exception:
                report.update(passed=False, safe_error="supplement_helper_post_check_failed")
        report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        with stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    print(("PASS" if report["passed"] else "STOP") + " " + args.phase + ("/" + args.test if args.test else "") + "; evidence: " + str(output))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
