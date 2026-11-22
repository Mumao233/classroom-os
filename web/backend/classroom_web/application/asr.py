"""Explicit, synthetic-only admission for realtime and recovered audio."""
from uuid import UUID

from ..domain.asr import Samples
from ..domain.auth import AuthRejected, AuthReply
from ..domain.billing import BillingBlocked, QuotaExceeded, ReservationRequest
from ..domain.data import Gone, NotFound, Resource
from ..domain.identity import AuthorizationDenied
from ..domain.tasks import IdempotencyMismatch, ResyncRequired
from ..infrastructure.asr_repository import PgAsrRepository
from .media import fingerprint, identifier


class AsrService:
    """Compose the established capture and budget gates without provider I/O."""

    WINDOW_MS = 20_000
    TOKEN_INPUT_MAX = 10_000
    TOKEN_OUTPUT_MAX = 2_000

    def __init__(self, capture, *, price_id: UUID, enabled=False):
        self.capture, self.price_id, self.enabled = capture, price_id, enabled
        self.auth, self.database = capture.auth, capture.database
        from .asr_events import AsrEvents
        self.events = AsrEvents(self)

    def require_enabled(self):
        if not self.enabled or not getattr(self.capture.store, 'synthetic_loopback', False):
            raise AuthRejected('SERVICE_DISABLED', 503)
        self.capture.available()

    def _epoch(self, repo, principal, policy, classroom, body, *, acquiring=True):
        epoch = repo.epoch(identifier(body['capture_epoch_id']), identifier(classroom))
        repo.consents(epoch, policy)
        if not policy['synthetic']:
            raise AuthRejected('SERVICE_DISABLED', 503)
        if body['consent_version'] != policy['asr_consent_version']:
            raise AuthRejected('CONSENT_REQUIRED', 403)
        if epoch['device_id'] != principal.device_id:
            raise AuthRejected('FORBIDDEN', 403)
        if epoch['fence_token'] != body['fence_token']:
            raise AuthRejected('STALE_FENCE', 409)
        if acquiring and body['mode'] == 'live':
            self.capture.active(repo, principal, epoch, body['fence_token'], recording=True)
        return epoch

    @staticmethod
    def _source_record(source, samples, epoch):
        return dict(id=source.chunk_id, first=samples.start, end=samples.end,
            receipt=source.receipt_id, sha256=source.sha256,
            manifest=epoch['manifest_sha256'] if source.ingest_mode == 'recovery' else None)

    @staticmethod
    def _continuous(records):
        records.sort(key=lambda row: row['first'])
        if not records:
            return records
        stop = 1
        while stop < len(records) and records[stop]['first'] == records[stop-1]['end']:
            stop += 1
        return records[:stop]

    def _scope(self, repo, epoch, body):
        chunk_ids = body['source_chunk_ids']
        if not chunk_ids and body['mode'] != 'live':
            raise AuthRejected('VALIDATION_FAILED', 422)
        if len(chunk_ids) != len(set(chunk_ids)):
            raise AuthRejected('VALIDATION_FAILED', 422)
        if len(chunk_ids) > 100:
            raise AuthRejected('CAPACITY_EXCEEDED', 422)
        records = []
        for item in chunk_ids:
            source, _ = repo.asr_source(repo.chunk(identifier(item)), epoch)
            if body['mode'] == 'live' and source.ingest_mode != 'live':
                raise AuthRejected('FORBIDDEN', 403)
            samples = Samples(source.start_sample, source.end_sample)
            previous = repo.dispatch_ranges(epoch['id'], samples)
            records.extend(self._source_record(source, free, epoch)
                           for free in samples.excluding(previous))
        if chunk_ids and not records:
            raise AuthRejected('REVISION_CONFLICT', 409)
        records = self._continuous(records)
        if records and records[-1]['end'] - records[0]['first'] > self.WINDOW_MS * 16:
            raise AuthRejected('CAPACITY_EXCEEDED', 422)
        return records

    def _reserve(self, repo, epoch, key, scope, *, parent=None):
        classroom = epoch['class_session_id']
        price = repo.tx.billing._price(self.price_id)
        if (not price.synthetic or (price.provider, price.model, price.region) !=
                ('aliyun', 'qwen-audio-3.1-asr-flash-streaming', 'cn-beijing')
                or set(price.rates) != {'audio_duration_ms','input_tokens','output_tokens'}):
            raise AuthRejected('BUDGET_BLOCKED', 409)
        duration = Samples(scope[0]['first'], scope[-1]['end']).duration_ms if scope else self.WINDOW_MS
        units = dict(audio_duration_ms=duration, input_tokens=self.TOKEN_INPUT_MAX,
                     output_tokens=self.TOKEN_OUTPUT_MAX)
        try:
            return repo.tx.billing.reserve(ReservationRequest(op_id=key, purpose='asr_live',
                price_id=self.price_id, maximum_units=units, task_max_microyuan=price.amount(units),
                input_ref={'schema_version': 1, 'class_session_id': str(classroom)}, class_session_id=classroom,
                schema_version='pr06b-1', parent_task_id=parent))
        except QuotaExceeded:
            raise AuthRejected('QUOTA_EXCEEDED', 409) from None
        except BillingBlocked:
            raise AuthRejected('BUDGET_BLOCKED', 409) from None

    def create(self, ctx, classroom, key, body):
        self.require_enabled()
        with self.capture.transaction(ctx, classroom) as (capture, principal, policy):
            repo = PgAsrRepository(capture.tx, principal.user_id)
            digest = fingerprint(dict(class_session_id=classroom, **body))
            op = 'createAsrStream'
            key = identifier(key)
            previous = repo.receipt(op, key, digest)
            epoch = self._epoch(repo, principal, policy, classroom, body, acquiring=previous is None)
            if previous is not None:
                return repo.view(repo.window(identifier(previous['response_body']['stream_id'])))
            repo.require_free_epoch(epoch['id'])
            source_scope = self._scope(repo, epoch, body)
            reservation = self._reserve(repo, epoch, key, source_scope)
            stream = repo.insert_window(task_id=reservation['task_id'], epoch=epoch, principal=principal,
                                        body=body, source_scope=source_scope, origin=None)
            repo.remember(op, key, digest, 'class_session', epoch['class_session_id'],
                          {'stream_id': str(stream['id'])}, 201)
            return repo.view(stream)

    def get(self, ctx, identity):
        try:
            with self.database.authorized_transaction(self.auth.principal(ctx)) as tx:
                repo = PgAsrRepository(tx, tx.catalog.owner_id)
                return repo.view(repo.window(identifier(identity), lock=False))
        except (NotFound, Gone):
            raise AuthRejected('NOT_FOUND', 404) from None
        except AuthorizationDenied:
            raise AuthRejected() from None

    def task_view(self, repo, task_id):
        task = repo.tx.tasks.get(task_id)
        attempt = repo.tx.tasks._attempt(task_id)
        reservation = repo.tx.billing.for_task(task_id)
        return dict(id=str(task.id), revision=task.revision, created_at=task.created_at.isoformat(),
            updated_at=task.updated_at.isoformat(), class_session_id=str(task.class_session_id),
            kind=task.kind, state=task.state, attempt_id=str(attempt.id) if attempt else None,
            parent_task_id=str(task.parent_task_id) if task.parent_task_id else None,
            completed_units=0, total_units=None, error_code=task.error_code,
            result_id=None, source_snapshot_id=None, provider_outcome=attempt.provider_outcome if attempt else 'not_submitted',
            maximum_cost_microyuan=str(reservation['reserved_microyuan']),
            settled_cost_microyuan=str(reservation['settled_microyuan']) if reservation['settled_microyuan'] is not None else None,
            may_have_charged=task.may_have_charged)

    def task(self, ctx, task_id, key=None):
        if key is not None:
            self.auth.require_csrf(ctx, 'session')
        with self.database.authorized_transaction(self.auth.principal(ctx)) as tx:
            repo = PgAsrRepository(tx, tx.catalog.owner_id)
            stream = repo.window(task=task_id, lock=key is not None)
            if key is not None:
                task = tx.tasks.cancel(task_id, key)
                if task.state == 'cancelled' and stream['attempt_id'] is None:
                    reservation = tx.billing.for_task(task_id)
                    if reservation['state'] == 'reserved':
                        tx.billing.release_unsent(reservation['id'])
                    repo.set_state(stream, 'failed')
                elif task.state == 'cancel_requested':
                    repo.freeze_finish(stream)
            return self.task_view(repo, task_id)

    def _retry_scope(self, repo, original, epoch):
        rows = repo.execute("""SELECT chunk_id,min(start_sample) AS first,max(end_sample) AS last
            FROM public.asr_dispatches WHERE owner_id=:owner AND stream_id=:stream
            GROUP BY chunk_id ORDER BY min(start_sample)""", stream=original['id']).mappings().all()
        finals = repo.execute("""SELECT start_sample,end_sample FROM public.asr_final_segments
            WHERE owner_id=:owner AND stream_id=:stream""", stream=original['id']).mappings().all()
        confirmed = [Samples(row['start_sample'], row['end_sample']) for row in finals]
        records = []
        for row in rows:
            source, _ = repo.asr_source(repo.chunk(row['chunk_id']), epoch)
            attempted = Samples(row['first'], row['last'])
            already_retried = repo.dispatch_ranges(epoch['id'], attempted, ignore_attempt=original['attempt_id'])
            records.extend(self._source_record(source, free, epoch)
                           for free in attempted.excluding([*confirmed, *already_retried]))
        if not records:
            raise AuthRejected('SOURCE_UNAVAILABLE', 409)
        return self._continuous(records)

    def retry(self, ctx, task_id, key, body):
        self.require_enabled()
        self.auth.require_csrf(ctx, 'session')
        principal = self.auth.principal(ctx)
        with self.database.authorized_transaction(principal) as tx:
            original = PgAsrRepository(tx, principal.user_id).window(task=task_id, lock=False)
        with self.capture.transaction(ctx, str(original['class_session_id'])) as (capture, principal, policy):
            repo = PgAsrRepository(capture.tx, principal.user_id)
            original = repo.window(task=task_id)
            retry_body = {'capture_epoch_id': str(original['capture_epoch_id']), 'fence_token': original['capture_fence'],
                          'consent_version': original['consent_version'], 'mode': 'catch_up'}
            epoch = self._epoch(repo, principal, policy, original['class_session_id'], retry_body)
            if not body['confirm_duplicate_billing'] or identifier(body['original_attempt_id']) != original['attempt_id']:
                raise AuthRejected('VALIDATION_FAILED', 422)
            digest = fingerprint(dict(task_id=str(task_id), **body))
            previous = repo.receipt('retryTask', key, digest)
            if previous is not None:
                return self.task_view(repo, previous['resource_id'])
            if original['state'] != 'unknown':
                raise AuthRejected('REVISION_CONFLICT', 409)
            repo.require_free_epoch(epoch['id'])
            scope = self._retry_scope(repo, original, epoch)
            reservation = self._reserve(repo, epoch, key, scope, parent=task_id)
            repo.insert_window(task_id=reservation['task_id'], epoch=epoch, principal=principal,
                body=retry_body, source_scope=scope, origin=original['attempt_id'])
            repo.remember('retryTask', key, digest, 'task', reservation['task_id'],
                          {'task_id': str(reservation['task_id'])}, 202)
            return self.task_view(repo, reservation['task_id'])

    def transcripts(self, ctx, operation, params):
        from .transcripts import read_transcripts

        principal = self.auth.principal(ctx)
        with self.database.authorized_transaction(principal) as tx:
            return read_transcripts(tx, operation, params)

    def dispatch(self, operation, ctx, body, key, params):
        try:
            if operation == 'createAsrStream':
                value = self.create(ctx, params['class_session_id'], key, body)
            elif operation == 'getAsrStream':
                value = self.get(ctx, params['stream_id'])
            elif operation == 'finishAsrStream':
                value = self.finish(ctx, params['stream_id'], key)
            elif operation == 'retryTask':
                value = self.retry(ctx, identifier(params['task_id']), identifier(key), body)
            elif operation in ('getTask', 'cancelTask'):
                value = self.task(ctx, identifier(params['task_id']), identifier(key) if key else None)
            else:
                value = self.transcripts(ctx, operation, params)
            return AuthReply(value)
        except (Gone, NotFound):
            raise AuthRejected('NOT_FOUND', 404) from None
        except AuthorizationDenied:
            raise AuthRejected() from None
        except IdempotencyMismatch:
            raise AuthRejected('IDEMPOTENCY_MISMATCH', 409) from None
        except ResyncRequired:
            raise AuthRejected('CURSOR_EXPIRED', 409) from None

    def finish(self, ctx, identity, key):
        self.auth.require_csrf(ctx, 'session')
        try:
            with self.database.authorized_transaction(self.auth.principal(ctx)) as tx:
                repo = PgAsrRepository(tx, tx.catalog.owner_id)
                stream = repo.window(identifier(identity))
                digest = fingerprint({'stream_id': identity})
                key = identifier(key)
                if repo.receipt('finishAsrStream', key, digest) is None:
                    stream = repo.freeze_finish(stream)
                    repo.remember('finishAsrStream', key, digest, 'class_session', stream['class_session_id'],
                                  {'stream_id': identity}, 200)
                return repo.view(stream)
        except AuthorizationDenied:
            raise AuthRejected() from None
