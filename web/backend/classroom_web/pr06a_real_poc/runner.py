"""One fenced batch. PG owns the hold, connection slots and frame intents."""
import asyncio
from dataclasses import dataclass

from ..asr_dashscope.transport import AuthenticationRejected, Disconnected
from ..asr_dashscope.wire import finish_task, parse_event, run_task
from ..asr_sim.simulator import ProtocolError
from .ledger import Attempt
from .policy import FRAME_SAMPLES, MAX_BATCH_SECONDS, MAX_CONNECTION_SECONDS, PocStopped, digest


@dataclass(frozen=True)
class Receipt:
    slot: int
    state: str
    stop: str | None


class _Session:
    def __init__(self, transport, ledger, attempt, sample):
        self.transport, self.ledger, self.attempt, self.sample = transport, ledger, attempt, sample
        self.started = asyncio.Event()
        self.run_recorded = asyncio.Event()
        self.finish_recorded = asyncio.Event()
        self.run_control = self.finish_control = 0
        self.ready = self.terminal = self.provider_failed = False
        self.stop = None
        self.ranges = [0]
        self.sentences = {}
        self.request = None
        self.usage = None

    async def _sentence(self, event, inbound):
        sentence = event.sentence
        if sentence is None:
            return
        last = self.sentences.get(sentence.sentence_id)
        if last is not None and last[0] == sentence:
            return
        if ((last is None and sentence.sentence_id != 1 + len(self.sentences))
                or (last is not None and last[0].final)
                or not 0 < inbound.audio_attempt < len(self.ranges)
                or 16 * max(sentence.begin_ms, sentence.end_ms or 0) > self.ranges[inbound.audio_attempt]):
            self.stop = self.stop or "sentence_identity_or_range"
            return
        revision = last[1] + 1 if last else 1
        await self.ledger.sentence(self.attempt, sentence, revision)
        self.sentences[sentence.sentence_id] = (sentence, revision)

    async def _read(self):
        try:
            while True:
                inbound = await self.transport.receive_text()
                event = parse_event(inbound.text, str(self.attempt.task_id))
                if not self.run_control or inbound.control_sequence < self.run_control:
                    raise PocStopped("response_before_run")
                await self.run_recorded.wait()
                if event.kind == "task-failed":
                    self.provider_failed = True
                    await self.ledger.protocol(self.attempt, "task-failed")
                    raise PocStopped("provider_failed")
                if event.kind == "task-started" and not self.ready:
                    await self.ledger.protocol(self.attempt, "task-started")
                    self.ready = True
                    self.started.set()
                    continue
                if not self.ready or event.kind == "task-started":
                    raise PocStopped("event_order")
                final = event.kind == "task-finished"
                if final and (not self.finish_control or inbound.control_sequence < self.finish_control):
                    raise PocStopped("terminal_before_finish")
                if final:
                    await self.finish_recorded.wait()
                    await self.ledger.protocol(self.attempt, "task-finished")
                    self.terminal = True
                if event.request_id and self.request and event.request_id != self.request:
                    self.stop = self.stop or "request_identity_conflict"
                else:
                    self.request = event.request_id or self.request
                    if event.usage_issue or (final and (event.usage is None or self.request is None)):
                        self.stop = self.stop or "usage_unresolved"
                    usage = event.usage
                    if (usage is not None and self.usage is not None
                            and (usage.input_tokens < self.usage.input_tokens or
                                 usage.output_tokens < self.usage.output_tokens)):
                        self.stop = self.stop or "usage_regression"
                        usage = None
                    if usage is not None:
                        self.usage = usage
                    if event.request_id or usage:
                        reached = await self.ledger.observe(self.attempt, event.request_id, usage,
                                                            final and not event.usage_issue and
                                                            usage is not None and self.request is not None)
                        if reached:
                            self.stop = self.stop or "cost_limit"
                if event.sentence_issue:
                    self.stop = self.stop or "sentence_invalid"
                else:
                    await self._sentence(event, inbound)
                if final:
                    if not any(item.final for item, _ in self.sentences.values()):
                        self.stop = self.stop or "missing_final_sentence"
                    return
        finally:
            self.started.set()

    async def run(self, deadline):
        reader = None
        transport = self.transport
        transport.claim()
        watchdog = asyncio.get_running_loop().call_at(deadline, transport.abort)
        try:
            async with asyncio.timeout_at(deadline - 0.25):
                if asyncio.get_running_loop().time() >= deadline - 0.25:
                    raise TimeoutError
                await transport.connect()
                await self.ledger.protocol(self.attempt, "handshake")
                await self.ledger.check_connection(self.attempt)
                reader = asyncio.create_task(self._read())
                await transport.send_json(run_task(str(self.attempt.task_id)),
                                          on_dispatched=lambda n: setattr(self, "run_control", n))
                await self.ledger.protocol(self.attempt, "run-task")
                self.run_recorded.set()
                await self.started.wait()
                await self._send_audio(reader)
                if reader.done():
                    await reader
                    raise PocStopped("premature_reader_exit")
                await transport.send_json(finish_task(str(self.attempt.task_id)),
                                          on_dispatched=lambda n: setattr(self, "finish_control", n))
                await self.ledger.protocol(self.attempt, "finish-task")
                self.finish_recorded.set()
                await reader
        finally:
            if reader is not None:
                reader.cancel()
            try:
                async with asyncio.timeout_at(deadline):
                    if asyncio.get_running_loop().time() < deadline:
                        await transport.close()
            finally:
                transport.abort()
                watchdog.cancel()
                if reader is not None:
                    await asyncio.gather(reader, return_exceptions=True)

    async def _send_audio(self, reader):
        size = self.sample.spec.send_samples
        loop = asyncio.get_running_loop()
        next_frame = loop.time()
        for start in range(0, size, FRAME_SAMPLES):
            # Pace 100 ms PCM frames and let the reader process available events.
            await asyncio.sleep(max(0, next_frame - loop.time()))
            if self.stop or reader.done():
                return
            end = min(start + FRAME_SAMPLES, size)
            frame = self.sample.pcm[2 * start:2 * end]
            try:
                await self.ledger.frame(self.attempt, start, end, digest(frame))
            except PocStopped as exc:
                self.stop = self.stop or str(exc)
                return
            if self.stop or reader.done():
                return
            self.ranges.append(end)
            self.transport.note_audio_attempt(len(self.ranges) - 1)
            next_frame = loop.time() + (end - start) / 16000
            await self.transport.send_binary(frame)
            await self.ledger.confirm_frame(self.attempt, start)


async def execute_batch(ledger, samples, reviewed_sha, account_ref, credential_fingerprint, transport_for):
    loop = asyncio.get_running_loop()
    batch_deadline = loop.time() + MAX_BATCH_SECONDS
    receipts = []
    attempt = session = None
    opened = False
    reason = None
    try:
        async with asyncio.timeout_at(batch_deadline):
            await ledger.open(reviewed_sha, account_ref, credential_fingerprint, samples)
            opened = True
            for sample in samples:
                deadline = min(batch_deadline, loop.time() + MAX_CONNECTION_SECONDS)
                candidate = Attempt.create()
                async with asyncio.timeout_at(deadline):
                    await ledger.claim(sample, candidate)
                    attempt = candidate
                    session = _Session(transport_for(candidate), ledger, candidate, sample)
                    await session.run(deadline)
                state = "succeeded"
                reason = session.stop
                await ledger.complete(attempt, state, reason)
                receipts.append(Receipt(sample.spec.slot, state, reason))
                attempt = session = None
                if reason:
                    break
            if not reason:
                await ledger.complete(None, None, "sequence_complete")
    except (PocStopped, AuthenticationRejected, Disconnected, ProtocolError, TimeoutError) as exc:
        if isinstance(exc, PocStopped):
            reason = str(exc)
        elif isinstance(exc, AuthenticationRejected):
            reason = "authentication_rejected"
        elif isinstance(exc, TimeoutError):
            reason = "timeout"
        elif isinstance(exc, Disconnected):
            reason = "connection_failed"
        else:
            reason = "protocol_invalid"
        if session and (session.terminal or session.provider_failed):
            state = "succeeded" if session.terminal else "failed"
        else:
            state = "failed" if isinstance(exc, AuthenticationRejected) else "unknown"
        if attempt is not None:
            # Audit persistence after the I/O deadline cannot grant more I/O.
            try:
                async with asyncio.timeout(1):
                    await ledger.complete(attempt, state, reason)
            except (PocStopped, TimeoutError):
                reason = "ledger_rejected_or_commit_unknown"
                state = "unknown"
            receipts.append(Receipt(sample.spec.slot, state, reason))
        else:
            if opened:
                try:
                    async with asyncio.timeout(1):
                        await ledger.complete(None, None, reason)
                except (PocStopped, TimeoutError):
                    reason = "ledger_rejected_or_commit_unknown"
            raise PocStopped(reason) from None
    return tuple(receipts)
