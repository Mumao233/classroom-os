"""Read a GitHub Actions data-only request for an explicitly pinned local pilot.

No repository text, issue, comment, branch name or artifact command is executed.
The caller must choose the approved control SHA and keep this process running.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import zipfile

from .controller import validate_request
from .publish import GitHubClient, REPO_API
from .state import StateError, atomic_json


def decode_request(data, base):
    if len(data) > 32768:
        raise StateError("request artifact exceeds limit")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].filename != "request.json" or members[0].file_size > 4096:
            raise StateError("unexpected queue artifact contents")
        raw = archive.read(members[0])
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise StateError("duplicate request fields")
            value[key] = item
        return value
    return validate_request(json.loads(raw, object_pairs_hook=unique), base)


def find_request(client, base):
    runs = client.get(REPO_API + "/actions/workflows/overnight-queue.yml/runs?status=success&per_page=30")
    for run in runs.get("workflow_runs", []):
        if (run.get("head_sha") != base or run.get("event") not in {"push", "workflow_dispatch", "schedule"}
                or run.get("path") != ".github/workflows/overnight-queue.yml"
                or run.get("actor", {}).get("login") != "Mumao233"
                or run.get("repository", {}).get("full_name") != "Mumao233/classroom-os"
                or type(run.get("id")) is not int):
            continue
        artifacts = client.get(REPO_API + "/actions/runs/" + str(run["id"]) + "/artifacts")
        matches = [item for item in artifacts.get("artifacts", [])
                   if item.get("name") == "overnight-request" and not item.get("expired")]
        if len(matches) != 1 or type(matches[0].get("id")) is not int:
            raise StateError("missing or ambiguous request artifact")
        data = client.get_bytes(REPO_API + "/actions/artifacts/" + str(matches[0]["id"]) + "/zip", max_bytes=32768)
        request = decode_request(data, base)
        if request["request_id"] != str(run["id"]):
            raise StateError("request/run identity mismatch")
        return request
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--base", required=True)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--run-id", required=True, help="Stable authorization ID; reuse on resume")
    parser.add_argument("--deps", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--watch", action="store_true", help="Poll once per minute until the first matching request")
    args = parser.parse_args()
    client = GitHubClient.from_git_credentials(args.source)
    client.verify_user()
    while True:
        request = find_request(client, args.base)
        if request:
            destination = args.source / "work/overnight/requests" / (request["request_id"] + ".json")
            destination.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(destination, request)
            command = [sys.executable, "-B", "-m", "tools.overnight.controller", "--source", str(args.source),
                "--base", args.base, "--base-ref", args.base_ref, "--run-id", args.run_id,
                "--deps", str(args.deps), "--request", str(destination)]
            if args.publish:
                command.append("--publish")
            return subprocess.call(command, cwd=args.source)
        if not args.watch:
            print(json.dumps({"status": "no_matching_pinned_request", "model_calls": 0}))
            return 0
        time.sleep(60)


if __name__ == "__main__":
    raise SystemExit(main())
