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
        self.dispatch_ready = asyncio.Event()
        self.dispatch_ready.set()
        self.run_control = self.finish_control = 0
        self.ready = self.recognition_ok = self.recognition_tainted = self.provider_failed = False
        self.stop = None
        self.ranges = [0]
        self.sentences = {}
        self.seen_sentences = set()
        self.seen_results = set()
        self.request = None
        self.usage = None
        self.received_count = self.received_bytes = 0

    async def _anomaly(self, code, inbound):
        self.stop = self.stop or code
        await self.ledger.anomaly(self.attempt, code, digest(inbound.text.encode("utf-8")))

    async def _sentence(self, sentence, inbound):
        if sentence is None or sentence in self.seen_sentences:
            return
        last = self.sentences.get(sentence.sentence_id)
        frontier = self.ranges[inbound.audio_attempt] if 0 < inbound.audio_attempt < len(self.ranges) else 0
        if ((last is None and sentence.sentence_id != 1 + len(self.sentences))
                or (last is not None and last[0].final)
                or 16 * sentence.begin_ms >= frontier
                or (sentence.end_ms is not None and 16 * sentence.end_ms > frontier)):
            self.recognition_tainted = True
            await self._anomaly("sentence_identity_or_range", inbound)
            return
        revision = last[1] + 1 if last else 1
        await self.ledger.sentence(self.attempt, sentence, revision, frontier)
        self.sentences[sentence.sentence_id] = (sentence, revision)
        self.seen_sentences.add(sentence)

    async def _usage(self, event, inbound, final):
        self.request = event.request_id or self.request
        usage = event.usage
        if event.usage_issue or (final and (usage is None or self.request is None)):
            await self._anomaly("usage_unresolved", inbound)
        if (usage is not None and self.usage is not None
                and (usage.input_tokens < self.usage.input_tokens or usage.output_tokens < self.usage.output_tokens)):
            await self._anomaly("usage_regression", inbound)
            usage = None
        if usage is not None:
            self.usage = usage
        if event.request_id or usage:
            reached = await self.ledger.observe(self.attempt, event.request_id, usage,
                                                final and usage is not None and self.request is not None)
            if reached:
                self.stop = self.stop or "cost_limit"

    async def _receive(self, inbound):
        try:
            event = parse_event(inbound.text, str(self.attempt.task_id))
        except ProtocolError:
            await self._anomaly("protocol_invalid", inbound)
            raise
        if not self.run_control or inbound.control_sequence < self.run_control:
            raise PocStopped("response_before_run")
        await self.run_recorded.wait()
        if event.kind == "task-failed":
            self.stop = "provider_failed"
            self.provider_failed = True
            await self.ledger.protocol(self.attempt, "task-failed")
            raise PocStopped(self.stop)
        if event.kind == "task-started" and not self.ready:
            await self.ledger.protocol(self.attempt, "task-started")
            self.ready = True
            self.started.set()
            return False
        if not self.ready or event.kind == "task-started":
            raise PocStopped("event_order")

        final = event.kind == "task-finished"
        if final:
            if not self.finish_control or inbound.control_sequence < self.finish_control:
                raise PocStopped("terminal_before_finish")
            await self.finish_recorded.wait()
            await self.ledger.protocol(self.attempt, "task-finished")
        elif event in self.seen_results:
            return False
        else:
            self.seen_results.add(event)

        if event.request_id and self.request and event.request_id != self.request:
            self.recognition_tainted = True
            await self._anomaly("request_identity_conflict", inbound)
        else:
            if event.sentence_issue:
                self.recognition_tainted = True
                await self._anomaly("sentence_invalid", inbound)
            await self._sentence(event.sentence, inbound)
            # Recognition and billing have independent validity boundaries.
            if final:
                self.recognition_ok = (not self.recognition_tainted and bool(self.sentences)
                                       and all(item.final for item, _ in self.sentences.values()))
            await self._usage(event, inbound, final)
        if final and not self.recognition_ok:
            self.stop = self.stop or "incomplete_recognition"
        return final

    async def _read(self):
        try:
            while True:
                inbound = await self.transport.receive_text()
                self.dispatch_ready.clear()
                try:
                    received = inbound.text.encode("utf-8")
                    self.received_count += 1
                    self.received_bytes += len(received)
                    if self.received_count > 512 or self.received_bytes > 2 * 1024 * 1024:
                        await self._anomaly("inbound_evidence_limit", inbound)
                        raise PocStopped("inbound_evidence_limit")
                    await self.ledger.inbound(self.attempt, self.received_count, inbound)
                    if await self._receive(inbound):
                        return
                except Exception:
                    self.stop = self.stop or "reader_stopped"
                    raise
                finally:
                    self.dispatch_ready.set()
        finally:
            self.started.set()

    async def run(self, deadline):
        reader = None
        transport = self.transport
        transport.claim()
        loop = asyncio.get_running_loop()
        watchdog = loop.call_at(deadline, transport.abort)

        async def authorize_handshake():
            await self.ledger.check_connection(self.attempt, handshake=True)
            if loop.time() >= deadline - 0.25:
                raise PocStopped("handshake_deadline")

        try:
            async with asyncio.timeout_at(deadline - 0.25):
                if loop.time() >= deadline - 0.25:
                    raise TimeoutError
                await transport.connect(before_handshake=authorize_handshake)
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
                    if loop.time() < deadline:
                        await transport.close()
            finally:
                transport.abort()
                watchdog.cancel()
                if reader is not None:
                    await asyncio.gather(reader, return_exceptions=True)

    async def _wait_dispatch(self):
        while not self.dispatch_ready.is_set():
            await self.dispatch_ready.wait()

    async def _send_audio(self, reader):
        size = self.sample.spec.send_samples
        loop = asyncio.get_running_loop()
        next_frame = loop.time()
        for start in range(0, size, FRAME_SAMPLES):
            await asyncio.sleep(max(0, next_frame - loop.time()))
            await self._wait_dispatch()
            if self.stop or reader.done():
                return
            end = min(start + FRAME_SAMPLES, size)
            frame = self.sample.pcm[2 * start:2 * end]
            try:
                await self.ledger.frame(self.attempt, start, end, digest(frame))
            except PocStopped as exc:
                self.stop = self.stop or str(exc)
                return
            await asyncio.sleep(0)
            await self._wait_dispatch()
            if self.stop or reader.done():
                await self.ledger.discard_frame(self.attempt, start)
                return
            try:
                self.transport.note_audio_attempt(len(self.ranges))
            except Exception:
                await self.ledger.discard_frame(self.attempt, start)
                raise Disconnected("audio_stamp_failed") from None
            self.ranges.append(end)
            next_frame = loop.time() + (end - start) / 16000
            await self.transport.send_binary(frame)
            await self.ledger.confirm_frame(self.attempt, start)


async def execute_batch(ledger, samples, reviewed_sha, account_ref, credential_fingerprint, transport_for):
    if any(len(sample.pcm) != sample.spec.send_samples * 2
           or digest(sample.pcm) != sample.spec.send_hash for sample in samples):
        raise PocStopped("pcm_payload_mismatch")
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
                state = "succeeded" if session.recognition_ok else "unknown"
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
        if session and (session.recognition_ok or session.provider_failed):
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
