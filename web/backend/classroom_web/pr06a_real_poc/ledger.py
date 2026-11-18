"""Narrow transaction client for the isolated PG execution role."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
import logging
from uuid import UUID, uuid4

from psycopg import Error
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from ..asr_dashscope.wire import MODEL, REGION
from .policy import AUTHORIZATION, AUTH_SHA256, BASELINE, ENDPOINT, KEY_ID, WORKSPACE, PocStopped, RequestConflict


@dataclass(frozen=True)
class Attempt:
    id: UUID
    submission_key: UUID
    task_id: UUID

    @classmethod
    def create(cls):
        return cls(uuid4(), uuid4(), uuid4())


async def finish_on_cancel(work):
    operation = asyncio.create_task(work)
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        while not operation.done():
            try:
                await asyncio.wait((operation,))
            except asyncio.CancelledError:
                continue
        if not operation.cancelled():
            operation.exception()
        raise


class PgLedger:
    def __init__(self, db):
        if not db.autocommit:
            raise PocStopped("top_level_transactions_required")
        self.db = db
        self.fence = uuid4()
        self._lock = asyncio.Lock()

    async def _transaction(self, statement, args):
        if self.db.info.transaction_status != TransactionStatus.IDLE:
            raise PocStopped("top_level_transactions_required")
        try:
            async with asyncio.timeout(2), self.db.transaction():
                await self.db.execute("SET LOCAL statement_timeout = '900ms'")
                await self.db.execute("SET LOCAL lock_timeout = '600ms'")
                await self.db.execute("SET LOCAL synchronous_commit = 'on'")
                cursor = await self.db.execute(statement, (self.fence, *args))
                row = await cursor.fetchone()
            return row[0]
        except TimeoutError:
            # Closing the PG session fences any unresolved transaction.
            await self.db.close()
            raise

    async def _change(self, statement, *args):
        try:
            async with self._lock:
                # No permit leaves this method before COMMIT. Cancelling its
                # caller cannot interrupt BEGIN or COMMIT on the shared socket.
                return await finish_on_cancel(self._transaction(statement, args))
        except Exception as exc:
            if isinstance(exc, Error) and exc.sqlstate == "P6201":
                raise RequestConflict("request_identity_conflict") from None
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

    @asynccontextmanager
    async def dispatch_lock(self):
        try:
            await self._change("SELECT pr06a_api.lock_dispatch(%s,true)")
            yield
        finally:
            try:
                await self._change("SELECT pr06a_api.lock_dispatch(%s,false)")
            except (PocStopped, asyncio.CancelledError):
                await self.db.close()
                raise

    async def check_connection(self, attempt, *, handshake=False):
        return await self._change("SELECT pr06a_api.check_connection(%s,%s,%s)", attempt.id, handshake)

    async def frame(self, attempt, start, end, frame_hash):
        await self._change("SELECT pr06a_api.frame_intent(%s,%s,%s,%s,%s)", attempt.id, start, end, frame_hash)

    async def check_frame(self, attempt, start):
        return await self._change("SELECT pr06a_api.check_frame(%s,%s,%s)", attempt.id, start)

    async def confirm_frame(self, attempt, start):
        await self._change("SELECT pr06a_api.confirm_frame(%s,%s,%s)", attempt.id, start)

    async def discard_frame(self, attempt, start):
        await self._change("SELECT pr06a_api.discard_frame(%s,%s,%s)", attempt.id, start)

    async def retain(self, attempt, packet):
        return await self._change("SELECT pr06a_api.retain_packet(%s,%s,%s)", attempt.id, Jsonb(packet))

    async def anomaly(self, attempt, code, raw_hash):
        await self._change("SELECT pr06a_api.anomaly(%s,%s,%s,%s)", attempt.id, code, raw_hash)

    async def observe(self, attempt, request, usage, final):
        return await self._change("SELECT pr06a_api.observe(%s,%s,%s,%s,%s,%s)",
                                  attempt.id, request, usage.input_tokens if usage else None,
                                  usage.output_tokens if usage else None, final)

    async def sentence(self, attempt, sentence, revision, required_sample):
        await self._change("SELECT pr06a_api.record_sentence(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                           attempt.id, sentence.sentence_id, revision, sentence.begin_ms,
                           sentence.end_ms, sentence.final, sentence.text, required_sample)

    async def complete(self, attempt, state, reason):
        await self._change("SELECT pr06a_api.complete(%s,%s,%s,%s)", attempt.id if attempt else None, state, reason)


    async def interrupt(self, attempt, reason):
        await self._change("SELECT pr06a_api.interrupt(%s,%s,%s)", attempt.id if attempt else None, reason)


def quiet_database_logger():
    # psycopg may log cancellation and server notices, including server text.
    logger = logging.getLogger("psycopg")
    logger.handlers[:] = [logging.NullHandler()]
    logger.propagate = False
    logger.setLevel(logging.CRITICAL + 1)
