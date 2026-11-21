"""Narrow queue identity. It has no direct access to task inputs or user bodies."""
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from ..domain.tasks import Claim


class PgTaskScheduler:
    def __init__(self, url):
        if make_url(url).drivername != 'postgresql+psycopg':
            raise ValueError('explicit PostgreSQL URL required')
        self.engine = create_engine(url, pool_size=4, max_overflow=0, pool_pre_ping=True,
                                    hide_parameters=True)

    def _check_identity(self, connection):
        safe = connection.scalar(text("""
            SELECT NOT r.rolsuper AND NOT r.rolbypassrls
              AND pg_has_role(current_user,'classroom_web_scheduler','USAGE')
              AND NOT pg_has_role(current_user,'classroom_web_runtime','USAGE')
              AND NOT pg_has_role(current_user,'classroom_web_task_dispatcher','USAGE')
              AND NOT has_table_privilege(current_user,'public.tasks','SELECT')
            FROM pg_roles r WHERE r.rolname=current_user
        """))
        if safe is not True:
            raise PermissionError('a separate restricted scheduler identity is required')

    def claim(self, worker, lease_seconds=30, batch_size=1, *, schema_version='1'):
        if not isinstance(worker, str) or not 1 <= len(worker) <= 100:
            raise ValueError('worker identifier must be 1..100 characters')
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError('lease seconds must be 1..3600')
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError('batch size must be 1..100')
        if schema_version not in ('1', 'pr07a-1', 'pr07b-1', 'pr07b-batch-1', 'pr08c-1', 'pr06b-1'):
            raise ValueError('unsupported task schema version')
        with self.engine.begin() as connection:
            self._check_identity(connection)
            if schema_version == '1':
                query = 'SELECT * FROM classroom_web_claim_tasks(:worker,:seconds,:batch)'
            else:
                query = 'SELECT * FROM classroom_web_claim_tasks(:worker,:seconds,:batch,:schema_version)'
            rows = connection.execute(text(query), {'worker': worker, 'seconds': lease_seconds,
                'batch': batch_size, 'schema_version': schema_version}).mappings().all()
            return [Claim(**row) for row in rows]

    def expired(self, batch_size=100):
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError('batch size must be 1..100')
        with self.engine.begin() as connection:
            self._check_identity(connection)
            return [Claim(**row) for row in connection.execute(text(
                'SELECT * FROM classroom_web_expired_tasks(:batch)'), {'batch': batch_size}).mappings()]

    def recover_revoked(self, task_id, lease_token):
        with self.engine.begin() as connection:
            self._check_identity(connection)
            return connection.scalar(text('SELECT classroom_web_recover_revoked(:task,:token)'),
                                     {'task': task_id, 'token': lease_token})

    def question_results(self, batch_size=100):
        """Routing metadata for bounded local-only question reconciliation."""
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError('question recovery batch must be 1..100')
        with self.engine.begin() as connection:
            self._check_identity(connection)
            rows = connection.execute(text(
                'SELECT * FROM classroom_web_qa_saved_candidates(:batch)'),
                {'batch': batch_size}).mappings()
            return [Claim(**row) for row in rows]

    def generation_results(self, batch_size=100, *, schema_version='pr07a-1'):
        if schema_version == 'pr07a-1':
            return self.question_results(batch_size)
        if schema_version not in ('pr07b-1', 'pr07b-batch-1') or type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError('invalid generation recovery bounds')
        with self.engine.begin() as connection:
            self._check_identity(connection)
            function = ('classroom_web_review_saved_candidates' if schema_version == 'pr07b-1'
                        else 'classroom_web_review_batch_saved_candidates')
            rows = connection.execute(text(f'SELECT * FROM {function}(:batch)'),
                {'batch': batch_size}).mappings()
            return [Claim(**row) for row in rows]

    def review_pending(self):
        with self.engine.begin() as connection:
            self._check_identity(connection)
            return [Claim(**row) for row in connection.execute(text(
                'SELECT * FROM classroom_web_review_pending(32)')).mappings()]

    def dispose(self):
        self.engine.dispose()
