"""Bounded Codex subscription execution; no API-key or billing fallback."""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

_HOST_ENV = frozenset({"PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
    "USERPROFILE", "HOME", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "PROGRAMDATA",
    "ALLUSERSPROFILE", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ", "CODEX_HOME"})
_SHELL_ENV = frozenset({"PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
    "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ", "TEMP", "TMP", "TMPDIR",
    "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE", "PYTHONUTF8"})
_PROTECTED = (".git", ".codex", ".agents", ".github", "automation", "tools/overnight", "AGENTS.md")


def clean_environment(work: Path, source=None) -> dict[str, str]:
    """No inherited proxies, Git overrides, agent sockets, API keys or loader hooks."""
    source = os.environ if source is None else source
    env = {k: v for k, v in source.items() if k.upper() in _HOST_ENV}
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    env.update(TEMP=str(work), TMP=str(work), TMPDIR=str(work),
               PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", PYTHONUTF8="1")
    return env


def _toml_table(values):
    return "{" + ",".join(json.dumps(k) + "=" + json.dumps(v) for k, v in values.items()) + "}"


def permission_args(workspace: Path, *, readonly=False, read_roots=(), env=None) -> list[str]:
    """Named profiles preserve deny rules; --sandbox must not override them."""
    workspace = Path(workspace).resolve()
    fs = {":root": "deny", ":minimal": "read", ":slash_tmp": "deny",
          workspace.as_posix(): "read" if readonly else "write",
          Path(sys.executable).resolve().parent.as_posix(): "read",
          "~/.codex": "deny", "~/.ssh": "deny", "~/.aws": "deny"}
    git = shutil.which("git", path=(env or os.environ).get("PATH"))
    if git:
        git_dir = Path(git).resolve().parent
        fs[(git_dir.parent if os.name == "nt" else git_dir).as_posix()] = "read"
    for path in read_roots:
        root = Path(path).resolve(strict=True)
        if root == root.parent or root == Path.home() or root == workspace or root in workspace.parents:
            raise ValueError("read root is too broad or overrides workspace")
        sensitive = [Path.home() / name for name in (".codex", ".ssh", ".aws")]
        if (env or {}).get("CODEX_HOME"):
            sensitive.append(Path(env["CODEX_HOME"]))
        if any(root == p.resolve() or p.resolve() in root.parents or root in p.resolve().parents for p in sensitive):
            raise ValueError("read root overlaps host credentials")
        fs[root.as_posix()] = "read"
    for name in _PROTECTED:
        fs[(workspace / name).resolve().as_posix()] = "read"
    if env and env.get("TEMP"):
        # Only this invocation's scratch, never its controller-output siblings.
        fs[Path(env["TEMP"]).resolve().as_posix()] = "write"
    shell_env = {k: v for k, v in (env or {}).items() if k.upper() in _SHELL_ENV}
    config = ['default_permissions="overnight"', "permissions.overnight.filesystem=" + _toml_table(fs),
              "permissions.overnight.network.enabled=false", 'approval_policy="never"',
              'forced_login_method="chatgpt"', 'model_provider="openai"',
              'web_search="disabled"', "notify=[]", "features.multi_agent=false",
              "features.multi_agent_v2=false", "features.apps=false", "features.enable_mcp_apps=false",
              "features.plugins=false", "features.remote_plugin=false", "features.hooks=false",
              "features.skill_mcp_dependency_install=false", "features.skill_search=false",
              "features.skip_host_skill_discovery=true", "features.use_legacy_landlock=false",
              'shell_environment_policy.inherit="none"',
              "shell_environment_policy.set=" + _toml_table(shell_env)]
    if os.name == "nt":
        config.append('windows.sandbox="mxc"')
    return [part for value in config for part in ("-c", value)]


class _WindowsJob:
    """Assign a suspended process before its first instruction; close kills descendants."""
    def __init__(self):
        from ctypes import wintypes as w
        self.w = w
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        k = self.k
        for name, args, result in (
            ("CreateJobObjectW", (ctypes.c_void_p, w.LPCWSTR), w.HANDLE),
            ("SetInformationJobObject", (w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD), w.BOOL),
            ("QueryInformationJobObject", (w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p), w.BOOL),
            ("TerminateJobObject", (w.HANDLE, w.UINT), w.BOOL),
            ("OpenProcess", (w.DWORD, w.BOOL, w.DWORD), w.HANDLE),
            ("WaitForSingleObject", (w.HANDLE, w.DWORD), w.DWORD),
            ("AssignProcessToJobObject", (w.HANDLE, w.HANDLE), w.BOOL),
            ("CloseHandle", (w.HANDLE,), w.BOOL)):
            getattr(k, name).argtypes, getattr(k, name).restype = args, result

        class Basic(ctypes.Structure):
            _fields_ = [("ProcessTime", ctypes.c_longlong), ("JobTime", ctypes.c_longlong),
                        ("Flags", w.DWORD), ("MinimumWorkingSet", ctypes.c_size_t),
                        ("MaximumWorkingSet", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD),
                        ("SchedulingClass", w.DWORD)]
        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]
        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", Counters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.Basic.Flags = 0x2000  # KILL_ON_JOB_CLOSE; no breakaway.
        if not k.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach_and_resume(self, process):
        w, k = self.w, self.k
        if not k.AssignProcessToJobObject(self.handle, w.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())

        class ThreadEntry(ctypes.Structure):
            _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ThreadID", w.DWORD),
                        ("th32OwnerProcessID", w.DWORD), ("tpBasePri", w.LONG),
                        ("tpDeltaPri", w.LONG), ("dwFlags", w.DWORD)]
        for name, args, result in (
            ("CreateToolhelp32Snapshot", (w.DWORD, w.DWORD), w.HANDLE),
            ("Thread32First", (w.HANDLE, ctypes.POINTER(ThreadEntry)), w.BOOL),
            ("Thread32Next", (w.HANDLE, ctypes.POINTER(ThreadEntry)), w.BOOL),
            ("OpenThread", (w.DWORD, w.BOOL, w.DWORD), w.HANDLE),
            ("ResumeThread", (w.HANDLE,), w.DWORD)):
            getattr(k, name).argtypes, getattr(k, name).restype = args, result
        snapshot = k.CreateToolhelp32Snapshot(4, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            present = k.Thread32First(snapshot, ctypes.byref(entry))
            while present:
                if entry.th32OwnerProcessID == process.pid:
                    thread = k.OpenThread(0x0002, False, entry.th32ThreadID)
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if k.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                        return
                    finally:
                        k.CloseHandle(thread)
                present = k.Thread32Next(snapshot, ctypes.byref(entry))
            raise RuntimeError("suspended child main thread not found")
        finally:
            k.CloseHandle(snapshot)

    def close(self):
        if not self.handle:
            return
        # Job active counts can reach zero before process termination finishes.
        # Wait on owned process handles too, so inherited output files are closed.
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in
                        ("UserTime", "KernelTime", "PeriodUserTime", "PeriodKernelTime")]
            _fields_ += [(name, self.w.DWORD) for name in
                         ("PageFaults", "TotalProcesses", "ActiveProcesses", "TerminatedProcesses")]
        processes = {}
        def capture_processes():
            capacity = 64
            while True:
                class ProcessIds(ctypes.Structure):
                    _fields_ = [("Assigned", self.w.DWORD), ("Count", self.w.DWORD),
                                ("Ids", ctypes.c_size_t * capacity)]
                ids = ProcessIds()
                if self.k.QueryInformationJobObject(self.handle, 3, ctypes.byref(ids), ctypes.sizeof(ids), None):
                    break
                error = ctypes.get_last_error()
                if error != 234:  # ERROR_MORE_DATA
                    raise ctypes.WinError(error)
                capacity = max(capacity * 2, ids.Assigned)
            for pid in ids.Ids[:ids.Count]:
                if pid in processes:
                    continue
                handle = self.k.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only.
                if handle:
                    processes[pid] = handle
                elif ctypes.get_last_error() != 87:  # Exited before OpenProcess.
                    raise ctypes.WinError(ctypes.get_last_error())
        try:
            capture_processes()
            if not self.k.TerminateJobObject(self.handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            deadline = time.monotonic() + 5
            while True:
                capture_processes()
                accounting = Accounting()
                if not self.k.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting),
                                                         ctypes.sizeof(accounting), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if accounting.ActiveProcesses == 0:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("job processes did not terminate")
                time.sleep(.01)
            for handle in processes.values():
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                status = self.k.WaitForSingleObject(handle, remaining_ms)
                if status == 258:
                    raise TimeoutError("job process termination did not finish")
                if status != 0:
                    raise ctypes.WinError(ctypes.get_last_error())
        finally:
            for handle in processes.values():
                self.k.CloseHandle(handle)
            handle, self.handle = self.handle, None
            if not self.k.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())


def verify_completed_output(output):
    """Read-only terminal evidence, not proof of process exit or code acceptance.

    A handled tool error is an item, not a failed model turn. Top-level error or
    turn.failed always rejects completion. Usage is retained even on rejection.
    No transcript or reasoning text is included in the returned metadata.
    """
    output = Path(output)
    report = {"passed": False, "usage": None, "thread_id": None,
              "terminal_count": 0, "nonfatal_item_errors": 0,
              "final_sha256": None, "events_sha256": None, "stderr_sha256": None,
              "error_reason": "completion_evidence_incomplete"}
    try:
        raw = (output / "stdout.jsonl").read_bytes()
        report["events_sha256"] = hashlib.sha256(raw).hexdigest()
        events, invalid_stream = [], False
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeError, ValueError):
                invalid_stream = True
                continue
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                invalid_stream = True
            else:
                events.append(event)
        if not events:
            return report
        terminals = [e for e in events if e["type"] == "turn.completed"]
        report["terminal_count"] = len(terminals)
        usage = {"turns": 0, "input_tokens": 0, "output_tokens": 0}
        valid_usage = bool(terminals)
        for terminal in terminals:
            values = terminal.get("usage", {})
            if not isinstance(values, dict) or any(type(values.get(k)) is not int or values[k] < 0 for k in ("input_tokens", "output_tokens")):
                valid_usage = False
                break
            usage["turns"] += 1
            for key in ("input_tokens", "output_tokens"):
                usage[key] += values[key]
        if valid_usage:
            report["usage"] = usage
        # A truncated record rejects completion without discarding previously
        # observed usage, which recovery still has to charge to the same run.
        if invalid_stream:
            return report
        threads = [e.get("thread_id") for e in events if e["type"] == "thread.started"]
        if len(threads) == 1 and isinstance(threads[0], str):
            report["thread_id"] = threads[0]
        if any(e["type"] in ("error", "turn.failed") for e in events):
            report["error_reason"] = "failed_turn_evidence"
            return report
        if len(terminals) != 1 or not valid_usage or len(threads) > 1 or events[-1]["type"] != "turn.completed":
            return report
        started, completed, messages = set(), set(), []
        for event in events:
            item = event.get("item", {})
            if not isinstance(item, dict):
                return report
            if event["type"] == "item.started":
                started.add(item.get("id"))
            if event["type"] == "item.completed":
                completed.add(item.get("id"))
                if item.get("type") == "error":
                    report["nonfatal_item_errors"] += 1
                if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                    messages.append(item["text"])
        if started - completed:
            return report
        final = (output / "final.json").read_bytes()
        report["final_sha256"] = hashlib.sha256(final).hexdigest()
        stderr = (output / "stderr.log").read_bytes()
        report["stderr_sha256"] = hashlib.sha256(stderr).hexdigest()
        if not final.strip() or not messages or final.decode("utf-8").rstrip("\r\n") != messages[-1].rstrip("\r\n"):
            report["error_reason"] = "final_message_mismatch"
            return report
        report.update(passed=True, error_reason=None)
        return report
    except (OSError, UnicodeError, ValueError, TypeError):
        return report


def _bounded_process(command, *, cwd, env, output, timeout_seconds, stdin=None, private=False,
                     completion_output=None, completion_grace_seconds=5):
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 7200:
        raise ValueError("timeout_seconds must be an integer in 1..7200")
    if completion_output is not None and private:
        raise ValueError("completion monitoring requires public JSONL files")
    if type(completion_grace_seconds) not in (int, float) or not 0 < completion_grace_seconds <= 5:
        raise ValueError("completion grace must be positive and at most five seconds")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    process, job = None, None
    code, error = 125, None
    captured = (b"", b"")
    forced, natural_exit, observed = False, False, None
    try:
        out_context = nullcontext(subprocess.PIPE) if private else (output / "stdout.jsonl").open("wb")
        err_context = nullcontext(subprocess.PIPE) if private else (output / "stderr.log").open("wb")
        scratch = output / "tmp"
        scratch.mkdir(exist_ok=True)
        # A bounded temporary stdin file avoids a synchronous pipe write hanging
        # before communicate's timeout if a child never consumes a large prompt.
        input_context = tempfile.TemporaryFile(mode="w+b", dir=scratch) if stdin is not None else nullcontext(subprocess.DEVNULL)
        with out_context as stdout, err_context as stderr, input_context as input_stream:
            if stdin is not None:
                input_stream.write(stdin.encode("utf-8"))
                input_stream.seek(0)
            extra = {"start_new_session": True} if os.name != "nt" else {
                "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x4}
            if os.name == "nt":
                job = _WindowsJob()
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=input_stream,
                                       stdout=stdout, stderr=stderr, **extra)
            if job:
                job.attach_and_resume(process)
            try:
                if completion_output is None:
                    captured = process.communicate(timeout=timeout_seconds)
                    code, natural_exit = process.returncode, True
                else:
                    signature, stable_since, grace_since = None, None, None
                    while True:
                        if process.poll() is not None:
                            code, natural_exit = process.returncode, True
                            break
                        now = time.monotonic()
                        if now - started >= timeout_seconds:
                            code, error = 124, "timeout"
                            break
                        evidence = verify_completed_output(completion_output)
                        current = (evidence["events_sha256"], evidence["final_sha256"], evidence["stderr_sha256"])
                        if not evidence["passed"]:
                            signature, stable_since, grace_since = None, None, None
                        elif current != signature:
                            signature, stable_since, grace_since = current, now, None
                        elif grace_since is None and now - stable_since >= .2:
                            grace_since = now
                        elif grace_since is not None and now - grace_since >= completion_grace_seconds:
                            # The finally block closes the whole job/group. Keep
                            # its observed exit code rather than manufacturing 0.
                            forced, observed = True, evidence
                            break
                        time.sleep(min(.1, max(.01, timeout_seconds - (now - started))))
            except subprocess.TimeoutExpired:
                code, error = 124, "timeout"
            except (KeyboardInterrupt, InterruptedError):
                code, error = 130, "interrupted"
    except (OSError, RuntimeError, subprocess.SubprocessError):
        code, error = 125, "executor_unavailable"
    finally:
        if job:
            try:
                job.close()
            except (OSError, TimeoutError):
                code, error = 125, "cleanup_failed"
        if process is not None:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                code, error = 125, "cleanup_failed"
            if forced and error is None:
                code = process.returncode
            if process.stdin:
                process.stdin.close()
            for pipe in (process.stdout, process.stderr):
                if pipe:
                    pipe.close()
    result = {"passed": code == 0 and error is None, "exit_code": code,
              "actual_exit_code": None if process is None else process.returncode,
              "forced_after_model_completion": forced, "natural_exit": natural_exit,
              "elapsed_seconds": round(time.monotonic() - started, 3), "error_reason": error}
    if observed is not None:
        result["completion_evidence"] = observed
    (output / "process.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    if private:
        result["_stdout"], result["_stderr"] = [(value or b"").decode("utf-8", errors="replace") for value in captured]
    return result


def _layout(workspace, output):
    workspace, output = Path(workspace).resolve(strict=True), Path(output).resolve()
    if not workspace.is_dir() or workspace == workspace.parent:
        raise ValueError("workspace must be a non-root directory")
    if output == workspace or workspace in output.parents or output in workspace.parents:
        raise ValueError("controller output and candidate workspace must be disjoint")
    for base in (workspace, *workspace.parents):
        if (base / ".codex" / "config.toml").exists():
            raise ValueError("project Codex configuration is not allowed")
    for name in _PROTECTED:
        p = workspace / name
        if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
            raise ValueError("protected controller path cannot be a link")
    output.mkdir(parents=True, exist_ok=True)
    return workspace, output


def _private_cli(codex, arguments, work, timeout_seconds=30):
    """Configuration/auth metadata only. Raw output never touches disk."""
    work = Path(work)
    env = clean_environment(work)
    output = work / "private-check"
    result = _bounded_process([str(codex), *arguments], cwd=work, env=env,
                              output=output, timeout_seconds=timeout_seconds, private=True)
    return result, result.pop("_stdout"), result.pop("_stderr")


def authentication_status(codex, work, timeout_seconds=30):
    result, stdout, stderr = _private_cli(codex, ["login", "status"], work, timeout_seconds)
    text = stdout + stderr
    ok = result["passed"] and "Logged in using ChatGPT" in text and "API key" not in text
    return {"ready": ok, "mode": "chatgpt" if ok else "unsupported_or_missing",
            "extra_api_billing_authorized": False}


def _mcp_disable_args(codex, work, timeout_seconds=30):
    # An empty TOML table merges; disable every discovered server by name instead.
    result, stdout, _ = _private_cli(codex, ["mcp", "list"], work, timeout_seconds)
    if not result["passed"]:
        raise ValueError("cannot establish MCP inventory")
    # Human listing masks environment values. Never request JSON configuration,
    # which can include literal credentials. Refuse an unfamiliar listing.
    names = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("Name ") or line.startswith("No MCP servers configured"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)\s+.+\s+(?:enabled|disabled)\s+\S+(?:\s+\S+)*", line)
        if not match:
            raise ValueError("unsupported MCP inventory")
        names.append(match.group(1))
    # --ignore-user-config removes original transports. A disabled entry must
    # still be structurally valid, otherwise Codex rejects it before startup.
    return [item for name in names for item in
            ("-c", "mcp_servers." + json.dumps(name)
             + '={enabled=false,command="overnight-disabled-not-executed"}')]


def run_sandbox_command(codex, workspace, command, output, timeout_seconds,
                        readonly=False, read_roots=()):
    workspace, output = _layout(workspace, output)
    if not command or not all(isinstance(part, str) and "\0" not in part for part in command):
        raise ValueError("command must be a nonempty argument list")
    env = clean_environment(output / "tmp")
    argv = [str(codex), *permission_args(workspace, readonly=readonly, read_roots=read_roots, env=env),
            "sandbox", "--permission-profile", "overnight", "--cd", str(workspace), "--", *command]
    return _bounded_process(argv, cwd=workspace, env=env, output=output, timeout_seconds=timeout_seconds)


_PROBE_SOURCE = '''import json, os, pathlib, socket, sys
canary, protected, port, readonly = sys.argv[1:]
r = {}
try:
    pathlib.Path(canary).read_bytes()
    r["outside_read_denied"] = False
except (PermissionError, FileNotFoundError):
    r["outside_read_denied"] = True
try:
    pathlib.Path(protected).write_text("UNEXPECTED")
    r["protected_write_denied"] = False
except (PermissionError, FileNotFoundError):
    r["protected_write_denied"] = True
try:
    pathlib.Path("workspace-write.txt").write_text("synthetic")
    r["workspace_permission_correct"] = readonly == "false"
except (PermissionError, FileNotFoundError):
    r["workspace_permission_correct"] = readonly == "true"
try:
    pathlib.Path(os.environ["TEMP"], "scratch-write.txt").write_text("synthetic")
    r["scratch_write_allowed"] = True
except OSError:
    r["scratch_write_allowed"] = False
try:
    with socket.create_connection(("127.0.0.1", int(port)), timeout=2):
        r["network_denied"] = False
except PermissionError:
    r["network_denied"] = True
except OSError as e:
    r["network_denied"] = getattr(e, "winerror", None) == 10013 or e.errno in (1, 13)
print(json.dumps(r))
sys.exit(0 if all(r.values()) else 1)
'''


def sandbox_probe(codex, workspace, output, timeout_seconds=180):
    """Dedicated synthetic directory, writable and read-only; no credential access."""
    workspace = Path(workspace).resolve()
    if workspace.exists() and any(workspace.iterdir()):
        raise ValueError("sandbox probe requires a new or empty dedicated workspace")
    workspace.mkdir(parents=True, exist_ok=True)
    workspace, output = _layout(workspace, output)
    canary = output / "outside-canary.txt"
    canary.write_text("SYNTHETIC-NOT-A-CREDENTIAL", encoding="utf-8")
    protected = workspace / "automation" / "probe-control.txt"
    protected.parent.mkdir()
    protected.write_text("SYNTHETIC-CONTROL", encoding="utf-8")
    git_control = workspace / ".git" / "probe-control.txt"
    git_control.parent.mkdir()
    git_control.write_text("SYNTHETIC-GIT-CONTROL", encoding="utf-8")
    probe = workspace / "sandbox_probe.py"
    probe.write_text(_PROBE_SOURCE, encoding="utf-8")
    runs = []
    deadline = time.monotonic() + timeout_seconds
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        listener.settimeout(2)
        port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            connection, _ = listener.accept()
            connection.close()
        for readonly, target, name in ((False, protected, "write-control"),
                                      (False, git_control, "write-git"),
                                      (True, protected, "readonly")):
            remaining = min(60, int(deadline - time.monotonic()))
            if remaining < 1:
                runs.append({"passed": False, "exit_code": 124, "error_reason": "timeout", "checks": {}})
                break
            result = run_sandbox_command(codex, workspace,
                [sys.executable, "-B", str(probe), str(canary), str(target), str(port), str(readonly).lower()],
                output / name, remaining, readonly=readonly)
            checks = {}
            for line in (output / name / "stdout.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict) and "outside_read_denied" in parsed:
                        checks = parsed
                except ValueError:
                    pass
            wanted = {"outside_read_denied", "protected_write_denied", "workspace_permission_correct", "scratch_write_allowed", "network_denied"}
            result["checks"] = checks
            result["passed"] = result["passed"] and set(checks) == wanted and all(v is True for v in checks.values())
            runs.append(result)
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
    unchanged = protected.read_text() == "SYNTHETIC-CONTROL" and git_control.read_text() == "SYNTHETIC-GIT-CONTROL"
    report = {"passed": unchanged and all(r["passed"] for r in runs), "checks": runs,
              "protected_unchanged": unchanged, "loopback_control_passed": True, "model_calls": 0}
    (output / "sandbox-probe.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _usage_and_error(stdout, stderr, result):
    usage = {"turns": 0, "input_tokens": 0, "output_tokens": 0}
    failed, invalid = False, False
    errors = []
    for line in stdout.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "turn.completed":
            values = event.get("usage", {})
            if not isinstance(values, dict) or any(type(values.get(k)) is not int or values[k] < 0 for k in ("input_tokens", "output_tokens")):
                invalid = True
            else:
                usage["turns"] += 1
                for k in ("input_tokens", "output_tokens"):
                    usage[k] += values[k]
        if event.get("type") in ("error", "turn.failed"):
            failed = True
            errors.append(json.dumps(event, ensure_ascii=False).lower())
    errors.append(stderr.read_text(encoding="utf-8", errors="replace").lower())
    text = "\n".join(errors)
    reason = result.get("error_reason")
    if reason is None:
        if any(s in text for s in ("usage limit", "quota_exhausted", "quota exceeded", "insufficient_quota")):
            reason = "quota_exhausted"
        elif any(s in text for s in ("rate_limit", "rate limit", "too many requests", "429")):
            reason = "rate_limit"
        elif any(s in text for s in ("401", "unauthorized", "authentication", "not logged in", "login required", "token expired")):
            reason = "authentication"
        elif failed or not result["passed"]:
            reason = "execution_failed"
        elif invalid or not usage["turns"]:
            reason = "usage_unknown"
    return (None if invalid or not usage["turns"] else usage), reason


def run_codex(codex, workspace, output, prompt, timeout_seconds, model="gpt-6.1-sol",
              readonly=False, schema=None):
    workspace, output = _layout(workspace, output)
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > 1_000_000:
        raise ValueError("prompt must contain 1..1000000 bytes")
    if not isinstance(model, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}", model):
        raise ValueError("invalid pinned model")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 7200:
        raise ValueError("invalid timeout")
    if any(output.iterdir()):
        raise ValueError("model invocation output directory must be new/empty")
    started = time.monotonic()

    def remaining(cap):
        return max(0, min(cap, timeout_seconds - int(time.monotonic() - started)))

    def blocked(reason):
        result = {"passed": False, "exit_code": 126, "elapsed_seconds": round(time.monotonic()-started, 3),
                  "usage": None, "error_reason": reason}
        (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    if not authentication_status(str(codex), output / "preflight-tmp", remaining(30))["ready"]:
        if remaining(30) == 0:
            return blocked("timeout")
        return blocked("authentication")
    if remaining(180) == 0:
        return blocked("timeout")
    probe = sandbox_probe(str(codex), output / "probe-workspace", output / "probe-evidence", remaining(180))
    if not probe["passed"]:
        if remaining(30) == 0:
            return blocked("timeout")
        return blocked("sandbox_unavailable")
    env = clean_environment(output / "tmp")
    try:
        if remaining(30) == 0:
            return blocked("timeout")
        mcp = _mcp_disable_args(str(codex), output / "mcp-tmp", remaining(30))
    except ValueError:
        return blocked("configuration_unavailable")
    argv = [str(codex), "--no-daemon", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
            "--json", "--color", "never", "--model", model, "--cd", str(workspace),
            *permission_args(workspace, readonly=readonly, env=env), *mcp,
            "--output-last-message", str(output / "final.json")]
    if schema is not None:
        schema_path = Path(schema).resolve(strict=True)
        if schema_path == workspace or workspace in schema_path.parents:
            raise ValueError("review schema must be controlled outside candidate workspace")
        json.loads(schema_path.read_text(encoding="utf-8"))
        argv += ["--output-schema", str(schema_path)]
    argv += ["-"]
    call_timeout = remaining(7200)
    if call_timeout < 1:
        return blocked("timeout")
    result = _bounded_process(argv, cwd=workspace, env=env, output=output,
                              timeout_seconds=call_timeout, stdin=prompt, completion_output=output)
    evidence = verify_completed_output(output)
    accounting = dict(result)
    accounting["passed"] = (result["passed"] or
        (result.get("forced_after_model_completion") and evidence["passed"] and result.get("error_reason") is None))
    usage, reason = _usage_and_error(output / "stdout.jsonl", output / "stderr.log", accounting)
    if reason is None and not evidence["passed"]:
        reason = evidence["error_reason"]
    result.update(usage=usage, error_reason=reason,
                  passed=bool(accounting["passed"] and evidence["passed"] and reason is None),
                  model_completion_verified=evidence["passed"], completion_evidence=evidence)
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["preflight"])
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--codex", default=shutil.which("codex"))
    args = parser.parse_args()
    if not args.codex:
        parser.error("Codex CLI is required")
    args.output.mkdir(parents=True, exist_ok=True)
    auth = authentication_status(args.codex, args.output / "tmp")
    result = {"auth": auth, "sandbox": sandbox_probe(args.codex, args.workspace, args.output)}
    print(json.dumps(result, indent=2))
    return 0 if auth["ready"] and result["sandbox"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
