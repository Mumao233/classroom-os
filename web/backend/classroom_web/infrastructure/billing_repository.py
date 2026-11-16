"""PostgreSQL billing ledger, composed with an existing owner UnitOfWork.

Every method runs inside its caller's transaction. Platform cycles are locked
before quota rows, tasks and reservations. No method performs provider I/O.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ..domain.billing import (BillingBlocked, Price, PURPOSES, QuotaExceeded,
                              ReservationRequest, Usage, UsageConflict,
                              billing_state, integer, period, quota_limits)
from ..domain.data import NotFound
from ..domain.tasks import StaleFence, UnsafeRetry, fingerprint, input_reference


def json_value(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str, allow_nan=False)


class PgBillingRepository:
    def __init__(self, transaction, *, synthetic_only=True):
        self.tx = transaction
        self.connection = transaction.connection
        self.owner_id = transaction.catalog.owner_id
        self.synthetic_only = synthetic_only
        safe = self.connection.scalar(text('''SELECT NOT r.rolsuper AND NOT r.rolbypassrls
            AND (pg_has_role(current_user,'classroom_web_runtime','USAGE')
                 OR pg_has_role(current_user,'classroom_billing_reconciler','USAGE'))
            AND NOT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname IN ('public','billing_control') AND c.relname IN
                  ('price_catalog','quota_periods','quota_reservations','reservation_allocations',
                   'usage_receipts','quota_adjustments','billing_permits','platform_budgets','exposures','switches','audit','alerts')
                  AND pg_has_role(current_user,c.relowner,'USAGE'))
            FROM pg_roles r WHERE r.rolname=current_user'''))
        if safe is not True:
            raise PermissionError('billing runtime must be unprivileged and must not own ledger tables')

    def _rows(self, sql, **params):
        return self.connection.execute(text(sql), {'owner': self.owner_id, **params}).mappings()

    def _now(self):
        return self.connection.scalar(text('SELECT clock_timestamp()'))

    def for_task(self, task_id):
        return self._rows('SELECT * FROM public.quota_reservations WHERE owner_id=:owner AND task_id=:task',
                          task=task_id).one_or_none()

    def sync_execution(self, task_id):
        row = self.for_task(task_id)
        if row is None:
            return None
        row = self._lock_existing(row['id'], authorize=False)
        facts = self.connection.scalar(text('SELECT billing_api.execution_metadata(:id)'), {'id': task_id})
        if facts is None:
            raise NotFound('execution metadata unavailable')
        state = row['state']
        attempt = row['attempt_id']
        if state == 'reserved':
            if facts['submitted_at'] is not None or facts['may_have_charged']:
                if facts['attempt_id'] is None:
                    raise BillingBlocked('send evidence requires metadata reconciliation')
                state, attempt = 'unknown', UUID(facts['attempt_id'])
            elif not facts['owner_active'] or facts['state'] in ('cancelled', 'failed'):
                state = 'released'
        # Recovery of saved raw output clears the old lease and requeues the
        # task; publishing that output cannot prove that final usage committed.
        # Keep the unconfirmed risk unknown through recovery and completion.
        elif state == 'submitted' and (not facts['owner_active']
                or facts['state'] in ('queued', 'unknown', 'succeeded', 'failed', 'cancelled')
                or facts['lease_expired'] or facts['cancel_requested']):
            state = 'unknown'
        if state != row['state']:
            self._rows('''UPDATE public.quota_reservations SET state=:state,attempt_id=:attempt
                WHERE owner_id=:owner AND id=:id''', state=state, attempt=attempt, id=row['id'])
            self._sync(row['id'])
        return self._get(row['id'])

    def _budget(self, start):
        try:
            return self.connection.scalar(text('SELECT billing_api.lock_budget(:start)'), {'start': start})
        except DBAPIError as error:
            raise BillingBlocked('budget service unavailable') from error

    def _switches(self, purpose):
        flags = self.connection.scalar(text('SELECT billing_api.lock_switches()'))['flags']
        if not flags.get('billing') or not flags.get(PURPOSES[purpose][0]):
            raise BillingBlocked('charged operation is paused')

    def _sync(self, identity, *, admit=False):
        try:
            self.connection.execute(text('SELECT billing_api.sync_exposure(:id,:admit)'),
                                    {'id': identity, 'admit': admit})
        except DBAPIError as error:
            raise BillingBlocked('budget admission failed') from error

    def _get(self, identity, *, lock=False):
        row = self._rows('SELECT * FROM public.quota_reservations WHERE owner_id=:owner AND id=:id'
                         + (' FOR UPDATE' if lock else ''), id=identity).one_or_none()
        if row is None:
            raise NotFound('reservation not found')
        return row

    def _price(self, identity):
        row = self._rows('SELECT * FROM public.price_catalog WHERE id=:id', id=identity).one_or_none()
        if row is None:
            raise BillingBlocked('price unavailable')
        return Price(id=row['id'], provider=row['provider'], model=row['model'], region=row['region'],
                     revision=row['revision'], rates=row['rates'], minimum_microyuan=row['minimum_microyuan'],
                     synthetic=row['synthetic'], verified=row['verified_at'] is not None)

    def _quota_used(self, quota_id):
        return self.connection.scalar(text('''
            SELECT coalesce(sum(CASE WHEN r.state='released' THEN 0
                WHEN r.state='settled' THEN coalesce(a.consumed_quantity,a.quantity)
                ELSE greatest(a.quantity,coalesce(a.consumed_quantity,0)) END),0)
            FROM public.reservation_allocations a JOIN public.quota_reservations r
              ON r.owner_id=a.owner_id AND r.id=a.reservation_id
            WHERE a.owner_id=:owner AND a.quota_period_id=:id
        '''), {'owner': self.owner_id, 'id': quota_id})

    def _quota_rows(self, request, now, stage):
        if request.quota_root_id is not None:
            return []
        resource = PURPOSES[request.purpose][2]
        resources = [resource, 'asr_day_ms'] if request.purpose == 'asr_live' else [resource]
        limits = quota_limits(stage)
        rows = []
        for resource in sorted(resources):
            start, end = period(now, 'day' if resource == 'asr_day_ms' else 'month')
            self._rows('''INSERT INTO public.quota_periods(id,owner_id,resource,period_start,period_end,limit_value)
                VALUES(:id,:owner,:resource,:start,:end,:limit) ON CONFLICT(owner_id,resource,period_start) DO NOTHING''',
                id=uuid4(), resource=resource, start=start, end=end, limit=limits[resource])
            row = self._rows('''SELECT * FROM public.quota_periods
                WHERE owner_id=:owner AND resource=:resource AND period_start=:start FOR UPDATE''',
                resource=resource, start=start).one()
            quantity = request.maximum_units['audio_duration_ms'] if resource.endswith('_ms') else 1
            if self._quota_used(row['id']) + quantity > row['limit_value']:
                raise QuotaExceeded('QUOTA_EXCEEDED')
            rows.append((row, quantity))
        return rows

    def reserve(self, request: ReservationRequest):
        request.validate()
        reference = input_reference(request.input_ref)
        request_hash = fingerprint(json.loads(json_value(asdict(request))))
        old = self.tx.receipts.lock_and_get('reserveBilling', request.op_id, request_hash)
        if old:
            self.tx.tasks.get(old['resource_id'])
            return self._rows('SELECT * FROM public.quota_reservations WHERE owner_id=:owner AND task_id=:task',
                              task=old['resource_id']).one()
        now = self._now()
        start, end = period(now)
        budget = self._budget(start)
        self._switches(request.purpose)
        price = self._price(request.price_id)
        effective = self._rows('SELECT effective_at FROM public.price_catalog WHERE id=:id', id=price.id).one()['effective_at']
        if effective > now or (self.synthetic_only and not price.synthetic) or (not price.synthetic and not price.verified):
            raise BillingBlocked('price is unavailable for this execution mode')
        maximum = price.amount(request.maximum_units)
        if maximum > request.task_max_microyuan:
            raise BillingBlocked('task maximum is below the conservative price bound')
        quotas = self._quota_rows(request, now, budget['stage'])
        if request.quota_root_id is not None:
            root = self._get(request.quota_root_id, lock=True)
            root_task = self.tx.tasks.get(root['task_id'])
            if (root['purpose'] != 'review_summary' or root['quota_root_id'] is not None
                    or root['state'] not in ('submitted', 'unknown', 'settled')
                    or root_task.class_session_id != request.class_session_id):
                raise BillingBlocked('summary child requires the existing submitted root operation')
        task = self.tx.tasks.enqueue(request.purpose, reference, op_id=request.op_id,
            operation='enqueueBudgetTask', class_session_id=request.class_session_id,
            parent_task_id=root['task_id'] if request.quota_root_id is not None else None)
        identity = uuid4()
        self._rows('''INSERT INTO public.quota_reservations(id,owner_id,task_id,quota_root_id,period_start,
            purpose,state,pool,reserved_microyuan,maximum_units,price_id,expires_at)
            VALUES(:id,:owner,:task,:root,:start,:purpose,'reserved',:pool,:maximum,cast(:units AS jsonb),:price,:expires)''',
            id=identity, task=task.id, root=request.quota_root_id, start=start, purpose=request.purpose,
            pool=PURPOSES[request.purpose][1], maximum=maximum, units=json_value(request.maximum_units), price=price.id,
            expires=min([end,*(row['period_end'] for row,_ in quotas)]))
        for row, quantity in quotas:
            self._rows('''INSERT INTO public.reservation_allocations(id,owner_id,reservation_id,quota_period_id,quantity)
                VALUES(:id,:owner,:reservation,:quota,:quantity)''',
                id=uuid4(), reservation=identity, quota=row['id'], quantity=quantity)
        self._sync(identity, admit=True)
        self.tx.receipts.store_task('reserveBilling', request.op_id, request_hash, task)
        return self._get(identity)

    def _lock_existing(self, identity, *, authorize=True):
        row = self._get(identity)
        self._budget(row['period_start'])
        # All allocations lock before task/attempt/reservation rows.
        if authorize:
            self._rows('''SELECT q.id FROM public.quota_periods q JOIN public.reservation_allocations a
                ON q.owner_id=a.owner_id AND q.id=a.quota_period_id
                WHERE a.owner_id=:owner AND a.reservation_id=:id ORDER BY q.resource,q.period_start FOR UPDATE OF q''',
                id=identity).all()
            self.tx.tasks.get(row['task_id'])
            self._rows('SELECT id FROM public.tasks WHERE owner_id=:owner AND id=:id FOR UPDATE', id=row['task_id']).one()
        return self._get(identity, lock=True)

    def authorize_send(self, reservation_id, attempt_id, fence, worker):
        row = self._lock_existing(reservation_id)
        if row['state'] != 'reserved' or not row['period_start'] <= self._now() < row['expires_at']:
            raise BillingBlocked('a fresh unsubmitted reservation in the current period is required')
        self._switches(row['purpose'])
        budget = self._budget(row['period_start'])
        if (budget['risk'] > budget['budget_microyuan'] - budget['safety_microyuan']
                or budget['risk'] >= budget['budget_microyuan']
                or budget['pool_risk'].get(row['pool'], 0) > budget['pools'][row['pool']]):
            raise BillingBlocked('platform risk exceeds the send allowance')
        task = self.tx.tasks.get(row['task_id'])
        price = self._price(row['price_id'])
        self.tx.tasks.mark_submitting(task.id, attempt_id, fence, worker)
        self._rows('''UPDATE public.task_attempts SET provider=:provider,model=:model,price_revision=:revision
            WHERE owner_id=:owner AND id=:attempt AND task_id=:task''',
            provider=price.provider, model=price.model, revision=price.revision, attempt=attempt_id, task=task.id)
        self._rows('''UPDATE public.quota_reservations SET attempt_id=:attempt,state='submitted'
            WHERE owner_id=:owner AND id=:id''', attempt=attempt_id, id=reservation_id)
        self._sync(reservation_id)
        return self._get(reservation_id)

    def consume_permit(self, reservation_id, submission_key, quantities, fence, worker):
        """Consume a bounded send unit once. COMMIT must precede its network I/O.

        A repeated key is evidence of an earlier intent and cannot authorize a
        second send. Streaming consumers use this before each new frame/window.
        """
        if not isinstance(submission_key, UUID) or submission_key.version != 4 or not quantities:
            raise ValueError('a stable submission key and bounded quantities are required')
        for quantity in quantities.values():
            integer(quantity)
        if not any(quantities.values()):
            raise ValueError('a send unit must consume a positive quantity')
        row = self._lock_existing(reservation_id)
        previous = self._rows('''SELECT id FROM public.billing_permits
            WHERE owner_id=:owner AND reservation_id=:id AND submission_key=:key''',
            id=reservation_id, key=submission_key).one_or_none()
        if previous is not None:
            raise UnsafeRetry('a consumed permit cannot authorize another send')
        if row['state'] != 'submitted' or not row['period_start'] <= self._now() < row['expires_at']:
            raise BillingBlocked('a current submitted window is required')
        if (row['settled_microyuan'] or 0) > row['reserved_microyuan']:
            raise BillingBlocked('observed usage exceeds the window cost bound')
        self._switches(row['purpose'])
        task = self.tx.tasks.get(row['task_id'])
        now = self.connection.scalar(text('SELECT clock_timestamp()'))
        if (task.state != 'running' or task.cancel_requested_at is not None or task.lease_token != fence
                or task.leased_by != worker or task.lease_expires_at is None or task.lease_expires_at <= now):
            raise StaleFence('STALE_FENCE')
        totals = {}
        for prior in self._rows('SELECT quantities FROM public.billing_permits WHERE owner_id=:owner AND reservation_id=:id',id=reservation_id):
            for meter, quantity in prior['quantities'].items():
                totals[meter] = totals.get(meter,0) + quantity
        if set(quantities) - set(row['maximum_units']) or any(
                totals.get(meter,0)+quantity > row['maximum_units'][meter] for meter,quantity in quantities.items()):
            raise BillingBlocked('the window meter bound is exhausted')
        budget = self._budget(row['period_start'])
        if (budget['risk'] > budget['budget_microyuan'] - budget['safety_microyuan']
                or budget['risk'] >= budget['budget_microyuan']
                or budget['pool_risk'].get(row['pool'],0) > budget['pools'][row['pool']]):
            raise BillingBlocked('platform risk exceeds the send allowance')
        identity = uuid4()
        self._rows('''INSERT INTO public.billing_permits(id,owner_id,reservation_id,submission_key,quantities)
            VALUES(:permit,:owner,:id,:key,cast(:quantities AS jsonb))''',
            permit=identity,id=reservation_id,key=submission_key,quantities=json_value(quantities))
        return dict(id=identity,expires_at=row['expires_at'],quantities=dict(quantities))

    def release_unsent(self, reservation_id):
        row = self._lock_existing(reservation_id)
        if row['state'] == 'released':
            return row
        # Lock the task and all existing attempts. A send intent can never be
        # inferred absent merely from the reservation state or an expired TTL.
        self._rows('SELECT id FROM public.tasks WHERE owner_id=:owner AND id=:id FOR UPDATE', id=row['task_id']).one()
        attempts = self._rows('''SELECT * FROM public.task_attempts WHERE owner_id=:owner AND task_id=:id
            ORDER BY attempt_number FOR UPDATE''', id=row['task_id']).all()
        if row['state'] != 'reserved' or any(a['submitted_at'] is not None or a['state'] not in ('planned', 'cancelled')
                                              for a in attempts):
            raise BillingBlocked('absence of a send intent must be proven')
        self.tx.tasks.cancel(row['task_id'], reservation_id)
        self._rows("UPDATE public.quota_reservations SET state='released' WHERE owner_id=:owner AND id=:id", id=reservation_id)
        self._sync(reservation_id)
        return self._get(reservation_id)

    def mark_unknown(self, reservation_id):
        row = self._lock_existing(reservation_id, authorize=False)
        if row['state'] not in ('submitted', 'unknown'):
            raise BillingBlocked('unknown requires an existing send intent')
        self._rows("UPDATE public.quota_reservations SET state='unknown' WHERE owner_id=:owner AND id=:id", id=reservation_id)
        self._sync(reservation_id)
        return self._get(reservation_id)

    def reconcile(self, reservation_id, usage: Usage):
        usage.validate()
        row = self._lock_existing(reservation_id, authorize=False)
        if row['state'] not in ('submitted', 'unknown', 'settled') or row['attempt_id'] is None:
            raise UsageConflict('usage requires an existing submitted reservation')
        price = self._price(row['price_id'])
        digest = fingerprint(json.loads(json_value(asdict(usage))))
        history = self._rows('''SELECT * FROM public.usage_receipts WHERE owner_id=:owner AND reservation_id=:id
            ORDER BY metric_revision DESC''', id=reservation_id).all()
        if any(h['provider_request_id'] != usage.provider_request_id or h['meter'] != usage.meter for h in history):
            raise UsageConflict('one cumulative usage identity is required per reservation')
        previous = next((h for h in history if h['metric_revision'] == usage.revision), None)
        if previous is not None:
            if previous['request_hash'] != digest:
                raise UsageConflict('usage identity contains conflicting data')
            return self._get(reservation_id)
        if usage.quantities is not None and set(usage.quantities) - set(price.rates):
            raise UsageConflict('usage contains a meter absent from the frozen price')
        complete = usage.quantities is not None and set(usage.quantities) == set(price.rates)
        amount = price.amount(usage.quantities) if complete else None
        latest = history[0] if history else None
        known = next((h for h in history if h['amount_microyuan'] is not None), None)
        fresh = latest is None or usage.revision > latest['metric_revision']
        if fresh and known is not None and known['final'] and (not usage.final or amount is None):
            raise UsageConflict('final usage requires a complete final correction')
        if fresh and known is not None and amount is not None:
            decreasing = amount < known['amount_microyuan'] or any(
                usage.quantities[meter] < quantity for meter, quantity in known['quantities'].items())
            if decreasing and not (usage.final and known['final'] and usage.adjustment_of == known['id']):
                raise UsageConflict('decreasing usage requires an explicit final invoice correction')
            if known['final'] and not usage.final:
                raise UsageConflict('a final measurement cannot revert to a partial measurement')
        if usage.adjustment_of is not None and (known is None or usage.adjustment_of != known['id'] or not usage.final):
            raise UsageConflict('invoice correction must reference the current known receipt')
        self._rows('''INSERT INTO public.usage_receipts(id,owner_id,reservation_id,attempt_id,provider,
            provider_request_id,meter,metric_revision,quantities,amount_microyuan,final,bill_month,adjustment_of,request_hash)
            VALUES(:id,:owner,:reservation,:attempt,:provider,:request,:meter,:revision,cast(:quantities AS jsonb),
                :amount,:final,:bill_month,:adjustment,:hash)''',
            id=uuid4(), reservation=reservation_id, attempt=row['attempt_id'], provider=price.provider,
            request=usage.provider_request_id, meter=usage.meter, revision=usage.revision,
            quantities=json_value(usage.quantities) if usage.quantities is not None else None,
            amount=amount, final=usage.final, bill_month=usage.bill_month, adjustment=usage.adjustment_of, hash=digest)
        if not fresh:
            return self._get(reservation_id)
        settled = amount if amount is not None else row['settled_microyuan']
        state = 'unknown'
        if amount is not None:
            state = 'settled' if usage.final else row['state']
        self._rows('''UPDATE public.quota_reservations SET state=:state,settled_microyuan=:amount
            WHERE owner_id=:owner AND id=:id''', state=state, amount=settled, id=reservation_id)
        if amount is not None:
            duration = usage.quantities.get('audio_duration_ms')
            self._rows('''UPDATE public.reservation_allocations a SET consumed_quantity=
                CASE WHEN q.resource LIKE '%_ms' THEN :duration ELSE a.quantity END
                FROM public.quota_periods q WHERE a.owner_id=:owner AND a.reservation_id=:id
                  AND q.owner_id=a.owner_id AND q.id=a.quota_period_id''', id=reservation_id, duration=duration)
        self._sync(reservation_id)
        return self._get(reservation_id)

    def quota(self):
        now = self._now()
        start, end = period(now)
        budget = self._budget(start)
        flags = self.connection.scalar(text('SELECT billing_api.lock_switches()'))['flags']
        buckets = []
        for resource, limit in quota_limits(budget['stage']).items():
            qstart, qend = period(now, 'day' if resource == 'asr_day_ms' else 'month')
            q = self._rows('''SELECT * FROM public.quota_periods
                WHERE owner_id=:owner AND resource=:resource AND period_start=:start''', resource=resource, start=qstart).one_or_none()
            values = dict(reserved=0, submitted=0, settled=0, unknown=0)
            if q is not None:
                limit = q['limit_value']
                for a in self._rows('''SELECT r.state,a.quantity,a.consumed_quantity FROM public.reservation_allocations a
                    JOIN public.quota_reservations r ON r.owner_id=a.owner_id AND r.id=a.reservation_id
                    WHERE a.owner_id=:owner AND a.quota_period_id=:id''', id=q['id']):
                    if a['state'] in values:
                        values[a['state']] += (a['consumed_quantity'] if a['state'] == 'settled' and a['consumed_quantity'] is not None
                                               else max(a['quantity'], a['consumed_quantity'] or 0))
            buckets.append(dict(kind=resource, period_start=qstart.isoformat(), period_end=qend.isoformat(),
                                limit=limit, **values, remaining=max(0, limit - sum(values.values()))))
        return dict(buckets=buckets, platform_billing_state=billing_state(budget['risk'],budget['budget_microyuan'],flags['billing']),
                    asr_enabled=flags['billing'] and flags['asr_streaming'] and flags['asr_filetrans'],
                    ai_enabled=flags['billing'] and flags['ai_interaction'] and flags['ai_summary'])
