"""Queue and execute local transfers without ever acquiring a provider send intent."""
import hashlib

from .deletion import DeletionWorker
from .portable_maintenance import PortableMaintenance
from .media import fingerprint, identifier
from .portable_commit import ImportCopy, as_json, literal_markdown
from .portable_snapshot import ExportSnapshot, scope_members
from .portable_transfers import operation_key
from ..domain.auth import AuthRejected, AuthReply
from ..domain.data import Gone, InactiveOwner, NotFound, OwnerContext, Resource, RevisionConflict
from ..domain.media import ObjectUnavailable
from ..domain.portable import InvalidPackage, PORTABLE_V21
from ..domain.portable_export import write_aggregate
from ..domain.qa import QaRejected
from ..domain.tasks import StaleFence, UnsafeRetry

SCHEMA = 'pr08c-1'
LEASE_SECONDS = int(PORTABLE_V21.worker_lease.total_seconds())
PATHS = frozenset(('/exports', '/imports/preview', '/imports/commit',
                   '/tasks/{task_id}', '/tasks/{task_id}/cancel'))


class PortableJobs:
    def __init__(self, transfers):
        self.transfers = transfers
        self.media = transfers.media
        self.deletion = transfers.deletion

    @staticmethod
    def _binding(life, task_id):
        return life.rows('''SELECT * FROM portable_task_bindings
            WHERE owner_id=:owner AND task_id=:task''', task=task_id).one_or_none()

    @staticmethod
    def _view(tx, task, binding):
        attempt = tx.tasks._attempt(task.id)
        success = task.state == 'succeeded'
        return dict(id=str(task.id), revision=task.revision, kind=task.kind, state=task.state,
                    created_at=task.created_at.isoformat(), updated_at=task.updated_at.isoformat(),
                    class_session_id=None, attempt_id=str(attempt.id) if attempt else None, parent_task_id=None,
                    completed_units=int(success), total_units=1, error_code=task.error_code,
                    result_id=str(binding['result_id']) if success else None,
                    source_snapshot_id=None, provider_outcome='not_submitted', maximum_cost_microyuan='0',
                    settled_cost_microyuan='0', may_have_charged=False)

    def export(self, ctx, key, body):
        if body['include_audio'] or body['confirmed_audio_privacy']:
            raise AuthRejected('VALIDATION_FAILED', 422)
        return self._enqueue(ctx, key, body, 'export')

    def commit(self, ctx, key, body):
        if body['confirm_independent_copy'] is not True:
            raise AuthRejected('VALIDATION_FAILED', 422)
        return self._enqueue(ctx, key, body, 'import')

    def _enqueue(self, ctx, key, body, kind):
        key = operation_key(key)
        request_hash = fingerprint(body)
        with self.transfers._transaction(ctx, write=True) as (life, repo, _):
            tx = repo.tx
            task = tx.tasks.enqueue(kind, {'schema_version': 1}, op_id=key,
                                    operation='portable' + kind.title(), schema_version=SCHEMA)
            binding = self._binding(life, task.id)
            if binding is not None:
                if binding['payload'] != body:
                    raise AuthRejected('IDEMPOTENCY_MISMATCH', 409)
                return self._view(tx, task, binding)
            if kind == 'export':
                scope_id = identifier(body['scope_id'])
                sources = scope_members(life, tx, body['scope'], scope_id)
                self.transfers._authorize_sources(repo, sources)
                transfer = life.reserve('export', PORTABLE_V21.archive_bytes, key, request_hash)
                slot = life.claim(transfer['id'], key, request_hash, task.id)
                life.bind_sources(transfer['id'], sources)
            else:
                preview_id = operation_key(body['preview_id'])
                preview = life.rows('''SELECT s.id,s.transfer_id,t.destination_course_id,t.archive_sha256
                    FROM portable_slots s JOIN portable_transfers t ON (t.owner_id,t.id)=(s.owner_id,s.transfer_id)
                    WHERE s.owner_id=:owner AND s.op_id=:id AND t.kind='import' ''', id=preview_id).one_or_none()
                if preview is None:
                    raise AuthRejected('NOT_FOUND', 404)
                if preview['archive_sha256'] != body['archive_sha256']:
                    raise AuthRejected('SOURCE_CHANGED', 409)
                tx.catalog._load(Resource.COURSE, preview['destination_course_id'], share=True)
                transfer = life.transfer(preview['transfer_id'], state='verified')
                slot = life.confirm(preview['id'], task.id)
            binding = life.rows('''INSERT INTO portable_task_bindings (owner_id,task_id,transfer_id,slot_id,payload)
                VALUES(:owner,:task,:transfer,:slot,CAST(:payload AS jsonb)) RETURNING *''',
                task=task.id, transfer=transfer['id'], slot=slot['id'], payload=as_json(body)).one()
            return self._view(tx, task, binding)

    def preview(self, ctx, key, body):
        upload_id = body['upload_id']
        try:
            self.transfers.upload_status(ctx, upload_id)
        except AuthRejected as error:
            if error.code != 'NOT_FOUND':
                raise
            upload_id = self.transfers.adapt_legacy(ctx, upload_id)
        result = self.transfers.preview(ctx, key, upload_id, body['destination_course_id'])
        return {key: result[key] for key in ('id','archive_sha256','class_session_count','media_bytes','expires_at')} | {
            'warnings': [], 'unsupported_fields': []}

    def task(self, ctx, task_id, *, cancel_key=None):
        task_id = identifier(task_id)
        with self.transfers._transaction(ctx, write=cancel_key is not None) as (life, repo, _):
            binding = self._binding(life, task_id)
            if binding is None:
                raise AuthRejected('NOT_FOUND', 404)
            task = repo.tx.tasks.get(task_id)
            if cancel_key is not None:
                task = repo.tx.tasks.cancel(task_id, operation_key(cancel_key))
                if task.state == 'cancelled':
                    life.close(binding['transfer_id'])
                    life.gate()
            return self._view(repo.tx, task, binding)

    def dispatch(self, operation_id, ctx, body, key, params):
        if operation_id == 'createExport':
            return AuthReply(self.export(ctx, key, body))
        if operation_id == 'previewImport':
            return AuthReply(self.preview(ctx, key, body))
        if operation_id == 'commitImport':
            return AuthReply(self.commit(ctx, key, body))
        if operation_id in ('getTask', 'cancelTask'):
            return AuthReply(self.task(ctx, params['task_id'], cancel_key=key))
        raise AuthRejected('NOT_FOUND', 404)


class PortableTaskWorker:
    def __init__(self, jobs, scheduler, *, worker_id, maintenance=None):
        self.jobs = jobs
        self.transfers = jobs.transfers
        self.scheduler = scheduler
        self.worker_id = worker_id
        deletion = self.transfers.deletion
        self.maintenance = maintenance or PortableMaintenance(self.transfers, DeletionWorker(
            queue=deletion.queue, store=deletion.store, policy=deletion.policy, worker_id=worker_id))

    def _recover(self):
        for claim in self.scheduler.expired():
            if claim.kind not in ('import','export'):
                continue
            owner = OwnerContext(claim.owner_id)
            try:
                with self.transfers._transaction(owner) as (_, repo, _):
                    if repo.tx.tasks.get(claim.id).schema_version == SCHEMA:
                        repo.tx.tasks.recover(claim.id, claim.lease_token)
            except (Gone, InactiveOwner, NotFound):
                self.scheduler.recover_revoked(claim.id, claim.lease_token)
            except (StaleFence, RevisionConflict):
                continue

    def run_once(self):
        self.maintenance.tick()
        if not self.transfers.deletion.ready:
            return False
        self._recover()
        claims = self.scheduler.claim(self.worker_id, LEASE_SECONDS, schema_version=SCHEMA)
        if not claims:
            return False
        claim = claims[0]
        owner = OwnerContext(claim.owner_id)
        try:
            with self.transfers._transaction(owner) as (life, repo, _):
                task = repo.tx.tasks.get(claim.id)
                binding = self.jobs._binding(life, claim.id)
                if task.schema_version != SCHEMA or binding is None:
                    raise UnsafeRetry('portable queue job has no owner binding')
                attempt = repo.tx.tasks.prepare_attempt(claim.id, claim.lease_token, self.worker_id)
                slot = life.rows('SELECT fence FROM portable_slots WHERE owner_id=:owner AND id=:id',
                                 id=binding['slot_id']).one()
                lease = binding['slot_id'], task.id, slot['fence']
            def pulse():
                with self.transfers._transaction(owner) as (life, repo, _):
                    repo.tx.tasks.heartbeat(task.id, claim.lease_token, self.worker_id, LEASE_SECONDS)
                    life.heartbeat(*lease)

            try:
                pulse()
                if task.kind == 'export':
                    self._export(owner, task, claim, binding, attempt.id, lease, pulse)
                else:
                    self._import(owner, task, claim, binding, attempt.id, lease, pulse)
            except (AuthRejected, InvalidPackage, QaRejected, Gone, NotFound, RevisionConflict, ObjectUnavailable, OSError) as error:
                self._failed(owner, task, claim, binding, attempt.id, getattr(error, 'code', 'STORAGE_UNAVAILABLE' if isinstance(error, (ObjectUnavailable, OSError))
                                                      else 'SOURCE_UNAVAILABLE'))
        except (StaleFence, UnsafeRetry, InactiveOwner):
            pass
        return True

    def _failed(self, owner, task, claim, binding, attempt, code):
        with self.transfers._transaction(owner) as (life, repo, _):
            repo.tx.tasks.finish_portable(task.id, claim.lease_token, self.worker_id, attempt, error=code)
            life.close(binding['transfer_id'])
            life.gate()

    def _finish(self, life, tx, task, claim, attempt, result):
        life.rows('''UPDATE portable_task_bindings SET result_id=:result WHERE owner_id=:owner AND task_id=:task
            AND result_id IS NULL RETURNING task_id''', task=task.id, result=result).one()
        tx.tasks.finish_portable(task.id, claim.lease_token, self.worker_id, attempt)

    def _import(self, owner, task, claim, binding, attempt, lease, pulse):
        copier = ImportCopy(self.transfers)
        with self.transfers._transaction(owner) as (life, repo, _):
            upload = life.live_work(*lease)
            repo.tx.catalog._load(Resource.COURSE, upload['destination_course_id'], share=True)
            parts = life.parts(upload['id'])
        self.transfers._complete_parts(upload, parts)
        rooms = copier.prepare(owner, task, upload, parts, pulse, allow_missing=binding['payload']['allow_missing'])
        for _, _, assets in rooms:
            for asset, expected in assets.values():
                if self.transfers.media.store.inspect(asset['object_key']) != expected:
                    raise AuthRejected('SOURCE_CHANGED', 409)
        with self.transfers._transaction(owner) as (life, repo, _):
            tx = repo.tx
            tx.catalog._load(Resource.COURSE, upload['destination_course_id'], share=True)
            tx.tasks._fenced(task.id, claim.lease_token, self.worker_id)
            life.gate()
            current = life.live_work(*lease)
            result = copier.publish(life, tx, task, current, rooms)
            life.finish(*lease, 'completed')
            self._finish(life, tx, task, claim, attempt, result)

    def _export(self, owner, task, claim, binding, attempt, lease, pulse):
        body = binding['payload']
        target = binding['transfer_id']
        reader = ExportSnapshot(self.transfers, owner, pulse)
        with self.transfers._transaction(owner) as (life, repo, _):
            sources = life.sources(target)
            self.transfers._authorize_sources(repo, sources)
        classrooms, deps = [], set()
        metadata = 0
        media_bytes = 0
        entries = 1
        for source in sources:
            classroom, dependencies = reader.read(source, allow_missing=body['allow_missing'])
            metadata += len(as_json(classroom.session).encode('utf8')) + len(as_json(classroom.media).encode('utf8'))
            media_bytes += sum(asset.size for asset in classroom.assets)
            entries += len(classroom.assets) + 2
            if (metadata > PORTABLE_V21.total_metadata_bytes or entries > PORTABLE_V21.files
                    or metadata + media_bytes > PORTABLE_V21.expanded_bytes):
                raise AuthRejected('CAPACITY_EXCEEDED', 413)
            classrooms.append(classroom)
            deps.update(dependencies)
        with self.transfers._transaction(owner) as (life, repo, _):
            self.transfers._authorize_sources(repo, sources)
            repo.tx.tasks._fenced(task.id, claim.lease_token, self.worker_id)
            life.live_work(*lease)
            for kind, source in sorted(deps):
                life.rows('''INSERT INTO portable_export_dependencies (owner_id,transfer_id,kind,source_id)
                    VALUES(:owner,:transfer,:kind,:id) ON CONFLICT DO NOTHING''', transfer=target, kind=kind, id=source)
            self.transfers.authorize_dependencies(life, repo, target)
        with self.transfers._spool() as output:
            length, digest, mime = self._write_export(output, body, classrooms, pulse)
            with self.transfers._transaction(owner) as (life, repo, _):
                self.transfers._authorize_sources(repo, sources)
                repo.tx.tasks._fenced(task.id, claim.lease_token, self.worker_id)
                life.gate()
                life.live_work(*lease)
                asset = life.stage(target, 0, length, digest, mime)
            obj = self.transfers.write_staged(owner, target, asset, output)
        with self.transfers._transaction(owner) as (life, repo, _):
            self.transfers._authorize_sources(repo, sources)
            repo.tx.tasks._fenced(task.id, claim.lease_token, self.worker_id)
            self.transfers.authorize_dependencies(life, repo, target)
            life.attach(target, asset, obj)
            life.finish(*lease, 'completed', inspected_sha256=digest)
            self._finish(life, repo.tx, task, claim, attempt, asset['id'])

    @staticmethod
    def _write_export(output, body, classrooms, pulse):
        if body['format'] == 'zip':
            length, digest, _ = write_aggregate(output, body['scope'], classrooms, heartbeat=pulse)
            return length, digest, 'application/zip'
        digest = hashlib.sha256()
        def write(value):
            chunk = value.encode('utf8')
            if output.tell() + len(chunk) > PORTABLE_V21.archive_bytes:
                raise AuthRejected('CAPACITY_EXCEEDED', 413)
            output.write(chunk)
            digest.update(chunk)
        if body['format'] == 'json':
            write('{"schema_version":1,"classrooms":[')
            for index, classroom in enumerate(classrooms):
                pulse()
                if index:
                    write(',')
                write(as_json(classroom.session))
            write(']}')
            mime = 'application/json'
        else:
            for classroom in classrooms:
                pulse()
                write('# ' + literal_markdown(classroom.session['title']) + '\n\n')
                for collection in ('segments','web_transcript'):
                    for row in classroom.session.get(collection, ()):
                        write(literal_markdown(row['text']) + '\n\n')
                for row in classroom.session.get('web_notes', ()):
                    write('## ' + literal_markdown(row['title']) + '\n\n' + literal_markdown(row['body']) + '\n\n')
            mime = 'text/markdown'
        return output.tell(), digest.hexdigest(), mime
