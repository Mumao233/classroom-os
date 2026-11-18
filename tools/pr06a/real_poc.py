"""Operator command for the isolated PR-06a batch. Default is sample inspection."""
import argparse
import asyncio
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web" / "backend"))

from classroom_web.pr06a_real_poc.policy import (AUTHORIZATION, AUTH_SHA256, BASELINE, ENDPOINT,
                                                    KEY_ID, MODEL, REGION, WORKSPACE,
                                                    PocStopped, digest, load_samples)

AUTH_FILE = ROOT / "docs" / "web-alpha" / "pr06a" / f"{AUTHORIZATION}.md"
RISK_REPORT = AUTH_FILE.parent / "risk-acceptance" / "REPORT.md"
RISK_REPORT_SHA256 = "c8f6eb43c3ec7d041bb8a63a847d70f7175e45e446528b89fc68ec607078451c"
RISK_REVIEW_BASE = "05559351bd709a07f7eee88fb357b749a6f49288"
DATABASE = "classroom_pr06a_real_poc"


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise PocStopped("invalid_arguments")


def _emit(record):
    print(json.dumps(record, ensure_ascii=True, separators=(",", ":")), flush=True)


def _git(*args):
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(("GIT_", "PR06A_", "PG")) and k != "DASHSCOPE_API_KEY"}

    return subprocess.check_output(["git", "-c", "core.fsmonitor=false", "-C", str(ROOT), *args],
                                   env=environment, stderr=subprocess.DEVNULL, timeout=10)


def _reviewed_head():
    head = _git("rev-parse", "HEAD").decode().strip()
    files = _git("ls-files", "-v").decode().splitlines()
    if (not re.fullmatch("[0-9a-f]{40}", head) or head == BASELINE or _git("status", "--porcelain").strip()
            or any(not line.startswith("H ") for line in files)):
        raise PocStopped("reviewed_clean_checkout_required")
    _git("merge-base", "--is-ancestor", BASELINE, head)
    return head


def _credential():
    secret = os.environ.pop("DASHSCOPE_API_KEY", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", secret):
        raise PocStopped("server_credential_missing_or_invalid")
    return secret


async def _db(arm):
    from psycopg import AsyncConnection
    from psycopg.conninfo import conninfo_to_dict
    from classroom_web.pr06a_real_poc.ledger import quiet_database_logger

    quiet_database_logger()
    dsn = os.environ.pop("PR06A_CONTROL_DSN" if arm else "PR06A_EXECUTOR_DSN", "")
    options = conninfo_to_dict(dsn)
    if options.get("dbname") != DATABASE or options.get("host") != "127.0.0.1" or "service" in options:
        raise PocStopped("isolated_poc_database_required")
    async with asyncio.timeout(5):
        db = await AsyncConnection.connect(dsn, autocommit=True, connect_timeout=3, hostaddr="127.0.0.1")
        try:
            for setting, value in (("log_parameter_max_length", "0"),
                                   ("log_parameter_max_length_on_error", "0"), ("log_error_verbosity", "terse")):
                result = await db.execute(f"SHOW {setting}")
                if await result.fetchone() != (value,):
                    raise PocStopped("database_parameter_logging_enabled")
        except BaseException:
            await db.close()
            raise
        return db


def _receipt(path, expected, failure):
    data = Path(path).read_bytes()
    if digest(data) != expected:
        raise PocStopped(failure)
    return json.loads(data)


def _verify_risk_acceptance(review, lines, expected_sha):
    report = RISK_REPORT.read_bytes()
    if expected_sha != RISK_REPORT_SHA256 or digest(report) != RISK_REPORT_SHA256:
        raise PocStopped("risk_acceptance_digest_mismatch")
    declarations = [line.strip() for line in lines if line.strip().upper().startswith("RISK-ACCEPTANCE:")]
    if declarations != [f"RISK-ACCEPTANCE: {expected_sha}"]:
        raise PocStopped("explicit_risk_acceptance_attestation_required")

    accepted = report.decode("utf-8").split("<!-- BEGIN ASTRA FINDINGS -->\n", 1)[1]
    accepted = accepted.split("\n<!-- END ASTRA FINDINGS -->", 1)[0]
    sections = "\n".join(lines).split("\nFull review comments:\n")
    if len(sections) != 2 or sections[1].strip() != accepted or re.search(r"\[P[0-3]\]", sections[0]):
        raise PocStopped("risk_acceptance_findings_mismatch")
    findings = [dict(priority=int(priority), title=title, file=file, line=int(line), missing_fields=[])
                for priority, title, file, line in re.findall(
                    r"^- \[P([0-3])\] (.+?) — ([^:\n]+):(\d+)(?:-\d+)?$", accepted, re.MULTILINE)]
    if (review.get("findings_total") != len(findings) or review.get("findings") != findings
            or review.get("findings_by_priority") != [0, 2, 2, 0]):
        raise PocStopped("risk_acceptance_findings_mismatch")
    return expected_sha


def _verify_review(args, head):
    risk_sha = args.risk_acceptance_sha256
    scope_base = BASELINE if risk_sha is None else RISK_REVIEW_BASE
    review = _receipt(args.review, args.review_sha256, "review_receipt_digest_mismatch")
    if (review.get("head") != head or review.get("state") != "review_completed"
            or review.get("scope") != "base" or review.get("scope_value") != scope_base
            or review.get("untracked_files_excluded_from_scope") is not False
            or len(review.get("gates", {})) != 10
            or not all(gate.get("ok") is True for gate in review["gates"].values())):
        raise PocStopped("review_receipt_not_accepted")
    verdict = Path(args.review).with_name("review.md").read_bytes()
    lines = verdict.decode("utf-8-sig").splitlines()
    verdict_lines = [line.strip() for line in lines if line.strip().upper().startswith("VERDICT:")]
    if (digest(verdict) != review.get("review_sha256") or verdict_lines != [f"VERDICT: PASS {head}"]):
        raise PocStopped("explicit_review_verdict_required")
    if risk_sha is not None:
        _git("merge-base", "--is-ancestor", RISK_REVIEW_BASE, head)
        return _verify_risk_acceptance(review, lines, risk_sha)
    if review.get("findings_by_priority", [1])[:2] != [0, 0]:
        raise PocStopped("review_receipt_not_accepted")
    return None


def _verify_receipts(args, head):
    risk_sha = _verify_review(args, head)
    vmware = _receipt(args.vmware_receipt, args.vmware_sha256, "vmware_receipt_digest_mismatch")
    if (vmware.get("commit") != head or vmware.get("mode") != "full"
            or vmware.get("result") not in ("passed", "passed_with_skips")
            or vmware.get("guest_exit_code") != 0
            or any(vmware.get(check) is not True for check in ("same_source_manifest", "source_reread_matches",
                    "evidence_sha256_matches_guest", "transport_succeeded"))):
        raise PocStopped("vmware_receipt_not_accepted")
    manifest = _receipt(Path(args.vmware_receipt).with_name("host-SOURCE.json"),
                        vmware.get("manifest_sha256"), "vmware_manifest_digest_mismatch")
    with ZipFile(BytesIO(_git("archive", "--format=zip", head))) as snapshot:
        files = {entry.filename: digest(snapshot.read(entry)) for entry in snapshot.infolist() if not entry.is_dir()}
    if (manifest.get("base_sha") != head or manifest.get("full") is not True or manifest.get("files_sha256") != files
            or manifest.get("runner_sha256") != files.get("tools/pr06a/run_vmware_validation.py")
            or vmware.get("executed_runner_sha256") != manifest["runner_sha256"]
            or vmware.get("source_archive_sha256") != manifest.get("source_zip_sha256")):
        raise PocStopped("vmware_git_tree_mismatch")
    return risk_sha


async def _arm(args, head, fingerprint):
    risk_sha = _verify_receipts(args, head)
    async with await _db(True) as db, asyncio.timeout(5), db.transaction():
        await db.execute("SET LOCAL synchronous_commit = 'on'")
        await db.execute(
            """INSERT INTO pr06a_control.activation(authorization_id,source_sha256,baseline,reviewed_sha,
               review_receipt,vmware_receipt,approval_ref,account_ref,workspace,key_id,region,model,endpoint,
               credential_sha256,executor_login,risk_acceptance_sha256,enabled)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)""",
            (AUTHORIZATION, AUTH_SHA256, BASELINE, head, args.review_sha256, args.vmware_sha256,
             args.approval_ref, args.account_ref,
             WORKSPACE, KEY_ID, REGION, MODEL, ENDPOINT, fingerprint, args.executor_login, risk_sha),
        )
    receipt = {"authorization": AUTHORIZATION, "candidate": head, "status": "armed"}
    if risk_sha is not None:
        receipt["risk_acceptance_sha256"] = risk_sha
    _emit(receipt)


async def _execute(args, head, key, fingerprint, samples):
    from classroom_web.pr06a_real_poc.ledger import PgLedger
    from classroom_web.pr06a_real_poc.runner import execute_batch
    from classroom_web.pr06a_real_poc.websocket import _WebSocket

    if _reviewed_head() != head:
        raise PocStopped("reviewed_checkout_changed")
    async with await _db(False) as db:
        receipts = await execute_batch(PgLedger(db), samples, head, args.account_ref, fingerprint,
                                       lambda _: _WebSocket(key))
    for receipt in receipts:
        _emit({"slot": receipt.slot, "recognition": receipt.state, "stop": receipt.stop})
    return int(len(receipts) != len(samples) or any(item.stop or item.state != "succeeded" for item in receipts))


def main():
    parser = _Parser()
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--arm", action="store_true")
    action.add_argument("--execute", action="store_true")
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--account-ref")
    parser.add_argument("--approval-ref")
    parser.add_argument("--executor-login")
    parser.add_argument("--review")
    parser.add_argument("--review-sha256")
    parser.add_argument("--risk-acceptance-sha256")
    parser.add_argument("--vmware-receipt")
    parser.add_argument("--vmware-sha256")
    try:
        args = parser.parse_args()
        samples = load_samples(args.samples, AUTH_FILE)
        if not args.arm and not args.execute:
            for sample in samples:
                _emit(vars(sample.spec))
            return 0

        if not args.account_ref or (args.arm and not all((args.approval_ref, args.executor_login,
                                                         args.review, args.review_sha256,
                                                         args.vmware_receipt, args.vmware_sha256))):
            raise PocStopped("operator_records_required")
        head = _reviewed_head()
        if not sys.stdin.isatty() or input("授权后输入「开始真实调用」：") != "开始真实调用":
            raise PocStopped("interactive_authorization_required")
        if _reviewed_head() != head:
            raise PocStopped("reviewed_checkout_changed")
        key = _credential()
        fingerprint = digest(key.encode("ascii"))
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as run:
            if args.arm:
                run.run(_arm(args, head, fingerprint))
                return 0
            return run.run(_execute(args, head, key, fingerprint, samples))
    except PocStopped as exc:
        _emit({"status": "stopped", "reason": str(exc)})
    except KeyboardInterrupt:
        _emit({"status": "stopped", "reason": "operator_interrupted"})
    except Exception:
        _emit({"status": "stopped", "reason": "preflight_or_execution_failed"})
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
