"""Owner-scoped capture state using the existing authorized unit of work."""
import json
from uuid import uuid4

from ..domain.auth import AuthRejected
from ..domain.data import Resource
from .media_repository import PgMediaRepository


class PgCaptureRepository(PgMediaRepository):
    def scope(self, classroom):
        self.tx.catalog._load(Resource.CLASS_SESSION, classroom, share=True)
        if self.execute("""SELECT EXISTS(SELECT FROM deletion_requests WHERE owner_id=:owner
                AND scope_type='audio' AND scope_id=:classroom)""", classroom=classroom).scalar_one():
            raise AuthRejected('GONE', 410)
        policy = self.execute("""SELECT p.*,r.max_lifetime,r.synthetic FROM capture_policy p
            LEFT JOIN retention_policies r ON r.version=p.retention_policy_version
            WHERE p.id FOR SHARE OF p""").mappings().one()
        if not policy['enabled'] or policy['max_lifetime'] is None:
            raise AuthRejected('SERVICE_DISABLED', 503)
        self.execute("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:owner AS text)||'capture',0))")
        self.lease()
        return policy

    def lease(self):
        return self.execute('SELECT * FROM capture_leases WHERE owner_id=:owner FOR UPDATE').mappings().one_or_none()

    def epoch(self, epoch_id, classroom):
        row = self.execute("""SELECT e.*,s.hard_expire_at,s.retention_policy_version,s.manifest_sha256,
            s.microphone_consent_id,s.recovery_consent_id,s.asr_consent_id,
            s.last_local_seq,s.last_sample,s.last_monotonic_ms
            FROM capture_epochs e JOIN capture_epoch_state s ON (s.owner_id,s.epoch_id)=(e.owner_id,e.id)
            WHERE e.owner_id=:owner AND e.id=:id AND e.class_session_id=:classroom
            FOR UPDATE OF e,s""", id=epoch_id, classroom=classroom).mappings().one_or_none()
        if row is None:
            raise AuthRejected('NOT_FOUND', 404)
        if row['hard_expire_at'] <= self.now():
            raise AuthRejected('GONE', 410)
        return row

    def consents(self, epoch, policy):
        for kind in ('microphone', 'recovery', 'asr'):
            row = self.execute('SELECT * FROM consent_records WHERE owner_id=:owner AND id=:id FOR SHARE',
                               id=epoch[kind + '_consent_id']).mappings().one_or_none()
            if (row is None or row['revoked_at'] is not None or row['kind'] != kind
                    or row['device_id'] != epoch['device_id']
                    or row['class_session_id'] != epoch['class_session_id']
                    or row['scope'] != {'capture_epoch_id': str(epoch['id'])}
                    or row['policy_version'] != policy[kind + '_consent_version']):
                raise AuthRejected('CONSENT_REQUIRED', 403)

    def chunk(self, chunk_id):
        row = self.execute("""SELECT c.*,s.receipt_id,s.recovery_grant_id FROM audio_chunks c
            JOIN audio_chunk_state s ON (s.owner_id,s.chunk_id)=(c.owner_id,c.id)
            WHERE c.owner_id=:owner AND c.id=:id FOR UPDATE OF c,s""", id=chunk_id).mappings().one_or_none()
        if row is None:
            raise AuthRejected('NOT_FOUND', 404)
        return row

    def event(self, event_type, epoch, data, aggregate_id):
        self.execute("""INSERT INTO owner_event_counters(id,owner_id,next_sequence) VALUES(:id,:owner,1)
            ON CONFLICT(owner_id) DO NOTHING""", id=uuid4())
        sequence = self.execute("""UPDATE owner_event_counters SET next_sequence=next_sequence+1
            WHERE owner_id=:owner RETURNING next_sequence-1""").scalar_one()
        self.execute("""INSERT INTO outbox_events(id,owner_id,event_sequence,event_type,aggregate_type,
            aggregate_id,aggregate_revision,payload,schema_version)
            VALUES(:id,:owner,:sequence,:type,'class_session',:aggregate,1,CAST(:data AS jsonb),'1')""",
            id=uuid4(), sequence=sequence, type=event_type, aggregate=aggregate_id, data=json.dumps(data))

    def gap(self, epoch, kind, reason, *, start=None, end=None, wall_start=None, wall_end=None):
        self.execute("""INSERT INTO capture_gaps(id,owner_id,class_session_id,capture_epoch_id,
            start_sample,end_sample,wall_start,wall_end,kind,reason_code)
            VALUES(:id,:owner,:classroom,:epoch,:start,:end,:wall_start,:wall_end,:kind,:reason)""",
            id=uuid4(), classroom=epoch['class_session_id'], epoch=epoch['id'], start=start, end=end,
            wall_start=wall_start, wall_end=wall_end, kind=kind, reason=reason)
