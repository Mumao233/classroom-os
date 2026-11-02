"""Independent review probes. Synthetic data only; no external service calls."""
import json
from pathlib import Path
import pytest
import sqlalchemy as sa

from classroom_web.domain.data import Gone
from classroom_web.infrastructure.local_reconciler import PgLocalResultReconciler
from test_identity_recovery import (
    complete_identity, saved_result, quarantine_saved, invalidate_identity,
    parity_expected, RecordingProvider,
)
from test_persistent_tasks import (
    task_db, scheduler, enqueue, claim_one, prepared, submitting, persisted, expire, WORKER,
)
from test_task_material_remediation import moved_source, enqueue_source, revoke_source
from test_task_worker import worker

OUT = Path(__file__).resolve().parents[4] / 'outputs/reviewer'

def evidence(name, data):
    (OUT / (name + '.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

def test_new_definer_owners_are_restricted(pg):
    with pg.admin.connect() as conn:
        rows = [dict(r) for r in conn.execute(sa.text("""
            SELECT p.proname, r.rolname, r.rolsuper, r.rolbypassrls, p.prosecdef,
                   p.proconfig, p.proacl::text AS acl
            FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
            WHERE p.proname IN ('classroom_web_identity_usable','classroom_web_attempt_submitted',
              'classroom_web_local_reconcile_candidates','classroom_web_reconcile_saved_result')
            ORDER BY p.proname
        """)).mappings()]
    evidence('function-owners', rows)
    assert len(rows) == 4
    assert all(not r['rolsuper'] and not r['rolbypassrls'] for r in rows), rows

@pytest.mark.parametrize('target', ['material', 'course', 'semester'])
def test_local_finish_keeps_revoked_frozen_source_guard(target, task_db, scheduler, owners, tree, pg):
    complete_identity(pg, owners.a)
    source = moved_source(task_db, owners.a, tree)
    task = enqueue_source(task_db, owners.a, source, False)
    claim = claim_one(scheduler, task)
    attempt = prepared(task_db, owners.a, task, claim)
    submitting(task_db, owners.a, task, attempt, claim)
    provider = RecordingProvider()
    raw = provider.submit(task, attempt)
    with task_db.transaction(owners.a) as tx:
        result = tx.tasks.store_result(task.id, attempt.id, raw)
        revoke_source(tx, owners.a, source, target)
    with pytest.raises(Gone):
        with task_db.transaction(owners.a) as tx:
            tx.tasks.publish_result(task.id, attempt.id, claim.lease_token, WORKER)
    invalidate_identity(pg, owners.a, 'email_verified_at')
    expire(pg, task.id)
    worker(task_db, scheduler, provider).recover_expired()
    local = PgLocalResultReconciler(task_db)
    candidates = [c for c in local.candidates() if c.id == task.id]
    finished = local.reconcile(candidates[0]) if candidates else False
    row = persisted(pg, 'tasks', id=task.id)[0]
    stored = persisted(pg, 'provider_results', id=result.id)[0]
    evidence('revoked-' + target, {'finished': finished, 'task': row['state'],
        'result': stored['validation_status'], 'provider_calls': provider.calls,
        'original_material_id': tree.material.id, 'current_course_id': source.destination_course.id})
    assert provider.calls == 1
    assert finished is False and stored['validation_status'] == 'pending', 'revoked source was finalized'

def test_large_valid_raw_has_safe_error_and_does_not_block_later_work(task_db, scheduler, owners, pg):
    marker = 'SYNTHETIC_PRIVATE_REVIEW_MARKER'
    raw = '{"marker":"' + marker + '","v":[' + ','.join(['0'] * 99900) + ']}'
    assert len(raw) < 200000 and parity_expected(raw)
    with pg.admin.connect() as conn:
        size = conn.scalar(sa.text('SELECT pg_column_size(CAST(:raw AS jsonb))'), {'raw': raw})
    state = saved_result(task_db, scheduler, owners, pg, raw)
    local, claim = quarantine_saved(state, 'password_hash', task_db, scheduler, owners, pg)
    # Another owner's legal raw follows the first candidate in the global queue.
    from types import SimpleNamespace
    other = SimpleNamespace(a=owners.b, b=owners.a)
    following = saved_result(task_db, scheduler, other, pg)
    quarantine_saved(following, 'password_hash', task_db, scheduler, other, pg)
    failures = []
    for action in (lambda: local.reconcile(claim), lambda: local.run_once()):
        try:
            action()
        except sa.exc.DBAPIError as err:
            failures.append({'sqlstate': err.orig.sqlstate,
                'constraint': err.orig.diag.constraint_name,
                'detail_contains_marker': marker in (err.orig.diag.message_detail or ''),
                'error_contains_marker': marker in str(err)})
    current = persisted(pg, 'tasks', id=state.task.id)[0]
    next_task = persisted(pg, 'tasks', id=following.task.id)[0]
    evidence('large-result', {'raw_characters': len(raw), 'jsonb_bytes': size,
        'python_serialized_bytes': len(json.dumps(json.loads(raw)).encode()),
        'errors': failures, 'current_state': current['state'], 'following_state': next_task['state'],
        'provider_calls': [state.provider.calls, following.provider.calls]})
    assert state.provider.calls == following.provider.calls == 1
    assert not failures, 'valid bounded raw leaked error details or stopped the reconciliation queue'
    assert next_task['state'] == 'succeeded'

@pytest.mark.parametrize('depth', [100, 1100])
def test_deep_json_matches_existing_python_validation(depth, task_db, scheduler, owners, pg):
    raw = '{"v":' + '['*depth + '0' + ']'*depth + '}'
    expected = parity_expected(raw)
    state = saved_result(task_db, scheduler, owners, pg, raw)
    local, claim = quarantine_saved(state, 'password_hash', task_db, scheduler, owners, pg)
    finished = local.reconcile(claim)
    with pg.admin.connect() as conn:
        actual = conn.scalar(sa.text('SELECT validation_status FROM provider_results WHERE id=:id'), {'id': state.result.id})
    readable = True
    try:
        persisted(pg, 'provider_results', id=state.result.id)
    except RecursionError:
        readable = False
    evidence('depth-' + str(depth), {'expected_valid': expected, 'actual': actual,
        'finished': finished, 'provider_calls': state.provider.calls, 'python_result_readable': readable})
    assert state.provider.calls == 1 and finished is True
    assert actual == ('valid' if expected else 'invalid')

# Third-round evidence adapter; original probe bytes above are preserved.
OUT = OUT.parent / 'pr03a/third-round/direct-probes' / __import__('uuid').uuid4().hex
OUT.mkdir(parents=True, exist_ok=False)
