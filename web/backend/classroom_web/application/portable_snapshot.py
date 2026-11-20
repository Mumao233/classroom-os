"""Read owner-authorized classroom snapshots for the provider-free export worker."""
from contextlib import contextmanager

from PIL import Image, UnidentifiedImageError

from .media import identifier, stored
from ..domain.auth import AuthRejected
from ..domain.data import Resource
from ..domain.media import ObjectUnavailable
from ..domain.portable import PORTABLE_V21
from ..domain.portable_export import ArchiveAsset, ArchiveClassroom


def scope_members(life, tx, scope, scope_id):
    if scope == 'class_session':
        return (tx.catalog._load(Resource.CLASS_SESSION, scope_id, share=True).id,)
    if scope == 'account':
        if scope_id != life.owner:
            raise AuthRejected('NOT_FOUND', 404)
    elif scope == 'course':
        tx.catalog._load(Resource.COURSE, scope_id, share=True)
    else:
        raise AuthRejected('VALIDATION_FAILED', 422)
    rows = life.rows('''SELECT s.id FROM class_sessions s
        JOIN courses c ON (s.owner_id,s.course_id)=(c.owner_id,c.id)
        JOIN semesters t ON (c.owner_id,c.semester_id)=(t.owner_id,t.id)
        WHERE s.owner_id=:owner AND s.deleted_at IS NULL AND c.deleted_at IS NULL AND t.deleted_at IS NULL
            AND (CAST(:course AS uuid) IS NULL OR s.course_id=:course)
        ORDER BY s.id LIMIT :limit''', course=scope_id if scope == 'course' else None,
        limit=PORTABLE_V21.files // 2 + 1).all()
    if len(rows) > PORTABLE_V21.files // 2:
        raise AuthRejected('CAPACITY_EXCEEDED', 413)
    if not rows:
        raise AuthRejected('SOURCE_UNAVAILABLE', 409)
    return tuple(row['id'] for row in rows)


class ExportSnapshot:
    def __init__(self, transfers, owner, pulse):
        self.transfers = transfers
        self.owner = owner
        self.pulse = pulse

    @contextmanager
    def _open_media(self, asset_id, original):
        self.pulse()
        with self.transfers._transaction(self.owner) as (_, repo, _):
            pinned = stored(repo.asset(asset_id, available=True))
            if pinned != original:
                raise AuthRejected('SOURCE_CHANGED', 409)
        with self.transfers._spool() as output:
            for offset in range(0, pinned.byte_length, 1_000_000):
                try:
                    block = self.transfers.media.store.read(
                        pinned, offset, min(pinned.byte_length - 1, offset + 999_999))
                except ObjectUnavailable:
                    raise AuthRejected('STORAGE_UNAVAILABLE', 503) from None
                output.write(block)
            output.seek(0)
            yield output

    def _read(self, classroom_id):
        with self.transfers._transaction(self.owner) as (life, repo, _):
            clazz = repo.tx.catalog._load(Resource.CLASS_SESSION, classroom_id, share=True)
            history = life.rows('''SELECT snapshot,id_map FROM portable_imported_classrooms
                WHERE owner_id=:owner AND class_session_id=:id''', id=classroom_id).one_or_none()
            sources = life.rows('''SELECT s.id,s.start_ms,s.end_ms,v.text,v.revision,v.content_sha256
                FROM qa_sources s JOIN qa_source_versions v
                  ON (s.owner_id,s.id,s.current_revision_id)=(v.owner_id,v.source_id,v.id)
                WHERE s.owner_id=:owner AND s.class_session_id=:id AND s.kind='transcript_segment'
                ORDER BY s.id LIMIT 513''', id=classroom_id).all()
            images = life.rows('''SELECT id FROM media_assets
                WHERE owner_id=:owner AND class_session_id=:id AND kind='slide' AND status='committed'
                AND access_revoked_at IS NULL AND (expires_at IS NULL OR expires_at>clock_timestamp())
                ORDER BY id LIMIT :limit''', id=classroom_id, limit=PORTABLE_V21.files + 1).all()
            notes = life.rows('''SELECT d.id,d.title,r.body_markdown,r.revision,r.source_snapshot_id
                FROM review_documents d JOIN review_revisions r
                    ON (d.owner_id,d.id,coalesce(d.latest_personal_revision_id,d.adopted_revision_id))=
                       (r.owner_id,r.document_id,r.id)
                WHERE d.owner_id=:owner AND d.class_session_id=:id ORDER BY d.id LIMIT 513''',
                id=classroom_id).all()
            if max(len(sources), len(notes)) > 512 or len(images)>PORTABLE_V21.files:
                raise AuthRejected('CAPACITY_EXCEEDED', 413)
            evidence = {}
            for note in notes:
                if note['source_snapshot_id'] is not None:
                    evidence[note['id']] = life.rows('''SELECT source_id,revision_id,frozen_text,source_hash AS content_sha256
                        FROM source_items WHERE owner_id=:owner AND snapshot_id=:id ORDER BY source_key''',
                        id=note['source_snapshot_id']).all()
                    if not evidence[note['id']]:
                        raise AuthRejected('SOURCE_UNAVAILABLE', 409)
            available = {row['id']: repo.asset(row['id'], available=True) for row in images}
        return clazz, history, sources, available, notes, evidence

    def read(self, classroom_id, *, allow_missing):
        self.pulse()
        clazz, history, sources, images, notes, evidence = self._read(classroom_id)
        session, media, mappings = {}, [], {}
        if history is not None:
            session = history['snapshot']['session']
            media = history['snapshot']['media']
            mappings = history['id_map']
        session.update(id=clazz.id.hex, title=clazz.title)
        session.setdefault('created_at', clazz.created_at.isoformat())
        existing_segments = {row['id'] for row in mappings.get('segments', {}).values() if 'revision_id' in row}
        if not existing_segments.issubset({str(row['id']) for row in sources}):
            raise AuthRejected('SOURCE_UNAVAILABLE', 409)
        session.setdefault('web_transcript', []).extend(
            dict(id=row['id'].hex, text=row['text'], start_ms=row['start_ms'], end_ms=row['end_ms'],
                 version=row['revision'], sha256=row['content_sha256'])
            for row in sources if str(row['id']) not in existing_segments)
        existing_review = {row['id'] for row in mappings.get('review', {}).values()}
        session.setdefault('web_notes', []).extend(
            dict(id=row['id'].hex, title=row['title'], body=row['body_markdown'], version=row['revision'],
                 evidence=[dict(source_id=item['source_id'].hex, revision_id=item['revision_id'].hex,
                                frozen_text=item['frozen_text'], content_sha256=item['content_sha256'])
                           for item in evidence.get(row['id'], ())])
            for row in notes if str(row['id']) not in existing_review)
        by_path = {}
        used = set()
        for item in media:
            if item['kind'] != 'image':
                item.pop('archive_path', None)
                item['availability'] = 'not_included'
                continue
            link = mappings.get('slides', {}).get(item['original_path'])
            image_id = identifier(link['id']) if link else None
            asset = images.get(image_id)
            if asset is None:
                item.pop('archive_path', None)
                item['availability'] = 'missing'
                if not allow_missing:
                    raise AuthRejected('SOURCE_UNAVAILABLE', 409)
                continue
            if asset['sha256'] != item['sha256']:
                raise AuthRejected('SOURCE_CHANGED', 409)
            used.add(image_id)
            by_path[item['archive_path']] = self._asset(item['archive_path'], asset)
        aliases = {item['id'] for item in media if item['kind'] == 'image'}
        number = 0
        for image_id, asset in images.items():
            if image_id in used:
                continue
            extension = {'image/jpeg': 'jpg', 'image/png': 'png'}.get(asset['mime_type'])
            if extension is None or asset['byte_length'] > PORTABLE_V21.part_bytes:
                raise AuthRejected('SOURCE_UNAVAILABLE', 409)
            while (alias := f'P{number:04d}') in aliases:
                number += 1
            aliases.add(alias)
            original = f'slides/{image_id.hex}.{extension}'
            path = f'assets/{asset["sha256"]}.{extension}'
            member = self._asset(path, asset)
            try:
                with member.open_stream() as source, Image.open(source) as probe:
                    width, height = probe.size
            except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
                raise AuthRejected('SOURCE_UNAVAILABLE', 409) from None
            row = dict(id=alias, path=original, version=asset['revision'], mime_type=asset['mime_type'],
                       sha256=asset['sha256'], width=width, height=height)
            session.setdefault('web_images', []).append(row)
            media.append(dict(kind='image', id=alias, version=row['version'], sha256=row['sha256'],
                              original_path=original, archive_path=path, availability='available', size=member.size))
            by_path.setdefault(path, member)
        dependencies = {('media', identity) for identity in images}
        dependencies.update(('transcript', row['id']) for row in sources)
        dependencies.update(('transcript', item['source_id']) for selection in evidence.values() for item in selection)
        return ArchiveClassroom(session, tuple(media), tuple(by_path.values())), dependencies

    def _asset(self, path, asset):
        return ArchiveAsset(path, asset['byte_length'], asset['sha256'],
                            lambda: self._open_media(asset['id'], stored(asset)))
