"""Boundary checks for the policy used by the PostgreSQL admission tests."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from classroom_web.domain.billing import (BillingBlocked, Price, billing_state,
    platform_limits, quota_limits, period)


@pytest.mark.parametrize(('risk','state'),[(299,'normal'),(300,'warning60'),(399,'warning60'),
    (400,'warning80'),(499,'warning80'),(500,'blocked100'),(501,'blocked100')])
def test_threshold_boundaries(risk,state):
    assert billing_state(risk,500)==state


def test_beijing_calendar_boundaries():
    before = datetime(2026,12,31,15,59,59,tzinfo=timezone.utc)
    after = datetime(2026,12,31,16,tzinfo=timezone.utc)
    assert period(before)[1]==after
    assert period(after)[0]==after
    assert period(after,'day')[0]==after
    assert period(after)[1]==datetime(2027,1,31,16,tzinfo=timezone.utc)


def test_product_limits():
    assert quota_limits(10)['retranscribe_month_ms']==8_640_000
    assert quota_limits(30)['retranscribe_month_ms']==12_960_000
    assert platform_limits(10)['total']==500_000_000
    assert platform_limits(30)['safety']==400_000_000


def test_price_rounding_and_missing_bounds():
    price = Price(uuid4(),'mock','synthetic','synthetic','v1',
                  {'input_tokens': {'microyuan':6,'units':1000},
                   'output_tokens': {'microyuan':45,'units':10000}},3,True,False)
    assert price.amount({'input_tokens':1,'output_tokens':1})==3
    assert price.amount({'input_tokens':1000,'output_tokens':1000})==11
    with pytest.raises(BillingBlocked):
        price.amount({'input_tokens':1})
    with pytest.raises(ValueError):
        price.amount({'input_tokens':True,'output_tokens':1})


def test_bigint_pricing_uses_sufficient_decimal_precision():
    largest = 2**63-1
    price = Price(uuid4(),'mock','synthetic','synthetic','v1',
                  {'input_tokens':{'microyuan':largest,'units':largest}},0,True,False)
    assert price.amount({'input_tokens':largest})==largest
