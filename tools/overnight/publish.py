"""Trusted publication of a validated, immutable candidate as a personal Draft PR.

No candidate code or hooks are executed here. Credentials stay in memory and
GitHub error bodies/headers are never included in errors or receipts. A failed
or interrupted POST is reconciled by querying the same head/base on the next
invocation; it is never blindly retried.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess
from urllib import error, parse, request

try:
    from .validation import ValidationError, WORKFLOW_BRANCH, validate_candidate, validate_policy
except ImportError:
    from validation import ValidationError, WORKFLOW_BRANCH, validate_candidate, validate_policy


OWNER = "Mumao233"
USER_ID = 325669781
REPOSITORY = "Mumao233/classroom-os"
REPO_API = "/repos/" + REPOSITORY
REMOTE_URL = "https://github.com/" + REPOSITORY + ".git"
EMAIL = "325669781+Mumao233@users.noreply.github.com"
BRANCH = re.compile(r"feature/(?:overnight|web-pr06a)-[A-Za-z0-9][A-Za-z0-9._-]{0,119}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")


class PublishError(ValueError):
    pass


class GitHubError(PublishError):
    def __init__(self, status=None):
        self.status = status
        super().__init__("GitHub request failed" + (f" (HTTP {status})" if status is not None else ""))


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Authorization is never sent to an artifact CDN or any redirect target.
        return None


def git_identity_env():
    return {"GIT_AUTHOR_NAME": OWNER, "GIT_AUTHOR_EMAIL": EMAIL,
            "GIT_COMMITTER_NAME": OWNER, "GIT_COMMITTER_EMAIL": EMAIL}


def _git_environment():
    result = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    result.update(GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GCM_GUI_PROMPT="0")
    return result


def _run_git(repo, *args, input_bytes=None, extra_env=None):
    environment = _git_environment()
    if extra_env:
        environment.update(extra_env)
    try:
        result = subprocess.run(["git", "--no-optional-locks", "-C", str(repo),
            "-c", "core.fsmonitor=false", *args], input=input_bytes, env=environment,
            capture_output=True, check=False, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PublishError("Git operation failed") from None
    if result.returncode:
        raise PublishError("Git operation failed")
    return result.stdout


def _valid_branch(branch, *, allow_workflow=False):
    return (isinstance(branch, str) and (BRANCH.fullmatch(branch) is not None
            or allow_workflow and WORKFLOW_BRANCH.fullmatch(branch) is not None)
            and ".." not in branch and not branch.endswith((".", ".lock")))


def _base_ref(ref, *, allow_workflow=False):
    if ref != "main" and not _valid_branch(ref, allow_workflow=allow_workflow):
        raise PublishError("unapproved base branch")


def verify_remote(repo):
    accepted = {REMOTE_URL, REMOTE_URL[:-4], "git@github.com:" + REPOSITORY + ".git"}
    for push in (False, True):
        args = ["remote", "get-url"] + (["--push"] if push else []) + ["--all", "origin"]
        try:
            urls = _run_git(repo, *args).decode("utf-8").splitlines()
        except UnicodeError:
            raise PublishError("invalid Git remote") from None
        if len(urls) != 1 or urls[0] not in accepted:
            raise PublishError("origin is not the approved Classroom OS repository")


class GitHubClient:
    """Small scoped REST client. GET returns metadata; POST only creates Draft PRs.

    Artifact downloads use a separate small, bounded, unauthenticated redirect
    path. JSON metadata requests never follow redirects.
    """
    def __init__(self, token, *, opener=None):
        if not isinstance(token, str) or not token or any(c.isspace() for c in token):
            raise PublishError("invalid credential")
        self._token = token
        self._opener = opener if opener is not None else request.build_opener(_NoRedirect())

    @classmethod
    def from_git_credentials(cls, repo):
        repo = Path(repo).resolve(strict=True)
        verify_remote(repo)
        raw = _run_git(repo, "-c", "credential.interactive=never", "credential", "fill",
                       input_bytes=b"protocol=https\nhost=github.com\n\n")
        try:
            fields = {}
            for line in raw.decode("utf-8").splitlines():
                if not line:
                    continue
                key, value = line.split("=", 1)
                if key in fields:
                    raise ValueError()
                fields[key] = value
            if fields.get("protocol") != "https" or fields.get("host") != "github.com":
                raise ValueError()
            token = fields["password"]
        except (UnicodeError, ValueError, KeyError):
            raise PublishError("Git Credential Manager did not return a usable GitHub credential") from None
        client = cls(token)
        client.verify_user()
        return client

    def _request(self, method, path, payload=None):
        if (not isinstance(path, str) or not path.startswith("/") or "\\" in path
                or any(ord(char) < 32 for char in path)):
            raise PublishError("invalid GitHub path")
        parsed = parse.urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.fragment or "%" in parsed.path:
            raise PublishError("GitHub URL redirects or encoded paths are not accepted")
        if parsed.path != "/user" and not parsed.path.startswith(REPO_API + "/") and parsed.path != REPO_API:
            raise PublishError("GitHub request is outside the approved repository")
        if ".." in parsed.path.split("/"):
            raise PublishError("invalid GitHub path")
        if method == "POST":
            if parsed.path != REPO_API + "/pulls" or parsed.query or not isinstance(payload, dict):
                raise PublishError("only Draft PR creation is permitted")
            if payload.get("draft") is not True or set(payload) != {"title", "body", "head", "base", "draft", "maintainer_can_modify"}:
                raise PublishError("invalid Draft PR payload")
            if not _valid_branch(payload["head"], allow_workflow=True) or payload["maintainer_can_modify"] is not False:
                raise PublishError("invalid Draft PR head or permissions")
            _base_ref(payload["base"], allow_workflow=True)
        data = None if payload is None else json.dumps(payload, allow_nan=False).encode("utf-8")
        req = request.Request("https://api.github.com" + path, data=data, method=method,
            headers={"Authorization": "Bearer " + self._token, "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Classroom-OS-trusted-publisher",
                "Content-Type": "application/json"})
        try:
            with self._opener.open(req, timeout=30) as response:
                raw = response.read(4_000_001)
                if len(raw) > 4_000_000:
                    raise GitHubError()
        except error.HTTPError as exc:
            raise GitHubError(exc.code) from None
        except (error.URLError, OSError, TimeoutError):
            raise GitHubError() from None
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise GitHubError() from None

    def get(self, path):
        return self._request("GET", path)

    def post(self, path, payload):
        return self._request("POST", path, payload)

    @staticmethod
    def _artifact_redirect(location):
        if (not isinstance(location, str) or not location or "\\" in location
                or any(ord(char) < 32 or ord(char) == 127 for char in location)):
            raise PublishError("invalid artifact redirect")
        try:
            target = parse.urlsplit(location)
            host = target.hostname or ""
            allowed = (host.endswith(".blob.core.windows.net") or
                       host.endswith(".actions.githubusercontent.com"))
            if (target.scheme != "https" or target.username is not None or target.password is not None
                    or target.port not in (None, 443) or target.fragment or not allowed
                    or re.fullmatch(r"[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?", host) is None):
                raise PublishError("artifact redirect is outside the trusted HTTPS download hosts")
            # Each suffix requires a real subdomain; root/suffix lookalikes fail.
            if host in {"blob.core.windows.net", "actions.githubusercontent.com"}:
                raise PublishError("invalid artifact download host")
        except ValueError:
            raise PublishError("invalid artifact redirect") from None
        return location

    def get_bytes(self, path, max_bytes=32768):
        """Download one small queue ZIP; credentials never follow redirects.

        This deliberately does not support arbitrary URLs, repositories, large
        logs, or code artifacts. The queue must independently validate the ZIP.
        """
        if (not isinstance(path, str)
                or re.fullmatch(re.escape(REPO_API) + r"/actions/artifacts/[1-9][0-9]*/zip", path) is None):
            raise PublishError("only this repository's queue artifact ZIP is permitted")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 32768:
            raise PublishError("artifact byte limit must be 1..32768")
        url = "https://api.github.com" + path
        for hop in range(3):
            headers = {"User-Agent": "Classroom-OS-trusted-publisher", "Accept": "application/octet-stream"}
            if hop == 0:
                headers.update(Authorization="Bearer " + self._token,
                    Accept="application/vnd.github+json", **{"X-GitHub-Api-Version": "2022-11-28"})
            req = request.Request(url, method="GET", headers=headers)
            try:
                with self._opener.open(req, timeout=30) as response:
                    declared = response.headers.get("Content-Length")
                    if declared is not None:
                        if not re.fullmatch(r"[0-9]+", declared) or int(declared) > max_bytes:
                            raise PublishError("artifact exceeds the configured byte limit")
                    data = response.read(max_bytes + 1)
                    if len(data) > max_bytes:
                        raise PublishError("artifact exceeds the configured byte limit")
                    return data
            except error.HTTPError as exc:
                if exc.code not in {301, 302, 303, 307, 308}:
                    raise GitHubError(exc.code) from None
                if hop == 2:
                    raise PublishError("artifact redirect limit exceeded") from None
                url = self._artifact_redirect(exc.headers.get("Location"))
            except (error.URLError, OSError, TimeoutError):
                raise GitHubError() from None
        raise PublishError("artifact download failed")

    def verify_user(self):
        user = self.get("/user")
        if (not isinstance(user, dict) or user.get("login") != OWNER
                or type(user.get("id")) is not int or user["id"] != USER_ID):
            raise PublishError("authenticated GitHub user is not the approved personal account")
        return {"login": OWNER, "id": USER_ID}

    def _git_auth_env(self):
        # Environment only: no token in argv, persistent Git config, log or file.
        encoded = base64.b64encode(("x-access-token:" + self._token).encode()).decode("ascii")
        return {"GIT_CONFIG_COUNT": "3", "GIT_CONFIG_KEY_0": "http.extraheader", "GIT_CONFIG_VALUE_0": "",
            "GIT_CONFIG_KEY_1": "http.https://github.com/.extraheader", "GIT_CONFIG_VALUE_1": "",
            "GIT_CONFIG_KEY_2": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_2": "AUTHORIZATION: basic " + encoded}


def _verify_commit_identity(repo, base_sha, head_sha):
    commits = _run_git(repo, "rev-list", "--reverse", base_sha + ".." + head_sha).decode("ascii").splitlines()
    if not commits or len(commits) > 50:
        raise PublishError("invalid candidate commit count")
    expected = (OWNER + " <" + EMAIL + "> ").encode("ascii")
    for commit in commits:
        if SHA.fullmatch(commit) is None:
            raise PublishError("invalid commit object")
        raw = _run_git(repo, "cat-file", "-p", commit)
        header, separator, body = raw.partition(b"\n\n")
        if not separator:
            raise PublishError("invalid commit metadata")
        for field in (b"author ", b"committer "):
            entries = [line[len(field):] for line in header.splitlines() if line.startswith(field)]
            if len(entries) != 1 or not entries[0].startswith(expected):
                raise PublishError("candidate commits must use the approved personal author and committer")
        if b"co-authored-by" in body.lower():
            raise PublishError("coauthor trailers are not permitted")


class Publisher:
    def __init__(self, repo, client):
        self.repo = Path(repo).resolve(strict=True)
        self.client = client

    def _remote_head(self, branch, *, missing_ok=False):
        try:
            value = self.client.get(REPO_API + "/git/ref/heads/" + branch)
        except GitHubError as exc:
            if missing_ok and exc.status == 404:
                return None
            raise
        if not isinstance(value, dict) or value.get("object", {}).get("type") != "commit":
            raise PublishError("remote branch does not reference a commit")
        sha = value["object"].get("sha")
        if not isinstance(sha, str) or SHA.fullmatch(sha) is None:
            raise PublishError("invalid remote commit")
        return sha

    def _check_pr(self, pr, branch, base_ref, base_sha, head_sha):
        try:
            valid = (pr["state"] == "open" and pr["draft"] is True and pr.get("merged_at") is None
                and pr["user"]["login"] == OWNER and type(pr["user"]["id"]) is int and pr["user"]["id"] == USER_ID
                and pr["head"]["ref"] == branch and pr["head"]["sha"] == head_sha
                and pr["head"]["repo"]["full_name"] == REPOSITORY
                and pr["base"]["ref"] == base_ref and pr["base"]["sha"] == base_sha
                and pr["base"]["repo"]["full_name"] == REPOSITORY
                and type(pr["number"]) is int and pr["number"] > 0
                and pr["html_url"] == "https://github.com/" + REPOSITORY + "/pull/" + str(pr["number"]))
        except (KeyError, TypeError):
            valid = False
        if not valid:
            raise PublishError("existing or returned PR does not match the approved Draft, identity and source mapping")

    def publish(self, *, task, branch, base_ref, base_sha, head_sha, title, body,
                expected_patch_sha256=None, policy=None):
        if policy is None:
            if task != "06a" or not _valid_branch(branch) or branch == base_ref:
                raise PublishError("unapproved task or destination branch")
        else:
            try:
                policy = validate_policy(policy, task)
            except ValidationError:
                raise PublishError("invalid trusted publication policy") from None
            if branch != policy["branch"] or base_ref != policy["base_ref"]:
                raise PublishError("publication branch/base differs from trusted task policy")
            if expected_patch_sha256 is None:
                raise PublishError("generic publication requires the approved patch hash")
        _base_ref(base_ref, allow_workflow=policy is not None)
        if any(not isinstance(value, str) or SHA.fullmatch(value) is None for value in (base_sha, head_sha)):
            raise PublishError("full base and head SHAs are required")
        if (not isinstance(title, str) or not 1 <= len(title) <= 240 or any(ord(c) < 32 for c in title)
                or not isinstance(body, str) or len(body) > 60000):
            raise PublishError("invalid PR title or body")
        if expected_patch_sha256 is not None and (not isinstance(expected_patch_sha256, str) or HASH.fullmatch(expected_patch_sha256) is None):
            raise PublishError("invalid expected patch hash")
        verify_remote(self.repo)
        self.client.verify_user()
        # Re-read immutable objects with controller-owned scope; worker reports
        # cannot confer publication authority or change the task binding.
        report = (validate_candidate(self.repo, base_sha, head_sha, task) if policy is None else
                  validate_candidate(self.repo, base_sha, head_sha, task, policy=policy))
        if (report.get("valid") is not True or report.get("base_sha") != base_sha or report.get("head_sha") != head_sha
                or not isinstance(report.get("patch_sha256"), str) or HASH.fullmatch(report["patch_sha256"]) is None
                or (expected_patch_sha256 is not None and report["patch_sha256"] != expected_patch_sha256)
                or (policy is not None and report.get("policy_sha256") != policy["sha256"])):
            raise PublishError("trusted candidate validation failed")
        _verify_commit_identity(self.repo, base_sha, head_sha)
        if self._remote_head(base_ref) != base_sha:
            raise PublishError("remote base changed; revalidate before publishing")
        remote_head = self._remote_head(branch, missing_ok=True)
        if remote_head not in (None, head_sha):
            raise PublishError("destination branch already contains another head; no force push is allowed")
        if remote_head is None:
            _run_git(self.repo, "-c", "credential.helper=", "-c", "push.followTags=false",
                "-c", "push.recurseSubmodules=no", "-c", "push.default=nothing",
                "push", "--no-verify", "--porcelain", REMOTE_URL,
                head_sha + ":refs/heads/" + branch, extra_env=self.client._git_auth_env())
        if self._remote_head(branch) != head_sha or self._remote_head(base_ref) != base_sha:
            raise PublishError("remote source mapping changed after publication; manual reconciliation required")
        query = parse.urlencode({"state": "all", "head": OWNER + ":" + branch, "base": base_ref, "per_page": 100})
        prs = self.client.get(REPO_API + "/pulls?" + query)
        if not isinstance(prs, list) or len(prs) > 1:
            raise PublishError("ambiguous existing PRs; reconcile manually")
        existing = bool(prs)
        pr = prs[0] if existing else self.client.post(REPO_API + "/pulls", {
            "title": title, "body": body, "head": branch, "base": base_ref,
            "draft": True, "maintainer_can_modify": False})
        self._check_pr(pr, branch, base_ref, base_sha, head_sha)
        receipt = {"status": "existing" if existing else "created", "repository": REPOSITORY,
            "author": OWNER, "author_id": USER_ID, "draft": True, "pr_number": pr["number"],
            "pr_url": pr["html_url"], "branch": branch, "base_ref": base_ref,
            "base_sha": base_sha, "head_sha": head_sha, "patch_sha256": report["patch_sha256"]}
        if policy is not None:
            receipt["policy_sha256"] = policy["sha256"]
        return receipt
