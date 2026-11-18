"""PostgreSQL-backed PR-06a gates with synthetic PCM and zero paid I/O."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict
import json
from pathlib import Path
from threading import Barrier
from uuid import uuid4

from psycopg import AsyncConnection
import pytest
import sqlalchemy as sa
from test_billing import billing_pg

from classroom_web.asr_dashscope.transport import AuthenticationRejected, FakeTransport
from classroom_web.pr06a_real_poc import policy
from classroom_web.pr06a_real_poc.ledger import Attempt, PgLedger
from classroom_web.pr06a_real_poc.runner import execute_batch
from classroom_web.test_support import SYNTHETIC_PASSWORD

REVIEWED = "8" * 40
ACCOUNT = "synthetic-console-attestation"
FINGERPRINT = policy.digest(b"synthetic-pr06a-key")
PACKAGE = Path(policy.__file__).parent


def _run(coro):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(coro)


@pytest.fixture
def poc(billing_pg):
    pg = billing_pg
    executor = "pr06a_executor_" + uuid4().hex[:16]
    with pg.admin.begin() as db:
        db.exec_driver_sql(f'CREATE ROLE "{executor}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
        db.exec_driver_sql(f'ALTER ROLE "{executor}" PASSWORD \'{SYNTHETIC_PASSWORD}\'')
        db.exec_driver_sql(open_overlay(), execution_options={"no_parameters": True})
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
          authorization,source_sha256,baseline,reviewed_sha,review_receipt,approval_ref,account_ref,
          workspace,key_id,region,model,endpoint,credential_sha256,executor_login,enabled)
          VALUES(:auth,:source,:base,:sha,:receipt,'synthetic approval',:account,:workspace,:key_id,
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
    async def connect(role=executor):
        url = pg.admin_url
        async with await AsyncConnection.connect(host=url.host, port=url.port, dbname=url.database,
                user=role, password=SYNTHETIC_PASSWORD, autocommit=True) as db:
            yield PgLedger(db)

    try:
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
    return ddl.replace("'2026-11-18 00:00:00+08'", "clock_timestamp()-interval '1 day'").replace(
        "'2027-01-01 00:00:00+08'", "clock_timestamp()+interval '1 day'")


class Peer(FakeTransport):
    def __init__(self, attempt, *, mode="normal"):
        super().__init__()
        self.task = str(attempt.task_id)
        self.mode = mode

    def event(self, event, **payload):
        self.feed_json({"header": {"event": event, "task_id": self.task}, "payload": payload})

    async def connect(self):
        await super().connect()
        if self.mode == "auth":
            raise AuthenticationRejected("synthetic handshake rejection")

    async def send_json(self, message, *, on_dispatched=None):
        await super().send_json(message, on_dispatched=on_dispatched)
        if message["header"]["action"] == "run-task":
            self.event("task-started")
        else:
            usage = {} if self.mode == "missing" else {"usage": {"input_tokens": 200000 if self.mode == "limit" else 5,
                                                                  "output_tokens": 1}}
            self.event("task-finished", **usage)

    async def send_binary(self, frame):
        await super().send_binary(frame)
        if len(self.sent_binary) == 1:
            self.event("result-generated", output={"request_id": self.task, "sentence": {
                "sentence_id": 1, "begin_time": 0, "end_time": 100,
                "sentence_end": True, "text": "synthetic transcript"}},
                usage={"input_tokens": 200000 if self.mode == "limit" else 5, "output_tokens": 1})


@pytest.mark.parametrize("mode,expected,count", [("normal", None, 3), ("auth", "authentication_rejected", 1),
                                                 ("missing", "usage_unresolved", 1), ("limit", "cost_limit", 1)])
def test_fenced_batch_against_postgres(poc, mode, expected, count):
    pg, samples, connect = poc
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
    if mode == "limit":
        assert len(peers[0].sent_binary) == 1
    with pg.admin.connect() as db:
        batch = db.execute(sa.text("SELECT * FROM pr06a_control.batch")).mappings().one()
        exposure = db.scalar(sa.text("SELECT billing_api.lock_budget(:cycle)"), dict(cycle=batch["period_start"]))
        assert exposure["unknown"] == exposure["pool_risk"]["asr"] == 1_000_000
        assert batch["input_token_limit"] is None and batch["held_microyuan"] == 1_000_000
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
            definition = db.scalar(sa.text("SELECT pg_get_functiondef('pr06a_control.check_activation()'::regprocedure)"))
            db.exec_driver_sql(definition.replace("clock_timestamp()+interval '1 day'", "'2000-01-01 00:00:00+08'"))
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


def test_concurrent_claim_and_unconfirmed_ranges(poc):
    pg, samples, connect = poc
    barrier = Barrier(2)

    async def open_batch():
        async with connect() as ledger:
            await ledger.open(REVIEWED, ACCOUNT, FINGERPRINT, samples)
            return ledger.fence

    fence = _run(open_batch())

    def claim():
        async def run():
            async with connect() as ledger:
                ledger.fence = fence
                attempt = Attempt.create()
                barrier.wait(timeout=5)
                try:
                    await ledger.claim(samples[0], attempt)
                except policy.PocStopped:
                    return None
                return attempt
        return _run(run())

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = [f.result(timeout=8) for f in [pool.submit(claim), pool.submit(claim)]]
    attempt, = [a for a in claims if a is not None]

    async def frames():
        async with connect() as ledger:
            ledger.fence = fence
            await ledger.frame(attempt, 0, 1600, policy.digest(samples[0].pcm[:3200]))
            with pytest.raises(policy.PocStopped):
                await ledger.frame(attempt, 1600, 3200, "a" * 64)
            await ledger.confirm_frame(attempt, 0)
            with pytest.raises(policy.PocStopped):
                await ledger.frame(attempt, 1600, 3201, "a" * 64)
            with pytest.raises(policy.PocStopped):
                await ledger.claim(samples[0], Attempt.create())
            with pytest.raises(policy.PocStopped):
                await ledger.claim(samples[1], Attempt.create())
    _run(frames())


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
            db.exec_driver_sql("SELECT pr06a_api.release_reconciled()")
        bill = sa.text("""SELECT pr06a_api.settle(:id,:rev,'verified-request',2,3,:gross,:net,'2027-01',:hash)""")
        args = dict(id=attempt.id, rev=1, gross=200000, net=150000, hash="a" * 64)
        db.execute(bill, args)
        db.execute(bill, args)
        assert db.execute(sa.text("SELECT held_microyuan,confirmed_microyuan FROM pr06a_control.batch")).one() == (
            800000, 200000)
        db.execute(bill, args | dict(rev=2, gross=1100000, net=950000, hash="b" * 64))
        db.exec_driver_sql("SELECT pr06a_api.release_reconciled()")
        assert db.execute(sa.text("SELECT held_microyuan,confirmed_microyuan FROM pr06a_control.batch")).one() == (
            0, 1100000)
        b = db.scalar(sa.text("SELECT billing_api.lock_budget(period_start) FROM pr06a_control.batch"))
        assert b["settled"] == b["pool_risk"]["asr"] == 1100000
