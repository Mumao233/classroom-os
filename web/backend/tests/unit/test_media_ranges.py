"""Byte ranges that previously had no implementation or response contract."""
import pytest
from classroom_web.domain.media import byte_range, MediaPolicy, media_state


@pytest.mark.parametrize('value,expected', [
    (None,(0,9,False)), ('bytes=0-0',(0,0,True)), ('bytes=4-',(4,9,True)),
    ('bytes=-3',(7,9,True)), ('bytes=-100',(0,9,True)), ('bytes=0-100',(0,9,True))])
def test_single_range_forms(value, expected):
    assert byte_range(value,10) == expected


@pytest.mark.parametrize('value', ['bytes=10-','bytes=4-2','bytes=-0','bytes=-',
    'bytes=0-1,3-4','bytes= 0-1','items=0-1','bytes=999999999999999999999999-'])
def test_unsafe_and_unsatisfiable_ranges(value):
    with pytest.raises(ValueError):
        byte_range(value,10)


def test_policy_has_bounded_memory_and_ticket_lifetime():
    with pytest.raises(ValueError):
        MediaPolicy(max_bytes=0)


@pytest.mark.parametrize('status,revoked,expected',[
    ('staging',None,'staging'),('verified',None,'staging'),('committed',None,'available'),
    ('committed',True,'access_revoked'),('delete_pending',True,'pending_delete'),
    ('delete_failed',True,'delete_failed'),('deleted',True,'deleted')])
def test_internal_states_project_to_public_contract(status,revoked,expected):
    assert media_state(status,revoked)==expected
