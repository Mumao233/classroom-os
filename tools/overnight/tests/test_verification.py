"""Real synthetic Git checkouts for candidate/evidence isolation; no models."""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.overnight.state import StateError
from tools.overnight.verification import (create_checkout, verify_checkout,
                                         dependency_manifest, verify_dependencies)

ROOT = Path(__file__).resolve().parents[3]
SCRATCH = ROOT / "work/overnight/verification-tests"


class VerificationTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="objects_", dir=SCRATCH))
        self.repo, self.checkout = self.root / "worker", self.root / "snapshot"
        self.repo.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Synthetic Test", GIT_AUTHOR_EMAIL="synthetic@example.invalid",
                        GIT_COMMITTER_NAME="Synthetic Test", GIT_COMMITTER_EMAIL="synthetic@example.invalid")
        self.git("init", "--quiet", "--template=")
        self.git("config", "core.ignorecase", "false")
        self.add("README.md", b"exact committed bytes\n")
        self.base = self.commit()

    def git(self, *args, data=None, repo=None):
        return subprocess.run(["git", "--no-optional-locks", "-C", str(repo or self.repo),
                               "-c", "core.hooksPath=", "-c", "commit.gpgsign=false", *args],
                              input=data, env=self.env, capture_output=True, check=True,
                              timeout=30).stdout

    def add(self, path, body, mode="100644"):
        blob = self.git("hash-object", "-w", "--stdin", data=body).decode().strip()
        self.git("update-index", "--add", "--cacheinfo", mode, blob, path)
        return blob

    def commit(self, parent=None):
        tree = self.git("write-tree").decode().strip()
        args = ["commit-tree", tree]
        if parent:
            args += ["-p", parent]
        head = self.git(*args, data=b"Synthetic verification fixture\n").decode().strip()
        self.git("update-ref", "--no-deref", "HEAD", head)
        return head

    def materialize_worker(self):
        self.git("checkout-index", "--all", "--force")

    def test_exact_blobs_manifest_and_review_diff(self):
        content = b"line1\r\nline2\n\x00\xff"
        blob = self.add("source/payload.bin", content)
        self.add("source/run.sh", b"#!/bin/sh\nexit 0\n", "100755")
        head = self.commit(self.base)
        manifest = create_checkout(self.repo, head, self.checkout)
        verify_checkout(self.checkout, manifest)
        self.assertEqual(manifest["head_sha"], head)
        self.assertEqual(manifest["tree_sha"], self.git("rev-parse", head + "^{tree}").decode().strip())
        self.assertEqual((self.checkout / "source/payload.bin").read_bytes(), content)
        record = next(x for x in manifest["files"] if x["path"] == "source/payload.bin")
        self.assertEqual(record, {"path": "source/payload.bin", "mode": "100644", "blob_sha": blob,
                                  "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)})
        diff = self.git("diff", "--name-only", self.base, head, repo=self.checkout).decode().splitlines()
        self.assertEqual(diff, ["source/payload.bin", "source/run.sh"])
        self.assertFalse((self.checkout / ".git/objects/info/alternates").exists())

    def test_ignored_dependency_passes_worker_but_is_absent_in_snapshot(self):
        self.add(".gitignore", b"helper.py\n")
        self.add("package/__init__.py", b"")
        self.add("package/protocol.py", b"from .helper import VALUE\n")
        head = self.commit(self.base)
        self.materialize_worker()
        (self.repo / "package/helper.py").write_text("VALUE = 42\n", encoding="utf-8")
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all"), b"")
        manifest = create_checkout(self.repo, head, self.checkout)
        command = [sys.executable, "-I", "-B", "-c",
                   "import sys; sys.path.insert(0, '.'); from package.protocol import VALUE; assert VALUE == 42"]
        worker = subprocess.run(command, cwd=self.repo, capture_output=True, timeout=20)
        snapshot = subprocess.run(command, cwd=self.checkout, capture_output=True, timeout=20)
        self.assertEqual(worker.returncode, 0, worker.stderr)
        self.assertNotEqual(snapshot.returncode, 0)
        self.assertIn(b"ModuleNotFoundError", snapshot.stderr)
        self.assertFalse((self.checkout / "package/helper.py").exists())
        verify_checkout(self.checkout, manifest)

    def test_worker_dirty_files_and_index_do_not_change_pinned_snapshot(self):
        self.materialize_worker()
        (self.repo / "README.md").write_bytes(b"working-file drift")
        self.add("README.md", b"staged drift")
        (self.repo / "untracked.py").write_text("raise RuntimeError\n")
        manifest = create_checkout(self.repo, self.base, self.checkout)
        self.assertEqual((self.checkout / "README.md").read_bytes(), b"exact committed bytes\n")
        self.assertFalse((self.checkout / "untracked.py").exists())
        verify_checkout(self.checkout, manifest)

    def test_filters_global_ignore_and_templates_do_not_transform_objects(self):
        self.add(".gitattributes", b"payload.txt filter=poison text eol=crlf\n")
        self.add("payload.txt", b"raw LF only\n")
        head = self.commit(self.base)
        ignore = self.root / "global-ignore"
        ignore.write_text("*\n")
        template = self.root / "template"
        (template / "hooks").mkdir(parents=True)
        (template / "hooks/post-checkout").write_text("unexpected hook\n")
        config = self.root / "global-gitconfig"
        config.write_text('[core]\n autocrlf = true\n excludesfile = ' + ignore.as_posix()
                          + '\n[filter "poison"]\n smudge = command-that-must-never-run\n required = true\n'
                          + '[init]\n templateDir = ' + template.as_posix() + '\n')
        self.git("config", "filter.poison.smudge", "command-that-must-never-run")
        self.git("config", "filter.poison.required", "true")
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config), "GIT_TEMPLATE_DIR": str(template)}):
            manifest = create_checkout(self.repo, head, self.checkout)
            verify_checkout(self.checkout, manifest)
        self.assertEqual((self.checkout / "payload.txt").read_bytes(), b"raw LF only\n")
        self.assertFalse((self.checkout / ".git/hooks/post-checkout").exists())

    def test_ignored_additions_and_empty_extra_directory_are_rejected(self):
        self.add(".gitignore", b"ignored.py\nextra/\n")
        head = self.commit(self.base)
        manifest = create_checkout(self.repo, head, self.checkout)
        ignored = self.checkout / "ignored.py"
        ignored.write_text("hidden = True\n")
        with self.assertRaisesRegex(StateError, "unexpected verification file"):
            verify_checkout(self.checkout, manifest)
        ignored.unlink()
        (self.checkout / "extra").mkdir()
        with self.assertRaisesRegex(StateError, "unexpected verification directory"):
            verify_checkout(self.checkout, manifest)

    def test_changed_missing_and_mode_shifted_source_are_rejected(self):
        manifest = create_checkout(self.repo, self.base, self.checkout)
        readme = self.checkout / "README.md"
        original = readme.read_bytes()
        readme.write_bytes(b"x" * len(original))
        with self.assertRaisesRegex(StateError, "source changed"):
            verify_checkout(self.checkout, manifest)
        readme.unlink()
        with self.assertRaisesRegex(StateError, "files are missing"):
            verify_checkout(self.checkout, manifest)
        readme.write_bytes(original)
        self.git("update-index", "--chmod=+x", "README.md", repo=self.checkout)
        with self.assertRaisesRegex(StateError, "index differs"):
            verify_checkout(self.checkout, manifest)

    def test_existing_destination_and_overlap_are_never_reused(self):
        self.checkout.mkdir()
        with self.assertRaisesRegex(StateError, "reuse"):
            create_checkout(self.repo, self.base, self.checkout)
        with self.assertRaisesRegex(StateError, "disjoint"):
            create_checkout(self.repo, self.base, self.repo / "nested")
        self.assertEqual(list(self.checkout.iterdir()), [])

    def test_symlink_and_gitlink_tree_entries_are_rejected(self):
        for mode, body in (("120000", b"../README.md"), ("160000", None)):
            with self.subTest(mode=mode):
                if body is None:
                    self.git("update-index", "--add", "--cacheinfo", mode, self.base, "special")
                else:
                    self.add("special", body, mode)
                head = self.commit(self.base)
                with self.assertRaisesRegex(StateError, "regular tracked files"):
                    create_checkout(self.repo, head, self.checkout)
                self.assertFalse(self.checkout.exists())
                self.git("update-index", "--force-remove", "special")

    def test_case_colliding_directory_prefixes_are_rejected(self):
        self.add("A/first.py", b"a\n")
        self.add("a/second.py", b"b\n")
        head = self.commit(self.base)
        with self.assertRaisesRegex(StateError, "collision"):
            create_checkout(self.repo, head, self.checkout)
        self.assertFalse(self.checkout.exists())

    def test_windows_reserved_and_traversal_tree_names_are_rejected(self):
        blob = self.git("hash-object", "-w", "--stdin", data=b"synthetic\n").decode().strip()
        for name in ("CON.txt", "..", "bad:name", "back\\slash", ".git", "trailing."):
            with self.subTest(name=name):
                raw = b"100644 " + name.encode() + b"\0" + bytes.fromhex(blob)
                tree = self.git("hash-object", "--literally", "-t", "tree", "-w", "--stdin", data=raw).decode().strip()
                head = self.git("commit-tree", tree, data=b"Unsafe fixture\n").decode().strip()
                with self.assertRaises((StateError, ValueError)):
                    create_checkout(self.repo, head, self.checkout)
                self.assertFalse(self.checkout.exists())

    def test_git_configuration_and_unsafe_metadata_are_rejected(self):
        manifest = create_checkout(self.repo, self.base, self.checkout)
        config = self.checkout / ".git/config"
        original = config.read_bytes()
        config.write_bytes(original + b"\n[diff]\n external = untrusted\n")
        with self.assertRaisesRegex(StateError, "configuration changed"):
            verify_checkout(self.checkout, manifest)
        config.write_bytes(original)
        (self.checkout / ".git/objects/info/alternates").write_text(str(self.repo / ".git/objects"))
        with self.assertRaisesRegex(StateError, "unsafe Git metadata"):
            verify_checkout(self.checkout, manifest)

    def test_missing_git_metadata_is_rejected_as_state_error(self):
        manifest = create_checkout(self.repo, self.base, self.checkout)
        (self.checkout / ".git/config").unlink()
        with self.assertRaisesRegex(StateError, "path is unavailable"):
            verify_checkout(self.checkout, manifest)

    def test_manifest_identity_and_malformed_records_are_rejected(self):
        manifest = create_checkout(self.repo, self.base, self.checkout)
        for key, value in (("head_sha", "0" * 40), ("tree_sha", "0" * 40), ("schema_version", True)):
            changed = dict(manifest, **{key: value})
            with self.subTest(key=key), self.assertRaises(StateError):
                verify_checkout(self.checkout, changed)
        changed = copy.deepcopy(manifest)
        changed["files"][0]["path"] = "../outside"
        with self.assertRaises(StateError):
            verify_checkout(self.checkout, changed)

    def test_dependencies_are_pinned_and_reject_added_removed_or_changed_files(self):
        deps = self.root / "dependencies"
        deps.mkdir()
        item = deps / "library.py"
        item.write_bytes(b"VALUE=1\n")
        manifest = dependency_manifest(deps, [self.repo, self.checkout])
        self.assertEqual(manifest["files"], [{"path": "library.py", "size_bytes": 8,
                                             "sha256": hashlib.sha256(b"VALUE=1\n").hexdigest()}])
        verify_dependencies(deps, manifest, [self.repo, self.checkout])
        item.write_bytes(b"VALUE=2\n")
        with self.assertRaisesRegex(StateError, "manifest changed"):
            verify_dependencies(deps, manifest, [self.repo])
        item.unlink()
        with self.assertRaisesRegex(StateError, "manifest changed"):
            verify_dependencies(deps, manifest, [self.repo])
        item.write_bytes(b"VALUE=1\n")
        (deps / "extra.py").write_text("untracked dependency\n")
        with self.assertRaisesRegex(StateError, "manifest changed"):
            verify_dependencies(deps, manifest, [self.repo])

    def test_dependency_isolation_rejects_overlap_but_allows_external_siblings(self):
        deps = self.root / "source/work/pr01/deps"
        deps.mkdir(parents=True)
        controls = [self.root / "source/web/backend", self.root / "source/tools/overnight",
                    self.root / "source/automation", self.root / "source/work/run"]
        manifest = dependency_manifest(deps, controls)
        verify_dependencies(deps, manifest, controls)
        for forbidden in (deps, deps.parent, deps / "nested"):
            with self.subTest(forbidden=str(forbidden)), self.assertRaisesRegex(StateError, "overlaps"):
                dependency_manifest(deps, [forbidden])

    def test_dependency_classroom_web_shadow_is_rejected_at_any_depth(self):
        deps = self.root / "dependencies"
        (deps / "nested/classroom_web").mkdir(parents=True)
        with self.assertRaisesRegex(StateError, "must not contain classroom_web"):
            dependency_manifest(deps, [self.repo])

    def test_dependency_links_and_reparse_ancestors_are_rejected(self):
        target = self.root / "target"
        (target / "deps").mkdir(parents=True)
        alias = self.root / "alias"
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)],
                           capture_output=True, check=True, timeout=20)
        else:
            alias.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(StateError, "link or reparse"):
            dependency_manifest(alias / "deps", [self.repo])
        with self.assertRaisesRegex(StateError, "link or reparse"):
            dependency_manifest(self.root, [])

    def test_dependency_hardlinked_file_is_rejected(self):
        deps = self.root / "dependencies"
        deps.mkdir()
        target = self.root / "aliased.py"
        target.write_text("shared source\n")
        os.link(target, deps / "linked.py")
        with self.assertRaisesRegex(StateError, "regular and unlinked"):
            dependency_manifest(deps, [self.repo])


if __name__ == "__main__":
    unittest.main()
