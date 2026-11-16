"""Incremental PR-11a entrypoint and lifecycle tests, with real PG and identity."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pyotp
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from classroom_web.application.billing import BillingService
from classroom_web.application.billing_browser import BillingAdmin, BillingBrowser
from classroom_web.application.billing_tasks import BillingTaskLifecycle
from classroom_web.application.task_worker import MockProvider, TaskWorker
from classroom_web.domain.auth import BrowserContext
from classroom_web.domain.billing import ReservationRequest, Usage
from classroom_web.domain.data import OwnerContext
from classroom_web.entrypoints.http import create_app
from classroom_web.infrastructure.billing_control import BillingMaintenance, PgBillingAdminRepository
from classroom_web.infrastructure.billing_schema import candidate_ddl, integration_ddl
from classroom_web.infrastructure.task_scheduler import PgTaskScheduler
from classroom_web.test_support import SYNTHETIC_PASSWORD
from test_authentication import auth, install_factor, PASSWORD, BASE

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location('pr11a_contract_candidate',ROOT/'tools/pr11a/contract_candidate.py')
contract_candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract_candidate)


@pytest.fixture(scope='module')
def fee_control(pg):
    tag = uuid4().hex[:12]
    names = {key:'pr02a_billing_'+key+'_'+tag for key in ('gateway','operator','reconciler','scheduler')}
    actor,price = uuid4(),uuid4()
    with pg.admin.begin() as connection:
        connection.exec_driver_sql(candidate_ddl()+integration_ddl(),execution_options={'no_parameters':True})
        for key,name in names.items():
            inherit = 'NOINHERIT' if key=='gateway' else 'INHERIT'
            connection.exec_driver_sql(f'CREATE ROLE "{name}" LOGIN {inherit} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
            if key=='gateway':
                connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA billing_api TO "{name}"')
                for function in ('issue_admin_proof','apply_admin','read_admin'):
                    connection.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION billing_api.{function}(jsonb) TO "{name}"')
            else:
                role = 'classroom_web_scheduler' if key=='scheduler' else 'classroom_billing_'+key
                connection.exec_driver_sql(f'GRANT {role} TO "{name}"')
        connection.execute(sa.text("INSERT INTO users(id,role,state) VALUES(:id,'admin','active')"),{'id':actor})
        connection.execute(sa.text('''INSERT INTO price_catalog(id,provider,model,region,revision,currency,rates,
            minimum_microyuan,synthetic,effective_at) VALUES(:id,'mock','synthetic','synthetic',:revision,'CNY',
            cast(:rates AS jsonb),0,true,'2020-01-01T00:00:00Z')'''),
            dict(id=price,revision=str(price),rates=json.dumps({'input_tokens':{'microyuan':1,'units':1}})))
    urls = {key:pg.runtime_url.set(username=name,password=SYNTHETIC_PASSWORD) for key,name in names.items()}
    gateway = PgBillingAdminRepository(urls['gateway'])
    operator = sa.create_engine(urls['operator'],hide_parameters=True)
    reconciler = sa.create_engine(urls['reconciler'],hide_parameters=True)
    scheduler = PgTaskScheduler(urls['scheduler'])
    lifecycle = BillingTaskLifecycle(reconciler)
    maintenance = BillingMaintenance(operator,actor,lifecycle)
    maintenance.run_once()
    yield SimpleNamespace(pg=pg,gateway=gateway,operator=operator,reconciler=reconciler,scheduler=scheduler,
        lifecycle=lifecycle,maintenance=maintenance,price=price,actor=actor)
    gateway.dispose();operator.dispose();reconciler.dispose();scheduler.dispose()
    with pg.admin.begin() as connection:
        for name in names.values():
            connection.exec_driver_sql(f'DROP OWNED BY "{name}"')
            connection.exec_driver_sql(f'DROP ROLE "{name}"')


@pytest.fixture
def fee_api(fee_control,auth):
    a,c = auth,fee_control
    with c.pg.admin.begin() as connection:
        revision = connection.scalar(sa.text('SELECT revision FROM billing_control.switches'))
        flags = {name:True for name in ('billing','registration','new_capture','asr_streaming','asr_filetrans','ai_interaction','ai_summary')}
        connection.execute(sa.text('SELECT billing_api.update_switches(:actor,:op,:revision,cast(:flags AS jsonb),:reason)'),
            dict(actor=c.actor,op=uuid4(),revision=revision,flags=json.dumps(flags),reason='Synthetic entrypoint setup'))
    browser = BillingBrowser(a.service,c.pg.db,c.lifecycle)
    admin = BillingAdmin(a.service,c.pg.db,c.gateway)
    base=json.loads((ROOT/'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf-8'))
    candidate=contract_candidate.build(base)
    app=create_app(auth=a.service,contract=candidate,origin='https://testserver',billing=browser,billing_admin=admin)
    with TestClient(app,base_url='https://testserver') as client:
        yield SimpleNamespace(client=client,a=a,c=c,browser=browser,admin=admin,contract=candidate)
    with c.pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE tasks SET not_before=clock_timestamp()+interval '365 days' WHERE owner_id=:owner"),{'owner':a.uid})


def sign_in(api, *, administrator=False):
    a=api.a
    seed=None
    if administrator:
        _,seed=install_factor(a,role='admin')
    context=api.client.get(BASE+'/auth/csrf').json()['csrf_token']
    response=api.client.post(BASE+'/auth/login',json=dict(email=a.email,password=PASSWORD,remember_me=False,device_name='billing test'),
        headers={'Origin':'https://testserver','Sec-Fetch-Site':'same-origin','X-CSRF-Token':context,'Idempotency-Key':str(uuid4())})
    if administrator:
        assert response.json()['state']=='mfa_required'
        response=api.client.post(BASE+'/auth/login/mfa',json=dict(factor_type='totp',challenge_id=response.json()['challenge_id'],code=pyotp.TOTP(seed).now()),
            headers={'Origin':'https://testserver','Sec-Fetch-Site':'same-origin','X-CSRF-Token':context,'Idempotency-Key':str(uuid4())})
    assert response.status_code==200,response.text
    return response.json(),seed


def reserve(api,session):
    ctx=BrowserContext(session=api.client.cookies.get('__Host-classroom_session'))
    principal=api.a.service.principal(ctx)
    request=ReservationRequest(op_id=uuid4(),purpose='ai_question',price_id=api.c.price,maximum_units={'input_tokens':100},
        task_max_microyuan=100,input_ref={'schema_version':1})
    return BillingService(api.c.pg.db).reserve(principal,request)


def headers(session, **extra):
    return {'Origin':'https://testserver','Sec-Fetch-Site':'same-origin','X-CSRF-Token':session['csrf_token'],
            'Idempotency-Key':str(uuid4()),**extra}


def prove(api,session,seed,purpose,body,key):
    operation=dict(purpose=purpose,target=dict(kind='account',id=str(api.a.uid)),candidate_parameters=dict(idempotency_key=key,body=body))
    code=pyotp.TOTP(seed).at(datetime.now(timezone.utc)+timedelta(seconds=30))
    response=api.client.post(BASE+'/auth/reauth',json=dict(password=PASSWORD,mfa_code=code,operation=operation),headers=headers(session))
    assert response.status_code==200,response.text
    return {'Idempotency-Key':key,'X-Reauth-Token':response.json()['reauth_token']}


class InvoiceProvider(MockProvider):
    def usage_for(self,task,attempt):
        return Usage(provider_request_id=str(attempt.id),meter='total',revision=1,quantities={'input_tokens':40},final=True)


# Separate process exits after the durable raw-result transaction and before
# returning usage. os._exit also bypasses TaskWorker's finally/synchronize path.
RAW_COMMITTED_CRASH = '''
import os
from pathlib import Path
from classroom_web.application.billing_tasks import BillingTaskLifecycle
from classroom_web.application.task_worker import MockProvider, TaskWorker
from classroom_web.infrastructure.database import Database
from classroom_web.infrastructure.task_scheduler import PgTaskScheduler
import sqlalchemy as sa
class CrashProvider(MockProvider):
    def submit(self, task, attempt):
        with Path(os.environ['PR11A_CALL_JOURNAL']).open('a', encoding='utf-8') as stream:
            stream.write(str(attempt.id)+'\\n');stream.flush();os.fsync(stream.fileno())
        return super().submit(task, attempt)
    def usage_for(self, task, attempt):
        os._exit(73)
database=Database(os.environ['PR11A_RUNTIME_URL'],cursor_secret=b'pr11a-r2-synthetic-cursor-key-0000')
scheduler=PgTaskScheduler(os.environ['PR11A_SCHEDULER_URL'])
engine=sa.create_engine(os.environ['PR11A_RECONCILER_URL'],hide_parameters=True)
TaskWorker(database,scheduler,CrashProvider(),worker_id='r2-crashing',billing=BillingTaskLifecycle(engine)).run_once()
raise RuntimeError('expected hard exit after store_result commit')
'''


class SavedResultOnlyProvider(MockProvider):
    def submit(self, task, attempt):
        self.calls += 1
        raise AssertionError('saved result must never be resubmitted')

    def usage_for(self, task, attempt):
        raise AssertionError('missing usage cannot be recreated from a saved result')


def fee_snapshot(api, row):
    with api.c.pg.admin.connect() as connection:
        return connection.execute(sa.text('''SELECT r.state,r.attempt_id,r.settled_microyuan,e.held,e.unknown,e.settled,
            t.state AS task_state FROM quota_reservations r JOIN billing_control.exposures e ON e.reservation_id=r.id
            JOIN tasks t ON t.id=r.task_id WHERE r.id=:id'''),row).mappings().one()


@pytest.mark.parametrize('resume', ('worker', 'maintenance'))
@pytest.mark.parametrize('checkpoint', ('hard-exit', 'usage-error', 'settlement-error'))
def test_saved_result_without_final_usage_recovers_unknown_without_resend(fee_api, tmp_path, checkpoint, resume):
    api=fee_api;session,_=sign_in(api);row=reserve(api,session)
    if checkpoint=='hard-exit':
        journal=tmp_path/'provider-calls.log'
        env=os.environ.copy()
        env.update(PR11A_RUNTIME_URL=api.c.pg.runtime_url.render_as_string(hide_password=False),
            PR11A_SCHEDULER_URL=api.c.scheduler.engine.url.render_as_string(hide_password=False),
            PR11A_RECONCILER_URL=api.c.reconciler.url.render_as_string(hide_password=False),
            PR11A_CALL_JOURNAL=str(journal))
        result=subprocess.run([sys.executable,'-B','-c',RAW_COMMITTED_CRASH],env=env,capture_output=True,timeout=30)
        assert result.returncode==73,result.stderr.decode('utf-8',errors='replace')
        assert len(journal.read_text(encoding='utf-8').splitlines())==1
    else:
        class FailingMeter(InvoiceProvider):
            def usage_for(self, task, attempt):
                if checkpoint=='usage-error':
                    raise RuntimeError('synthetic usage callback unavailable')
                return super().usage_for(task,attempt)
        class FailingSettlement(BillingTaskLifecycle):
            def completed(self, owner_id, task_id, usage):
                raise RuntimeError('synthetic settlement callback unavailable')
        provider=FailingMeter()
        lifecycle=FailingSettlement(api.c.reconciler) if checkpoint=='settlement-error' else api.c.lifecycle
        with pytest.raises(RuntimeError,match='synthetic .* callback unavailable'):
            TaskWorker(api.c.pg.db,api.c.scheduler,provider,worker_id='r2-failing-callback',billing=lifecycle).run_once()
        assert provider.calls==1
    before=fee_snapshot(api,row)
    assert before['task_state']=='running' and before['state']=='submitted'
    with api.c.pg.admin.begin() as connection:
        raw=connection.execute(sa.text('''SELECT p.validation_status FROM provider_results p JOIN task_attempts a
            ON a.id=p.attempt_id WHERE a.task_id=:id'''),{'id':row['task_id']}).scalar_one()
        assert raw=='pending'  # proves store_result committed before interruption
        connection.execute(sa.text("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"),
                           {'id':row['task_id']})
    restarted=SavedResultOnlyProvider()
    # Also cover durable polling when the result is published by the existing
    # saved-result path without any live billing callback.
    lifecycle=api.c.lifecycle if resume=='worker' else None
    worker=TaskWorker(api.c.pg.db,api.c.scheduler,restarted,worker_id='r2-restarted',billing=lifecycle)
    worker.recover_expired()
    queued=fee_snapshot(api,row)
    assert queued['task_state']=='queued'
    assert worker.run_once()
    if resume=='maintenance':
        assert fee_snapshot(api,row)['task_state']=='succeeded'
        assert api.c.lifecycle.recover_pending()>=1
    recovered=fee_snapshot(api,row)
    assert recovered['task_state']=='succeeded' and recovered['state']=='unknown'
    if resume=='worker':
        assert queued['state']=='unknown'
    assert (recovered['held'],recovered['unknown'],recovered['settled'])==(0,100,0)
    assert restarted.calls==0 and recovered['attempt_id']==before['attempt_id']
    with api.c.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT count(*) FROM task_attempts WHERE task_id=:id'),{'id':row['task_id']})==1
        assert connection.scalar(sa.text('SELECT count(*) FROM usage_receipts WHERE reservation_id=:id'),row)==0
    api.c.lifecycle.synchronize(api.a.uid,row['task_id'])
    invoice=Usage(provider_request_id=str(recovered['attempt_id']),meter='total',revision=1,quantities={'input_tokens':40},final=True)
    api.c.lifecycle.late_usage(api.a.uid,row['task_id'],invoice)
    api.c.lifecycle.late_usage(api.a.uid,row['task_id'],invoice)
    api.c.lifecycle.synchronize(api.a.uid,row['task_id'])
    api.c.lifecycle.completed(api.a.uid,row['task_id'],None)
    api.c.lifecycle.recover_pending()
    settled=fee_snapshot(api,row)
    assert settled['state']=='settled' and settled['settled_microyuan']==40
    assert (settled['held'],settled['unknown'],settled['settled'])==(0,0,40)
    with api.c.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT count(*) FROM usage_receipts WHERE reservation_id=:id'),row)==1
    assert not TaskWorker(api.c.pg.db,api.c.scheduler,restarted,worker_id='r2-again',billing=api.c.lifecycle).run_once()
    assert restarted.calls==0


@pytest.mark.parametrize('final_state', ('succeeded', 'failed'))
def test_recovered_saved_result_preserves_settled_fee(fee_api, final_state):
    api=fee_api;session,_=sign_in(api);row=reserve(api,session)
    claim=api.c.scheduler.claim('r2-settled',30,1)[0]
    with api.c.pg.db.transaction(OwnerContext(api.a.uid)) as tx:
        task=tx.tasks.get(claim.id);attempt=tx.tasks.prepare_attempt(claim.id,claim.lease_token,'r2-settled')
        api.c.lifecycle.before_submit(tx,task,attempt,claim,'r2-settled')
    with api.c.pg.db.transaction(OwnerContext(api.a.uid)) as tx:
        tx.tasks.store_result(task.id,attempt.id,'{"synthetic":true}' if final_state=='succeeded' else 'invalid synthetic output')
    api.c.lifecycle.completed(api.a.uid,task.id,Usage(provider_request_id=str(attempt.id),meter='total',revision=1,
        quantities={'input_tokens':40},final=True))
    with api.c.pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"),{'id':task.id})
    provider=SavedResultOnlyProvider()
    assert TaskWorker(api.c.pg.db,api.c.scheduler,provider,worker_id='r2-settled-restart',billing=api.c.lifecycle).run_once()
    api.c.lifecycle.synchronize(api.a.uid,task.id)
    api.c.lifecycle.completed(api.a.uid,task.id,None)
    result=fee_snapshot(api,row)
    assert result['task_state']==final_state and result['state']=='settled' and result['settled_microyuan']==40
    assert (result['held'],result['unknown'],result['settled'])==(0,0,40)
    assert provider.calls==0


def test_quota_http_calls_real_budget_and_preserves_csrf_boundary(fee_api):
    api=fee_api
    session,_=sign_in(api)
    assert api.client.get(BASE+'/me/quota').json()['buckets'][3]['limit']>0
    row=reserve(api,session)
    response=api.client.get(BASE+'/me/quota')
    assert response.status_code==200,response.text
    bucket=next(b for b in response.json()['buckets'] if b['kind']=='interactive_month_count')
    assert bucket['reserved']==1 and bucket['remaining']==19
    rejected=api.client.post(BASE+f"/tasks/{row['task_id']}/cancel",json={},headers={'Idempotency-Key':str(uuid4())})
    assert rejected.status_code==403
    response=api.client.post(BASE+f"/tasks/{row['task_id']}/cancel",json={},headers=headers(session))
    assert response.status_code==200,response.text
    assert response.json()['state']=='cancelled'
    with api.c.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT state FROM quota_reservations WHERE id=:id'),row)=='released'


def test_normal_worker_commits_budget_and_permit_before_provider(fee_api):
    api=fee_api;session,_=sign_in(api);row=reserve(api,session)
    class InspectProvider(InvoiceProvider):
        def submit(self,task,attempt):
            with api.c.pg.admin.connect() as connection:
                assert connection.scalar(sa.text('SELECT state FROM quota_reservations WHERE id=:id'),row)=='submitted'
                assert connection.scalar(sa.text('SELECT count(*) FROM billing_permits WHERE reservation_id=:id'),row)==1
            return super().submit(task,attempt)
    provider=InspectProvider()
    worker=TaskWorker(api.c.pg.db,api.c.scheduler,provider,worker_id='billing-normal',billing=api.c.lifecycle)
    assert worker.run_once() and provider.calls==1
    with api.c.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT settled_microyuan FROM quota_reservations WHERE id=:id'),row)==40


def test_reserved_task_without_hooks_fails_closed(fee_api):
    api=fee_api;session,_=sign_in(api);reserve(api,session)
    provider=MockProvider()
    TaskWorker(api.c.pg.db,api.c.scheduler,provider,worker_id='missing-hook').run_once()
    assert provider.calls==0


def test_expiry_recovery_links_existing_task_unknown_to_ledger(fee_api):
    api=fee_api;session,_=sign_in(api);row=reserve(api,session)
    claim=api.c.scheduler.claim('expired-billing',30,1)[0]
    with api.c.pg.db.transaction(OwnerContext(api.a.uid)) as tx:
        task=tx.tasks.get(claim.id);attempt=tx.tasks.prepare_attempt(claim.id,claim.lease_token,'expired-billing')
        api.c.lifecycle.before_submit(tx,task,attempt,claim,'expired-billing')
    with api.c.pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"),{'id':claim.id})
    TaskWorker(api.c.pg.db,api.c.scheduler,MockProvider(),worker_id='recovery-billing',billing=api.c.lifecycle).recover_expired()
    with api.c.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT state FROM quota_reservations WHERE id=:id'),row)=='unknown'


def test_revocation_then_late_usage_uses_metadata_only_reconciler(fee_api):
    api=fee_api;session,_=sign_in(api);row=reserve(api,session)
    class RevokingProvider(InvoiceProvider):
        def submit(self,task,attempt):
            with api.c.pg.admin.begin() as connection:
                connection.execute(sa.text("UPDATE users SET state='disabled' WHERE id=:id"),{'id':api.a.uid})
            return super().submit(task,attempt)
    provider=RevokingProvider()
    TaskWorker(api.c.pg.db,api.c.scheduler,provider,worker_id='revoked-billing',billing=api.c.lifecycle).run_once()
    with api.c.pg.admin.connect() as connection:
        state=connection.execute(sa.text('SELECT state,attempt_id FROM quota_reservations WHERE id=:id'),row).mappings().one()
    assert state['state']=='unknown'
    api.c.lifecycle.late_usage(api.a.uid,row['task_id'],Usage(provider_request_id=str(state['attempt_id']),meter='total',revision=1,
        quantities={'input_tokens':40},final=True))
    assert api.client.get(BASE+'/me/quota').status_code==401
    with api.c.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT settled_microyuan FROM quota_reservations WHERE id=:id'),row)==40
    with api.c.reconciler.connect() as connection,pytest.raises(sa.exc.DBAPIError):
        connection.execute(sa.text('SELECT input_ref FROM tasks'))


def test_admin_switches_require_bound_proof_and_idempotent_reason(fee_api):
    api=fee_api;session,seed=sign_in(api,administrator=True)
    current=api.client.get(BASE+'/admin/switches').json()
    body=dict(base_revision=current['revision'],billing_enabled=False,reason='Synthetic emergency pause')
    assert api.client.patch(BASE+'/admin/switches',json=body,headers=headers(session)).status_code==403
    operation=dict(purpose='adminUpdateSwitches',target=dict(kind='account',id=str(api.a.uid)),
        candidate_parameters=dict(idempotency_key=str(uuid4()),body=body))
    missing_mfa=api.client.post(BASE+'/auth/reauth',json=dict(password=PASSWORD,operation=operation),headers=headers(session))
    assert missing_mfa.status_code==401,missing_mfa.text
    assert missing_mfa.json()['error']['code']=='UNAUTHENTICATED'
    key=str(uuid4());proof=prove(api,session,seed,'adminUpdateSwitches',body,key)
    wrong=api.client.patch(BASE+'/admin/switches',json=body|{'reason':'Changed binding'},headers=headers(session,**proof))
    assert wrong.status_code==403,wrong.text
    response=api.client.patch(BASE+'/admin/switches',json=body,headers=headers(session,**proof))
    assert response.status_code==200,response.text
    assert not response.json()['billing_enabled']
    replay=api.client.patch(BASE+'/admin/switches',json=body,headers=headers(session,**proof))
    assert replay.status_code==200,replay.text
    with api.c.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT count(*) FROM billing_control.audit WHERE op_id=:id'),{'id':key})==1
    with pytest.raises(Exception):
        reserve(api,session)


def test_student_and_gateway_cannot_bypass_admin_mfa(fee_api):
    api=fee_api;session,_=sign_in(api)
    assert api.client.get(BASE+'/admin/switches').status_code==403
    body=dict(base_revision=1,billing_enabled=False,reason='Synthetic denial')
    assert api.client.patch(BASE+'/admin/switches',json=body,headers=headers(session)).status_code==403
    for query in ('SELECT * FROM auth_sessions','SELECT * FROM billing_control.switches'):
        with api.c.gateway.engine.connect() as connection,pytest.raises(sa.exc.DBAPIError):
            connection.execute(sa.text(query))


def test_admin_quota_adjustment_binds_target_units_and_key(fee_api):
    api=fee_api;session,seed=sign_in(api,administrator=True)
    assert api.client.get(BASE+'/admin/users/invalid-uuid/quota').status_code==422
    response=api.client.get(BASE+f'/admin/users/{api.a.uid}/quota')
    assert response.status_code==200,response.text
    bucket=next(b for b in response.json()['buckets'] if b['kind']=='interactive_month_count')
    body=dict(user_id=str(api.a.uid),bucket_kind=bucket['kind'],period_start=bucket['period_start'],additional_units=3,reason='Synthetic quota increase')
    key=str(uuid4());proof=prove(api,session,seed,'adminAdjustQuota',body,key)
    wrong=api.client.post(BASE+'/admin/quota-adjustments',json=body|{'additional_units':4},headers=headers(session,**proof))
    assert wrong.status_code==403,wrong.text
    with api.c.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT count(*) FROM quota_periods WHERE owner_id=:id'),{'id':api.a.uid})==0
    response=api.client.post(BASE+'/admin/quota-adjustments',json=body,headers=headers(session,**proof))
    assert response.status_code==201,response.text
    assert next(b for b in response.json()['buckets'] if b['kind']==bucket['kind'])['limit']==23
    assert api.client.post(BASE+'/admin/quota-adjustments',json=body,headers=headers(session,**proof)).status_code==201


def test_admin_usage_uses_money_strings_and_calendar_maintenance_is_idempotent(fee_api):
    api=fee_api;session,_=sign_in(api,administrator=True)
    first=api.c.maintenance.run_once();second=api.c.maintenance.run_once()
    assert first['cycle']['period_start']==second['cycle']['period_start'] and not second['cycle']['opened']
    response=api.client.get(BASE+'/admin/usage')
    assert response.status_code==200,response.text
    assert response.json()['budget_microyuan']=='500000000'
    assert isinstance(response.json()['reserved_microyuan'],str)


def test_threshold_outbox_is_transactional_and_acknowledged_once(fee_api):
    api=fee_api;session,_=sign_in(api);row=reserve(api,session)
    claim=api.c.scheduler.claim('notify-billing',30,1)[0]
    with api.c.pg.db.transaction(OwnerContext(api.a.uid)) as tx:
        task=tx.tasks.get(claim.id);attempt=tx.tasks.prepare_attempt(task.id,claim.lease_token,'notify-billing')
        api.c.lifecycle.before_submit(tx,task,attempt,claim,'notify-billing')
    api.c.lifecycle.late_usage(api.a.uid,row['task_id'],Usage(provider_request_id=str(attempt.id),meter='total',revision=1,
        quantities={'input_tokens':300_000_000},final=True))
    first=api.c.maintenance.run_once()['notifications'];second=api.c.maintenance.run_once()['notifications']
    assert {r['id'] for r in first}=={r['id'] for r in second}
    assert {r['threshold'] for r in first}>={60,80}
    for message in first:api.c.maintenance.acknowledge(message['id'])
    assert api.c.maintenance.run_once()['notifications']==[]


def test_expired_admin_proof_changes_no_switches(fee_api):
    api=fee_api;session,seed=sign_in(api,administrator=True)
    before=api.client.get(BASE+'/admin/switches').json()
    body=dict(base_revision=before['revision'],ai_summary_enabled=False,reason='Synthetic expiration check')
    key=str(uuid4());proof=prove(api,session,seed,'adminUpdateSwitches',body,key)
    with api.c.pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE reauth_proofs SET verified_at=clock_timestamp()-interval '2 minutes',expires_at=clock_timestamp()-interval '1 second' WHERE owner_id=:id AND operation_key=:key"),
            {'id':api.a.uid,'key':key})
    response=api.client.patch(BASE+'/admin/switches',json=body,headers=headers(session,**proof))
    assert response.status_code==403,response.text
    assert api.client.get(BASE+'/admin/switches').json()==before


def test_composed_media_and_billing_migration_candidate(pg,tmp_path):
    import shutil
    from alembic import command
    from conftest import migration_config
    from classroom_web.infrastructure.database import Database
    name='pr02a_fee_composed_'+uuid4().hex[:24]
    control=sa.create_engine(pg.admin_url,isolation_level='AUTOCOMMIT')
    admin=database=None
    migrations=tmp_path/'migrations'
    shutil.copytree(ROOT/'web/backend/migrations',migrations)
    candidate=migrations/'versions/pr11a_review_candidate.py'
    candidate.write_text("from alembic import op\nfrom classroom_web.infrastructure.billing_schema import candidate_ddl,integration_ddl\n"
        "revision='pr11a_review_candidate'\ndown_revision='0009_private_media'\nbranch_labels=None\ndepends_on=None\n"
        "def upgrade():\n    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(candidate_ddl()+integration_ddl())\n"
        "def downgrade():\n    raise RuntimeError('preserve billing history')\n",encoding='utf-8')
    try:
        with control.connect() as connection:connection.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE template0')
        url=pg.admin_url.set(database=name)
        admin=sa.create_engine(url)
        cfg=migration_config(url.render_as_string(hide_password=False),migrations)
        with admin.connect() as connection:
            cfg.attributes['connection']=connection
            command.upgrade(cfg,'0008_manual_recovery')
        owner=OwnerContext(uuid4())
        with admin.begin() as connection:
            connection.execute(sa.text("INSERT INTO users(id,role,state) VALUES(:id,'student','active')"),{'id':owner.owner_id})
        database=Database(pg.runtime_url.set(database=name).render_as_string(hide_password=False),cursor_secret=b'pr11a-composed-synthetic-cursor-key')
        key=uuid4()
        with database.transaction(owner) as tx:task=tx.tasks.enqueue('export',{'schema_version':1},op_id=key)
        with admin.connect() as connection:
            cfg.attributes['connection']=connection
            command.upgrade(cfg,'head')
        with database.transaction(owner) as tx:
            assert tx.tasks.enqueue('export',{'schema_version':1},op_id=key).id==task.id
        with admin.connect() as connection:
            assert connection.scalar(sa.text('SELECT version_num FROM alembic_version'))=='pr11a_review_candidate'
            assert connection.scalar(sa.text("SELECT to_regclass('public.media_assets') IS NOT NULL"))
            assert connection.scalar(sa.text("SELECT to_regclass('billing_control.notification_outbox') IS NOT NULL"))
    finally:
        if database is not None:database.dispose()
        if admin is not None:admin.dispose()
        with control.connect() as connection:connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        control.dispose()
