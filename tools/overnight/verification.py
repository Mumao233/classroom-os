"""Materialize and verify exact Git objects, never a worker's working files.

The controller keeps the returned manifest outside this checkout and grants the
test/review sandbox read-only access. Scratch and dependencies must be elsewhere.
This module verifies contents; it does not itself install filesystem permissions.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import unicodedata

from .state import StateError

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MODES = {"100644", "100755"}
_DEVICES = re.compile(r"(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?\Z", re.I)


def _git(repo, *args, data=None):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull,
               GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS="1",
               GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repo),
         "-c", "core.hooksPath=" + str(Path(repo) / ".git" / "disabled-hooks"),
         "-c", "core.fsmonitor=false", "-c", "protocol.file.allow=always", *args],
        input=data, capture_output=True, env=env, timeout=120)
    if result.returncode:
        raise StateError("verification Git operation failed: " + args[0])
    return result.stdout


def _plain(path):
    try:
        info = path.lstat()
    except OSError as error:
        raise StateError("verification path is unavailable: " + str(path)) from error
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise StateError("verification path is a link or reparse point: " + str(path))
    return info


def _absolute(path, *, exists):
    path = Path(os.path.abspath(path))
    for parent in reversed((path, *path.parents)):
        if os.path.lexists(parent):
            _plain(parent)
    if exists and not path.is_dir():
        raise StateError("verification directory does not exist")
    return path


def _walk_error(error):
    raise StateError("verification tree cannot be completely read") from error


def _safe_path(name):
    if not isinstance(name, str) or not name or any(ord(c) < 32 for c in name):
        raise StateError("unsafe tracked path")
    if any(c in name for c in '\\:*?"<>|'):
        raise StateError("unsafe tracked path: " + repr(name))
    parts = name.split("/")
    for part in parts:
        if (part in {"", ".", ".."} or part.endswith((".", " "))
                or part.casefold() == ".git" or re.fullmatch(r"git~[0-9]+", part, re.I)
                or _DEVICES.fullmatch(part)):
            raise StateError("unsafe tracked path: " + repr(name))
    return parts


def _paths(records):
    """Check directory prefixes too: A/x and a/y collide on Windows."""
    seen, files = {}, set()
    for item in records:
        parts = _safe_path(item["path"])
        for end in range(1, len(parts) + 1):
            name = "/".join(parts[:end])
            key = unicodedata.normalize("NFC", name).casefold()
            if key in seen and seen[key] != name:
                raise StateError("case or normalization collision in tracked paths")
            seen[key] = name
            if end < len(parts) and key in files:
                raise StateError("tracked file is also a directory")
        if key in files or any(other.startswith(key + "/") for other in seen):
            raise StateError("duplicate or conflicting tracked path")
        files.add(key)


def _tree(repo, head):
    if not isinstance(head, str) or not _SHA.fullmatch(head):
        raise StateError("complete lowercase commit SHA required")
    if _git(repo, "cat-file", "-t", head).strip() != b"commit":
        raise StateError("checkout HEAD is not a commit")
    tree = _git(repo, "rev-parse", head + "^{tree}").decode("ascii").strip()
    if not _SHA.fullmatch(tree):
        raise StateError("invalid tree identity")
    raw = _git(repo, "ls-tree", "-r", "-z", "--full-tree", head)
    records = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, kind, blob = metadata.decode("ascii").split()
        if mode not in _MODES or kind != "blob" or not _SHA.fullmatch(blob):
            raise StateError("only regular tracked files can enter verification")
        records.append({"path": path.decode("utf-8"), "mode": mode, "blob_sha": blob})
    _paths(records)
    return tree, records


def _blobs(repo, records):
    ids = list(dict.fromkeys(item["blob_sha"] for item in records))
    if not ids:
        return {}
    raw = _git(repo, "cat-file", "--batch", data=("\n".join(ids) + "\n").encode("ascii"))
    result, cursor = {}, 0
    for oid in ids:
        end = raw.index(b"\n", cursor)
        header = raw[cursor:end].decode("ascii").split()
        if len(header) != 3 or header[:2] != [oid, "blob"]:
            raise StateError("unexpected Git batch object")
        size = int(header[2])
        if size < 0:
            raise StateError("invalid Git blob size")
        body = raw[end + 1:end + 1 + size]
        cursor = end + 1 + size
        if len(body) != size or raw[cursor:cursor + 1] != b"\n":
            raise StateError("truncated Git batch object")
        cursor += 1
        if hashlib.sha1(b"blob " + str(size).encode("ascii") + b"\0" + body).hexdigest() != oid:
            raise StateError("Git blob does not match its identity")
        result[oid] = body
    if cursor != len(raw):
        raise StateError("unexpected trailing Git batch output")
    return result


def _metadata_safe(path, expected_config):
    gitdir = path / ".git"
    if not stat.S_ISDIR(_plain(gitdir).st_mode):
        raise StateError("verification requires its own Git directory")
    for folder, dirs, files in os.walk(gitdir, followlinks=False, onerror=_walk_error):
        for name in dirs + files:
            info = _plain(Path(folder) / name)
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise StateError("unsafe Git metadata file type")
    for name in ("commondir", "config.worktree", "shallow", "info/grafts", "info/attributes",
                 "objects/info/alternates", "objects/info/http-alternates", "refs/replace"):
        if os.path.lexists(gitdir / name):
            raise StateError("unsafe Git metadata: " + name)
    for name in ("hooks", "disabled-hooks"):
        folder = gitdir / name
        if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
            raise StateError("verification Git hooks must be empty")
    _plain(gitdir / "config")
    if hashlib.sha256((gitdir / "config").read_bytes()).hexdigest() != expected_config:
        raise StateError("verification Git configuration changed")
    if _git(path, "for-each-ref", "--format=%(refname)", "refs/replace").strip():
        raise StateError("verification Git replacement refs are forbidden")


def create_checkout(candidate_repo: Path, head: str, destination: Path) -> dict:
    """Create a fresh independent Git repository and write exact regular blobs.

    No checkout/smudge, ignored-file copy, shared object store, or worker index is
    used. An interrupted destination is retained and must never be reused.
    """
    repo = _absolute(candidate_repo, exists=True)
    destination = _absolute(destination, exists=False)
    if os.path.lexists(destination):
        raise StateError("refusing to reuse verification destination")
    if repo == destination or repo in destination.parents or destination in repo.parents:
        raise StateError("worker and verification checkout must be disjoint")
    tree, records = _tree(repo, head)
    destination.mkdir(parents=True)
    _git(destination, "init", "--quiet", "--template=")
    _git(destination, "config", "core.autocrlf", "false")
    _git(destination, "config", "core.safecrlf", "false")
    _git(destination, "fetch", "--quiet", "--no-tags", "--no-recurse-submodules",
         "--no-write-fetch-head", str(repo), head)
    actual_tree, actual_records = _tree(destination, head)
    if (actual_tree, actual_records) != (tree, records):
        raise StateError("fetched candidate differs from validated objects")
    bodies = _blobs(destination, records)
    files = []
    for record in records:
        body = bodies[record["blob_sha"]]
        path = destination.joinpath(*record["path"].split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as output:
            output.write(body)
        path.chmod(0o755 if record["mode"] == "100755" else 0o644)
        files.append(dict(record, sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body)))
    _git(destination, "read-tree", head)
    _git(destination, "update-ref", "--no-deref", "HEAD", head)
    manifest = {"schema_version": 1, "head_sha": head, "tree_sha": tree, "files": files,
                "git_config_sha256": hashlib.sha256((destination / ".git/config").read_bytes()).hexdigest()}
    verify_checkout(destination, manifest)
    return manifest


def verify_checkout(path: Path, manifest: dict) -> None:
    """Fail closed for missing, changed, additional, or linked source entries."""
    path = _absolute(path, exists=True)
    if (not isinstance(manifest, dict) or set(manifest) !=
            {"schema_version", "head_sha", "tree_sha", "files", "git_config_sha256"}
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or not isinstance(manifest["files"], list)
            or not isinstance(manifest["git_config_sha256"], str)
            or not _DIGEST.fullmatch(manifest["git_config_sha256"])):
        raise StateError("invalid verification manifest")
    records = manifest["files"]
    for item in records:
        if (not isinstance(item, dict) or set(item) != {"path", "mode", "blob_sha", "sha256", "size_bytes"}
                or not isinstance(item["mode"], str) or item["mode"] not in _MODES or not isinstance(item["blob_sha"], str)
                or not _SHA.fullmatch(item["blob_sha"]) or not isinstance(item["sha256"], str)
                or not _DIGEST.fullmatch(item["sha256"]) or type(item["size_bytes"]) is not int
                or item["size_bytes"] < 0):
            raise StateError("invalid verification file record")
    _paths(records)
    _metadata_safe(path, manifest["git_config_sha256"])
    if _git(path, "rev-parse", "HEAD").decode("ascii").strip() != manifest["head_sha"]:
        raise StateError("verification checkout HEAD changed")
    tree, actual_records = _tree(path, manifest["head_sha"])
    expected_records = [{key: item[key] for key in ("path", "mode", "blob_sha")} for item in records]
    if tree != manifest["tree_sha"] or actual_records != expected_records:
        raise StateError("verification Git tree differs from manifest")
    expected_index = b"".join((item["mode"] + " " + item["blob_sha"] + " 0\t" + item["path"] + "\0").encode("utf-8")
                              for item in records)
    if _git(path, "ls-files", "--stage", "-z") != expected_index:
        raise StateError("verification Git index differs from manifest")
    expected = {item["path"]: item for item in records}
    expected_dirs = {"/".join(parts[:i]) for name in expected for parts in [name.split("/")]
                     for i in range(1, len(parts))}
    actual = set()
    for folder, dirs, files in os.walk(path, followlinks=False, onerror=_walk_error):
        current = Path(folder)
        if current == path:
            dirs.remove(".git")
        for name in dirs:
            child = current / name
            if not stat.S_ISDIR(_plain(child).st_mode) or child.relative_to(path).as_posix() not in expected_dirs:
                raise StateError("unexpected verification directory")
        for name in files:
            child = current / name
            info = _plain(child)
            relative = child.relative_to(path).as_posix()
            if not stat.S_ISREG(info.st_mode) or relative not in expected:
                raise StateError("unexpected verification file: " + relative)
            item = expected[relative]
            if (info.st_size != item["size_bytes"]
                    or hashlib.sha256(child.read_bytes()).hexdigest() != item["sha256"]
                    or (os.name != "nt" and bool(info.st_mode & 0o111) != (item["mode"] == "100755"))):
                raise StateError("verification source changed: " + relative)
            actual.add(relative)
    if actual != set(expected):
        raise StateError("verification source files are missing")


def dependency_manifest(path: Path, forbidden_roots) -> dict:
    """Pin external dependency bytes without importing or executing them."""
    path = _absolute(path, exists=True)
    for forbidden in forbidden_roots:
        forbidden = _absolute(forbidden, exists=False)
        if path == forbidden or path in forbidden.parents or forbidden in path.parents:
            raise StateError("dependency root overlaps a forbidden root")
    files = []
    for folder, dirs, names in os.walk(path, followlinks=False, onerror=_walk_error):
        current = Path(folder)
        if current.name.casefold() == "classroom_web":
            raise StateError("dependency root must not contain classroom_web")
        for name in dirs:
            child = current / name
            if not stat.S_ISDIR(_plain(child).st_mode):
                raise StateError("dependency directory is not regular")
            if name.casefold() == "classroom_web":
                raise StateError("dependency root must not contain classroom_web")
        for name in names:
            child = current / name
            info = _plain(child)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise StateError("dependency files must be regular and unlinked")
            digest = hashlib.sha256()
            with child.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            after = _plain(child)
            if (after.st_size, after.st_mtime_ns, after.st_ino) != (info.st_size, info.st_mtime_ns, info.st_ino):
                raise StateError("dependency changed while hashing")
            files.append({"path": child.relative_to(path).as_posix(), "size_bytes": info.st_size,
                          "sha256": digest.hexdigest()})
    files.sort(key=lambda item: item["path"])
    manifest = {"schema_version": 1, "root": str(path), "files": files}
    manifest["sha256"] = hashlib.sha256(json.dumps(manifest, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    return manifest


def verify_dependencies(path: Path, manifest: dict, forbidden_roots) -> None:
    """Recheck the complete dependency manifest and its path isolation."""
    if dependency_manifest(path, forbidden_roots) != manifest:
        raise StateError("verification dependency manifest changed")
