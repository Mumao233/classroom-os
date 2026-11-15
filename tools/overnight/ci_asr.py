"""Credential-free hosted ASR gate with parent-validated completion records."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.overnight.asr_gate import parse_summary
from tools.overnight.ci_backend import clean_environment
from tools.overnight.state import atomic_json
from tools.overnight.verification import create_checkout, verify_checkout
from tools.overnight.validation import candidate_patch


def run(source, output):
    source, output = source.resolve(strict=True), output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence = output.parent / ("asr-sessions-" + uuid.uuid4().hex)
    evidence.mkdir()
    # Outside source: never use artifacts or ignored files from a prior CI step.
    checkout = source.parent / ("overnight-asr-" + uuid.uuid4().hex)
    head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    manifest = create_checkout(source, head, checkout)
    parent = subprocess.check_output(["git", "-C", str(source), "rev-parse", head + "^"], text=True).strip()
    binding = {"head_sha": head, "patch_sha256": hashlib.sha256(candidate_patch(source, parent, head)).hexdigest()}
    report = {"scope": "Explicit simulated ASR; no real provider or quality acceptance",
              "candidate_binding": binding, "patch_base_sha": parent,
              "verified_checkout": manifest, "sessions": {}, "passed": False}
    try:
        for stage in ("candidate", "trusted"):
            nonce = uuid.uuid4().hex
            command = [sys.executable, "-I", "-B", str(source / "tools/overnight/asr_gate.py"),
                "--stage", stage, "--workspace", str(checkout), "--deps", sysconfig.get_path("purelib"),
                "--nonce", nonce, "--head", head, "--patch", binding["patch_sha256"]]
            process = subprocess.run(command, cwd=checkout, capture_output=True, text=True, encoding="utf-8",
                                     env=clean_environment(os.environ), timeout=180)
            (evidence / (stage + ".stdout.log")).write_text(process.stdout, encoding="utf-8")
            (evidence / (stage + ".stderr.log")).write_text(process.stderr, encoding="utf-8")
            print(process.stderr, end="", flush=True)
            session = {"exit_code": process.returncode, "passed": False}
            try:
                if process.returncode != 0:
                    raise ValueError("ASR test subprocess failed")
                session["summary"] = parse_summary(process.stdout, stage=stage, nonce=nonce, binding=binding)
                session["passed"] = True
            except ValueError as error:
                session["error"] = str(error)
            report["sessions"][stage] = session
            verify_checkout(checkout, manifest)
        report["passed"] = all(item["passed"] for item in report["sessions"].values())
        report["status"] = "passed" if report["passed"] else "failed"
    except Exception as error:
        report.update(status="failed", error=str(error))
    atomic_json(output, report)
    print(json.dumps({"passed": report["passed"], "report": str(output)}))
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(0 if run(args.source, args.output) else 1)
