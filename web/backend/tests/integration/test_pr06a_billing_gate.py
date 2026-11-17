"""PR-06a checks the existing default-off billing boundary on real PostgreSQL."""
from uuid import uuid4

import sqlalchemy as sa

from classroom_web.domain.billing import SWITCHES
from classroom_web.domain.data import OwnerContext
from classroom_web.infrastructure.billing_repository import PgBillingRepository
from classroom_web.infrastructure.schema import tables
from test_billing import billing_pg


def test_asr_billing_defaults_remain_closed(billing_pg):
    owner = OwnerContext(uuid4())
    with billing_pg.admin.begin() as connection:
        flags = connection.scalar(sa.text('SELECT flags FROM billing_control.switches'))
        assert set(flags) == SWITCHES
        assert all(value is False for value in flags.values())
        assert flags['billing'] is False
        assert flags['asr_streaming'] is False
        connection.execute(tables['users'].insert(), {
            'id': owner.owner_id, 'role': 'student', 'state': 'active',
        })
    with billing_pg.db.transaction(owner) as transaction:
        assert PgBillingRepository(transaction).synthetic_only is True
