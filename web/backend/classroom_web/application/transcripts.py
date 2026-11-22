"""Authenticated transcript reads using the original signed cursor format."""
import hmac
from uuid import UUID

from ..domain.auth import AuthRejected
from ..domain.tasks import ResyncRequired
from ..infrastructure.qa_repository import _stamp
from ..infrastructure.transcript_repository import PgTranscriptRepository
from .media import identifier


def pagination_cursor(tx, cursor, target, kind, *, last=None):
    if cursor is None and last is None:
        return UUID(int=0)
    if tx.events.secret is None:
        raise AuthRejected('SERVICE_DISABLED', 503)
    value = last.hex if last is not None else cursor[:32]
    if not value.isascii():
        raise ResyncRequired('invalid transcript cursor')
    proof = hmac.digest(tx.events.secret,
        f'asr:{tx.catalog.owner_id}:{target}:{kind}:{value}'.encode('ascii'), 'sha256').hex()
    if last is not None:
        return value + proof
    if len(cursor) != 96 or not cursor.isascii() or not hmac.compare_digest(cursor[32:], proof):
        raise ResyncRequired('invalid transcript cursor')
    return UUID(hex=value)


def read_transcripts(tx, operation, params):
    limit = params.get('limit', 100)
    repo = PgTranscriptRepository(tx)
    if operation == 'listTranscriptVersions':
        target = identifier(params['class_session_id'])
        classroom = repo.classroom(target)
        kind = 'version'
        after = pagination_cursor(tx, params.get('cursor'), target, kind)
        rows = repo.versions(target, after, limit+1)
        items = [dict(id=str(row['id']), revision=1, created_at=_stamp(row['created_at']),
            updated_at=_stamp(row['updated_at']), class_session_id=str(target), kind=row['kind'],
            state=row['state'], source_task_id=str(row['task_id']),
            adopted=row['id'] == classroom.adopted_transcript_version_id) for row in rows[:limit]]
    else:
        target = identifier(params['version_id'])
        kind = 'segment'
        after = pagination_cursor(tx, params.get('cursor'), target, kind)
        rows, items = repo.segments(target, after, limit+1)
    cursor = pagination_cursor(tx, None, target, kind, last=rows[limit-1]['id']) if len(rows) > limit else None
    return {'items': items[:limit], 'next_cursor': cursor}
