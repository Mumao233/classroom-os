"""Owner-scoped immutable review versions and explicit user-controlled adoption.

Each command is called inside one authorized UoW. A personal-write conflict is a
returned value so its submitted branch commits before the HTTP layer returns 409.
"""
from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import hmac
import json
import unicodedata
from uuid import UUID, uuid4

from sqlalchemy import text as sql

from ..domain.data import RevisionConflict
from ..domain.qa import QaRejected
from .qa_repository import PgQaRepository, _hash, _json, _stamp


_SYNC_SERVER_UNREAD = object()


class PgReviewRepository:
    def __init__(self, tx):
        self.tx = tx
        self.connection = tx.connection
        self.owner_id = tx.catalog.owner_id
        self.sources = PgQaRepository(tx)

    def _rows(self, statement, **params):
        return self.connection.execute(sql(statement), dict(owner=self.owner_id, **params)).mappings()

    def _serialize(self):
        self._rows("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:owner AS text)||'-review',0))")

    @staticmethod
    def _uuid(value):
        try:
            identity = UUID(str(value))
            if identity.version != 4:
                raise ValueError()
            return identity
        except (TypeError, ValueError, AttributeError):
            raise QaRejected('VALIDATION_FAILED', 422) from None

    @staticmethod
    def _text(value, maximum, *, nonempty=False):
        if (not isinstance(value, str) or len(value) > maximum or '\x00' in value
                or nonempty and not value.strip()):
            raise QaRejected('VALIDATION_FAILED', 422)
        try:
            value.encode('utf8')
        except UnicodeError:
            raise QaRejected('VALIDATION_FAILED', 422) from None
        return value

    @staticmethod
    def _base(body):
        value = body.get('base_revision')
        if type(value) is not int or value < 1:
            raise QaRejected('VALIDATION_FAILED', 422)
        return value

    def _document(self, document_id, *, lock=False):
        row = self._rows('SELECT * FROM review_documents WHERE owner_id=:owner AND id=:id',
                         id=document_id).one_or_none()
        if row is None:
            raise QaRejected('NOT_FOUND', 404)
        self.sources._class(row['class_session_id'])
        if row['origin_snapshot_id'] is not None:
            self.sources._snapshot_row(row['origin_snapshot_id'])
        if lock:
            row = self._rows('SELECT * FROM review_documents WHERE owner_id=:owner AND id=:id FOR UPDATE',
                             id=document_id).one_or_none()
            if row is None:
                raise QaRejected('NOT_FOUND', 404)
        return row

    def _revision(self, revision_id, document_id=None):
        row = self._rows('SELECT * FROM review_revisions WHERE owner_id=:owner AND id=:id',
                         id=revision_id).one_or_none()
        if row is None or document_id is not None and row['document_id'] != document_id:
            raise QaRejected('NOT_FOUND', 404)
        self._document(row['document_id'])
        if row['source_snapshot_id'] is not None:
            self.sources._snapshot_row(row['source_snapshot_id'])
        return row

    @staticmethod
    def _revision_dto(row):
        return dict(id=str(row['id']), revision=row['revision'], created_at=_stamp(row['created_at']),
                    updated_at=_stamp(row['updated_at']), document_id=str(row['document_id']), kind=row['kind'],
                    parent_revision_id=str(row['parent_revision_id']) if row['parent_revision_id'] else None,
                    source_snapshot_id=str(row['source_snapshot_id']) if row['source_snapshot_id'] else None,
                    raw_output_ref=str(row['raw_output_ref']) if row['raw_output_ref'] else None,
                    generation_task_id=str(row['task_id']) if row['task_id'] else None,
                    body_markdown=row['body_markdown'], structured_content=row['structured_content'],
                    personal_note=row['personal_note'])

    def _source_status(self, row):
        snapshot_id = row['origin_snapshot_id']
        active = row['adopted_revision_id'] or row['latest_personal_revision_id']
        if active:
            revision = self._rows('SELECT source_snapshot_id FROM review_revisions WHERE owner_id=:owner AND id=:id',
                                  id=active).one_or_none()
            if revision is None:
                return 'unavailable'
            snapshot_id = revision['source_snapshot_id']
        if snapshot_id is None:
            return 'unchanged'
        snapshot = self._rows('SELECT * FROM source_snapshots WHERE owner_id=:owner AND id=:id', id=snapshot_id).one_or_none()
        if snapshot is None:
            return 'unavailable'
        changed = self._rows('''SELECT EXISTS(SELECT FROM source_items i JOIN qa_sources s
            ON s.owner_id=i.owner_id AND s.id=i.source_id WHERE i.owner_id=:owner AND i.snapshot_id=:snapshot
            AND i.revision_id<>s.current_revision_id) AS changed''', snapshot=snapshot_id).one()['changed']
        if changed:
            return 'changed'
        added = self._rows('''SELECT EXISTS(SELECT FROM qa_sources WHERE owner_id=:owner
            AND class_session_id=:classroom AND NOT id=ANY(:sources)) AS added''',
            classroom=row['class_session_id'], sources=snapshot['source_ids']).one()['added']
        return 'new_material_not_included' if added else 'unchanged'

    def _document_dto(self, row):
        candidates = self._rows('''SELECT id FROM review_revisions WHERE owner_id=:owner
            AND document_id=:id AND kind='ai_candidate' ORDER BY revision''', id=row['id']).all()
        if len(candidates) > 1000:
            raise QaRejected('CAPACITY_EXCEEDED', 409)
        return dict(id=str(row['id']), revision=row['revision'], created_at=_stamp(row['created_at']),
                    updated_at=_stamp(row['updated_at']), class_session_id=str(row['class_session_id']),
                    kind=row['kind'], title=row['title'],
                    adopted_revision_id=str(row['adopted_revision_id']) if row['adopted_revision_id'] else None,
                    latest_personal_revision_id=str(row['latest_personal_revision_id']) if row['latest_personal_revision_id'] else None,
                    candidate_revision_ids=[str(item['id']) for item in candidates], understood=row['understood'],
                    source_status=self._source_status(row))

    def document(self, document_id, revision=None):
        row = self._document(document_id)
        if revision is not None and (type(revision) is not int or revision < 1):
            raise QaRejected('VALIDATION_FAILED', 422)
        if revision is None or revision == row['revision']:
            return self._document_dto(row)
        history = self._rows('''SELECT projection FROM review_document_history
            WHERE owner_id=:owner AND document_id=:id AND revision=:revision''',
            id=document_id, revision=revision).one_or_none()
        if history is None:
            raise RevisionConflict(row['revision'])
        result = dict(history['projection'])
        # Historical metadata remains exact while access status always reflects
        # current authority. Revision bodies are separately reauthorized reads.
        result['source_status'] = self._source_status(row)
        return result

    def revision(self, revision_id):
        return self._revision_dto(self._revision(revision_id))

    def _cursor_secret(self):
        secret = self.tx.events.secret
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise QaRejected('SERVICE_DISABLED', 503)
        return secret

    def _cursor(self, row, filters):
        payload = _json(dict(owner=str(self.owner_id), filters=_hash(filters),
                             created_at=_stamp(row['created_at']), id=str(row['id']))).encode('utf8')
        signature = hmac.digest(self._cursor_secret(), b'review-page-v1\x00'+payload, 'sha256')
        return base64.urlsafe_b64encode(payload+b'.'+signature).decode('ascii').rstrip('=')

    def _decode_cursor(self, cursor, filters):
        if cursor is None:
            return None, None
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 512:
                raise ValueError()
            raw = base64.b64decode(cursor+'='*(-len(cursor)%4), altchars=b'-_', validate=True)
            payload, signature = raw[:-33], raw[-32:]
            if raw[-33:-32] != b'.' or not hmac.compare_digest(signature,
                    hmac.digest(self._cursor_secret(), b'review-page-v1\x00'+payload, 'sha256')):
                raise ValueError()
            value = json.loads(payload)
            if (set(value) != {'owner', 'filters', 'created_at', 'id'}
                    or value['owner'] != str(self.owner_id) or value['filters'] != _hash(filters)):
                raise ValueError()
            created = datetime.fromisoformat(value['created_at'])
            if created.tzinfo is None:
                raise ValueError()
            return self._uuid(value['id']), created
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise QaRejected('VALIDATION_FAILED', 422) from None

    def documents(self, *, semester_id=None, course_id=None, class_session_id=None,
                  kind=None, understood=None, limit=50, cursor=None):
        if (type(limit) is not int or not 1 <= limit <= 100
                or kind not in (None, 'summary', 'card', 'note')
                or understood is not None and type(understood) is not bool):
            raise QaRejected('VALIDATION_FAILED', 422)
        if semester_id:
            self.tx.catalog.get_semester(semester_id)
        if course_id:
            self.tx.catalog.get_course(course_id)
        if class_session_id:
            self.sources._class(class_session_id)
        filters = dict(scope='documents', semester=str(semester_id) if semester_id else None,
                       course=str(course_id) if course_id else None,
                       classroom=str(class_session_id) if class_session_id else None,
                       kind=kind, understood=understood)
        previous, created = self._decode_cursor(cursor, filters)
        rows = self._rows('''SELECT d.* FROM review_documents d
            JOIN class_sessions s ON s.owner_id=d.owner_id AND s.id=d.class_session_id
            JOIN courses c ON c.owner_id=s.owner_id AND c.id=s.course_id
            WHERE d.owner_id=:owner
             AND (CAST(:semester AS uuid) IS NULL OR c.semester_id=:semester)
             AND (CAST(:course AS uuid) IS NULL OR c.id=:course)
             AND (CAST(:classroom AS uuid) IS NULL OR s.id=:classroom)
             AND (CAST(:kind AS text) IS NULL OR d.kind=:kind)
             AND (CAST(:understood AS boolean) IS NULL OR d.understood=:understood)
             AND (CAST(:created AS timestamptz) IS NULL OR (d.created_at,d.id)>(:created,CAST(:previous AS uuid)))
            ORDER BY d.created_at,d.id LIMIT :limit''', semester=semester_id, course=course_id,
            classroom=class_session_id, kind=kind, understood=understood, created=created,
            previous=previous, limit=limit+1).all()
        return dict(items=[self.document(row['id']) for row in rows[:limit]],
                    next_cursor=self._cursor(rows[limit-1], filters) if len(rows)>limit else None)

    def revisions(self, document_id, limit=50, cursor=None):
        self._document(document_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise QaRejected('VALIDATION_FAILED', 422)
        filters = dict(scope='revisions', document=str(document_id))
        previous, created = self._decode_cursor(cursor, filters)
        rows = self._rows('''SELECT * FROM review_revisions WHERE owner_id=:owner AND document_id=:document
            AND (CAST(:created AS timestamptz) IS NULL OR (created_at,id)>(:created,CAST(:previous AS uuid)))
            ORDER BY created_at,id LIMIT :limit''', document=document_id, created=created,
            previous=previous, limit=limit+1).all()
        return dict(items=[self.revision(row['id']) for row in rows[:limit]],
                    next_cursor=self._cursor(rows[limit-1], filters) if len(rows)>limit else None)

    def _remember(self, operation, op_id, digest, kind, identity, status):
        self.sources._remember(operation, op_id, digest, kind, identity, status)

    def _record_history(self, row):
        self._rows('''INSERT INTO review_document_history(id,owner_id,document_id,revision,projection)
            VALUES(:id,:owner,:document,:revision,CAST(:projection AS jsonb))''',
            id=uuid4(), document=row['id'], revision=row['revision'], projection=_json(self._document_dto(row)))

    def _event(self, row):
        self._rows('''INSERT INTO owner_event_counters(id,owner_id,next_sequence) VALUES(:id,:owner,1)
            ON CONFLICT(owner_id) DO NOTHING''', id=uuid4())
        number = self._rows('''UPDATE owner_event_counters SET next_sequence=next_sequence+1
            WHERE owner_id=:owner RETURNING next_sequence''').one()['next_sequence']-1
        identity = uuid4()
        self._rows('''INSERT INTO outbox_events(id,owner_id,event_sequence,event_type,aggregate_type,
            aggregate_id,aggregate_revision,payload,schema_version)
            VALUES(:id,:owner,:sequence,'object.changed','review_document',:document,:revision,CAST(:payload AS jsonb),'1')''',
            id=identity, sequence=number, document=row['id'], revision=row['revision'],
            payload=_json(dict(object_type='review_document', object_id=str(row['id']), revision=row['revision'])))
        self._rows("SELECT pg_notify('classroom_web_events',:event)", event=str(identity))

    def _advance(self, row, **fields):
        # Column names originate from fixed command implementations only.
        assignments = ','.join(f'{key}=:{key}' for key in fields)
        prefix = assignments+',' if assignments else ''
        updated = self._rows(f'''UPDATE review_documents SET {prefix}revision=revision+1,
            updated_at=clock_timestamp() WHERE owner_id=:owner AND id=:id AND revision=:expected RETURNING *''',
            id=row['id'], expected=row['revision'], **fields).one_or_none()
        if updated is None:
            raise RevisionConflict(row['revision'])
        self._record_history(updated)
        self._event(updated)
        return updated

    def _new_document(self, classroom, kind, title, snapshot=None, parent=None, artifact_key='note'):
        row = self._rows('''INSERT INTO review_documents(id,owner_id,class_session_id,kind,title,
            origin_snapshot_id,parent_document_id,artifact_key)
            VALUES(:id,:owner,:classroom,:kind,:title,:snapshot,:parent,:artifact) RETURNING *''',
            id=uuid4(), classroom=classroom, kind=kind, title=title, snapshot=snapshot,
            parent=parent, artifact=artifact_key).one()
        self._record_history(row)
        return row

    def _append(self, document, *, kind, body, snapshot=None, parent=None, task=None,
                artifact=None, draft=None, structured=None, note='', raw=None):
        # Document CAS advances for every content append, including conflicts.
        # This clock remains monotonic when RLS hides a revoked older version.
        number = document['revision']
        if number > 10000:
            raise QaRejected('CAPACITY_EXCEEDED', 409)
        return self._rows('''INSERT INTO review_revisions(id,owner_id,document_id,class_session_id,revision,kind,
            parent_revision_id,source_snapshot_id,raw_output_ref,task_id,artifact_key,draft_id,
            body_markdown,structured_content,personal_note) VALUES(:id,:owner,:document,:classroom,:revision,:kind,
            :parent,:snapshot,:raw,:task,:artifact,:draft,:body,CAST(:structured AS jsonb),:note) RETURNING *''',
            id=uuid4(), document=document['id'], classroom=document['class_session_id'], revision=number,
            kind=kind, parent=parent, snapshot=snapshot, raw=raw, task=task, artifact=artifact, draft=draft,
            body=body, structured=_json(structured) if structured is not None else None, note=note).one()

    def create_note(self, body, op_id):
        classroom = self._uuid(body.get('class_session_id'))
        self.sources._class(classroom)
        self._serialize()
        digest = _hash(body)
        old = self.tx.receipts.lock_and_get('createPersonalNote', op_id, digest)
        if old:
            return self.document(old['resource_id'])
        title = self._text(body.get('title'), 200)
        content = self._text(body.get('body_markdown'), 120000)
        draft = self._uuid(body.get('draft_id'))
        document = self._new_document(classroom, 'note', title)
        revision = self._append(document, kind='personal', body=content, draft=draft)
        document = self._advance(document, latest_personal_revision_id=revision['id'])
        self._remember('createPersonalNote', op_id, digest, 'review_document', document['id'], 201)
        return self._document_dto(document)

    def update_document(self, document_id, body, op_id):
        self._serialize()
        row = self._document(document_id, lock=True)
        digest = _hash(dict(document_id=str(document_id), body=body))
        old = self.tx.receipts.lock_and_get('updateReviewDocument', op_id, digest)
        if old:
            return self.document(old['resource_id'])
        if self._base(body) != row['revision']:
            raise RevisionConflict(row['revision'])
        fields = {}
        if 'title' in body:
            fields['title'] = self._text(body['title'], 200)
        if 'understood' in body:
            if type(body['understood']) is not bool:
                raise QaRejected('VALIDATION_FAILED', 422)
            fields['understood'] = body['understood']
        updated = self._advance(row, **fields)
        self._remember('updateReviewDocument', op_id, digest, 'review_document', document_id, 200)
        return self._document_dto(updated)

    def _conflict_dto(self, identity):
        conflict = self._rows('SELECT * FROM review_conflicts WHERE owner_id=:owner AND id=:id', id=identity).one_or_none()
        if conflict is None:
            raise QaRejected('NOT_FOUND', 404)
        self._document(conflict['document_id'])
        return dict(conflict_id=str(conflict['id']), current_revision=conflict['current_document_revision'],
                    submitted_revision=self.revision(conflict['submitted_revision_id']))

    def _sync_server_content(self, document_id, current, document_revision):
        if current is None:
            history = self._rows('''SELECT projection FROM review_document_history WHERE owner_id=:owner
                AND document_id=:document AND revision=:revision''',document=document_id,
                revision=document_revision).one_or_none()
            if history:
                candidates=history['projection']['candidate_revision_ids']
                current=self._uuid(candidates[-1]) if candidates else None
        if current is None:
            return
        try:
            return self.revision(current)
        except QaRejected as error:
            if error.status!=404:
                raise
            # Keep the original 07b branch. A hidden historical cloud version
            # cannot be replaced with another visible candidate in sync metadata.
            return

    def _sync_conflict(self, identity, document_id, body, op_id, *, server_content=_SYNC_SERVER_UNREAD):
        # 07b can still be deployed at its own migration boundary. Once 08b is
        # present, an exact original request makes legacy conflict recovery safe.
        if self.connection.scalar(sql("SELECT to_regclass('public.sync_conflicts')")) is None:
            return
        from .sync_repository import PgSyncRepository
        conflict = self._rows('SELECT * FROM review_conflicts WHERE owner_id=:owner AND id=:id', id=identity).one()
        if server_content is _SYNC_SERVER_UNREAD:
            server_content=self._sync_server_content(document_id,conflict['current_revision_id'],
                                                     conflict['current_document_revision']-1)
        if server_content is None:
            return
        document = self._document(document_id)
        operation = dict(op_id=str(op_id), object_type='personal_revision', object_id=str(document_id),
                         draft_id=body['draft_id'], base_revision=body['base_revision'], payload=body)
        PgSyncRepository(self.tx).record_conflict(operation, conflict['current_document_revision'],
            server_content, document['class_session_id'], identity=identity)

    def write_personal(self, document_id, body, op_id):
        self._serialize()
        document = self._document(document_id, lock=True)
        digest = _hash(dict(document_id=str(document_id), body=body))
        old = self.tx.receipts.lock_and_get('writePersonalRevision', op_id, digest)
        if old:
            if old['resource_type'] == 'review_conflict':
                self._sync_conflict(old['resource_id'], document_id, body, op_id)
            return self._conflict_dto(old['resource_id']) if old['resource_type']=='review_conflict' else self.revision(old['resource_id'])
        base = self._base(body)
        draft = self._uuid(body.get('draft_id'))
        content = self._text(body.get('body_markdown'), 120000)
        note = self._text(body.get('personal_note'), 12000)
        parent_id = self._uuid(body['base_content_revision_id']) if body.get('base_content_revision_id') else None
        parent = self._revision(parent_id, document_id) if parent_id else None
        if parent is None and document['kind'] != 'note':
            raise QaRejected('VALIDATION_FAILED', 422)
        sync_server = None
        if (base != document['revision']
                and self.connection.scalar(sql("SELECT to_regclass('public.sync_conflicts')")) is not None):
            # Lock the exact cloud lineage before _advance acquires the owner
            # event counter. Source revocation acquires its source row first.
            sync_server=self._sync_server_content(document_id,
                document['latest_personal_revision_id'] or document['adopted_revision_id'],document['revision'])
        revision = self._append(document, kind='personal', body=content, note=note, draft=draft,
                                snapshot=parent['source_snapshot_id'] if parent else None, parent=parent_id)
        if base != document['revision']:
            current_id = document['latest_personal_revision_id'] or document['adopted_revision_id']
            document = self._advance(document)
            identity = uuid4()
            self._rows('''INSERT INTO review_conflicts(id,owner_id,document_id,submitted_revision_id,
                current_revision_id,current_document_revision) VALUES(:id,:owner,:document,:submitted,:current,:revision)''',
                id=identity, document=document_id, submitted=revision['id'], current=current_id, revision=document['revision'])
            self._remember('writePersonalRevision', op_id, digest, 'review_conflict', identity, 409)
            self._sync_conflict(identity, document_id, body, op_id, server_content=sync_server)
            return self._conflict_dto(identity)
        self._advance(document, latest_personal_revision_id=revision['id'])
        self._remember('writePersonalRevision', op_id, digest, 'review_revision', revision['id'], 201)
        return self._revision_dto(revision)

    def adopt(self, document_id, body, op_id):
        self._serialize()
        document = self._document(document_id, lock=True)
        digest = _hash(dict(document_id=str(document_id), body=body))
        old = self.tx.receipts.lock_and_get('adoptReviewRevision', op_id, digest)
        if old:
            return self.document(old['resource_id'])
        if self._base(body) != document['revision']:
            raise RevisionConflict(document['revision'])
        revision = self._revision(self._uuid(body.get('revision_id')), document_id)
        document = self._advance(document, adopted_revision_id=revision['id'])
        self._remember('adoptReviewRevision', op_id, digest, 'review_document', document_id, 200)
        return self._document_dto(document)

    def publish(self, task, payload, snapshot_id, request, *, raw_output_ref=None):
        from ..domain.review import validate_review_reply

        selection = self.sources._selection(snapshot_id)
        payload = validate_review_reply(_json(payload), selection, purpose='review_summary')
        snapshot = self.sources._snapshot_row(snapshot_id)
        if snapshot['class_session_id'] != task.class_session_id or task.owner_id != self.owner_id:
            raise QaRejected('NOT_FOUND', 404)
        self._serialize()
        prior = self._rows('''SELECT document_id,id,artifact_key,structured_content,source_snapshot_id
            FROM review_revisions WHERE owner_id=:owner AND task_id=:task
            ORDER BY CASE WHEN artifact_key='summary' THEN 0 ELSE 1 END,artifact_key''', task=task.id).all()
        if prior:
            summary = next(item for item in prior if item['artifact_key']=='summary')
            if summary['structured_content'] != payload or summary['source_snapshot_id'] != snapshot_id:
                raise ValueError('published review result is immutable')
            return dict(summary_document_id=str(summary['document_id']),
                        document_ids=[str(item['document_id']) for item in prior], revision_ids=[str(item['id']) for item in prior])
        target = request.get('target_document_id')
        if target:
            summary = self._document(self._uuid(target), lock=True)
            if summary['kind'] != 'summary' or summary['class_session_id'] != task.class_session_id:
                raise QaRejected('NOT_FOUND', 404)
        else:
            summary = self._new_document(task.class_session_id, 'summary', payload['notes'][0]['title'],
                                         snapshot=snapshot_id, artifact_key='summary')
        raw_id = raw_output_ref
        if raw_id is None:
            results = self._rows('''SELECT r.id FROM provider_results r JOIN task_attempts a
                ON a.owner_id=r.owner_id AND a.id=r.attempt_id WHERE r.owner_id=:owner
                AND a.task_id=:task LIMIT 2''', task=task.id).all()
            if len(results) > 1:
                raise ValueError('exact provider result is required for a multi-attempt task')
            raw_id = results[0]['id'] if results else None
        body = '\n\n'.join('## '+note['title']+'\n\n'+note['body'] for note in payload['notes'])
        self._text(body, 120000, nonempty=True)
        summary_revision = self._append(summary, kind='ai_candidate', body=body, structured=payload,
            snapshot=snapshot_id, task=task.id, artifact='summary', raw=raw_id)
        self._advance(summary)
        documents, revisions = [str(summary['id'])], [str(summary_revision['id'])]
        seen = set()
        card_items = []
        for card in payload['cards']:
            front = ' '.join(unicodedata.normalize('NFC', card['front']).split())
            card_items.append((_hash(dict(type=card['type'], front=front)), card))
        for key, card in sorted(card_items, key=lambda entry: entry[0]):
            if key in seen:
                raise QaRejected('OUTPUT_INVALID', 422)
            seen.add(key)
            document = self._rows('''SELECT * FROM review_documents WHERE owner_id=:owner
                AND parent_document_id=:parent AND artifact_key=:artifact FOR UPDATE''',
                parent=summary['id'], artifact=key).one_or_none()
            if document is None:
                document = self._new_document(task.class_session_id, 'card', card['title'], snapshot=snapshot_id,
                                              parent=summary['id'], artifact_key=key)
            card_payload = dict(schema_version=1, cards=[card], notes=payload['notes'])
            card_revision = self._append(document, kind='ai_candidate', body=card['front']+'\n\n'+card['back'],
                structured=card_payload, snapshot=snapshot_id, task=task.id, artifact=key, raw=raw_id)
            self._advance(document)
            documents.append(str(document['id']))
            revisions.append(str(card_revision['id']))
        return dict(summary_document_id=str(summary['id']), document_ids=documents, revision_ids=revisions)
