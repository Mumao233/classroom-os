"""Windows-native ownership snapshots and a suspended, job-owned PG launcher.

No database, shell, service manager, firewall or network socket is used here.
IP Helper observes listeners; it is not an outbound-network sandbox. All process
termination is by a handle created by this object or this object's private job.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import ctypes
from ctypes import wintypes as w
from dataclasses import asdict, dataclass, field
import hashlib
import ipaddress
import os
from pathlib import Path
import struct
import subprocess
import threading
import time

import target_guard as guard

_DWORD_MAX = 0xFFFFFFFF
_TABLE_LIMIT = 16 * 1024 * 1024
_COMMAND_LIMIT = 128 * 1024
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF


def _fail(reason: str) -> None:
    guard._reject("native ownership: " + reason)


def _pid(pid: int) -> None:
    if type(pid) is not int or not 1 <= pid <= _DWORD_MAX:
        _fail("invalid native process ID")


def _port(port: int) -> None:
    if type(port) is not int or not 1 <= port <= 65535 or port == 5432:
        _fail("invalid dedicated port")


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
        ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD),
        ("dwXSize", w.DWORD), ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD),
        ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
        ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
        ("lpReserved2", ctypes.POINTER(w.BYTE)), ("hStdInput", w.HANDLE),
        ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE),
                ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]


class _BasicJobLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", w.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
        ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD),
        ("SchedulingClass", w.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedJobLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicJobLimits), ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _UnicodeString(ctypes.Structure):
    _fields_ = [("Length", w.USHORT), ("MaximumLength", w.USHORT),
                ("Buffer", ctypes.c_void_p)]


class _ProcessBasicInformation(ctypes.Structure):
    # Native ProcessBasicInformation layout on supported 64-bit Windows.
    _fields_ = [("Reserved1", ctypes.c_void_p), ("PebBaseAddress", ctypes.c_void_p),
                ("Reserved2a", ctypes.c_void_p), ("Reserved2b", ctypes.c_void_p),
                ("UniqueProcessId", ctypes.c_size_t), ("ParentProcessId", ctypes.c_size_t)]


class _Api:
    def __init__(self) -> None:
        if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
            _fail("64-bit Windows native API required")
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.ip = ctypes.WinDLL("iphlpapi", use_last_error=True)
        self.nt = ctypes.WinDLL("ntdll", use_last_error=True)
        self.sh = ctypes.WinDLL("shell32", use_last_error=True)
        self._bind(self.k, "GetCurrentProcess", [], w.HANDLE)
        self._bind(self.k, "OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)
        self._bind(self.k, "GetProcessId", [w.HANDLE], w.DWORD)
        self._bind(self.k, "CloseHandle", [w.HANDLE], w.BOOL)
        self._bind(self.k, "GetProcessTimes", [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4, w.BOOL)
        self._bind(self.k, "QueryFullProcessImageNameW", [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)], w.BOOL)
        self._bind(self.k, "WaitForSingleObject", [w.HANDLE, w.DWORD], w.DWORD)
        self._bind(self.k, "GetExitCodeProcess", [w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL)
        self._bind(self.k, "CreateJobObjectW", [ctypes.c_void_p, w.LPCWSTR], w.HANDLE)
        self._bind(self.k, "SetInformationJobObject", [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL)
        self._bind(self.k, "QueryInformationJobObject", [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL)
        self._bind(self.k, "AssignProcessToJobObject", [w.HANDLE, w.HANDLE], w.BOOL)
        self._bind(self.k, "IsProcessInJob", [w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)], w.BOOL)
        self._bind(self.k, "TerminateJobObject", [w.HANDLE, w.UINT], w.BOOL)
        self._bind(self.k, "TerminateProcess", [w.HANDLE, w.UINT], w.BOOL)
        self._bind(self.k, "ResumeThread", [w.HANDLE], w.DWORD)
        self._bind(self.k, "DuplicateHandle", [w.HANDLE, w.HANDLE, w.HANDLE, ctypes.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD], w.BOOL)
        self._bind(self.k, "InitializeProcThreadAttributeList", [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)], w.BOOL)
        self._bind(self.k, "UpdateProcThreadAttribute", [ctypes.c_void_p, w.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p], w.BOOL)
        self._bind(self.k, "DeleteProcThreadAttributeList", [ctypes.c_void_p], None)
        self._bind(self.k, "CreateProcessW", [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD, ctypes.c_void_p, w.LPCWSTR, ctypes.POINTER(_StartupInfo), ctypes.POINTER(_ProcessInformation)], w.BOOL)
        self._bind(self.k, "LocalFree", [w.HLOCAL], w.HLOCAL)
        self._bind(self.ip, "GetExtendedTcpTable", [ctypes.c_void_p, ctypes.POINTER(w.DWORD), w.BOOL, w.ULONG, ctypes.c_int, w.ULONG], w.DWORD)
        self._bind(self.nt, "NtQueryInformationProcess", [w.HANDLE, w.ULONG, ctypes.c_void_p, w.ULONG, ctypes.POINTER(w.ULONG)], w.LONG)
        self._bind(self.sh, "CommandLineToArgvW", [w.LPCWSTR, ctypes.POINTER(ctypes.c_int)], ctypes.POINTER(w.LPWSTR))

    @staticmethod
    def _bind(library, name, arguments, result) -> None:
        try:
            function = getattr(library, name)
        except AttributeError:
            _fail("required native entrypoint unavailable")
        function.argtypes, function.restype = arguments, result


@dataclass(frozen=True, order=True)
class Listener:
    family: str
    address: str
    port: int
    pid: int
    scope_id: int = 0


@dataclass(frozen=True, order=True)
class NetworkRow:
    family: str
    local_address: str
    local_port: int
    remote_address: str
    remote_port: int
    state: int
    pid: int
    local_scope_id: int = 0
    remote_scope_id: int = 0


@dataclass(frozen=True)
class ProcessDetails:
    identity: guard.ProcessIdentity
    parent_pid: int
    arguments: tuple[str, ...] = field(repr=False)
    data_directory: Path | None = None


def _parse_listener_table(raw: bytes, family: int) -> tuple[Listener, ...]:
    """Pure decoder for IP Helper OWNER_PID_LISTENER table snapshots."""
    layout = struct.Struct("<IIIIII") if family == 2 else struct.Struct("<16sII16sIIII") if family == 23 else None
    if layout is None or len(raw) < 4 or len(raw) > _TABLE_LIMIT:
        _fail("invalid listener table")
    count = struct.unpack_from("<I", raw)[0]
    if count > 250000 or 4 + count * layout.size > len(raw):
        _fail("truncated listener table")
    result = []
    for offset in range(4, 4 + count * layout.size, layout.size):
        row = layout.unpack_from(raw, offset)
        if family == 2:
            state, local, port_word, _remote, _remote_port, pid = row
            address, scope = str(ipaddress.IPv4Address(local.to_bytes(4, "little"))), 0
        else:
            local, scope, port_word, _remote, _remote_scope, _remote_port, state, pid = row
            address = str(ipaddress.IPv6Address(local))
        # Some Windows builds also expose bound/transitional entries in this
        # view. They are not listeners; never let an unrelated entry invalidate
        # the selected-port LISTEN ownership check.
        if not 0 <= state <= 12:
            _fail("invalid listener row")
        if state != 2:
            continue
        # Windows documents the upper port-word bits as potentially uninitialized.
        if not 1 <= pid <= _DWORD_MAX:
            _fail("listener owner unavailable")
        port = int.from_bytes((port_word & 65535).to_bytes(2, "little"), "big")
        if not 1 <= port <= 65535:
            _fail("invalid listener port")
        result.append(Listener("ipv4" if family == 2 else "ipv6", address, port, pid, scope))
    return tuple(result)


def _tcp_table_bytes(api: _Api, family: int, table_class: int) -> bytes:
    size = w.DWORD(0)
    status = api.ip.GetExtendedTcpTable(None, ctypes.byref(size), False, family, table_class, 0)
    if status not in (0, 122):
        _fail("listener observation unavailable")
    for _ in range(3):
        if not 4 <= size.value <= _TABLE_LIMIT:
            _fail("listener table exceeds observation budget")
        buffer = ctypes.create_string_buffer(size.value)
        allocated = len(buffer)
        status = api.ip.GetExtendedTcpTable(buffer, ctypes.byref(size), False, family, table_class, 0)
        if status == 122:
            continue
        if status != 0 or not 4 <= size.value <= allocated:
            _fail("listener observation failed")
        return buffer.raw[:size.value]
    _fail("listener table changed beyond retry budget")


def observe_listeners(port: int) -> tuple[Listener, ...]:
    """Observe only listeners on the selected port; never open/bind a socket."""
    _port(port)
    api = _Api()
    rows = (*_parse_listener_table(_tcp_table_bytes(api, 2, 3), 2),
            *_parse_listener_table(_tcp_table_bytes(api, 23, 3), 23))
    return tuple(sorted(row for row in rows if row.port == port))


def _parse_tcp_rows(raw: bytes, family: int, pids: set[int]) -> tuple[NetworkRow, ...]:
    """Decode OWNER_PID_ALL, exporting no row owned by an unrelated PID."""
    layout = struct.Struct("<IIIIII") if family == 2 else struct.Struct("<16sII16sIIII") if family == 23 else None
    if layout is None or len(raw) < 4 or len(raw) > _TABLE_LIMIT:
        _fail("invalid TCP observation table")
    count = struct.unpack_from("<I", raw)[0]
    if count > 250000 or 4 + count * layout.size > len(raw):
        _fail("truncated TCP observation table")
    rows = []
    for offset in range(4, 4 + count * layout.size, layout.size):
        row = layout.unpack_from(raw, offset)
        if family == 2:
            state, local, local_word, remote, remote_word, pid = row
            if pid not in pids:
                continue
            local_address = str(ipaddress.IPv4Address(local.to_bytes(4, "little")))
            remote_address = str(ipaddress.IPv4Address(remote.to_bytes(4, "little")))
            local_scope, remote_scope = 0, 0
        else:
            local, local_scope, local_word, remote, remote_scope, remote_word, state, pid = row
            if pid not in pids:
                continue
            local_address, remote_address = str(ipaddress.IPv6Address(local)), str(ipaddress.IPv6Address(remote))
        if not 0 <= state <= 12:
            _fail("invalid owned TCP state")
        local_port = int.from_bytes((local_word & 65535).to_bytes(2, "little"), "big")
        remote_port = int.from_bytes((remote_word & 65535).to_bytes(2, "little"), "big")
        rows.append(NetworkRow("ipv4" if family == 2 else "ipv6", local_address, local_port,
                               remote_address, remote_port, state, pid, local_scope, remote_scope))
    return tuple(rows)


def tcp_rows(pids: set[int]) -> tuple[NetworkRow, ...]:
    """Observe selected owned process IDs only; this neither blocks traffic nor connects."""
    if not isinstance(pids, set) or not pids or len(pids) > 4096:
        _fail("explicit bounded process-ID set required")
    for pid in pids:
        _pid(pid)
    api = _Api()
    return tuple(sorted((*_parse_tcp_rows(_tcp_table_bytes(api, 2, 5), 2, pids),
                         *_parse_tcp_rows(_tcp_table_bytes(api, 23, 5), 23, pids))))


def _handle_identity(api: _Api, handle, expected_pid: int) -> guard.ProcessIdentity:
    if api.k.GetProcessId(handle) != expected_pid:
        _fail("process handle differs from requested identity")
    created, exited, kernel, user = (w.FILETIME() for _ in range(4))
    size, name = w.DWORD(32768), ctypes.create_unicode_buffer(32768)
    if (not api.k.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user))
            or exited.dwLowDateTime or exited.dwHighDateTime
            or not api.k.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size))):
        _fail("live process identity unavailable")
    token = (created.dwHighDateTime << 32) | created.dwLowDateTime
    if not token:
        _fail("invalid process creation token")
    return guard.ProcessIdentity(expected_pid, token, guard._absolute(name.value))


def _command_arguments(api: _Api, handle) -> tuple[str, ...]:
    needed = w.ULONG(0)
    status = int(api.nt.NtQueryInformationProcess(handle, 60, None, 0, ctypes.byref(needed))) & _DWORD_MAX
    if status not in (0, 0xC0000004, 0xC0000023, 0x80000005):
        _fail("native command-line observation unavailable")
    if not ctypes.sizeof(_UnicodeString) <= needed.value <= _COMMAND_LIMIT:
        _fail("native command line exceeds observation budget")
    buffer = ctypes.create_string_buffer(needed.value)
    returned = w.ULONG(0)
    if api.nt.NtQueryInformationProcess(handle, 60, buffer, len(buffer), ctypes.byref(returned)) != 0:
        _fail("native command-line observation failed")
    value = _UnicodeString.from_buffer(buffer)
    base = ctypes.addressof(buffer)
    if (not value.Buffer or value.Length % 2 or value.MaximumLength < value.Length
            or value.Buffer < base + ctypes.sizeof(_UnicodeString)
            or value.Buffer + value.Length > base + len(buffer)):
        _fail("invalid native command-line buffer")
    try:
        command = ctypes.string_at(value.Buffer, value.Length).decode("utf-16-le", "strict")
    except UnicodeError:
        _fail("invalid native command-line encoding")
    if not command or "\x00" in command:
        _fail("invalid native command line")
    count = ctypes.c_int(0)
    argv = api.sh.CommandLineToArgvW(command, ctypes.byref(count))
    if not argv:
        _fail("native argument parsing failed")
    try:
        if not 1 <= count.value <= 128:
            _fail("native argument count exceeds budget")
        return tuple(argv[index] for index in range(count.value))
    finally:
        api.k.LocalFree(ctypes.cast(argv, w.HLOCAL))


def _data_argument(arguments: Sequence[str]) -> Path | None:
    positions = [index for index, value in enumerate(arguments) if value == "-D"]
    if not positions:
        return None
    if len(positions) != 1 or positions[0] + 1 >= len(arguments):
        _fail("ambiguous data-directory argument")
    return guard._absolute(arguments[positions[0] + 1])


def observe_process_details(pid: int) -> ProcessDetails:
    """Read one process by handle; raw argv is private to trusted caller code."""
    _pid(pid)
    api = _Api()
    handle = api.k.OpenProcess(0x1000, False, pid)
    if not handle:
        _fail("process query handle unavailable")
    try:
        before = _handle_identity(api, handle, pid)
        basic, returned = _ProcessBasicInformation(), w.ULONG(0)
        if (api.nt.NtQueryInformationProcess(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic), ctypes.byref(returned)) != 0
                or basic.UniqueProcessId != pid):
            _fail("native parent identity unavailable")
        parent = int(basic.ParentProcessId)
        _pid(parent)
        arguments = _command_arguments(api, handle)
        after = _handle_identity(api, handle, pid)
        if before != after or guard.observe_windows_process(pid) != after:
            _fail("process identity changed during observation")
        return ProcessDetails(after, parent, arguments, _data_argument(arguments))
    finally:
        api.k.CloseHandle(handle)


def _revalidate_plan(plan: guard.RunPlan) -> None:
    if not isinstance(plan, guard.RunPlan):
        _fail("validated run plan required")
    manifest = {
        "schema_version": 1, "kind": "pr03b.synthetic_run_plan", "run_id": plan.run_id,
        "instance_nonce": plan.instance_nonce, "worktree": str(plan.root),
        "data_directory": str(plan.data_directory), "port": plan.port,
        "database": plan.database, "login": plan.login,
        "postgres_executable": str(plan.postgres_executable),
        "postgres_sha256": plan.postgres_sha256, "server_version_num": plan.server_version_num,
    }
    url = f"postgresql+psycopg://{plan.login}@127.0.0.1:{plan.port}/{plan.database}"
    if guard.validate_plan(url, manifest, guard.ROOT, os.environ) != plan:
        _fail("run plan changed from this checkout")


def fixed_postgres_arguments(plan: guard.RunPlan) -> tuple[str, ...]:
    _revalidate_plan(plan)
    return (str(plan.postgres_executable), "-D", str(plan.data_directory),
            "-h", "127.0.0.1", "-p", str(plan.port))


def _environment_block(environment: Mapping[str, str]):
    if not isinstance(environment, Mapping):
        _fail("explicit child environment mapping required")
    guard.check_environment(environment)
    entries, keys = [], set()
    for key, value in environment.items():
        if (not isinstance(key, str) or not key or "=" in key or "\x00" in key
                or not isinstance(value, str) or "\x00" in value or key.upper() in keys):
            _fail("invalid or ambiguous child environment")
        keys.add(key.upper())
        entries.append((key, value))
    block = "\x00".join(key + "=" + value for key, value in sorted(entries, key=lambda pair: pair[0].upper())) + "\x00\x00"
    if len(block) > 65536:
        _fail("child environment exceeds budget")
    return ctypes.create_unicode_buffer(block)


class NativeJob:
    """One private non-breakaway job, one fixed-argv PostgreSQL primary process.

    Context-manager lifetime is mandatory for the caller. KILL_ON_JOB_CLOSE also
    limits orphaning if the launcher itself terminates. Nested-job assignment may
    be denied by the host: that is a hard failure, never an uncontained fallback.
    """

    def __init__(self, plan: guard.RunPlan):
        self._lock = threading.RLock()
        _revalidate_plan(plan)
        self.plan, self.api = plan, _Api()
        self._process, self._job = None, None
        self.identity: guard.ProcessIdentity | None = None
        self.cleanup_confirmed = False
        self._unassigned_cleanup_unconfirmed = False
        self._last_exit_code: int | None = None
        self.parent = guard.observe_windows_process(os.getpid())
        job = self.api.k.CreateJobObjectW(None, None)
        if not job:
            _fail("private job creation failed")
        limits = _ExtendedJobLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE only; no BREAKAWAY flags.
        if not self.api.k.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.api.k.CloseHandle(job)
            _fail("private job limits unavailable")
        self._job = job

    def __enter__(self) -> NativeJob:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _require_open(self):
        # Callers retain _lock until the captured handle is no longer in use.
        job = self._job
        if not job:
            _fail("private job is closed")
        return job

    def contains(self, pid: int) -> bool:
        with self._lock:
            _pid(pid)
            job = self._require_open()
            handle = self.api.k.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                if self.api.k.GetProcessId(handle) != pid:
                    _fail("process handle identity differs")
                inside = w.BOOL(False)
                if not self.api.k.IsProcessInJob(handle, job, ctypes.byref(inside)):
                    _fail("job membership observation failed")
                return bool(inside.value)
            finally:
                self.api.k.CloseHandle(handle)

    def process_ids(self) -> tuple[int, ...]:
        with self._lock:
            job = self._require_open()
            capacity = 64
            for _ in range(4):
                size = 8 + capacity * ctypes.sizeof(ctypes.c_size_t)
                buffer, returned = ctypes.create_string_buffer(size), w.DWORD(0)
                ok = self.api.k.QueryInformationJobObject(job, 3, buffer, size, ctypes.byref(returned))
                assigned, listed = struct.unpack_from("<II", buffer.raw)
                if ok:
                    if listed > capacity or listed > assigned:
                        _fail("invalid job process list")
                    ids = tuple(struct.unpack_from("<Q", buffer.raw, 8 + 8 * index)[0] for index in range(listed))
                    for pid in ids:
                        _pid(pid)
                    return tuple(sorted(ids))
                if ctypes.get_last_error() != 234 or capacity >= 4096:
                    _fail("job process-list observation failed")
                capacity = min(4096, max(capacity * 2, assigned))
            _fail("job process list exceeds retry budget")

    def _duplicate_log_handle(self, stream):
        import msvcrt
        duplicate = w.HANDLE()
        current = self.api.k.GetCurrentProcess()
        source = w.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
        if not self.api.k.DuplicateHandle(current, source, current, ctypes.byref(duplicate), 0, True, 2):
            _fail("stdio handle duplication failed")
        return duplicate

    def start_postgres(self, environment: Mapping[str, str], stdout_path: Path, stderr_path: Path) -> guard.ProcessIdentity:
        with self._lock:
            arguments = fixed_postgres_arguments(self.plan)
            return self._start(arguments, self.plan.postgres_executable, self.plan.postgres_sha256,
                               environment, stdout_path, stderr_path)

    def start_helper(self, name: str, arguments: Sequence[str], expected_sha256: str,
                     environment: Mapping[str, str], stdout_path: Path, stderr_path: Path) -> guard.ProcessIdentity:
        """Only fixed initdb/pg_controldata calls, including all their descendants.

        The independently approved toolchain manifest supplies the helper hash;
        accepting a caller hash is not itself a component-provenance assertion.
        """
        with self._lock:
            _revalidate_plan(self.plan)
            if name not in ("initdb", "pg_controldata"):
                _fail("helper is not in the fixed allowlist")
            if not isinstance(expected_sha256, str) or not guard._HEX64.fullmatch(expected_sha256):
                _fail("invalid helper executable pin")
            data = str(self.plan.data_directory)
            expected = (("-D", data, "-U", self.plan.login, "--pwfile", str(self.plan.run_root / "synthetic.pw"),
                         "--encoding=UTF8", "--locale=C", "--auth-local=reject",
                         "--auth-host=scram-sha-256", "--no-instructions")
                        if name == "initdb" else ("-D", data))
            if (not isinstance(arguments, Sequence) or isinstance(arguments, (str, bytes))
                    or any(not isinstance(value, str) for value in arguments) or tuple(arguments) != expected):
                _fail("helper arguments differ from the fixed run-owned command")
            if name == "initdb":
                password_file = guard.contained_path(str(self.plan.run_root / "synthetic.pw"), self.plan.run_root)
                if guard._read_file(password_file, 1024) not in (
                        guard.SYNTHETIC_PASSWORD.encode("ascii"),
                        (guard.SYNTHETIC_PASSWORD + "\n").encode("ascii"),
                        (guard.SYNTHETIC_PASSWORD + "\r\n").encode("ascii")):
                    _fail("helper password file is not the fixed synthetic input")
            executable = self.plan.postgres_executable.parent / (name + ".exe")
            guard.no_reparse(executable)
            return self._start((str(executable), *expected), executable, expected_sha256,
                               environment, stdout_path, stderr_path)

    def _start(self, arguments: Sequence[str], executable_path: Path, expected_sha256: str,
               environment: Mapping[str, str], stdout_path: Path, stderr_path: Path) -> guard.ProcessIdentity:
        with self._lock:
            job = self._require_open()
            if self._process or self.identity:
                _fail("job primary process already exists")
            _revalidate_plan(self.plan)
            executable = guard._read_file(executable_path, 64 * 1024 * 1024)
            if hashlib.sha256(executable).hexdigest() != expected_sha256:
                _fail("executable pin differs before creation")
            out = guard.contained_path(str(stdout_path), self.plan.run_root)
            err = guard.contained_path(str(stderr_path), self.plan.run_root)
            if out == err or out.exists() or err.exists():
                _fail("stdio logs must be distinct new run-owned files")
            env = _environment_block(environment)
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
            if len(command) > 32767:
                _fail("fixed command exceeds native limit")
            streams, duplicates, attribute = [], [], None
            pi, assigned, resumed = _ProcessInformation(), False, False
            try:
                # Exclusive creation preserves earlier evidence and prevents symlink following at the final name.
                streams.append(open(os.devnull, "rb"))
                streams.append(out.open("xb"))
                streams.append(err.open("xb"))
                guard.no_reparse(out)
                guard.no_reparse(err)
                for stream in streams:
                    duplicates.append(self._duplicate_log_handle(stream))
                handles = (w.HANDLE * 3)(*(handle.value for handle in duplicates))
                required = ctypes.c_size_t(0)
                self.api.k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(required))
                if not 1 <= required.value <= 65536:
                    _fail("native handle-list budget invalid")
                attribute = ctypes.create_string_buffer(required.value)
                if not self.api.k.InitializeProcThreadAttributeList(attribute, 1, 0, ctypes.byref(required)):
                    attribute = None
                    _fail("native handle-list initialization failed")
                if not self.api.k.UpdateProcThreadAttribute(attribute, 0, 0x20002, handles, ctypes.sizeof(handles), None, None):
                    _fail("native handle-list setup failed")
                startup = _StartupInfoEx()
                startup.StartupInfo.cb = ctypes.sizeof(startup)
                startup.StartupInfo.dwFlags = 0x100 | 1  # USESTDHANDLES + USESHOWWINDOW.
                startup.StartupInfo.wShowWindow = 0
                startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = duplicates
                startup.lpAttributeList = ctypes.cast(attribute, ctypes.c_void_p)
                flags = 0x4 | 0x08000000 | 0x00080000 | 0x400  # SUSPENDED, NO_WINDOW, EXTENDED_STARTUPINFO, UNICODE_ENV.
                if not self.api.k.CreateProcessW(str(executable_path), command, None, None, True, flags, env, str(self.plan.run_root), ctypes.byref(startup.StartupInfo), ctypes.byref(pi)):
                    _fail("suspended process creation failed")
                if not self.api.k.AssignProcessToJobObject(job, pi.hProcess):
                    _fail("private job assignment failed")
                assigned = True
                inside = w.BOOL(False)
                if not self.api.k.IsProcessInJob(pi.hProcess, job, ctypes.byref(inside)) or not inside.value:
                    _fail("new process job membership differs")
                identity = _handle_identity(self.api, pi.hProcess, int(pi.dwProcessId))
                if identity.executable != executable_path:
                    _fail("new process executable differs")
                if self.api.k.ResumeThread(pi.hThread) != 1:
                    _fail("new process initial suspension differs")
                resumed = True
                self._process, self.identity = pi.hProcess, identity
                pi.hProcess = None  # Ownership transferred to this object.
                return identity
            except BaseException:
                if pi.hProcess:
                    if assigned:
                        terminated = self.api.k.TerminateJobObject(job, 1)
                    else:
                        # Only the exact handle just returned by our suspended CreateProcessW, never an external PID.
                        terminated = self.api.k.TerminateProcess(pi.hProcess, 1)
                    exited = self.api.k.WaitForSingleObject(pi.hProcess, 10000) == 0
                    if not assigned and not exited:
                        self._unassigned_cleanup_unconfirmed = True
                    if not terminated and not exited:
                        _fail("new suspended process cleanup unconfirmed")
                elif resumed and self._process:
                    self.api.k.TerminateJobObject(job, 1)
                raise
            finally:
                if pi.hThread:
                    self.api.k.CloseHandle(pi.hThread)
                if pi.hProcess:
                    self.api.k.CloseHandle(pi.hProcess)
                if attribute is not None:
                    self.api.k.DeleteProcThreadAttributeList(attribute)
                for handle in duplicates:
                    self.api.k.CloseHandle(handle)
                for stream in streams:
                    stream.close()

    def wait(self, timeout: float = 0) -> bool:
        """True if this job's primary exited; no arbitrary PID or kill operation."""
        with self._lock:
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 <= timeout <= 30:
                _fail("invalid owned-process wait budget")
            process = self._process
            if not process:
                return True
            result = self.api.k.WaitForSingleObject(process, int(timeout * 1000))
            if result == _WAIT_TIMEOUT:
                return False
            if result != 0:
                _fail("owned-process wait failed")
            return True

    def exit_code(self) -> int | None:
        """Exit code from the retained primary HANDLE; None means not known/exited."""
        with self._lock:
            process = self._process
            if not process:
                return self._last_exit_code
            if not self.wait(0):
                return None
            code = w.DWORD()
            if not self.api.k.GetExitCodeProcess(process, ctypes.byref(code)):
                _fail("owned-process exit code unavailable")
            self._last_exit_code = int(code.value)
            return self._last_exit_code

    def close(self, timeout: float = 10) -> None:
        with self._lock:
            if not self._job:
                return
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 <= timeout <= 30:
                _fail("invalid job cleanup budget")
            job, process = self._job, self._process
            error = None
            try:
                if self.process_ids() and not self.api.k.TerminateJobObject(job, 1):
                    _fail("owned job termination failed")
                deadline = time.monotonic() + timeout
                while self.process_ids():
                    if time.monotonic() >= deadline:
                        _fail("owned job termination not confirmed")
                    time.sleep(0.01)
                if process and not self.wait(max(0, min(30, deadline - time.monotonic()))):
                    _fail("owned primary exit not confirmed")
                if self._unassigned_cleanup_unconfirmed:
                    _fail("earlier unassigned-child cleanup remains unconfirmed")
                self.exit_code()
                self.cleanup_confirmed = True
            except BaseException as exc:
                error = exc
            finally:
                self._job, self._process = None, None
                if process:
                    self.api.k.CloseHandle(process)
                # Last job handle is not inherited. Closing it kills only this job if graceful confirmation failed.
                self.api.k.CloseHandle(job)
            if error is not None:
                raise error


def job_network(job: NativeJob) -> dict:
    """Snapshot native TCP for a stable set of this private job's processes.

    A loopback-only snapshot is retrospective evidence. It does not prevent a
    later native/libpq connection and is never reported as OS network isolation.
    """
    if not isinstance(job, NativeJob):
        _fail("private owned job required")
    before = job.process_ids()
    if not before:
        _fail("private job has no live processes")
    identities = {}
    for pid in before:
        if not job.contains(pid):
            _fail("job process membership changed")
        identities[pid] = guard.observe_windows_process(pid)
    rows = tcp_rows(set(before))
    after = job.process_ids()
    if after != before:
        _fail("job process set changed during network observation")
    for pid in after:
        if not job.contains(pid) or guard.observe_windows_process(pid) != identities[pid]:
            _fail("job process identity changed during network observation")
    unexpected = []
    for row in rows:
        local = ipaddress.ip_address(row.local_address)
        remote = ipaddress.ip_address(row.remote_address)
        if (not local.is_loopback
                or (row.state != 2 and (row.remote_port or not remote.is_unspecified) and not remote.is_loopback)):
            unexpected.append(row)
    return {"evidence_kind": "native_job_tcp_snapshot", "process_ids": list(after),
            "rows": [asdict(row) for row in rows], "process_set_stable": True,
            "loopback_only_observed": not unexpected, "non_loopback_row_count": len(unexpected),
            "target_verified": False, "database_execution_allowed": False,
            "os_network_isolation_verified": False}


def verify_listener_views(port: int, pid: int) -> dict:
    """Require LISTEN=2 in both public TCP views; never infer it from class alone.

    An inconsistent provider can return an apparently valid state in just one
    view. The ALL-table cross-check must also report the same listener.
    Both views still use IP Helper;
    agreement is not an attestation of kernel integrity or network isolation.
    """
    _port(port)
    _pid(pid)
    listeners = observe_listeners(port)
    selected = tuple(row for row in tcp_rows({pid}) if row.local_port == port)
    expected = (Listener("ipv4", "127.0.0.1", port, pid),)
    expected_all = (NetworkRow("ipv4", "127.0.0.1", port, "0.0.0.0", 0, 2, pid),)
    if (listeners != expected or tuple(row for row in selected if row.state == 2) != expected_all
            or any(row.state == 0 for row in selected)):
        _fail("listener-only and all-TCP views do not confirm the same LISTEN=2 endpoint")
    return {"listener_only": [asdict(row) for row in listeners],
            "all_tcp_selected_owner": [asdict(row) for row in selected],
            "listen_state_confirmed_in_both_views": True}


def verify_owned_endpoint(plan: guard.RunPlan, expected_process: guard.ProcessIdentity,
                          expected_parent: guard.ProcessIdentity, job: NativeJob,
                          expected_argv: Sequence[str] | None = None) -> dict:
    """Native before/after snapshot, not a DB handshake or firewall attestation.

    The caller must repeat this around its live, read-only PG probe and again at
    each protected execution boundary; JSON snapshots cannot stand in for that.
    """
    arguments = fixed_postgres_arguments(plan)
    if expected_argv is not None and tuple(expected_argv) != arguments:
        _fail("expected argv differs from fixed launcher arguments")
    if not isinstance(job, NativeJob) or job.plan != plan or job.identity != expected_process:
        _fail("endpoint is not the primary of this private job")
    if guard.observe_windows_process(expected_parent.pid) != expected_parent or not job.contains(expected_process.pid):
        _fail("launcher parent or job membership differs")
    first_process = observe_process_details(expected_process.pid)
    first_views = verify_listener_views(plan.port, expected_process.pid)
    if (first_process.identity != expected_process or first_process.parent_pid != expected_parent.pid
            or first_process.arguments != arguments or first_process.data_directory != plan.data_directory):
        _fail("listener, process, parent, argv or data directory differs")
    raw = guard._read_file(plan.postgres_executable, 64 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != plan.postgres_sha256:
        _fail("endpoint executable pin differs")
    last_process = observe_process_details(expected_process.pid)
    verify_listener_views(plan.port, expected_process.pid)
    if (last_process != first_process
            or guard.observe_windows_process(expected_parent.pid) != expected_parent
            or not job.contains(expected_process.pid)
            or guard._read_file(plan.postgres_executable, 64 * 1024 * 1024) != raw):
        _fail("native endpoint changed during observation")
    return {
        "evidence_kind": "native_listener_process_snapshot", "pid": expected_process.pid,
        "creation_token": expected_process.creation_token, "executable": str(expected_process.executable),
        "parent_pid": expected_parent.pid, "parent_creation_token": expected_parent.creation_token,
        "data_directory": str(plan.data_directory), "address": "127.0.0.1", "port": plan.port,
        "job_membership": True, "job_process_ids": list(job.process_ids()),
        "fixed_argv_verified": True, "ipv4_ipv6_listener_tables_checked": True,
        "listener_views": first_views,
        "target_verified": False, "database_execution_allowed": False,
        "os_network_isolation_verified": False,
    }
