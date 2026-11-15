"""Fixed experiment services; every transaction retains native/role checks."""
from contextlib import ExitStack
import threading,queue,time
import common as b,schema
import admission
import request_diagnostics as rd
class FixedPool:
    @staticmethod
    def pool_size(service):
        b.require(service in ('auth','session','runtime'),'fixed_pool_service')
        # Restore the original finite AUTH2/SESSION8/RUNTIME8 counts now that
        # complete native observations have been made cheaper. Tightening the
        # read channels caused a queue ahead of nested session/runtime work.
        # These are PG connections,
        # not hash slots: the global two/eight/one-second admission is unchanged.
        # Every real backend remains in each complete native Job observation.
        return 2 if service=='auth' else 8
    def __init__(self,lease,services=('auth','session')):
        b.require(services and set(services)<= {'auth','session','runtime'},'fixed_pool_services')
        self.lease=lease;self.pool_stack=ExitStack();self.pool={};self.pool_locks={k:threading.RLock() for k in services};self.closing=False
        self.channels={};self.stacks={}
        for service in services:
            available=queue.Queue()
            for index in range(self.pool_size(service)):
                available.put(index);self.stacks[service,index]=ExitStack()
            self.channels[service]=available
    def database(self,service,statement,args=(),read_only=False):
        return self.transaction(service,lambda c:c.execute(statement,args).fetchone()[0],read_only=read_only)
    def transaction(self,service,body,read_only=False):
        if not hasattr(self,'channels'):return self._database(service,body,read_only)
        b.require(service in self.channels and not self.closing,'fixed_pool_service_or_closed')
        wait=admission.live()
        try:
            with rd.stage('pool.wait.'+service):index=self.channels[service].get(timeout=5 if wait is None else wait)
        except queue.Empty:raise admission.Expired('pool_wait_deadline') from None
        try:return self._database(service,body,read_only,index)
        finally:self.channels[service].put(index)
    def _database(self,service,body,read_only,index=None):
        from runner import role_identity_ok
        import native_process as native
        b.require(service in self.pool_locks and not self.closing,'fixed_pool_service_or_closed')
        key=service if index is None else (service,index)
        lock=self.pool_locks[service] if index is None else threading.RLock()
        with admission.locked(lock):
            admission.live()
            if key not in self.pool:
                stack=self.pool_stack if index is None else self.stacks[key]
                with rd.stage('pool.open.'+service):self.pool[key]=stack.enter_context(self.lease.connect(service))
            c=self.pool[key]
            try:
                with rd.stage('transaction.begin.'+service):c.execute('BEGIN READ ONLY' if read_only else 'BEGIN READ WRITE')
                remain=admission.live()
                if remain is not None:
                    ms=max(1,int(remain*1000))
                    with rd.stage('transaction.timeouts.'+service):c.execute("SELECT set_config('statement_timeout',%s,true),set_config('lock_timeout',%s,true)",(str(min(3000,ms)),str(min(1000,ms))))
                pid=c.c.info.backend_pid;h,creation=self.lease._backend_handles[pid]
                with rd.stage('native.identity.'+service):
                    actual=native._handle_identity(self.lease.job.api,h,pid)
                    b.require(actual.creation_token==creation and actual.executable==self.lease.plan.postgres_executable and self.lease.job.contains(pid),'pooled_backend_identity')
                    self.lease.job.token.process(pid)
                with rd.stage('role.identity.'+service):
                    row=c.execute("SELECT current_user::text,session_user::text,current_database(),host(inet_server_addr()),inet_server_port(),current_setting('server_version_num')::integer,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication,rolinherit FROM pg_catalog.pg_roles WHERE rolname=current_user").fetchone()
                    b.require(role_identity_ok(row,self.lease.plan,schema.logins(self.lease.plan.run_id)[service]),'pooled_service_role_identity')
                admission.live()
                with rd.stage('sql.body.'+service):result=body(c)
                admission.live()
                with rd.stage('transaction.commit.'+service):c.execute('COMMIT')
                return result
            except BaseException as original:
                # Record before rollback: cleanup may itself fail and become
                # the exception seen by the HTTP handler. Never lose origin.
                rd.failure(original,'transaction.failure.before_rollback.'+service)
                # Expiry never prevents rollback, nor authorizes new work.
                # Its separate bounded cleanup context can execute only this
                # already-owned connection's ROLLBACK / close path.
                token=admission.CURRENT.set(time.monotonic()+5)
                try:
                    with rd.stage('transaction.rollback.'+service,secondary=True):c.execute('ROLLBACK')
                except BaseException:self.closing=True;raise
                finally:admission.CURRENT.reset(token)
                raise
    def close_pool(self):
        self.closing=True
        if hasattr(self,'channels'):
            # Drain all channels before closing any connection. One cleanup
            # budget covers the whole wait, independent of request expiry.
            held=[];deadline=time.monotonic()+5
            try:
                with rd.stage('pool.drain'):
                    for service in sorted(self.channels):
                        for _ in range(self.pool_size(service)):
                            wait=deadline-time.monotonic()
                            if wait<=0:raise admission.Expired('pool_close_deadline')
                            try:index=self.channels[service].get(timeout=wait)
                            except queue.Empty:raise admission.Expired('pool_close_deadline') from None
                            held.append((service,index))
                # A failed close must not strand the other owned connections.
                # Preserve the first error and mark later failures secondary.
                first_error=None
                for (service,index),stack in self.stacks.items():
                    try:stack.close()
                    except BaseException as error:
                        rd.failure(error,'pool.close.'+service,secondary=first_error is not None)
                        if first_error is None:first_error=error
                if first_error is not None:raise first_error
            finally:
                for service,index in held:self.channels[service].put(index)
            return
        with ExitStack() as locks:
            for name in sorted(self.pool_locks):locks.enter_context(self.pool_locks[name])
            self.pool_stack.close()
    def __enter__(self):return self
    def __exit__(self,*exc):self.close_pool()
