"""Owner-scoped persistent task transitions. Use only within a database UoW."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from uuid import UUID, uuid4

import sqlalchemy as sa

from ..domain.data import Gone, NotFound
from ..domain.tasks import (Attempt, IdempotencyMismatch, KINDS, ProviderResult,
                            ResultConflict, StaleFence, Task, UnsafeRetry,
                            fingerprint, input_reference, validate_json_output)
from .task_schema import tables
from .task_sources import PgTaskSources


class PgTaskRepository:
    def __init__(self, connection, owner_id, catalog, events, receipts):
        self.connection, self.owner_id, self.catalog = connection, owner_id, catalog
        self.events, self.receipts = events, receipts
        self.sources = PgTaskSources(connection, owner_id)

    def _query(self, name, identity):
        t = tables[name]
        return sa.select(t).where(t.c.owner_id == self.owner_id, t.c.id == identity)

    def _load(self, identity, *, lock=False):
        row = self.connection.execute(self._query('tasks', identity)).mappings().one_or_none()
        if row is None:
            raise NotFound('task not found')
        input_reference(row['input_ref'])
        if self.connection.scalar(sa.text("SELECT classroom_web_tombstoned('account', :owner)"),
                                   {'owner': self.owner_id}):
            raise Gone('account revoked')
        self.sources.authorize(row['class_session_id'], row['input_ref'])
        if lock:
            original_class = row['class_session_id']
            row = self.connection.execute(self._query('tasks', identity).with_for_update()).mappings().one()
            if original_class != row['class_session_id']:
                raise StaleFence('task lineage changed')
        return Task(**row)

    def get(self, identity):
        return self._load(identity)

    def _attempt(self, task_id, *, lock=False):
        t = tables['task_attempts']
        q = sa.select(t).where(t.c.owner_id == self.owner_id, t.c.task_id == task_id).order_by(
            t.c.attempt_number.desc()).limit(1)
        if lock:
            q = q.with_for_update()
        row = self.connection.execute(q).mappings().one_or_none()
        return Attempt(**row) if row else None

    def _history(self, task_id):
        """Task lock precedes all attempts, in stable attempt-number order."""
        t = tables['task_attempts']
        return [Attempt(**row) for row in self.connection.execute(sa.select(t).where(
            t.c.owner_id == self.owner_id, t.c.task_id == task_id
        ).order_by(t.c.attempt_number).with_for_update()).mappings()]

    def _saved_result(self, history):
        if not history:
            return None, None
        t = tables['provider_results']
        results = {row['attempt_id']: row for row in self.connection.execute(sa.select(t).where(
            t.c.owner_id == self.owner_id, t.c.attempt_id.in_([attempt.id for attempt in history])
        ).order_by(t.c.id).with_for_update()).mappings()}
        for attempt in reversed(history):
            result = results.get(attempt.id)
            if result is not None:
                # Normal store_result commits this pointer atomically. Also
                # recognize durable result evidence in legacy/incomplete rows.
                if attempt.raw_output_ref is None:
                    self.connection.execute(tables['task_attempts'].update().where(
                        tables['task_attempts'].c.owner_id == self.owner_id,
                        tables['task_attempts'].c.id == attempt.id
                    ).values(raw_output_ref=result['id'], provider_outcome='succeeded'))
                    attempt = replace(attempt, raw_output_ref=result['id'], provider_outcome='succeeded')
                    history[:] = [attempt if item.id == attempt.id else item for item in history]
                return attempt, result
        return None, None

    @staticmethod
    def _submitted(attempt):
        return bool(attempt.submitted_at or attempt.provider_request_id or attempt.raw_output_ref
            or attempt.provider_outcome in ('succeeded', 'failed', 'unknown')
            or attempt.state not in ('planned', 'cancelled'))

    def _cancel_unsent(self, history):
        for attempt in history:
            if not self._submitted(attempt) and attempt.state != 'cancelled':
                self.connection.execute(tables['task_attempts'].update().where(
                    tables['task_attempts'].c.owner_id == self.owner_id,
                    tables['task_attempts'].c.id == attempt.id
                ).values(state='cancelled', finished_at=sa.func.clock_timestamp()))

    def _unknown_submissions(self, history):
        for attempt in history:
            if self._submitted(attempt) and not attempt.raw_output_ref and attempt.state not in ('succeeded', 'failed'):
                self.connection.execute(tables['task_attempts'].update().where(
                    tables['task_attempts'].c.owner_id == self.owner_id,
                    tables['task_attempts'].c.id == attempt.id
                ).values(state='unknown', provider_outcome=(attempt.provider_outcome
                    if attempt.provider_outcome == 'succeeded' else 'unknown'),
                    error_code='PROVIDER_RESULT_UNKNOWN'))

    def _fenced(self, identity, token, worker):
        task = self._load(identity, lock=True)
        now = self.connection.scalar(sa.select(sa.func.clock_timestamp()))
        if (task.state not in ('running', 'cancel_requested') or task.lease_token != token
                or task.leased_by != worker or task.lease_expires_at is None
                or task.lease_expires_at <= now):
            raise StaleFence('STALE_FENCE')
        return task

    def _write(self, task, **values):
        t = tables['tasks']
        row = self.connection.execute(t.update().where(t.c.owner_id == self.owner_id,
            t.c.id == task.id, t.c.lease_token == task.lease_token).values(
                **values, revision=t.c.revision + 1, updated_at=sa.func.clock_timestamp()
            ).returning(t)).mappings().one()
        result = Task(**row)
        attempt = self._attempt(task.id)
        self.events.task_changed(result, may_have_charged=bool(attempt and attempt.submitted_at))
        return result

    def enqueue(self, kind, input_ref, *, op_id, operation='enqueueTask',
                class_session_id=None, parent_task_id=None, priority=0):
        if kind not in KINDS or type(priority) is not int or not -100 <= priority <= 100:
            raise ValueError('invalid task kind or priority')
        reference = input_reference(input_ref)
        request_hash = fingerprint({'kind': kind, 'input_ref': reference,
            'class_session_id': str(class_session_id) if class_session_id else None,
            'parent_task_id': str(parent_task_id) if parent_task_id else None, 'priority': priority})
        old = self.receipts.lock_and_get(operation, op_id, request_hash)
        if old:
            # Authorize the current lineage before returning even a minimized receipt.
            return self.get(old['resource_id'])
        self.sources.authorize(class_session_id, reference)
        if parent_task_id:
            parent = self._load(parent_task_id)
            if parent.class_session_id != class_session_id:
                raise ValueError('child task must have same classroom')
        row = self.connection.execute(tables['tasks'].insert().values(
            id=uuid4(), owner_id=self.owner_id, kind=kind, state='queued', input_ref=reference,
            schema_version='1', class_session_id=class_session_id, parent_task_id=parent_task_id,
            priority=priority, not_before=sa.func.clock_timestamp()).returning(tables['tasks'])).mappings().one()
        task = Task(**row)
        self.receipts.store_task(operation, op_id, request_hash, task)
        self.events.task_changed(task)
        return task

    def heartbeat(self, task_id, lease_token, worker, lease_seconds=30):
        task = self._fenced(task_id, lease_token, worker)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError('lease_seconds must be 1..3600')
        # Renewal is scheduling metadata, not a new visible task state/revision.
        self.connection.execute(tables['tasks'].update().where(
            tables['tasks'].c.owner_id == self.owner_id, tables['tasks'].c.id == task.id
        ).values(lease_expires_at=sa.func.clock_timestamp() + sa.text(f"interval '{lease_seconds} seconds'")))
        return self.get(task_id)

    def prepare_attempt(self, task_id, lease_token, worker):
        task = self._fenced(task_id, lease_token, worker)
        history = self._history(task_id)
        saved, result = self._saved_result(history)
        if saved:
            if result['conflict_sha256'] is not None:
                raise ResultConflict('PROVIDER_RESULT_CONFLICT')
            return saved
        if task.may_have_charged or any(self._submitted(item) for item in history):
            raise UnsafeRetry('existing attempt may have charged; no automatic resend')
        if task.state != 'running' or task.cancel_requested_at is not None:
            raise UnsafeRetry('task cancellation prevents new submissions')
        if history:
            if history[-1].state == 'planned':
                return history[-1]
            raise UnsafeRetry('attempt is no longer dispatchable')
        row = self.connection.execute(tables['task_attempts'].insert().values(
            id=uuid4(), owner_id=self.owner_id, task_id=task_id, attempt_number=1,
            state='planned', provider='mock', model='synthetic', price_revision='synthetic-no-charge',
            provider_outcome='not_submitted').returning(tables['task_attempts'])).mappings().one()
        return Attempt(**row)

    def mark_submitting(self, task_id, attempt_id, lease_token, worker):
        task = self._fenced(task_id, lease_token, worker)
        history = self._history(task_id)
        attempt = history[-1] if history else None
        if (task.state != 'running' or attempt is None or attempt.id != attempt_id
                or task.cancel_requested_at is not None or task.may_have_charged
                or any(self._submitted(item) for item in history)
                or attempt.state != 'planned' or attempt.submitted_at is not None
                or attempt.raw_output_ref is not None):
            raise UnsafeRetry('only a never-submitted planned attempt can acquire send intent')
        self.connection.execute(tables['task_attempts'].update().where(
            tables['task_attempts'].c.id == attempt_id, tables['task_attempts'].c.owner_id == self.owner_id
        ).values(state='submitting', submitted_at=sa.func.clock_timestamp(), provider_outcome='unknown'))
        self._write(task, may_have_charged=True)

    def store_result(self, task_id, attempt_id, raw_output):
        task = self._load(task_id, lock=True)
        attempt = next((item for item in self._history(task_id) if item.id == attempt_id), None)
        if attempt is None or attempt.id != attempt_id or attempt.submitted_at is None:
            raise UnsafeRetry('result requires the existing submitted attempt')
        if not isinstance(raw_output, str) or len(raw_output) > 200000:
            raise ValueError('raw result exceeds bounded text limit')
        digest = hashlib.sha256(raw_output.encode('utf-8')).hexdigest()
        t = tables['provider_results']
        old = self.connection.execute(sa.select(t).where(
            t.c.owner_id == self.owner_id, t.c.attempt_id == attempt_id)).mappings().one_or_none()
        if old:
            if old['sha256'] != digest or old['raw_output'] != raw_output:
                # Return normally so this non-content diagnostic commits. Raising
                # inside a UoW would roll it back and erase the evidence.
                row = self.connection.execute(t.update().where(
                    t.c.owner_id == self.owner_id, t.c.id == old['id']
                ).values(conflict_sha256=old['conflict_sha256'] or digest,
                         conflict_detected_at=old['conflict_detected_at'] or sa.func.clock_timestamp())
                    .returning(t)).mappings().one()
                if task.error_code != 'PROVIDER_RESULT_CONFLICT':
                    self._write(task, state='unknown' if task.state in ('running','cancel_requested','queued') else task.state,
                        error_code='PROVIDER_RESULT_CONFLICT', leased_by=None, lease_expires_at=None)
                return ProviderResult(**row)
            return ProviderResult(**old)
        row = self.connection.execute(t.insert().values(id=uuid4(), owner_id=self.owner_id,
            attempt_id=attempt_id, raw_output=raw_output, sha256=digest,
            validation_status='pending', received_at=sa.func.clock_timestamp()).returning(t)).mappings().one()
        self.connection.execute(tables['task_attempts'].update().where(
            tables['task_attempts'].c.id == attempt_id, tables['task_attempts'].c.owner_id == self.owner_id
        ).values(raw_output_ref=row['id'], provider_outcome='succeeded'))
        return ProviderResult(**row)

    def publish_result(self, task_id, attempt_id, lease_token, worker, validator=validate_json_output):
        task = self._fenced(task_id, lease_token, worker)
        history = self._history(task_id)
        attempt = next((item for item in history if item.id == attempt_id), None)
        if attempt is None or attempt.id != attempt_id or attempt.raw_output_ref is None:
            raise ValueError('no durable result to validate')
        return self._finish_result(task, attempt, validator)

    def _finish_result(self, task, attempt, validator):
        attempt_id = attempt.id
        t = tables['provider_results']
        row = self.connection.execute(self._query('provider_results', attempt.raw_output_ref)
                                       .with_for_update()).mappings().one()
        if row['attempt_id'] != attempt.id:
            raise ResultConflict('provider result belongs to another attempt')
        if row['conflict_sha256'] is not None:
            raise ResultConflict('PROVIDER_RESULT_CONFLICT')
        try:
            payload = validator(row['raw_output'])  # pure, bounded local validation only
            if not isinstance(payload, dict) or len(json.dumps(payload, allow_nan=False).encode()) > 800000:
                raise ValueError('invalid validated result')
            valid = True
        except (ValueError, TypeError, RecursionError):
            payload, valid = None, False
        self.connection.execute(t.update().where(t.c.id == row['id'], t.c.owner_id == self.owner_id)
            .values(validation_status='valid' if valid else 'invalid',
                    validated_payload=payload if valid else sa.null()))
        self.connection.execute(tables['task_attempts'].update().where(
            tables['task_attempts'].c.id == attempt_id, tables['task_attempts'].c.owner_id == self.owner_id
        ).values(state='succeeded' if valid else 'failed', provider_outcome='succeeded',
                 error_code=None if valid else 'OUTPUT_INVALID', finished_at=sa.func.clock_timestamp()))
        return self._write(task, state='succeeded' if valid else 'failed',
            error_code=None if valid else 'OUTPUT_INVALID', completed_at=sa.func.clock_timestamp(),
            leased_by=None, lease_expires_at=None, lease_token=task.lease_token + 1,
            may_have_charged=True)

    def reconcile_result(self, task_id, op_id, validator=validate_json_output):
        """Explicitly reconcile only already stored bytes; new_submission=false.

        A stale worker never acquires this authorization from its old fence.
        There is no provider query, submission, refund or new attempt here.
        """
        digest = fingerprint({'task_id': str(task_id), 'new_submission': False})
        old = self.receipts.lock_and_get('reconcileTask', op_id, digest)
        task = self._load(task_id, lock=True)
        # Repair legacy cancelled+pending-raw too; a minimized old receipt is
        # not permission to leave durable local work stranded permanently.
        live = task.lease_expires_at is not None and task.lease_expires_at > self.connection.scalar(
            sa.select(sa.func.clock_timestamp()))
        if task.state in ('unknown', 'queued', 'cancelled', 'cancel_requested') and not live:
            history = self._history(task_id)
            attempt, result = self._saved_result(history)
            if attempt and result['conflict_sha256'] is None:
                self._cancel_unsent(history)
                task = self._finish_result(task, attempt, validator)
        if not old:
            self.receipts.store_task('reconcileTask', op_id, digest, task, response_status=200)
        return task

    def mark_unknown(self, task_id, attempt_id, lease_token, worker):
        task = self._fenced(task_id, lease_token, worker)
        attempt = self._attempt(task_id, lock=True)
        if attempt is None or attempt.id != attempt_id or attempt.submitted_at is None:
            raise ValueError('unknown requires submitted attempt')
        self.connection.execute(tables['task_attempts'].update().where(
            tables['task_attempts'].c.id == attempt_id, tables['task_attempts'].c.owner_id == self.owner_id
        ).values(state='unknown', provider_outcome='unknown', error_code='PROVIDER_RESULT_UNKNOWN'))
        return self._write(task, state='unknown', error_code='PROVIDER_RESULT_UNKNOWN',
                           leased_by=None, lease_expires_at=None)

    def recover(self, task_id, lease_token):
        task = self._load(task_id, lock=True)
        if task.lease_token != lease_token:
            raise StaleFence('STALE_FENCE')
        if task.state not in ('running', 'cancel_requested'):
            return task
        now = self.connection.scalar(sa.select(sa.func.clock_timestamp()))
        if task.lease_expires_at is None or task.lease_expires_at > now:
            raise StaleFence('lease is still live')
        history = self._history(task_id)
        saved, result = self._saved_result(history)
        submitted = task.may_have_charged or any(self._submitted(item) for item in history)
        if saved and result['conflict_sha256'] is not None:
            state, error = 'unknown', 'PROVIDER_RESULT_CONFLICT'
        elif not saved and submitted:
            self._unknown_submissions(history)
            self._cancel_unsent(history)
            state, error = 'unknown', 'PROVIDER_RESULT_UNKNOWN'
        elif task.cancel_requested_at is not None and not saved:
            self._cancel_unsent(history)
            state, error = 'cancelled', None
        else:
            if task.cancel_requested_at is not None:
                self._cancel_unsent(history)
            state, error = 'queued', None
        return self._write(task, state=state, error_code=error, lease_token=task.lease_token + 1,
            leased_by=None, lease_expires_at=None,
            completed_at=sa.func.clock_timestamp() if state == 'cancelled' else None,
            may_have_charged=bool(submitted or saved))

    def cancel(self, task_id, op_id):
        request_hash = fingerprint({'task_id': str(task_id)})
        old = self.receipts.lock_and_get('cancelTask', op_id, request_hash)
        task = self._load(task_id, lock=True)
        history = self._history(task.id)
        saved, result = self._saved_result(history)
        repair = task.state == 'cancelled' and saved is not None
        if old and not repair:
            return task
        if task.state not in ('succeeded', 'failed') and (task.state != 'cancelled' or repair):
            self._cancel_unsent(history)
            submitted = task.may_have_charged or any(self._submitted(item) for item in history)
            now = self.connection.scalar(sa.select(sa.func.clock_timestamp()))
            live = task.lease_expires_at is not None and task.lease_expires_at > now
            error: str | None
            if saved and result['conflict_sha256'] is not None:
                state, error = 'unknown', 'PROVIDER_RESULT_CONFLICT'
            elif saved:
                state = ('cancel_requested' if live else
                         'unknown' if task.state == 'unknown' else 'queued')
                error = task.error_code if state == 'unknown' else None
            elif submitted:
                state = 'cancel_requested' if live and task.state != 'unknown' else 'unknown'
                error = None if state == 'cancel_requested' else 'PROVIDER_RESULT_UNKNOWN'
                if not live:
                    self._unknown_submissions(history)
            else:
                state, error = 'cancelled', None
            values = dict(state=state, error_code=error, may_have_charged=bool(submitted or saved),
                cancel_requested_at=task.cancel_requested_at or now,
                completed_at=now if state == 'cancelled' else None)
            if not live or state in ('cancelled', 'unknown'):
                values.update(leased_by=None, lease_expires_at=None, lease_token=task.lease_token + 1)
            task = self._write(task, **values)
        if not old:
            self.receipts.store_task('cancelTask', op_id, request_hash, task, response_status=200)
        return task
