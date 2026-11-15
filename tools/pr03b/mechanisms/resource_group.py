"""Guest-only runtime Job quotas. No host, registry or VM hardware changes."""
import ctypes as c,os,struct
from ctypes import wintypes as w
import common as b
CURRENT=None
class CPU(c.Structure):_fields_=[('ControlFlags',w.DWORD),('CpuRate',w.DWORD)]
class Budget:
    def __init__(self):
        import native_process as native
        b.require(os.name=='nt' and os.cpu_count()==4 and b.HOME.exists(),'fixed_four_CPU_guest_budget')
        self.clients=[];self.peak_observed_rss=0;self.api=native._Api();self.h=self.api.k.CreateJobObjectW(None,None)
        b.require(bool(self.h),'SUT_resource_job_create')
        limits=native._ExtendedJobLimits();limits.BasicLimitInformation.LimitFlags=0x200|0x10|0x800
        limits.BasicLimitInformation.Affinity=3;limits.JobMemoryLimit=4*1024**3
        # Root Job rate is system-wide: 50% of four logical CPUs, with the
        # same two-CPU affinity also enforced. The load client breaks out of
        # this resource Job before receiving its own root 25%/one-CPU quota.
        # https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information
        self.set(9,limits);self.set(15,CPU(1|4,5000))
        b.require(self.api.k.AssignProcessToJobObject(self.h,self.api.k.GetCurrentProcess()),'SUT_resource_job_assign')
        self.verify()
    def set(self,kind,value):b.require(self.api.k.SetInformationJobObject(self.h,kind,c.byref(value),c.sizeof(value)),'resource_quota_set')
    def contains(self,pid,*,missing_ok=False):
        c.set_last_error(0);p=self.api.k.OpenProcess(0x1000,False,pid)
        if not p and missing_ok and c.get_last_error()==87:return False
        b.require(bool(p),'resource_process_query')
        try:
            flag=w.BOOL();b.require(self.api.k.IsProcessInJob(p,self.h,c.byref(flag)),'resource_membership_query');return bool(flag.value)
        finally:self.api.k.CloseHandle(p)
    def verify(self):
        import native_process as native
        limits=native._ExtendedJobLimits();cpu=CPU()
        b.require(self.api.k.QueryInformationJobObject(self.h,9,c.byref(limits),c.sizeof(limits),None),'resource_memory_readback')
        b.require(self.api.k.QueryInformationJobObject(self.h,15,c.byref(cpu),c.sizeof(cpu),None),'resource_CPU_readback')
        b.require(limits.JobMemoryLimit==4*1024**3 and limits.BasicLimitInformation.Affinity==3 and limits.BasicLimitInformation.LimitFlags==0xa10 and cpu.ControlFlags==5 and cpu.CpuRate==5000,'resource_budget_changed')
        return {'SUT_CPU_cap_equivalent':2,'SUT_affinity_mask':3,'SUT_RAM_limit_bytes':limits.JobMemoryLimit,'memory_hard_limit_metric':'Windows Job Object aggregate committed memory; not a physical-RSS ceiling','SUT_peak_commit_bytes':limits.PeakJobMemoryUsed,'SUT_peak_fraction':limits.PeakJobMemoryUsed/limits.JobMemoryLimit,'hard_limits_read_back':True,'load_client_in_separate_job':bool(self.clients),'load_client_start_readbacks':list(self.clients)}
    def resident_sample(self):
        import psutil
        capacity=64;ids=None
        for _ in range(4):
            size=8+capacity*8;buffer=c.create_string_buffer(size);returned=w.DWORD()
            ok=self.api.k.QueryInformationJobObject(self.h,3,buffer,size,c.byref(returned))
            assigned,listed=struct.unpack_from('<II',buffer.raw)
            if ok:
                b.require(listed<=capacity and listed<=assigned,'resource_process_list_shape')
                ids=[struct.unpack_from('<Q',buffer.raw,8+i*8)[0] for i in range(listed)];break
            b.require(c.get_last_error()==234 and capacity<4096,'resource_process_list_query');capacity=min(4096,max(capacity*2,assigned))
        b.require(ids is not None and all(0<pid<=0xffffffff for pid in ids),'resource_process_list_budget')
        rows=[];ended=[]
        for pid in ids:
            if not self.contains(pid,missing_ok=True):ended.append(pid);continue
            try:
                process=psutil.Process(pid);memory=process.memory_info();created=process.create_time();cpu=process.cpu_times()
                if not self.contains(pid,missing_ok=True):ended.append(pid);continue
                rows.append({'PID':pid,'creation_epoch':created,'RSS_bytes':memory.rss,'private_bytes':memory.private,'CPU_user_seconds':cpu.user,'CPU_system_seconds':cpu.system})
            except psutil.NoSuchProcess:ended.append(pid)
        total=sum(v['RSS_bytes'] for v in rows);self.peak_observed_rss=max(self.peak_observed_rss,total)
        memory=psutil.virtual_memory();swap=psutil.swap_memory()
        return {'SUT_processes':rows,'SUT_RSS_sum_bytes':total,'SUT_observed_peak_RSS_sum_bytes':self.peak_observed_rss,'ended_during_sample':ended,'guest_physical_total_bytes':memory.total,'guest_physical_available_bytes':memory.available,'guest_swap_used_bytes':swap.used,'guest_swap_IO_available':False,'RSS_scope':'sum can double-count shared pages; 1s samples do not establish an unsampled physical-memory ceiling; Windows psutil swap IO counters are unavailable'}
    def client(self,job,pid):
        import native_process as native
        b.require(not self.contains(pid),'load_client_must_not_inherit_SUT_quota')
        limits=native._ExtendedJobLimits();b.require(job.api.k.QueryInformationJobObject(job._job,9,c.byref(limits),c.sizeof(limits),None),'client_job_limits_query')
        b.require(limits.BasicLimitInformation.LimitFlags==0x2000,'original_client_job_not_breakaway')
        limits.BasicLimitInformation.LimitFlags|=0x200|0x10;limits.BasicLimitInformation.Affinity=4;limits.JobMemoryLimit=1024**3
        b.require(job.api.k.SetInformationJobObject(job._job,9,c.byref(limits),c.sizeof(limits)),'client_memory_affinity_quota')
        cpu=CPU(5,2500);b.require(job.api.k.SetInformationJobObject(job._job,15,c.byref(cpu),c.sizeof(cpu)),'client_CPU_quota')
        actual=native._ExtendedJobLimits();actual_cpu=CPU()
        b.require(job.api.k.QueryInformationJobObject(job._job,9,c.byref(actual),c.sizeof(actual),None) and job.api.k.QueryInformationJobObject(job._job,15,c.byref(actual_cpu),c.sizeof(actual_cpu),None),'client_resource_quota_readback')
        b.require(actual.BasicLimitInformation.LimitFlags==0x2210 and actual.BasicLimitInformation.Affinity==4 and actual.JobMemoryLimit==1024**3 and actual_cpu.ControlFlags==5 and actual_cpu.CpuRate==2500,'client_resource_quota_mismatch')
        b.require(job.contains(pid) and job.token.process(pid)['administrative_groups_enabled'] is False,'client_original_job_and_reduced_token')
        receipt={'PID':pid,'client_affinity_mask':4,'client_CPU_cap_equivalent':1,'client_RAM_limit_bytes':1024**3,'not_in_SUT_job':True,'original_nonbreakaway_owned_job':True,'hard_limits_read_back':True}
        self.clients.append(receipt);return receipt
class ClientCreation:
    """Only a fixed load client leaves the resource group; never its owned Job."""
    def __init__(self,original):self.original=original
    def __getattr__(self,name):return getattr(self.original,name)
    def CreateProcessW(self,*args):
        values=list(args);values[5]|=0x01000000
        return self.original.CreateProcessW(*values)
def install():
    global CURRENT
    b.require(CURRENT is None,'resource_group_once');CURRENT=Budget();return CURRENT
