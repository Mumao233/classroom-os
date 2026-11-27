"""Incremental PR-11a entrypoint and lifecycle tests, with real PG and identity."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pyotp
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from conftest import migrated_database

from classroom_web.application.billing import BillingService
from classroom_web.application.billing_browser import BillingAdmin, BillingBrowser
from classroom_web.application.billing_tasks import BillingTaskLifecycle
from classroom_web.application.qa_browser import QaBrowser
from classroom_web.application.task_worker import MockProvider, TaskWorker
from classroom_web.domain.auth import BrowserContext
from classroom_web.domain.billing import BillingBlocked, ReservationRequest, Usage
from classroom_web.domain.data import OwnerContext
from classroom_web.entrypoints.http import create_app
from classroom_web.infrastructure.billing_control import BillingMaintenance, PgBillingAdminRepository
from classroom_web.infrastructure.billing_repository import PgBillingRepository
from classroom_web.infrastructure.billing_schema import candidate_ddl, integration_ddl
from classroom_web.infrastructure.qa_repository import PgQaRepository
from classroom_web.infrastructure.task_scheduler import PgTaskScheduler
from classroom_web.test_support import SYNTHETIC_PASSWORD
from test_authentication import auth, install_factor, PASSWORD, BASE

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location('pr11a_contract_candidate',ROOT/'tools/pr11a/contract_candidate.py')
contract_candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract_candidate)
qa_spec = importlib.util.spec_from_file_location('pr07a_billing_contract_candidate', ROOT/'tools/pr07a/contract_candidate.py')
qa_contract_candidate = importlib.util.module_from_spec(qa_spec)
qa_spec.loader.exec_module(qa_contract_candidate)


@pytest.fixture(scope='module')
def pg():
    """Keep billing tables and function grants out of formal schema regressions."""
    with migrated_database() as database:
        yield database


@contextmanager
def installed_fee_control(pg):
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


@pytest.fixture(scope='module')
def fee_control(pg):
    with installed_fee_control(pg) as control:
        yield control


@pytest.fixture
def fee_api(fee_control,auth,request):
    a,c = auth,fee_control
    with c.pg.admin.begin() as connection:
        revision = connection.scalar(sa.text('SELECT revision FROM billing_control.switches'))
        flags = {name:True for name in ('billing','registration','new_capture','asr_streaming','asr_filetrans','ai_interaction','ai_summary')}
        connection.execute(sa.text('SELECT billing_api.update_switches(:actor,:op,:revision,cast(:flags AS jsonb),:reason)'),
            dict(actor=c.actor,op=uuid4(),revision=revision,flags=json.dumps(flags),reason='Synthetic entrypoint setup'))
    browser = BillingBrowser(a.service,c.pg.db,c.lifecycle)
    admin = BillingAdmin(a.service,c.pg.db,c.gateway)
    base=json.loads((ROOT/'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf-8'))
    contract = base if getattr(request, 'param', 'candidate') == 'formal' else contract_candidate.build(base)
    app=create_app(auth=a.service,contract=contract,origin='https://testserver',billing=browser,billing_admin=admin)
    with TestClient(app,base_url='https://testserver') as client:
        yield SimpleNamespace(client=client,a=a,c=c,browser=browser,admin=admin,contract=contract)
    with c.pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE tasks SET not_before=clock_timestamp()+interval '365 days' WHERE owner_id=:owner"),{'owner':a.uid})


@pytest.fixture
def qa_fee_api(fee_api):
    api = fee_api
    config = dict(enabled=False, billing_required=False, price_id=None,
                  price_version='synthetic-no-charge', maximum_cost_microyuan=0,
                  input_token_budget=8192, max_output_tokens=1024,
                  destination=dict(provider='fake', model='qa-deterministic-v1', region='synthetic',
                                   configuration_revision='pr07a-fake-1'))
    qa = QaBrowser(auth=api.a.service, database=api.c.pg.db, config=config)
    contract = qa_contract_candidate.build(api.contract)
    app = create_app(auth=api.a.service, contract=contract, origin='https://testserver',
                     billing=api.browser, qa=qa)
    with TestClient(app, base_url='https://testserver') as client:
        yield SimpleNamespace(client=client, a=api.a, c=api.c, qa=qa)


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


def prove(api,session,seed,purpose,body,key,*,target=None):
    operation=dict(purpose=purpose,target=target or dict(kind='account',id=str(api.a.uid)),candidate_parameters=dict(idempotency_key=key,body=body))
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


def test_disabled_qa_preserves_billing_task_cancellation(qa_fee_api):
    api = qa_fee_api
    session, _ = sign_in(api)
    row = reserve(api, session)
    before = fee_snapshot(api, row)
    assert before['state'] == 'reserved' and before['held'] == 100
    response = api.client.post(BASE+f"/tasks/{row['task_id']}/cancel", json={}, headers=headers(session))
    assert response.status_code == 200, response.text
    assert response.json()['state'] == 'cancelled'
    after = fee_snapshot(api, row)
    assert after['task_state'] == 'cancelled' and after['state'] == 'released'
    assert (after['held'], after['unknown'], after['settled']) == (0, 0, 0)


def test_disabled_qa_rejects_qa_task_cancellation_without_mutation(qa_fee_api):
    api = qa_fee_api
    session, _ = sign_in(api)
    with api.c.pg.db.transaction(OwnerContext(api.a.uid)) as tx:
        semester = tx.catalog.create_semester('Synthetic QA cancellation semester')
        course = tx.catalog.create_course(semester.id, 'Synthetic QA cancellation course')
        classroom = tx.catalog.create_class_session(course.id, 'Synthetic QA cancellation classroom')
        PgQaRepository(tx).add_source(classroom.id, kind='transcript_segment', object_id=uuid4(),
                                     text='边际成本是增加一单位产量带来的额外成本。', start_ms=1000, end_ms=3000)
    api.qa.config['enabled'] = True
    request = dict(kind='ai_question', scope='all', question='边际成本是什么？', seen_sources=[],
                   include_images=False, accept_partial_scope=True, anchor_ms=120000)
    response = api.client.post(BASE+f'/class-sessions/{classroom.id}/generation-plans',
                               json=request, headers=headers(session))
    assert response.status_code == 201, response.text
    plan = response.json()
    response = api.client.post(BASE+f"/generation-plans/{plan['id']}/execute", headers=headers(session),
        json=dict(plan_sha256=plan['plan_sha256'],
                  confirmed_destination_revision=plan['destination']['configuration_revision'],
                  confirm_send=True, confirm_images=False, confirm_original_snapshot=False,
                  confirm_partial_scope=True))
    assert response.status_code == 202, response.text
    task_id = response.json()['id']
    with api.c.pg.admin.connect() as connection:
        before = connection.scalar(sa.text('SELECT row_to_json(t) FROM tasks t WHERE id=:id'), dict(id=task_id))
    assert before['schema_version'] == 'pr07a-1' and before['state'] == 'queued'
    api.qa.config['enabled'] = False
    response = api.client.post(BASE+f'/tasks/{task_id}/cancel', json={}, headers=headers(session))
    assert response.status_code == 503, response.text
    assert response.json()['error']['code'] == 'SERVICE_DISABLED'
    with api.c.pg.admin.connect() as connection:
        after = connection.scalar(sa.text('SELECT row_to_json(t) FROM tasks t WHERE id=:id'), dict(id=task_id))
    assert after == before


@pytest.mark.parametrize('denial, status', (('missing_csrf', 403), ('revoked_session', 401)))
def test_disabled_qa_billing_fallback_keeps_authentication_guards(qa_fee_api, denial, status):
    api = qa_fee_api
    session, _ = sign_in(api)
    row = reserve(api, session)
    before = fee_snapshot(api, row)
    request_headers = headers(session)
    if denial == 'missing_csrf':
        request_headers.pop('X-CSRF-Token')
    else:
        with api.c.pg.admin.begin() as connection:
            connection.execute(sa.text('UPDATE users SET security_epoch=security_epoch+1 WHERE id=:id'),
                               dict(id=api.a.uid))
    response = api.client.post(BASE+f"/tasks/{row['task_id']}/cancel", json={}, headers=request_headers)
    assert response.status_code == status, response.text
    assert fee_snapshot(api, row) == before


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


@pytest.mark.parametrize('fee_api', ['candidate', 'formal'], indirect=True)
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


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
def test_formal_admin_reauth_and_switch_projection(fee_api):
    api = fee_api
    session, seed = sign_in(api, administrator=True)
    response = api.client.get(BASE + '/admin/switches')
    assert response.status_code == 200, response.text
    public_fields = {'revision', 'registration_enabled', 'max_active_users', 'asr_enabled', 'ai_enabled',
                     'billing_enabled', 'new_capture_enabled', 'asr_streaming_enabled', 'asr_filetrans_enabled',
                     'ai_interaction_enabled', 'ai_summary_enabled'}
    assert set(response.json()) == public_fields
    body = dict(base_revision=response.json()['revision'], asr_enabled=False, ai_enabled=False, reason='Synthetic coarse switch')
    key = str(uuid4())
    proof = prove(api, session, seed, 'adminUpdateSwitches', body, key)
    wrong_key = {**proof, 'Idempotency-Key': str(uuid4())}
    rejected = api.client.patch(BASE + '/admin/switches', json=body, headers=headers(session, **wrong_key))
    assert rejected.status_code == 403 and rejected.json()['error']['code'] == 'REAUTH_REQUIRED'
    changed = api.client.patch(BASE + '/admin/switches', json=body, headers=headers(session, **proof))
    assert changed.status_code == 200, changed.text
    assert set(changed.json()) == public_fields
    for field in ('asr_enabled', 'ai_enabled', 'asr_streaming_enabled', 'asr_filetrans_enabled',
                  'ai_interaction_enabled', 'ai_summary_enabled'):
        assert changed.json()[field] is False
    assert api.client.get(BASE + '/admin/switches').json() == changed.json()


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
@pytest.mark.parametrize(('stored', 'requested'), [(True, False), (False, True), (False, False)])
def test_new_capture_switch_declines_transitions_atomically(fee_api, stored, requested):
    api = fee_api
    session, seed = sign_in(api, administrator=True)
    path = BASE + '/admin/switches'
    key = str(uuid4())
    if not stored:
        with api.c.pg.admin.begin() as db:
            db.execute(sa.text("""SELECT billing_api.update_switches(:actor,:op,
                (SELECT revision FROM billing_control.switches),jsonb_build_object('new_capture',false),'Synthetic stored capture flag')"""),
                       dict(actor=api.c.actor, op=uuid4()))
    initial = api.client.get(path).json()
    assert initial['new_capture_enabled'] is stored
    body = dict(base_revision=initial['revision'], new_capture_enabled=requested,
                billing_enabled=not initial['billing_enabled'], reason='Synthetic capture switch edit')
    proof = prove(api, session, seed, 'adminUpdateSwitches', body, key)
    response = api.client.patch(path, json=body, headers=headers(session, **proof))
    if requested == stored:
        assert response.status_code == 200, response.text
        assert response.json() == {**initial, 'revision': initial['revision'] + 1,
                                   'billing_enabled': body['billing_enabled']}
    else:
        assert response.status_code == 503 and response.json()['error']['code'] == 'SERVICE_DISABLED', response.text
        assert api.client.get(path).json() == initial
        with api.c.pg.admin.connect() as db:
            assert db.scalar(sa.text('SELECT count(*) FROM billing_control.audit WHERE op_id=:op'), {'op': key}) == 0
            assert db.scalar(sa.text('SELECT count(*) FROM preauth_receipts WHERE operation_key=:op'), {'op': key}) == 0
            assert db.execute(sa.text('SELECT consumed_at FROM reauth_proofs WHERE operation_key=:op'),
                              {'op': key}).scalar_one() is None


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
@pytest.mark.parametrize('legacy_session_key', [False, True])
def test_switch_receipt_after_fresh_login_rechecks_authority_and_body(fee_api, legacy_session_key):
    api = fee_api
    session, seed = sign_in(api, administrator=True)
    path = BASE + '/admin/switches'
    initial = api.client.get(path).json()
    key = str(uuid4())
    body = dict(base_revision=initial['revision'], ai_summary_enabled=False, reason='Synthetic first edit')
    proof = prove(api, session, seed, 'adminUpdateSwitches', body, key)
    first = api.client.patch(path, json=body, headers=headers(session, **proof))
    assert first.status_code == 200, first.text
    expected = first.json()
    with api.c.pg.admin.begin() as db:
        receipt_context = db.execute(sa.text("""SELECT context_hash FROM preauth_receipts
            WHERE operation='adminUpdateSwitches' AND operation_key=:op"""), {'op': key}).scalar_one()
        assert receipt_context == str(api.a.uid)
        if legacy_session_key:
            db.execute(sa.text("""UPDATE preauth_receipts SET context_hash=(
                SELECT token_hash FROM auth_sessions WHERE owner_id=:owner AND device_id=:device)
                WHERE context_hash=:context AND operation='adminUpdateSwitches' AND operation_key=:op"""),
                       dict(owner=api.a.uid, device=session['device_id'], context=receipt_context, op=key))
        # A different actor can use the same key, even while the first receipt is still needed.
        db.execute(sa.text("""SELECT billing_api.update_switches(:actor,:op,:revision,
            jsonb_build_object('billing',false),'Synthetic second edit')"""), dict(actor=api.c.actor, op=key, revision=expected['revision']))
    current = api.client.get(path).json()
    assert current == {**expected, 'revision': expected['revision'] + 1, 'billing_enabled': False}
    logged_out = api.client.post(BASE + '/auth/logout', json={}, headers=headers(session))
    assert logged_out.status_code == 200, logged_out.text
    # Install a fresh synthetic factor for the second login without recycling a TOTP counter.
    with api.c.pg.admin.begin() as db:
        db.execute(sa.text("""UPDATE mfa_credentials SET state='revoked',revoked_at=clock_timestamp()
            WHERE owner_id=:owner AND state='active'"""), {'owner': api.a.uid})
    session, seed = sign_in(api, administrator=True)
    if legacy_session_key:
        # The old producer could save the later state on a second session-scoped retry.
        with api.c.pg.admin.begin() as db:
            db.execute(sa.text("""INSERT INTO preauth_receipts(context_hash,operation,operation_key,commitment,result)
                SELECT s.token_hash,r.operation,r.operation_key,r.commitment,cast(:current AS jsonb)
                FROM preauth_receipts r JOIN auth_sessions s ON s.owner_id=:owner AND s.device_id=:device
                WHERE r.operation='adminUpdateSwitches' AND r.operation_key=:op"""),
                       dict(owner=api.a.uid, device=session['device_id'], current=json.dumps(current), op=key))
    stale = api.client.patch(path, json=body, headers=headers(session, **proof))
    assert stale.status_code == 403 and stale.json()['error']['code'] == 'REAUTH_REQUIRED', stale.text
    fresh = prove(api, session, seed, 'adminUpdateSwitches', body, key)
    mismatch = api.client.patch(path, json={**body, 'reason': 'Different binding'}, headers=headers(session, **fresh))
    assert mismatch.status_code == 409 and mismatch.json()['error']['code'] == 'IDEMPOTENCY_MISMATCH', mismatch.text
    replay = api.client.patch(path, json=body, headers=headers(session, **fresh))
    assert replay.status_code == 200 and replay.json() == expected, replay.text
    assert api.client.get(path).json() == current
    with api.c.pg.admin.begin() as db:
        assert db.scalar(sa.text('SELECT count(*) FROM billing_control.audit WHERE actor_id=:owner AND op_id=:op'),
                         dict(owner=api.a.uid, op=key)) == 1
        db.execute(sa.text("UPDATE users SET role='student' WHERE id=:owner"), {'owner': api.a.uid})
    denied = api.client.patch(path, json=body, headers=headers(session, **fresh))
    assert denied.status_code == 401 and denied.json()['error']['code'] == 'UNAUTHENTICATED', denied.text


def test_switch_audit_key_is_scoped_apart_from_other_operations(fee_control):
    with fee_control.pg.admin.begin() as db:
        period_op = db.execute(sa.text("""SELECT actor_id,op_id FROM billing_control.audit
            WHERE action='open_period' LIMIT 1""")).mappings().one()
        db.execute(sa.text("""SELECT billing_api.update_switches(:actor_id,:op_id,
            (SELECT revision FROM billing_control.switches),jsonb_build_object('ai_summary',true),'Synthetic scope check')"""), dict(period_op))
        actions = db.scalars(sa.text('SELECT action FROM billing_control.audit WHERE actor_id=:actor_id AND op_id=:op_id'),
                             dict(period_op)).all()
        assert sorted(actions) == ['open_period', 'switches']


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
def test_switch_audit_without_http_snapshot_cannot_be_replayed_as_current(fee_api):
    api = fee_api
    session, seed = sign_in(api, administrator=True)
    path = BASE + '/admin/switches'
    revision = api.client.get(path).json()['revision']
    key = str(uuid4())
    body = dict(base_revision=revision, ai_summary_enabled=False, reason='Synthetic operator edit')
    with api.c.operator.begin() as db:
        db.execute(sa.text("""SELECT billing_api.update_switches(:actor,:op,:revision,
            jsonb_build_object('ai_summary',false),:reason)"""),
                   dict(actor=api.a.uid, op=key, revision=revision, reason=body['reason']))
    proof = prove(api, session, seed, 'adminUpdateSwitches', body, key)
    answer = api.client.patch(path, json=body, headers=headers(session, **proof))
    assert answer.status_code == 503 and answer.json()['error']['code'] == 'RESULT_UNKNOWN', answer.text
    assert api.client.get(path).json()['revision'] == revision + 1


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
def test_mixed_switch_projection_matches_admission_and_replays_the_original_revision(fee_api):
    api = fee_api
    session, seed = sign_in(api, administrator=True)
    initial = api.client.get(BASE + '/admin/switches').json()
    body = dict(base_revision=initial['revision'], asr_filetrans_enabled=False, ai_summary_enabled=False,
                reason='Synthetic pause of two services')
    key = str(uuid4())
    proof = prove(api, session, seed, 'adminUpdateSwitches', body, key)
    response = api.client.patch(BASE + '/admin/switches', json=body, headers=headers(session, **proof))
    assert response.status_code == 200, response.text
    mixed = response.json()
    assert mixed == {**initial, 'revision': initial['revision'] + 1, 'asr_filetrans_enabled': False, 'ai_summary_enabled': False}
    assert mixed['asr_enabled'] and mixed['ai_enabled'] and mixed['asr_streaming_enabled'] and mixed['ai_interaction_enabled']
    assert api.client.get(BASE + '/admin/switches').json() == mixed
    quota = api.client.get(BASE + '/me/quota').json()
    assert quota['asr_enabled'] and quota['ai_enabled']

    price = uuid4()
    with api.c.pg.admin.begin() as db:
        db.execute(sa.text('''INSERT INTO price_catalog(id,provider,model,region,revision,currency,rates,
                minimum_microyuan,synthetic,effective_at) VALUES(:id,'mock','synthetic','synthetic',:revision,'CNY',
                cast(:rates AS jsonb),0,true,'2020-01-01T00:00:00Z')'''),
                   dict(id=price, revision=str(price), rates=json.dumps({'audio_duration_ms': {'microyuan': 1, 'units': 1}})))
    principal = api.a.service.principal(BrowserContext(session=api.client.cookies.get('__Host-classroom_session')))
    billing = BillingService(api.c.pg.db)

    def attempt(purpose):
        audio = purpose.startswith('asr_')
        return billing.reserve(principal, ReservationRequest(op_id=uuid4(), purpose=purpose, price_id=price if audio else api.c.price,
                               maximum_units={'audio_duration_ms' if audio else 'input_tokens': 1},
                               task_max_microyuan=1, input_ref={'schema_version': 1}))

    for enabled in ('asr_live', 'ai_question'):
        assert attempt(enabled)['state'] == 'reserved'
    for disabled in ('asr_retranscribe', 'review_summary'):
        with pytest.raises(BillingBlocked, match='charged operation is paused'):
            attempt(disabled)

    with api.c.pg.admin.begin() as db:
        # Historical receipts saved the AND summaries alongside the original member flags.
        stored = db.execute(sa.text('''UPDATE preauth_receipts SET result=result || cast(:old AS jsonb)
                WHERE operation='adminUpdateSwitches' AND operation_key=:key RETURNING result'''),
                            dict(old=json.dumps({'asr_enabled': False, 'ai_enabled': False}), key=key)).scalar_one()
        assert stored['revision'] == mixed['revision'] and not stored['ai_enabled'] and stored['ai_interaction_enabled']
        db.execute(sa.text('SELECT billing_api.update_switches(:actor,:op,:revision,cast(:flags AS jsonb),:reason)'),
                   dict(actor=api.c.actor, op=uuid4(), revision=mixed['revision'], flags=json.dumps({'billing': False}),
                        reason='Synthetic billing master pause'))
    live = api.client.get(BASE + '/admin/switches').json()
    assert live == {**mixed, 'revision': mixed['revision'] + 1, 'billing_enabled': False}
    quota = api.client.get(BASE + '/me/quota').json()
    assert not quota['asr_enabled'] and not quota['ai_enabled']
    for disabled in ('asr_live', 'ai_question'):
        with pytest.raises(BillingBlocked, match='charged operation is paused'):
            attempt(disabled)
    replay = api.client.patch(BASE + '/admin/switches', json=body, headers=headers(session, **proof))
    assert replay.status_code == 200, replay.text
    assert replay.json() == mixed
    assert api.client.get(BASE + '/admin/switches').json() == live


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
def test_fine_and_coarse_switches_share_a_revision(fee_api):
    api = fee_api
    session, seed = sign_in(api, administrator=True)
    initial = api.client.get(BASE + '/admin/switches').json()
    coarse = dict(base_revision=initial['revision'], asr_enabled=False, reason='Synthetic stale batch')
    proof = prove(api, session, seed, 'adminUpdateSwitches', coarse, str(uuid4()))
    with api.c.pg.admin.begin() as db:
        db.execute(sa.text('SELECT billing_api.update_switches(:actor,:op,:revision,cast(:flags AS jsonb),:reason)'),
                   dict(actor=api.c.actor, op=uuid4(), revision=initial['revision'],
                        flags=json.dumps({'ai_interaction': False}), reason='Synthetic concurrent service edit'))
    response = api.client.patch(BASE + '/admin/switches', json=coarse, headers=headers(session, **proof))
    assert response.status_code == 409 and response.json()['error']['code'] == 'REVISION_CONFLICT', response.text
    assert api.client.get(BASE + '/admin/switches').json() == {
        **initial, 'revision': initial['revision'] + 1, 'ai_interaction_enabled': False}


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
def test_formal_invalid_admin_candidates_are_rejected_before_password_or_totp(fee_api):
    api = fee_api
    session, seed = sign_in(api, administrator=True)
    current = api.client.get(BASE + '/admin/switches').json()
    user_id = str(api.a.uid)
    key = 'ab' + str(uuid4())[2:]
    reason = ' \ufeff Preserve the original reason.\u0085 '
    switch = dict(base_revision=current['revision'], ai_enabled=False, reason=reason)
    quota = dict(user_id=user_id, bucket_kind='interactive_month_count', period_start='2026-11-01T00:00:00+08:00',
                additional_units=1, reason=reason)
    invalid = (
        ('adminUpdateSwitches', 'account', key.upper(), switch),
        ('adminUpdateSwitches', 'account', key, switch | {'reason': '\u0085\u001c'}),
        ('adminUpdateSwitches', 'account', key, switch | {'reason': '\ufeff'}),
        ('adminUpdateSwitches', 'account', key, switch | {'reason': 'Before\x00after'}),
        ('adminUpdateSwitches', 'account', key, dict(base_revision=current['revision'], reason=reason)),
        ('adminUpdateSwitches', 'account', key, switch | {'ai_interaction_enabled': True}),
        ('adminUpdateSwitches', 'account', key, switch | {'asr_enabled': False, 'asr_streaming_enabled': True}),
        ('adminUpdateSwitches', 'account', key, switch | {'base_revision': 2**53}),
        ('adminAdjustQuota', 'account', key, quota | {'reason': '\u001d\u001e\u001f'}),
        ('adminAdjustQuota', 'account', key, quota | {'reason': 'Before\x00after'}),
        ('adminAdjustQuota', 'account', key, quota | {'additional_units': 2**53}),
        ('adminUpdateUser', 'user', key, dict(base_revision=2**63, status='active', reason=reason)),
        ('adminCreateInvitation', 'account', key, dict(kind='single_use', reason='\u2028\u2029')),
        ('adminCreateInvitation', 'account', key, dict(kind='single_use', email='tester@example.test', reason=reason)),
        ('adminCreateInvitation', 'account', key, dict(kind='email_bound', reason=reason)),
        ('adminCreateInvitation', 'account', key, dict(kind='email_bound', email='tester@localhost', reason=reason)),
        ('adminRevokeInvitation', 'invitation', key + '\n', dict(reason=reason)),
        ('adminReissueInvitation', 'invitation', key, dict(reason='\u0085\ufeff')),
    )
    step_query = sa.text('SELECT last_accepted_step FROM mfa_credentials WHERE owner_id=:id')
    with api.c.pg.admin.connect() as db:
        before = db.scalar(step_query, {'id': api.a.uid})
    code = pyotp.TOTP(seed).at(datetime.now(timezone.utc) + timedelta(seconds=30))
    with patch.object(api.a.service, 'check_current_password', wraps=api.a.service.check_current_password) as password_check:
        invalid_candidates = [(*case, user_id) for case in invalid]
        # An all-digit UUID has no uppercase spelling. The fallback keeps this format check deterministic.
        uppercase = user_id.upper() if user_id != user_id.upper() else key.upper()
        invalid_candidates.extend([
            ('adminAdjustQuota', 'account', key, quota, uppercase),
            ('adminUpdateSwitches', 'account', key, switch, uppercase),
            ('adminCreateInvitation', 'account', key, dict(kind='single_use', reason=reason), uppercase),
        ])
        for purpose, kind, future_key, body, target_id in invalid_candidates:
            operation = dict(purpose=purpose, target=dict(kind=kind, id=target_id),
                             candidate_parameters=dict(idempotency_key=future_key, body=body))
            response = api.client.post(BASE + '/auth/reauth', json=dict(password=PASSWORD, mfa_code=code, operation=operation),
                                       headers=headers(session))
            assert response.status_code == 422 and response.json()['error']['code'] == 'VALIDATION_FAILED', (purpose, response.text)
        password_check.assert_not_called()
    with api.c.pg.admin.connect() as db:
        assert db.scalar(step_query, {'id': api.a.uid}) == before
        assert db.scalar(sa.text('SELECT count(*) FROM reauth_proofs WHERE owner_id=:id'), {'id': api.a.uid}) == 0

    # Exercise the mounted write validators with a real, still usable proof.
    proof = prove(api, session, seed, 'adminUpdateSwitches', switch, key)
    for purpose, _, future_key, body in invalid:
        method, path = {
            'adminUpdateSwitches': ('PATCH', '/admin/switches'),
            'adminAdjustQuota': ('POST', '/admin/quota-adjustments'),
            'adminUpdateUser': ('PATCH', '/admin/users/' + user_id),
            'adminCreateInvitation': ('POST', '/admin/invitations'),
            'adminRevokeInvitation': ('POST', f'/admin/invitations/{user_id}/revoke'),
            'adminReissueInvitation': ('POST', f'/admin/invitations/{user_id}/reissue'),
        }[purpose]
        write_headers = headers(session, **{**proof, 'Idempotency-Key': future_key})
        response = api.client.request(method, BASE + path, json=body, headers=write_headers)
        assert response.status_code == 422 and response.json()['error']['code'] == 'VALIDATION_FAILED', response.text
    changed = api.client.patch(BASE + '/admin/switches', json=switch, headers=headers(session, **proof))
    assert changed.status_code == 200, changed.text
    with api.c.pg.admin.connect() as db:
        assert db.scalar(sa.text('SELECT reason FROM billing_control.audit WHERE op_id=:id'), {'id': key}) == reason


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
def test_formal_reauth_without_admin_write_handler_stays_disabled(fee_api):
    api = fee_api
    session, _ = sign_in(api, administrator=True)
    # Both apps share AuthService. Reauthentication must use the consumer mounted on this app.
    api.a.service.admin_reauth = api.admin
    api.a.client.cookies.update(api.client.cookies)
    reason = {'reason': 'Synthetic administration'}
    for purpose, kind, body in (
        ('adminUpdateUser', 'user', dict(base_revision=1, status='disabled', **reason)),
        ('adminCreateInvitation', 'account', dict(kind='single_use', **reason)),
        ('adminRevokeInvitation', 'invitation', reason),
        ('adminReissueInvitation', 'invitation', reason),
    ):
        target_id = str(api.a.uid) if kind == 'account' else str(uuid4())
        operation = dict(purpose=purpose, target=dict(kind=kind, id=target_id),
                         candidate_parameters=dict(idempotency_key=str(uuid4()), body=body))
        response = api.a.client.post(BASE + '/auth/reauth', json=dict(password=PASSWORD, operation=operation),
                                   headers=headers(session))
        assert response.status_code == 503 and response.json()['error']['code'] == 'SERVICE_DISABLED', response.text
    switches = dict(purpose='adminUpdateSwitches', target=dict(kind='account', id=str(api.a.uid)),
                    candidate_parameters=dict(idempotency_key=str(uuid4()), body=dict(base_revision=1, ai_enabled=False, **reason)))
    disabled = api.a.client.post(BASE + '/auth/reauth', json=dict(password=PASSWORD, operation=switches), headers=headers(session))
    assert disabled.status_code == 503 and disabled.json()['error']['code'] == 'SERVICE_DISABLED', disabled.text
    with api.c.pg.admin.connect() as connection:
        assert connection.scalar(sa.text('SELECT count(*) FROM reauth_proofs WHERE owner_id=:id'), {'id': api.a.uid}) == 0


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
def test_formal_admin_proof_requires_an_admin_session(fee_api):
    api = fee_api
    operation = dict(purpose='adminUpdateSwitches', target=dict(kind='account', id=str(api.a.uid)),
                     candidate_parameters=dict(idempotency_key=str(uuid4()),
                        body=dict(base_revision=1, ai_enabled=False, reason='Synthetic denial')))
    payload = dict(password=PASSWORD, operation=operation)
    csrf = api.client.get(BASE + '/auth/csrf').json()['csrf_token']
    anonymous = api.client.post(BASE + '/auth/reauth', json=payload, headers=headers({'csrf_token': csrf}))
    assert anonymous.status_code == 403
    session, _ = sign_in(api)
    student = api.client.post(BASE + '/auth/reauth', json=payload, headers=headers(session))
    assert student.status_code == 403 and student.json()['error']['code'] == 'FORBIDDEN'


def test_admin_usage_uses_money_strings_and_calendar_maintenance_is_idempotent(fee_api):
    api=fee_api;session,_=sign_in(api,administrator=True)
    first=api.c.maintenance.run_once();second=api.c.maintenance.run_once()
    assert first['cycle']['period_start']==second['cycle']['period_start'] and not second['cycle']['opened']
    response=api.client.get(BASE+'/admin/usage')
    assert response.status_code==200,response.text
    assert response.json()['budget_microyuan']=='500000000'
    assert isinstance(response.json()['reserved_microyuan'],str)


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
def test_formal_usage_risk_includes_settled_once_and_excludes_safety(fee_api, monkeypatch):
    api = fee_api
    session, _ = sign_in(api, administrator=True)
    # Use a separate budget month with different fixed and safety amounts.
    start = datetime.fromisoformat('2048-02-01T00:00:00+08:00')
    monkeypatch.setattr(PgBillingRepository, '_now', lambda self: start + timedelta(days=1))
    with api.c.pg.admin.begin() as connection:
        connection.execute(sa.text('SELECT billing_api.open_period(:start,30,:actor,:op,:reason)'),
                           dict(start=start, actor=api.c.actor, op=uuid4(), reason='Synthetic usage projection'))
    row = reserve(api, session)
    worker = 'formal-usage'
    claim = api.c.scheduler.claim(worker, 30, 1)[0]
    assert claim.id == row['task_id']
    with api.c.pg.db.transaction(OwnerContext(api.a.uid)) as tx:
        task = tx.tasks.get(claim.id)
        attempt = tx.tasks.prepare_attempt(claim.id, claim.lease_token, worker)
        api.c.lifecycle.before_submit(tx, task, attempt, claim, worker)
    partial = Usage(provider_request_id=str(attempt.id), meter='total', revision=1,
                    quantities={'input_tokens': 40}, final=False)
    api.c.lifecycle.completed(api.a.uid, claim.id, partial)
    api.c.lifecycle.completed(api.a.uid, claim.id, None)
    reserve(api, session)

    response = api.client.get(BASE + '/admin/usage', params={'period_start': '2048-02-01'})
    assert response.status_code == 200, response.text
    usage = response.json()
    assert usage['budget_microyuan'] == '2000000000'
    assert usage['safety_reserve_microyuan'] == '400000000'
    risk_fields = ('fixed_and_accrued_microyuan', 'settled_microyuan', 'reserved_microyuan', 'unknown_microyuan')
    assert tuple(usage[field] for field in risk_fields) == ('300000000', '40', '100', '60')
    with api.c.pg.admin.connect() as connection:
        budget = connection.scalar(sa.text('SELECT billing_api.lock_budget(:start)'), {'start': start})
    assert sum(int(usage[field]) for field in risk_fields) == budget['risk'] == 300000200


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
    from alembic.script import ScriptDirectory
    from conftest import migration_config
    from classroom_web.infrastructure.database import Database
    name='pr02a_fee_composed_'+uuid4().hex[:24]
    control=sa.create_engine(pg.admin_url,isolation_level='AUTOCOMMIT')
    admin=database=None
    migrations=tmp_path/'migrations'
    shutil.copytree(ROOT/'web/backend/migrations',migrations)
    base_config=migration_config(pg.admin_url.render_as_string(hide_password=False),migrations)
    heads=ScriptDirectory.from_config(base_config).get_heads()
    assert len(heads)==1
    candidate=migrations/'versions/pr11a_review_candidate.py'
    candidate.write_text("from alembic import op\nfrom classroom_web.infrastructure.billing_schema import candidate_ddl,integration_ddl\n"
        f"revision='pr11a_review_candidate'\ndown_revision={heads[0]!r}\nbranch_labels=None\ndepends_on=None\n"
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


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
def test_admin_numbers_preserve_precision_before_factors_and_writes(fee_api):
    api = fee_api
    session, seed = sign_in(api, administrator=True)
    user_id, key = str(api.a.uid), str(uuid4())
    reason = 'Synthetic decimal precision'
    switch = dict(base_revision='__wire_number__', ai_enabled=False, reason=reason)
    user = dict(base_revision='__wire_number__', status='active', reason=reason)
    quota = dict(user_id=user_id, bucket_kind='interactive_month_count', period_start='2026-11-01T00:00:00+08:00',
                 additional_units='__wire_number__', reason=reason)

    def number_on_wire(body, number):
        return json.dumps(body).replace('"__wire_number__"', number)

    def proof_body(purpose, kind, body):
        return dict(password=PASSWORD, mfa_code=pyotp.TOTP(seed).at(datetime.now(timezone.utc) + timedelta(seconds=30)),
                    operation=dict(purpose=purpose, target=dict(kind=kind, id=user_id),
                                   candidate_parameters=dict(idempotency_key=key, body=body)))

    routes = (
        ('POST', '/auth/reauth', proof_body('adminUpdateUser', 'user', user)),
        ('POST', '/auth/reauth', proof_body('adminUpdateSwitches', 'account', switch)),
        ('POST', '/auth/reauth', proof_body('adminAdjustQuota', 'account', quota)),
        ('PATCH', '/admin/users/' + user_id, user),
        ('PATCH', '/admin/switches', switch),
        ('POST', '/admin/quota-adjustments', quota),
    )
    with (patch.object(api.a.service, 'check_current_password', wraps=api.a.service.check_current_password) as password,
          patch.object(api.admin, 'apply', wraps=api.admin.apply) as apply):
        for method, path, body in routes:
            for number in ('1.00000000000000001', '999999999999999999e-18',
                           '9007199254740991.0000000001', '1e999999999'):
                request_headers = headers(session, **{'Content-Type': 'application/json'})
                if path != '/auth/reauth':
                    request_headers['Idempotency-Key'] = key
                response = api.client.request(method, BASE + path, content=number_on_wire(body, number),
                                              headers=request_headers)
                assert response.status_code == 422 and response.json()['error']['code'] == 'VALIDATION_FAILED', (
                    path, number, response.text)
        password.assert_not_called()
        apply.assert_not_called()

    # The exact maximum in exponent notation reaches the normal proof commitment as an integer.
    with patch.object(api.a.crypto, 'commitment', wraps=api.a.crypto.commitment) as commitment:
        body = proof_body('adminAdjustQuota', 'account', quota)
        response = api.client.post(BASE + '/auth/reauth', content=number_on_wire(body, '90071992547409910e-1'),
                                   headers=headers(session, **{'Content-Type': 'application/json'}))
        assert response.status_code == 200, response.text
        amounts = [call.args[1]['candidate_parameters']['body']['additional_units']
                   for call in commitment.call_args_list if call.args[0] == 'operation']
        assert amounts and all(type(amount) is int and amount == 2**53 - 1 for amount in amounts)


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
@pytest.mark.parametrize('purpose', ['adminUpdateSwitches', 'adminAdjustQuota'])
def test_admin_integral_wire_numbers_bind_and_replay_as_integers(fee_api, purpose):
    api = fee_api
    session, seed = sign_in(api, administrator=True)
    user_id = str(api.a.uid)
    key = str(uuid4())
    reason = 'Synthetic integral wire number'
    if purpose == 'adminUpdateSwitches':
        field, method, path, success = 'base_revision', 'PATCH', '/admin/switches', 200
        value = api.client.get(BASE + path).json()['revision']
        body = dict(base_revision=value, ai_enabled=False, reason=reason)
    else:
        field, method, path, success = 'additional_units', 'POST', '/admin/quota-adjustments', 201
        quota = api.client.get(BASE + '/admin/users/' + user_id + '/quota').json()
        bucket = next(item for item in quota['buckets'] if item['kind'] == 'interactive_month_count')
        value = 1
        body = dict(user_id=user_id, bucket_kind=bucket['kind'], period_start=bucket['period_start'],
                    additional_units=value, reason=reason)

    # Bypass TestClient's JSON encoder to send the exact integer, decimal and exponent spellings.
    marker = '__raw_json_number__'
    template = {**body, field: marker}
    operation = dict(purpose=purpose, target=dict(kind='account', id=user_id),
                     candidate_parameters=dict(idempotency_key=key, body=template))
    code = pyotp.TOTP(seed).at(datetime.now(timezone.utc) + timedelta(seconds=30))
    proof_json = json.dumps(dict(password=PASSWORD, mfa_code=code, operation=operation)).replace(json.dumps(marker), f'{value}.0')
    with patch.object(api.a.crypto, 'commitment', wraps=api.a.crypto.commitment) as commitment:
        proof = api.client.post(BASE + '/auth/reauth', content=proof_json,
                                headers=headers(session, **{'Content-Type': 'application/json'}))
        assert proof.status_code == 200, proof.text
        write_headers = headers(session, **{'Content-Type': 'application/json', 'Idempotency-Key': key,
                                            'X-Reauth-Token': proof.json()['reauth_token']})
        write_json = json.dumps(template).replace(json.dumps(marker), f'{value}e0')
        changed = api.client.request(method, BASE + path, content=write_json, headers=write_headers)
        assert changed.status_code == success, changed.text
        replay = api.client.request(method, BASE + path, content=json.dumps(body), headers=write_headers)
        assert replay.status_code == success and replay.json() == changed.json(), replay.text
        bound_values = [call.args[1]['candidate_parameters']['body'][field] for call in commitment.call_args_list
                        if call.args[0] == 'operation' and call.args[1]['purpose'] == purpose]
        assert len(bound_values) >= 3 and all(type(number) is int and number == value for number in bound_values)
    if purpose == 'adminAdjustQuota':
        updated = next(item for item in changed.json()['buckets'] if item['kind'] == bucket['kind'])
        assert updated['limit'] == bucket['limit'] + value
    else:
        assert changed.json()['revision'] == value + 1 and changed.json()['ai_enabled'] is False
    with api.c.pg.admin.connect() as db:
        assert db.scalar(sa.text('SELECT count(*) FROM billing_control.audit WHERE op_id=:id'), {'id': key}) == 1


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
def test_cumulative_quota_overflow_rolls_back_new_period_and_keeps_proof(fee_api):
    api = fee_api
    session, seed = sign_in(api, administrator=True)
    user_id, key = str(api.a.uid), str(uuid4())
    quota = api.client.get(BASE + '/admin/users/' + user_id + '/quota').json()
    bucket = next(item for item in quota['buckets'] if item['kind'] == 'interactive_month_count')
    delta = 2**53 - bucket['limit']  # The delta fits, the proposed total exceeds the maximum by one.
    body = dict(user_id=user_id, bucket_kind=bucket['kind'], period_start=bucket['period_start'],
                additional_units=delta, reason='Synthetic cumulative overflow')
    proof = prove(api, session, seed, 'adminAdjustQuota', body, key)
    for _ in range(2):
        response = api.client.post(BASE + '/admin/quota-adjustments', json=body, headers=headers(session, **proof))
        assert response.status_code == 422 and response.json()['error']['code'] == 'VALIDATION_FAILED', response.text
    with api.c.pg.admin.connect() as db:
        assert db.scalar(sa.text('SELECT count(*) FROM quota_periods WHERE owner_id=:id'), {'id': api.a.uid}) == 0
        assert db.scalar(sa.text('SELECT count(*) FROM quota_adjustments WHERE op_id=:id'), {'id': key}) == 0
        assert db.scalar(sa.text('SELECT count(*) FROM billing_control.audit WHERE op_id=:id'), {'id': key}) == 0
        assert db.scalar(sa.text('SELECT count(*) FROM preauth_receipts WHERE operation_key=:id'), {'id': key}) == 0
        assert db.execute(sa.text('SELECT consumed_at FROM reauth_proofs WHERE operation_key=:id'), {'id': key}).scalar_one() is None


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
def test_concurrent_adjustments_only_spend_remaining_safe_integer_headroom_once(fee_api):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    api = fee_api
    session, _ = sign_in(api, administrator=True)
    user_id = str(api.a.uid)
    quota = api.client.get(BASE + '/admin/users/' + user_id + '/quota').json()
    bucket = next(item for item in quota['buckets'] if item['kind'] == 'interactive_month_count')
    period_id, ops, maximum = uuid4(), (uuid4(), uuid4()), 2**53 - 1
    with api.c.pg.admin.begin() as db:
        db.execute(sa.text('''INSERT INTO quota_periods(id,owner_id,resource,period_start,period_end,limit_value)
            VALUES(:id,:owner,:resource,:start,:end,:lim)'''),
            dict(id=period_id, owner=user_id, resource=bucket['kind'], start=bucket['period_start'],
                 end=bucket['period_end'], lim=maximum - 1))

    def apply(op_id, barrier=None):
        try:
            with api.c.operator.begin() as db:
                db.execute(sa.text("SELECT set_config('app.owner_id',:owner,true)"), {'owner': user_id})
                if barrier is not None:
                    barrier.wait(timeout=10)
                db.execute(sa.text("SELECT billing_api.adjust_quota(:actor,:op,:period,1,'Synthetic last unit')"),
                           dict(actor=api.c.actor, op=op_id, period=period_id))
            return True
        except sa.exc.DBAPIError as error:
            assert error.orig.sqlstate == '22003' and error.orig.diag.message_primary == 'QUOTA_LIMIT_OUT_OF_RANGE'
            return False

    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(apply, op, barrier) for op in ops]
        accepted = [future.result(timeout=15) for future in futures]
    assert sorted(accepted) == [False, True]
    assert apply(ops[accepted.index(True)])  # An exact audit replay is valid even at the maximum.
    with api.c.pg.admin.connect() as db:
        assert db.scalar(sa.text('SELECT limit_value FROM quota_periods WHERE id=:id'), {'id': period_id}) == maximum
        assert db.scalar(sa.text('SELECT count(*) FROM quota_adjustments WHERE quota_period_id=:id'), {'id': period_id}) == 1
        assert db.scalar(sa.text('SELECT count(*) FROM billing_control.audit WHERE op_id IN (:a,:b)'),
                         {'a': ops[0], 'b': ops[1]}) == 1
