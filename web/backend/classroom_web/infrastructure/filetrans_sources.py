"""Freeze and reauthorize server-owned capture slices for offline Filetrans."""
from datetime import timedelta
from uuid import UUID

from ..application.media import stored
from ..domain.auth import AuthRejected
from ..domain.data import Resource
from ..domain.filetrans import MAX_CHUNKS, MAX_SEGMENTS, selected_groups
from .capture_repository import PgCaptureRepository
from .qa_repository import _hash
from .task_sources import PgTaskSources


class FiletransRejected(AuthRejected):
    def __init__(self, code, status, message):
        super().__init__(code, status)
        self.public_message = message


class PgFiletransSources(PgCaptureRepository):
    def __init__(self, tx):
        super().__init__(tx, tx.catalog.owner_id)

    def classroom(self, class_id, *, audio=True):
        PgTaskSources(self.connection, self.owner_id).authorize(class_id, {'schema_version': 1})
        classroom = self.tx.catalog._load(Resource.CLASS_SESSION, class_id, share=True)
        if audio and self.execute("""SELECT EXISTS(SELECT FROM deletion_requests
                WHERE owner_id=:owner AND scope_type='audio' AND scope_id=:classroom)""",
                classroom=class_id).scalar_one():
            raise FiletransRejected('GONE', 410, 'Cloud audio for this classroom was deleted.')
        return classroom

    def policy(self, class_id):
        self.classroom(class_id)
        policy = self.scope(class_id)
        if not policy['synthetic']:
            raise AuthRejected('SERVICE_DISABLED', 503)
        return policy

    @staticmethod
    def window(request):
        scope = request['scope']
        start, end = request.get('start_ms'), request.get('end_ms')
        if (request.get('seen_sources') != [] or request.get('include_images') is not False
                or any(name in request for name in ('question', 'card_types', 'english_answer', 'anchor_ms'))):
            raise AuthRejected('VALIDATION_FAILED', 422)
        if scope == 'all' and start is None and end is None:
            return None
        if scope == 'range' and isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= 2**53-1:
            return start * 16, end * 16
        raise FiletransRejected('VALIDATION_FAILED', 422, 'Select all cloud audio or an explicit audio range.')

    def epochs(self, class_id):
        rows = self.execute("""SELECT e.*,s.manifest_sha256,s.hard_expire_at,
                s.microphone_consent_id,s.recovery_consent_id,s.asr_consent_id
            FROM capture_epochs e JOIN capture_epoch_state s ON (s.owner_id,s.epoch_id)=(e.owner_id,e.id)
            WHERE e.owner_id=:owner AND e.class_session_id=:classroom
            ORDER BY e.timeline_offset_ms,e.created_at,e.id LIMIT :limit FOR SHARE OF e,s""",
            classroom=class_id, limit=MAX_CHUNKS+1).mappings().all()
        if len(rows) > MAX_CHUNKS:
            raise AuthRejected('CAPACITY_EXCEEDED', 409)
        return rows

    def rows(self, class_id, ids=None):
        return self.execute("""SELECT a.*,c.id AS chunk_id,c.capture_epoch_id,c.seq,
                c.start_sample,c.end_sample,c.sha256 AS chunk_sha256,c.ingest_mode,
                cs.receipt_id,cs.recovery_grant_id,
                g.epoch_id AS granted_epoch,g.manifest_sha256 AS granted_manifest,g.last_seq,g.last_sample
            FROM audio_chunks c JOIN audio_chunk_state cs ON (cs.owner_id,cs.chunk_id)=(c.owner_id,c.id)
            JOIN media_assets a ON (a.owner_id,a.id)=(c.owner_id,c.asset_id)
            LEFT JOIN capture_recovery_grants g ON (g.owner_id,g.id)=(cs.owner_id,cs.recovery_grant_id)
            WHERE c.owner_id=:owner AND c.class_session_id=:classroom AND a.class_session_id=:classroom
                AND (:all_chunks OR c.id=ANY(CAST(:ids AS uuid[])))
            ORDER BY a.id LIMIT :limit FOR SHARE OF a,c,cs""", classroom=class_id,
            all_chunks=ids is None, ids=ids or [], limit=MAX_CHUNKS+1).mappings().all()

    @staticmethod
    def record(row, epoch, first, last, now):
        if row['status'] in ('delete_pending', 'delete_failed', 'deleted') or row['deleted_at'] or row['access_revoked_at']:
            raise FiletransRejected('GONE', 410, 'Cloud audio in the selection was deleted.')
        if row['status'] != 'committed' or row['confirmed_at'] is None:
            return None
        if (row['kind'] != 'audio_chunk' or row['sha256'] != row['chunk_sha256']
                or row['size_bytes'] != 2 * (row['end_sample'] - row['start_sample'])
                or row['confirmed_at'] is None):
            raise AuthRejected('SOURCE_UNAVAILABLE', 409)
        expiry = min(value for value in (row['expires_at'], row['hard_expire_at'],
            epoch['hard_expire_at'], row['confirmed_at'] + timedelta(days=7)) if value is not None)
        if expiry <= now:
            raise FiletransRejected('GONE', 410, 'Cloud audio in the selection has expired.')
        if row['recovery_grant_id'] is not None or row['ingest_mode'] == 'recovery':
            if (row['granted_epoch'] != epoch['id'] or row['granted_manifest'] != epoch['manifest_sha256']
                    or row['last_seq'] < row['seq'] or row['last_sample'] < row['end_sample']):
                raise AuthRejected('SOURCE_UNAVAILABLE', 409)
        obj = stored(dict(row, byte_length=row['size_bytes']))
        return dict(id=str(row['chunk_id']), epoch=str(epoch['id']), receipt=str(row['receipt_id']),
            asset=str(row['id']), seal=epoch['manifest_sha256'], offset=epoch['timeline_offset_ms'],
            sha256=row['sha256'], object_key=obj.key, etag=obj.etag, object_version=obj.version_id,
            byte_length=obj.byte_length, source_first=row['start_sample'], source_last=row['end_sample'],
            first=first, last=last, expires_at=expiry.isoformat())

    def freeze(self, class_id, request):
        policy = self.policy(class_id)
        window = self.window(request)
        epochs = self.epochs(class_id)
        rows = self.rows(class_id)
        if len(rows) > MAX_CHUNKS:
            raise AuthRejected('CAPACITY_EXCEEDED', 409)
        by_epoch = {}
        for row in rows:
            by_epoch.setdefault(row['capture_epoch_id'], []).append(row)
        now = self.now()
        records, windows = [], []
        complete = True
        for epoch in epochs:
            offset = epoch['timeline_offset_ms'] * 16
            end = epoch['end_sample']
            if end is None:
                if window is None or window[1] > offset:
                    complete = False
                continue
            first, last = (0, end) if window is None else (max(0, window[0]-offset), min(end, window[1]-offset))
            if first >= last:
                continue
            if epoch['status'] not in ('ended', 'retired') or epoch['manifest_sha256'] is None:
                complete = False
                continue
            self.consents(epoch, policy)
            windows.append((first+offset, last+offset))
            frontier = first
            for row in sorted(by_epoch.get(epoch['id'], []), key=lambda row: row['start_sample']):
                a, b = max(first, row['start_sample']), min(last, row['end_sample'])
                if a >= b:
                    continue
                record = self.record(row, epoch, a, b, now)
                if record is None:
                    continue
                if a < frontier:
                    raise AuthRejected('SOURCE_UNAVAILABLE', 409)
                complete &= a == frontier
                frontier = b
                records.append(record)
            complete &= frontier == last
        if window is not None:
            frontier = window[0]
            for first, last in sorted(windows):
                if first > frontier:
                    break
                frontier = max(frontier, last)
            complete &= frontier >= window[1]
        if not records:
            raise FiletransRejected('SOURCE_UNAVAILABLE', 409, 'No sealed, durably confirmed cloud audio is available.')
        if len(selected_groups(records)) > MAX_SEGMENTS:
            raise AuthRejected('CAPACITY_EXCEEDED', 409)
        return dict(format='pcm_s16le/16000/mono', class_session_id=str(class_id),
                    scope=request['scope'], range=window, complete=complete, chunks=records)

    def verify(self, plan):
        class_id = plan['class_session_id']
        policy = self.policy(class_id)
        manifest = plan['audio_manifest']
        if _hash(manifest) != plan['audio_manifest_sha256']:
            raise AuthRejected('SOURCE_UNAVAILABLE', 409)
        records = manifest['chunks']
        epochs = {str(row['id']): row for row in self.epochs(class_id)}
        rows = {str(row['chunk_id']): row for row in self.rows(class_id, [UUID(row['id']) for row in records])}
        now = self.now()
        checked = set()
        for frozen in records:
            row, epoch = rows.get(frozen['id']), epochs.get(frozen['epoch'])
            if row is None or epoch is None or epoch['id'] != row['capture_epoch_id']:
                raise AuthRejected('SOURCE_UNAVAILABLE', 409)
            if epoch['id'] not in checked:
                self.consents(epoch, policy)
                checked.add(epoch['id'])
            if self.record(row, epoch, frozen['first'], frozen['last'], now) != frozen:
                raise FiletransRejected('SOURCE_CHANGED', 409, 'The frozen cloud audio is no longer available unchanged.')
        return [(row, stored(dict(rows[row['id']], byte_length=row['byte_length']))) for row in records]

    def publication(self, plan):
        self.classroom(plan['class_session_id'])
        # Explicit revocation wins. Natural expiry alone does not revoke text
        # from an already-authorized submission unless deletion has begun.
        ids = [UUID(row['id']) for row in plan['audio_manifest']['chunks']]
        rows = self.rows(plan['class_session_id'], ids)
        if len(rows) != len(ids) or any(row['deleted_at'] or row['access_revoked_at']
                or row['status'] != 'committed' for row in rows):
            raise AuthRejected('GONE', 410)
