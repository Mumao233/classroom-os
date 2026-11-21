"""Plan, admission and candidates in the same authenticated PostgreSQL UoW."""
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from ..domain.auth import AuthRejected
from ..domain.billing import ReservationRequest
from ..domain.filetrans import DESTINATION, KIND, SCHEMA, candidate_segments, duration_ms
from .filetrans_sources import FiletransRejected, PgFiletransSources
from .qa_repository import PgQaRepository, _hash, _json, _stamp


class OtherGeneration(Exception):
    """Allow an existing HTTP generation owner to handle this request."""


class PgFiletransRepository(PgFiletransSources):
    def get_plan(self, plan_id):
        row = self.execute("""SELECT * FROM generation_plans
            WHERE owner_id=:owner AND id=:id AND kind='asr_retranscribe'""",
            id=plan_id).mappings().one_or_none()
        if row is None:
            raise OtherGeneration()
        self.classroom(row['class_session_id'], audio=False)
        return row

    def run(self, task_id):
        row = self.execute("""SELECT * FROM filetrans_runs WHERE owner_id=:owner AND task_id=:id""",
                           id=task_id).mappings().one_or_none()
        if row is None:
            raise OtherGeneration()
        return row

    def check_session(self, run):
        if not self.execute("""SELECT classroom_web_session_valid(:owner,:session,:device,:epoch,:role)""",
                session=run['auth_session_id'], device=run['device_id'],
                epoch=run['security_epoch'], role=run['auth_role']).scalar_one():
            raise AuthRejected('UNAUTHENTICATED', 401)

    def serialize(self, classroom):
        self.execute("""SELECT pg_advisory_xact_lock(hashtextextended('filetrans:' ||
                       CAST(:classroom AS text),0))""", classroom=classroom)

    def config(self, price_id, manifest):
        price = self.tx.billing._price(price_id)
        if (not price.synthetic or (price.provider, price.model, price.region) !=
                (DESTINATION['provider'], DESTINATION['model'], DESTINATION['region'])
                or set(price.rates) != {'audio_duration_ms'}):
            raise AuthRejected('BUDGET_BLOCKED', 409)
        units = {'audio_duration_ms': duration_ms(manifest['chunks'])}
        return dict(destination=DESTINATION, price_version=price.revision, price_id=str(price.id),
                    units=units, maximum_cost_microyuan=price.amount(units))

    def _sources_status(self, row):
        try:
            self.verify(row)
        except AuthRejected as error:
            if error.code in ('GONE', 'SOURCE_UNAVAILABLE', 'SOURCE_CHANGED', 'CONSENT_REQUIRED', 'SERVICE_DISABLED'):
                return 'unavailable'
            raise
        return 'changed' if self.source_changed(row) else 'unchanged'

    def source_changed(self, row):
        try:
            current = self.freeze(row['class_session_id'], row['request'])
        except AuthRejected as error:
            # The original listed chunks have passed verify(). Newly available
            # or newly inaccessible material can change the current selection.
            if error.code in ('GONE', 'SOURCE_UNAVAILABLE', 'CONSENT_REQUIRED', 'CAPACITY_EXCEEDED'):
                return True
            raise
        return _hash(current) != row['audio_manifest_sha256']

    def plan_view(self, row, *, check=True):
        manifest, config = row['audio_manifest'], row['config']
        return dict(id=str(row['id']), class_session_id=str(row['class_session_id']), kind=KIND,
            plan_sha256=row['plan_hash'], source_snapshot=None, audio_manifest_sha256=row['audio_manifest_sha256'],
            audio_chunk_ids=[part['id'] for part in manifest['chunks']], destination=config['destination'],
            prompt_version=SCHEMA, output_schema_version=SCHEMA, price_version=config['price_version'],
            estimated_batches=1, maximum_cost_microyuan=str(config['maximum_cost_microyuan']),
            quota_units=config['units']['audio_duration_ms'], expires_at=_stamp(row['expires_at']),
            source_status=self._sources_status(row) if check else 'unchanged', contains_images=False)

    def prepare(self, class_id, request, key, price_id):
        self.serialize(class_id)
        self.classroom(class_id)
        digest = _hash(dict(class_session_id=str(class_id), request=request))
        old = self.receipt('prepareGeneration', key, digest)
        if old:
            return self.plan_view(self.get_plan(old['resource_id']))
        count = self.execute("""SELECT count(*) FROM generation_plans WHERE owner_id=:owner
            AND kind='asr_retranscribe' AND class_session_id=:classroom
            AND created_at>clock_timestamp()-interval '1 minute'""", classroom=class_id).scalar_one()
        if count >= 12:
            raise AuthRejected('RATE_LIMITED', 429)
        manifest = self.freeze(class_id, request)
        if not manifest['complete'] and not request['accept_partial_scope']:
            raise FiletransRejected('SOURCE_UNAVAILABLE', 409, 'Cloud audio has gaps. Confirm the available portion or wait for durable upload.')
        self.tx.billing._switches(KIND)
        config = self.config(price_id, manifest)
        now = self.now()
        expiry = min(now + timedelta(minutes=10),
                     min_epoch_expiry(manifest['chunks']))
        if expiry <= now:
            raise FiletransRejected('GONE', 410, 'Cloud audio in the selection has expired.')
        manifest_hash = _hash(manifest)
        plan_hash = _hash(dict(audio_manifest_sha256=manifest_hash, request=request, config=config))
        identity = uuid4()
        row = self.execute("""INSERT INTO generation_plans(id,owner_id,class_session_id,kind,plan_hash,
            request,config,expires_at,audio_manifest,audio_manifest_sha256)
            VALUES(:id,:owner,:classroom,:kind,:digest,CAST(:request AS jsonb),CAST(:config AS jsonb),
                   :expires,CAST(:manifest AS jsonb),:manifest_digest) RETURNING *""",
            id=identity, classroom=class_id, kind=KIND, digest=plan_hash, request=_json(request),
            config=_json(config), expires=expiry, manifest=_json(manifest), manifest_digest=manifest_hash).mappings().one()
        self.remember('prepareGeneration', key, digest, 'generation_plan', identity, {'id': str(identity)}, 201)
        return self.plan_view(row, check=False)

    def _approval(self, row, *, original=None):
        used = self.execute("""SELECT EXISTS(SELECT FROM filetrans_runs WHERE owner_id=:owner
                    AND class_session_id=:classroom)""", classroom=row['class_session_id']).scalar_one()
        if not used and original is None:
            return None
        approval = self.execute("""SELECT id FROM filetrans_approvals a
            WHERE a.owner_id=:owner AND a.plan_id=:plan
              AND a.original_task_id IS NOT DISTINCT FROM CAST(:original AS uuid)
              AND a.expires_at>clock_timestamp()
              AND NOT EXISTS(SELECT FROM filetrans_runs r WHERE r.approval_id=a.id)
            ORDER BY a.created_at,a.id LIMIT 1""", plan=row['id'], original=original).scalar_one_or_none()
        if approval is None:
            raise FiletransRejected('QUOTA_EXCEEDED', 409,
                'This classroom has used its Filetrans operation. An additional operation requires approval.')
        return approval

    def _reserve(self, row, principal, price_id, approval, *, key, original=None, attempt=None):
        config = row['config']
        if self.config(price_id, row['audio_manifest']) != config:
            raise AuthRejected('SOURCE_CHANGED', 409)
        class_id = row['class_session_id']
        reservation = self.tx.billing.reserve(ReservationRequest(op_id=key, purpose=KIND,
            price_id=price_id, maximum_units=config['units'], task_max_microyuan=config['maximum_cost_microyuan'],
            input_ref={'schema_version': 1, 'class_session_id': str(class_id)}, class_session_id=class_id,
            schema_version=SCHEMA, plan_id=row['id'], parent_task_id=original))
        self.execute("""INSERT INTO filetrans_runs(task_id,owner_id,class_session_id,plan_id,auth_session_id,
            device_id,security_epoch,auth_role,approval_id,original_task_id,original_attempt_id)
            VALUES(:task,:owner,:classroom,:plan,:session,:device,:epoch,:role,:approval,:original,:attempt)""",
            task=reservation['task_id'], classroom=class_id, plan=row['id'], session=principal.session_id,
            device=principal.device_id, epoch=principal.security_epoch, role=principal.role,
            approval=approval, original=original, attempt=attempt)
        return reservation['task_id']

    def execute_plan(self, row, body, key, principal, price_id):
        self.serialize(row['class_session_id'])
        row = self.get_plan(row['id'])
        digest = _hash(dict(plan_id=str(row['id']), body=body))
        old = self.receipt('executeGeneration', key, digest)
        if old:
            return self.task_view(old['resource_id'])
        if (body['plan_sha256'] != row['plan_hash']
                or body['confirmed_destination_revision'] != row['config']['destination']['configuration_revision']):
            raise AuthRejected('SOURCE_CHANGED', 409)
        if body['confirm_send'] is not True or body['confirm_images'] is not False:
            raise AuthRejected('VALIDATION_FAILED', 422)
        task = row['consumed_task_id']
        if task is None:
            self.verify(row)  # Report expired/deleted audio before a generic plan expiry.
            if row['expires_at'] <= self.now():
                raise AuthRejected('PLAN_EXPIRED', 409)
            if not row['audio_manifest']['complete'] and not body['confirm_partial_scope']:
                raise FiletransRejected('VALIDATION_FAILED', 422, 'Confirm sending only the available portion of cloud audio.')
            if not body['confirm_original_snapshot'] and self.source_changed(row):
                raise AuthRejected('SOURCE_CHANGED', 409)
            approval = self._approval(row)
            task = self._reserve(row, principal, price_id, approval, key=row['id'])
            self.execute("""UPDATE generation_plans SET consumed_task_id=:task,consumed_at=clock_timestamp()
                WHERE owner_id=:owner AND id=:id""", task=task, id=row['id'])
        self.remember('executeGeneration', key, digest, 'task', task, {'id': str(task)}, 202)
        return self.task_view(task)

    def retry(self, run, body, key, principal, price_id):
        original = run['task_id']
        self.serialize(run['class_session_id'])
        digest = _hash(dict(task_id=str(original), body=body))
        old = self.receipt('retryTask', key, digest)
        if old:
            return self.task_view(old['resource_id'])
        task = self.tx.tasks.get(original)
        attempt = self.tx.tasks._attempt(original)
        if (body['confirm_duplicate_billing'] is not True or attempt is None
                or UUID(body['original_attempt_id']) != attempt.id):
            raise AuthRejected('VALIDATION_FAILED', 422)
        if task.state != 'unknown' or attempt.provider_outcome != 'unknown':
            raise AuthRejected('REVISION_CONFLICT', 409)
        existing = self.execute("""SELECT task_id FROM filetrans_runs WHERE owner_id=:owner AND original_task_id=:id""",
                                id=original).scalar_one_or_none()
        if existing is not None:
            raise AuthRejected('REVISION_CONFLICT', 409)
        plan = self.get_plan(run['plan_id'])
        self.verify(plan)
        approval = self._approval(plan, original=original)
        task = self._reserve(plan, principal, price_id, approval, key=uuid4(), original=original, attempt=attempt.id)
        self.remember('retryTask', key, digest, 'task', task, {'id': str(task)}, 202)
        return self.task_view(task)

    def task_view(self, task_id):
        task = self.tx.tasks.get(task_id)
        attempt = self.tx.tasks._attempt(task_id)
        reservation = self.tx.billing.for_task(task_id)
        result = self.execute("""SELECT id FROM filetrans_versions WHERE owner_id=:owner AND task_id=:task""",
                              task=task_id).scalar_one_or_none()
        return dict(id=str(task.id), revision=task.revision, created_at=_stamp(task.created_at),
            updated_at=_stamp(task.updated_at), class_session_id=str(task.class_session_id), kind=KIND,
            state=task.state, attempt_id=str(attempt.id) if attempt else None,
            parent_task_id=str(task.parent_task_id) if task.parent_task_id else None,
            completed_units=int(task.state == 'succeeded'), total_units=1, error_code=task.error_code,
            result_id=str(result) if result else None, source_snapshot_id=None,
            provider_outcome=attempt.provider_outcome if attempt else 'not_submitted',
            maximum_cost_microyuan=str(reservation['reserved_microyuan']),
            settled_cost_microyuan=_decimal(reservation['settled_microyuan']), may_have_charged=task.may_have_charged)

    def publish(self, task, plan, reply):
        version = uuid4()
        self.execute("""INSERT INTO filetrans_versions(id,owner_id,class_session_id,task_id)
            VALUES(:id,:owner,:classroom,:task)""", id=version, classroom=task.class_session_id, task=task.id)
        learning = PgQaRepository(self.tx)
        for part in candidate_segments(plan['audio_manifest']['chunks'], reply.text, reply.request_id):
            segment, revision = uuid4(), uuid4()
            self.execute("""INSERT INTO filetrans_segments(id,owner_id,class_session_id,version_id,segment_id,
                start_ms,end_ms,source_chunk_ids,body,provider_request_id)
                VALUES(:revision,:owner,:classroom,:version,:segment,
                :start_ms,:end_ms,:source_chunk_ids,:body,:provider_request_id)""",
                revision=revision, classroom=task.class_session_id, version=version, segment=segment, **part)
            learning.add_source(task.class_session_id, kind='transcript_segment', object_id=segment,
                text=part['body'], start_ms=part['start_ms'], end_ms=part['end_ms'],
                origin='retranscription', asr_identity=(segment, revision))
        return version


def min_epoch_expiry(parts):
    return min(datetime.fromisoformat(part['expires_at']) for part in parts)


def _decimal(value):
    return str(value) if value is not None else None
