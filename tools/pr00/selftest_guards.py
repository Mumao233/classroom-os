"""Synthetic proof that the opt-in Python guard is active before pytest."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch


def rejected(action):
    try:
        action()
    except PermissionError as error:
        assert "PR00 guard:" in str(error), error
    else:
        raise AssertionError("The guard did not reject forbidden I/O")


assert getattr(sys, "classroom_pr00_guard", False), "sitecustomize guard was not loaded"
assert os.environ["HF_HUB_OFFLINE"] == "1"
with socket.socket() as server:
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    with socket.create_connection(server.getsockname(), timeout=2) as client:
        accepted, _ = server.accept()
        with accepted:
            client.sendall(b"synthetic")
            assert accepted.recv(16) == b"synthetic"
with socket.socket() as connection:
    rejected(lambda: connection.connect(("192.0.2.1", 443)))
with socket.socket() as server:
    rejected(lambda: server.bind(("0.0.0.0", 0)))
rejected(lambda: socket.getaddrinfo("pr00-example.invalid", 443))
rejected(lambda: subprocess.Popen(["codex", "--version"]))
rejected(lambda: (Path(os.environ["CLASSROOM_PR00_REPO_ROOT"]) / "config.json").read_bytes())
with tempfile.TemporaryDirectory() as folder:
    synthetic = Path(folder) / "synthetic-key-store.json"
    synthetic.write_text("synthetic-only", encoding="utf-8")
    with patch("os.name", "posix"):
        assert synthetic.read_text(encoding="utf-8") == "synthetic-only"
print("PASS: Python guard, loopback, external/DNS/bind/subprocess/private-path denial; synthetic data only")
