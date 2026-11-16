"""PR-11a real PostgreSQL admission, idempotency and crash-recovery scenarios.

All prices, bills, users and process credentials are synthetic. Controlled test
clocks exercise calendar boundaries. Production obtains its clock from PG.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from itertools import count
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier, Event
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
import pytest
import sqlalchemy as sa

from classroom_web.application.billing import reconciliation_transaction
from classroom_web.domain.billing import (BillingBlocked, QuotaExceeded, ReservationRequest,
                                          Usage, UsageConflict, period)
from classroom_web.domain.data import OwnerContext, NotFound
from classroom_web.domain.tasks import IdempotencyMismatch, StaleFence, UnsafeRetry
from classroom_web.infrastructure.billing_repository import PgBillingRepository, json_value
from classroom_web.infrastructure.billing_schema import candidate_ddl
from classroom_web.infrastructure.database import Database
from classroom_web.infrastructure.schema import tables
from classroom_web.infrastructure.task_scheduler import PgTaskScheduler
from classroom_web.test_support import SYNTHETIC_PASSWORD
from conftest import migration_config


@pytest.fixture(scope='module')
def billing_pg(pg):
    """The candidate schema never changes the shared regression database."""
    name = 'pr02a_billing_' + uuid4().hex
    scheduler_login = 'pr02a_billing_scheduler_' + uuid4().hex[:20]
    reconciler_login = 'pr02a_billing_reconcile_' + uuid4().hex[:20]
    control = sa.create_engine(pg.admin_url, isolation_level='AUTOCOMMIT')
    admin = db = scheduler = reconciler = None
    try:
        with control.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE template0')
        url = pg.admin_url.set(database=name)
        admin = sa.create_engine(url)
        cfg = migration_config(url.render_as_string(hide_password=False))
        with admin.connect() as connection:
            cfg.attributes['connection'] = connection
            command.upgrade(cfg, 'head')
        with admin.begin() as connection:
            baseline_revisions = connection.execute(sa.text('SELECT version_num FROM alembic_version')).scalars().all()
            assert len(baseline_revisions) == 1
            connection.exec_driver_sql(candidate_ddl(), execution_options={'no_parameters': True})
            for login, role in ((scheduler_login, 'classroom_web_scheduler'),
                                (reconciler_login, 'classroom_billing_reconciler')):
                connection.exec_driver_sql(f'CREATE ROLE "{login}" LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE')
                connection.exec_driver_sql(f'GRANT {role} TO "{login}"')
        runtime = url.set(username=pg.login, password=SYNTHETIC_PASSWORD)
        db = Database(runtime.render_as_string(hide_password=False), pool_size=12,
                      cursor_secret=b'pr11a-synthetic-cursor-key-00000000')
        scheduler = PgTaskScheduler(url.set(username=scheduler_login).render_as_string(hide_password=False))
        reconciler = sa.create_engine(url.set(username=reconciler_login, password=SYNTHETIC_PASSWORD))
        yield SimpleNamespace(admin=admin, db=db, scheduler=scheduler, reconciler=reconciler,
                              runtime_url=runtime,admin_url=url,baseline_revisions=baseline_revisions)
    finally:
        for resource in (db, scheduler):
            if resource is not None:
                resource.dispose()
        for engine in (reconciler, admin):
            if engine is not None:
                engine.dispose()
        with control.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            for login in (scheduler_login, reconciler_login):
                connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{login}"')
        control.dispose()


CYCLES = count()


@pytest.fixture
def ledger(billing_pg):
    pg = billing_pg
    index = next(CYCLES) * 3
    now = datetime(2030 + index // 12, index % 12 + 1, 15, tzinfo=timezone.utc)
    owner, other = OwnerContext(uuid4()), OwnerContext(uuid4())
    actor, price = uuid4(), uuid4()
    with pg.admin.begin() as connection:
        connection.execute(tables['users'].insert(), [
            {'id': owner.owner_id, 'role': 'student', 'state': 'active'},
            {'id': other.owner_id, 'role': 'student', 'state': 'active'},
            {'id': actor, 'role': 'admin', 'state': 'active'}])
        connection.execute(sa.text('SELECT billing_api.open_period(:start,10,:actor,:op,:reason)'),
                           dict(start=period(now)[0], actor=actor, op=uuid4(), reason='synthetic cycle'))
        revision = connection.scalar(sa.text('SELECT revision FROM billing_control.switches'))
        flags = {key: True for key in ('billing','registration','new_capture','asr_streaming','asr_filetrans','ai_interaction','ai_summary')}
        connection.execute(sa.text('SELECT billing_api.update_switches(:actor,:op,:revision,cast(:flags AS jsonb),:reason)'),
                           dict(actor=actor, op=uuid4(), revision=revision, flags=json_value(flags), reason='synthetic enable'))
        connection.execute(sa.text('''INSERT INTO public.price_catalog(id,provider,model,region,revision,currency,
            rates,minimum_microyuan,synthetic,effective_at) VALUES(:id,'mock','synthetic','synthetic',:revision,'CNY',
            cast(:rates AS jsonb),0,true,'2020-01-01T00:00:00Z')'''),
            dict(id=price, revision=str(price), rates=json_value({'input_tokens': {'microyuan': 1, 'units': 1}})))

    @contextmanager
    def transaction(context=owner, at=None):
        with pg.db.transaction(context) as tx:
            repository = PgBillingRepository(tx)
            repository._now = lambda: at or now
            yield repository

    def request(**kwargs):
        values = dict(op_id=uuid4(), purpose='ai_question', price_id=price,
                      maximum_units={'input_tokens': 100}, task_max_microyuan=100,
                      input_ref={'schema_version': 1})
        return ReservationRequest(**(values | kwargs))

    def reserve(value=None, context=owner, at=None):
        with transaction(context, at) as repository:
            return dict(repository.reserve(value or request()))

    def send(row):
        claim = pg.scheduler.claim('pr11a-synthetic-worker', 3600, 1)[0]
        assert claim.id == row['task_id']
        with transaction() as repository:
            attempt = repository.tx.tasks.prepare_attempt(claim.id, claim.lease_token, 'pr11a-synthetic-worker')
        with transaction() as repository:
            return dict(repository.authorize_send(row['id'], attempt.id, claim.lease_token, 'pr11a-synthetic-worker'))

    def budget(at=None):
        with transaction(at=at) as repository:
            return repository._budget(period(at or now)[0])

    value = SimpleNamespace(pg=pg, now=now, owner=owner, other=other, actor=actor, price=price,
        transaction=transaction, request=request, reserve=reserve, send=send, budget=budget)
    yield value
    with pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE public.tasks SET not_before='2099-01-01T00:00:00Z' WHERE owner_id IN (:a,:b)"),
                           dict(a=owner.owner_id, b=other.owner_id))


def measurement(row, revision=1, quantity=40, final=True, **kwargs):
    return Usage(provider_request_id=str(row['id']), meter='total', revision=revision,
                 quantities={'input_tokens': quantity} if quantity is not None else None,
                 final=final, **kwargs)


def test_reservation_and_duplicate_key(ledger):
    request = ledger.request()
    first = ledger.reserve(request)
    assert ledger.reserve(request)['id'] == first['id']
    assert ledger.budget()['held'] == 100
    with pytest.raises(IdempotencyMismatch):
        ledger.reserve(replace(request, maximum_units={'input_tokens': 99}))
    with ledger.transaction() as repository:
        quota = repository.quota()
    assert next(b for b in quota['buckets'] if b['kind']=='interactive_month_count')['remaining'] == 19


def test_admission_and_task_creation_rollback_together(ledger):
    with pytest.raises(BillingBlocked):
        ledger.reserve(ledger.request(maximum_units={'input_tokens': 106_000_000}, task_max_microyuan=106_000_000))
    with ledger.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT count(*) FROM tasks WHERE owner_id=:owner'), {'owner':ledger.owner.owner_id}) == 0
    assert ledger.budget()['held'] == 0


def test_same_key_concurrent_reservation(ledger):
    request = ledger.request()
    barrier = Barrier(8)
    def run(_):
        barrier.wait()
        return ledger.reserve(request)['id']
    with ThreadPoolExecutor(max_workers=8) as pool:
        identities = list(pool.map(run, range(8)))
    assert len(set(identities)) == 1
    assert ledger.budget()['held'] == 100


def test_platform_budget_concurrent_different_owners(ledger):
    barrier = Barrier(2)
    def run(owner):
        barrier.wait()
        try:
            return ledger.reserve(ledger.request(maximum_units={'input_tokens': 60_000_000}, task_max_microyuan=60_000_000), owner)
        except BillingBlocked:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(run, [ledger.owner,ledger.other]))
    assert sum(r is not None for r in rows) == 1
    assert ledger.budget()['held'] == 60_000_000


def test_quota_concurrent_last_operation(ledger):
    with ledger.transaction() as repository:
        rows = repository._quota_rows(ledger.request(),ledger.now,10)
        repository._rows('UPDATE quota_periods SET limit_value=1 WHERE id=:id AND owner_id=:owner',id=rows[0][0]['id'])
    barrier = Barrier(2)
    def run(_):
        barrier.wait()
        try:
            return ledger.reserve()
        except QuotaExceeded:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(run,range(2)))
    assert sum(r is not None for r in rows) == 1


def test_duplicate_cumulative_and_final_usage(ledger):
    row = ledger.send(ledger.reserve())
    for usage in [measurement(row,quantity=10, final=False), measurement(row,quantity=10, final=False),
                  measurement(row,revision=2, quantity=30, final=False), measurement(row,revision=3), measurement(row,revision=3)]:
        with ledger.transaction() as repository:
            repository.reconcile(row['id'],usage)
    budget = ledger.budget()
    assert (budget['settled'],budget['held'],budget['unknown']) == (40,0,0)
    with ledger.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT count(*) FROM usage_receipts WHERE reservation_id=:id'),row) == 3


def test_conflicting_and_late_revision(ledger):
    row = ledger.send(ledger.reserve())
    with ledger.transaction() as repository:
        repository.reconcile(row['id'],measurement(row,revision=3))
    with pytest.raises(UsageConflict), ledger.transaction() as repository:
        repository.reconcile(row['id'],measurement(row,revision=3,quantity=41))
    with ledger.transaction() as repository:
        repository.reconcile(row['id'],measurement(row,revision=1,quantity=10,final=False))
    assert ledger.budget()['settled'] == 40


def test_unknown_missing_usage_keeps_hold(ledger):
    row = ledger.send(ledger.reserve())
    with ledger.transaction() as repository:
        repository.mark_unknown(row['id'])
        repository.reconcile(row['id'],measurement(row,quantity=None))
    budget = ledger.budget()
    assert (budget['held'],budget['unknown'],budget['settled']) == (0,100,0)
    with pytest.raises(BillingBlocked), ledger.transaction() as repository:
        repository.release_unsent(row['id'])
    with ledger.transaction() as repository:
        repository.reconcile(row['id'],measurement(row,revision=2))
    assert ledger.budget()['unknown'] == 0


def test_partial_usage_retains_residual_and_overrun(ledger):
    row = ledger.send(ledger.reserve())
    with ledger.transaction() as repository:
        repository.reconcile(row['id'],measurement(row,quantity=25,final=False))
    assert (ledger.budget()['held'],ledger.budget()['settled']) == (75,25)
    with ledger.transaction() as repository:
        repository.mark_unknown(row['id'])
        repository.reconcile(row['id'],measurement(row,revision=2,quantity=150,final=False))
    assert (ledger.budget()['unknown'],ledger.budget()['settled']) == (0,150)


def test_invoice_correction_is_append_only(ledger):
    row = ledger.send(ledger.reserve())
    with ledger.transaction() as repository:
        repository.reconcile(row['id'],measurement(row,quantity=70))
    with pytest.raises(UsageConflict),ledger.transaction() as repository:
        repository.reconcile(row['id'],measurement(row,revision=2,quantity=50))
    with ledger.pg.admin.connect() as connection:
        receipt = connection.scalar(sa.text('SELECT id FROM usage_receipts WHERE reservation_id=:id'),row)
    with ledger.transaction() as repository:
        repository.reconcile(row['id'],measurement(row,revision=2,quantity=50,adjustment_of=receipt))
    assert ledger.budget()['settled'] == 50
    with pytest.raises(sa.exc.DBAPIError),ledger.transaction() as repository:
        repository._rows('UPDATE usage_receipts SET amount_microyuan=0 WHERE owner_id=:owner')


def test_release_proves_no_intent_and_replay_stays_released(ledger):
    request = ledger.request()
    row = ledger.reserve(request)
    with ledger.transaction() as repository:
        assert repository.release_unsent(row['id'])['state']=='released'
    assert ledger.reserve(request)['state']=='released'
    assert ledger.budget()['held']==0


def test_cross_owner_denied(ledger):
    row = ledger.reserve()
    with pytest.raises(NotFound),ledger.transaction(ledger.other) as repository:
        repository.release_unsent(row['id'])
    with pytest.raises(sa.exc.DBAPIError),ledger.transaction() as repository:
        repository._rows('UPDATE billing_control.platform_budgets SET budget_microyuan=999999999')


def test_reconciliation_after_account_disabled(ledger):
    row = ledger.send(ledger.reserve())
    with ledger.pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE users SET state='disabled' WHERE id=:id"), {'id':ledger.owner.owner_id})
    with reconciliation_transaction(ledger.pg.reconciler,ledger.owner.owner_id) as repository:
        repository.reconcile(row['id'],measurement(row))
    with ledger.pg.reconciler.connect() as connection:
        with pytest.raises(sa.exc.DBAPIError):
            connection.execute(sa.text('SELECT input_ref FROM tasks'))
    with ledger.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT settled_microyuan FROM quota_reservations WHERE id=:id'),row)==40


def test_cross_month_preserves_unknown_and_bill_month(ledger):
    row = ledger.send(ledger.reserve())
    with ledger.transaction() as repository:
        repository.mark_unknown(row['id'])
    next_month = period(ledger.now)[1] + timedelta(seconds=1)
    with ledger.pg.admin.begin() as connection:
        connection.execute(sa.text('SELECT billing_api.open_period(:start,10,:actor,:op,:reason)'),
            dict(start=period(next_month)[0],actor=ledger.actor,op=uuid4(),reason='synthetic next cycle'))
    second = ledger.reserve(at=next_month)
    assert ledger.budget()['unknown']==100
    assert ledger.budget(next_month)['held']==100
    with ledger.transaction(at=next_month) as repository:
        repository.reconcile(row['id'],measurement(row,bill_month='2040-01'))
    assert ledger.budget()['settled']==40
    assert ledger.budget(next_month)['held']==100
    assert second['period_start'] != row['period_start']


def test_old_unsent_hold_requires_release_and_new_reservation(ledger):
    row = ledger.reserve()
    next_month = period(ledger.now)[1] + timedelta(seconds=1)
    claim = ledger.pg.scheduler.claim('pr11a-synthetic-worker',3600,1)[0]
    with ledger.transaction(at=next_month) as repository:
        attempt = repository.tx.tasks.prepare_attempt(claim.id,claim.lease_token,'pr11a-synthetic-worker')
    with pytest.raises(BillingBlocked),ledger.transaction(at=next_month) as repository:
        repository.authorize_send(row['id'],attempt.id,claim.lease_token,'pr11a-synthetic-worker')
    with ledger.transaction(at=next_month) as repository:
        repository.release_unsent(row['id'])
    assert ledger.budget()['held']==0


def test_switch_closure_blocks_send_but_accepts_usage(ledger):
    submitted = ledger.send(ledger.reserve())
    pending = ledger.reserve()
    with ledger.pg.admin.begin() as connection:
        revision = connection.scalar(sa.text('SELECT revision FROM billing_control.switches'))
        connection.execute(sa.text('SELECT billing_api.update_switches(:actor,:op,:revision,cast(:flags AS jsonb),:reason)'),
            dict(actor=ledger.actor,op=uuid4(),revision=revision,flags='{"billing":false}',reason='synthetic pause'))
    with pytest.raises(BillingBlocked):
        ledger.reserve()
    with pytest.raises(BillingBlocked):
        ledger.send(pending)
    with ledger.transaction() as repository:
        repository.reconcile(submitted['id'],measurement(submitted))
    assert ledger.budget()['settled']==40


def test_unknown_cannot_authorize_again(ledger):
    row = ledger.send(ledger.reserve())
    with ledger.transaction() as repository:
        repository.mark_unknown(row['id'])
    with pytest.raises(BillingBlocked),ledger.transaction() as repository:
        repository.authorize_send(row['id'],row['attempt_id'],1,'pr11a-synthetic-worker')


def test_database_failure_rolls_back_entire_settlement(ledger):
    row = ledger.send(ledger.reserve())
    with pytest.raises(sa.exc.DBAPIError),ledger.transaction() as repository:
        repository.reconcile(row['id'],measurement(row))
        repository.connection.execute(sa.text('SELECT 1/0'))
    assert (ledger.budget()['held'],ledger.budget()['settled']) == (100,0)
    with ledger.transaction() as repository:
        repository.reconcile(row['id'],measurement(row))
    assert ledger.budget()['settled']==40


@pytest.mark.parametrize('checkpoint',['settlement-before-commit','settlement-after-commit'])
def test_process_exit_and_duplicate_recovery(ledger,checkpoint):
    row = ledger.send(ledger.reserve())
    environment = dict(os.environ,PR11A_RUNTIME_URL=ledger.pg.runtime_url.render_as_string(hide_password=False))
    script = Path(__file__).resolve().parents[4]/'tools/pr11a/crash_case.py'
    result = subprocess.run([sys.executable,'-B',str(script),checkpoint,str(ledger.owner.owner_id),str(row['id'])],
                            env=environment,capture_output=True,timeout=30)
    assert result.returncode == 73,result.stderr.decode('utf-8',errors='replace')
    assert ledger.budget()['settled'] == (0 if checkpoint=='settlement-before-commit' else 40)
    with ledger.transaction() as repository:
        repository.reconcile(row['id'],measurement(row))
    assert (ledger.budget()['held'],ledger.budget()['settled']) == (0,40)


def test_send_crash_preserves_risk_and_task_engine_does_not_resend(ledger,tmp_path):
    from classroom_web.application.task_worker import MockProvider,TaskWorker
    row = ledger.reserve()
    claim = ledger.pg.scheduler.claim('pr11a-synthetic-worker',3600,1)[0]
    assert claim.id==row['task_id']
    environment = dict(os.environ,PR11A_RUNTIME_URL=ledger.pg.runtime_url.render_as_string(hide_password=False))
    script = Path(__file__).resolve().parents[4]/'tools/pr11a/crash_case.py'
    journal = tmp_path/'synthetic-sends.txt'
    result = subprocess.run([sys.executable,'-B',str(script),'sent-after-commit',str(ledger.owner.owner_id),
        str(row['id']),'--at',ledger.now.isoformat(),'--journal',str(journal)],env=environment,capture_output=True,timeout=30)
    assert result.returncode==73,result.stderr.decode('utf-8',errors='replace')
    with ledger.pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"),
                           {'id':row['task_id']})
    provider = MockProvider()
    worker = TaskWorker(ledger.pg.db,ledger.pg.scheduler,provider,worker_id='recovery-worker')
    worker.run_once()
    with ledger.transaction() as repository:
        assert repository.tx.tasks.get(row['task_id']).state=='unknown'
        repository.mark_unknown(row['id'])
    assert ledger.budget()['unknown']==100
    assert provider.calls==0
    assert len(journal.read_text(encoding='utf-8').splitlines())==1


def test_summary_children_share_operation_quota(ledger):
    root = ledger.send(ledger.reserve(ledger.request(purpose='review_summary')))
    child = ledger.reserve(ledger.request(purpose='review_summary',quota_root_id=root['id']))
    with ledger.transaction() as repository:
        summary = next(b for b in repository.quota()['buckets'] if b['kind']=='summary_month_count')
    assert summary['submitted']==1 and summary['reserved']==0 and summary['remaining']==7
    assert ledger.budget()['held']==200
    assert child['quota_root_id']==root['id']


def test_alert_thresholds_durable_once_per_cycle(ledger):
    row = ledger.send(ledger.reserve())
    for revision,quantity in [(1,300_000_000),(2,400_000_000),(3,500_000_000)]:
        with ledger.transaction() as repository:
            repository.reconcile(row['id'],measurement(row,revision=revision,quantity=quantity))
    assert ledger.budget()['risk']==600_000_000
    with ledger.pg.admin.connect() as connection:
        thresholds = connection.execute(sa.text('SELECT threshold FROM billing_control.alerts WHERE period_start=:start ORDER BY threshold'),
            {'start':period(ledger.now)[0]}).scalars().all()
    assert thresholds==[60,80,100]
    with pytest.raises(BillingBlocked):
        ledger.reserve()


def test_runtime_cannot_change_switches_or_adjust_quota(ledger):
    with pytest.raises(sa.exc.DBAPIError),ledger.transaction() as repository:
        repository.connection.execute(sa.text('SELECT billing_api.update_switches(:actor,:op,1,\'{}\',\'synthetic\')'),
                                      {'actor':ledger.actor,'op':uuid4()})


def test_quota_adjustment_is_idempotent_and_preserves_budget(ledger):
    with ledger.transaction() as repository:
        quotas = repository._quota_rows(ledger.request(),ledger.now,10)
    quota = quotas[0][0]
    operation = uuid4()
    for _ in range(2):
        with ledger.pg.admin.begin() as connection:
            connection.execute(sa.text("SELECT set_config('app.owner_id',:owner,true)"),{'owner':str(ledger.owner.owner_id)})
            connection.execute(sa.text('SELECT billing_api.adjust_quota(:actor,:op,:quota,5,:reason)'),
                dict(actor=ledger.actor,op=operation,quota=quota['id'],reason='synthetic additional allowance'))
    with ledger.transaction() as repository:
        bucket = next(b for b in repository.quota()['buckets'] if b['kind']=='interactive_month_count')
    assert bucket['limit']==25
    assert ledger.budget()['budget_microyuan']==500_000_000


def test_candidate_upgrade_preserves_single_alembic_head_and_history(ledger):
    with ledger.pg.admin.connect() as connection:
        assert connection.execute(sa.text('SELECT version_num FROM alembic_version')).scalars().all()==ledger.pg.baseline_revisions
        assert connection.scalar(sa.text('SELECT count(*) FROM price_catalog'))>0
        assert connection.scalar(sa.text("SELECT relforcerowsecurity FROM pg_class WHERE oid='quota_reservations'::regclass")) is True


@pytest.mark.parametrize(('quantity','blocked'),[(10_800_000,False),(10_800_001,True)])
def test_asr_daily_quota_also_reserves_monthly(ledger,quantity,blocked):
    price = uuid4()
    with ledger.pg.admin.begin() as connection:
        connection.execute(sa.text('''INSERT INTO price_catalog(id,provider,model,region,revision,currency,rates,
            minimum_microyuan,synthetic,effective_at) VALUES(:id,'mock','synthetic','synthetic',:revision,'CNY',
            cast(:rates AS jsonb),0,true,'2020-01-01T00:00:00Z')'''),
            {'id':price,'revision':str(price),'rates':json_value({'audio_duration_ms':{'microyuan':1,'units':1}})})
    request = ledger.request(purpose='asr_live',price_id=price,
        maximum_units={'audio_duration_ms':quantity},task_max_microyuan=quantity)
    if blocked:
        with pytest.raises(QuotaExceeded):
            ledger.reserve(request)
    else:
        ledger.reserve(request)
        with ledger.transaction() as repository:
            buckets = {b['kind']:b for b in repository.quota()['buckets']}
        assert buckets['asr_day_ms']['remaining']==0
        assert buckets['asr_month_ms']['remaining']==43_200_000-quantity


def test_final_receipt_cannot_be_replaced_by_missing_usage(ledger):
    row = ledger.send(ledger.reserve())
    with ledger.transaction() as repository:
        repository.reconcile(row['id'],measurement(row))
    with pytest.raises(UsageConflict),ledger.transaction() as repository:
        repository.reconcile(row['id'],measurement(row,revision=2,quantity=None))
    assert ledger.budget()['settled']==40 and ledger.budget()['unknown']==0


def test_concurrent_duplicate_settlement(ledger):
    row = ledger.send(ledger.reserve())
    barrier = Barrier(4)
    def run(_):
        barrier.wait()
        with ledger.transaction() as repository:
            return repository.reconcile(row['id'],measurement(row))['state']
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(run,range(4)))==['settled']*4
    assert ledger.budget()['settled']==40


def test_candidate_ddl_rollback_and_existing_task_compatibility(billing_pg):
    name = 'pr02a_billing_upgrade_' + uuid4().hex[:24]
    control = sa.create_engine(billing_pg.admin_url,isolation_level='AUTOCOMMIT')
    admin = database = None
    try:
        with control.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE template0')
        url = billing_pg.admin_url.set(database=name)
        admin = sa.create_engine(url)
        cfg = migration_config(url.render_as_string(hide_password=False))
        with admin.connect() as connection:
            cfg.attributes['connection']=connection
            command.upgrade(cfg,'head')
        owner = OwnerContext(uuid4())
        with admin.begin() as connection:
            connection.execute(tables['users'].insert(),{'id':owner.owner_id,'role':'student','state':'active'})
        database = Database(billing_pg.runtime_url.set(database=name).render_as_string(hide_password=False),
                            cursor_secret=b'pr11a-synthetic-cursor-key-00000000')
        operation = uuid4()
        with database.transaction(owner) as tx:
            task = tx.tasks.enqueue('export',{'schema_version':1},op_id=operation)
        with admin.connect() as connection:
            baseline_revisions = connection.execute(sa.text('SELECT version_num FROM alembic_version')).scalars().all()
            assert len(baseline_revisions) == 1
        with pytest.raises(sa.exc.DBAPIError),admin.begin() as connection:
            connection.exec_driver_sql(candidate_ddl()+'\nSELECT 1/0;',execution_options={'no_parameters':True})
        with admin.connect() as connection:
            assert connection.scalar(sa.text("SELECT to_regnamespace('billing_api')")) is None
            assert connection.execute(sa.text('SELECT version_num FROM alembic_version')).scalars().all()==baseline_revisions
        with admin.begin() as connection:
            connection.exec_driver_sql(candidate_ddl(),execution_options={'no_parameters':True})
        with admin.connect() as connection:
            cfg.attributes['connection']=connection
            command.upgrade(cfg,'head')
        with database.transaction(owner) as tx:
            assert tx.tasks.enqueue('export',{'schema_version':1},op_id=operation).id==task.id
            assert tx.tasks.get(task.id).state=='queued'
        with admin.connect() as connection:
            assert connection.scalar(sa.text('SELECT count(*) FROM quota_reservations'))==0
    finally:
        if database is not None:
            database.dispose()
        if admin is not None:
            admin.dispose()
        with control.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        control.dispose()


def test_meter_permits_are_bounded_and_single_use(ledger):
    row = ledger.send(ledger.reserve())
    key = uuid4()
    with ledger.transaction() as repository:
        task = repository.tx.tasks.get(row['task_id'])
        repository.consume_permit(row['id'],key,{'input_tokens':40},task.lease_token,task.leased_by)
    with pytest.raises(UnsafeRetry),ledger.transaction() as repository:
        repository.consume_permit(row['id'],key,{'input_tokens':40},task.lease_token,task.leased_by)
    with ledger.transaction() as repository:
        repository.consume_permit(row['id'],uuid4(),{'input_tokens':60},task.lease_token,task.leased_by)
    with pytest.raises(BillingBlocked),ledger.transaction() as repository:
        repository.consume_permit(row['id'],uuid4(),{'input_tokens':1},task.lease_token,task.leased_by)
    assert ledger.budget()['held']==100


def test_permit_checks_fence_and_unknown(ledger):
    row = ledger.send(ledger.reserve())
    with ledger.transaction() as repository:
        task = repository.tx.tasks.get(row['task_id'])
    with pytest.raises(StaleFence),ledger.transaction() as repository:
        repository.consume_permit(row['id'],uuid4(),{'input_tokens':1},task.lease_token+1,task.leased_by)
    with ledger.transaction() as repository:
        repository.mark_unknown(row['id'])
    with pytest.raises(BillingBlocked),ledger.transaction() as repository:
        repository.consume_permit(row['id'],uuid4(),{'input_tokens':1},task.lease_token,task.leased_by)


def test_old_daily_hold_cannot_authorize_send_in_same_month(ledger):
    price = uuid4()
    with ledger.pg.admin.begin() as connection:
        connection.execute(sa.text('''INSERT INTO price_catalog(id,provider,model,region,revision,currency,rates,
            minimum_microyuan,synthetic,effective_at) VALUES(:id,'mock','synthetic','synthetic',:revision,'CNY',
            cast(:rates AS jsonb),0,true,'2020-01-01T00:00:00Z')'''),
            {'id':price,'revision':str(price),'rates':json_value({'audio_duration_ms':{'microyuan':1,'units':1}})})
    row = ledger.reserve(ledger.request(purpose='asr_live',price_id=price,
        maximum_units={'audio_duration_ms':1000},task_max_microyuan=1000))
    next_day = period(ledger.now,'day')[1]
    claim = ledger.pg.scheduler.claim('pr11a-synthetic-worker',3600,1)[0]
    with ledger.transaction(at=next_day) as repository:
        attempt = repository.tx.tasks.prepare_attempt(claim.id,claim.lease_token,'pr11a-synthetic-worker')
    with pytest.raises(BillingBlocked),ledger.transaction(at=next_day) as repository:
        repository.authorize_send(row['id'],attempt.id,claim.lease_token,'pr11a-synthetic-worker')
    assert row['expires_at']==next_day


def test_missing_one_meter_preserves_unknown_exposure(ledger):
    price = uuid4()
    with ledger.pg.admin.begin() as connection:
        connection.execute(sa.text('''INSERT INTO price_catalog(id,provider,model,region,revision,currency,rates,
            minimum_microyuan,synthetic,effective_at) VALUES(:id,'mock','synthetic','synthetic',:revision,'CNY',
            cast(:rates AS jsonb),0,true,'2020-01-01T00:00:00Z')'''),
            {'id':price,'revision':str(price),'rates':json_value({
                'input_tokens':{'microyuan':1,'units':1},'output_tokens':{'microyuan':1,'units':1}})})
    row = ledger.send(ledger.reserve(ledger.request(price_id=price,
        maximum_units={'input_tokens':100,'output_tokens':100},task_max_microyuan=200)))
    with ledger.transaction() as repository:
        assert repository.reconcile(row['id'],measurement(row))['state']=='unknown'
    assert ledger.budget()['unknown']==200
    with ledger.transaction() as repository:
        repository.reconcile(row['id'],Usage(provider_request_id=str(row['id']),meter='total',revision=2,
            quantities={'input_tokens':30,'output_tokens':20},final=True))
    assert (ledger.budget()['settled'],ledger.budget()['unknown'])==(50,0)


def test_complete_partial_usage_preserves_live_permits(ledger):
    row = ledger.send(ledger.reserve())
    with ledger.transaction() as repository:
        task = repository.tx.tasks.get(row['task_id'])
        repository.consume_permit(row['id'],uuid4(),{'input_tokens':40},task.lease_token,task.leased_by)
        assert repository.reconcile(row['id'],measurement(row,quantity=40,final=False))['state']=='submitted'
    with ledger.transaction() as repository:
        repository.consume_permit(row['id'],uuid4(),{'input_tokens':60},task.lease_token,task.leased_by)
        assert repository.reconcile(row['id'],measurement(row,revision=2,quantity=150,final=False))['state']=='submitted'
    with pytest.raises(BillingBlocked),ledger.transaction() as repository:
        repository.consume_permit(row['id'],uuid4(),{'input_tokens':1},task.lease_token,task.leased_by)


def test_switch_close_waits_for_committed_send_intent(ledger):
    row = ledger.reserve()
    claim = ledger.pg.scheduler.claim('pr11a-synthetic-worker',3600,1)[0]
    with ledger.transaction() as repository:
        attempt = repository.tx.tasks.prepare_attempt(claim.id,claim.lease_token,'pr11a-synthetic-worker')
    intent_ready,release_intent,writer_ready = Event(),Event(),Event()
    writer = {}

    def send():
        with ledger.transaction() as repository:
            repository.authorize_send(row['id'],attempt.id,claim.lease_token,'pr11a-synthetic-worker')
            intent_ready.set()
            assert release_intent.wait(10)

    def close():
        with ledger.pg.admin.begin() as connection:
            writer['pid'] = connection.scalar(sa.text('SELECT pg_backend_pid()'))
            revision = connection.scalar(sa.text('SELECT revision FROM billing_control.switches'))
            writer_ready.set()
            connection.execute(sa.text('SELECT billing_api.update_switches(:actor,:op,:revision,cast(:flags AS jsonb),:reason)'),
                dict(actor=ledger.actor,op=uuid4(),revision=revision,flags='{"billing":false}',reason='synthetic concurrent close'))

    with ThreadPoolExecutor(max_workers=2) as pool:
        sending = pool.submit(send)
        closing = None
        try:
            assert intent_ready.wait(10)
            closing = pool.submit(close)
            assert writer_ready.wait(10)
            blocked = False
            deadline = monotonic()+3
            while monotonic()<deadline:
                with ledger.pg.admin.connect() as connection:
                    blocked = bool(connection.scalar(sa.text('SELECT pg_blocking_pids(:pid)'),writer))
                if blocked:
                    break
                sleep(0.02)
            assert blocked,'the switch writer must wait for the earlier send intent COMMIT'
        finally:
            release_intent.set()
        sending.result(timeout=10)
        closing.result(timeout=10)
    with pytest.raises(BillingBlocked),ledger.transaction() as repository:
        repository.consume_permit(row['id'],uuid4(),{'input_tokens':1},claim.lease_token,'pr11a-synthetic-worker')
    assert ledger.budget()['held']==100
