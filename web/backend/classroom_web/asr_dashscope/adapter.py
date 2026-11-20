"""One locally bounded DashScope request, with no production composition."""
import asyncio
from dataclasses import dataclass, fields

from ..asr_sim.simulator import ProtocolError, identity, integer, uid, validate_frame
from .billing import StreamIdentity, SyntheticRequestPermit
from .transport import AuthenticationRejected, InboundText, Transport
from .wire import TokenUsage, UsageError, WireEvent, finish_task, parse_event, run_task


@dataclass(frozen=True)
class Deadlines:
    connect: float = 5
    initialize: float = 5
    write: float = 5
    read: float = 15
    finish: float = 8
    close: float = 2
    session: float = 45

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) not in (int, float) or not 0 < value <= 300:
                raise ProtocolError('invalid deadline')


_ID_FIELDS = frozenset(('task_id', 'attempt_id', 'stream_id', 'provider_task_id'))
_CONFIG_FIELDS = _ID_FIELDS | {'class_session_id', 'capture_epoch_id'}


class DashScopeAdapter:
    """Five-method provider port on one event loop, with single-use ownership.

    start/send/finish are awaitable. events returns a bounded batch of internal
    evidence for the stream, and query never makes a provider call. The owner
    must finish or close, preferably by using the async context manager.
    """

    MAX_FRAME_BYTES = 32_000
    MAX_FRAMES = 512
    MAX_WIRE_EVENTS = 512

    def __init__(self, transport: Transport, *, permit: SyntheticRequestPermit | None = None,
                 deadlines: Deadlines = Deadlines()):
        self._transport = transport
        self._permit = permit
        self._deadlines = deadlines
        self._state = 'idle'
        self._stopping = False
        self._disposed = False
        self._loop = None
        self._session_deadline = self._finish_deadline = None
        self._identity = None
        self._event_batch = []
        self._write_lock = asyncio.Lock()
        self._terminal_event = asyncio.Event()
        self._reader = self._writer = self._finisher = self._closer = None
        self._run_attempted = self._finish_attempted = False
        self._run_handoff_sequence = self._finish_handoff_sequence = None
        self._finish_write_complete = False
        self._pending_terminal: WireEvent | None = None
        self._attempted_ranges = []
        self._sent_ranges = []
        self._request_id = None
        self._wire_count = 0
        self._latest_id = 0
        self._sentences = {}
        self._seen_partials = {}
        self._deferred_transcripts = []
        self._seen_results: set[WireEvent] = set()
        self._usage = None
        self._usage_final = False
        self._usage_revision = 0
        self._provider_terminal = None
        self._close_error = None

    @property
    def state(self):
        return self._state

    @property
    def attempted_ranges(self):
        return tuple(self._attempted_ranges)

    @property
    def sent_ranges(self):
        return tuple(self._sent_ranges)

    @property
    def usage(self) -> TokenUsage | None:
        return self._usage

    @property
    def billing_pending(self):
        return self._state != 'succeeded' or not self._usage_final or self._request_id is None

    @property
    def close_error(self):
        return self._close_error

    def _receipt(self):
        return {'stream_id': self._identity.stream_id, 'state': self._state}

    def _verify_loop(self):
        if self._loop is not None and self._loop is not asyncio.get_running_loop():
            raise ProtocolError('stream belongs to another event loop')

    def _timeout(self, seconds, *, expires_at=None):
        now = self._loop.time()
        deadline = min(now + seconds, self._session_deadline)
        if expires_at is not None:
            deadline = min(deadline, expires_at)
        self._require_time(deadline)
        return asyncio.timeout_at(deadline)

    def _require_time(self, deadline):
        if self._loop.time() >= deadline:
            raise TimeoutError

    async def start(self, config):
        if self._state != 'idle' or self._disposed:
            raise ProtocolError('adapter already used')
        if (type(config) is not dict or not _CONFIG_FIELDS <= config.keys()
                or config.keys() - (_CONFIG_FIELDS | {'mode'})):
            raise ProtocolError('invalid start config')
        if config.get('mode', 'synthetic') != 'synthetic':
            raise ProtocolError('real ASR is disabled pending verified billing bounds and approval')
        if getattr(self._transport, 'offline', False) is not True:
            raise ProtocolError('offline transport required')
        session = uid(config['class_session_id'])
        epoch = uid(config['capture_epoch_id'])
        stream = StreamIdentity(**{field: config[field] for field in _ID_FIELDS})
        if stream.provider_task_id in (session, epoch):
            raise ProtocolError('provider task identity must be allocated separately')
        if type(self._permit) is not SyntheticRequestPermit:
            raise ProtocolError('a synthetic reservation and permit are required')
        self._permit.consume(stream)
        try:
            self._transport.claim()
        except Exception:
            raise ProtocolError('transport ownership unavailable') from None

        self._identity, self._session, self._epoch = stream, session, epoch
        self._loop = asyncio.get_running_loop()
        self._session_deadline = self._loop.time() + self._deadlines.session
        self._state = 'starting'
        phase = 'connect'
        try:
            await self._write(self._transport.connect, seconds=self._deadlines.connect)
            phase = 'initialize'
            if self._state != 'starting' or self._stopping:
                raise ProtocolError('start closed')
            async with self._timeout(self._deadlines.initialize) as limit:
                await self._write(self._send_run, expires_at=limit.when())
                if self._state != 'starting' or self._stopping:
                    raise ProtocolError('start closed')
                self._reader = asyncio.create_task(self._read(expires_at=limit.when()))
                try:
                    self._accept(await self._reader)
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    raise ProtocolError('start closed') from None
            if self._state != 'streaming' or self._stopping:
                raise ProtocolError('provider did not start')
            self._reader = asyncio.create_task(self._read_events())
            return self._receipt()
        except asyncio.CancelledError:
            await self.aclose()
            raise
        except Exception as exc:
            if self._provider_terminal == 'task-failed':
                reason = 'task_failed'
            elif isinstance(exc, AuthenticationRejected) and phase == 'connect':
                reason = 'authentication_rejected'
            elif isinstance(exc, TimeoutError):
                reason = phase + '_timeout'
            elif isinstance(exc, UsageError):
                reason = 'usage_conflict'
            elif isinstance(exc, ProtocolError):
                reason = 'provider_protocol_error'
            else:
                reason = phase + '_failed'
            self._stop_unconfirmed(reason)
            await self._wait_closed()
            raise ProtocolError(reason) from None

    async def send(self, frame):
        self._verify_loop()
        if self._state != 'streaming' or self._stopping:
            raise ProtocolError('not accepting frames')
        async with self._write_lock:
            if self._state != 'streaming' or self._stopping:
                raise ProtocolError('not accepting frames')
            pcm, start, end = validate_frame(frame, max_bytes=self.MAX_FRAME_BYTES)
            if self._attempted_ranges and start != self._attempted_ranges[-1][1]:
                raise ProtocolError('frames must be contiguous and cannot be resent')
            if len(self._attempted_ranges) >= self.MAX_FRAMES:
                raise ProtocolError('frame limit reached')
            base = self._attempted_ranges[0][0] if self._attempted_ranges else start
            self._permit.check_audio(end - base)
            try:
                await self._write(self._send_frame, pcm, start, end)
                # A reader can have closed admission while this write resolves.
                # Its terminal receipt and final evidence become visible together.
                await self._wait_closed()
            except asyncio.CancelledError:
                self._stop_unconfirmed('closed')
                await self._wait_closed()
                raise
            except Exception as exc:
                reason = 'send_timeout' if isinstance(exc, TimeoutError) else 'send_failed'
                self._stop_unconfirmed(reason)
                await self._wait_closed()
                raise ProtocolError(reason) from None
            return self._receipt()

    async def finish(self):
        self._verify_loop()
        if not self._stopping and self._state == 'streaming':
            self._finish_deadline = min(self._loop.time() + self._deadlines.finish, self._session_deadline)
            self._state = 'draining'
            self._finisher = asyncio.create_task(self._finish())
        elif not self._stopping and self._state != 'draining':
            raise ProtocolError('cannot finish this state')
        try:
            if self._finisher is not None:
                await asyncio.shield(self._finisher)
            await self._wait_closed()
        except asyncio.CancelledError:
            await self.aclose()
            raise
        return self._receipt()

    async def _finish(self):
        try:
            async with self._timeout(self._deadlines.finish, expires_at=self._finish_deadline) as limit:
                async with self._write_lock:
                    if self._state == 'draining' and not self._stopping:
                        await self._write(self._send_finish, expires_at=limit.when())
                        self._finish_write_complete = True
                        self._confirm_pending_terminal()
                await self._terminal_event.wait()
        except Exception as exc:
            reason = 'finish_timeout' if isinstance(exc, TimeoutError) else 'finish_failed'
            self._stop_unconfirmed(reason)

    def events(self):
        batch, self._event_batch = self._event_batch, []
        return batch

    def query(self, receipt):
        if (self._identity is None or type(receipt) is not dict
                or receipt.get('stream_id') != self._identity.stream_id):
            raise ProtocolError('receipt does not identify current stream')
        return {'status': 'unsupported'}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        await self.aclose()

    async def aclose(self):
        self._verify_loop()
        self._disposed = True
        self._stop_unconfirmed('closed')
        await self._wait_closed()
        if self._finisher is not None:
            await asyncio.shield(self._finisher)

    async def _send_run(self):
        message = run_task(self._identity.provider_task_id)
        self._run_attempted = True
        await self._transport.send_json(message, on_dispatched=self._mark_run_handoff)
        if self._run_handoff_sequence is None:
            raise ProtocolError('transport omitted run handoff')

    def _mark_run_handoff(self, control_sequence):
        if self._state == 'starting' and not self._stopping:
            if self._run_handoff_sequence is not None:
                raise ProtocolError('duplicate run handoff')
            self._run_handoff_sequence = integer(control_sequence, 1)

    async def _send_frame(self, pcm, start, end):
        # A transport exception cannot prove that the bytes did not leave.
        self._transport.note_audio_attempt(len(self._attempted_ranges) + 1)
        self._attempted_ranges.append((start, end))
        await self._transport.send_binary(pcm)
        self._sent_ranges.append((start, end))
        if not self._stopping:
            self._publish_confirmed_transcripts(end)

    async def _send_finish(self):
        message = finish_task(self._identity.provider_task_id)
        self._finish_attempted = True
        await self._transport.send_json(message, on_dispatched=self._mark_finish_handoff)
        if self._finish_handoff_sequence is None:
            raise ProtocolError('transport omitted finish handoff')

    def _mark_finish_handoff(self, control_sequence):
        if self._state == 'draining' and not self._stopping:
            if self._finish_handoff_sequence is not None:
                raise ProtocolError('duplicate finish handoff')
            self._finish_handoff_sequence = integer(control_sequence, self._run_handoff_sequence + 1)

    async def _write(self, method, *args, seconds=None, expires_at=None):
        limit = self._timeout(self._deadlines.write if seconds is None else seconds,
                              expires_at=expires_at)

        async def invoke():
            self._require_time(limit.when())
            if self._stopping:
                raise ProtocolError('writer stopped with the connection')
            await method(*args)

        try:
            async with limit:
                self._writer = asyncio.create_task(invoke())
                await self._writer
                self._require_time(limit.when())
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            raise ProtocolError('writer stopped with the connection') from None
        finally:
            self._writer = None

    async def _read(self, *, expires_at=None):
        async with self._timeout(self._deadlines.read, expires_at=expires_at) as limit:
            raw = await self._transport.receive_text()
            self._require_time(limit.when())
        self._wire_count += 1
        if self._wire_count > self.MAX_WIRE_EVENTS:
            raise ProtocolError('wire event limit reached')
        if type(raw) is not InboundText:
            raise ProtocolError('inbound transport sequencing is required')
        control_sequence = integer(raw.control_sequence)
        audio_attempt = integer(raw.audio_attempt, maximum=len(self._attempted_ranges))
        return parse_event(raw.text, self._identity.provider_task_id), control_sequence, audio_attempt

    async def _read_events(self):
        try:
            while not self._stopping and self._pending_terminal is None:
                self._accept(await self._read())
        except asyncio.CancelledError:
            self._stop_unconfirmed('receive_failed')
            raise
        except UsageError:
            self._stop_unconfirmed('usage_conflict')
        except ProtocolError:
            self._stop_unconfirmed('provider_protocol_error')
        except TimeoutError:
            self._stop_unconfirmed('receive_timeout')
        except Exception:
            self._stop_unconfirmed('receive_failed')

    def _accept(self, event):
        if self._stopping:
            return
        event, ingress_sequence, audio_attempt = event
        if self._state == 'starting':
            if ingress_sequence != self._run_handoff_sequence:
                raise ProtocolError('startup response preceded run-task handoff')
            if event.kind not in ('task-started', 'task-failed'):
                raise ProtocolError('provider event before task-started')
        if event.kind == 'result-generated' and event in self._seen_results:
            return
        if event.request_id is not None:
            if self._request_id is not None and event.request_id != self._request_id:
                raise ProtocolError('provider request identity changed')
            self._request_id = event.request_id
        if event.kind == 'task-started':
            if self._state == 'starting':
                self._state = 'streaming'
                self._emit('asr.started')
            return
        if event.kind == 'task-failed':
            self._provider_terminal = event.kind
            self._stop('failed', 'task_failed')
            return
        if event.kind == 'task-finished':
            if self._state != 'draining' or not self._finish_attempted:
                raise ProtocolError('provider finished before finish-task')
            self._provider_terminal = event.kind
            self._pending_terminal = event
            if self._finish_handoff_sequence is None or ingress_sequence != self._finish_handoff_sequence:
                self._stop_unconfirmed('finish_ack_before_dispatch')
                return
            self._confirm_pending_terminal()
            return

        self._accept_result(event, audio_attempt)
        self._seen_results.add(event)

    def _accept_result(self, event, audio_attempt):
        # A valid sentence and a valid meter reading are independent evidence.
        sentence_issue = event.sentence_issue
        if event.sentence is not None:
            try:
                self._accept_sentence(event.sentence, audio_attempt)
            except ProtocolError:
                sentence_issue = True
        if event.usage_issue:
            self._usage_anomaly('invalid_result_usage', final=False)
            raise UsageError('invalid cumulative result usage')
        if event.usage is not None:
            self._accept_usage(event.usage, final=False)
        if sentence_issue:
            raise ProtocolError('invalid result sentence')

    def _confirm_pending_terminal(self):
        if self._stopping or self._state != 'draining' or self._pending_terminal is None:
            return
        if self._loop.time() >= self._finish_deadline:
            self._stop_unconfirmed('finish_timeout')
            return
        if not self._finish_write_complete:
            return
        event, self._pending_terminal = self._pending_terminal, None
        if event.usage_issue:
            self._usage_anomaly('invalid_terminal_usage', final=True)
        elif event.usage is not None:
            try:
                self._accept_usage(event.usage, final=True)
            except UsageError:
                # The terminal separately proves that delivery of recognition ended.
                pass
        if (self._deferred_transcripts or not self._sentences
                or any(not sentence.final for sentence, _ in self._sentences.values())):
            self._stop('unknown', 'incomplete_transcript')
        else:
            self._stop('succeeded', 'task_finished')

    def _accept_usage(self, usage, *, final):
        if (usage.input_tokens > self._permit.input_tokens_limit
                or usage.output_tokens > self._permit.output_tokens_limit):
            self._usage_anomaly('overrun', usage, final=final)
            raise UsageError('cumulative usage exceeds permitted bounds')
        if self._usage is not None and (usage.input_tokens < self._usage.input_tokens
                                        or usage.output_tokens < self._usage.output_tokens):
            self._usage_anomaly('regression', usage, final=final)
            raise UsageError('cumulative usage regressed')
        if usage != self._usage or final and not self._usage_final:
            self._usage = usage
            self._usage_final = final
            self._usage_revision += 1
            self._emit('asr.usage', input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                       revision=self._usage_revision, final=final)

    def _usage_anomaly(self, reason, observed=None, *, final):
        self._emit('asr.usage_anomaly', reason=reason, final=final,
                   observed_input_tokens=observed.input_tokens if observed is not None else None,
                   observed_output_tokens=observed.output_tokens if observed is not None else None,
                   input_tokens_limit=self._permit.input_tokens_limit,
                   output_tokens_limit=self._permit.output_tokens_limit)

    def _accept_sentence(self, sentence, audio_attempt):
        number = sentence.sentence_id
        prior, revision = self._sentences.get(number, (None, 0))
        if prior == sentence:
            return
        if prior is not None and prior.final:
            if sentence.final:
                raise ProtocolError('conflicting final revision')
            return
        if prior is None and number != self._latest_id + 1:
            raise ProtocolError('sentence identity is out of order or has a gap')
        seen = self._seen_partials.setdefault(number, set())
        if not sentence.final and sentence in seen:
            return
        if not audio_attempt:
            raise ProtocolError('sentence before audio')
        base = self._attempted_ranges[0][0]
        attempted_end = self._attempted_ranges[audio_attempt - 1][1]
        start = base + sentence.begin_ms * 16
        end = base + sentence.end_ms * 16 if sentence.end_ms is not None else None
        if start >= attempted_end or (end is not None and end > attempted_end):
            raise ProtocolError('sentence outside attempted audio')
        if not sentence.final:
            seen.add(sentence)
        self._latest_id = max(self._latest_id, number)
        self._sentences[number] = (sentence, revision + 1)
        candidate = self._event('asr.final_candidate' if sentence.final else 'asr.partial',
                                segment_id=identity('dashscope-sentence', self._identity.stream_id,
                                                     self._identity.provider_task_id, number),
                                sequence=number - 1, revision=revision + 1, text=sentence.text,
                                start_sample=start, end_sample=end, timestamp_source='provider_segment')
        # A growing partial can reflect the newest attempted frame even
        # when the provider gives no end time for it.
        required_sample = max(start + 1, end) if end is not None else attempted_end
        self._deferred_transcripts.append((required_sample, candidate))
        if self._sent_ranges:
            self._publish_confirmed_transcripts(self._sent_ranges[-1][1])

    def _publish_confirmed_transcripts(self, end_sample, *, finalizing=False):
        remaining = []
        blocked_segments = set()
        for required_sample, event in self._deferred_transcripts:
            segment = event['data']['segment_id']
            if required_sample <= end_sample and segment not in blocked_segments:
                self._event_batch.append(event)
            elif finalizing:
                self._event_batch.append({'type': 'asr.transcript_unconfirmed', 'data': {
                    **event['data'], 'candidate_type': event['type'],
                }})
            else:
                remaining.append((required_sample, event))
                blocked_segments.add(segment)
        self._deferred_transcripts = remaining

    def _event(self, kind, **data):
        return {'type': kind, 'data': {
            **data, 'task_id': self._identity.task_id, 'attempt_id': self._identity.attempt_id,
            'stream_id': self._identity.stream_id, 'class_session_id': self._session,
            'capture_epoch_id': self._epoch, 'provider_task_id': self._identity.provider_task_id,
            'provider_request_id': self._request_id,
        }}

    def _emit(self, kind, **data):
        self._event_batch.append(self._event(kind, **data))

    def _stop_unconfirmed(self, reason):
        if self._state != 'idle' and not self._stopping:
            self._stop('unknown' if self._run_attempted else 'failed', reason)

    def _stop(self, state, reason):
        if self._stopping:
            return
        if self._pending_terminal is not None:
            self._usage_anomaly('unconfirmed_finish_dispatch', self._pending_terminal.usage, final=True)
            self._pending_terminal = None
        self._stopping = True
        self._terminal_event.set()
        self._closer = asyncio.create_task(self._close_transport(state, reason))

    async def _wait_closed(self):
        if self._closer is not None:
            await asyncio.shield(self._closer)

    async def _close_transport(self, state, reason):
        pending = [task for task in (self._reader, self._writer) if task is not None and not task.done()]
        for task in pending:
            task.cancel()
        try:
            async with asyncio.timeout(self._deadlines.close):
                await asyncio.gather(*pending, return_exceptions=True)
                await self._transport.close()
        except asyncio.CancelledError:
            self._close_error = 'close_failed'
            if asyncio.current_task().cancelling():
                raise
        except Exception as exc:
            self._close_error = 'close_timeout' if isinstance(exc, TimeoutError) else 'close_failed'
        finally:
            if self._close_error is not None:
                try:
                    self._transport.abort()
                except Exception:
                    self._close_error = 'abort_failed'
            # A runnable write may still have completed between _stop and
            # cancellation. Release evidence using the settled local frontier.
            confirmed_end = self._sent_ranges[-1][1] if self._sent_ranges else 0
            self._publish_confirmed_transcripts(confirmed_end, finalizing=True)
            self._state = state
            self._emit('asr.terminal', state=state, reason=reason,
                       provider_terminal=self._provider_terminal, billing_pending=self.billing_pending)
