"""PR-03b run-owned guard components; no database or subprocess execution.

Plan, local ownership, and server-observation checks are separate evidence tiers.
None returns an execution capability. A future reviewed adapter must obtain live
server facts and repeat ownership checks at the protected execution boundary.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web/backend"))
from classroom_web.test_support import (  # noqa: E402
    SYNTHETIC_PASSWORD, UnsafeTestDatabaseTarget, validate_test_database_target,
)

_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f\ud800-\udfff]")
PLAN_KEYS = frozenset(("schema_version", "kind", "run_id", "instance_nonce", "worktree",
                       "data_directory", "port", "database", "login", "postgres_executable",
                       "postgres_sha256", "server_version_num"))


def _reject(reason: str) -> None:
    raise UnsafeTestDatabaseTarget("PR-03b guard: " + reason)


def check_environment(environ: Mapping[str, str]) -> None:
    if any(str(k).upper().startswith("PG") for k in environ):
        _reject("inherited libpq settings are forbidden")
    if any(str(k).upper() == "CLASSROOM_WEB_DATABASE_URL" for k in environ):
        _reject("inherited application database target is forbidden")


def _absolute(value: object) -> Path:
    # Reject network/device/traversal syntax before stat/resolve/open.
    if (not isinstance(value, str) or not value or len(value) > 2048
            or _CONTROL.search(value) or value.startswith(("\\\\", "//"))
            or any(p in (".", "..") for p in re.split(r"[/\\]", value))):
        _reject("invalid absolute local path")
    path = Path(value)
    if not path.is_absolute() or (os.name == "nt" and path.drive.startswith("\\")):
        _reject("absolute local filesystem path required")
    if os.name == "nt" and (":" in str(path)[2:] or any(part.endswith((".", " ")) for part in path.parts)):
        _reject("ambiguous Windows path forbidden")
    return path


def no_reparse(path: Path) -> None:
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            _reject("path metadata unavailable")
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            _reject("symlink or reparse path forbidden")


def contained_path(value: object, anchor: Path) -> Path:
    path = _absolute(value)
    try:
        relative = path.relative_to(anchor)
    except ValueError:
        _reject("path is outside the run-owned tree")
    if not relative.parts:
        _reject("run-owned child path required")
    no_reparse(path)
    return path


def _read_file(path: Path, limit: int) -> bytes:
    no_reparse(path)
    try:
        before = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            _reject("invalid or oversized run-owned file")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            raw = stream.read(limit + 1)
        after = path.stat(follow_symlinks=False)
    except OSError:
        _reject("run-owned evidence unavailable")
    signatures = [(s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns) for s in (before, opened, after)]
    if len(raw) > limit or signatures[0] != signatures[1] or signatures[0] != signatures[2]:
        _reject("run-owned file changed during read")
    no_reparse(path)
    return raw


def strict_json(raw: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _reject("duplicate JSON field")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: _reject("nonfinite JSON number"))
    except (ValueError, UnicodeError, RecursionError):
        _reject("invalid JSON evidence")
    if not isinstance(value, dict):
        _reject("JSON object required")
    return value


@dataclass(frozen=True)
class RunPlan:
    root: Path
    run_id: str
    instance_nonce: str
    run_root: Path
    data_directory: Path
    port: int
    database: str
    login: str
    postgres_executable: Path
    postgres_sha256: str
    server_version_num: int

    def public_summary(self) -> dict:
        return {"run_id": self.run_id, "port": self.port, "database": self.database,
                "login": self.login, "evidence_kind": "plan_only", "target_verified": False,
                "database_execution_allowed": False}


def load_plan(plan_path: str | Path, root: Path = ROOT) -> dict:
    base = _absolute(str(root)) / "work/pr03b/runs"
    path = contained_path(str(plan_path), base)
    parts = path.relative_to(base).parts
    if len(parts) != 2 or not _HEX32.fullmatch(parts[0]) or parts[1] != "plan.json":
        _reject("plan must be at the exact run-owned plan path")
    value = strict_json(_read_file(path, 32768))
    if value.get("run_id") != parts[0]:
        _reject("plan path and run identity differ")
    return value


def validate_plan(value: str, manifest: dict, root: Path = ROOT,
                  environ: Mapping[str, str] | None = None) -> RunPlan:
    environment = os.environ if environ is None else environ
    check_environment(environment)
    # Preserve the original shared guard, including pr02a_ and PG* behavior.
    url = validate_test_database_target(value, environment)
    if url.password != SYNTHETIC_PASSWORD:
        _reject("only the fixed synthetic credential is accepted")
    if not isinstance(manifest, dict) or set(manifest) != PLAN_KEYS:
        _reject("plan fields differ from the fixed schema")
    if (type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or manifest["kind"] != "pr03b.synthetic_run_plan"
            or not isinstance(manifest["run_id"], str) or not _HEX32.fullmatch(manifest["run_id"])
            or not isinstance(manifest["instance_nonce"], str) or not _HEX64.fullmatch(manifest["instance_nonce"])
            or not isinstance(manifest["postgres_sha256"], str) or not _HEX64.fullmatch(manifest["postgres_sha256"])
            or type(manifest["port"]) is not int
            or type(manifest["server_version_num"]) is not int
            or not 170000 <= manifest["server_version_num"] < 180000):
        _reject("invalid plan version or run identity")
    trusted_root = _absolute(str(root))
    if _absolute(manifest["worktree"]) != trusted_root:
        _reject("worktree is not this runner checkout")
    no_reparse(trusted_root)
    run_id = manifest["run_id"]
    run_root = contained_path(str(trusted_root / "work/pr03b/runs" / run_id), trusted_root / "work/pr03b/runs")
    data = contained_path(manifest["data_directory"], run_root)
    if data != run_root / "pg/data":
        _reject("data directory is not the exact run-owned location")
    database, login = "pr02a_"+run_id, "pr02a_"+run_id+"_bootstrap"
    if (manifest["database"] != database or manifest["login"] != login
            or url.database != database or url.username != login or url.port != manifest["port"]):
        _reject("target differs from the exact run database and login")
    exe = _absolute(manifest["postgres_executable"])
    if exe.name.lower() not in ("postgres", "postgres.exe"):
        _reject("postgres executable name required")
    no_reparse(exe)
    return RunPlan(trusted_root, run_id, manifest["instance_nonce"], run_root, data,
                   url.port, database, login, exe, manifest["postgres_sha256"], manifest["server_version_num"])


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    creation_token: int  # Exact Windows FILETIME, not a seconds tolerance.
    executable: Path


@dataclass(frozen=True)
class LocalObservation:
    process: ProcessIdentity
    postmaster_start_epoch: int
    marker_sha256: str
    postmaster_sha256: str


def observe_windows_process(pid: int) -> ProcessIdentity:
    """Read OS identity without invoking a child, shell, socket or PostgreSQL."""
    if os.name != "nt" or type(pid) is not int or not 1 <= pid <= 0xFFFFFFFF:
        _reject("native process observation unavailable")
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessId.argtypes = (wintypes.HANDLE,)
    kernel.GetProcessId.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME))
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        _reject("process identity unavailable")
    try:
        if kernel.GetProcessId(handle) != pid:
            _reject("process handle identity differs")
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        created, exited, kern, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) or not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kern), ctypes.byref(user)):
            _reject("process identity observation failed")
        if exited.dwHighDateTime or exited.dwLowDateTime:
            _reject("process has exited")
        token = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return ProcessIdentity(pid, token, _absolute(buffer.value))
    finally:
        kernel.CloseHandle(handle)


def verify_local_ownership(plan: RunPlan, *, observe_process: Callable[[int], ProcessIdentity] | None = None) -> LocalObservation:
    """Read fresh local evidence twice; this is still not a live PG handshake.

    Injection is a trusted Python selftest seam, never a CLI JSON option. Files
    and process facts are not a defense against a malicious same-OS-account user.
    """
    observer = observe_windows_process if observe_process is None else observe_process
    no_reparse(plan.data_directory)
    owner_path, pid_path = plan.run_root / "owner.json", plan.data_directory / "postmaster.pid"
    owner_raw = _read_file(owner_path, 32768)
    owner = strict_json(owner_raw)
    keys = PLAN_KEYS | {"pid", "creation_token", "postmaster_start_epoch"}
    if set(owner) != keys or owner.get("kind") != "pr03b.synthetic_cluster_owner":
        _reject("invalid ownership marker schema")
    expected = {"schema_version": 1, "kind": "pr03b.synthetic_cluster_owner", "run_id": plan.run_id,
                "instance_nonce": plan.instance_nonce, "worktree": str(plan.root),
                "data_directory": str(plan.data_directory), "port": plan.port, "database": plan.database,
                "login": plan.login, "postgres_executable": str(plan.postgres_executable),
                "postgres_sha256": plan.postgres_sha256, "server_version_num": plan.server_version_num}
    if any(type(owner[k]) is not type(v) or owner[k] != v for k,v in expected.items()):
        _reject("ownership marker does not match this run plan")
    for field in ("pid", "creation_token", "postmaster_start_epoch"):
        if type(owner[field]) is not int or owner[field] <= 0:
            _reject("invalid process identity in marker")
    if owner["pid"] > 0xFFFFFFFF:
        _reject("process ID is outside the native range")
    pid_raw = _read_file(pid_path, 8192)
    try:
        lines = pid_raw.decode("utf-8").splitlines()
        matches = (len(lines) >= 8 and lines[0] == str(owner["pid"])
                   and _absolute(lines[1]) == plan.data_directory
                   and lines[2] == str(owner["postmaster_start_epoch"])
                   and lines[3] == str(plan.port) and lines[5] == "127.0.0.1"
                   and lines[7].strip() == "ready")
    except (UnicodeError, IndexError):
        matches = False
    if not matches:
        _reject("postmaster file does not match current run")
    expected_process = ProcessIdentity(owner["pid"], owner["creation_token"], plan.postgres_executable)
    if observer(owner["pid"]) != expected_process:
        _reject("process identity or creation token differs")
    exe_raw = _read_file(plan.postgres_executable, 64 * 1024 * 1024)
    if hashlib.sha256(exe_raw).hexdigest() != plan.postgres_sha256:
        _reject("postgres executable hash differs")
    if (observer(owner["pid"]) != expected_process or _read_file(owner_path, 32768) != owner_raw
            or _read_file(pid_path, 8192) != pid_raw
            or _read_file(plan.postgres_executable, 64 * 1024 * 1024) != exe_raw):
        _reject("local ownership changed during verification")
    return LocalObservation(expected_process, owner["postmaster_start_epoch"], hashlib.sha256(owner_raw).hexdigest(), hashlib.sha256(pid_raw).hexdigest())


def validate_server_observation(plan: RunPlan, local: LocalObservation, observed: dict) -> None:
    """Compare future trusted read-only probe facts; never mint a DB permission.

    This batch has no live PG probe or execution entry. A snapshot supplied by a
    caller/file is not fresh server evidence. Later code must re-observe local
    identity around its live probe and at the protected write boundary.
    """
    expected = {"login": plan.login, "database": plan.database, "data_directory": str(plan.data_directory),
                "host": "127.0.0.1", "port": plan.port, "listen_addresses": "127.0.0.1",
                "server_version_num": plan.server_version_num,
                "postmaster_pid": local.process.pid, "postmaster_start_epoch": local.postmaster_start_epoch}
    if not isinstance(observed, dict) or set(observed) != set(expected):
        _reject("server observation fields differ")
    if any(type(observed[k]) is not type(v) or observed[k] != v for k,v in expected.items()):
        _reject("server observation does not match this run")
