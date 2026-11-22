"""The existing version/segment contract over realtime and file candidates."""
from uuid import UUID

from ..domain.auth import AuthRejected
from ..domain.data import Resource, RevisionConflict
from .asr_repository import PgAsrRepository
from .qa_repository import _hash, _stamp
from .task_sources import PgTaskSources


VERSION_ROWS = """SELECT s.version_id AS id,s.class_session_id,s.task_id,s.created_at,s.updated_at,
        'realtime' AS kind,
        CASE WHEN s.state IN ('failed','succeeded','unknown') THEN 'sealed' ELSE 'open' END AS state
    FROM asr_streams s
    WHERE s.owner_id=:owner AND s.class_session_id=:target
      AND EXISTS(SELECT FROM asr_final_segments f WHERE (f.owner_id,f.stream_id)=(s.owner_id,s.id))
    UNION ALL SELECT v.id,v.class_session_id,v.task_id,v.created_at,v.created_at,'retranscription','sealed'
        FROM filetrans_versions v WHERE v.owner_id=:owner AND v.class_session_id=:target"""


class PgTranscriptRepository(PgAsrRepository):
    def __init__(self, tx):
        super().__init__(tx, tx.catalog.owner_id)

    def classroom(self, identity, *, write=False):
        PgTaskSources(self.connection, self.owner_id).authorize(identity, {'schema_version': 1}, write=write)
        return self.tx.catalog.get_class_session(identity)

    def versions(self, classroom, after, limit):
        return self.execute(f"""SELECT * FROM ({VERSION_ROWS}) versions
            WHERE id>:after ORDER BY id LIMIT :limit""", target=classroom, after=after, limit=limit).mappings().all()

    def version(self, classroom, version_id):
        return self.execute(f"""SELECT * FROM ({VERSION_ROWS}) versions WHERE id=:version""",
                            target=classroom, version=version_id).mappings().one_or_none()

    def segments(self, version_id, after, limit):
        version = self.execute("""SELECT class_session_id,'retranscription' AS kind,NULL::uuid AS stream_id
             FROM filetrans_versions WHERE owner_id=:owner AND id=:version
             UNION ALL SELECT class_session_id,'live_asr',id FROM asr_streams
             WHERE owner_id=:owner AND version_id=:version""", version=version_id).mappings().one_or_none()
        if version is None:
            raise AuthRejected('NOT_FOUND', 404)
        self.classroom(version['class_session_id'])
        if version['kind'] == 'live_asr':
            source = 'SELECT * FROM asr_final_segments WHERE owner_id=:owner AND stream_id=:target'
            target = version['stream_id']
        else:
            source = 'SELECT f.*,1 AS revision FROM filetrans_segments f WHERE owner_id=:owner AND version_id=:target'
            target = version_id
        rows = self.execute(source + ' AND id>:after ORDER BY id LIMIT :limit',
                            target=target, after=after, limit=limit).mappings().all()
        items = [dict(id=str(row['segment_id']), revision=row['revision'], created_at=_stamp(row['created_at']),
            updated_at=_stamp(row['created_at']), class_session_id=str(version['class_session_id']),
            transcript_version_id=str(version_id), stream_id=_id(version['stream_id']),
            segment_revision_id=str(row['id']), kind='machine', origin=version['kind'], status='final',
            text=row['body'], start_ms=row['start_ms'], end_ms=row['end_ms'],
            timestamp_source='provider_segment' if version['stream_id'] else 'capture_mapping',
            provider_request_id=row['provider_request_id'], source_chunk_ids=[str(item) for item in row['source_chunk_ids']],
            adopted_revision_id=None) for row in rows]
        return rows, items

    def adopt(self, classroom_id, body, key):
        classroom = self.classroom(classroom_id, write=True)
        digest = _hash(dict(class_session_id=str(classroom_id), body=body))
        old = self.receipt('adoptTranscriptVersion', key, digest)
        if old:
            return old['response_body']
        version = self.version(classroom_id, UUID(body['transcript_version_id']))
        if version is None:
            raise AuthRejected('NOT_FOUND', 404)
        if version['state'] != 'sealed':
            raise RevisionConflict(classroom.revision)
        classroom = self.tx.catalog._cas(Resource.CLASS_SESSION, classroom_id, body['base_revision'],
                                        adopted_transcript_version_id=version['id'])
        dto = self.class_view(classroom)
        self.remember('adoptTranscriptVersion', key, digest, 'class_session', classroom_id, dto, 200)
        return dto

    def class_view(self, classroom):
        policy = self.tx.catalog.get_material(classroom.course_material_version_id)
        stats = self.execute("""SELECT min(e.wall_started_at) AS started,max(e.wall_ended_at) AS ended,
                max(s.hard_expire_at) AS expiry,sum(coalesce(e.end_sample,s.last_sample)) AS samples,
                bool_or(e.status='recording') AS recording,bool_or(e.status='paused') AS paused
            FROM capture_epochs e JOIN capture_epoch_state s ON (s.owner_id,s.epoch_id)=(e.owner_id,e.id)
            WHERE e.owner_id=:owner AND e.class_session_id=:id""", id=classroom.id).mappings().one()
        gaps = self.execute("""SELECT count(*) FILTER(WHERE kind='known_missing') AS known,
                    count(*) FILTER(WHERE kind='unknown') AS unknown
                FROM capture_gaps WHERE owner_id=:owner AND class_session_id=:id""", id=classroom.id).mappings().one()
        upload = self.execute("""SELECT CASE WHEN count(*)=0 THEN 'pending'
                WHEN bool_and(a.status='committed') THEN 'durable'
                WHEN bool_or(a.status IN ('staging','verified')) THEN 'uploading' ELSE 'failed' END
            FROM audio_chunks c JOIN media_assets a ON (a.owner_id,a.id)=(c.owner_id,c.asset_id)
            WHERE c.owner_id=:owner AND c.class_session_id=:id""", id=classroom.id).scalar_one()
        stream = self.execute("""SELECT state FROM asr_streams WHERE owner_id=:owner AND class_session_id=:id
                ORDER BY created_at DESC,id DESC LIMIT 1""", id=classroom.id).scalar_one_or_none()
        ai = self.execute("""SELECT state FROM tasks WHERE owner_id=:owner AND class_session_id=:id
                AND kind IN ('ai_question','ai_explanation','review_summary')
                ORDER BY created_at DESC,id DESC LIMIT 1""", id=classroom.id).scalar_one_or_none()
        capture = ('recording' if stats['recording'] else 'paused' if stats['paused']
                   else 'ended' if stats['ended'] is not None else 'idle')
        return dict(id=str(classroom.id), revision=classroom.revision, created_at=_stamp(classroom.created_at),
            updated_at=_stamp(classroom.updated_at), course_id=str(classroom.course_id), title=classroom.title,
            language=policy.language, started_at=_stamp(stats['started'] or classroom.started_at),
            capture_ended_at=_stamp(stats['ended'] or classroom.ended_at) if capture == 'ended' else None,
            audio_expires_at=_stamp(stats['expiry'] or classroom.audio_expires_at),
            adopted_transcript_version_id=_id(classroom.adopted_transcript_version_id),
            flows=dict(capture=capture, upload=upload, asr=stream or 'idle', ai=ai or 'idle', sync='synced'),
            effective_capture_ms=int(stats['samples'] or 0)//16,
            known_gap_count=gaps['known'], unknown_gap_count=gaps['unknown'], deleted_at=None)


def _id(value):
    return str(value) if value is not None else None
