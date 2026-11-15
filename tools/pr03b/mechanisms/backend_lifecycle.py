"""Retained native backend HANDLE; Job membership is not an exit predicate."""
import ctypes as c,time
from ctypes import wintypes as w
import common as b
def pin(lease,pid):
    import native_process as native
    handle=lease.job.api.k.OpenProcess(0x101000,False,pid)
    b.require(handle,'backend_native_exit_handle')
    try:
        identity=native._handle_identity(lease.job.api,handle,pid)
        b.require(identity.executable==lease.plan.postgres_executable and lease.job.contains(pid),'backend_handle_owned_PG')
        return handle,identity.creation_token
    except BaseException:lease.job.api.k.CloseHandle(handle);raise
def _observe_close(k,handle,observe,clock,sleep):
    started=clock();deadline=started+2;samples=[]
    while True:
        # A signalled process HANDLE and the Job/TCP views need not become
        # observable in one snapshot. All must agree within ONE original 2s
        # total budget; do not add a second wait after the process wait.
        wait=k.WaitForSingleObject(handle,max(0,int((deadline-clock())*1000)))
        code=w.DWORD();observed=k.GetExitCodeProcess(handle,c.byref(code))
        member,active,row_count=observe();elapsed=clock()-started
        sample={'wait_result':wait,'exit_code':code.value if observed else None,
            'IsProcessInJob_after_wait':member,'in_active_job_list':active,
            'remaining_TCP_rows':row_count,'elapsed_seconds':round(elapsed,6)}
        samples.append(sample)
        closed=wait==0 and observed and code.value==0 and not active and row_count==0
        if closed and elapsed<=2:return True,samples
        if elapsed>=2 or wait not in (0,258) or not observed or code.value not in (0,259):return False,samples
        sleep(min(.01,max(0,deadline-clock())))

def finish(lease,pid,pinned):
    import native_process as native
    handle,creation=pinned;k=lease.job.api.k
    def observe():return lease.job.contains(pid),pid in lease.job.process_ids(),len(native.tcp_rows({pid}))
    try:
        closed,samples=_observe_close(k,handle,observe,time.monotonic,time.sleep)
        row={'phase':'native_backend_exit','backend_pid':pid,'creation_token':creation,
            **samples[-1],'closure_observations':samples,'total_budget_seconds':2,
            'handle_pinned_before_libpq_close':True}
        lease.events.append(row)
        b.require(closed,'backend_native_exit_not_confirmed')
    finally:k.CloseHandle(handle)
