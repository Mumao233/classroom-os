"""Two isolated service pools; fixed functions, bounded SQL, no owner fallback."""
from contextlib import contextmanager
import json,re,os
from collections import deque
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from ..domain.auth import AuthRejected
from .auth_deadlines import remaining

VERIFIER={'locate_auth','verify_auth','verify_factor'}
SESSION={'validate_session','establish_session','protocol_context','revoke_session','issue_reauth',
         'apply_account_change','manage_email_action','list_security_context','redeem_auto_recovery',
         'manage_ws_ticket','authentication_budget','issue_control_reauth','verify_control_bridge','queue_auth_mail'}


class PgAuthRepository:
    def __init__(self,*,verifier_url,session_url):
        urls=[make_url(v) for v in (verifier_url,session_url)]
        if (any(u.drivername!='postgresql+psycopg' or u.query or not all((u.host,u.port,u.database,u.username,u.password)) for u in urls)
                or urls[0].username==urls[1].username or any(k.upper().startswith('PG') for k in os.environ)):
            raise ValueError('separate explicit verifier and session logins required')
        self.engines={key:sa.create_engine(url,pool_size=2,max_overflow=0,pool_timeout=1,
                                          pool_pre_ping=True,hide_parameters=True,connect_args={'connect_timeout':1,'options':'-c statement_timeout=1000 -c lock_timeout=500'})
                      for key,url in zip(('verifier','session'),urls)}
        self.faults=deque(maxlen=32)

    @contextmanager
    def transaction(self,*,verifier=False):
        phase='acquiring'
        try:
            with self.engines['verifier' if verifier else 'session'].begin() as connection:
                safe=connection.scalar(sa.text("""
                  SELECT NOT(rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolinherit)
                    AND NOT EXISTS(SELECT FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user))
                    AND NOT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                      WHERE n.nspname IN('public','identity_control') AND c.relkind IN('r','p') AND pg_has_role(current_user,c.relowner,'MEMBER'))
                    AND NOT pg_has_role(current_user,'classroom_web_runtime','MEMBER')
                  FROM pg_roles WHERE rolname=current_user
                """))
                if safe is not True:raise PermissionError('authentication requires a restricted separate NOINHERIT login')
                left=remaining();ms=1000 if left is None else max(1,min(1000,int(left*1000)))
                connection.execute(sa.text("SELECT set_config('statement_timeout',:statement,true),set_config('lock_timeout',:lock,true)"),{'statement':str(ms),'lock':str(min(500,ms))})
                connection.execute(sa.text("SET LOCAL timezone='UTC'"))
                phase='executing'
                yield connection
                remaining();phase='committing'
        except sa.exc.DBAPIError as error:
            state=getattr(error.orig,'sqlstate',None)
            context=getattr(getattr(error.orig,'diag',None),'context','') or ''
            locations=re.findall(r'function ([a-zA-Z0-9_.]+)\([^\n]*?\) line ([0-9]+)',context)
            self.faults.append({'kind':type(error.orig).__name__,'sqlstate':state,'locations':locations})
            if phase=='committing' and (error.connection_invalidated or state is None or state.startswith('08')):
                raise AuthRejected('RESULT_UNKNOWN',503) from None
            if state=='42501':raise AuthRejected('UNAUTHENTICATED',401) from None
            if state=='23505':raise AuthRejected('IDEMPOTENCY_MISMATCH',409) from None
            raise AuthRejected('STORAGE_UNAVAILABLE',503) from None
        except sa.exc.TimeoutError:raise AuthRejected('RATE_LIMITED',429) from None

    def call(self,operation,payload,*,verifier=False,before_commit=None):
        if operation not in (VERIFIER if verifier else SESSION):raise ValueError('unsupported fixed authentication operation')
        remaining()
        with self.transaction(verifier=verifier) as connection:
            result=connection.scalar(sa.text(f'SELECT identity_api.{operation}(CAST(:payload AS jsonb))'),
                                     {'payload':json.dumps(payload,separators=(',',':'),ensure_ascii=True)})
            if before_commit is not None:before_commit()
        # Returned rejections are interpreted after commit, so an independent
        # rate-limit increment is not silently rolled back by an HTTP error.
        if isinstance(result,dict) and 'error' in result:
            code=result['error'];status={'MFA_REQUIRED':403,'FORBIDDEN':403,'REAUTH_REQUIRED':403,'SERVICE_DISABLED':503,
                                         'IDEMPOTENCY_MISMATCH':409,'SECRET_ALREADY_ISSUED':409,'RATE_LIMITED':429}.get(code,401)
            raise AuthRejected(code,status)
        return result

    def dispose(self):
        for engine in self.engines.values():engine.dispose()
