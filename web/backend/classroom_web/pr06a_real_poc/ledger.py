"""Narrow transaction client for the isolated PG execution role."""
import asyncio
from dataclasses import asdict, dataclass
import logging
from uuid import UUID, uuid4

from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from ..asr_dashscope.wire import MODEL, REGION
from .policy import AUTHORIZATION, AUTH_SHA256, BASELINE, ENDPOINT, KEY_ID, WORKSPACE, PocStopped


@dataclass(frozen=True)
class Attempt:
    id: UUID
    submission_key: UUID
    task_id: UUID

    @classmethod
    def create(cls):
        return cls(uuid4(), uuid4(), uuid4())


class PgLedger:
    def __init__(self, db):
        if not db.autocommit:
            raise PocStopped("top_level_transactions_required")
        self.db = db
        self.fence = uuid4()
        self._lock = asyncio.Lock()

    async def _change(self, statement, *args):
        try:
            async with self._lock:
                if self.db.info.transaction_status != TransactionStatus.IDLE:
                    raise PocStopped("top_level_transactions_required")
                async with self.db.transaction():
                    await self.db.execute("SET LOCAL statement_timeout = '900ms'")
                    await self.db.execute("SET LOCAL lock_timeout = '600ms'")
                    await self.db.execute("SET LOCAL synchronous_commit = 'on'")
                    cursor = await self.db.execute(statement, (self.fence, *args))
                    row = await cursor.fetchone()
            # The caller gets no permit until the transaction manager has
            # confirmed COMMIT. Errors and cancellation never yield a permit.
            return row[0]
        except Exception:
            raise PocStopped("ledger_rejected_or_commit_unknown") from None

    async def open(self, reviewed_sha, account_ref, credential_fingerprint, samples):
        identity = {
            "authorization": AUTHORIZATION, "source_sha256": AUTH_SHA256,
            "baseline": BASELINE, "reviewed_sha": reviewed_sha, "workspace": WORKSPACE,
            "key_id": KEY_ID, "region": REGION, "model": MODEL, "endpoint": ENDPOINT,
            "account_ref": account_ref,
        }
        await self._change("SELECT pr06a_api.open_batch(%s,%s,%s,%s)",
                           Jsonb(identity), credential_fingerprint, Jsonb([asdict(s.spec) for s in samples]))

    async def claim(self, sample, attempt):
        await self._change("SELECT pr06a_api.claim_attempt(%s,%s,%s,%s,%s)",
                           sample.spec.slot, attempt.id, attempt.submission_key, attempt.task_id)

    async def protocol(self, attempt, event):
        await self._change("SELECT pr06a_api.protocol(%s,%s,%s)", attempt.id, event)

    async def check_connection(self, attempt):
        await self._change("SELECT pr06a_api.check_connection(%s,%s)", attempt.id)

    async def frame(self, attempt, start, end, frame_hash):
        await self._change("SELECT pr06a_api.frame_intent(%s,%s,%s,%s,%s)", attempt.id, start, end, frame_hash)

    async def confirm_frame(self, attempt, start):
        await self._change("SELECT pr06a_api.confirm_frame(%s,%s,%s)", attempt.id, start)

    async def observe(self, attempt, request, usage, final):
        return await self._change("SELECT pr06a_api.observe(%s,%s,%s,%s,%s,%s)",
                                  attempt.id, request, usage.input_tokens if usage else None,
                                  usage.output_tokens if usage else None, final)

    async def sentence(self, attempt, sentence, revision):
        await self._change("SELECT pr06a_api.record_sentence(%s,%s,%s,%s,%s,%s,%s,%s)",
                           attempt.id, sentence.sentence_id, revision, sentence.begin_ms,
                           sentence.end_ms, sentence.final, sentence.text)

    async def complete(self, attempt, state, reason):
        await self._change("SELECT pr06a_api.complete(%s,%s,%s,%s)", attempt.id if attempt else None, state, reason)


def quiet_database_logger():
    # psycopg may log cancellation and server notices, including server text.
    logger = logging.getLogger("psycopg")
    logger.handlers[:] = [logging.NullHandler()]
    logger.propagate = False
    logger.setLevel(logging.CRITICAL + 1)
