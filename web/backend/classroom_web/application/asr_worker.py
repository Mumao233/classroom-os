"""Bounded synthetic ASR worker. PG authorizes before any provider action."""
from datetime import datetime, timezone
from time import monotonic
from uuid import UUID, uuid4
import asyncio
import json

from ..asr_dashscope.adapter import DashScopeAdapter
from ..asr_dashscope.billing import StreamIdentity, SyntheticRequestPermit
from ..asr_dashscope.transport import FakeTransport
from ..asr_sim.simulator import ProtocolError
from ..domain.asr import source_frames
from ..domain.auth import AuthRejected
from ..domain.billing import BillingBlocked, Usage
from ..domain.data import Gone, InactiveOwner, NotFound, OwnerContext
from ..domain.media import ObjectUnavailable
from ..domain.tasks import StaleFence
from ..infrastructure.asr_repository import PgAsrRepository
from .media import stored

ASR_SCHEMA = 'pr06b-1'


class AsrWorker:
    """All adapter connections and event buffers belong to one claimed task."""

    def __init__(self, service, queue, lifecycle, *, worker_id, make_transport, on_partial=None):
        self.service, self.database, self.queue = service, service.database, queue
        self.lifecycle, self.worker_id = lifecycle, worker_id
        self.make_transport = make_transport
        self.on_partial = on_partial if on_partial is not None else service.events.partial

    def recover_expired(self):
        for claim in self.queue.expired():
            if claim.kind != 'asr_live':
                continue
            try:
                with self.database.transaction(OwnerContext(claim.owner_id)) as tx:
                    task = tx.tasks.get(claim.id)
                    if task.schema_version != ASR_SCHEMA:
                        continue
                    tx.tasks.recover(claim.id, claim.lease_token)
            except (Gone, InactiveOwner, NotFound):
                if not self.queue.recover_revoked(claim.id, claim.lease_token):
                    continue
            except StaleFence:
                continue
            self.lifecycle.synchronize(claim.owner_id, claim.id)

    async def run_once(self):
        await asyncio.to_thread(self.recover_expired)
        claims = await asyncio.to_thread(self.queue.claim, self.worker_id, lease_seconds=60, schema_version=ASR_SCHEMA)
        if not claims:
            return False
        await self.run_claim(claims[0])
        return True

    async def serve(self, stop, errors):
        while not stop.is_set():
            try:
                worked = await self.run_once()
            except Exception as error:
                errors.append({'kind': type(error).__name__, 'stage': 'asr_worker'})
                worked = False
            if not worked:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.25)
                except TimeoutError:
                    pass

    def _repo(self, tx, claim):
        return PgAsrRepository(tx, claim.owner_id)

    def _candidate(self, claim):
        self.service.require_enabled()
        with self.database.transaction(OwnerContext(claim.owner_id)) as tx:
            repo = self._repo(tx, claim)
            stream = repo.window(task=claim.id, lock=False)
            epoch = repo.request_scope(stream)
            stream = repo.window(task=claim.id)
            cutoff = repo.source_cutoff(stream, epoch)
            candidate = repo.next_chunk(stream, epoch, cutoff)
            done = bool(stream['source_scope']) or cutoff is not None
            return stream, epoch, candidate, done

    def _start(self, claim, candidate):
        """Hang the first committed handshake authorization off the original task."""
        with self.database.transaction(OwnerContext(claim.owner_id)) as tx:
            repo = self._repo(tx, claim)
            stream = repo.window(task=claim.id, lock=False)
            epoch = repo.request_scope(stream)
            stream = repo.window(task=claim.id)
            cutoff = repo.source_cutoff(stream, epoch)
            current = repo.next_chunk(stream, epoch, cutoff)
            if current is None or current[0] != candidate:
                raise AuthRejected('SOURCE_CHANGED', 409)
            attempt = tx.tasks.prepare_attempt(claim.id, claim.lease_token, self.worker_id)
            fees = tx.billing
            reservation = fees.for_task(claim.id)
            reservation = fees.authorize_send(reservation['id'], attempt.id, claim.lease_token, self.worker_id)
            units = reservation['maximum_units']
            handshake = fees.consume_permit(reservation['id'], stream['id'],
                {'input_tokens': units['input_tokens'], 'output_tokens': units['output_tokens']},
                claim.lease_token, self.worker_id)
            stream = repo.set_state(stream, 'streaming' if stream['mode'] == 'live' else 'catching_up',
                                    attempt=attempt.id)
            identity = StreamIdentity(str(claim.id), str(attempt.id), str(stream['id']), str(stream['provider_task_id']))
            permit = SyntheticRequestPermit(reservation_id=str(reservation['id']),
                permit_id=str(handshake['id']), identity=identity, maximum_units=units)
            return stream, identity, permit, reservation['expires_at']

    def _mint_frame(self, claim, stream, frame, prior_samples):
        self.service.require_enabled()
        with self.database.transaction(OwnerContext(claim.owner_id)) as tx:
            repo = self._repo(tx, claim)
            epoch = repo.request_scope(stream)
            current = repo.window(task=claim.id)
            if current['state'] not in ('streaming', 'catching_up') or current['attempt_id'] != stream['attempt_id']:
                raise StaleFence('STALE_FENCE')
            cutoff = repo.source_cutoff(current, epoch)
            chunk = repo.chunk(UUID(frame.source.chunk_id))
            source, asset = repo.asr_source(chunk, epoch)
            if source != frame.source or (cutoff is not None and asset['confirmed_at'] > cutoff):
                raise AuthRejected('SOURCE_CHANGED', 409)
            if current['mode'] == 'live' and source.ingest_mode != 'live':
                raise AuthRejected('FORBIDDEN', 403)
            next_source = repo.next_chunk(current, epoch, cutoff)
            if next_source is None or next_source[0] != source or not next_source[2].contains(frame.samples):
                raise AuthRejected('SOURCE_CHANGED', 409)
            if next_source[2].start != frame.samples.start:
                raise AuthRejected('REVISION_CONFLICT', 409)
            reservation = tx.billing.for_task(claim.id)
            identity = uuid4()
            sent_ms = (prior_samples + frame.samples.count + 15) // 16 - (prior_samples + 15) // 16
            # Full provider frames have integral duration. An isolated sub-ms tail
            # can land inside a millisecond already reserved by the prior slice.
            if sent_ms == 0:
                sent_ms = 1
            tx.billing.consume_permit(reservation['id'], identity, {'audio_duration_ms': sent_ms},
                claim.lease_token, self.worker_id)
            repo.add_dispatch(current, frame, identity)
            return identity, reservation['expires_at']

    def _handoff(self, claim, stream, submission):
        with self.database.transaction(OwnerContext(claim.owner_id)) as tx:
            tx.tasks._fenced(claim.id, claim.lease_token, self.worker_id)
            repo = self._repo(tx, claim)
            repo.confirm_handoff(stream, submission)

    def _events(self, claim, stream, epoch, adapter, sent_samples):
        for event in adapter.events():
            data, kind = event['data'], event['type']
            if (data['task_id'], data['attempt_id'], data['stream_id']) != (
                    str(claim.id), str(stream['attempt_id']), str(stream['id'])):
                raise ProtocolError('event does not belong to the original attempt')
            request = data['provider_request_id']
            if kind == 'asr.usage' and request:
                usage = Usage(provider_request_id=request, meter='asr', revision=data['revision'],
                    quantities={'audio_duration_ms': (sent_samples + 15) // 16,
                                'input_tokens': data['input_tokens'], 'output_tokens': data['output_tokens']},
                    final=data['final'])
                self.lifecycle.late_usage(claim.owner_id, claim.id, usage)
            if kind not in ('asr.final_candidate', 'asr.partial') and request is None:
                continue
            try:
                with self.database.transaction(OwnerContext(claim.owner_id)) as tx:
                    repo = self._repo(tx, claim)
                    tx.tasks._fenced(claim.id, claim.lease_token, self.worker_id)
                    stored_stream = repo.window(task=claim.id)
                    if request is not None:
                        if stored_stream['provider_request_id'] not in (None, request):
                            raise ProtocolError('provider request identity changed')
                        repo.set_state(stored_stream, stored_stream['state'], request=request)
                    if kind in ('asr.final_candidate', 'asr.partial'):
                        repo.check_session(stored_stream)
                    if kind == 'asr.final_candidate':
                        repo.record_final(stored_stream, data, epoch['timeline_offset_ms'])
                if kind == 'asr.partial' and self.on_partial is not None:
                    self.on_partial(claim.owner_id, data, epoch['timeline_offset_ms'])
            except (InactiveOwner, AuthRejected) as error:
                if isinstance(error, AuthRejected) and error.code == 'REVISION_CONFLICT':
                    raise

    def _complete(self, claim, adapter):
        with self.database.transaction(OwnerContext(claim.owner_id)) as tx:
            repo = self._repo(tx, claim)
            stream = repo.window(task=claim.id)
            attempt = stream['attempt_id']
            if attempt is None:
                reservation = tx.billing.for_task(claim.id)
                tx.billing.release_unsent(reservation['id'])
                repo.set_state(stream, 'failed')
                return
            if adapter is not None and adapter.state == 'failed':
                tx.tasks.fail_asr_attempt(claim.id, attempt, claim.lease_token, self.worker_id)
                repo.set_state(stream, 'failed')
            elif adapter is None or adapter.state != 'succeeded':
                if adapter is not None:
                    adapter.query({'stream_id': str(stream['id'])})
                tx.tasks.mark_unknown(claim.id, attempt, claim.lease_token, self.worker_id)
                repo.set_state(stream, 'unknown')
            else:
                tx.tasks.store_result(claim.id, attempt, json.dumps({'stream_id': str(stream['id'])}))
                tx.tasks.publish_result(claim.id, attempt, claim.lease_token, self.worker_id)
                repo.set_state(stream, 'succeeded')
        if adapter is None or adapter.billing_pending:
            self.lifecycle.completed(claim.owner_id, claim.id, None)

    async def run_claim(self, claim):
        # Use the adapter's shorter bounded lifetime, leaving a separate drain budget.
        deadline = monotonic() + 25
        adapter = None
        sent_samples = 0
        try:
            while monotonic() < deadline:
                if adapter is not None and adapter.state != 'streaming':
                    break
                stream, epoch, candidate, done = await asyncio.to_thread(self._candidate, claim)
                if candidate is None:
                    if done:
                        break
                    if adapter is not None:
                        self._events(claim, stream, epoch, adapter, sent_samples)
                    await asyncio.sleep(0.05)
                    continue
                source, asset, selected = candidate
                pcm = await asyncio.to_thread(self.service.capture.store.read, stored(asset), 0, asset['byte_length'] - 1)
                frames = source_frames(source, pcm, selected)
                if adapter is None:
                    transport = self.make_transport(stream)
                    if not isinstance(transport, FakeTransport):
                        raise ValueError('an offline ASR transport is required')
                    stream, identity, permit, expires = await asyncio.to_thread(self._start, claim, source)
                    if datetime.now(timezone.utc) >= expires:
                        break
                    adapter = DashScopeAdapter(transport, permit=permit)
                    await adapter.start({**vars(identity), 'class_session_id': str(stream['class_session_id']),
                                         'capture_epoch_id': str(stream['capture_epoch_id'])})
                    if adapter.state != 'streaming':
                        break
                for frame in frames:
                    if monotonic() >= deadline or adapter.state != 'streaming':
                        break
                    submission, expires = await asyncio.to_thread(self._mint_frame, claim, stream, frame, sent_samples)
                    if datetime.now(timezone.utc) >= expires:
                        break
                    await adapter.send(frame.provider_value())
                    if (frame.samples.start, frame.samples.end) in adapter.sent_ranges:
                        await asyncio.to_thread(self._handoff, claim, stream, submission)
                        sent_samples += frame.samples.count
                    self._events(claim, stream, epoch, adapter, sent_samples)
                if datetime.now(timezone.utc) >= expires:
                    break
        except (AuthRejected, BillingBlocked, ObjectUnavailable, ProtocolError, ValueError, StaleFence):
            pass
        finally:
            if adapter is not None:
                if adapter.state == 'streaming':
                    try:
                        with self.database.transaction(OwnerContext(claim.owner_id)) as tx:
                            repo = self._repo(tx, claim)
                            current = repo.window(task=claim.id)
                            repo.set_state(current, 'draining')
                    except (AuthRejected, InactiveOwner):
                        pass
                    await adapter.finish()
                try:
                    self._events(claim, stream, epoch, adapter, sent_samples)
                finally:
                    await adapter.aclose()
            try:
                self._complete(claim, adapter)
            except (AuthRejected, InactiveOwner, StaleFence):
                self.lifecycle.synchronize(claim.owner_id, claim.id)
