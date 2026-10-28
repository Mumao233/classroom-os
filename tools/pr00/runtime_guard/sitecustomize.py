"""Accidental I/O guard for PR-00 test children, never imported by the app.

Enabled only by the runner's explicit environment and PYTHONPATH. This is a
test-process guard, not an operating-system security sandbox.
"""
import ipaddress
import os
from pathlib import Path
import sys


if os.environ.get("CLASSROOM_PR00_TEST_GUARD") == "1":
    _root = Path(os.environ["CLASSROOM_PR00_REPO_ROOT"]).resolve()
    # Tests intentionally mock os.name to cover non-Windows key-store behavior.
    # Keep the real host path class instead of re-selecting it during open().
    _host_path = type(_root)
    _private_roots = [_root / name for name in ("data", "models", ".codex")]
    _private_files = {_root / name for name in ("config.json", "config.local.json", ".env")}

    def _loopback(host):
        if isinstance(host, bytes):
            host = host.decode("ascii", "strict")
        if str(host).lower().rstrip(".") == "localhost":
            return True
        try:
            address = ipaddress.ip_address(str(host).split("%", 1)[0])
            return address.is_loopback or bool(
                getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback
            )
        except ValueError:
            return False

    def _require_loopback(address):
        if not isinstance(address, tuple) or not address or not _loopback(address[0]):
            raise PermissionError("PR00 guard: only loopback network I/O is allowed")

    def _audit(event, args):
        if event in ("socket.connect", "socket.bind"):
            _require_loopback(args[1])
        elif event == "socket.sendto":
            _require_loopback(args[-1])
        elif event in ("socket.getaddrinfo", "socket.gethostbyname"):
            if not _loopback(args[0]):
                raise PermissionError("PR00 guard: external DNS is disabled")
        elif event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn"):
            # Existing Codex bridge tests mock these boundaries. No actual
            # subprocess (including an installed real Codex CLI) is needed.
            raise PermissionError("PR00 guard: real test subprocesses are disabled")
        elif event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            candidate = _host_path(os.fsdecode(args[0])).resolve()
            if candidate in _private_files or any(
                candidate == directory or directory in candidate.parents
                for directory in _private_roots
            ) or (candidate.parent == _root and candidate.name.startswith(".env.")):
                raise PermissionError("PR00 guard: local private data/configuration is inaccessible")

    sys.addaudithook(_audit)
    sys.classroom_pr00_guard = True
