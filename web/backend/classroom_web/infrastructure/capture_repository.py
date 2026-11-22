"""Owner-scoped capture state using the existing authorized unit of work."""
import base64
from datetime import timedelta
import hmac
import json
from uuid import UUID, uuid4

from ..domain.auth import AuthRejected
from ..domain.capture import DurableCaptureChunk
from ..domain.data import Resource
from .media_repository import PgMediaRepository


MANIFEST_CURSOR_LIFETIME = timedelta(minutes=15)


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

    def _manifest_signature(self, classroom, payload):
        key = self.tx.events.secret
        if key is None:
            raise AuthRejected('SERVICE_DISABLED', 503)
        message = f'listAudioManifests:v1:{self.owner_id}:{classroom}:{payload}'
        return hmac.digest(key, message.encode('ascii'), 'sha256').hex()

    def manifest_continuation(self, classroom, cursor):
        if cursor is None:
            return None
        payload, _, signature = cursor.rpartition('.')
        if (not 1 <= len(cursor) <= 512 or not cursor.isascii()
                or not hmac.compare_digest(signature, self._manifest_signature(classroom, payload))):
            raise AuthRejected('VALIDATION_FAILED', 422)
        try:
            position = base64.urlsafe_b64decode(payload).decode('ascii')
            ceiling, offset, fence, identity, expiry = position.split('.')
            return int(ceiling), int(offset), int(fence), UUID(identity), int(expiry)
        except ValueError:
            raise AuthRejected('VALIDATION_FAILED', 422) from None

    def manifest_epoch_page(self, classroom, limit, continuation):
        now = self.now()
        if continuation is None:
            # scope() holds the same owner lock as acquire/resume/takeover. Every
            # later epoch has a greater fence, even when timeline offsets tie.
            ceiling = self.execute("""SELECT coalesce(max(fence_token),0) FROM capture_epochs
                WHERE owner_id=:owner AND class_session_id=:classroom""", classroom=classroom).scalar_one()
            offset, fence, identity = -1, 0, UUID(int=0)
            expiry = int((now + MANIFEST_CURSOR_LIFETIME).timestamp())
        else:
            ceiling, offset, fence, identity, expiry = continuation
            if expiry <= now.timestamp():
                raise AuthRejected('CURSOR_EXPIRED', 409)
        rows = self.execute("""SELECT id,timeline_offset_ms,fence_token FROM capture_epochs
            WHERE owner_id=:owner AND class_session_id=:classroom AND fence_token<=:ceiling
            AND (timeline_offset_ms,fence_token,id)>(:offset,:fence,:identity)
            ORDER BY timeline_offset_ms,fence_token,id LIMIT :count""", classroom=classroom,
            ceiling=ceiling, offset=offset, fence=fence, identity=identity, count=limit + 1).mappings().all()
        cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            last = rows[-1]
            position = f"{ceiling}.{last['timeline_offset_ms']}.{last['fence_token']}.{last['id'].hex}.{expiry}"
            payload = base64.urlsafe_b64encode(position.encode('ascii')).decode('ascii')
            cursor = f'{payload}.{self._manifest_signature(classroom, payload)}'
        return [row['id'] for row in rows], cursor

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

    def asr_submission(self, chunk_id):
        state = self.execute("""SELECT CASE
            WHEN bool_or(s.state='unknown' OR NOT d.handed_off) THEN 'unknown'
            WHEN range_agg(int8range(d.start_sample,d.end_sample)) FILTER (WHERE s.state='succeeded')
                @> int8range(min(c.start_sample),max(c.end_sample)) THEN 'completed'
            WHEN count(d.id)>0 THEN 'submitted' ELSE 'not_submitted' END
            FROM public.asr_dispatches d
            JOIN public.asr_streams s ON (s.owner_id,s.id)=(d.owner_id,d.stream_id)
            JOIN public.audio_chunks c ON (c.owner_id,c.id)=(d.owner_id,d.chunk_id)
            WHERE d.owner_id=:owner AND d.chunk_id=:id""", id=chunk_id).scalar_one()
        return state

    def unknown_asr_ranges(self, epoch_id):
        rows = self.execute("""SELECT lower(span) AS start_sample, upper(span) AS end_sample
            FROM unnest((SELECT range_agg(int8range(d.start_sample,d.end_sample))
                FROM public.asr_dispatches d JOIN public.asr_streams s ON (s.owner_id,s.id)=(d.owner_id,d.stream_id)
                WHERE d.owner_id=:owner AND d.capture_epoch_id=:epoch AND (s.state='unknown' OR NOT d.handed_off)
            )) AS ranges(span) ORDER BY start_sample""", epoch=epoch_id).mappings()
        return [dict(row) for row in rows]

    def asr_source(self, chunk, epoch):
        if (chunk['capture_epoch_id'], chunk['class_session_id']) != (epoch['id'], epoch['class_session_id']):
            raise AuthRejected('NOT_FOUND', 404)
        asset = self.asset(chunk['asset_id'], available=True)
        if chunk['recovery_grant_id']:
            grant = self.execute("""SELECT * FROM capture_recovery_grants
                WHERE owner_id=:owner AND id=:id AND epoch_id=:epoch""",
                id=chunk['recovery_grant_id'], epoch=epoch['id']).mappings().one_or_none()
            if (grant is None or grant['manifest_sha256'] != epoch['manifest_sha256']
                    or chunk['seq'] > grant['last_seq'] or chunk['end_sample'] > grant['last_sample']):
                raise AuthRejected('SOURCE_UNAVAILABLE', 409)
        source = DurableCaptureChunk(str(chunk['id']), str(epoch['class_session_id']), str(epoch['id']),
            chunk['seq'], chunk['start_sample'], chunk['end_sample'], chunk['sha256'], str(chunk['asset_id']),
            'recovery' if chunk['recovery_grant_id'] else chunk['ingest_mode'], str(chunk['receipt_id']))
        return source, asset

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
