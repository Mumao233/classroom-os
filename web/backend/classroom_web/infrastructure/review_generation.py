"""Review generation reuses the frozen sources and plan engine from PR-07a."""
import json
import hashlib
from datetime import timedelta
from uuid import UUID, uuid4

from ..application.review_context import plan_review_context, partition_review_context
from ..domain.billing import BillingBlocked, QuotaExceeded, ReservationRequest
from ..domain.qa import QaRejected
from ..domain.tasks import fingerprint
from ..domain.review import validate_review_reply, review_batch_question, merge_review_outputs
from .qa_repository import PgQaRepository, _hash, _json
from .review_repository import PgReviewRepository


class PgReviewGenerationRepository(PgQaRepository):
    task_kind = 'review_summary'
    schema_version = 'pr07b-1'
    select_context = staticmethod(plan_review_context)

    def prompt(self, request):
        card_types = request.get('card_types', ['concept', 'derivation', 'example', 'pitfall'])
        if (not isinstance(card_types, list) or not 0 <= len(card_types) <= 4
                or any(item not in ('concept', 'derivation', 'example', 'pitfall') for item in card_types)
                or len(set(card_types)) != len(card_types)
                or type(request.get('english_answer', False)) is not bool
                or request.get('granularity', 'standard') not in ('concise', 'standard', 'detailed')):
            raise QaRejected('VALIDATION_FAILED', 422)
        return json.dumps(dict(instruction='Organize the authorized classroom sources into a summary and review cards.',
            card_types=card_types, english_answer=request.get('english_answer', False),
            granularity=request.get('granularity', 'standard')), ensure_ascii=False, separators=(',', ':'))

    def validate_target(self, class_session_id, request):
        target = request.get('target_document_id')
        if target is None:
            if 'base_revision' in request:
                raise QaRejected('VALIDATION_FAILED', 422)
            return
        document = PgReviewRepository(self.tx).document(UUID(target))
        if document['class_session_id'] != str(class_session_id) or document['kind'] != 'summary':
            raise QaRejected('SOURCE_UNAVAILABLE', 404)
        if type(request.get('base_revision')) is not int or request['base_revision'] < 1:
            raise QaRejected('VALIDATION_FAILED', 422)
        # The observed base is provenance. Publication adds a candidate and never
        # changes the user's adopted or latest-personal pointer.

    def _batches(self, plan_id):
        return self._rows('''SELECT * FROM review_batches WHERE owner_id=:owner
            AND plan_id=:plan ORDER BY batch_index''', plan=plan_id).all()

    def _partition(self, plan):
        return partition_review_context(self._selection(plan['snapshot_id']),
            question=self.prompt(plan['request']), input_token_budget=plan['config']['input_token_budget'],
            max_output_tokens=plan['config']['max_output_tokens'])

    def prepared_plan(self, plan_id, selection, request, config):
        parts = partition_review_context(selection, question=self.prompt(request),
            input_token_budget=config['input_token_budget'], max_output_tokens=config['max_output_tokens'])
        if config['maximum_cost_microyuan'] * len(parts) >= 2**63:
            raise QaRejected('INVALID_CONTEXT_BUDGET', 422)
        plan = self._plan_row(plan_id)
        for index, part in enumerate(parts):
            self._rows('''INSERT INTO review_batches(id,owner_id,plan_id,class_session_id,
                batch_index,snapshot_id,source_keys) VALUES(:id,:owner,:plan,:classroom,:index,
                :snapshot,CAST(:keys AS jsonb))''', id=uuid4(), plan=plan_id,
                classroom=plan['class_session_id'], index=index, snapshot=plan['snapshot_id'],
                keys=_json([entry.alias for entry in part.entries]))

    def plan(self, plan_id):
        value = super().plan(plan_id)
        count = len(self._batches(plan_id)) or 1
        value['estimated_batches'] = count
        value['maximum_cost_microyuan'] = str(int(value['maximum_cost_microyuan']) * count)
        return value

    def created_task(self, task, plan):
        self._rows('''UPDATE review_batches SET task_id=:task,child_plan_id=:plan
            WHERE owner_id=:owner AND plan_id=:plan AND batch_index=0''', task=task.id, plan=plan['id'])

    def _batch_input(self, task_id):
        batch = self._rows('SELECT * FROM review_batches WHERE owner_id=:owner AND task_id=:task',
                           task=task_id).one()
        plan = self._plan_row(batch['plan_id'])
        parts = self._partition(plan)
        selected = parts[batch['batch_index']]
        if [entry.alias for entry in selected.entries] != batch['source_keys']:
            raise QaRejected('PLAN_CHANGED', 409)
        return plan, selected, review_batch_question(self.prompt(plan['request']), batch['batch_index'], len(parts))

    def validator(self, task_id):
        task = self.tx.tasks.get(task_id)
        plan, selection, question = self._batch_input(task_id)
        limits = json.loads(question)
        requested_types = set(plan['request'].get('card_types', ['concept', 'derivation', 'example', 'pitfall']))

        def validate(raw):
            payload = validate_review_reply(raw, selection)
            if len(payload['cards']) > limits['max_cards'] or len(payload['notes']) > limits['max_notes']:
                raise ValueError('batch output exceeds its frozen share of the output budget')
            if any(card['type'] not in requested_types for card in payload['cards']):
                raise ValueError('unrequested card type')
            if not plan['request'].get('english_answer', False) and any(
                    card['english'] or card['english_meaning'] for card in payload['cards']):
                raise ValueError('unrequested English answer')
            return payload

        return validate

    def worker_input(self, task_id):
        values = super().worker_input(task_id)
        task = self.tx.tasks.get(task_id)
        self.validate_target(task.class_session_id, values['request'])
        _, values['selection'], values['question'] = self._batch_input(task_id)
        return values

    def finish(self, task, payload, *, raw_output_ref=None):
        plan = self._plan_row(task.plan_id)
        self._snapshot_row(plan['snapshot_id'])
        self.validate_target(task.class_session_id, plan['request'])
        if len(self._batches(plan['id'])) > 1:
            return dict(awaiting_batches=True)
        return PgReviewRepository(self.tx).publish(task, payload, plan['snapshot_id'], plan['request'],
                                                  raw_output_ref=raw_output_ref)

    def _completed_batch(self, task_id):
        if task_id is None:
            return None
        return self._rows('''SELECT r.* FROM provider_results r JOIN task_attempts a
            ON a.owner_id=r.owner_id AND a.id=r.attempt_id WHERE r.owner_id=:owner
            AND a.task_id=:task AND r.validation_status='valid' AND r.conflict_sha256 IS NULL
            ORDER BY a.attempt_number DESC LIMIT 1''', task=task_id).one_or_none()

    def _lock_batch_tasks(self, batches):
        for batch in batches[1:]:
            if batch['task_id'] is not None:
                self.tx.tasks._load(batch['task_id'], lock=True)

    def _has_conflict(self, plan_id):
        return self._rows('''SELECT 1 FROM provider_results r JOIN task_attempts a
            ON a.owner_id=r.owner_id AND a.id=r.attempt_id JOIN review_batches b
            ON b.owner_id=a.owner_id AND b.task_id=a.task_id
            WHERE b.owner_id=:owner AND b.plan_id=:plan AND r.conflict_sha256 IS NOT NULL
            LIMIT 1''', plan=plan_id).first() is not None

    def _create_batch(self, root, plan, batch):
        identity = uuid4()
        self._rows('''INSERT INTO generation_plans(id,owner_id,class_session_id,snapshot_id,kind,
            plan_hash,request,config,expires_at) VALUES(:id,:owner,:classroom,:snapshot,'review_summary',
            :digest,CAST(:request AS jsonb),CAST(:config AS jsonb),:expires)''', id=identity,
            classroom=root.class_session_id, snapshot=plan['snapshot_id'],
            digest=_hash(dict(parent=plan['plan_hash'], batch_index=batch['batch_index'])),
            request=_json(plan['request']), config=_json(plan['config']), expires=self._now()+timedelta(minutes=10))
        reference = dict(root.input_ref)
        config = plan['config']
        if config['billing_required']:
            reservation = self.tx.billing.for_task(root.id)
            if reservation is None:
                raise BillingBlocked('summary root reservation is unavailable')
            child = self.tx.billing.reserve(ReservationRequest(op_id=identity, purpose='review_summary',
                price_id=UUID(config['price_id']), maximum_units=dict(input_tokens=config['input_token_budget'],
                    output_tokens=config['max_output_tokens']), task_max_microyuan=config['maximum_cost_microyuan'],
                input_ref=reference, class_session_id=root.class_session_id, quota_root_id=reservation['id'],
                schema_version='pr07b-batch-1', plan_id=identity))
            task_id = child['task_id']
        else:
            child = self.tx.tasks.enqueue('review_summary', reference, op_id=identity,
                operation='enqueueReviewBatch', class_session_id=root.class_session_id,
                parent_task_id=root.id, schema_version='pr07b-batch-1', plan_id=identity)
            task_id = child.id
        self._rows('''UPDATE generation_plans SET consumed_task_id=:task,consumed_at=clock_timestamp()
            WHERE owner_id=:owner AND id=:plan''', task=task_id, plan=identity)
        self._rows('''UPDATE review_batches SET task_id=:task,child_plan_id=:plan
            WHERE owner_id=:owner AND id=:id''', task=task_id, plan=identity, id=batch['id'])

    def _end_parent(self, task, state, error=None):
        return self.tx.tasks._write(task, state=state, error_code=error,
            leased_by=None, lease_expires_at=None, lease_token=task.lease_token+1,
            completed_at=self._now(), may_have_charged=task.may_have_charged)

    def advance(self, task_id, *, schedule=True):
        """Advance one confirmed operation using saved results and at most one new child."""
        self._serialize()
        task = self.tx.tasks._load(task_id, lock=True)
        if task.schema_version != 'pr07b-1' or task.error_code not in (
                'BATCHES_PENDING', 'BATCH_RESULT_UNKNOWN', 'PROVIDER_RESULT_CONFLICT'):
            return False
        plan = self._plan_row(task.plan_id)
        batches = self._batches(task.plan_id)
        self._lock_batch_tasks(batches)
        if self._has_conflict(task.plan_id):
            for batch in batches[1:]:
                if batch['task_id'] is not None:
                    child = self.tx.tasks.get(batch['task_id'])
                    if child.state in ('queued', 'running'):
                        self.tx.tasks.cancel(child.id, uuid4())
            if task.state != 'unknown' or task.error_code != 'BATCH_RESULT_UNKNOWN':
                self.tx.tasks._write(task, state='unknown', error_code='BATCH_RESULT_UNKNOWN',
                    leased_by=None, lease_expires_at=None, lease_token=task.lease_token+1)
                return True
            return False
        try:
            self.validate_target(task.class_session_id, plan['request'])
        except QaRejected:
            self._end_parent(task, 'failed', 'RESULT_TARGET_UNAVAILABLE')
            return True
        results = []
        pending = []
        for batch in batches:
            result = self._completed_batch(batch['task_id'])
            if result is not None:
                results.append(result)
            else:
                pending.append(batch)
        if not pending:
            try:
                payload = merge_review_outputs([result['validated_payload'] for result in results],
                                               self._selection(plan['snapshot_id']))
            except ValueError:
                self._end_parent(task, 'failed', 'OUTPUT_INVALID')
                return True
            try:
                with self.connection.begin_nested():
                    PgReviewRepository(self.tx).publish(task, payload, plan['snapshot_id'], plan['request'],
                                                        raw_output_ref=results[0]['id'])
            except QaRejected as error:
                if error.code not in ('NOT_FOUND', 'SOURCE_UNAVAILABLE', 'CAPACITY_EXCEEDED'):
                    raise
                self._end_parent(task, 'failed', 'CAPACITY_EXCEEDED' if error.code == 'CAPACITY_EXCEEDED'
                                 else 'RESULT_TARGET_UNAVAILABLE')
                return True
            self._end_parent(task, 'succeeded')
            return True
        current = pending[0]
        if task.cancel_requested_at is not None:
            uncertain = False
            for batch in pending:
                if batch['task_id'] is not None:
                    child = self.tx.tasks.cancel(batch['task_id'], uuid4())
                    uncertain |= child.state in ('unknown', 'cancel_requested', 'running')
            if uncertain:
                if task.state != 'unknown':
                    self.tx.tasks._write(task, state='unknown', error_code='BATCH_RESULT_UNKNOWN',
                        leased_by=None, lease_expires_at=None, lease_token=task.lease_token+1)
                    return True
                return False
            self._end_parent(task, 'cancelled')
            return True
        if current['task_id'] is not None:
            child = self.tx.tasks.get(current['task_id'])
            if child.state in ('failed', 'cancelled'):
                self._end_parent(task, 'failed', child.error_code or 'BATCH_CANCELLED')
                return True
            if child.state == 'unknown' and task.state != 'unknown':
                self.tx.tasks._write(task, state='unknown', error_code='BATCH_RESULT_UNKNOWN',
                    leased_by=None, lease_expires_at=None, lease_token=task.lease_token+1)
                return True
            return False
        if not schedule:
            return False
        try:
            with self.connection.begin_nested():
                self._create_batch(task, plan, current)
                if task.state == 'unknown':
                    self.tx.tasks._write(task, state='running', error_code='BATCHES_PENDING')
        except (BillingBlocked, QuotaExceeded):
            self._end_parent(task, 'failed', 'BUDGET_BLOCKED')
        return True

    def cancel(self, task_id, op_id):
        self._serialize()
        task = self.tx.tasks._load(task_id, lock=True)
        batches = self._batches(task.plan_id)
        self._lock_batch_tasks(batches)
        if len(batches) > 1 and task.state == 'cancelled':
            return self._terminal_receipt(task, op_id, 'cancelTask', dict(task_id=str(task_id)))
        if len(batches) <= 1 or task.state in ('succeeded', 'failed'):
            return super().cancel(task_id, op_id)
        task = super().cancel(task_id, op_id)
        # The first durable result cannot turn a cancelled multi-batch operation
        # back into a queued first-call task.
        if self._completed_batch(task_id) is not None:
            task = self.tx.tasks._write(task, state='cancel_requested', error_code='BATCHES_PENDING',
                leased_by=None, lease_expires_at=None, lease_token=task.lease_token+1)
            self.advance(task_id, schedule=False)
        return self.tx.tasks.get(task_id)

    def reconcile(self, task_id, op_id):
        self._serialize()
        task = self.tx.tasks._load(task_id, lock=True)
        batches = self._batches(task.plan_id)
        self._lock_batch_tasks(batches)
        if len(batches) > 1 and task.state == 'cancelled':
            return self._terminal_receipt(task, op_id, 'reconcileTask',
                                          dict(task_id=str(task_id), new_submission=False))
        if task.error_code in ('BATCHES_PENDING', 'BATCH_RESULT_UNKNOWN'):
            digest = fingerprint(dict(task_id=str(task_id), new_submission=False))
            old = self.tx.receipts.lock_and_get('reconcileTask', op_id, digest)
            for batch in batches:
                if batch['batch_index'] and batch['task_id']:
                    child_key = UUID(bytes=hashlib.sha256(op_id.bytes+batch['task_id'].bytes).digest()[:16], version=4)
                    self.tx.tasks.reconcile_result(batch['task_id'], child_key)
            self.advance(task_id, schedule=False)
            result = self.tx.tasks.get(task_id)
            if old is None:
                self.tx.receipts.store_task('reconcileTask', op_id, digest, result, response_status=200)
            return result
        return super().reconcile(task_id, op_id)

    def _terminal_receipt(self, task, op_id, operation, body):
        self._serialize()
        digest = fingerprint(body)
        old = self.tx.receipts.lock_and_get(operation, op_id, digest)
        if old is None:
            self.tx.receipts.store_task(operation, op_id, digest, task, response_status=200)
        return task

    def synchronization_ids(self, task_id):
        task = self.tx.tasks.get(task_id)
        batches = self._batches(task.plan_id)
        return [row['task_id'] for row in batches if row['task_id']] or [task_id]

    def task_view(self, task_id):
        value = super().task_view(task_id)
        task = self.tx.tasks.get(task_id)
        batches = self._batches(task.plan_id)
        if len(batches) > 1:
            value['total_units'] = len(batches)
            completed_units = 0
            batch_tasks = []
            for row in batches:
                state = 'planned'
                if row['task_id'] is not None:
                    child = self.tx.tasks.get(row['task_id'])
                    state = child.state
                    if child.error_code == 'PROVIDER_RESULT_CONFLICT':
                        state = 'unknown'
                    elif self._completed_batch(row['task_id']) is not None:
                        state = 'succeeded'
                        completed_units += 1
                batch_tasks.append(dict(batch_index=row['batch_index'],
                    task_id=str(row['task_id']) if row['task_id'] else None,
                    state=state, source_ids=row['source_keys']))
            value['completed_units'] = completed_units
            value['maximum_cost_microyuan'] = str(int(value['maximum_cost_microyuan']) * len(batches))
            value['batch_tasks'] = batch_tasks
            plan = self._plan_row(task.plan_id)
            if plan['config']['billing_required']:
                reservations = [self.tx.billing.for_task(row['task_id']) for row in batches if row['task_id']]
                value['settled_cost_microyuan'] = (str(sum(row['settled_microyuan'] for row in reservations))
                    if len(reservations) == len(batches) and all(row['state'] == 'settled' for row in reservations)
                    else None)
        return value

    def result_id(self, task_id):
        row = self._rows('''SELECT document_id FROM review_revisions WHERE owner_id=:owner
            AND task_id=:task AND artifact_key='summary' ''', task=task_id).one_or_none()
        return row['document_id'] if row else None

    def raw_output(self, task_id):
        self.task_view(task_id)
        result = self._rows('''SELECT r.*,a.id AS attempt_id,a.error_code FROM provider_results r
            JOIN task_attempts a ON a.owner_id=r.owner_id AND a.id=r.attempt_id
            WHERE r.owner_id=:owner AND a.task_id=:task
            ORDER BY EXISTS(SELECT FROM review_revisions v WHERE v.owner_id=r.owner_id
                AND v.task_id=:task AND v.artifact_key='summary' AND v.raw_output_ref=r.id) DESC,
                a.attempt_number DESC LIMIT 1''', task=task_id).one_or_none()
        if result is None:
            raise QaRejected('RESULT_UNAVAILABLE', 404)
        raw = result['raw_output']
        return dict(task_id=str(task_id), attempt_id=str(result['attempt_id']),
                    validation_status='not_validated' if result['validation_status'] == 'pending' else result['validation_status'],
                    raw_text=raw, error_code=result['error_code'],
                    created_at=result['received_at'].isoformat())


class PgReviewBatchRepository(PgReviewGenerationRepository):
    schema_version = 'pr07b-batch-1'

    def worker_input(self, task_id):
        self._serialize()
        values = super().worker_input(task_id)
        plan, _, _ = self._batch_input(task_id)
        root = self.tx.tasks._load(plan['consumed_task_id'], lock=True)
        self._lock_batch_tasks(self._batches(root.plan_id))
        if (root.state != 'running' or root.error_code != 'BATCHES_PENDING'
                or root.cancel_requested_at is not None or self._has_conflict(root.plan_id)):
            raise QaRejected('SOURCE_UNAVAILABLE', 404)
        return values

    def finish(self, task, payload, *, raw_output_ref=None):
        # A successful batch persists its validated provider result. Only its
        # parent operation may publish the final document and card versions.
        self._batch_input(task.id)

    def cancel(self, task_id, op_id):
        return self.tx.tasks.cancel(task_id, op_id)

    def reconcile(self, task_id, op_id):
        return self.tx.tasks.reconcile_result(task_id, op_id)
