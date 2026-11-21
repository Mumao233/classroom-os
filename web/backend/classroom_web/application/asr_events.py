"""Bounded, process-local partials; final replay uses committed owner cursors."""
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from uuid import UUID

from ..infrastructure.asr_repository import PgAsrRepository


class AsrEvents:
    def __init__(self, service):
        self.service = service
        self.buffer = deque(maxlen=256)
        self.lock = Lock()
        self.sequence = 0

    def partial(self, owner, data, epoch_offset):
        entry = dict(class_session_id=data['class_session_id'], stream_id=data['stream_id'],
                     segment_id=data['segment_id'], sequence=data['sequence'], revision=data['revision'],
                     audio_offset_ms=epoch_offset + data['start_sample'] // 16,
                     provider_request_id=data['provider_request_id'], status='partial', text=data['text'])
        with self.lock:
            self.sequence += 1
            self.buffer.append((self.sequence, monotonic(), owner, entry))

    def watermark(self, ctx):
        principal = self.service.auth.principal(ctx)
        with self.service.database.authorized_transaction(principal) as tx:
            return tx.events.watermark(), self.sequence

    def poll(self, ctx, cursor, seen_partial):
        principal = self.service.auth.principal(ctx)
        with self.lock:
            fresh = monotonic() - 5
            # Reading or publishing a batch opportunistically drops expired plaintext.
            while self.buffer and self.buffer[0][1] <= fresh:
                self.buffer.popleft()
            partials = [data for seq, _, owner, data in self.buffer
                        if seq > seen_partial and owner == principal.user_id]
            high = self.sequence
        with self.service.database.authorized_transaction(principal) as tx:
            repo = PgAsrRepository(tx, principal.user_id)
            page = tx.events.replay(cursor)
            events = []
            for event in page.events:
                if event['type'] == 'transcript.final':
                    data = repo.final_event(UUID(event['data']['segment_revision_id']))
                    if data is not None:
                        events.append({**event, 'data': data})
                elif event['type'] == 'task.changed':
                    permitted = repo.execute("""SELECT EXISTS(SELECT FROM public.asr_streams
                        WHERE owner_id=:owner AND task_id=:task)""",
                        task=UUID(event['data']['task_id'])).scalar_one()
                    if permitted:
                        events.append(event)
            transient = []
            for data in partials:
                allowed = repo.execute("""SELECT EXISTS(SELECT FROM public.asr_streams s
                    WHERE s.owner_id=:owner AND s.id=:stream
                    AND NOT EXISTS(SELECT FROM public.asr_final_segments f
                        WHERE (f.owner_id,f.stream_id)=(s.owner_id,s.id) AND f.segment_id=:segment))""",
                    stream=UUID(data['stream_id']), segment=UUID(data['segment_id'])).scalar_one()
                if allowed:
                    transient.append({'protocol_version': 1, 'type': 'transcript.partial',
                        'sent_at': datetime.now(timezone.utc).isoformat(), 'data': data})
            return page.cursor, high, events, transient, len(page.events) < 100
