"""Fixed control-plane calls with four distinct non-owner credentials."""
from contextlib import contextmanager
from collections import deque
import json,os
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from ..domain.auth import AuthRejected
from .auth_deadlines import remaining

CONTROL_FUNCTIONS={
    'recovery':{'control_executor','open_manual_case','manual_case_plan','prepare_manual_proof','issue_manual','complete_manual'},
    'receiver':{'register_receiver','receiver_peek','confirm_manual_receiver'},
    'status':{'read_manual_status'},
    'maintenance':{'bind_control_broker','apply_registration_change','publish_registration_ready','registration_change_status','adopt_registration_transition'},
}


class PgRecoveryRepository:
    def __init__(self,*,recovery_url,receiver_url,status_url,maintenance_url):
        urls={k:make_url(v) for k,v in zip(CONTROL_FUNCTIONS,(recovery_url,receiver_url,status_url,maintenance_url))}
        if (any(k.upper().startswith('PG') for k in os.environ)
                or any(u.drivername!='postgresql+psycopg' or u.query or not all((u.host,u.port,u.database,u.username,u.password)) for u in urls.values())
                or len({u.username for u in urls.values()})!=4 or len({(u.host,u.port,u.database) for u in urls.values()})!=1):
            raise ValueError('four separate explicit restricted logins in one control database required')
        self.engines={k:sa.create_engine(u,pool_size=1,max_overflow=0,pool_timeout=1,pool_pre_ping=True,hide_parameters=True,
                        connect_args={'connect_timeout':1,'options':'-c timezone=UTC -c statement_timeout=3000 -c lock_timeout=1000'}) for k,u in urls.items()}
        self.faults=deque(maxlen=32)
        self.service_logins=tuple(u.username for u in urls.values())

    def call(self,operation,payload):
        matches=[k for k,names in CONTROL_FUNCTIONS.items() if operation in names]
        if len(matches)!=1:raise ValueError('unsupported control-plane operation')
        phase='acquiring'
        try:
            with self.engines[matches[0]].begin() as connection:
                safe=connection.scalar(sa.text("""SELECT NOT(rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolinherit)
                  AND NOT EXISTS(SELECT FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user))
                  AND NOT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN('public','identity_control')
                    AND c.relkind IN('r','p') AND pg_has_role(current_user,c.relowner,'MEMBER'))
                  AND NOT pg_has_role(current_user,'classroom_web_runtime','MEMBER') FROM pg_roles WHERE rolname=current_user"""))
                if safe is not True:raise PermissionError('control plane requires restricted independent credentials')
                left=remaining();ms=3000 if left is None else max(1,min(3000,int(left*1000)))
                connection.execute(sa.text("SELECT set_config('statement_timeout',:statement,true),set_config('lock_timeout',:lock,true)"),{'statement':str(ms),'lock':str(min(1000,ms))})
                phase='executing'
                result=connection.scalar(sa.text(f'SELECT identity_api.{operation}(CAST(:p AS jsonb))'),{'p':json.dumps(payload,separators=(',',':'),ensure_ascii=True)})
                remaining()
                phase='committing'
            return result
        except sa.exc.DBAPIError as error:
            state=getattr(error.orig,'sqlstate',None);self.faults.append({'operation':operation,'kind':type(error.orig).__name__,'sqlstate':state,'phase':phase})
            if phase=='committing' and (error.connection_invalidated or state is None or state.startswith('08')):raise AuthRejected('RESULT_UNKNOWN',503) from None
            if state in ('42501','23503','23505','23514'):raise AuthRejected('FORBIDDEN',403) from None
            raise AuthRejected('STORAGE_UNAVAILABLE',503) from None
        except sa.exc.TimeoutError:raise AuthRejected('RATE_LIMITED',429) from None

    def dispose(self):
        for engine in self.engines.values():engine.dispose()
