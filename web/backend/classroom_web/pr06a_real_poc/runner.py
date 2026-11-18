"""One fenced batch. PG owns the hold, connection slots and frame intents."""
import asyncio
from bisect import bisect_left
from dataclasses import dataclass

from ..asr_dashscope.transport import AuthenticationRejected, Disconnected
from ..asr_dashscope.wire import finish_task, parse_event, run_task
from ..asr_sim.simulator import ProtocolError
from .ledger import Attempt
from .policy import FRAME_SAMPLES, MAX_BATCH_SECONDS, MAX_CONNECTION_SECONDS, PocStopped, UnsafeInbound, digest


@dataclass(frozen=True)
class Receipt:
    slot: int | None
    state: str
    stop: str | None


class _Session:
    def __init__(self, transport, ledger, attempt, sample):
        self.transport, self.ledger, self.attempt, self.sample = transport, ledger, attempt, sample
        self.started = asyncio.Event()
        self.run_recorded = asyncio.Event()
        self.finish_recorded = asyncio.Event()
        self.terminal_signal = asyncio.Event()
        self.terminal_event = None
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
        if last is not None and last[0].final and not sentence.final:
            return
        frontier = self.ranges[inbound.audio_attempt] if 0 < inbound.audio_attempt < len(self.ranges) else 0
        if ((last is None and sentence.sentence_id != 1 + len(self.sentences))
                or (last is not None and last[0].final)
                or 16 * sentence.begin_ms >= frontier
                or (sentence.end_ms is not None and 16 * sentence.end_ms > frontier)):
            self.recognition_tainted = True
            await self._anomaly("sentence_identity_or_range", inbound)
            return
        revision = last[1] + 1 if last else 1
        required = frontier
        if sentence.end_ms is not None:
            end = max(16 * sentence.begin_ms + 1, 16 * sentence.end_ms)
            required = self.ranges[bisect_left(self.ranges, end, lo=1)]
        await self.ledger.sentence(self.attempt, sentence, revision, required)
        self.sentences[sentence.sentence_id] = (sentence, revision)
        self.seen_sentences.add(sentence)

    async def _usage(self, event, inbound, final):
        self.request = event.request_id or self.request
        usage = event.usage
        if event.usage_issue or (final and (usage is None or self.request is None)):
            if event.risk_usage is not None:
                await self.ledger.observe_risk(self.attempt, event.risk_usage)
            await self._anomaly("usage_unresolved", inbound)
        if (usage is not None and self.usage is not None
                and (usage.input_tokens < self.usage.input_tokens or usage.output_tokens < self.usage.output_tokens)):
            await self.ledger.observe_risk(self.attempt, usage)
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
            event = parse_event(inbound.text, str(self.attempt.task_id), capture_risk=True)
        except ProtocolError:
            await self._anomaly("protocol_invalid", inbound)
            raise
        if not self.run_control or inbound.control_sequence < self.run_control:
            raise PocStopped("response_before_run")
        await self.run_recorded.wait()
        if self.terminal_event is not None:
            if event != self.terminal_event:
                risk = event.usage or event.risk_usage
                if risk is not None and (event.request_id is None or event.request_id == self.request):
                    await self.ledger.observe_risk(self.attempt, risk)
                await self._anomaly("post_terminal_conflict", inbound)
            return
        if event.kind == "task-failed":
            self.stop = "provider_failed"
            self.provider_failed = True
            await self.ledger.protocol(self.attempt, "task-failed")
            raise PocStopped(self.stop)
        if event.kind == "task-started":
            if not self.ready:
                await self.ledger.protocol(self.attempt, "task-started")
                self.ready = True
                self.started.set()
            return False
        if not self.ready:
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
            # The PG request binding must succeed before writing recognized text.
            if not final:
                await self._usage(event, inbound, False)
                await self._sentence(event.sentence, inbound)
            # Recognition and billing have independent validity boundaries.
            if final:
                if event.request_id and self.request is None:
                    await self.ledger.observe(self.attempt, event.request_id, None, False)
                self.recognition_ok = (not self.recognition_tainted and bool(self.sentences)
                                       and all(item.final for item, _ in self.sentences.values()))
                await self._usage(event, inbound, True)
        if final:
            if not self.recognition_ok:
                self.stop = self.stop or "incomplete_recognition"
            self.terminal_event = event
            self.terminal_signal.set()

    async def _read(self):
        try:
            while True:
                try:
                    inbound = await self.transport.receive_text()
                except Disconnected:
                    if self.terminal_event is not None:
                        return
                    raise
                except UnsafeInbound as exc:
                    self.dispatch_ready.clear()
                    self.stop = str(exc)
                    try:
                        await self.ledger.anomaly(self.attempt, self.stop, exc.checksum)
                    finally:
                        self.dispatch_ready.set()
                    raise
                self.dispatch_ready.clear()
                try:
                    received = inbound.text.encode("utf-8")
                    self.received_count += 1
                    self.received_bytes += len(received)
                    if self.received_count > 512 or self.received_bytes > 2 * 1024 * 1024:
                        await self._anomaly("inbound_evidence_limit", inbound)
                        raise PocStopped("inbound_evidence_limit")
                    await self.ledger.inbound(self.attempt, self.received_count, inbound)
                    await self._receive(inbound)
                except Exception:
                    self.stop = self.stop or "reader_stopped"
                    raise
                finally:
                    self.dispatch_ready.set()
        finally:
            self.started.set()
            self.terminal_signal.set()

    async def run(self, deadline):
        reader = None
        transport = self.transport
        transport.claim()
        loop = asyncio.get_running_loop()
        watchdog = loop.call_at(deadline, transport.abort)
        write_deadline = deadline - 0.25

        async def authorize_handshake():
            requested = loop.time()
            seconds = await self.ledger.check_connection(self.attempt, handshake=True)
            expires = min(write_deadline, requested + seconds)
            if loop.time() >= expires:
                raise PocStopped("handshake_deadline")
            return expires

        try:
            async with asyncio.timeout_at(write_deadline):
                if loop.time() >= write_deadline:
                    raise TimeoutError
                await transport.connect(before_handshake=authorize_handshake)
                await self.ledger.protocol(self.attempt, "handshake")
                requested = loop.time()
                seconds = await self.ledger.check_connection(self.attempt)
                if loop.time() >= min(write_deadline, requested + seconds):
                    raise PocStopped("run_deadline")
                reader = asyncio.create_task(self._read())
                await transport.send_json(run_task(str(self.attempt.task_id)),
                                          on_dispatched=lambda n: setattr(self, "run_control", n))
                await self.ledger.protocol(self.attempt, "run-task")
                self.run_recorded.set()
                await self.started.wait()
                await self._send_audio(reader, write_deadline)
                await self._wait_dispatch(reader)
                if reader.done() or self.provider_failed:
                    await reader
                    raise PocStopped("premature_reader_exit")
                await transport.send_json(finish_task(str(self.attempt.task_id)),
                                          on_dispatched=lambda n: setattr(self, "finish_control", n))
                await self.ledger.protocol(self.attempt, "finish-task")
                self.finish_recorded.set()
                await self.terminal_signal.wait()
                await self._wait_dispatch(reader)
                if reader.done():
                    await reader
        finally:
            closed = False
            try:
                async with asyncio.timeout_at(deadline):
                    if loop.time() < deadline:
                        await transport.close()
                        closed = True
            finally:
                if not closed:
                    transport.abort()
                try:
                    if reader is not None:
                        _, pending = await asyncio.wait((reader,), timeout=2)
                        if pending:
                            transport.abort()
                            reader.cancel()
                            self.stop = self.stop or "accounting_cleanup_timeout"
                        await asyncio.gather(reader, return_exceptions=True)
                finally:
                    transport.abort()
                    watchdog.cancel()
        if reader is not None and not reader.cancelled():
            await reader

    async def _wait_dispatch(self, reader):
        while not reader.done():
            if not self.dispatch_ready.is_set():
                await self.dispatch_ready.wait()
            elif self.transport.has_pending():
                await asyncio.sleep(0.001)
            else:
                return

    async def _send_audio(self, reader, deadline):
        size = self.sample.spec.send_samples
        loop = asyncio.get_running_loop()
        next_frame = loop.time()
        for start in range(0, size, FRAME_SAMPLES):
            await asyncio.sleep(max(0, next_frame - loop.time()))
            await self._wait_dispatch(reader)
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
            while True:
                await self._wait_dispatch(reader)
                if self.stop or reader.done():
                    break
                try:
                    requested = loop.time()
                    seconds = await self.ledger.check_frame(self.attempt, start)
                except PocStopped as exc:
                    self.stop = self.stop or str(exc)
                    break
                if self.dispatch_ready.is_set() and not self.transport.has_pending():
                    if loop.time() >= min(deadline, requested + seconds):
                        self.stop = "audio_deadline"
                    break
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
    opened = admitted = False
    reason = None
    try:
        async with asyncio.timeout_at(batch_deadline):
            await ledger.open(reviewed_sha, account_ref, credential_fingerprint, samples)
            opened = True
            for sample in samples:
                deadline = min(batch_deadline, loop.time() + MAX_CONNECTION_SECONDS)
                attempt = Attempt.create()
                async with asyncio.timeout_at(deadline):
                    await ledger.claim(sample, attempt)
                    admitted = True
                    session = _Session(transport_for(attempt), ledger, attempt, sample)
                    await session.run(deadline)
                state = "succeeded" if session.recognition_ok else "unknown"
                reason = session.stop
                await ledger.complete(attempt, state, reason)
                receipts.append(Receipt(sample.spec.slot, state, reason))
                attempt = session = None
                admitted = False
                if reason:
                    break
            if not reason:
                await ledger.complete(None, None, "sequence_complete")
    except asyncio.CancelledError:
        try:
            async with asyncio.timeout(1):
                await ledger.interrupt(attempt, "operator_interrupted")
        except (PocStopped, TimeoutError):
            pass
        raise
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
            state = "succeeded" if session.recognition_ok else "failed"
        else:
            state = "failed" if isinstance(exc, AuthenticationRejected) else "unknown"
        if admitted:
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
                        await ledger.interrupt(attempt, reason)
                except (PocStopped, TimeoutError):
                    reason = "ledger_rejected_or_commit_unknown"
            if not receipts:
                raise PocStopped(reason) from None
            receipts.append(Receipt(sample.spec.slot if attempt is not None else None, "not_dialed", reason))
    return tuple(receipts)
