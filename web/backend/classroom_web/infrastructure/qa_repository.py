"""Owner-scoped immutable question preparation and result projection.

All methods run inside the caller's UoW. External model I/O is owned by the
worker. Reads and minimized receipt replay reacquire current source authority.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta
import hashlib
import hmac
import json
from uuid import UUID, uuid4

from sqlalchemy import text as sql

from ..domain.billing import ReservationRequest
from ..domain.qa import Source, ContextEntry, ContextSelection, QaRejected, validate_reply
from ..application.qa_context import select_context
from .task_sources import PgTaskSources


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      default=str, allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf8')).hexdigest()


def _stamp(value):
    return value.isoformat() if value is not None else None


class PgQaRepository:
    task_kind = 'ai_question'
    schema_version = 'pr07a-1'
    select_context = staticmethod(select_context)

    def __init__(self, tx):
        self.tx = tx
        self.connection = tx.connection
        self.owner_id = tx.catalog.owner_id

    def _rows(self, statement, **params):
        return self.connection.execute(sql(statement), dict(owner=self.owner_id, **params)).mappings()

    def _now(self):
        return self.connection.scalar(sql('SELECT clock_timestamp()'))

    def _serialize(self):
        # Serializes absent receipts, per-owner ingress capacity and plan consumption.
        self._rows("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:owner AS text)||'-qa',0))")

    def _class(self, identity):
        PgTaskSources(self.connection, self.owner_id).authorize(identity, {'schema_version': 1})
        return self.tx.catalog.get_class_session(identity)

    def _one(self, table, identity, *, lock=False):
        # Names originate exclusively from this module, never from requests.
        row = self._rows(f'SELECT * FROM {table} WHERE owner_id=:owner AND id=:id'
                         + (' FOR UPDATE' if lock else ''), id=identity).one_or_none()
        if row is None:
            raise QaRejected('SOURCE_UNAVAILABLE', 404)
        return row

    def _remember(self, operation, op_id, digest, kind, resource_id, status):
        self._rows('''INSERT INTO operation_receipts
            (id,owner_id,operation,op_id,request_hash,resource_type,resource_id,response_status,response_body)
            VALUES(:id,:owner,:operation,:op,:digest,:kind,:resource,:status,CAST(:body AS jsonb))''',
            id=uuid4(), operation=operation, op=op_id, digest=digest, kind=kind,
            resource=resource_id, status=status, body=_json({'id': str(resource_id)}))

    @staticmethod
    def _config(config):
        keys = ('destination', 'price_id', 'price_version', 'maximum_cost_microyuan',
                'input_token_budget', 'max_output_tokens', 'enabled', 'billing_required')
        if not isinstance(config, dict) or any(key not in config for key in keys):
            raise QaRejected('SERVICE_DISABLED', 503)
        value = {key: config[key] for key in keys}
        destination = value['destination']
        if (not isinstance(destination, dict)
                or set(destination) != {'provider', 'model', 'region', 'configuration_revision'}
                or any(not isinstance(item, str) or not 1 <= len(item) <= 128
                       for item in destination.values())
                or type(value['enabled']) is not bool or not value['enabled']
                or type(value['billing_required']) is not bool
                or type(value['maximum_cost_microyuan']) is not int
                or not 0 <= value['maximum_cost_microyuan'] < 2**63
                or type(value['input_token_budget']) is not int
                or not 512 <= value['input_token_budget'] <= 65536
                or type(value['max_output_tokens']) is not int
                or not 1 <= value['max_output_tokens'] <= 8192
                or not isinstance(value['price_version'], str)
                or not 1 <= len(value['price_version']) <= 64):
            raise QaRejected('SERVICE_DISABLED', 503)
        if value['billing_required']:
            try:
                value['price_id'] = str(UUID(str(value['price_id'])))
            except (ValueError, TypeError):
                raise QaRejected('SERVICE_DISABLED', 503) from None
        elif (destination['provider'] not in ('fake', 'mock')
                or value['maximum_cost_microyuan'] != 0 or value['price_id'] is not None):
            raise QaRejected('SERVICE_DISABLED', 503)
        return json.loads(_json(value))

    def _source(self, source_id, revision_id=None):
        source = self._one('qa_sources', source_id)
        version = self._one('qa_source_versions', revision_id or source['current_revision_id'])
        if version['source_id'] != source_id or version['class_session_id'] != source['class_session_id']:
            raise QaRejected('SOURCE_UNAVAILABLE', 404)
        return Source(source_id=source_id, fragment_id=source_id, revision_id=version['id'],
                      object_id=source['object_id'], class_session_id=source['class_session_id'],
                      owner_id=self.owner_id, kind=source['kind'], text=version['text'],
                      content_sha256=version['content_sha256'], start_ms=source['start_ms'],
                      end_ms=source['end_ms'], visible_at=version['created_at'], origin=source['origin'])

    @staticmethod
    def _validate_text(value):
        if (not isinstance(value, str) or not value.strip()
                or len(value.encode('utf8')) > 32768 or '\x00' in value):
            raise QaRejected('VALIDATION_FAILED', 422)
        return value

    @staticmethod
    def material_text(material):
        glossary = '\n'.join(_json(entry) for entry in material.glossary)
        return '\n'.join(part for part in (material.syllabus, glossary) if part)

    def add_source(self, class_session_id, *, kind, object_id, text, start_ms=None,
                   end_ms=None, origin='manual', asr_identity=None):
        classroom = self._class(class_session_id)
        self._serialize()
        text = self._validate_text(text)
        if kind not in ('transcript_segment', 'course_material_version'):
            raise QaRejected('VALIDATION_FAILED', 422)
        if (start_ms is None) != (end_ms is None) or (start_ms is not None and
                (type(start_ms) is not int or type(end_ms) is not int or not 0 <= start_ms < end_ms <= 2**53-1)):
            raise QaRejected('VALIDATION_FAILED', 422)
        if kind == 'course_material_version':
            if object_id != classroom.course_material_version_id or start_ms is not None:
                raise QaRejected('SOURCE_UNAVAILABLE', 404)
            material = self.tx.catalog.get_material(object_id)
            if text != self.material_text(material):
                raise QaRejected('SOURCE_UNAVAILABLE', 404)
            origin = 'material'
        if origin not in ('manual', 'material', 'live_asr', 'import', 'retranscription'):
            raise QaRejected('VALIDATION_FAILED', 422)
        if origin != 'retranscription':
            count = self._rows('''SELECT count(*) AS n FROM qa_sources WHERE owner_id=:owner
                AND class_session_id=:classroom AND origin<>'retranscription' ''',
                classroom=class_session_id).one()['n']
            if count >= 512:
                raise QaRejected('SOURCE_LIMIT', 429)
        if asr_identity is not None and (origin not in ('live_asr', 'retranscription') or kind != 'transcript_segment'):
            raise QaRejected('VALIDATION_FAILED', 422)
        source_id, revision_id = asr_identity if asr_identity is not None else (uuid4(), uuid4())
        self._rows('''INSERT INTO qa_sources(id,owner_id,class_session_id,kind,object_id,current_revision_id,origin,start_ms,end_ms)
            VALUES(:id,:owner,:classroom,:kind,:object,:version,:origin,:start,:end)''',
            id=source_id, classroom=class_session_id, kind=kind, object=object_id,
            version=revision_id, origin=origin, start=start_ms, end=end_ms)
        self._rows('''INSERT INTO qa_source_versions(id,owner_id,source_id,class_session_id,revision,text,content_sha256)
            VALUES(:id,:owner,:source,:classroom,1,:text,:digest)''', id=revision_id, source=source_id,
            classroom=class_session_id, text=text, digest=hashlib.sha256(text.encode('utf8')).hexdigest())
        return self._source(source_id)

    def revise_source(self, source_id, text):
        initial = self._one('qa_sources', source_id)
        self._class(initial['class_session_id'])
        source = self._one('qa_sources', source_id, lock=True)
        if source['kind'] != 'transcript_segment':
            raise QaRejected('SOURCE_IMMUTABLE', 409)
        text = self._validate_text(text)
        previous = self._one('qa_source_versions', source['current_revision_id'])
        if previous['revision'] >= 100:
            raise QaRejected('SOURCE_LIMIT', 429)
        identity = uuid4()
        self._rows('''INSERT INTO qa_source_versions(id,owner_id,source_id,class_session_id,revision,text,content_sha256)
            VALUES(:id,:owner,:source,:classroom,:revision,:text,:digest)''', id=identity,
            source=source_id, classroom=source['class_session_id'], revision=previous['revision']+1,
            text=text, digest=hashlib.sha256(text.encode('utf8')).hexdigest())
        self._rows('UPDATE qa_sources SET current_revision_id=:revision WHERE owner_id=:owner AND id=:id',
                   id=source_id, revision=identity)
        return self._source(source_id)

    def revoke_source(self, source_id):
        initial = self._one('qa_sources', source_id)
        self._class(initial['class_session_id'])
        self._one('qa_sources', source_id, lock=True)
        self._rows('SELECT classroom_web_revoke_qa_source(:id)', id=source_id).one()

    def _snapshot_row(self, snapshot_id):
        snapshot = self._one('source_snapshots', snapshot_id)
        classroom = self._class(snapshot['class_session_id'])
        if classroom.course_material_version_id != snapshot['course_material_version_id']:
            raise QaRejected('SOURCE_UNAVAILABLE', 409)
        # Shared locks serialize read/send/publication against source revoke/revision.
        rows = self._rows('''SELECT id FROM qa_sources WHERE owner_id=:owner
            AND id=ANY(:ids) ORDER BY id FOR SHARE''', ids=snapshot['source_ids']).all()
        if {row['id'] for row in rows} != set(snapshot['source_ids']):
            raise QaRejected('SOURCE_UNAVAILABLE', 404)
        return snapshot

    def _selection(self, snapshot_id):
        snapshot = self._snapshot_row(snapshot_id)
        rows = self._rows('''SELECT * FROM source_items WHERE owner_id=:owner AND snapshot_id=:id
            ORDER BY length(source_key),source_key''', id=snapshot_id).all()
        if not rows or {row['source_id'] for row in rows} != set(snapshot['source_ids']):
            raise QaRejected('SOURCE_UNAVAILABLE', 409)
        entries = tuple(ContextEntry(alias=row['source_key'],
                        source=self._source(row['source_id'], row['revision_id']),
                        excerpt=row['frozen_text'], excerpt_start=row['excerpt_start'],
                        excerpt_end=row['excerpt_end'], token_bound=row['token_bound']) for row in rows)
        coverage = snapshot['coverage']
        return ContextSelection(entries=entries, token_bound=coverage['token_bound'],
                                scope_complete=coverage['scope_complete'], reasons=tuple(coverage['reasons']))

    def prepare(self, class_session_id, request, op_id, config):
        config = self._config(config)
        classroom = self._class(class_session_id)
        self._serialize()
        digest = _hash({'class_session_id': str(class_session_id), 'request': request})
        old = self.tx.receipts.lock_and_get('prepareGeneration', op_id, digest)
        if old:
            return self.plan(old['resource_id'])
        if (request.get('kind') != self.task_kind or request.get('include_images') is not False
                or request.get('scope') not in ('all', 'recent30', 'recent120', 'range')):
            raise QaRejected('UNSUPPORTED_SCOPE', 422)
        question = self.prompt(request)
        if not isinstance(question, str) or not question.strip() or len(question) > 12000:
            raise QaRejected('VALIDATION_FAILED', 422)
        count = self._rows('''SELECT count(*) AS n FROM operation_receipts WHERE owner_id=:owner
            AND operation='prepareGeneration' AND created_at>clock_timestamp()-interval '1 minute' ''').one()['n']
        if count >= 12:
            raise QaRejected('RATE_LIMITED', 429)
        asked_at = self._now()
        transcript_filter = self.transcript_filter(classroom)
        source_rows = self._rows(f'''SELECT s.id FROM qa_sources s
            WHERE s.owner_id=:owner AND s.class_session_id=:classroom AND {transcript_filter}
            ORDER BY s.id LIMIT 513 FOR SHARE''', classroom=class_session_id,
            adopted=classroom.adopted_transcript_version_id).all()
        sources = [self._source(row['id']) for row in source_rows]
        if len(sources) > 512:
            raise QaRejected('SOURCE_LIMIT', 429)
        by_id = {str(source.source_id): source for source in sources}
        seen = request.get('seen_sources', [])
        if not isinstance(seen, list) or len(seen) > 512:
            raise QaRejected('VALIDATION_FAILED', 422)
        for reference in seen:
            source = by_id.get(reference.get('id'))
            if (source is None or reference.get('kind') != source.kind
                    or reference.get('revision_id') != str(source.revision_id)
                    or reference.get('content_sha256') != source.content_sha256):
                raise QaRejected('SOURCE_CHANGED', 409)
        # Empty seen_sources asks the server to choose from the authorized classroom.
        # An explicit list further narrows authorization and cannot inject text.
        if seen:
            sources = [by_id[reference['id']] for reference in seen]
        known_end = max((source.end_ms for source in sources if source.end_ms is not None), default=0)
        anchor = request.get('anchor_ms', known_end)
        self.validate_target(class_session_id, request)
        selection = self.select_context(sources, question=question, owner_id=self.owner_id,
            class_session_id=class_session_id, asked_at=asked_at, scope=request['scope'],
            anchor_ms=anchor, start_ms=request.get('start_ms'), end_ms=request.get('end_ms'),
            input_token_budget=config['input_token_budget'], max_output_tokens=config['max_output_tokens'])
        if not selection.scope_complete and not request.get('accept_partial_scope', False):
            raise QaRejected('CONTEXT_INSUFFICIENT', 409)
        snapshot_id, plan_id = uuid4(), uuid4()
        entries = [self._item_dto(entry) for entry in selection.entries]
        scope = dict(scope=request['scope'], anchor_ms=anchor, start_ms=request.get('start_ms'),
                     end_ms=request.get('end_ms'), asked_at=asked_at.isoformat())
        coverage = dict(token_bound=selection.token_bound, scope_complete=selection.scope_complete,
                        reasons=list(selection.reasons))
        snapshot_hash = _hash(dict(scope=scope, items=entries, coverage=coverage))
        self._rows('''INSERT INTO source_snapshots(id,owner_id,class_session_id,course_material_version_id,
            scope,snapshot_hash,coverage,source_ids) VALUES(:id,:owner,:classroom,:material,
            CAST(:scope AS jsonb),:digest,CAST(:coverage AS jsonb),:sources)''', id=snapshot_id,
            classroom=class_session_id, material=classroom.course_material_version_id,
            scope=_json(scope), digest=snapshot_hash, coverage=_json(coverage),
            sources=sorted({entry.source.source_id for entry in selection.entries}))
        for entry in selection.entries:
            self._rows('''INSERT INTO source_items(id,owner_id,snapshot_id,class_session_id,source_id,revision_id,
                source_key,frozen_text,source_hash,excerpt_sha256,excerpt_start,excerpt_end,token_bound)
                VALUES(:id,:owner,:snapshot,:classroom,:source,:version,:alias,:excerpt,:digest,:excerpt_hash,:start,:end,:tokens)''',
                id=uuid4(), snapshot=snapshot_id, classroom=class_session_id, source=entry.source.source_id,
                version=entry.source.revision_id, alias=entry.alias, excerpt=entry.excerpt,
                digest=entry.source.content_sha256, excerpt_hash=hashlib.sha256(entry.excerpt.encode('utf8')).hexdigest(),
                start=entry.excerpt_start, end=entry.excerpt_end, tokens=entry.token_bound)
        plan_hash = _hash(dict(snapshot_hash=snapshot_hash, request=request, config=config))
        self._rows('''INSERT INTO generation_plans(id,owner_id,class_session_id,snapshot_id,kind,plan_hash,request,config,expires_at)
            VALUES(:id,:owner,:classroom,:snapshot,:kind,:digest,CAST(:request AS jsonb),CAST(:config AS jsonb),:expires)''',
            id=plan_id, classroom=class_session_id, snapshot=snapshot_id, digest=plan_hash,
            kind=self.task_kind, request=_json(request), config=_json(config), expires=asked_at+timedelta(minutes=10))
        self._remember('prepareGeneration', op_id, digest, 'generation_plan', plan_id, 201)
        self.prepared_plan(plan_id, selection, request, config)
        return self.plan(plan_id)

    def prepared_plan(self, plan_id, selection, request, config):
        """Persist specialized execution units in the same preparation transaction."""

    def prompt(self, request):
        return request.get('question')

    def validate_target(self, class_session_id, request):
        """Specialized generators can authorize a frozen destination document."""

    def _plan_row(self, plan_id, *, lock=False):
        row = self._one('generation_plans', plan_id, lock=lock)
        if row['kind'] != self.task_kind:
            raise QaRejected('SOURCE_UNAVAILABLE', 404)
        return row

    @staticmethod
    def _item_dto(entry):
        source = entry.source
        return dict(alias=entry.alias, kind=source.kind, source_id=str(source.source_id),
                    fragment_id=str(source.fragment_id or source.source_id), object_id=str(source.object_id),
                    revision_id=str(source.revision_id), class_session_id=str(source.class_session_id),
                    content_sha256=source.content_sha256, excerpt=entry.excerpt,
                    excerpt_sha256=hashlib.sha256(entry.excerpt.encode('utf8')).hexdigest(),
                    excerpt_start=entry.excerpt_start, excerpt_end=entry.excerpt_end,
                    start_ms=source.start_ms, end_ms=source.end_ms, visible_at=_stamp(source.visible_at),
                    origin=source.origin)

    def snapshot(self, snapshot_id):
        row = self._snapshot_row(snapshot_id)
        selection = self._selection(snapshot_id)
        items = [self._item_dto(entry) for entry in selection.entries]
        starts = [item['start_ms'] for item in items if item['start_ms'] is not None]
        ends = [item['end_ms'] for item in items if item['end_ms'] is not None]
        return dict(id=str(row['id']), class_session_id=str(row['class_session_id']),
                    snapshot_sha256=row['snapshot_hash'], source_refs=[dict(kind=item['kind'],
                        id=item['source_id'], revision_id=item['revision_id'], content_sha256=item['content_sha256']) for item in items],
                    start_ms=min(starts) if starts else None, end_ms=max(ends) if ends else None,
                    scope_complete=selection.scope_complete, missing_reasons=list(selection.reasons),
                    created_at=_stamp(row['created_at']), items=items)

    @staticmethod
    def transcript_filter(classroom):
        # A null pointer keeps the existing source selection and also works
        # during the historical PR-07a migration and rollback checks.
        if classroom.adopted_transcript_version_id is None:
            return "s.origin<>'retranscription'"
        return 'classroom_web_current_transcript(s.owner_id,s.class_session_id,s.origin,s.id,:adopted)'

    def selection_changed(self, snapshot_id, class_id):
        classroom = self._class(class_id)
        transcript_filter = self.transcript_filter(classroom)
        return self._rows(f'''SELECT EXISTS(SELECT FROM source_items i JOIN qa_sources s
            ON (s.owner_id,s.id)=(i.owner_id,i.source_id)
            WHERE i.owner_id=:owner AND i.snapshot_id=:snapshot
              AND (i.revision_id<>s.current_revision_id OR NOT ({transcript_filter}))) AS changed''',
            snapshot=snapshot_id, adopted=classroom.adopted_transcript_version_id).one()['changed']

    def plan(self, plan_id):
        row = self._plan_row(plan_id)
        snapshot = self.snapshot(row['snapshot_id'])
        config = row['config']
        changed = self.selection_changed(row['snapshot_id'], row['class_session_id'])
        return dict(id=str(plan_id), class_session_id=str(row['class_session_id']), kind=row['kind'],
            plan_sha256=row['plan_hash'], source_snapshot=snapshot, audio_manifest_sha256=None,
            audio_chunk_ids=[], destination=config['destination'], prompt_version=self.schema_version,
            output_schema_version=self.schema_version, price_version=config['price_version'], estimated_batches=1,
            maximum_cost_microyuan=str(config['maximum_cost_microyuan']), quota_units=1,
            expires_at=_stamp(row['expires_at']), source_status='changed' if changed else 'unchanged',
            contains_images=False)

    def execute(self, plan_id, body, op_id, config):
        config = self._config(config)
        self._serialize()
        digest = _hash(dict(plan_id=str(plan_id), body=body))
        old = self.tx.receipts.lock_and_get('executeGeneration', op_id, digest)
        row = self._plan_row(plan_id)
        self._snapshot_row(row['snapshot_id'])
        row = self._plan_row(plan_id, lock=True)
        if old:
            return self.tx.tasks.get(old['resource_id'])
        if row['consumed_task_id'] is not None:
            task = self.tx.tasks.get(row['consumed_task_id'])
            self._remember('executeGeneration', op_id, digest, 'task', task.id, 202)
            return task
        if row['expires_at'] <= self._now():
            raise QaRejected('PLAN_EXPIRED', 409)
        if (row['config'] != config or body.get('plan_sha256') != row['plan_hash']
                or body.get('confirmed_destination_revision') != config['destination']['configuration_revision']):
            raise QaRejected('PLAN_CHANGED', 409)
        plan = self.plan(plan_id)
        if (body.get('confirm_send') is not True or body.get('confirm_images') is not False
                or (plan['source_status'] == 'changed' and body.get('confirm_original_snapshot') is not True)
                or (not plan['source_snapshot']['scope_complete'] and body.get('confirm_partial_scope') is not True)):
            raise QaRejected('CONFIRMATION_REQUIRED', 409)
        self.validate_target(row['class_session_id'], row['request'])
        active = self._rows('''SELECT count(*) AS n FROM tasks WHERE owner_id=:owner AND schema_version IN ('pr07a-1','pr07b-1')
            AND state IN ('queued','running','unknown','cancel_requested')''').one()['n']
        if active >= 4:
            raise QaRejected('RATE_LIMITED', 429)
        classroom = self._class(row['class_session_id'])
        reference = dict(schema_version=1, class_session_id=str(classroom.id),
                         course_material_version_id=str(classroom.course_material_version_id))
        if config['billing_required']:
            price = self.tx.billing._price(UUID(config['price_id']))
            destination = config['destination']
            if (not price.synthetic or price.provider != destination['provider']
                    or price.model != destination['model'] or price.region != destination['region']
                    or price.revision != config['price_version']):
                raise QaRejected('PLAN_CHANGED', 409)
            reservation = self.tx.billing.reserve(ReservationRequest(op_id=plan_id, purpose=self.task_kind,
                price_id=UUID(config['price_id']), maximum_units={'input_tokens': config['input_token_budget'],
                    'output_tokens': config['max_output_tokens']}, task_max_microyuan=config['maximum_cost_microyuan'],
                input_ref=reference, class_session_id=classroom.id, schema_version=self.schema_version, plan_id=plan_id))
            task = self.tx.tasks.get(reservation['task_id'])
        else:
            task = self.tx.tasks.enqueue(self.task_kind, reference, op_id=plan_id,
                operation='enqueueQuestion' if self.task_kind == 'ai_question' else 'enqueueReview',
                class_session_id=classroom.id, schema_version=self.schema_version, plan_id=plan_id)
        self._rows('''UPDATE generation_plans SET consumed_task_id=:task,consumed_at=clock_timestamp()
            WHERE owner_id=:owner AND id=:id''', id=plan_id, task=task.id)
        self.created_task(task, row)
        self._remember('executeGeneration', op_id, digest, 'task', task.id, 202)
        return task

    def created_task(self, task, plan):
        self._rows('''INSERT INTO questions(id,owner_id,class_session_id,snapshot_id,task_id,prompt)
            VALUES(:id,:owner,:classroom,:snapshot,:task,:prompt)''', id=uuid4(), classroom=task.class_session_id,
            snapshot=plan['snapshot_id'], task=task.id, prompt=plan['request']['question'])

    def cancel(self, task_id, op_id):
        return self.tx.tasks.cancel(task_id, op_id)

    def reconcile(self, task_id, op_id):
        return self.tx.tasks.reconcile_result(task_id, op_id)

    def synchronization_ids(self, task_id):
        return [task_id]

    def worker_input(self, task_id):
        task = self.tx.tasks.get(task_id)
        if task.schema_version != self.schema_version or task.plan_id is None:
            raise QaRejected('SOURCE_UNAVAILABLE', 404)
        row = self._plan_row(task.plan_id)
        if task.class_session_id != row['class_session_id'] or row['consumed_task_id'] != task_id:
            raise QaRejected('SOURCE_UNAVAILABLE', 404)
        selection = self._selection(row['snapshot_id'])
        reservation_id = None
        if row['config']['billing_required']:
            reservation = self.tx.billing.for_task(task_id)
            if reservation is None:
                raise QaRejected('BILLING_UNAVAILABLE', 503)
            reservation_id = reservation['id']
        return dict(question=self.prompt(row['request']), request=row['request'], selection=selection,
                    config=row['config'], destination=row['config']['destination'], reservation_id=reservation_id)

    def validator(self, task_id):
        task = self.tx.tasks.get(task_id)
        row = self._one('generation_plans', task.plan_id)
        selection = self._selection(row['snapshot_id'])
        return lambda raw: validate_reply(raw, selection)

    def finish(self, task, payload):
        plan = self._one('generation_plans', task.plan_id)
        selection = self._selection(plan['snapshot_id'])
        aliases = {entry.alias for entry in selection.entries}
        answer, citations = payload.get('answer_markdown'), payload.get('source_ids')
        if (not isinstance(answer, str) or not answer or len(answer) > 120000
                or not isinstance(citations, list) or not citations or len(citations) != len(set(citations))
                or not set(citations) <= aliases):
            raise ValueError('invalid question result')
        row = self._rows('SELECT * FROM questions WHERE owner_id=:owner AND task_id=:id FOR UPDATE', id=task.id).one()
        if row['answer_markdown'] is not None:
            if row['answer_markdown'] != answer or row['cited_source_ids'] != citations:
                raise ValueError('question result is immutable')
            return
        self._rows('''UPDATE questions SET answer_markdown=:answer,cited_source_ids=CAST(:citations AS jsonb),
            revision=revision+1,updated_at=clock_timestamp() WHERE owner_id=:owner AND id=:id''',
            id=row['id'], answer=answer, citations=_json(citations))

    def question(self, question_id):
        row = self._one('questions', question_id)
        self._snapshot_row(row['snapshot_id'])
        task = self.tx.tasks.get(row['task_id'])
        return dict(id=str(row['id']), revision=row['revision'], created_at=_stamp(row['created_at']),
            updated_at=_stamp(row['updated_at']), class_session_id=str(row['class_session_id']),
            task_id=str(row['task_id']), source_snapshot_id=str(row['snapshot_id']), question=row['prompt'],
            answer_markdown=row['answer_markdown'], state=task.state, understood=row['understood'],
            cited_source_ids=row['cited_source_ids'])

    def questions(self, class_id, limit=50, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise QaRejected('VALIDATION_FAILED', 422)
        if class_id is not None:
            self._class(class_id)
        previous, created = None, None
        if cursor is not None:
            previous, created = self._decode_cursor(cursor, class_id)
        rows = self._rows('''SELECT id,created_at FROM questions WHERE owner_id=:owner
            AND (CAST(:classroom AS uuid) IS NULL OR class_session_id=:classroom)
            AND (CAST(:created AS timestamptz) IS NULL OR (created_at,id)>(:created,CAST(:previous AS uuid)))
            ORDER BY created_at,id LIMIT :limit''',
            classroom=class_id, previous=previous, created=created, limit=limit+1).all()
        return dict(items=[self.question(row['id']) for row in rows[:limit]],
                    next_cursor=self._cursor(rows[limit-1], class_id) if len(rows)>limit else None)

    def _cursor_secret(self):
        secret = self.tx.events.secret
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise QaRejected('SERVICE_DISABLED', 503)
        return secret

    def _cursor(self, row, class_id):
        body = _json(dict(v=1, owner=str(self.owner_id), classroom=str(class_id) if class_id else None,
                          created_at=_stamp(row['created_at']), id=str(row['id']))).encode('utf8')
        signature = hmac.digest(self._cursor_secret(), b'qa-page-v1\x00'+body, 'sha256')
        return base64.urlsafe_b64encode(body+b'.'+signature).decode('ascii').rstrip('=')

    def _decode_cursor(self, cursor, class_id):
        secret = self._cursor_secret()
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 512:
                raise ValueError()
            raw = base64.b64decode(cursor+'='*(-len(cursor)%4), altchars=b'-_', validate=True)
            body, signature = raw[:-33], raw[-32:]
            if (raw[-33:-32] != b'.' or not hmac.compare_digest(signature,
                    hmac.digest(secret, b'qa-page-v1\x00'+body, 'sha256'))):
                raise ValueError()
            value = json.loads(body)
            if (set(value) != {'v', 'owner', 'classroom', 'created_at', 'id'} or value['v'] != 1
                    or value['owner'] != str(self.owner_id)
                    or value['classroom'] != (str(class_id) if class_id else None)):
                raise ValueError()
            identity, created = UUID(value['id']), datetime.fromisoformat(value['created_at'])
            if identity.version != 4 or created.tzinfo is None:
                raise ValueError()
            return identity, created
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise QaRejected('VALIDATION_FAILED', 422) from None

    def task_view(self, task_id):
        task = self.tx.tasks.get(task_id)
        if task.schema_version != self.schema_version:
            raise QaRejected('SOURCE_UNAVAILABLE', 404)
        plan = self._plan_row(task.plan_id)
        self._snapshot_row(plan['snapshot_id'])
        result_id = self.result_id(task_id)
        attempt = self.tx.tasks._attempt(task_id)
        settled = None
        if plan['config']['billing_required']:
            reservation = self.tx.billing.for_task(task_id)
            if reservation and reservation['state'] == 'settled':
                settled = str(reservation['settled_microyuan'])
        return dict(id=str(task.id), revision=task.revision, created_at=_stamp(task.created_at),
            updated_at=_stamp(task.updated_at), class_session_id=str(task.class_session_id), kind=task.kind,
            state=task.state, attempt_id=str(attempt.id) if attempt else None,
            parent_task_id=str(task.parent_task_id) if task.parent_task_id else None,
            completed_units=1 if task.state=='succeeded' else 0, total_units=1, error_code=task.error_code,
            result_id=str(result_id) if result_id and task.state=='succeeded' else None,
            source_snapshot_id=str(plan['snapshot_id']), provider_outcome=attempt.provider_outcome if attempt else 'not_submitted',
            maximum_cost_microyuan=str(plan['config']['maximum_cost_microyuan']), settled_cost_microyuan=settled,
            may_have_charged=task.may_have_charged)

    def result_id(self, task_id):
        row = self._rows('SELECT id FROM questions WHERE owner_id=:owner AND task_id=:task', task=task_id).one_or_none()
        return row['id'] if row else None
