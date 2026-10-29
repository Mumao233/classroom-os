"""Transactional owner event sequence, minimized receipts and authenticated cursors."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from ..domain.tasks import IdempotencyMismatch, ReplayPage, ResyncRequired
from .task_schema import tables


class PgReceiptRepository:
    def __init__(self, connection, owner_id):
        self.connection, self.owner_id = connection, owner_id

    def lock_and_get(self, operation: str, op_id: UUID, request_hash: str):
        if (not isinstance(op_id, UUID) or op_id.version != 4
                or not isinstance(operation, str) or not 1 <= len(operation) <= 100
                or not operation.isascii() or not operation.replace('_', '').isalnum()):
            raise ValueError('invalid operation identity')
        # Serializes the absent-receipt case too. Hash collisions only serialize
        # unrelated operations; the actual unique key/hash remain authoritative.
        key = int.from_bytes(hashlib.sha256(
            f'{self.owner_id}:{operation}:{op_id}'.encode()).digest()[:8], 'big', signed=True)
        self.connection.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})
        t = tables['operation_receipts']
        row = self.connection.execute(sa.select(t).where(
            t.c.owner_id == self.owner_id, t.c.operation == operation, t.c.op_id == op_id
        )).mappings().one_or_none()
        if row is not None and row['request_hash'] != request_hash:
            raise IdempotencyMismatch('IDEMPOTENCY_MISMATCH')
        return row

    def store_task(self, operation, op_id, request_hash, task, response_status=202):
        # No caller-supplied response body: bearer/secret/raw response storage
        # cannot accidentally enter the generic receipt mechanism.
        self.connection.execute(tables['operation_receipts'].insert().values(
            id=uuid4(), owner_id=self.owner_id, operation=operation, op_id=op_id,
            request_hash=request_hash, resource_type='task', resource_id=task.id,
            response_status=response_status, response_body={'task_id': str(task.id)}, schema_version='1'))


class PgEventRepository:
    def __init__(self, connection, owner_id, cursor_secret: bytes | None):
        self.connection, self.owner_id = connection, owner_id
        self.secret = cursor_secret

    def _cursor(self, sequence):
        if self.secret is None or len(self.secret) < 32:
            raise ValueError('a stable server cursor secret of at least 32 bytes is required')
        body = f'1:{self.owner_id}:{sequence}'.encode('ascii')
        signature = hmac.digest(self.secret, body, 'sha256')
        return base64.urlsafe_b64encode(body + b'.' + signature).decode('ascii').rstrip('=')

    def _decode(self, cursor):
        if cursor is None:
            return 0
        try:
            if not isinstance(cursor, str) or len(cursor) > 512:
                raise ValueError()
            raw = base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True)
            body, signature = raw[:-33], raw[-32:]
            if raw[-33:-32] != b'.' or self.secret is None:
                raise ValueError()
            if not hmac.compare_digest(signature, hmac.digest(self.secret, body, 'sha256')):
                raise ValueError()
            version, owner, sequence = body.decode('ascii').split(':')
            if version != '1' or owner != str(self.owner_id) or not sequence.isdecimal():
                raise ValueError()
            return int(sequence)
        except (ValueError, UnicodeError, IndexError) as error:
            raise ResyncRequired('resync_required') from error

    def _head(self):
        t = tables['owner_event_counters']
        next_value = self.connection.scalar(sa.select(t.c.next_sequence).where(t.c.owner_id == self.owner_id))
        return (next_value or 1) - 1

    def watermark(self):
        return self._cursor(self._head())

    def task_changed(self, task, *, may_have_charged=False):
        counter = tables['owner_event_counters']
        self.connection.execute(insert(counter).values(
            id=uuid4(), owner_id=self.owner_id, next_sequence=1
        ).on_conflict_do_nothing(index_elements=['owner_id']))
        # UPDATE locks the owner counter until this entire business transaction
        # commits. Rollback restores its value, so there are no commit-order holes.
        number = self.connection.scalar(counter.update().where(
            counter.c.owner_id == self.owner_id
        ).values(next_sequence=counter.c.next_sequence + 1).returning(counter.c.next_sequence)) - 1
        payload = {'task_id': str(task.id),
                   'class_session_id': str(task.class_session_id) if task.class_session_id else None,
                   'revision': task.revision, 'state': task.state, 'error_code': task.error_code,
                   'may_have_charged': bool(may_have_charged or task.may_have_charged or task.state == 'unknown')}
        event_id = uuid4()
        self.connection.execute(tables['outbox_events'].insert().values(
            id=event_id, owner_id=self.owner_id, event_sequence=number, event_type='task.changed',
            aggregate_type='task', aggregate_id=task.id, aggregate_revision=task.revision,
            payload=payload, schema_version='1'))
        # NOTIFY is a commit-delivered hint only; replay does not depend on it.
        self.connection.execute(sa.text("SELECT pg_notify('classroom_web_events', :event)"),
                                {'event': str(event_id)})
        return event_id

    def replay(self, cursor=None, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('event page limit must be 1..100')
        sequence = self._decode(cursor)
        # Read head first. At READ COMMITTED a later append can only extend it;
        # restricting the page to this committed head keeps the response complete.
        head = self._head()
        if sequence > head:
            raise ResyncRequired('resync_required')
        t = tables['outbox_events']
        rows = self.connection.execute(sa.select(t).where(
            t.c.owner_id == self.owner_id, t.c.event_sequence > sequence,
            t.c.event_sequence <= head).order_by(t.c.event_sequence).limit(limit)).mappings().all()
        expected = min(limit, head - sequence)
        if len(rows) != expected or any(row['event_sequence'] != sequence + i + 1 for i, row in enumerate(rows)):
            raise ResyncRequired('resync_required')
        events = []
        for row in rows:
            # This engine only emits minimized task status, never immutable text.
            events.append({'protocol_version': 1, 'type': row['event_type'],
                           'sent_at': row['created_at'].isoformat(), 'data': row['payload'],
                           'event_id': str(row['id']), 'cursor': self._cursor(row['event_sequence'])})
        return ReplayPage(events, self._cursor(rows[-1]['event_sequence'] if rows else sequence))

    def ack(self, event_id: UUID):
        t = tables['outbox_events']
        self.connection.execute(t.update().where(t.c.owner_id == self.owner_id, t.c.id == event_id,
            t.c.published_at.is_(None)).values(published_at=sa.func.clock_timestamp()))
