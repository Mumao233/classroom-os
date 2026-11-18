"""PostgreSQL-backed PR-06a gates with synthetic PCM and zero paid I/O."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

from psycopg import AsyncConnection, errors
import pytest
import sqlalchemy as sa
from test_billing import billing_pg

from classroom_web.asr_dashscope.transport import AuthenticationRejected, Disconnected, FakeTransport
from classroom_web.pr06a_real_poc import policy
from classroom_web.pr06a_real_poc.ledger import Attempt, PgLedger
from classroom_web.pr06a_real_poc import runner
from classroom_web.pr06a_real_poc.runner import execute_batch
from classroom_web.test_support import SYNTHETIC_PASSWORD

REVIEWED = "8" * 40
ACCOUNT = "synthetic-console-attestation"
SECOND_ACCOUNT = "PR06A-ACCT-ATTEST-20261118-R2"
FINGERPRINT = policy.digest(b"synthetic-pr06a-key")
PACKAGE = Path(policy.__file__).parent


def _run(coro):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(coro)


@pytest.fixture
def poc_operator():
    script = Path(__file__).resolve().parents[4] / "tools/pr06a/real_poc.py"
    spec = spec_from_file_location("pr06a_operator", script)
    operator = module_from_spec(spec)
    spec.loader.exec_module(operator)
    return operator


@pytest.mark.parametrize("key", ["a" * 20, "sk-ws-H.synthetic.fixture_only-0123", "a" * 255 + "."],
                         ids=["lower-bound", "dotted-workspace", "upper-bound"])
def test_poc_credential_reads_and_clears_server_environment(poc_operator, monkeypatch, capsys, key):
    monkeypatch.setenv("DASHSCOPE_API_KEY", key)
    assert poc_operator._credential() == key
    assert "DASHSCOPE_API_KEY" not in os.environ
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("key", [None, "a" * 19, "a" * 257, "a" * 20 + " ", "a" * 20 + "\n",
                                  "a" * 20 + "全", "a" * 20 + "/"],
                         ids=["missing", "too-short", "too-long", "space", "newline", "non-ascii", "slash"])
def test_poc_credential_rejects_invalid_values_without_echo(poc_operator, monkeypatch, capsys, key):
    monkeypatch.setenv("KEY", "synthetic_alternate_env_is_ignored")
    if key is None:
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    else:
        monkeypatch.setenv("DASHSCOPE_API_KEY", key)
    with pytest.raises(policy.PocStopped, match="^server_credential_missing_or_invalid$"):
        poc_operator._credential()
    assert "DASHSCOPE_API_KEY" not in os.environ
    assert capsys.readouterr() == ("", "")


@pytest.fixture
def arm_receipts(tmp_path, monkeypatch, poc_operator):
    operator = poc_operator
    raw = operator.RISK_REPORT.read_bytes()
    risk_file = tmp_path / "REPORT.md"
    risk_file.write_bytes(raw)
    monkeypatch.setattr(operator, "RISK_REPORT", risk_file)
    findings_block = (raw.decode().split("<!-- BEGIN ASTRA FINDINGS -->\n")[1]
                      .split("\n<!-- END ASTRA FINDINGS -->")[0])
    args = SimpleNamespace(review=str(tmp_path / "status.json"), review_sha256=None,
                           risk_acceptance_sha256=operator.RISK_REPORT_SHA256,
                           vmware_receipt=str(tmp_path / "digest.json"), vmware_sha256=None,
                           authorization_id=policy.AUTHORIZATION,
                           approval_ref="synthetic approval", account_ref=ACCOUNT, executor_login="synthetic_executor")
    markdown = (f"VERDICT: PASS {REVIEWED}\n\nRISK-ACCEPTANCE: {args.risk_acceptance_sha256}\n\n"
                f"Full review comments:\n\n{findings_block}\n")
    findings = [
        (1, "Carry the inferred output floor across cumulative receipts", "pr06a_real_poc/runner.py", 104),
        (1, "Preserve a usage body's inferred floor when a mirror disagrees", "asr_dashscope/wire.py", 264),
        (2, "Normalize redundant totals in the metering replay key", "asr_dashscope/wire.py", 344),
        (2, "Preserve heartbeat timing when duplicate identity fields agree", "asr_dashscope/wire.py", 290),
    ]
    status = dict(head=REVIEWED, state="review_completed", scope="base", scope_value=operator.RISK_REVIEW_BASE,
                  untracked_files_excluded_from_scope=False,
                  gates={name: {"ok": True} for name in sorted(operator.ASTRA_INTEGRITY_GATES)},
                  findings_total=4, findings_by_priority=[0, 2, 2, 0], findings=[
                      dict(priority=p, title=t, file=f"web/backend/classroom_web/{f}", line=n, missing_fields=[])
                      for p, t, f, n in findings])

    def write_review(text):
        data = text.encode()
        (tmp_path / "review.md").write_bytes(data)
        status["review_sha256"] = policy.digest(data)
        data = json.dumps(status).encode()
        Path(args.review).write_bytes(data)
        args.review_sha256 = policy.digest(data)

    archive = BytesIO()
    runner_path = "tools/pr06a/run_vmware_validation.py"
    files = {runner_path: b"synthetic frozen runner"}
    for authorization_id in policy.AUTHORIZATION_DIGESTS:
        path = operator._authorization_path(authorization_id)
        files[path.relative_to(operator.ROOT).as_posix()] = path.read_bytes()
    with ZipFile(archive, "w") as snapshot:
        for name, data in files.items():
            snapshot.writestr(name, data)
    runner_hash = policy.digest(b"synthetic frozen runner")
    source_hash = policy.digest(archive.getvalue())
    manifest = dict(base_sha=REVIEWED, full=True, files_sha256={name: policy.digest(data) for name, data in files.items()},
                    runner_sha256=runner_hash, source_zip_sha256=source_hash)
    manifest_file = tmp_path / "host-SOURCE.json"
    manifest_file.write_bytes(json.dumps(manifest).encode())
    digest = dict(commit=REVIEWED, mode="full", result="passed", guest_exit_code=0, transport_succeeded=True,
                  same_source_manifest=True, source_reread_matches=True, evidence_sha256_matches_guest=True,
                  manifest_sha256=policy.digest(manifest_file.read_bytes()), source_archive_sha256=source_hash,
                  executed_runner_sha256=runner_hash)
    Path(args.vmware_receipt).write_bytes(json.dumps(digest).encode())
    args.vmware_sha256 = policy.digest(Path(args.vmware_receipt).read_bytes())

    def git(*arguments):
        if arguments == ("archive", "--format=zip", REVIEWED):
            return archive.getvalue()
        assert arguments == ("merge-base", "--is-ancestor", operator.RISK_REVIEW_BASE, REVIEWED)
        return b""

    monkeypatch.setattr(operator, "_git", git)
    write_review(markdown)
    return operator, args, status, markdown, write_review


@pytest.mark.parametrize("mutation,reason", [
    ("extra-markdown-finding", "risk_acceptance_findings_mismatch"),
    ("extra-status-finding", "risk_acceptance_findings_mismatch"),
    ("changed-finding-body", "risk_acceptance_findings_mismatch"),
    ("wrong-head", "review_receipt_not_accepted"),
    ("wrong-integrity-gate", "review_receipt_not_accepted"),
    ("ordinary-pass-with-p1", "review_receipt_not_accepted"),
    ("changed-report", "risk_acceptance_digest_mismatch"),
    ("forged-report-digest", "risk_acceptance_digest_mismatch"),
    ("no-attestation", "explicit_risk_acceptance_attestation_required"),
    ("blocked-gate", "explicit_review_verdict_required"),
])
def test_risk_acceptance_rejects_before_db(arm_receipts, monkeypatch, mutation, reason):
    operator, args, status, markdown, write_review = arm_receipts
    if mutation == "extra-markdown-finding":
        markdown += "\n- [P2] Synthetic new finding — tools/pr06a/real_poc.py:1\n"
    elif mutation == "extra-status-finding":
        status["findings"].append(dict(priority=2, title="Synthetic unregistered finding"))
    elif mutation == "changed-finding-body":
        markdown = markdown.replace("¥1.05", "¥1.04")
    elif mutation == "wrong-head":
        status["head"] = operator.RISK_REVIEW_BASE
    elif mutation == "wrong-integrity-gate":
        status["gates"]["unrecognized_gate"] = status["gates"].pop("working_tree_unchanged")
    elif mutation == "ordinary-pass-with-p1":
        status["scope_value"] = operator.BASELINE
        markdown = markdown.replace(f"RISK-ACCEPTANCE: {args.risk_acceptance_sha256}\n\n", "")
        args.risk_acceptance_sha256 = None
    elif mutation in ("changed-report", "forged-report-digest"):
        operator.RISK_REPORT.write_bytes(operator.RISK_REPORT.read_bytes() + b"unexpected revision\n")
        if mutation == "forged-report-digest":
            new_digest = policy.digest(operator.RISK_REPORT.read_bytes())
            markdown = markdown.replace(args.risk_acceptance_sha256, new_digest)
            args.risk_acceptance_sha256 = new_digest
    elif mutation == "no-attestation":
        markdown = markdown.replace("RISK-ACCEPTANCE:", "OTHER:")
    elif mutation == "blocked-gate":
        markdown = markdown.replace("VERDICT: PASS", "VERDICT: BLOCK")
    write_review(markdown)

    async def no_database(_arm):
        pytest.fail("rejected evidence reached the activation database")

    monkeypatch.setattr(operator, "_db", no_database)
    with pytest.raises(policy.PocStopped, match=f"^{reason}$"):
        _run(operator._arm(args, REVIEWED, FINGERPRINT))


@pytest.mark.parametrize("mutation,reason", [
    ("unregistered-id", "unregistered_authorization"),
    ("changed-file", "authorization_digest_mismatch"),
    ("wrong-review-head", "review_receipt_not_accepted"),
    ("wrong-vmware-head", "vmware_receipt_not_accepted"),
])
def test_second_authorization_rejects_before_db(arm_receipts, tmp_path, monkeypatch, mutation, reason):
    operator, args, status, markdown, write_review = arm_receipts
    args.authorization_id = policy.SECOND_AUTHORIZATION
    if mutation == "unregistered-id":
        args.authorization_id += "-unregistered"
    elif mutation == "changed-file":
        changed = tmp_path / "modified-authorization.md"
        changed.write_bytes(operator._authorization_path(args.authorization_id).read_bytes() + b"changed")
        monkeypatch.setattr(operator, "_authorization_path", lambda _: changed)
    elif mutation == "wrong-review-head":
        status["head"] = "7" * 40
        write_review(markdown)
    else:
        receipt = Path(args.vmware_receipt)
        data = json.loads(receipt.read_bytes())
        receipt.write_bytes(json.dumps(data | {"commit": "7" * 40}).encode())
        args.vmware_sha256 = policy.digest(receipt.read_bytes())

    async def no_database(_arm):
        pytest.fail("invalid authorization reached the activation database")

    monkeypatch.setattr(operator, "_db", no_database)
    with pytest.raises(policy.PocStopped, match=f"^{reason}$"):
        _run(operator._arm(args, REVIEWED, FINGERPRINT))


@pytest.mark.parametrize("use_exception", [False, True], ids=["ordinary-pass", "accepted-risks"])
def test_arm_audit_records_only_the_validated_risk_digest(poc, arm_receipts, monkeypatch, capsys, use_exception):
    pg, _, connect = poc
    operator, args, status, _, write_review = arm_receipts
    if not use_exception:
        args.risk_acceptance_sha256 = None
        status.update(scope_value=operator.BASELINE, findings_by_priority=[0, 0, 0, 0],
                      findings_total=0, findings=[])
        write_review(f"VERDICT: PASS {REVIEWED}\n")
    args.executor_login = connect.executor
    with pg.admin.begin() as db:
        db.exec_driver_sql("DELETE FROM pr06a_control.activation")

    async def control_db(arm):
        assert arm
        url = pg.admin_url
        return await AsyncConnection.connect(host=url.host, port=url.port, dbname=url.database,
                                             user=url.username, password=url.password, autocommit=True)

    monkeypatch.setattr(operator, "_db", control_db)
    _run(operator._arm(args, REVIEWED, FINGERPRINT))
    with pg.admin.connect() as db:
        row = db.execute(sa.text("SELECT reviewed_sha, review_receipt, vmware_receipt, risk_acceptance_sha256 "
                                 "FROM pr06a_control.activation")).one()
        assert row == (REVIEWED, args.review_sha256, args.vmware_sha256, args.risk_acceptance_sha256)
        assert db.scalar(sa.text("SELECT count(*) FROM pr06a_control.attempts")) == 0
    assert json.loads(capsys.readouterr().out).get("risk_acceptance_sha256") == args.risk_acceptance_sha256


@pytest.mark.parametrize("compatible", [True, False], ids=["verified-overlay", "missing-receipt-function"])
def test_documented_risk_audit_upgrade_checks_overlay(poc, compatible):
    pg, _, _ = poc
    runbook = Path(__file__).resolve().parents[4] / "docs/web-alpha/pr06a/REAL-POC-RUNBOOK-20261118.md"
    upgrade = runbook.read_text(encoding="utf-8").split("```sql\n")[1].split("\n```")[0]
    with pg.admin.begin() as db:
        db.exec_driver_sql("ALTER TABLE pr06a_control.activation DROP COLUMN risk_acceptance_sha256")
        before = db.scalar(sa.text("SELECT to_jsonb(a) FROM pr06a_control.activation a"))
        if not compatible:
            db.exec_driver_sql("DROP FUNCTION pr06a_api.retain_packet(uuid,uuid,jsonb)")
            with pytest.raises(sa.exc.DBAPIError, match="PR06A_OVERLAY_UPGRADE_BLOCKED"), db.begin_nested():
                db.exec_driver_sql(upgrade, execution_options={"no_parameters": True})
            assert db.scalar(sa.text("SELECT to_jsonb(a) FROM pr06a_control.activation a")) == before
        else:
            db.exec_driver_sql(upgrade, execution_options={"no_parameters": True})
            assert db.scalar(sa.text("SELECT to_jsonb(a) FROM pr06a_control.activation a")) == (
                before | {"risk_acceptance_sha256": None})


@pytest.fixture
def poc(billing_pg, request):
    pg = billing_pg
    executor = "pr06a_executor_" + uuid4().hex[:16]
    with pg.admin.begin() as db:
        db.exec_driver_sql(f'CREATE ROLE "{executor}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
        db.exec_driver_sql(f'ALTER ROLE "{executor}" PASSWORD \'{SYNTHETIC_PASSWORD}\'')
        db.exec_driver_sql(open_overlay(), execution_options={"no_parameters": True})
        if getattr(request, "param", None) != "legacy":
            db.exec_driver_sql(batch_migration(), execution_options={"no_parameters": True})
        db.exec_driver_sql(f'GRANT classroom_pr06a_executor TO "{executor}"')
        registry = json.loads((PACKAGE / "samples.json").read_bytes())
        assert [dict(row) for row in db.execute(sa.text(
            "SELECT * FROM pr06a_control.samples ORDER BY slot")).mappings()] == registry
        fixtures = []
        for entry in registry:
            slot = entry["slot"]
            pcm = bytes([slot, 0]) * 3200
            spec = policy.SampleSpec(slot, entry["name"], entry["wav_hash"], policy.digest(pcm),
                                     3200, 3200, policy.digest(pcm))
            fixtures.append(policy.Sample(spec, pcm))
            db.execute(sa.text("UPDATE pr06a_control.samples SET samples=:samples,send_samples=:send_samples,"
                               "pcm_hash=:pcm_hash,send_hash=:send_hash WHERE slot=:slot"), asdict(spec))
        db.execute(sa.text("""INSERT INTO pr06a_control.activation(
          authorization_id,source_sha256,baseline,reviewed_sha,review_receipt,vmware_receipt,approval_ref,account_ref,
          workspace,key_id,region,model,endpoint,credential_sha256,executor_login,enabled)
          VALUES(:auth,:source,:base,:sha,:receipt,:receipt,'synthetic approval',:account,:workspace,:key_id,
                 :region,:model,:endpoint,:fingerprint,:login,true)"""),
                   dict(auth=policy.AUTHORIZATION, source=policy.AUTH_SHA256, base=policy.BASELINE, sha=REVIEWED,
                        receipt="a" * 64, account=ACCOUNT, workspace=policy.WORKSPACE, key_id=policy.KEY_ID,
                        region=policy.REGION, model=policy.MODEL, endpoint=policy.ENDPOINT,
                        fingerprint=FINGERPRINT, login=executor))
        if not db.scalar(sa.text("SELECT count(*) FROM billing_control.platform_budgets WHERE "
                                 "period_end>now() AND period_start<=now()")):
            db.execute(sa.text("""SELECT billing_api.open_period(
              date_trunc('month',clock_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai',
              10,gen_random_uuid(),gen_random_uuid(),'synthetic PR-06a cycle')"""))

    @asynccontextmanager
    async def connect(role=executor, authorization_id=policy.AUTHORIZATION):
        url = pg.admin_url
        async with await AsyncConnection.connect(host=url.host, port=url.port, dbname=url.database,
                user=role, password=SYNTHETIC_PASSWORD, autocommit=True) as db:
            yield PgLedger(db, authorization_id)

    try:
        connect.executor = executor
        yield pg, tuple(fixtures), connect
    finally:
        with pg.admin.begin() as db:
            # All objects and roles belong to the disposable billing test DB.
            db.exec_driver_sql("DROP VIEW billing_control.pr06a_combined_exposures")
            db.exec_driver_sql("DROP SCHEMA pr06a_api,pr06a_control CASCADE")
            db.exec_driver_sql(f'DROP ROLE "{executor}"')


def open_overlay():
    # Dedicated fixtures can move the authorization window independently of
    # the calendar where CI is run. No client clock override exists in the SQL.
    ddl = (PACKAGE / "install.sql").read_text(encoding="utf-8")
    return fixture_dates(ddl)


def batch_migration():
    return fixture_dates((PACKAGE / "authorization_batches.sql").read_text(encoding="utf-8"))


def fixture_dates(ddl):
    return (ddl.replace("'2026-11-18 00:00:00+08'", "clock_timestamp()-interval '1 day'")
            .replace("'2026-11-19 00:00:00+08'", "clock_timestamp()-interval '1 day'")
            .replace("'2027-01-01 00:00:00+08'::timestamptz", "(clock_timestamp()+interval '1 day')")
            .replace("'2027-01-01 00:00:00+08'", "clock_timestamp()+interval '1 day'"))


class Peer(FakeTransport):
    def __init__(self, attempt, *, mode="normal"):
        super().__init__()
        self.task = str(attempt.task_id)
        self.mode = mode

    def event(self, event, **payload):
        self.feed_json({"header": {"event": event, "task_id": self.task}, "payload": payload})

    def has_pending(self):
        return not self._inbound.empty()

    def has_buffered(self):
        return self.has_pending()

    def dispose(self):
        self.abort()

    async def connect(self, *, before_handshake):
        async with before_handshake():
            await super().connect()
            if self.mode == "auth":
                raise AuthenticationRejected("synthetic handshake rejection")
            if self.mode == "pre-run":
                self.event("task-started")

    async def receive_text(self):
        try:
            return await super().receive_text()
        except Disconnected:
            if self.closed:
                raise policy.EndOfStream("synthetic clean close") from None
            raise

    async def send_json(self, message, *, on_dispatched=None):
        await super().send_json(message, on_dispatched=on_dispatched)
        if message["header"]["action"] == "run-task":
            if self.mode == "partial-regression":
                self.event("task-started", usage={"input_tokens": 100})
                self.event("task-started", usage={"total_tokens": 5})
            elif self.mode == "start-regression":
                self.event("task-started", usage={"input_tokens": 100, "output_tokens": 1})
            elif self.mode == "start-cumulative":
                self.event("task-started", usage={"total_tokens": 180000})
                self.event("task-started", usage={"output_tokens": 0})
            elif self.mode == "start-disputed":
                self.event("task-started", usage={"total_tokens": 180000}, output={
                    "usage": {"output_tokens": 0}, "output": {"usage": {"output_tokens": 1}}})
            elif self.mode != "silent":
                self.event("task-started")
        else:
            if self.mode == "terminal-meter-source":
                for canonical, mirror in ((100, 80), (80, 100), (100, 80)):
                    self.event("task-finished", usage={"input_tokens": canonical, "output_tokens": 0},
                               output={"usage": {"input_tokens": mirror, "output_tokens": 0}})
                return
            if self.mode == "malformed-heartbeat-meter":
                for timestamp, tokens in ((100, 5), (200, 10), (300, 5), (300, 5)):
                    payload = {"header": {"event": "result-generated", "task_id": self.task},
                               "payload": {"usage": {"input_tokens": tokens, "output_tokens": 1},
                                           "output": {"sentence": {"sentence_id": 0, "heartbeat": True,
                                                                   "begin_time": timestamp, "end_time": None,
                                                                   "sentence_end": False, "text": ""}}}}
                    self.feed_text('{"ignored":0,"ignored":1,' + json.dumps(payload)[1:])
                self.event("task-finished", usage={"input_tokens": 10, "output_tokens": 1})
                return
            usage = ({} if self.mode in ("missing", "start-cumulative", "start-disputed") else
                     {"usage": {"input_tokens": 200000 if self.mode == "limit" else 5, "output_tokens": 1}})
            self.event("task-finished", **usage)
            if self.mode == "terminal-cap":
                self.event("task-finished", **usage)
                self.event("task-finished", usage={"input_tokens": 200000, "output_tokens": 1})
            if self.mode == "terminal-invalid":
                self.event("invalid-kind")
                self.event("task-finished", usage={"input_tokens": 200000, "output_tokens": 1})
            if self.mode == "terminal-conflict":
                for _ in range(2):
                    self.event("task-finished", usage={"input_tokens": 200000, "output_tokens": 1})

    async def send_binary(self, frame):
        await super().send_binary(frame)
        if self.mode == "early-terminal":
            self.event("task-finished", usage={"total_tokens": 300000})
            return
        if self.mode == "pending-meter":
            self.event("result-generated", output={"request_id": self.task, "sentence": {
                "sentence_id": 1, "begin_time": 0, "end_time": 100,
                "sentence_end": True, "text": "synthetic high exposure"}},
                usage={"input_tokens": 200000, "output_tokens": 1})
            await self.inbound_started.wait()
            raise Disconnected("synthetic send failure during raw receipt")
        if self.mode == "provisional":
            self.event("result-generated", output={"request_id": self.task, "sentence": {
                "sentence_id": 1, "begin_time": 0, "end_time": 100,
                "sentence_end": True, "text": "synthetic provisional"}},
                usage={"input_tokens": 0, "output_tokens": 0})
            self.feed_disconnect()
            return
        if self.mode == "unconfirmed":
            self.event("result-generated", output={"request_id": self.task, "sentence": {
                "sentence_id": 1, "begin_time": 0, "end_time": 100,
                "sentence_end": True, "text": "synthetic unconfirmed"}})
            await self.sentence_recorded.wait()
            raise Disconnected("synthetic uncertain write")
        if len(self.sent_binary) == 1:
            self.event("result-generated", output={"request_id": self.task, "sentence": {
                "sentence_id": 1, "begin_time": 0, "end_time": 100,
                "sentence_end": True, "text": "synthetic transcript"}},
                usage={"input_tokens": 200000 if self.mode == "limit" else 5, "output_tokens": 1})
            if self.mode == "regression":
                self.event("result-generated", output={"request_id": self.task, "sentence": {
                    "sentence_id": 1, "begin_time": 0, "end_time": 100,
                    "sentence_end": True, "text": "synthetic transcript"}},
                    usage={"input_tokens": 200000, "output_tokens": 0})
            if self.mode == "mirrors":
                self.event("result-generated", output={"request_id": self.task, "sentence": {
                    "sentence_id": 1, "begin_time": 0, "end_time": 100,
                    "sentence_end": True, "text": "synthetic transcript"},
                    "usage": {"input_tokens": 200001, "output_tokens": 1}},
                    usage={"input_tokens": 200000, "output_tokens": 1})
            if self.mode == "foreign":
                self.event("result-generated", output={"request_id": "wrong-request", "sentence": {
                    "sentence_id": 2, "begin_time": 0, "end_time": 100,
                    "sentence_end": True, "text": "synthetic foreign"}})
        if len(self.sent_binary) == 2 and self.mode == "partial":
            self.event("result-generated", output={"request_id": self.task, "sentence": {
                "sentence_id": 2, "begin_time": 100, "end_time": None,
                "sentence_end": False, "text": "synthetic partial"}})


@pytest.mark.parametrize("mode,expected,count", [("normal", None, 3), ("auth", "authentication_rejected", 1),
                                                 ("silent", "timeout", 1),
                                                 ("missing", "usage_unresolved", 1), ("limit", "cost_limit", 1),
                                                 ("foreign", "request_identity_conflict", 1),
                                                 ("regression", "usage_regression", 1),
                                                 ("start-regression", "usage_regression", 1),
                                                 ("partial-regression", "usage_regression", 1),
                                                 ("start-cumulative", "ledger_rejected_or_commit_unknown", 1),
                                                 ("start-disputed", "usage_unresolved", 1),
                                                 ("mirrors", "usage_unresolved", 1),
                                                 ("terminal-conflict", "post_terminal_conflict", 1),
                                                 ("terminal-meter-source", "usage_unresolved", 1),
                                                 ("malformed-heartbeat-meter", "protocol_invalid", 1),
                                                 ("terminal-invalid", "protocol_invalid", 1),
                                                 ("terminal-cap", "inbound_evidence_limit", 1),
                                                 ("early-terminal", "terminal_before_finish", 1),
                                                 ("pre-run", "pre_run_message", 1),
                                                 ("partial", "incomplete_recognition", 1)])
def test_fenced_batch_against_postgres(poc, monkeypatch, mode, expected, count):
    pg, samples, connect = poc
    if mode == "silent":
        monkeypatch.setattr(runner, "MAX_CONNECTION_SECONDS", 0.8)
    if mode == "terminal-cap":
        monkeypatch.setattr(runner, "MAX_INBOUND_PACKETS", 3)
    peers = []

    def create(attempt):
        peer = Peer(attempt, mode=mode)
        peers.append(peer)
        with pg.admin.connect() as db:
            assert db.scalar(sa.text("SELECT count(*) FROM pr06a_control.attempts WHERE attempt=:id"),
                             dict(id=attempt.id)) == 1
        return peer

    async def run():
        async with connect() as ledger:
            return await execute_batch(ledger, samples, REVIEWED, ACCOUNT, FINGERPRINT, create)

    receipts = _run(run())
    assert len(receipts) == len(peers) == count and receipts[-1].stop == expected
    assert all(peer.connect_count == 1 and peer.closed for peer in peers)
    if mode == "normal":
        assert all([kind for kind, _ in peer.sent] == ["json", "binary", "binary", "json"] for peer in peers)
    if mode in ("limit", "foreign", "regression", "mirrors"):
        assert len(peers[0].sent_binary) == 1
    if mode in ("partial", "foreign", "early-terminal"):
        assert receipts[0].state == "unknown"
    if mode == "pre-run":
        assert not peers[0].sent
    if mode in ("partial-regression", "start-cumulative", "start-disputed"):
        assert not peers[0].sent_binary
    with pg.admin.connect() as db:
        batch = db.execute(sa.text("SELECT * FROM pr06a_control.batch")).mappings().one()
        exposure = db.scalar(sa.text("SELECT billing_api.lock_budget(:cycle)"), dict(cycle=batch["period_start"]))
        expected_exposure = {"limit": 1_200_005, "regression": 1_200_005,
                             "mirrors": 1_200_011, "terminal-conflict": 1_200_005,
                             "start-cumulative": 1_080_000, "start-disputed": 1_079_999,
                             "early-terminal": 1_350_000, "terminal-invalid": 1_200_005,
                             "terminal-cap": 1_200_005}.get(mode, 1_000_000)
        assert exposure["unknown"] == exposure["pool_risk"]["asr"] == expected_exposure
        assert batch["input_token_limit"] is None and batch["held_microyuan"] == 1_000_000
        if mode == "terminal-conflict":
            assert db.scalar(sa.text("SELECT metering_revision FROM pr06a_control.attempts")) == 6
            assert db.scalar(sa.text("SELECT metering_issue_pending FROM pr06a_control.attempts"))
        if mode in ("start-cumulative", "start-disputed"):
            assert db.scalar(sa.text("SELECT count(*) FROM pr06a_control.usage")) == 0
            assert db.scalar(sa.text("SELECT count(*) FROM pr06a_control.frames")) == 0
        if mode in ("terminal-meter-source", "malformed-heartbeat-meter"):
            expected_issues = 2 if mode == "terminal-meter-source" else 1
            assert db.scalar(sa.text("SELECT count(*) FROM pr06a_control.metering_issues")) == expected_issues
            assert db.scalar(sa.text("SELECT metering_issue_pending FROM pr06a_control.attempts"))
        if mode == "foreign":
            assert db.scalar(sa.text("SELECT count(*) FROM pr06a_control.recognition")) == 1
            assert db.scalar(sa.text("SELECT code FROM pr06a_control.anomalies")) == "request_identity_conflict"
    with pytest.raises(policy.PocStopped):
        _run(run())


@pytest.mark.parametrize("invalid", ["wrong-login", "wrong-account", "wrong-key", "wrong-candidate",
                                     "wrong-workspace", "wrong-region", "wrong-model", "wrong-endpoint",
                                     "wrong-key-id", "wrong-sample", "revoked", "disabled", "expired",
                                     "platform-risk", "production-switch"])
def test_rejected_before_transport_exists(poc, monkeypatch, invalid):
    pg, samples, connect = poc
    settings = {}
    if invalid in {"wrong-workspace", "wrong-region", "wrong-model", "wrong-endpoint", "wrong-key-id"}:
        from classroom_web.pr06a_real_poc import ledger
        monkeypatch.setattr(ledger, invalid.split("-")[1].upper() if invalid != "wrong-key-id" else "KEY_ID", "wrong")
    if invalid == "wrong-sample":
        samples = samples[1:]
    if invalid == "wrong-login":
        settings["role"] = pg.runtime_url.username
    with pg.admin.begin() as db:
        if invalid in {"revoked", "disabled"}:
            db.exec_driver_sql("UPDATE pr06a_control.activation SET " +
                               ("revoked=true" if invalid == "revoked" else "enabled=false"))
        elif invalid == "expired":
            definition = db.scalar(sa.text("SELECT pg_get_functiondef('pr06a_control.check_activation(text)'::regprocedure)"))
            db.exec_driver_sql(definition.replace("clock_timestamp()+interval '1 day'", "'2000-01-01 00:00:00+08'"),
                               execution_options={"no_parameters": True})
        elif invalid == "production-switch":
            db.exec_driver_sql("""UPDATE billing_control.switches SET flags=flags||'{"billing":true}'::jsonb""")
        elif invalid == "platform-risk":
            db.exec_driver_sql("UPDATE billing_control.platform_budgets SET fixed_microyuan=budget_microyuan "
                               "WHERE period_start<=now() AND period_end>now()")

    def forbidden(_):
        pytest.fail("no socket may be constructed")

    async def run():
        async with connect(**settings) as ledger:
            await execute_batch(ledger, samples, "7" * 40 if invalid == "wrong-candidate" else REVIEWED,
                                "wrong" if invalid == "wrong-account" else ACCOUNT,
                                "0" * 64 if invalid == "wrong-key" else FINGERPRINT, forbidden)

    try:
        with pytest.raises(policy.PocStopped):
            _run(run())
        with pg.admin.connect() as db:
            assert db.scalar(sa.text("SELECT count(*) FROM pr06a_control.batch")) == 0
    finally:
        with pg.admin.begin() as db:
            db.exec_driver_sql("""UPDATE billing_control.switches SET flags=flags||'{"billing":false}'::jsonb""")
            db.exec_driver_sql("UPDATE billing_control.platform_budgets SET fixed_microyuan=100000000 "
                               "WHERE period_start<=now() AND period_end>now()")


@pytest.mark.parametrize("poc", ["legacy"], indirect=True)
def test_migration_and_second_batch_preserve_first_exposure(poc, arm_receipts, monkeypatch):
    pg, samples, connect = poc
    operator, args, _, _, _ = arm_receipts
    args.authorization_id = policy.SECOND_AUTHORIZATION
    args.account_ref = SECOND_ACCOUNT
    args.executor_login = connect.executor

    async def close_first():
        async with connect() as ledger:
            await ledger.open(REVIEWED, ACCOUNT, FINGERPRINT, samples)
            attempt = Attempt.create()
            await ledger.claim(samples[0], attempt)
            await ledger.complete(attempt, "unknown", "connection_failed")

    def first_snapshot(db):
        return tuple(db.scalar(sa.text(f"""SELECT to_jsonb(r) FROM pr06a_control.{table} r
                            WHERE authorization_id=:id"""), {"id": policy.AUTHORIZATION})
                     for table in ("activation", "batch", "attempts"))

    _run(close_first())
    with pg.admin.begin() as db:
        first = first_snapshot(db)
        assert first[1]["closed"] and first[1]["held_microyuan"] == 1_000_000
        db.exec_driver_sql(batch_migration(), execution_options={"no_parameters": True})
        assert first_snapshot(db) == first
        assert db.scalar(sa.text("SELECT count(*) FROM pr06a_control.activation")) == 1
        assert db.scalar(sa.text("SELECT count(*) FROM pr06a_control.batch")) == 1

    async def control_db(arm):
        assert arm
        url = pg.admin_url
        return await AsyncConnection.connect(host=url.host, port=url.port, dbname=url.database,
                                             user=url.username, password=url.password, autocommit=True)

    monkeypatch.setattr(operator, "_db", control_db)
    _run(operator._arm(args, REVIEWED, FINGERPRINT))
    with pytest.raises(errors.UniqueViolation):
        _run(operator._arm(args, REVIEWED, FINGERPRINT))

    peers = []

    def peer(attempt):
        instance = Peer(attempt)
        peers.append(instance)
        return instance

    def forbidden(_):
        pytest.fail("duplicate or invalid second activation reached the transport")

    async def execute_second(factory, reviewed_sha=REVIEWED):
        async with connect(authorization_id=policy.SECOND_AUTHORIZATION) as ledger:
            return await execute_batch(ledger, samples, reviewed_sha, SECOND_ACCOUNT, FINGERPRINT, factory)

    with pytest.raises(policy.PocStopped):
        _run(execute_second(forbidden, reviewed_sha="7" * 40))
    receipts = _run(execute_second(peer))
    assert [(item.slot, item.state, item.stop) for item in receipts] == [
        (1, "succeeded", None), (2, "succeeded", None), (3, "succeeded", None)]
    assert all(transport.connect_count == 1 and len(transport.sent_binary) == 2 and transport.closed
               for transport in peers) and len(peers) == 3
    with pytest.raises(policy.PocStopped):
        _run(execute_second(forbidden))

    second = {"id": policy.SECOND_AUTHORIZATION}
    with pg.admin.begin() as db:
        assert first_snapshot(db) == first
        assert db.execute(sa.text("""SELECT source_sha256,reviewed_sha,account_ref FROM pr06a_control.activation
                                     WHERE authorization_id=:id"""), second).one() == (
            policy.SECOND_AUTH_SHA256, REVIEWED, SECOND_ACCOUNT)
        batch = db.execute(sa.text("""SELECT held_microyuan,closed,period_start FROM pr06a_control.batch
                                      WHERE authorization_id=:id"""), second).one()
        assert batch[:2] == (1_000_000, True)
        exposure = db.scalar(sa.text("SELECT billing_api.lock_budget(:cycle)"), {"cycle": batch.period_start})
        assert exposure["unknown"] == exposure["pool_risk"]["asr"] == 2_000_000
        release = sa.text("SELECT pr06a_api.release_reconciled(:id)")
        with pytest.raises(sa.exc.DBAPIError, match="PR06A_EXPOSURE_UNRESOLVED"), db.begin_nested():
            db.execute(release, second)
        db.execute(sa.text("SELECT pr06a_api.revoke(:id)"), second)
        assert db.scalar(sa.text("SELECT authorization_id FROM pr06a_control.revocation")) == policy.SECOND_AUTHORIZATION
        attempts = db.execute(sa.text("""SELECT attempt,request_id,input_tokens,output_tokens,metering_revision
                                     FROM pr06a_control.attempts WHERE authorization_id=:id"""), second).mappings().all()
        bill = sa.text("""SELECT pr06a_api.settle(:attempt,1,:request_id,:input_tokens,:output_tokens,0,0,
                         '2027-01',:evidence,:metering_revision,true)""")
        for attempt in attempts:
            db.execute(bill, dict(attempt) | {"evidence": "d" * 64})
        db.execute(release, second)
        assert first_snapshot(db) == first
        assert db.execute(sa.text("""SELECT held_microyuan,released FROM pr06a_control.batch
                                     WHERE authorization_id=:id"""), second).one() == (0, True)


@pytest.mark.parametrize("poc", ["legacy"], indirect=True)
def test_batch_migration_refuses_an_open_first_batch(poc):
    pg, samples, connect = poc

    async def open_first():
        async with connect() as ledger:
            await ledger.open(REVIEWED, ACCOUNT, FINGERPRINT, samples)

    _run(open_first())
    with pg.admin.begin() as db:
        with pytest.raises(sa.exc.DBAPIError, match="PR06A_BATCH_MIGRATION_REQUIRES_CLOSED_BATCH"), db.begin_nested():
            db.exec_driver_sql(batch_migration(), execution_options={"no_parameters": True})
        assert db.execute(sa.text("SELECT closed,held_microyuan FROM pr06a_control.batch")).one() == (False, 1_000_000)
        assert db.scalar(sa.text("SELECT to_regprocedure('pr06a_api.revoke()') IS NOT NULL"))
        assert not db.scalar(sa.text("SELECT to_regprocedure('pr06a_api.revoke(text)') IS NOT NULL"))


def test_concurrent_last_slot_and_unconfirmed_ranges(poc):
    _, samples, connect = poc

    async def race(ledger, other, sample):
        other.fence = ledger.fence
        attempt = Attempt.create()
        accepted, rejected = await asyncio.gather(ledger.claim(sample, attempt), other.claim(sample, Attempt.create()),
                                                 return_exceptions=True)
        assert accepted is None and isinstance(rejected, policy.PocStopped)
        return attempt

    async def run():
        async with connect() as ledger, connect() as other:
            await ledger.db.execute("CREATE TEMP TABLE pg_stat_activity(pid integer, backend_start timestamptz)")
            await ledger.open(REVIEWED, ACCOUNT, FINGERPRINT, samples)
            for sample in samples[:2]:
                attempt = Attempt.create()
                await ledger.claim(sample, attempt)
                await runner._Session(Peer(attempt), ledger, attempt, sample).run(asyncio.get_running_loop().time() + 5)
                await ledger.complete(attempt, "succeeded", None)
            sample = samples[2]
            attempt = await race(ledger, other, sample)
            await ledger.frame(attempt, 0, 1600, policy.digest(sample.pcm[:3200]))
            with pytest.raises(policy.PocStopped):
                await ledger.frame(attempt, 1600, 3200, "a" * 64)
            await ledger.confirm_frame(attempt, 0)
            with pytest.raises(policy.PocStopped):
                await ledger.frame(attempt, 1600, 3201, "a" * 64)
            with pytest.raises(policy.PocStopped):
                await ledger.claim(sample, Attempt.create())
            with pytest.raises(policy.PocStopped):
                await ledger._change("SELECT pr06a_api.claim_attempt(%s,4,%s,%s,%s)",
                                     uuid4(), uuid4(), uuid4())
    _run(run())


def test_receipt_risk_and_attribution_are_atomic(poc):
    pg, samples, connect = poc

    async def run():
        async with connect() as ledger:
            await ledger.open(REVIEWED, ACCOUNT, FINGERPRINT, samples)
            attempt = Attempt.create()
            await ledger.claim(samples[0], attempt)
            packet = dict(raw_hash="a" * 64, sequence=1, control=1, frame=0, payload="{}",
                          input=200000, output=-1)
            with pytest.raises(policy.PocStopped):
                await ledger.retain(attempt, packet)
            with pg.admin.connect() as db:
                assert db.scalar(sa.text("SELECT count(*) FROM pr06a_control.inbound")) == 0
                assert db.scalar(sa.text("SELECT observed_input FROM pr06a_control.attempts")) is None
            assert await ledger.retain(attempt, packet | dict(output=1, request_fingerprint="b" * 64))
            assert not await ledger.retain(attempt, packet | dict(sequence=2, request_id="other-request", output=9))
            with pg.admin.connect() as db:
                assert db.scalar(sa.text("SELECT count(*) FROM pr06a_control.inbound")) == 2
                assert db.execute(sa.text("""SELECT observed_input,observed_output,request_id,redacted_request_fingerprint
                    FROM pr06a_control.attempts""")).one() == (200000, 1, None, "b" * 64)
                assert db.scalar(sa.text("SELECT code FROM pr06a_control.anomalies")) == "request_identity_conflict"
                revision = db.scalar(sa.text("SELECT metering_revision FROM pr06a_control.attempts"))
            await ledger.complete(attempt, "unknown", "synthetic_close")
            with pg.admin.begin() as db:
                bill = sa.text("""SELECT pr06a_api.settle(:attempt,1,:request,200000,1,1200005,1200005,
                  '2027-01',:proof,:revision,true,:fingerprint)""")
                args = dict(attempt=attempt.id, proof="c" * 64, revision=revision)
                with pytest.raises(sa.exc.DBAPIError, match="PR06A_BILL_ATTRIBUTION_CONFLICT"), db.begin_nested():
                    db.execute(bill, args | dict(request="wrong-clean-id", fingerprint=None))
                db.execute(bill, args | dict(request=None, fingerprint="b" * 64))
                db.execute(sa.text("SELECT pr06a_api.release_reconciled(:id)"), {"id": policy.AUTHORIZATION})
                assert db.scalar(sa.text("SELECT request_id FROM pr06a_control.bill_evidence")) is None

    _run(run())


def test_revoke_before_arm_is_durable(poc):
    pg, _, _ = poc
    with pg.admin.begin() as db:
        original = db.scalar(sa.text("SELECT to_jsonb(a) FROM pr06a_control.activation a"))
        db.exec_driver_sql("DELETE FROM pr06a_control.activation")
        db.execute(sa.text("SELECT pr06a_api.revoke(:id)"), {"id": policy.AUTHORIZATION})
        assert db.scalar(sa.text("SELECT count(*) FROM pr06a_control.revocation")) == 1
        with pytest.raises(sa.exc.DBAPIError, match="PR06A_AUTHORIZATION_REVOKED"), db.begin_nested():
            db.execute(sa.text("""INSERT INTO pr06a_control.activation
              SELECT * FROM jsonb_populate_record(NULL::pr06a_control.activation,cast(:row AS jsonb))"""),
                       dict(row=json.dumps(original)))


def test_control_revoke_serializes_with_dispatch(poc):
    pg, samples, connect = poc

    def revoke():
        with pg.admin.begin() as db:
            db.execute(sa.text("SELECT pr06a_api.revoke(:id)"), {"id": policy.AUTHORIZATION})

    async def run():
        async with connect() as ledger, asyncio.timeout(5):
            await ledger.open(REVIEWED, ACCOUNT, FINGERPRINT, samples)
            attempt = Attempt.create()
            await ledger.claim(samples[0], attempt)
            async with ledger.dispatch_lock():
                task = asyncio.create_task(asyncio.to_thread(revoke))
                while True:
                    with pg.admin.connect() as db:
                        waiting = db.scalar(sa.text("""SELECT count(*) FROM pg_locks
                          WHERE locktype='advisory' AND classid=6206 AND objid=1
                            AND objsubid=2 AND NOT granted AND mode='ExclusiveLock'"""))
                        assert not db.scalar(sa.text("SELECT revoked FROM pr06a_control.activation"))
                    if waiting:
                        break
                    await asyncio.sleep(0.01)
                assert not task.done()
            await task
            with pytest.raises(policy.PocStopped):
                await ledger.frame(attempt, 0, 1600, "a" * 64)

    _run(run())


@pytest.mark.parametrize("checkpoint", ["before", "after"])
def test_executor_crash_does_not_open_again(poc, tmp_path, checkpoint):
    pg, samples, connect = poc
    script = tmp_path / "synthetic_poc_crash.py"
    script.write_text("""
import json
import os
from uuid import uuid4
from psycopg import connect
from psycopg.types.json import Jsonb

request = json.loads(os.environ["PR06A_SYNTHETIC_TEST_REQUEST"])
with connect(**request["database"], autocommit=True) as db:
    fence = uuid4()
    db.execute("SELECT pr06a_api.open_batch(%s,%s,%s,%s)",
        (fence,Jsonb(request["identity"]),request["fingerprint"],Jsonb(request["samples"])))
    with db.transaction():
        db.execute("SELECT pr06a_api.claim_attempt(%s,1,%s,%s,%s)", (fence,uuid4(),uuid4(),uuid4()))
        if request["checkpoint"] == "before":
            os._exit(73)
    os._exit(73)
""", encoding="utf-8")
    url = pg.admin_url
    request = {"database": {"host": url.host, "port": url.port, "dbname": url.database,
                           "user": connect.executor, "password": SYNTHETIC_PASSWORD},
               "identity": {"authorization": policy.AUTHORIZATION, "source_sha256": policy.AUTH_SHA256,
                            "baseline": policy.BASELINE, "reviewed_sha": REVIEWED, "workspace": policy.WORKSPACE,
                            "key_id": policy.KEY_ID, "region": policy.REGION, "model": policy.MODEL,
                            "endpoint": policy.ENDPOINT, "account_ref": ACCOUNT},
               "fingerprint": FINGERPRINT, "samples": [asdict(s.spec) for s in samples], "checkpoint": checkpoint}
    child = subprocess.run([sys.executable, "-B", str(script)], timeout=20, capture_output=True,
                           env=dict(os.environ, PR06A_SYNTHETIC_TEST_REQUEST=json.dumps(request)))
    assert child.returncode == 73, child.stderr.decode("utf-8", errors="replace")
    with pg.admin.begin() as db:
        assert db.scalar(sa.text("SELECT count(*) FROM pr06a_control.attempts")) == int(checkpoint == "after")
        assert db.scalar(sa.text("SELECT held_microyuan FROM pr06a_control.batch")) == 1000000
        # Model a new Beijing month with the original batch and exposure intact.
        cycle = "(date_trunc('month',clock_timestamp() AT TIME ZONE 'Asia/Shanghai') + interval '1 month')"
        db.exec_driver_sql(f"""SELECT billing_api.open_period(
          {cycle} AT TIME ZONE 'Asia/Shanghai',10,gen_random_uuid(),gen_random_uuid(),'synthetic later cycle')
          """)
        definition = db.scalar(sa.text(
            "SELECT pg_get_functiondef('pr06a_api.open_batch(uuid,jsonb,text,jsonb)'::regprocedure)"))
        db.exec_driver_sql(definition.replace("date_trunc('month',t AT TIME ZONE 'Asia/Shanghai')", cycle),
                           execution_options={"no_parameters": True})

    async def restart():
        async with connect() as ledger:
            await ledger.open(REVIEWED, ACCOUNT, FINGERPRINT, samples)

    try:
        with pytest.raises(policy.PocStopped):
            _run(restart())
    finally:
        with pg.admin.begin() as db:
            db.exec_driver_sql("DELETE FROM billing_control.platform_budgets WHERE period_start>clock_timestamp()")


@pytest.mark.parametrize("mode", ["stamp", "unconfirmed", "pending-failure", "revoked-after-intent", "pending-meter"])
def test_frame_and_sentence_evidence_preserves_dispatch_boundary(poc, monkeypatch, mode):
    pg, samples, connect = poc
    peers = []
    original_frame = PgLedger.frame
    original_sentence = PgLedger.sentence
    original_retain = PgLedger.retain

    async def retain_packet(self, attempt, packet):
        if mode == "pending-meter" and packet.get("frame"):
            peers[0].inbound_started.set()
            await asyncio.sleep(0.05)
        return await original_retain(self, attempt, packet)

    async def reserve_frame(self, attempt, start, end, checksum):
        await original_frame(self, attempt, start, end, checksum)
        if mode == "revoked-after-intent":
            with pg.admin.begin() as db:
                db.exec_driver_sql("UPDATE pr06a_control.activation SET revoked=true")
        if mode == "pending-failure" and start > 0:
            peers[0].feed_json({"header": {"event": "task-failed", "task_id": str(attempt.task_id),
                                           "error_code": "synthetic", "error_message": "synthetic failure"},
                               "payload": {}})
            await asyncio.sleep(0)

    async def record_sentence(self, attempt, sentence, revision, required_sample):
        await original_sentence(self, attempt, sentence, revision, required_sample)
        peers[0].sentence_recorded.set()

    monkeypatch.setattr(PgLedger, "frame", reserve_frame)
    monkeypatch.setattr(PgLedger, "sentence", record_sentence)
    monkeypatch.setattr(PgLedger, "retain", retain_packet)

    def create(attempt):
        peer = Peer(attempt, mode=mode)
        peer.sentence_recorded = asyncio.Event()
        peer.inbound_started = asyncio.Event()
        peers.append(peer)
        if mode == "stamp":
            def fail(_):
                raise RuntimeError("synthetic stamp failure")
            peer.note_audio_attempt = fail
        return peer

    async def run():
        async with connect() as ledger:
            return await execute_batch(ledger, samples, REVIEWED, ACCOUNT, FINGERPRINT, create)

    receipt, = _run(run())
    assert receipt.state == ("failed" if mode == "pending-failure" else "unknown")
    assert len(peers[0].sent_binary) == (0 if mode in ("stamp", "revoked-after-intent") else 1)
    with pg.admin.connect() as db:
        frames = db.execute(sa.text("SELECT disposition FROM pr06a_control.frames ORDER BY start_sample")).scalars().all()
        assert frames == (["confirmed", "not_sent"] if mode == "pending-failure"
                          else ["not_sent"] if mode in ("stamp", "revoked-after-intent") else ["intent"])
        if mode == "pending-meter":
            exposure = db.scalar(sa.text("SELECT billing_api.lock_budget(period_start) FROM pr06a_control.batch"))
            assert exposure["unknown"] == 1200005
        if mode == "unconfirmed":
            assert db.execute(sa.text("""SELECT r.required_sample,f.disposition FROM pr06a_control.recognition r
                JOIN pr06a_control.frames f ON f.attempt=r.attempt AND f.end_sample=r.required_sample""")).one() == (
                1600,"intent")


@pytest.mark.parametrize("second_mode", ["auth", "provisional"])
def test_bill_revision_cannot_clear_unmetered_attempt(poc, second_mode):
    pg, samples, connect = poc

    peers = []

    def create(attempt):
        peer = Peer(attempt, mode=second_mode if peers else "normal")
        peers.append(peer)
        return peer

    async def run():
        async with connect() as ledger:
            return await execute_batch(ledger, samples, REVIEWED, ACCOUNT, FINGERPRINT, create)
    assert [r.state for r in _run(run())] == ["succeeded", "failed" if second_mode == "auth" else "unknown"]

    with pg.admin.begin() as db:
        first, second = db.execute(sa.text("SELECT * FROM pr06a_control.attempts ORDER BY slot")).mappings()
        bill = sa.text("""SELECT pr06a_api.settle(:id,:rev,:request,:i,:o,:gross,:gross,
          '2027-01',:hash,:metering,false)""")
        evidence = dict(id=first["attempt"], request=first["request_id"], i=5, o=1, gross=1100000)
        db.execute(bill, evidence | dict(rev=1, metering=0, hash="a" * 64))
        assert db.scalar(sa.text("SELECT reconciled_metering_revision FROM pr06a_control.attempts WHERE slot=1")) is None
        ledger = db.scalar(sa.text("SELECT billing_api.lock_budget(period_start) FROM pr06a_control.batch"))
        assert ledger["settled"] == 1100000 and ledger["unknown"] == 1000000
        with pytest.raises(sa.exc.DBAPIError), db.begin_nested():
            db.execute(sa.text("SELECT pr06a_api.release_reconciled(:id)"), {"id": policy.AUTHORIZATION})
        db.execute(bill, evidence | dict(rev=2, metering=first["metering_revision"], hash="b" * 64))
        db.execute(bill, dict(id=second["attempt"], rev=1, request=second["request_id"], i=0, o=0, gross=0,
                              metering=second["metering_revision"], hash="c" * 64))
        db.execute(sa.text("SELECT pr06a_api.release_reconciled(:id)"), {"id": policy.AUTHORIZATION})
        assert db.scalar(sa.text("SELECT overrun_microyuan FROM pr06a_control.batch")) == 100000
        ledger = db.scalar(sa.text("SELECT billing_api.lock_budget(period_start) FROM pr06a_control.batch"))
        assert ledger["settled"] == 1100000 and ledger["unknown"] == 0


def test_reconcile_keeps_unresolved_hold_and_books_late_overspend(poc):
    pg, samples, connect = poc

    async def run():
        async with connect() as ledger:
            await ledger.open(REVIEWED, ACCOUNT, FINGERPRINT, samples)
            attempt = Attempt.create()
            await ledger.claim(samples[0], attempt)
            await ledger.complete(attempt, "unknown", "synthetic_disconnect")
            return attempt

    attempt = _run(run())
    with pg.admin.begin() as db:
        with pytest.raises(sa.exc.DBAPIError), db.begin_nested():
            db.execute(sa.text("SELECT pr06a_api.release_reconciled(:id)"), {"id": policy.AUTHORIZATION})
        bill = sa.text("""SELECT pr06a_api.settle(:id,:rev,'verified-request',2,3,:gross,:net,'2027-01',:hash,0,false)""")
        args = dict(id=attempt.id, rev=1, gross=200000, net=150000, hash="a" * 64)
        db.execute(bill, args)
        db.execute(bill, args)
        assert db.execute(sa.text("SELECT held_microyuan,confirmed_microyuan FROM pr06a_control.batch")).one() == (
            800000, 200000)
        db.execute(bill, args | dict(rev=2, gross=1100000, net=950000, hash="b" * 64))
        db.execute(sa.text("SELECT pr06a_api.release_reconciled(:id)"), {"id": policy.AUTHORIZATION})
        assert db.execute(sa.text("SELECT held_microyuan,confirmed_microyuan FROM pr06a_control.batch")).one() == (
            0, 1100000)
        b = db.scalar(sa.text("SELECT billing_api.lock_budget(period_start) FROM pr06a_control.batch"))
        assert b["settled"] == b["pool_risk"]["asr"] == 1100000
