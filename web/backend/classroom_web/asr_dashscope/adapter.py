"""One locally bounded DashScope request, with no production composition."""
import asyncio
from dataclasses import dataclass, fields

from ..asr_sim.simulator import ProtocolError, identity, uid, validate_frame
from .billing import StreamIdentity, SyntheticRequestPermit
from .transport import AuthenticationRejected, Transport
from .wire import TokenUsage, UsageError, finish_task, parse_event, run_task


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


_TERMINAL = frozenset(('succeeded', 'failed', 'unknown'))
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
        self._disposed = False
        self._loop = None
        self._session_deadline = None
        self._identity = None
        self._event_batch = []
        self._write_lock = asyncio.Lock()
        self._terminal_event = asyncio.Event()
        self._reader = self._writer = self._finisher = self._closer = None
        self._run_attempted = self._finish_attempted = False
        self._attempted_ranges = []
        self._sent_ranges = []
        self._request_id = None
        self._wire_count = 0
        self._latest_id = 0
        self._sentences = {}
        self._seen_partials = {}
        self._usage = None
        self._usage_final = False
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
        return self._state != 'succeeded' or not self._usage_final

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
            if self._state != 'starting':
                raise ProtocolError('start closed')
            async with self._timeout(self._deadlines.initialize) as limit:
                await self._write(self._send_run, expires_at=limit.when())
                self._reader = asyncio.create_task(self._read(expires_at=limit.when()))
                try:
                    self._accept(await self._reader)
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    raise ProtocolError('start closed') from None
            if self._state != 'streaming':
                raise ProtocolError('provider did not start')
            self._reader = asyncio.create_task(self._read_events())
            return self._receipt()
        except asyncio.CancelledError:
            await self.aclose()
            raise
        except Exception as exc:
            if isinstance(exc, AuthenticationRejected) and phase == 'connect':
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
        if self._state != 'streaming':
            raise ProtocolError('not accepting frames')
        async with self._write_lock:
            if self._state != 'streaming':
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
            except asyncio.CancelledError:
                self._stop_unconfirmed('closed')
                await self._wait_closed()
                raise
            except Exception as exc:
                reason = 'send_timeout' if isinstance(exc, TimeoutError) else 'send_failed'
                self._stop_unconfirmed(reason)
                await self._wait_closed()
                raise ProtocolError(reason) from None
            self._sent_ranges.append((start, end))
            return self._receipt()

    async def finish(self):
        self._verify_loop()
        if self._state == 'streaming':
            self._state = 'draining'
            self._finisher = asyncio.create_task(self._finish())
        elif self._state != 'draining' and self._state not in _TERMINAL:
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
            async with self._timeout(self._deadlines.finish) as limit:
                async with self._write_lock:
                    if self._state == 'draining':
                        await self._write(self._send_finish, expires_at=limit.when())
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
        await self._transport.send_json(message)

    async def _send_frame(self, pcm, start, end):
        # A transport exception cannot prove that the bytes did not leave.
        self._attempted_ranges.append((start, end))
        await self._transport.send_binary(pcm)

    async def _send_finish(self):
        message = finish_task(self._identity.provider_task_id)
        self._finish_attempted = True
        await self._transport.send_json(message)

    async def _write(self, method, *args, seconds=None, expires_at=None):
        limit = self._timeout(self._deadlines.write if seconds is None else seconds,
                              expires_at=expires_at)

        async def invoke():
            self._require_time(limit.when())
            if self._state in _TERMINAL:
                raise ProtocolError('writer stopped with the connection')
            await method(*args)

        try:
            async with limit:
                self._writer = asyncio.create_task(invoke())
                await self._writer
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
        return parse_event(raw, self._identity.provider_task_id)

    async def _read_events(self):
        try:
            while self._state not in _TERMINAL:
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
        if self._state in _TERMINAL:
            return
        if self._state == 'starting' and event.kind not in ('task-started', 'task-failed'):
            raise ProtocolError('provider event before task-started')
        if event.kind == 'task-finished' and (self._state != 'draining' or not self._finish_attempted):
            raise ProtocolError('provider finished before finish-task')
        if event.request_id is not None:
            if self._request_id is not None and event.request_id != self._request_id:
                raise ProtocolError('provider request identity changed')
            self._request_id = event.request_id
        if event.usage is not None:
            self._accept_usage(event.usage, final=event.kind == 'task-finished')
        if event.kind == 'task-started' and self._state == 'starting':
            self._state = 'streaming'
            self._emit('asr.started')
        elif event.kind == 'task-failed':
            self._provider_terminal = event.kind
            self._stop('failed', 'task_failed')
        elif event.kind == 'task-finished':
            self._provider_terminal = event.kind
            if not self._sentences or any(not sentence.final for sentence, _ in self._sentences.values()):
                self._stop('unknown', 'incomplete_transcript')
            else:
                self._stop('succeeded', 'task_finished')
        elif event.sentence is not None:
            self._accept_sentence(event.sentence)

    def _accept_usage(self, usage, *, final):
        if (usage.input_tokens > self._permit.input_tokens_limit
                or usage.output_tokens > self._permit.output_tokens_limit
                or self._usage is not None and (
                    usage.input_tokens < self._usage.input_tokens
                    or usage.output_tokens < self._usage.output_tokens)):
            raise UsageError('cumulative usage outside permitted bounds')
        if usage != self._usage or final and not self._usage_final:
            self._usage = usage
            self._usage_final = final
            self._emit('asr.usage', input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                       final=final)

    def _accept_sentence(self, sentence):
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
        if not self._attempted_ranges:
            raise ProtocolError('sentence before audio')
        base = self._attempted_ranges[0][0]
        start = base + sentence.begin_ms * 16
        end = base + sentence.end_ms * 16 if sentence.end_ms is not None else None
        if start >= self._attempted_ranges[-1][1] or (end is not None and end > self._attempted_ranges[-1][1]):
            raise ProtocolError('sentence outside attempted audio')
        if not sentence.final:
            seen.add(sentence)
        self._latest_id = max(self._latest_id, number)
        self._sentences[number] = (sentence, revision + 1)
        self._emit('asr.final_candidate' if sentence.final else 'asr.partial',
                   segment_id=identity('dashscope-sentence', self._identity.stream_id,
                                        self._identity.provider_task_id, number),
                   sequence=number - 1, revision=revision + 1, text=sentence.text,
                   start_sample=start, end_sample=end, timestamp_source='provider_segment')

    def _emit(self, kind, **data):
        self._event_batch.append({'type': kind, 'data': {
            **data, 'task_id': self._identity.task_id, 'attempt_id': self._identity.attempt_id,
            'stream_id': self._identity.stream_id, 'class_session_id': self._session,
            'capture_epoch_id': self._epoch, 'provider_task_id': self._identity.provider_task_id,
            'provider_request_id': self._request_id,
        }})

    def _stop_unconfirmed(self, reason):
        if self._state != 'idle' and self._state not in _TERMINAL:
            self._stop('unknown' if self._run_attempted else 'failed', reason)

    def _stop(self, state, reason):
        if self._state in _TERMINAL:
            return
        self._state = state
        self._emit('asr.terminal', state=state, reason=reason,
                   provider_terminal=self._provider_terminal, billing_pending=self.billing_pending)
        self._terminal_event.set()
        self._closer = asyncio.create_task(self._close_transport())

    async def _wait_closed(self):
        if self._closer is not None:
            await asyncio.shield(self._closer)

    async def _close_transport(self):
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
