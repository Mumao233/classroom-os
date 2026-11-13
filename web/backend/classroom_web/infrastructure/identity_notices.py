"""Identity-only delivery after claim commit; no task/Provider/Outbox coupling."""
import json,os
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from ..domain.auth import AuthRejected
from .auth_crypto import digest,random_token


class PgIdentityNoticeRepository:
    def __init__(self,url):
        u=make_url(url)
        if u.drivername!='postgresql+psycopg' or u.query or not all((u.host,u.port,u.database,u.username,u.password)) or any(k.upper().startswith('PG') for k in os.environ):
            raise ValueError('explicit independent notice-only credential required')
        self.engine=sa.create_engine(u,pool_size=1,max_overflow=0,pool_timeout=1,hide_parameters=True,connect_args={'connect_timeout':1,'options':'-c statement_timeout=2000 -c lock_timeout=1000'})

    def call(self,name,p):
        if name not in ('claim_identity_notices','finish_identity_notice'):raise ValueError('fixed identity-notice functions only')
        try:
            with self.engine.begin() as connection:
                safe=connection.scalar(sa.text("SELECT NOT(rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolinherit) AND NOT EXISTS(SELECT FROM pg_auth_members WHERE member=r.oid) FROM pg_roles r WHERE rolname=current_user"))
                if safe is not True:raise AuthRejected('SERVICE_DISABLED',503)
                value=connection.scalar(sa.text(f'SELECT identity_api.{name}(CAST(:p AS jsonb))'),dict(p=json.dumps(p,separators=(',',':'))))
            return value
        except (sa.exc.DBAPIError,sa.exc.TimeoutError):raise AuthRejected('STORAGE_UNAVAILABLE',503) from None

    def dispose(self):self.engine.dispose()


class IdentityNoticeDispatcher:
    def __init__(self,repository,deliver):self.repository=repository;self.deliver=deliver

    def dispatch_once(self):
        claim=digest('identity_notice',random_token())
        batch=self.repository.call('claim_identity_notices',dict(limit=32,claim_hash=claim))
        if batch is None:raise AuthRejected('STORAGE_UNAVAILABLE',503)
        result=dict(claimed=len(batch['items']),delivered=0,failed=0)
        for event in batch['items']:
            # Claim transaction already ended. Delivery may re-read current
            # authorization in a separate transaction; no event lock survives.
            try:
                if self.deliver(event) is not True:result['failed']+=1;continue
                if self.repository.call('finish_identity_notice',dict(id=event['id'],scope=event['scope'],claim_hash=claim)):result['delivered']+=1
                else:result['failed']+=1
            except Exception:result['failed']+=1
        return result
