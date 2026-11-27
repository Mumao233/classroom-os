"""PR-11c migration checks require PostgreSQL only."""
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from classroom_web.domain.data import OwnerContext
from classroom_web.infrastructure.database import Database
from classroom_web.infrastructure.operations_repository import PgOperationsRepository
from classroom_web.infrastructure.sync_repository import PgSyncRepository
from test_task_migrations import previous_schema, upgrade, version


def test_migrations_from_empty_and_previous_head(pg, previous_schema):
    assert version(SimpleNamespace(engine=pg.admin)) == '0019_search_operations'
    upgrade(previous_schema, '0018_filetrans')
    assert version(previous_schema) == '0018_filetrans'
    owner = OwnerContext(previous_schema.owner_id)
    database = Database(previous_schema.runtime_url.render_as_string(hide_password=False))
    try:
        def edit(name):
            return dict(op_id=str(uuid4()), object_type='course', object_id=str(previous_schema.course_id),
                        draft_id=str(uuid4()), base_revision=1, payload=dict(base_revision=1, name=name))

        with database.transaction(owner) as tx:
            sync = PgSyncRepository(tx)
            assert sync.apply(edit('Current course text'))['status'] == 'applied'
            pending = sync.apply(edit('Unresolved course edit'))
            resolved = sync.apply(edit('Already resolved course edit'))
            assert pending['status'] == resolved['status'] == 'conflict'
            assert sync.resolve(UUID(resolved['conflict_id']), dict(base_revision=2, resolution='use_server'),
                                uuid4())['status'] == 'applied'
        upgrade(previous_schema, 'head')
        assert version(previous_schema) == '0019_search_operations'
        upgrade(previous_schema, 'head')
        with database.transaction(owner) as tx:
            operations = PgOperationsRepository(tx)
            notice, = operations.notifications({})['items']
            assert notice['kind'] == 'conflict' and notice['object_id'] == pending['conflict_id']
            assert notice['resolved_at'] is None
            sync = PgSyncRepository(tx)
            conflict = sync.conflict(UUID(notice['object_id']))
            assert conflict['id'] == pending['conflict_id'] and not conflict['resolved']
            assert sync.resolve(UUID(notice['object_id']), dict(base_revision=2, resolution='use_server'),
                                uuid4())['status'] == 'applied'
            resolved_notice, = operations.notifications({})['items']
            assert resolved_notice['id'] == notice['id'] and resolved_notice['resolved_at'] is not None
    finally:
        database.dispose()
    with previous_schema.engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT extname FROM pg_extension WHERE extname='pg_trgm'")) == 'pg_trgm'
        indexes = dict(conn.execute(sa.text("""SELECT indexname, indexdef FROM pg_indexes
            WHERE schemaname='public' AND indexname LIKE 'pr11c\\_%'""")).all())
    assert set(indexes) == {'pr11c_course_name_trgm', 'pr11c_class_title_trgm', 'pr11c_feedback_rate',
                            'pr11c_notification_page'}
    for name, table, column in (('pr11c_course_name_trgm', 'courses', 'name'),
                                ('pr11c_class_title_trgm', 'class_sessions', 'title')):
        definition = indexes[name]
        assert f'ON public.{table} USING gin' in definition and f'lower({column}' in definition
        assert 'gin_trgm_ops' in definition and 'WHERE (deleted_at IS NULL)' in definition
    rate = indexes['pr11c_feedback_rate']
    assert 'ON public.operation_receipts' in rate and '(owner_id, created_at)' in rate
    assert "operation = 'submitFeedback'" in rate


@pytest.mark.parametrize('reader,indirect', [
    ('classroom_web_feedback_admin', False),
    ('classroom_web_feedback_excerpt', True),
])
def test_runtime_rejects_overlapping_feedback_reader_roles(pg, owners, reader, indirect):
    login = 'pr02a_pr11c_overlap_' + uuid4().hex
    bridge = 'pr02a_pr11c_bridge_' + uuid4().hex
    with pg.admin.begin() as conn:
        conn.exec_driver_sql(f'CREATE ROLE {login} LOGIN NOINHERIT')
        conn.exec_driver_sql(f'GRANT classroom_web_runtime TO {login}')
        if indirect:
            conn.exec_driver_sql(f'CREATE ROLE {bridge} NOLOGIN NOINHERIT')
            conn.exec_driver_sql(f'GRANT {reader} TO {bridge}')
            conn.exec_driver_sql(f'GRANT {bridge} TO {login}')
        else:
            conn.exec_driver_sql(f'GRANT {reader} TO {login}')
    database = Database(pg.runtime_url.set(username=login).render_as_string(hide_password=False))
    try:
        with pytest.raises(PermissionError, match='feedback reader'), database.transaction(owners.a):
            pytest.fail('overlapping runtime login reached an owner transaction')
    finally:
        database.dispose()
        with pg.admin.begin() as conn:
            conn.exec_driver_sql(f'DROP ROLE {login}')
            if indirect:
                conn.exec_driver_sql(f'DROP ROLE {bridge}')
