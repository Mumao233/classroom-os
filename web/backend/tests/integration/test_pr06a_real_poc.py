"""PostgreSQL-backed PR-06a gates with synthetic PCM and zero paid I/O."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from psycopg import AsyncConnection
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
    async def connect(role=executor):
        url = pg.admin_url
        async with await AsyncConnection.connect(host=url.host, port=url.port, dbname=url.database,
                user=role, password=SYNTHETIC_PASSWORD, autocommit=True) as db:
            yield PgLedger(db)

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
    return (ddl.replace("'2026-11-18 00:00:00+08'", "clock_timestamp()-interval '1 day'")
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

    async def connect(self, *, before_handshake):
        await before_handshake()
        await super().connect()
        if self.mode == "auth":
            raise AuthenticationRejected("synthetic handshake rejection")

    async def send_json(self, message, *, on_dispatched=None):
        await super().send_json(message, on_dispatched=on_dispatched)
        if message["header"]["action"] == "run-task":
            if self.mode != "silent":
                self.event("task-started")
        else:
            usage = {} if self.mode == "missing" else {"usage": {"input_tokens": 200000 if self.mode == "limit" else 5,
                                                                  "output_tokens": 1}}
            self.event("task-finished", **usage)
            if self.mode == "terminal-conflict":
                self.event("task-finished", usage={"input_tokens": 200000, "output_tokens": 1})

    async def send_binary(self, frame):
        await super().send_binary(frame)
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
                                                 ("mirrors", "usage_unresolved", 1),
                                                 ("terminal-conflict", "post_terminal_conflict", 1),
                                                 ("partial", "incomplete_recognition", 1)])
def test_fenced_batch_against_postgres(poc, monkeypatch, mode, expected, count):
    pg, samples, connect = poc
    if mode == "silent":
        monkeypatch.setattr(runner, "MAX_CONNECTION_SECONDS", 0.8)
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
    if mode in ("partial", "foreign"):
        assert receipts[0].state == "unknown"
    with pg.admin.connect() as db:
        batch = db.execute(sa.text("SELECT * FROM pr06a_control.batch")).mappings().one()
        exposure = db.scalar(sa.text("SELECT billing_api.lock_budget(:cycle)"), dict(cycle=batch["period_start"]))
        expected_exposure = {"limit": 1_200_005, "regression": 1_200_005,
                             "mirrors": 1_200_011, "terminal-conflict": 1_200_005}.get(mode, 1_000_000)
        assert exposure["unknown"] == exposure["pool_risk"]["asr"] == expected_exposure
        assert batch["input_token_limit"] is None and batch["held_microyuan"] == 1_000_000
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
            definition = db.scalar(sa.text("SELECT pg_get_functiondef('pr06a_control.check_activation()'::regprocedure)"))
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
    original_inbound = PgLedger.inbound

    async def record_inbound(self, attempt, sequence, frame):
        if mode == "pending-meter" and frame.audio_attempt:
            peers[0].inbound_started.set()
            await asyncio.sleep(0.05)
        await original_inbound(self, attempt, sequence, frame)

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
    monkeypatch.setattr(PgLedger, "inbound", record_inbound)

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
            db.exec_driver_sql("SELECT pr06a_api.release_reconciled()")
        db.execute(bill, evidence | dict(rev=2, metering=first["metering_revision"], hash="b" * 64))
        db.execute(bill, dict(id=second["attempt"], rev=1, request=second["request_id"], i=0, o=0, gross=0,
                              metering=second["metering_revision"], hash="c" * 64))
        db.exec_driver_sql("SELECT pr06a_api.release_reconciled()")
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
            db.exec_driver_sql("SELECT pr06a_api.release_reconciled()")
        bill = sa.text("""SELECT pr06a_api.settle(:id,:rev,'verified-request',2,3,:gross,:net,'2027-01',:hash,0,false)""")
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
