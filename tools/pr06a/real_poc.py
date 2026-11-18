"""Operator command for the isolated PR-06a batch. Default is sample inspection."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web" / "backend"))

from classroom_web.pr06a_real_poc.policy import (AUTHORIZATION, AUTH_SHA256, BASELINE, ENDPOINT,
                                                    KEY_ID, MODEL, REGION, WORKSPACE,
                                                    PocStopped, digest, load_samples)

AUTH_FILE = ROOT / "docs" / "web-alpha" / "pr06a" / f"{AUTHORIZATION}.md"
DATABASE = "classroom_pr06a_real_poc"


def _emit(record):
    print(json.dumps(record, ensure_ascii=True, separators=(",", ":")), flush=True)


def _reviewed_head():
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(("GIT_", "PR06A_", "PG")) and k != "DASHSCOPE_API_KEY"}

    def git(*args):
        return subprocess.check_output(["git", "-c", "core.fsmonitor=false", "-C", str(ROOT), *args],
                                       env=environment, stderr=subprocess.DEVNULL, timeout=4).decode().strip()

    head = git("rev-parse", "HEAD")
    if not re.fullmatch("[0-9a-f]{40}", head) or head == BASELINE or git("status", "--porcelain"):
        raise PocStopped("reviewed_clean_checkout_required")
    git("merge-base", "--is-ancestor", BASELINE, head)
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
    if conninfo_to_dict(dsn).get("dbname") != DATABASE:
        raise PocStopped("isolated_poc_database_required")
    async with asyncio.timeout(5):
        return await AsyncConnection.connect(dsn, autocommit=True, connect_timeout=3)


async def _arm(args, head, fingerprint):
    review_path = Path(args.review)
    review_hash = digest(review_path.read_bytes())
    if review_hash != args.review_sha256:
        raise PocStopped("review_receipt_digest_mismatch")
    async with await _db(True) as db, asyncio.timeout(5), db.transaction():
        await db.execute("SET LOCAL synchronous_commit = 'on'")
        await db.execute(
            """INSERT INTO pr06a_control.activation(authorization,source_sha256,baseline,reviewed_sha,
               review_receipt,approval_ref,account_ref,workspace,key_id,region,model,endpoint,
               credential_sha256,executor_login,enabled)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)""",
            (AUTHORIZATION, AUTH_SHA256, BASELINE, head, review_hash, args.approval_ref, args.account_ref,
             WORKSPACE, KEY_ID, REGION, MODEL, ENDPOINT, fingerprint, args.executor_login),
        )
    _emit({"authorization": AUTHORIZATION, "candidate": head, "status": "armed"})


async def _execute(args, head, key, fingerprint, samples):
    from classroom_web.pr06a_real_poc.ledger import PgLedger
    from classroom_web.pr06a_real_poc.runner import execute_batch
    from classroom_web.pr06a_real_poc.websocket import _WebSocket

    async with await _db(False) as db:
        receipts = await execute_batch(PgLedger(db), samples, head, args.account_ref, fingerprint,
                                       lambda _: _WebSocket(key))
    for receipt in receipts:
        _emit({"slot": receipt.slot, "recognition": receipt.state, "stop": receipt.stop})
    return int(len(receipts) != len(samples) or any(item.stop or item.state != "succeeded" for item in receipts))


def main():
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--arm", action="store_true")
    action.add_argument("--execute", action="store_true")
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--account-ref")
    parser.add_argument("--approval-ref")
    parser.add_argument("--executor-login")
    parser.add_argument("--review")
    parser.add_argument("--review-sha256")
    args = parser.parse_args()
    try:
        samples = load_samples(args.samples, AUTH_FILE)
        if not args.arm and not args.execute:
            for sample in samples:
                _emit(vars(sample.spec))
            return 0

        if not args.account_ref or (args.arm and not all((args.approval_ref, args.executor_login,
                                                         args.review, args.review_sha256))):
            raise PocStopped("operator_records_required")
        head = _reviewed_head()
        if not sys.stdin.isatty() or input("授权后输入「开始真实调用」：") != "开始真实调用":
            raise PocStopped("interactive_authorization_required")
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
