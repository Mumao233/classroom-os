"""Prepare image copies in private storage and publish an independent classroom in one UoW."""
import hashlib
import json
import re
from uuid import uuid4

from ..domain.auth import AuthRejected
from ..domain.data import Resource
from ..domain.portable import PORTABLE_V21, inspect_candidate, require
from ..domain.portable_export import require_portable_metadata
from ..infrastructure.qa_repository import PgQaRepository
from ..infrastructure.review_repository import PgReviewRepository

SESSION_FIELDS = frozenset(('id', 'schema_version', 'title', 'created_at', 'segments', 'groups',
                            'slides', 'questions', 'revisions', 'review', 'audio', 'web_notes', 'web_transcript', 'web_images'))


def as_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def literal_markdown(value):
    # Legacy input is plain text here. Preserve the untouched value in the archival snapshot.
    escaped = re.sub(r'([\\`*_{}\[\]()<>#!|+~>:.&-])', lambda match: chr(92) + match[0], value)
    require(len(escaped) <= 120000, 'CAPACITY_EXCEEDED')
    return escaped


class ImportCopy:
    def __init__(self, transfers):
        self.transfers = transfers

    def prepare(self, owner, task, upload, parts, pulse, *, allow_missing):
        ready = []
        with self.transfers._spool() as spool:
            self.transfers._assemble(owner, upload, parts, spool, pulse=pulse)

            def apply_room(session, media, archive, entries):
                session = {name: value for name, value in session.items() if name in SESSION_FIELDS}
                require_portable_metadata(session)
                if len(session.get('segments', ())) + len(session.get('web_transcript', ())) > 512:
                    raise AuthRejected('CAPACITY_EXCEEDED', 413)
                media = [{field: value for field, value in item.items()
                          if field in ('kind','id','version','sha256','original_path','archive_path','size','availability')}
                         for item in media]
                if not allow_missing and any(item['kind'] == 'image' and item['availability'] != 'available' for item in media):
                    raise AuthRejected('SOURCE_UNAVAILABLE', 409)
                assets = {}
                for member in media:
                    if member['kind'] != 'image' or member['availability'] != 'available':
                        continue
                    path = member['archive_path']
                    if path in assets:
                        continue
                    asset = self._reserve_asset(owner, task, upload['id'], session['id'], member, pulse)
                    with self.transfers._spool() as content, archive.open(entries[path]) as source:
                        digest = hashlib.sha256()
                        for block in iter(lambda: source.read(1_000_000), b''):
                            content.write(block)
                            digest.update(block)
                        if content.tell() != member['size'] or digest.hexdigest() != member['sha256']:
                            raise AuthRejected('SOURCE_CHANGED', 409)
                        obj = self.transfers.write_staged(owner, upload['id'], asset, content, copy=True)
                    assets[path] = (asset, obj)
                ready.append((session, media, assets))

            inspect_candidate(spool, upload['reserved_bytes'], upload['mime_type'], on_room=apply_room)
        return ready

    def _reserve_asset(self, owner, task, transfer, alias, member, pulse):
        path, length, sha = member['archive_path'], member['size'], member['sha256']
        pulse()
        with self.transfers._transaction(owner) as (life, _, _):
            budget = life.gate()
            life.transfer(transfer, state='verified')
            prior = life.rows('''SELECT * FROM portable_import_assets
                WHERE owner_id=:owner AND task_id=:task AND classroom_alias=:alias AND archive_path=:path''',
                task=task.id, alias=alias, path=path).one_or_none()
            if prior:
                if prior['byte_length'] != length or prior['sha256'] != sha or prior['erased_at'] is not None:
                    raise AuthRejected('SOURCE_CHANGED', 409)
                return prior
            if budget['import_bytes'] + length > PORTABLE_V21.pool_bytes:
                raise AuthRejected('CAPACITY_EXCEEDED', 413)
            asset = uuid4()
            return life.rows('''INSERT INTO portable_import_assets
                (id,owner_id,task_id,transfer_id,classroom_alias,archive_path,sha256,byte_length,object_key)
                VALUES(:id,:owner,:task,:transfer,:alias,:path,:sha,:length,:key) RETURNING *''',
                id=asset, task=task.id, transfer=transfer, alias=alias, path=path, sha=sha, length=length,
                key=f'media/{life.owner.hex}/{asset.hex}/{sha}').one()

    def publish(self, life, tx, task, upload, rooms):
        course = upload['destination_course_id']
        tx.catalog._load(Resource.COURSE, course, share=True)
        published = []
        for session, media, assets in rooms:
            classroom = tx.catalog.create_class_session(course, session['title'])
            identities = {'classroom': {session['id']: str(classroom.id)}}
            for path, (asset, obj) in assets.items():
                if not obj.version_id:
                    raise AuthRejected('STORAGE_UNAVAILABLE', 503)
                mime = 'image/png' if path.endswith('.png') else 'image/jpeg'
                life.rows('''INSERT INTO media_assets
                        (id,owner_id,class_session_id,kind,sha256,size_bytes,mime_type,object_key,
                        status,object_etag,object_version,first_cloud_received_at,confirmed_at)
                    VALUES(:id,:owner,:classroom,'slide',:sha,:size,:mime,:key,
                        'committed',:etag,:version,:received,clock_timestamp()) RETURNING id''',
                    id=asset['id'], classroom=classroom.id, sha=asset['sha256'], size=asset['byte_length'],
                    mime=mime, key=asset['object_key'], etag=obj.etag, version=obj.version_id, received=obj.received_at).one()
                life.rows('''UPDATE portable_import_assets SET published_at=clock_timestamp()
                    WHERE owner_id=:owner AND id=:id AND erased_at IS NULL RETURNING id''', id=asset['id']).one()
            identities['slides'] = {
                member['original_path']: dict(id=str(assets[member['archive_path']][0]['id'])
                    if member.get('archive_path') in assets else str(uuid4()),
                    version=member['version'], sha256=member['sha256'])
                for member in media if member['kind'] == 'image'}
            identities['segments'] = self._segments(tx, classroom.id, session)
            identities['review'] = self._review(tx, classroom.id, session)
            for kind in ('groups', 'questions', 'revisions'):
                identities[kind] = {item['id']: str(uuid4()) for item in session.get(kind, ())}
            snapshot = dict(session=session, media=media)
            life.rows('''INSERT INTO portable_imported_classrooms
                    (class_session_id,owner_id,task_id,original_id,original_sha256,snapshot,id_map)
                VALUES(:id,:owner,:task,:alias,:sha,CAST(:snapshot AS jsonb),CAST(:ids AS jsonb)) RETURNING class_session_id''',
                id=classroom.id, task=task.id, alias=session['id'], sha=upload['archive_sha256'],
                snapshot=as_json(snapshot), ids=as_json(identities)).one()
            published.append(classroom.id)
        return published[0]

    @staticmethod
    def _segments(tx, classroom, session):
        sources = PgQaRepository(tx)
        mapped = {}
        for item in session.get('segments', ()):
            start, end = round(item['start'] * 1000), round(item['end'] * 1000)
            if not item['text'].strip():
                mapped[item['id']] = dict(id=str(uuid4()), original_version=item.get('version', 0),
                                          original_hash=item.get('content_hash'))
                continue
            source = sources.add_source(classroom, kind='transcript_segment', object_id=uuid4(),
                                        text=item['text'], start_ms=start if end > start else None,
                                        end_ms=end if end > start else None, origin='import')
            mapped[item['id']] = dict(id=str(source.source_id), revision_id=str(source.revision_id),
                original_version=item.get('version', 0), original_hash=item.get('content_hash'),
                restored_sha256=source.content_sha256)
        for item in session.get('web_transcript', ()):
            source = sources.add_source(classroom, kind='transcript_segment', object_id=uuid4(),
                text=item['text'], start_ms=item['start_ms'], end_ms=item['end_ms'], origin='import')
            mapped[item['id']] = dict(id=str(source.source_id), revision_id=str(source.revision_id),
                original_version=item['version'], original_hash=item['sha256'],
                restored_sha256=source.content_sha256)
        return mapped

    @staticmethod
    def _review(tx, classroom, session):
        review = PgReviewRepository(tx)
        mapped = {}
        overlays = {item['legacy_review_key']: item['id'] for item in session.get('web_notes', ())
                    if 'legacy_review_key' in item}
        for kind in ('cards', 'notes', 'marks', 'runs'):
            for item in session.get('review', {}).get(kind, ()):
                native = None
                key = f'{kind}:{item["id"]}'
                if kind in ('cards', 'notes') and key not in overlays:
                    draft = item['user_draft']
                    content = draft['content']
                    if content.strip() or draft['personal_note'].strip():
                        title = item['ai_content'].get('title') or 'Imported review'
                        require(len(title) <= 200)
                        document = review.create_note(dict(class_session_id=str(classroom), title=title,
                            body_markdown=literal_markdown(content + ('\n\n' + draft['personal_note'] if draft['personal_note'] else '')),
                            draft_id=str(uuid4())), uuid4())
                        native = document['id']
                mapped[key] = dict(id=native or str(uuid4()), native=native is not None,
                    original_snapshot_hash=item.get('evidence', {}).get('snapshot_hash'))
        for note in session.get('web_notes', ()):
            document = review.create_note(dict(class_session_id=str(classroom), title=note['title'],
                body_markdown=note['body'], draft_id=str(uuid4())), uuid4(),
                portable_annotation=note.get('personal_note', ''))
            mapped[f'web_note:{note["id"]}'] = dict(id=document['id'], native=True)
            if 'legacy_review_key' in note:
                mapped[note['legacy_review_key']].update(id=document['id'], native=True)
        return mapped
