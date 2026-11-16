"""PR-04b pure values: policy bounds, deterministic intents and DTO projection."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from classroom_web.domain.deletion import (DeletionIntent, DeletionPolicy, canonical, deletion_dto)


def test_policy_has_no_retention_default_and_rejects_unbounded_values():
    policy = DeletionPolicy()
    # Product decisions stay unset; only technical bounds have defaults.
    assert policy.physical_deadline is None
    assert policy.write_settle >= timedelta(seconds=60)
    for invalid in (dict(write_settle=timedelta(hours=2)), dict(lease_seconds=0), dict(lease_seconds=True),
                    dict(retry_backoff=timedelta(hours=2)), dict(confirmation_lifetime=timedelta(0)),
                    dict(orphan_grace=timedelta(days=31)), dict(physical_deadline=timedelta(0))):
        with pytest.raises(ValueError):
            DeletionPolicy(**invalid)


def test_backoff_grows_and_is_capped():
    policy = DeletionPolicy(retry_backoff=timedelta(seconds=10), max_backoff=timedelta(seconds=45))
    assert [policy.backoff(n) for n in (1, 2, 3, 4, 50)] == [10, 20, 40, 45, 45]


def test_one_intent_per_owner_and_scope_and_no_content_in_the_record():
    owner, scope = uuid4(), uuid4()
    first = DeletionIntent.for_scope(owner, 'class_session', scope)
    assert first == DeletionIntent.for_scope(owner, 'class_session', scope)
    assert first.intent_id != DeletionIntent.for_scope(owner, 'audio', scope).intent_id
    assert first.intent_id != DeletionIntent.for_scope(uuid4(), 'class_session', scope).intent_id
    assert first.intent_id.version == 4 and first.request_id.version == 4
    assert set(first.record()) == {'intent_id', 'request_id', 'owner_id', 'scope_type', 'scope_id', 'origin'}
    assert DeletionIntent.from_record(first.record()) == first


def test_forged_or_malformed_intents_are_rejected():
    owner, scope = uuid4(), uuid4()
    good = DeletionIntent.for_scope(owner, 'course', scope).record()
    for change in (dict(request_id=str(uuid4())), dict(intent_id=str(uuid4())), dict(scope_type='course_material_version'),
                   dict(origin='admin'), dict(owner_id=str(uuid4()))):
        with pytest.raises(ValueError):
            DeletionIntent.from_record({**good, **change})
    with pytest.raises(ValueError):
        DeletionIntent.from_record({**good, 'title': 'private text'})
    with pytest.raises(ValueError):
        DeletionIntent.for_scope(owner, 'account', scope)
    assert DeletionIntent.for_scope(owner, 'account', owner).scope_id == owner


def test_canonical_encoding_is_stable():
    assert canonical(dict(b=1, a='x')) == b'{"a":"x","b":1}'
    with pytest.raises(ValueError):
        canonical(dict(a=float('nan')))


def test_database_states_project_to_the_frozen_dto():
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    row = dict(id=uuid4(), revision=3, created_at=now, updated_at=now, scope_type='audio', scope_id=uuid4(),
               state='pending', access_revoked_at=now, completed_at=None, last_error_code=None)
    dto = deletion_dto(row)
    assert dto['state'] == 'pending_delete' and dto['physical_completed_at'] is None
    assert [deletion_dto({**row, 'state': state})['state'] for state in ('access_revoked', 'failed', 'complete')] \
        == ['access_revoked', 'delete_failed', 'deleted']
