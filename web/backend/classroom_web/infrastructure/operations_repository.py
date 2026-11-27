"""Read live sources and write minimized operation receipts under owner RLS."""
import base64
import hashlib
import hmac
import json
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ..domain.auth import AuthRejected
from .auth_deadlines import remaining
from .task_sources import PgTaskSources


SEARCH = """
WITH live_classes AS (
    SELECT s.* FROM class_sessions s
    WHERE s.owner_id=:owner AND classroom_web_qa_class_live(s.id)
      AND (CAST(:course AS uuid) IS NULL OR s.course_id=:course)
      AND (CAST(:classroom AS uuid) IS NULL OR s.id=:classroom)
), matched AS (
    SELECT 'course' AS kind, c.id, NULL::uuid AS classroom, c.name AS title,
           c.name AS source, NULL::uuid AS revision, 'course/'||c.id AS locator
    FROM courses c JOIN semesters s ON (s.owner_id,s.id)=(c.owner_id,c.semester_id)
    WHERE :courses AND c.owner_id=:owner AND c.deleted_at IS NULL AND s.deleted_at IS NULL
      AND (CAST(:course AS uuid) IS NULL OR c.id=:course)
      AND (CAST(:classroom AS uuid) IS NULL OR EXISTS (SELECT FROM live_classes l WHERE l.course_id=c.id))
      AND NOT EXISTS (SELECT FROM deletion_tombstones t WHERE t.owner_id=c.owner_id
          AND ((t.resource_type='course' AND t.resource_id=c.id)
            OR (t.resource_type='semester' AND t.resource_id=s.id)
            OR (t.resource_type='account' AND t.resource_id=c.owner_id)))
      AND lower(c.name) LIKE lower(:pattern) ESCAPE '!'
    UNION ALL
    SELECT 'class_session', s.id, s.id, s.title, s.title, NULL, 'classroom/'||s.id
    FROM live_classes s JOIN class_sessions c ON (c.owner_id,c.id)=(s.owner_id,s.id)
    WHERE :classrooms AND c.deleted_at IS NULL AND lower(c.title) LIKE lower(:pattern) ESCAPE '!'
    UNION ALL
    SELECT 'transcript_segment', f.segment_id, s.id, s.title, f.body, f.id,
           'transcript/'||s.id||'/'||a.version_id||'/'||f.segment_id
    FROM live_classes s JOIN asr_streams a ON (a.owner_id,a.class_session_id)=(s.owner_id,s.id)
      JOIN asr_final_segments f ON (f.owner_id,f.stream_id)=(a.owner_id,a.id)
    WHERE :transcripts AND (s.adopted_transcript_version_id IS NULL OR s.adopted_transcript_version_id=a.version_id)
      AND lower(f.body) LIKE lower(:pattern) ESCAPE '!'
    UNION ALL
    SELECT 'transcript_segment', f.segment_id, s.id, s.title, f.body, f.id,
           'transcript/'||s.id||'/'||f.version_id||'/'||f.segment_id
    FROM live_classes s JOIN filetrans_segments f
      ON (f.owner_id,f.class_session_id,f.version_id)=(s.owner_id,s.id,s.adopted_transcript_version_id)
    WHERE :transcripts AND lower(f.body) LIKE lower(:pattern) ESCAPE '!'
    UNION ALL
    SELECT CASE WHEN d.kind='card' THEN 'card' ELSE 'review_document' END,
           d.id, s.id, d.title, hit.value,
           r.id, 'review/'||d.id||'/'||r.id
    FROM live_classes s JOIN review_documents d ON (d.owner_id,d.class_session_id)=(s.owner_id,s.id)
      JOIN review_revisions r ON (r.owner_id,r.document_id)=(d.owner_id,d.id)
      CROSS JOIN LATERAL (
        SELECT value FROM (
          VALUES (0,0,0,r.body_markdown), (0,0,1,r.personal_note), (0,0,2,d.title)
          UNION ALL
          SELECT section.rank, entry.ordinal, field.rank, field.value #>> '{}'
          FROM (VALUES (1,r.structured_content->'notes'), (2,r.structured_content->'cards')) section(rank,items)
            CROSS JOIN LATERAL jsonb_array_elements(
              CASE WHEN jsonb_typeof(section.items)='array' THEN section.items ELSE '[]'::jsonb END
            ) WITH ORDINALITY entry(item,ordinal)
            CROSS JOIN LATERAL (
              VALUES (0,entry.item->'title'),
                     (1,CASE section.rank WHEN 1 THEN entry.item->'body' ELSE entry.item->'front' END),
                     (2,CASE WHEN section.rank=2 THEN entry.item->'back' END),
                     (3,entry.item->'supplement'),
                     (4,CASE WHEN section.rank=2 THEN entry.item->'english' END),
                     (5,CASE WHEN section.rank=2 AND entry.item->>'english'<>'' THEN entry.item->'english_meaning' END)
              UNION ALL
              SELECT 5+ordinal, value FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(entry.item->'missing')='array' THEN entry.item->'missing' ELSE '[]'::jsonb END
              ) WITH ORDINALITY missing(value,ordinal)
            ) field(rank,value)
          WHERE jsonb_typeof(field.value)='string'
        ) AS candidates(group_rank,item_rank,field_rank,value)
        WHERE lower(value) LIKE lower(:pattern) ESCAPE '!'
        ORDER BY group_rank,item_rank,field_rank LIMIT 1
      ) hit
    WHERE :reviews
      AND (r.id=d.adopted_revision_id OR (r.id=d.latest_personal_revision_id AND r.kind='personal'))
)
SELECT kind, id, classroom, title, revision, locator,
       substring(source FROM greatest(1,strpos(lower(source),lower(:query))-60) FOR 300) AS snippet
FROM matched
WHERE (kind,id,coalesce(revision,CAST(:zero AS uuid))) > (:kind,:id,:revision)
ORDER BY kind,id,coalesce(revision,CAST(:zero AS uuid)) LIMIT :limit
"""

NOTIFICATION_FIELDS = 'id,kind,severity,object_id,read_at,resolved_at,created_at,message_code'
ZERO = UUID(int=0)


class PgOperationsRepository:
    def __init__(self, tx):
        self.tx = tx
        self.owner = tx.catalog.owner_id

    def _execute(self, sql, **params):
        return self.tx.connection.execute(text(sql), {'owner': self.owner, **params})

    def _signature(self, scope, anchor):
        secret = self.tx.events.secret
        if secret is None:
            raise AuthRejected('SERVICE_DISABLED', 503)
        binding = json.dumps([str(self.owner), scope], ensure_ascii=True, separators=(',', ':')).encode()
        return hmac.digest(secret, b'pr11c-page-v1\0' + binding + b'\0' + anchor, hashlib.sha256)

    def _cursor(self, scope, *anchor):
        payload = '|'.join(str(part) for part in anchor).encode('ascii')
        return base64.urlsafe_b64encode(payload + self._signature(scope, payload)).decode('ascii').rstrip('=')

    def _anchor(self, cursor, scope):
        try:
            if not cursor or len(cursor) > 512:
                raise ValueError()
            raw = base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True)
            payload, signature = raw[:-32], raw[-32:]
            canonical = base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')
            if cursor != canonical or not hmac.compare_digest(signature, self._signature(scope, payload)):
                raise ValueError()
            return payload.decode('ascii').split('|')
        except (ValueError, UnicodeError):
            raise AuthRejected('VALIDATION_FAILED', 422) from None

    def search(self, params, readers):
        query = params['q'].strip()
        if not query or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in query):
            raise AuthRejected('VALIDATION_FAILED', 422)
        course = UUID(params['course_id']) if 'course_id' in params else None
        classroom = UUID(params['class_session_id']) if 'class_session_id' in params else None
        limit = params.get('limit', 30)
        scope = ['search', query, str(course), str(classroom), limit]
        kind, last, revision = '', ZERO, ZERO
        if 'cursor' in params:
            kind, last, revision = self._anchor(params['cursor'], scope)
            last, revision = UUID(last), UUID(revision)
        if course:
            self.tx.catalog.get_course(course)
        if classroom:
            PgTaskSources(self.tx.connection, self.owner).authorize(classroom, {'schema_version': 1})
        if len(query) < 3:
            left = remaining()
            ms = 1000 if left is None else max(1, min(1000, int(left * 1000)))
            self._execute("SELECT set_config('statement_timeout',:ms,true)", ms=str(ms))
        pattern = '%' + query.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
        try:
            rows = self._execute(SEARCH, query=query, pattern=pattern, course=course, classroom=classroom,
                                 kind=kind, id=last, revision=revision, zero=ZERO, limit=limit + 1,
                                 **readers).mappings().all()
        except DBAPIError as error:
            if getattr(error.orig, 'sqlstate', None) != '57014':
                raise
            raise AuthRejected('RATE_LIMITED', 429) from None
        items = [dict(object_type=r['kind'], object_id=str(r['id']),
                      class_session_id=str(r['classroom']) if r['classroom'] else None,
                      revision_id=str(r['revision']) if r['revision'] else None,
                      title=r['title'], snippet=r['snippet'], locator=r['locator']) for r in rows[:limit]]
        cursor = None
        if len(rows) > limit:
            row = rows[limit - 1]
            cursor = self._cursor(scope, row['kind'], row['id'], row['revision'] or ZERO)
        return {'items': items, 'next_cursor': cursor}

    @staticmethod
    def _notification(row):
        return dict(row, id=str(row['id']), object_id=str(row['object_id']) if row['object_id'] else None,
                    **{name: row[name].isoformat() if row[name] else None
                       for name in ('read_at', 'resolved_at', 'created_at')})

    def notifications(self, params):
        limit = params.get('limit', 30)
        scope = ['notifications', limit]
        after, identity = None, None
        if 'cursor' in params:
            stamp, identity = self._anchor(params['cursor'], scope)
            after, identity = datetime.fromisoformat(stamp), UUID(identity)
        rows = self._execute(f"""SELECT {NOTIFICATION_FIELDS} FROM notifications
            WHERE owner_id=:owner AND (CAST(:after AS timestamptz) IS NULL OR (created_at,id)<(:after,:id))
            ORDER BY created_at DESC,id DESC LIMIT :limit""",
            after=after, id=identity, limit=limit + 1).mappings().all()
        cursor = self._cursor(scope, rows[limit - 1]['created_at'].isoformat(), rows[limit - 1]['id']) \
            if len(rows) > limit else None
        return {'items': [self._notification(row) for row in rows[:limit]], 'next_cursor': cursor}

    def visible_notification_events(self, events):
        # Advance the underlying cursor through all events, even when the source
        # of an older notification no longer passes the same RLS as the list.
        notices = {event['event_id']: UUID(event['data']['notification_id'])
                   for event in events if event['type'] == 'notification.created'}
        if not notices:
            return []
        visible = set(self._execute("""SELECT id FROM notifications
            WHERE owner_id=:owner AND id=ANY(:ids)""", ids=list(notices.values())).scalars())
        return [event for event in events if notices.get(event['event_id']) in visible]

    def _remember(self, operation, key, digest, kind, target, status):
        self._execute("""INSERT INTO operation_receipts
            (id,owner_id,operation,op_id,request_hash,resource_type,resource_id,response_status,response_body)
            VALUES(:id,:owner,:operation,:key,:digest,:kind,:target,:status,'{}')""",
            id=uuid4(), operation=operation, key=key, digest=digest, kind=kind, target=target, status=status)

    def read_notification(self, target, key, digest):
        receipt = self.tx.receipts.lock_and_get('readNotification', key, digest)
        row = self._execute(f"""UPDATE notifications SET read_at=coalesce(read_at,clock_timestamp())
            WHERE owner_id=:owner AND id=:id RETURNING {NOTIFICATION_FIELDS}""", id=target).mappings().one_or_none()
        if row is None:
            raise AuthRejected('NOT_FOUND', 404)
        if receipt is None:
            self._remember('readNotification', key, digest, 'notification', target, 200)
        return self._notification(row)

    def feedback(self, body, key, digest):
        receipt = self.tx.receipts.lock_and_get('submitFeedback', key, digest)
        if receipt is not None:
            return {'accepted': True, 'request_id': str(receipt['resource_id'])}
        # Serialize the owner's rate check and successful insert across workers.
        self._execute("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:owner AS text)||:scope,0))",
                      scope=':feedback')
        count = self._execute("""SELECT count(*) FROM operation_receipts
            WHERE owner_id=:owner AND operation='submitFeedback'
              AND created_at>clock_timestamp()-interval '10 minutes'""").scalar_one()
        if count >= 10:
            raise AuthRejected('RATE_LIMITED', 429)
        target = uuid4()
        self._execute("""INSERT INTO feedback_reports
            (id,owner_id,category,build_id,description,request_id,consent,confirmed_excerpt)
            VALUES(:id,:owner,:category,:build_id,:description,:request_id,:consent,:excerpt)""",
            id=target, category=body['category'], build_id=body['build_id'], description=body['description'],
            request_id=UUID(body['request_id']) if body['request_id'] else None,
            consent=body['confirm_send_excerpt'],
            excerpt=(body['confirmed_excerpt'] or None) if body['confirm_send_excerpt'] else None)
        self._remember('submitFeedback', key, digest, 'feedback_report', target, 202)
        return {'accepted': True, 'request_id': str(target)}
