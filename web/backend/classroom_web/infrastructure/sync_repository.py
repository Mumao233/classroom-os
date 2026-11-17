"""Typed offline commands with immutable receipts and explicit conflict decisions."""
import hashlib
from uuid import UUID, uuid4

from sqlalchemy import text as sql

from ..domain.data import Gone, NotFound, Resource, RevisionConflict
from ..domain.qa import QaRejected
from ..domain.policies import bounded_text, language_value
from .qa_repository import _hash, _json, _stamp
from .review_repository import PgReviewRepository


DEFAULT_PREFERENCES = dict(theme='system', reduced_effects=False, reading_font_px=17,
                           explanation_language='auto', automatic_explanation=False)
SYNC_TABLES = frozenset(('sync_operations', 'sync_conflicts', 'sync_resolutions', 'sync_preferences', 'sync_marks'))


class PgSyncRepository:
    def __init__(self, tx):
        self.tx = tx
        self.owner = tx.catalog.owner_id
        self.review = PgReviewRepository(tx)

    def rows(self, statement, **params):
        return self.tx.connection.execute(sql(statement), dict(owner=self.owner, **params)).mappings()

    def serialize(self):
        self.rows("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:owner AS text)||'-sync',0))")

    def preferences(self):
        row = self.rows('SELECT * FROM sync_preferences WHERE owner_id=:owner').one_or_none()
        if row is None:
            raise QaRejected('STORAGE_UNAVAILABLE',503)
        if not isinstance(row['value'],dict):
            raise QaRejected('INCOMPATIBLE_CLIENT',409)
        value = {key:row['value'].get(key,default) for key,default in DEFAULT_PREFERENCES.items()}
        if (value['theme'] not in ('system','day','night')
                or type(value['reduced_effects']) is not bool
                or type(value['reading_font_px']) is not int or not 14 <= value['reading_font_px'] <= 32
                or value['explanation_language'] not in ('auto','zh','en')
                or type(value['automatic_explanation']) is not bool):
            raise QaRejected('INCOMPATIBLE_CLIENT',409)
        return dict(value,revision=row['revision'])

    def course(self, identity):
        course = self.tx.catalog.get_course(identity)
        counts = self.rows('''SELECT count(*) AS count,max(started_at) AS last FROM class_sessions
            WHERE owner_id=:owner AND course_id=:id AND deleted_at IS NULL''', id=identity).one()
        return dict(id=str(course.id), revision=course.revision, created_at=_stamp(course.created_at),
                    updated_at=_stamp(course.updated_at), semester_id=str(course.semester_id), name=course.name,
                    english_name=course.english_name or '', language=course.language, teacher=course.teacher or '',
                    room=course.room or '', color_token=course.color_token, icon_token=course.icon_key,
                    archived=course.archived_at is not None, class_session_count=counts['count'],
                    last_class_session_at=_stamp(counts['last']))

    def mark(self, identity, *, lock=False):
        row = self.rows('SELECT * FROM sync_marks WHERE owner_id=:owner AND id=:id', id=identity).one_or_none()
        if row is None:
            raise NotFound('mark unavailable')
        self.review.sources._class(row['class_session_id'])
        self.tx.catalog._load(Resource.CLASS_SESSION, row['class_session_id'], share=lock)
        if lock:
            row = self.rows('SELECT * FROM sync_marks WHERE owner_id=:owner AND id=:id FOR UPDATE', id=identity).one()
        return dict(id=str(row['id']), revision=row['revision'], created_at=_stamp(row['created_at']),
                    updated_at=_stamp(row['updated_at']), class_session_id=str(row['class_session_id']),
                    kind=row['kind'], anchor=row['anchor'], note=row['note'], understood=row['understood'])

    def marks(self, classroom, cursor=None, limit=50):
        self.review.sources._class(classroom)
        filters = dict(scope='marks',classroom=str(classroom))
        previous, created = self.review._decode_cursor(cursor,filters)
        rows = self.rows('''SELECT * FROM sync_marks WHERE owner_id=:owner AND class_session_id=:classroom
            AND (CAST(:created AS timestamptz) IS NULL OR (created_at,id)>(:created,CAST(:previous AS uuid)))
            ORDER BY created_at,id LIMIT :limit''', classroom=classroom,created=created,previous=previous,limit=limit+1).all()
        return dict(items=[self.mark(row['id']) for row in rows[:limit]],
                    next_cursor=self.review._cursor(rows[limit-1],filters) if len(rows)>limit else None)

    def create_mark(self, classroom, body, key):
        self.serialize()
        self.review.sources._class(classroom)
        self.tx.catalog._load(Resource.CLASS_SESSION,classroom,share=True)
        digest = _hash(dict(class_session_id=str(classroom),body=body))
        old = self.tx.receipts.lock_and_get('createMark',key,digest)
        if old:
            return self.mark(old['resource_id'])
        self.validate_patch('mark',body)
        anchor = body['anchor']
        if anchor['kind']=='review_revision':
            revision = self.review._revision(UUID(anchor['revision_id']),UUID(anchor['document_id']))
            if revision['class_session_id']!=classroom or hashlib.sha256(revision['body_markdown'].encode('utf8')).hexdigest()!=anchor['content_sha256']:
                raise QaRejected('SOURCE_UNAVAILABLE',404)
        elif anchor['kind']=='source':
            ref = anchor['source_ref']
            source = self.rows('''SELECT s.class_session_id,s.kind,v.content_sha256 FROM qa_sources s JOIN qa_source_versions v
                ON v.owner_id=s.owner_id AND v.source_id=s.id WHERE s.owner_id=:owner AND s.id=:id AND v.id=:revision''',
                id=UUID(ref['id']),revision=UUID(ref['revision_id'])).one_or_none()
            if source is None or source['class_session_id']!=classroom or source['kind']!=ref['kind'] or source['content_sha256']!=ref['content_sha256']:
                raise QaRejected('SOURCE_UNAVAILABLE',404)
        else:
            epoch = self.rows('''SELECT id FROM capture_epochs WHERE owner_id=:owner
                AND id=:id AND class_session_id=:classroom''',id=UUID(anchor['capture_epoch_id']),classroom=classroom).one_or_none()
            if epoch is None:
                raise QaRejected('SOURCE_UNAVAILABLE',404)
        identity = uuid4()
        self.rows('''INSERT INTO sync_marks(id,owner_id,class_session_id,kind,anchor,note)
            VALUES(:id,:owner,:classroom,:kind,CAST(:anchor AS jsonb),:note)''',
            id=identity,classroom=classroom,kind=body['kind'],anchor=_json(anchor),note=body['note'])
        self.review.sources._remember('createMark',key,digest,'mark',identity,201)
        return self.mark(identity)

    def current(self, kind, identity, *, lock=False):
        if kind == 'personal_revision':
            row = self.review._document(identity, lock=lock)
            active = row['latest_personal_revision_id'] or row['adopted_revision_id']
            if active is None:
                history = self.rows('''SELECT projection FROM review_document_history WHERE owner_id=:owner
                    AND document_id=:id AND revision=:revision''',id=identity,revision=row['revision']).one_or_none()
                candidates=history['projection']['candidate_revision_ids'] if history else []
                if not candidates:
                    raise QaRejected('SOURCE_UNAVAILABLE', 404)
                active = UUID(candidates[-1])
            return row['revision'], self.review.revision(active), row['class_session_id']
        if kind == 'course':
            if lock:
                self.tx.catalog._load(Resource.COURSE, identity, write=True)
            value = self.course(identity)
            return value['revision'], value, None
        if kind == 'mark':
            value = self.mark(identity, lock=lock)
            return value['revision'], value, UUID(value['class_session_id'])
        if kind == 'preferences' and identity == self.owner:
            if lock:
                self.rows('SELECT id FROM sync_preferences WHERE owner_id=:owner FOR UPDATE').one()
            value = self.preferences()
            return value['revision'], value, None
        raise NotFound('object unavailable')

    def remember(self, operation, key, digest, kind, identity, revision, status, *, conflict=None, classroom=None):
        row = self.rows('''INSERT INTO sync_operations(id,owner_id,operation,op_id,request_hash,
            object_type,object_id,class_session_id,result_revision,conflict_id,status)
            VALUES(:id,:owner,:operation,:key,:digest,:kind,:object,:classroom,:revision,:conflict,:status)
            RETURNING *''', id=uuid4(), operation=operation, key=key, digest=digest,
            kind=kind, object=identity, classroom=classroom, revision=revision, conflict=conflict, status=status).one()
        self.review.sources._remember(operation, key, digest, 'sync_operation', row['id'], 200)
        return self.receipt_dto(row)

    def receipt_dto(self, row):
        status = row['status']
        if status not in ('rejected', 'deleted'):
            try:
                if row['object_type'] == 'personal_revision' and status == 'conflict':
                    self.review._conflict_dto(row['conflict_id'])
                else:
                    self.current(row['object_type'], row['object_id'])
            except (Gone, NotFound):
                # A minimized known receipt proves the object belonged to this owner.
                status = 'deleted'
            except QaRejected as error:
                if error.status != 404:
                    raise
                status = 'deleted'
        visible = status not in ('deleted', 'rejected')
        return dict(op_id=str(row['op_id']), operation=row['operation'], request_sha256=row['request_hash'],
                    status=status, object_id=str(row['object_id']) if visible else None,
                    revision=row['result_revision'] if visible else None,
                    conflict_id=str(row['conflict_id']) if visible and row['conflict_id'] else None,
                    task_id=None, created_at=_stamp(row['created_at']))

    def _receipt_row(self, operation, key):
        return self.rows('''SELECT * FROM sync_operations WHERE owner_id=:owner
            AND operation=:operation AND op_id=:key''', operation=operation, key=key).one_or_none()

    def receipt(self, operation, key):
        row = self._receipt_row(operation, key)
        if row is None:
            return self.legacy_receipt(operation,key)
        return self.receipt_dto(row)

    def legacy_receipt(self, operation, key):
        row = self.rows('''SELECT * FROM operation_receipts WHERE owner_id=:owner
            AND operation=:operation AND op_id=:key''',operation=operation,key=key).one_or_none()
        if row is None or row['resource_type'] not in ('review_document','review_revision','review_conflict','task','mark','sync_batch'):
            raise NotFound('operation unavailable')
        result = dict(op_id=str(key),operation=operation,request_sha256=row['request_hash'],status='applied',
                      object_id=None,revision=None,conflict_id=None,task_id=None,created_at=_stamp(row['created_at']))
        identity,kind = row['resource_id'],row['resource_type']
        try:
            if kind=='review_document':
                self.review._document(identity)
                result['object_id']=str(identity)
            elif kind=='review_revision':
                revision=self.review._revision(identity)
                result.update(object_id=str(revision['document_id']),revision=revision['revision']+1)
            elif kind=='review_conflict':
                conflict=self.review._conflict_dto(identity)
                result.update(status='conflict',conflict_id=str(identity),revision=conflict['current_revision'],
                              object_id=conflict['submitted_revision']['document_id'])
            elif kind=='mark':
                self.mark(identity)
                result['object_id']=str(identity)
            elif kind=='task':
                self.tx.tasks.get(identity)
                result['task_id']=str(identity)
            else:
                result['status']='pending'
        except (Gone,NotFound):
            result['status']='deleted'
        except QaRejected as error:
            if error.status!=404:
                raise
            result['status']='deleted'
        return result

    def record_conflict(self, operation, revision, content, classroom, *, identity=None):
        identity = identity or uuid4()
        self.rows('''INSERT INTO sync_conflicts(id,owner_id,object_type,object_id,class_session_id,
            base_revision,server_revision,local_operation,server_content)
            VALUES(:id,:owner,:kind,:object,:classroom,:base,:revision,CAST(:local AS jsonb),CAST(:server AS jsonb))
            ON CONFLICT(owner_id,id) DO NOTHING''', id=identity, kind=operation['object_type'],
            object=UUID(operation['object_id']), classroom=classroom, base=operation['base_revision'],
            revision=revision, local=_json(operation), server=_json(content))
        return identity

    @staticmethod
    def validate_patch(kind,payload):
        limits = {
            'course':dict(name=160,english_name=200,teacher=100,room=100,color_token=64,icon_token=64),
            'mark':dict(note=2000),
            'personal_revision':dict(body_markdown=120000,personal_note=12000),
        }
        try:
            for field,limit in limits.get(kind,{}).items():
                if field in payload:
                    bounded_text(payload[field],limit,nonempty=kind=='course' and field=='name')
            if kind=='course' and 'language' in payload:
                language_value(payload['language'])
        except ValueError:
            raise QaRejected('VALIDATION_FAILED',422) from None

    def conflict(self, identity):
        reference = self.rows('''SELECT object_type,object_id,server_revision,server_content->>'id' AS cloud_id
            FROM sync_conflicts WHERE owner_id=:owner AND id=:id''', id=identity).one_or_none()
        if reference is None:
            original = self.review._conflict_row(identity)
            if original is not None:
                synced = self.rows('''SELECT 1 FROM sync_operations WHERE owner_id=:owner AND conflict_id=:id
                    LIMIT 1''', id=identity).first()
                if synced and self.review._conflict_cloud(original) is None:
                    raise NotFound('conflict unavailable')
                raise QaRejected('INCOMPATIBLE_CLIENT', 409)
            raise NotFound('conflict unavailable')
        if reference['object_type'] == 'personal_revision':
            original = self.review._conflict_row(identity)
            if (original is None or original['document_id'] != reference['object_id']
                    or original['current_document_revision'] != reference['server_revision']):
                raise NotFound('conflict unavailable')
            self.review._revision(original['submitted_revision_id'], original['document_id'])
            cloud = self.review._conflict_cloud(original)
            if cloud is None or cloud['id'] != reference['cloud_id']:
                raise NotFound('conflict unavailable')
        else:
            self.current(reference['object_type'], reference['object_id'])
        row = self.rows('SELECT * FROM sync_conflicts WHERE owner_id=:owner AND id=:id', id=identity).one_or_none()
        if row is None:
            raise NotFound('conflict unavailable')
        resolved = self.rows('SELECT id FROM sync_resolutions WHERE owner_id=:owner AND conflict_id=:id', id=identity).one_or_none()
        return dict(id=str(identity), object_id=str(row['object_id']), base_revision=row['base_revision'],
                    server_revision=row['server_revision'], local_operation=row['local_operation'],
                    server_content=row['server_content'], resolved=resolved is not None)

    def apply(self, operation, *, operation_name='syncOperation'):
        self.serialize()
        key = UUID(operation['op_id'])
        kind, identity = operation['object_type'], UUID(operation['object_id'])
        payload = operation['payload']
        if (operation['base_revision'] != payload['base_revision']
                or kind == 'personal_revision' and operation['draft_id'] != payload['draft_id']):
            raise QaRejected('VALIDATION_FAILED', 422)
        digest = _hash(operation)
        old = self.tx.receipts.lock_and_get(operation_name, key, digest)
        stored = None
        if old:
            if kind != 'personal_revision':
                return self.receipt(operation_name, key)
            stored = self._receipt_row(operation_name, key)
            if stored is None:
                return self.legacy_receipt(operation_name, key)
            if stored['status'] != 'conflict':
                return self.receipt_dto(stored)

        original = None
        if kind == 'personal_revision':
            # The same owner lock serializes direct PR-07b writes. Check their
            # receipt before considering any current cloud content.
            self.review._serialize()
            _, original = self.review._personal_receipt(identity, payload, key)
        historical_conflict = original is not None and original['resource_type'] == 'review_conflict'
        if stored is not None:
            if (historical_conflict and original['resource_id'] == stored['conflict_id']
                    and not self.rows('SELECT 1 FROM sync_conflicts WHERE owner_id=:owner AND id=:id',
                                      id=stored['conflict_id']).first()):
                # A first replay may have recorded the receipt while its
                # original comparison was unavailable. Retry only that backfill.
                self.review.write_personal(identity, payload, key)
            return self.receipt_dto(stored)

        self.validate_patch(kind,payload)
        try:
            if historical_conflict:
                classroom = self.review._document(identity, lock=True)['class_session_id']
            else:
                revision, content, classroom = self.current(kind, identity, lock=True)
        except Gone:
            return self.remember(operation_name, key, digest, kind, identity, None, 'deleted')
        except (NotFound, QaRejected) as error:
            if isinstance(error,QaRejected) and error.status != 404:
                raise
            owned = self.rows('SELECT classroom_web_sync_owned_object(:kind,:id) AS owned',kind=kind,id=identity).one()['owned']
            if not owned:
                raise
            return self.remember(operation_name, key, digest, kind, identity, None, 'deleted')
        if kind == 'personal_revision':
            value = self.review.write_personal(identity, payload, key)
            updated = value['current_revision'] if 'conflict_id' in value else value['revision']+1
            conflict = UUID(value['conflict_id']) if 'conflict_id' in value else None
            return self.remember(operation_name, key, digest, kind, identity, updated,
                                 'conflict' if conflict else 'applied', conflict=conflict, classroom=classroom)
        if revision != operation['base_revision']:
            conflict = self.record_conflict(operation, revision, content, classroom)
            return self.remember(operation_name, key, digest, kind, identity, revision,
                                 'conflict', conflict=conflict, classroom=classroom)
        if kind == 'course':
            fields = {key: value for key, value in payload.items() if key != 'base_revision'}
            if 'semester_id' in fields:
                fields['semester_id'] = UUID(fields['semester_id'])
                self.tx.catalog._load(Resource.SEMESTER, fields['semester_id'], share=True)
            if 'icon_token' in fields:
                fields['icon_key'] = fields.pop('icon_token')
            if 'archived' in fields:
                fields['archived_at'] = self.rows('SELECT clock_timestamp() AS now').one()['now'] if fields.pop('archived') else None
            self.tx.catalog._cas(Resource.COURSE, identity, revision, **fields)
        elif kind == 'mark':
            value = dict(content, **{key: value for key, value in payload.items() if key != 'base_revision'})
            self.rows('''UPDATE sync_marks SET note=:note,understood=:understood,revision=revision+1,
                updated_at=clock_timestamp() WHERE owner_id=:owner AND id=:id AND revision=:revision RETURNING revision''',
                id=identity, revision=revision, note=value['note'], understood=value['understood']).one()
        else:
            value = dict(self.rows('SELECT value FROM sync_preferences WHERE owner_id=:owner').one()['value'])
            value.update({key: value for key, value in payload.items() if key != 'base_revision'})
            self.rows('''UPDATE sync_preferences SET value=CAST(:value AS jsonb),revision=revision+1,
                updated_at=clock_timestamp() WHERE owner_id=:owner AND revision=:revision RETURNING revision''',
                revision=revision, value=_json(value)).one()
        return self.remember(operation_name, key, digest, kind, identity, revision+1, 'applied', classroom=classroom)

    def resolve(self, identity, body, key):
        self.serialize()
        digest = _hash(dict(conflict_id=str(identity), body=body))
        old = self.tx.receipts.lock_and_get('resolveConflict', key, digest)
        if old:
            return self.receipt('resolveConflict', key)
        if self.review._conflict_row(identity) is not None:
            # Personal resolution can lock the document after taking source
            # locks in conflict(). Serialize against direct Review writes first.
            self.review._serialize()
        conflict = self.conflict(identity)
        operation = conflict['local_operation']
        kind, target = operation['object_type'], UUID(operation['object_id'])
        resolution = body['resolution']
        if kind == 'personal_revision' or resolution == 'copy_to_new_note':
            self.review._serialize()
        if kind == 'course' and resolution in ('use_server','copy_to_new_note'):
            # Freeze the compared CAS without upgrading the shared ancestor lock
            # already held by concurrent Review note creation.
            self.tx.catalog._load(Resource.COURSE,target,share=True)
            revision, _, classroom = self.current(kind,target)
        else:
            revision, _, classroom = self.current(kind, target, lock=True)
        if (conflict['resolved'] or body['base_revision'] != revision
                or conflict['server_revision'] != revision):
            raise RevisionConflict(revision)
        if resolution == 'edited_merge':
            merged = body.get('merged_operation')
            if (merged is None or body.get('destination_class_session_id') is not None
                    or merged['object_type'] != kind or UUID(merged['object_id']) != target
                    or merged['base_revision'] != revision or merged['op_id'] == operation['op_id']):
                raise QaRejected('VALIDATION_FAILED', 422)
            value = self.apply(merged)
            if value['status'] != 'applied':
                raise RevisionConflict(revision)
            revision = value['revision']
        elif resolution == 'copy_to_new_note':
            if body.get('merged_operation') is not None or not body.get('destination_class_session_id'):
                raise QaRejected('VALIDATION_FAILED', 422)
            destination = UUID(body['destination_class_session_id'])
            payload = operation['payload']
            content = payload['body_markdown'] if kind=='personal_revision' else _json(payload)
            note = self.review.create_note(dict(class_session_id=str(destination), title='冲突副本',
                body_markdown=content,
                draft_id=str(uuid4())), key)
            target, classroom = UUID(note['id']), destination
            if kind=='personal_revision' and payload['personal_note']:
                self.review.write_personal(target,dict(base_revision=note['revision'],
                    base_content_revision_id=note['latest_personal_revision_id'],draft_id=str(uuid4()),
                    body_markdown=payload['body_markdown'],personal_note=payload['personal_note']),key)
            revision = self.review.document(target)['revision']
            kind = 'personal_revision'
        elif resolution == 'use_server':
            if body.get('merged_operation') is not None or body.get('destination_class_session_id') is not None:
                raise QaRejected('VALIDATION_FAILED', 422)
        else:
            raise QaRejected('VALIDATION_FAILED', 422)
        self.rows('''INSERT INTO sync_resolutions(id,owner_id,conflict_id,op_id)
            VALUES(:id,:owner,:conflict,:op)''', id=uuid4(), conflict=identity, op=key)
        return self.remember('resolveConflict', key, digest, kind, target, revision, 'applied', classroom=classroom)

    def bootstrap(self):
        projections = []
        for table, kind, parent, adopted, live in (
            ('semesters','semester','NULL::uuid','NULL::uuid',"deleted_at IS NULL AND NOT classroom_web_tombstoned('semester',id)"),
            ('courses','course','semester_id','NULL::uuid',"deleted_at IS NULL AND NOT classroom_web_tombstoned('course',id) AND EXISTS(SELECT FROM semesters s WHERE s.owner_id=courses.owner_id AND s.id=semester_id AND s.deleted_at IS NULL AND NOT classroom_web_tombstoned('semester',s.id))"),
            ('class_sessions','class_session','course_id','NULL::uuid','classroom_web_qa_class_live(id)'),
            ('review_documents','review_document','class_session_id','adopted_revision_id','true'),
            ('sync_marks','mark','class_session_id','NULL::uuid','true')):
            rows = self.rows(f'''SELECT id,revision,updated_at,{parent} AS parent,{adopted} AS adopted
                FROM {table} WHERE owner_id=:owner AND ({live}) ORDER BY id LIMIT :limit''', limit=10001-len(projections)).all()
            projections.extend(dict(object_type=kind, object_id=str(row['id']), revision=row['revision'],
                parent_id=str(row['parent']) if row['parent'] else None,
                adopted_revision_id=str(row['adopted']) if row['adopted'] else None,
                updated_at=_stamp(row['updated_at'])) for row in rows)
            if len(projections) > 9999:
                raise QaRejected('CAPACITY_EXCEEDED', 409)
        prefs = self.rows('SELECT revision,updated_at FROM sync_preferences WHERE owner_id=:owner').one()
        projections.append(dict(object_type='preferences',object_id=str(self.owner),revision=prefs['revision'],
                                parent_id=None,adopted_revision_id=None,updated_at=_stamp(prefs['updated_at'])))
        return dict(watermark=self.tx.events.watermark(), generated_at=_stamp(self.rows('SELECT clock_timestamp() AS now').one()['now']),
                    projection_complete=True, projections=projections, maximum_projection_count=10000)

    def changes(self, cursor=None, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise QaRejected('VALIDATION_FAILED',422)
        # One repeatable-read snapshot spans all internal pages.
        items, current = [], cursor
        remaining = limit
        while remaining:
            page = self.tx.events.replay(current, min(100, remaining))
            current = page.cursor
            for event in page.events:
                row = self.rows('SELECT * FROM outbox_events WHERE owner_id=:owner AND id=:id', id=UUID(event['event_id'])).one()
                if row['event_type'] == 'object.deleted':
                    request = self.rows('''SELECT coalesce(x.revision,1) AS revision FROM deletion_requests d
                        LEFT JOIN deletion_executions x ON x.owner_id=d.owner_id AND x.request_id=d.id
                        WHERE d.owner_id=:owner AND d.id=:id''', id=UUID(row['payload']['deletion_id'])).one_or_none()
                    if request:
                        items.append(dict(event_id=event['event_id'],object_type='deletion',
                            object_id=row['payload']['deletion_id'],revision=request['revision'],deleted=True))
                elif row['aggregate_revision'] is not None and row['aggregate_type'] in (
                        'semester','course','class_session','review_document','mark','preferences','task','question'):
                    deleted = False
                    if row['aggregate_type']=='review_document':
                        deleted = self.rows('SELECT id FROM review_documents WHERE owner_id=:owner AND id=:id',id=row['aggregate_id']).one_or_none() is None
                    elif row['aggregate_type']=='mark':
                        deleted = self.rows('SELECT id FROM sync_marks WHERE owner_id=:owner AND id=:id',id=row['aggregate_id']).one_or_none() is None
                    items.append(dict(event_id=event['event_id'],object_type=row['aggregate_type'],
                        object_id=str(row['aggregate_id']),revision=row['aggregate_revision'],deleted=deleted))
            remaining -= len(page.events)
            if len(page.events) < min(100, remaining+len(page.events)):
                break
        if current is None:
            current = self.tx.events.watermark()
        return dict(items=items,next_cursor=current,has_more=self.tx.events._decode(current)<self.tx.events._head())
