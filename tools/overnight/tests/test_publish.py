"""Publisher tests use fake GitHub and Git; no credential, push or PR is real."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib import error

from tools.overnight.publish import (EMAIL, OWNER, REPOSITORY, REPO_API, REMOTE_URL, USER_ID,
    GitHubClient, GitHubError, PublishError, Publisher, _NoRedirect, git_identity_env, verify_remote)


ROOT = Path(__file__).resolve().parents[3]
BASE = "a" * 40
HEAD = "b" * 40
PATCH_HASH = "c" * 64
BRANCH = "feature/web-pr06a-synthetic"


def pr_record():
    return {"number": 19, "state": "open", "draft": True, "merged_at": None,
        "html_url": "https://github.com/" + REPOSITORY + "/pull/19",
        "user": {"login": OWNER, "id": USER_ID},
        "head": {"ref": BRANCH, "sha": HEAD, "repo": {"full_name": REPOSITORY}},
        "base": {"ref": "main", "sha": BASE, "repo": {"full_name": REPOSITORY}}}


class FakeGitHub:
    def __init__(self, existing_branch=False, existing_pr=False):
        self.branch = HEAD if existing_branch else None
        self.base = BASE
        self.pr = pr_record() if existing_pr else None
        self.requests = []
        self.user = {"login": OWNER, "id": USER_ID}

    def verify_user(self):
        self.requests.append(("GET", "/user"))
        if self.user != {"login": OWNER, "id": USER_ID}:
            raise PublishError("wrong personal account")
        return self.user

    def _git_auth_env(self):
        return {"SYNTHETIC_MEMORY_ONLY": "synthetic-no-credential"}

    def get(self, path):
        self.requests.append(("GET", path))
        if path == REPO_API + "/git/ref/heads/main":
            return {"object": {"type": "commit", "sha": self.base}}
        if path == REPO_API + "/git/ref/heads/" + BRANCH:
            if self.branch is None:
                raise GitHubError(404)
            return {"object": {"type": "commit", "sha": self.branch}}
        if path.startswith(REPO_API + "/pulls?"):
            return [] if self.pr is None else [deepcopy(self.pr)]
        raise AssertionError("unexpected fake endpoint")

    def post(self, path, payload):
        self.requests.append(("POST", path, payload))
        if path != REPO_API + "/pulls":
            raise AssertionError("non-PR mutation")
        self.pr = pr_record()
        return deepcopy(self.pr)


class PublisherTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / "work/overnight/tests"
        root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="publish-", dir=root)
        self.repo = Path(self.tmp.name)
        self.client = FakeGitHub()
        self.git_calls = []
        self.commit_body = b"Implement approved synthetic protocol tests\n"
        self.author = self.committer = OWNER
        self.remote = REMOTE_URL
        self.report = {"valid": True, "base_sha": BASE, "head_sha": HEAD, "patch_sha256": PATCH_HASH}
        self.git_patch = patch("tools.overnight.publish._run_git", side_effect=self.git)
        self.validation_patch = patch("tools.overnight.publish.validate_candidate", side_effect=lambda *a, **k: deepcopy(self.report))
        self.git_patch.start()
        self.validation_patch.start()

    def tearDown(self):
        self.git_patch.stop()
        self.validation_patch.stop()
        self.tmp.cleanup()

    def git(self, repo, *args, **kwargs):
        self.git_calls.append((args, kwargs))
        if args[:2] == ("remote", "get-url"):
            return (self.remote + "\n").encode()
        if args[0] == "rev-list":
            return (HEAD + "\n").encode()
        if args[:2] == ("cat-file", "-p"):
            return (f"tree {'d'*40}\nparent {BASE}\nauthor {self.author} <{EMAIL}> 123 +0000\n"
                    f"committer {self.committer} <{EMAIL}> 123 +0000\n\n").encode() + self.commit_body
        if "push" in args:
            self.client.branch = HEAD
            return b"synthetic success"
        raise AssertionError("unexpected fake Git command")

    def publish(self, **changes):
        args = dict(task="06a", branch=BRANCH, base_ref="main", base_sha=BASE, head_sha=HEAD,
                    title="PR-06a synthetic", body="Synthetic test evidence", expected_patch_sha256=PATCH_HASH)
        args.update(changes)
        return Publisher(self.repo, self.client).publish(**args)

    def assert_no_push(self):
        self.assertFalse(any("push" in args for args, kwargs in self.git_calls))

    def test_new_pr_uses_personal_identity_exact_sha_and_draft_only(self):
        receipt = self.publish()
        self.assertEqual(receipt["status"], "created")
        self.assertEqual(receipt["author"], OWNER)
        self.assertEqual(receipt["head_sha"], HEAD)
        self.assertEqual(receipt["patch_sha256"], PATCH_HASH)
        self.assertTrue(receipt["draft"])
        self.assertEqual(self.client.requests[0], ("GET", "/user"))
        pushes = [args for args, kwargs in self.git_calls if "push" in args]
        self.assertEqual(len(pushes), 1)
        self.assertIn(HEAD + ":refs/heads/" + BRANCH, pushes[0])
        self.assertIn("push.followTags=false", pushes[0])
        self.assertIn("push.recurseSubmodules=no", pushes[0])
        self.assertFalse(any(arg.startswith("--force") for arg in pushes[0]))
        posts = [entry for entry in self.client.requests if entry[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][1], REPO_API + "/pulls")
        self.assertTrue(posts[0][2]["draft"])
        self.assertFalse(posts[0][2]["maintainer_can_modify"])

    def test_retry_reconciles_existing_pr_without_second_push_or_post(self):
        self.client = FakeGitHub(existing_branch=True, existing_pr=True)
        receipt = self.publish()
        self.assertEqual(receipt["status"], "existing")
        self.assert_no_push()
        self.assertFalse(any(entry[0] == "POST" for entry in self.client.requests))

    def test_existing_pr_rejects_bot_non_draft_wrong_head_wrong_base_and_closed(self):
        for change in ("bot", "not-draft", "head", "base", "closed", "fork"):
            with self.subTest(change=change):
                self.client = FakeGitHub(existing_branch=True, existing_pr=True)
                if change == "bot":
                    self.client.pr["user"]["login"] = "codex[bot]"
                elif change == "not-draft":
                    self.client.pr["draft"] = False
                elif change == "head":
                    self.client.pr["head"]["sha"] = "d" * 40
                elif change == "base":
                    self.client.pr["base"]["sha"] = "d" * 40
                elif change == "closed":
                    self.client.pr["state"] = "closed"
                else:
                    self.client.pr["head"]["repo"]["full_name"] = "other/classroom-os"
                with self.assertRaises(PublishError):
                    self.publish()
                self.assert_no_push()

    def test_unsafe_refs_and_out_of_scope_task_never_reach_git(self):
        for changes in ({"branch": "main"}, {"branch": "refs/tags/v1"}, {"branch": "feature/web-pr06a-x..y"},
                        {"branch": "feature/web-pr06a-x.lock"}, {"branch": "feature/web-pr06a-x\nmain"},
                        {"task": "03b"}, {"base_ref": "refs/tags/v1"}, {"head_sha": "HEAD"}):
            with self.subTest(changes=changes), self.assertRaises(PublishError):
                self.publish(**changes)
        self.assertEqual(self.git_calls, [])

    def test_validation_hash_and_personal_commit_identity_are_required(self):
        for problem in ("validation", "hash", "author", "committer", "coauthor"):
            with self.subTest(problem=problem):
                self.report["valid"] = problem != "validation"
                self.report["patch_sha256"] = "d" * 64 if problem == "hash" else PATCH_HASH
                self.author = "Codex" if problem == "author" else OWNER
                self.committer = "Codex" if problem == "committer" else OWNER
                self.commit_body = b"Co-authored-by: Codex <synthetic@example.test>" if problem == "coauthor" else b"ordinary"
                with self.assertRaises(PublishError):
                    self.publish()
                self.assert_no_push()

    def test_remote_account_base_drift_and_destination_collision_fail_closed(self):
        self.remote = "https://github.com/Mumao233/personal-web.git"
        with self.assertRaises(PublishError):
            self.publish()
        self.remote = REMOTE_URL
        self.client.user = {"login": "other", "id": 1}
        with self.assertRaises(PublishError):
            self.publish()
        self.client.user = {"login": OWNER, "id": USER_ID}
        self.client.base = "d" * 40
        with self.assertRaises(PublishError):
            self.publish()
        self.client.base, self.client.branch = BASE, "d" * 40
        with self.assertRaises(PublishError):
            self.publish()
        self.assert_no_push()

    def test_personal_identity_helper_has_both_author_and_committer(self):
        identity = git_identity_env()
        self.assertEqual(identity, {"GIT_AUTHOR_NAME": OWNER, "GIT_AUTHOR_EMAIL": EMAIL,
            "GIT_COMMITTER_NAME": OWNER, "GIT_COMMITTER_EMAIL": EMAIL})


class GitHubClientTests(unittest.TestCase):
    def test_artifact_redirects_never_receive_authorization(self):
        class Response(io.BytesIO):
            headers = {"Content-Length": "8"}
        first = "https://productionresultssa1.blob.core.windows.net/actions/request.zip?sig=synthetic"
        second = "https://results.actions.githubusercontent.com/request.zip?sig=synthetic"
        opener = Mock()
        opener.open.side_effect = [
            error.HTTPError("https://api.github.com", 302, "redirect", {"Location": first}, None),
            error.HTTPError(first, 302, "redirect", {"Location": second}, None), Response(b"fake-zip")]
        client = GitHubClient("synthetic-token", opener=opener)
        self.assertEqual(client.get_bytes(REPO_API + "/actions/artifacts/123/zip", 32768), b"fake-zip")
        requests = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer synthetic-token")
        for redirected in requests[1:]:
            self.assertIsNone(redirected.get_header("Authorization"))
            self.assertNotIn("synthetic-token", repr(redirected.header_items()))

    def test_artifact_size_limit_works_without_trusting_content_length(self):
        class Response(io.BytesIO):
            headers = {}
        for response in (Response(b"x" * 32769), Response(b"x" * 11)):
            opener = Mock()
            opener.open.return_value = response
            client = GitHubClient("synthetic-token", opener=opener)
            with self.assertRaises(PublishError):
                client.get_bytes(REPO_API + "/actions/artifacts/123/zip", max_bytes=10)
        response = Response(b"small")
        response.headers = {"Content-Length": "32769"}
        opener.open.return_value = response
        with self.assertRaises(PublishError):
            client.get_bytes(REPO_API + "/actions/artifacts/123/zip")
        with self.assertRaises(PublishError):
            client.get_bytes(REPO_API + "/actions/artifacts/123/zip", 32769)

    def test_artifact_rejects_foreign_urls_unsafe_redirects_and_hop_loops(self):
        targets = ["http://productionresultssa1.blob.core.windows.net/x",
            "https://productionresultssa1.blob.core.windows.net.evil.test/x",
            "https://evil.test/x", "https://user:secret@x.blob.core.windows.net/x",
            "https://x.blob.core.windows.net:444/x", "https://blob.core.windows.net/x",
            "https://api.github.com/user", "https://x.actions.githubusercontent.com/x\nsecret"]
        for location in targets:
            opener = Mock()
            opener.open.side_effect = error.HTTPError("https://api.github.com", 302, "redirect", {"Location": location}, None)
            client = GitHubClient("synthetic-token", opener=opener)
            with self.subTest(location=location), self.assertRaises(PublishError):
                client.get_bytes(REPO_API + "/actions/artifacts/123/zip")
            self.assertEqual(opener.open.call_count, 1)
        opener = Mock()
        opener.open.side_effect = error.HTTPError("https://api.github.com", 302, "redirect",
            {"Location": "https://x.blob.core.windows.net/x"}, None)
        client = GitHubClient("synthetic-token", opener=opener)
        with self.assertRaises(PublishError):
            client.get_bytes(REPO_API + "/actions/artifacts/123/zip")
        self.assertEqual(opener.open.call_count, 3)
        for path in ("https://x.blob.core.windows.net/x", "/repos/other/repo/actions/artifacts/1/zip",
                     REPO_API + "/actions/artifacts/123/zip?redirect=1"):
            with self.assertRaises(PublishError):
                client.get_bytes(path)

    def test_scoped_metadata_get_and_no_redirects(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps({"login": OWNER, "id": USER_ID}).encode())
        client = GitHubClient("synthetic-token", opener=opener)
        self.assertEqual(client.verify_user(), {"login": OWNER, "id": USER_ID})
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.github.com/user")
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test"))
        for path in ("https://other.test", "//other.test", "/repos/other/repo", REPO_API + "/../secrets",
                     REPO_API + "/%2e%2e/secrets"):
            with self.subTest(path=path), self.assertRaises(PublishError):
                client.get(path)

    def test_post_cannot_merge_release_deploy_or_create_non_draft(self):
        opener = Mock()
        client = GitHubClient("synthetic-token", opener=opener)
        for path in (REPO_API + "/pulls/1/merge", REPO_API + "/releases", REPO_API + "/deployments"):
            with self.assertRaises(PublishError):
                client.post(path, {})
        with self.assertRaises(PublishError):
            client.post(REPO_API + "/pulls", {"draft": False})
        opener.open.assert_not_called()

    def test_network_failure_does_not_expose_token_body_or_headers(self):
        opener = Mock()
        opener.open.side_effect = error.HTTPError("https://api.github.com/user", 401,
            "synthetic-token", {"Authorization": "synthetic-token"}, io.BytesIO(b"synthetic-token"))
        client = GitHubClient("synthetic-token", opener=opener)
        with self.assertRaises(GitHubError) as caught:
            client.get("/user")
        self.assertEqual(caught.exception.status, 401)
        self.assertNotIn("synthetic-token", str(caught.exception))

    def test_credential_fill_is_memory_only_and_verifies_the_account(self):
        with (patch("tools.overnight.publish.verify_remote") as remote,
              patch("tools.overnight.publish._run_git",
                    return_value=b"protocol=https\nhost=github.com\nusername=synthetic\npassword=synthetic-token\n") as git,
              patch.object(GitHubClient, "verify_user", return_value={"login": OWNER, "id": USER_ID}) as user):
            client = GitHubClient.from_git_credentials(ROOT)
        remote.assert_called_once()
        user.assert_called_once()
        self.assertEqual(git.call_args.kwargs["input_bytes"], b"protocol=https\nhost=github.com\n\n")
        self.assertNotIn("synthetic-token", repr(git.call_args.args))
        self.assertNotIn("synthetic-token", repr(client))


if __name__ == "__main__":
    unittest.main()
