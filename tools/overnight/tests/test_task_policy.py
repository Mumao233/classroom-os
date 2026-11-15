"""Generic policy tests: synthetic Git objects and mocked CLI/GitHub only."""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.overnight import executor, publish, validation


ROOT = Path(__file__).resolve().parents[3]
BRANCH = "feature/workflow-05a-synthetic"
BASE = "a" * 40
HEAD = "b" * 40
PATCH = "c" * 64


def permission_table(text):
    """Parse the executor's JSON-string inline table on Python 3.10 too."""
    if not isinstance(text, str) or not text.startswith("{"):
        raise ValueError("permission table must start with an opening brace")
    decoder, result, position = json.JSONDecoder(), {}, 1
    if text[position:] == "}":
        return result
    while True:
        key, position = decoder.raw_decode(text, position)
        if not isinstance(key, str) or key in result or text[position:position + 1] != "=":
            raise ValueError("permission table requires unique string keys and equals signs")
        value, position = decoder.raw_decode(text, position + 1)
        if not isinstance(value, str):
            raise ValueError("permission table values must be strings")
        result[key] = value
        delimiter = text[position:position + 1]
        if delimiter == "}" and position == len(text) - 1:
            return result
        if delimiter != ",":
            raise ValueError("permission table has an invalid delimiter or trailing content")
        position += 1


def scope(**changes):
    result = {"add_prefixes": ["web/backend/classroom_web/new_task/", "web/backend/tests/new_task/"],
              "modify_files": ["web/backend/classroom_web/existing.py"],
              "delete_files": ["web/backend/classroom_web/obsolete.py"],
              "deny_prefixes": [], "max_files": 30, "max_bytes": 500000}
    result.update(changes)
    return result


def policy(**changes):
    spec = {"scope": scope(), "branch": BRANCH, "base": {"ref": "main"}}
    spec.update(changes)
    return validation.make_policy("05a", spec)


class ScopeTests(unittest.TestCase):
    def test_canonical_policy_copies_and_hashes_scope(self):
        original = scope(add_prefixes=["tools/pr05a/", "docs/web-alpha/pr05a/"])
        value = policy(scope=original)
        reversed_scope = deepcopy(original)
        reversed_scope["add_prefixes"].reverse()
        self.assertEqual(value, policy(scope=reversed_scope))
        self.assertEqual(value, validation.validate_policy(value, "05a"))
        content = {key: val for key, val in value.items() if key != "sha256"}
        self.assertEqual(value["sha256"], hashlib.sha256(json.dumps(content, sort_keys=True,
            ensure_ascii=False, separators=(",", ":")).encode()).hexdigest())
        original["add_prefixes"].append("tools/other/")
        self.assertNotIn("tools/other/", value["scope"]["add_prefixes"])

    def test_policy_digest_task_and_branch_binding(self):
        value = policy()
        with self.assertRaises(validation.ValidationError):
            validation.validate_policy(value, "06a")
        for field, replacement in (("id", "06a"), ("branch", "feature/workflow-other"),
                ("base_ref", "feature/workflow-base"), ("sha256", "0" * 64)):
            changed = deepcopy(value)
            changed[field] = replacement
            with self.subTest(field=field), self.assertRaises(validation.ValidationError):
                validation.validate_policy(changed)
        changed = deepcopy(value)
        changed["scope"]["max_files"] += 1
        with self.assertRaises(validation.ValidationError):
            validation.validate_policy(changed)

    def test_policy_requires_strict_approved_refs(self):
        for branch in ("main", "feature/web-pr06a-x", "feature/workflow-x..y",
                       "feature/workflow-x.lock", "feature/workflow-x\n"):
            with self.subTest(branch=branch), self.assertRaises(validation.ValidationError):
                policy(branch=branch)
        for ref in (BRANCH, "HEAD", "origin/main", "refs/tags/release"):
            with self.subTest(ref=ref), self.assertRaises(validation.ValidationError):
                policy(base={"ref": ref})
        self.assertEqual(policy(base={"ref": "feature/workflow-predecessor"})["base_ref"],
                         "feature/workflow-predecessor")

    def test_controls_contracts_original_tests_and_broad_parents_rejected(self):
        paths = ["tools/overnight/", "automation/", ".github/", "web/", "web/backend/",
                 "docs/web-alpha/", "docs/web-alpha/architecture/contracts/", "web/backend/migrations/",
                 "static/", "templates/", "tests/", "tools/pr01/"]
        for path in paths:
            with self.subTest(path=path), self.assertRaises(validation.ValidationError):
                validation.validate_scope(scope(add_prefixes=[path]))
        for path in ("web/backend/tests/new_task/test_original.py", "web/frontend/src/view.test.ts",
                     "web/backend/classroom_web/test_old.py", "app.py", "requirements.txt",
                     "tools/new/AGENTS.md", "web/backend/migrations/one.py"):
            for action in ("modify_files", "delete_files"):
                with self.subTest(path=path, action=action), self.assertRaises(validation.ValidationError):
                    validation.validate_scope(scope(**{action: [path]}))

    def test_denials_are_allowed_but_may_not_overlap_grants(self):
        self.assertEqual(validation.validate_scope(scope(deny_prefixes=[".git/", "automation/"]))["deny_prefixes"],
                         [".git/", "automation/"])
        for deny in ("web/backend/classroom_web/new_task/private/", "web/backend/classroom_web/",
                     "web/backend/classroom_web/existing.py"):
            with self.subTest(deny=deny), self.assertRaises(validation.ValidationError):
                validation.validate_scope(scope(deny_prefixes=[deny]))

    def test_malformed_paths_limits_and_fields_rejected(self):
        for path in ("/absolute/", "../outside/", "web/../outside/", "web\\bad/", "web//bad/",
                     "web/COM1/", "web/a:/", "web/a./", "web/a /", "web/quo?te/",
                     "web/e\u0301/", "web/no-slash"):
            with self.subTest(path=path), self.assertRaises(validation.ValidationError):
                validation.validate_scope(scope(add_prefixes=[path]))
        for limit in (True, 0, -1, 3.5, "30"):
            for field in ("max_files", "max_bytes"):
                with self.subTest(field=field, limit=limit), self.assertRaises(validation.ValidationError):
                    validation.validate_scope(scope(**{field: limit}))
        with self.assertRaises(validation.ValidationError):
            validation.validate_scope(scope(extra=True))
        with self.assertRaises(validation.ValidationError):
            validation.validate_scope(scope(add_prefixes=[], modify_files=[], delete_files=[]))
        with self.assertRaises(validation.ValidationError):
            validation.validate_scope(scope(add_prefixes=["web/new/", "web/NEW/"]))
        with self.assertRaises(validation.ValidationError):
            validation.validate_scope(scope(delete_files=["web/backend/classroom_web/EXISTING.py"]))


class SyntheticGitTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / "work/overnight/task-policy-tests"
        root.mkdir(parents=True, exist_ok=True)
        # Immutable object fixtures are retained; no working checkout is executed.
        self.repo = Path(tempfile.mkdtemp(prefix="objects-", dir=root))
        self.env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        self.env.update(publish.git_identity_env(), GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
        self.git("init", "--quiet")
        self.add("AGENTS.md", b"synthetic control\n")
        self.add("web/backend/classroom_web/existing.py", b"original\n")
        self.add("web/backend/classroom_web/obsolete.py", b"obsolete\n")
        self.add("web/backend/classroom_web/new_task/old.py", b"old\n")
        self.add("web/backend/tests/new_task/test_original.py", b"assert True\n")
        self.base = self.commit()
        self.policy = policy()

    def git(self, *args, data=None):
        return subprocess.run(["git", "--no-optional-locks", "-C", str(self.repo),
            "-c", "commit.gpgsign=false", "-c", "core.ignorecase=false", *args],
            input=data, env=self.env, capture_output=True, check=True).stdout

    def add(self, path, body, mode="100644"):
        blob = self.git("hash-object", "-w", "--stdin", data=body).decode().strip()
        self.git("update-index", "--add", "--cacheinfo", mode, blob, path)
        return blob

    def commit(self, *parents):
        tree = self.git("write-tree").decode().strip()
        args = ["commit-tree", tree]
        for parent in parents:
            args.extend(("-p", parent))
        return self.git(*args, data=b"Synthetic task policy fixture\n").decode().strip()

    def verify(self, **kwargs):
        return validation.validate_candidate(self.repo, self.base, self.commit(self.base),
                                             "05a", policy=self.policy, **kwargs)

    def test_exact_add_modify_delete_manifest_and_hash(self):
        self.add("web/backend/classroom_web/new_task/added.py", b"added\n")
        self.add("web/backend/tests/new_task/test_added.py", b"assert True\n")
        self.add("web/backend/classroom_web/existing.py", b"modified\n")
        self.git("update-index", "--force-remove", "web/backend/classroom_web/obsolete.py")
        result = self.verify()
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["policy_sha256"], self.policy["sha256"])
        self.assertEqual({item["status"] for item in result["files"]}, {"A", "M", "D"})
        deleted = next(item for item in result["files"] if item["status"] == "D")
        self.assertEqual(deleted["old_sha256"], hashlib.sha256(b"obsolete\n").hexdigest())
        self.assertEqual(deleted["old_size_bytes"], len(b"obsolete\n"))
        self.assertEqual(result["patch_sha256"], hashlib.sha256(validation.candidate_patch(
            self.repo, self.base, result["head_sha"])).hexdigest())

    def test_existing_file_in_add_directory_is_not_modifiable(self):
        self.add("web/backend/classroom_web/new_task/old.py", b"unapproved replacement\n")
        self.assertFalse(self.verify()["valid"])

    def test_explicit_modify_does_not_allow_deletion_or_addition(self):
        self.git("update-index", "--force-remove", "web/backend/classroom_web/existing.py")
        self.assertFalse(self.verify()["valid"])
        self.git("read-tree", self.base)
        self.policy = policy(scope=scope(modify_files=["web/backend/classroom_web/new.py"]))
        self.add("web/backend/classroom_web/new.py", b"unapproved new\n")
        self.assertFalse(self.verify()["valid"])

    def test_original_tests_and_protected_added_file_rejected(self):
        for path in ("web/backend/tests/new_task/test_original.py", "web/backend/classroom_web/new_task/AGENTS.md",
                     "web/backend/classroom_web/new_task/.env", "tools/overnight/evil.py",
                     "web/backend/migrations/new.py", "outside/new.py"):
            with self.subTest(path=path):
                self.git("read-tree", self.base)
                self.add(path, b"unsafe\n")
                self.assertFalse(self.verify()["valid"])

    def test_symlink_and_case_colliding_addition_rejected(self):
        self.add("web/backend/classroom_web/new_task/link.py", b"../../existing.py", "120000")
        self.assertFalse(self.verify()["valid"])
        self.git("read-tree", self.base)
        self.add("web/backend/classroom_web/new_task/OLD.py", b"case alias\n")
        self.assertFalse(self.verify()["valid"])

    def test_scope_limits_cannot_be_overridden_by_call_arguments(self):
        self.policy = policy(scope=scope(max_bytes=2))
        self.add("web/backend/classroom_web/new_task/added.py", b"123\n")
        result = self.verify(max_bytes=500000)
        self.assertFalse(result["valid"])
        self.assertEqual(result["limits"]["max_bytes"], 2)

    def test_modified_task_new_test_still_is_addition_against_base(self):
        path = "web/backend/tests/new_task/test_added.py"
        self.add(path, b"assert 1\n")
        previous = self.commit(self.base)
        self.add(path, b"assert 2\n")
        head = self.commit(previous)
        result = validation.validate_candidate(self.repo, self.base, head, "05a", policy=self.policy)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["files"][0]["status"], "A")


class ExecutionPolicyTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / "work/overnight/task-policy-tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="execution-", dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "candidate"
        self.workspace.mkdir()
        self.output = self.root / "controller"
        self.new_dir = self.workspace / "web/new"
        self.new_dir.mkdir(parents=True)
        self.old_file = self.new_dir / "old.py"
        self.old_file.write_text("synthetic original")
        self.env = executor.clean_environment(self.output / "tmp", {"PATH": ""})

    def permissions(self, **kwargs):
        args = executor.permission_args(self.workspace, env=self.env, **kwargs)
        entry = next(arg for arg in args if arg.startswith("permissions.overnight.filesystem="))
        return permission_table(entry.split("=", 1)[1])

    def test_explicit_writes_preserve_readonly_workspace_and_existing_files(self):
        fs = self.permissions(write_paths=[self.new_dir])
        self.assertEqual(fs[self.workspace.as_posix()], "read")
        self.assertEqual(fs[self.new_dir.as_posix()], "write")
        self.assertEqual(fs[self.old_file.as_posix()], "read")
        self.assertEqual(fs[(self.workspace / "tools/overnight").as_posix()], "read")
        self.assertEqual(fs[(self.output / "tmp").as_posix()], "write")
        self.assertNotIn(self.output.as_posix(), fs)
        fs = self.permissions(write_paths=[self.new_dir, self.old_file])
        self.assertEqual(fs[self.old_file.as_posix()], "write")

    def test_legacy_default_permissions_unchanged(self):
        self.assertEqual(self.permissions()[self.workspace.as_posix()], "write")
        self.assertEqual(self.permissions(readonly=True)[self.workspace.as_posix()], "read")
        self.assertEqual(self.permissions(write_paths=[])[self.workspace.as_posix()], "read")
        escaped = {'path with "quote", comma=and\\slash': "read"}
        self.assertEqual(permission_table(executor._toml_table(escaped)), escaped)
        for malformed in ('{"key":"read"}', '{"key"="read",}', '{"key"="read"}trailing',
                          '{"key"="read","key"="write"}', '{"key"=true}', '{1="read"}',
                          '{"key"="read"', '{"key"="read" "other"="write"}'):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                permission_table(malformed)

    def test_outside_root_protected_and_duplicate_writes_rejected(self):
        for paths in ([self.root], [self.workspace], ["../outside"], ["web/../other"],
                      ["tools/overnight/new.py"], ["web/backend"], ["app.py"],
                      [self.old_file, self.old_file]):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                self.permissions(write_paths=paths)
        with self.assertRaises(ValueError):
            self.permissions(write_paths=[self.new_dir], readonly=True)

    def test_hardlinks_cannot_be_granted_write_permission(self):
        alias = self.new_dir / "alias.py"
        os.link(self.old_file, alias)
        with self.assertRaises(ValueError):
            self.permissions(write_paths=[alias])
        fs = self.permissions(write_paths=[self.new_dir])
        self.assertEqual(fs[alias.as_posix()], "read")
        self.assertEqual(fs[self.old_file.as_posix()], "read")

    def test_symlink_descendants_rejected_without_following_them(self):
        link = self.new_dir / "linked"
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("host cannot create synthetic symlink: " + str(exc))
        with self.assertRaises(ValueError):
            self.permissions(write_paths=[self.new_dir])
        with self.assertRaises(ValueError):
            self.permissions(write_paths=[link / "escaped.py"])

    def _invoke(self, output, **options):
        def completed(command, **kwargs):
            target = kwargs["output"]
            events = [{"type": "item.completed", "item": {"type": "agent_message", "text": "SYNTHETIC FINAL"}},
                      {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}}]
            (target / "stdout.jsonl").write_text("\n".join(json.dumps(event) for event in events) + "\n")
            (target / "stderr.log").write_text("")
            (target / "final.json").write_text("SYNTHETIC FINAL")
            return {"passed": True, "exit_code": 0, "elapsed_seconds": .01, "error_reason": None}
        with patch.object(executor, "authentication_status", return_value={"ready": True}), \
             patch.object(executor, "sandbox_probe", return_value={"passed": True}), \
             patch.object(executor, "_mcp_disable_args", return_value=[]), \
             patch.object(executor, "_bounded_process", side_effect=completed) as run:
            result = executor.run_codex("synthetic-codex", self.workspace, output, "synthetic prompt", 30, **options)
        return result, run.call_args.args[0]

    def test_reasoning_effort_read_roots_and_write_parameters_recorded(self):
        deps = self.root / "fixed-deps"
        deps.mkdir()
        result, argv = self._invoke(self.root / "invoke", reasoning_effort="ultra",
                                   write_paths=[self.new_dir], read_roots=[deps])
        self.assertTrue(result["passed"], result)
        self.assertIn('model_reasoning_effort="ultra"', argv)
        self.assertEqual(result["execution_parameters"], {"model": "gpt-6.1-sol", "reasoning_effort": "ultra",
            "write_paths": ["web/new"], "read_roots": [str(deps.resolve())]})
        fs_arg = next(arg for arg in argv if arg.startswith("permissions.overnight.filesystem="))
        self.assertIn(json.dumps(deps.as_posix()) + '="read"', fs_arg)

    def test_legacy_invocation_omits_reasoning_and_new_receipt_fields(self):
        result, argv = self._invoke(self.root / "legacy-invoke")
        self.assertTrue(result["passed"], result)
        self.assertFalse(any("model_reasoning_effort" in arg for arg in argv))
        self.assertNotIn("execution_parameters", result)

    def test_unknown_model_effort_and_readonly_conflict_fail_before_auth(self):
        for index, options in enumerate(({"model": "unknown", "reasoning_effort": "high"},
                {"reasoning_effort": "extreme"}, {"reasoning_effort": True},
                {"readonly": True, "write_paths": [self.new_dir]})):
            with self.subTest(options=options), patch.object(executor, "authentication_status") as auth:
                with self.assertRaises(ValueError):
                    executor.run_codex("synthetic", self.workspace, self.root / ("reject-" + str(index)),
                                       "synthetic", 30, **options)
                auth.assert_not_called()

    def test_failed_preflight_retains_requested_parameters_without_model_call(self):
        with patch.object(executor, "authentication_status", return_value={"ready": False}), \
             patch.object(executor, "_bounded_process") as run:
            result = executor.run_codex("synthetic", self.workspace, self.root / "auth-fail", "synthetic", 30,
                                        reasoning_effort="high", write_paths=[])
        run.assert_not_called()
        self.assertEqual(result["error_reason"], "authentication")
        self.assertEqual(result["execution_parameters"]["reasoning_effort"], "high")


class FakeGitHub:
    def __init__(self):
        self.branch = None
        self.base = BASE
        self.pr = None
        self.requests = []

    def record(self):
        return {"number": 1, "state": "open", "draft": True, "merged_at": None,
            "html_url": "https://github.com/" + publish.REPOSITORY + "/pull/1",
            "user": {"login": publish.OWNER, "id": publish.USER_ID},
            "head": {"ref": BRANCH, "sha": HEAD, "repo": {"full_name": publish.REPOSITORY}},
            "base": {"ref": "main", "sha": BASE, "repo": {"full_name": publish.REPOSITORY}}}

    def verify_user(self):
        self.requests.append(("GET", "/user"))

    def _git_auth_env(self):
        return {"SYNTHETIC": "NO CREDENTIALS"}

    def get(self, path):
        self.requests.append(("GET", path))
        if path == publish.REPO_API + "/git/ref/heads/main":
            return {"object": {"type": "commit", "sha": self.base}}
        if path == publish.REPO_API + "/git/ref/heads/" + BRANCH:
            if self.branch is None:
                raise publish.GitHubError(404)
            return {"object": {"type": "commit", "sha": self.branch}}
        if path.startswith(publish.REPO_API + "/pulls?"):
            return [] if self.pr is None else [deepcopy(self.pr)]
        raise AssertionError("unexpected synthetic endpoint")

    def post(self, path, payload):
        self.requests.append(("POST", path, payload))
        self.pr = self.record()
        return deepcopy(self.pr)


class GenericPublisherTests(unittest.TestCase):
    def setUp(self):
        self.policy = policy()
        self.client = FakeGitHub()
        self.report = {"valid": True, "base_sha": BASE, "head_sha": HEAD, "patch_sha256": PATCH,
                       "policy_sha256": self.policy["sha256"]}
        self.calls = []
        git_patch = patch.object(publish, "_run_git", side_effect=self.git)
        validation_patch = patch.object(publish, "validate_candidate", side_effect=lambda *a, **k: deepcopy(self.report))
        git_patch.start()
        self.validator = validation_patch.start()
        self.addCleanup(git_patch.stop)
        self.addCleanup(validation_patch.stop)

    def git(self, repo, *args, **kwargs):
        self.calls.append((args, kwargs))
        if args[:2] == ("remote", "get-url"):
            return (publish.REMOTE_URL + "\n").encode()
        if args[0] == "rev-list":
            return (HEAD + "\n").encode()
        if args[:2] == ("cat-file", "-p"):
            identity = publish.OWNER + " <" + publish.EMAIL + "> 123 +0000"
            return ("tree " + "d" * 40 + "\nauthor " + identity + "\ncommitter " + identity +
                    "\n\nSynthetic candidate\n").encode()
        if "push" in args:
            self.client.branch = HEAD
            return b"synthetic"
        raise AssertionError("unexpected synthetic Git invocation")

    def call(self, **changes):
        args = {"task": "05a", "branch": BRANCH, "base_ref": "main", "base_sha": BASE, "head_sha": HEAD,
                "title": "Synthetic generic task", "body": "Synthetic evidence", "expected_patch_sha256": PATCH,
                "policy": self.policy}
        args.update(changes)
        return publish.Publisher(ROOT, self.client).publish(**args)

    def test_generic_publication_revalidates_policy_and_is_draft_only(self):
        receipt = self.call()
        self.assertTrue(receipt["draft"])
        self.assertEqual(receipt["policy_sha256"], self.policy["sha256"])
        self.assertEqual(self.validator.call_args.kwargs["policy"], self.policy)
        pushes = [args for args, kwargs in self.calls if "push" in args]
        self.assertEqual(len(pushes), 1)
        self.assertIn(HEAD + ":refs/heads/" + BRANCH, pushes[0])
        self.assertFalse(any(arg.startswith("--force") for arg in pushes[0]))
        posts = [entry for entry in self.client.requests if entry[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0][2]["draft"])
        self.assertFalse(posts[0][2]["maintainer_can_modify"])

    def test_wrong_policy_task_branch_base_or_missing_patch_rejected_before_git(self):
        changed = deepcopy(self.policy)
        changed["sha256"] = "0" * 64
        for options in ({"policy": changed}, {"task": "06a"}, {"branch": "feature/workflow-other"},
                        {"base_ref": "feature/workflow-base"}, {"expected_patch_sha256": None}):
            with self.subTest(options=options), self.assertRaises(publish.PublishError):
                self.call(**options)
        self.assertEqual(self.calls, [])
        self.validator.assert_not_called()

    def test_validation_policy_or_patch_mismatch_prevents_push(self):
        for field in ("policy_sha256", "patch_sha256"):
            previous = self.report[field]
            self.report[field] = "0" * 64
            with self.subTest(field=field), self.assertRaises(publish.PublishError):
                self.call()
            self.report[field] = previous
        self.assertFalse(any("push" in args for args, kwargs in self.calls))

    def test_existing_branch_and_pr_reconciled_without_writes(self):
        self.client.branch = HEAD
        self.client.pr = self.client.record()
        receipt = self.call()
        self.assertEqual(receipt["status"], "existing")
        self.assertFalse(any("push" in args for args, kwargs in self.calls))
        self.assertFalse(any(entry[0] == "POST" for entry in self.client.requests))

    def test_existing_branch_collision_never_forced(self):
        self.client.branch = "d" * 40
        with self.assertRaises(publish.PublishError):
            self.call()
        self.assertFalse(any("push" in args for args, kwargs in self.calls))

    def test_legacy_entry_does_not_accept_workflow_branch_without_policy(self):
        with self.assertRaises(publish.PublishError):
            self.call(task="06a", policy=None)
        with self.assertRaises(publish.PublishError):
            self.call(task="06a", policy=None, branch="feature/web-pr06a-synthetic", base_ref=BRANCH)
        self.assertEqual(self.calls, [])

    def test_scoped_http_client_accepts_workflow_only_as_draft_payload(self):
        payload = {"title": "synthetic", "body": "synthetic", "head": BRANCH, "base": "main",
                   "draft": True, "maintainer_can_modify": False}
        response = io.BytesIO(b"{}")
        with patch.object(publish.request.OpenerDirector, "open", return_value=response) as opened:
            client = publish.GitHubClient("SYNTHETIC-NONCREDENTIAL")
            self.assertEqual(client.post(publish.REPO_API + "/pulls", payload), {})
            self.assertEqual(opened.call_args.args[0].method, "POST")
        payload["draft"] = False
        with self.assertRaises(publish.PublishError):
            client.post(publish.REPO_API + "/pulls", payload)


if __name__ == "__main__":
    unittest.main()
