"""Same frozen topology policy code, explicitly authorized boot identity data."""
from pathlib import Path
from types import FunctionType
import hashlib,json
import common as b
RECORD_SHA='bad6dbe4b1332fa00a6ddafc2bb9ab26c41ff3ccdbc3b47c939c36ee1e1cad67'
BOOT='2026-11-09T11:58:46.5000000Z'
MACHINE='210f9d961522ac5a7104c086fb210c698688da2242a2abceed5b970e6a0887b1'
BOOT_ROOT=Path(r'C:\Users\Administrator\PR03bS3BootBinding01')
IDENTITIES=frozenset((
 ('c20f2416-002c-43a0-94c6-c6a1675b75b0',14,6),
 ('b64e18fe-219d-4c4a-a6d5-ff40cbd4f2b9',13,6),
 ('93123211-9629-4e04-82f0-ea2e4f221468',11,131),
 ('2ee2c70c-a092-4d88-a654-98c8d7645cd5',8,131),
 ('07374750-e68b-490e-9330-9fd785cd71b6',3,131)))
def record():
    raw=(Path(__file__).parent/'EPOCH-BINDING.json').read_bytes()
    b.require(hashlib.sha256(raw).hexdigest()==RECORD_SHA,'boot_record_pin')
    value=b.strict(raw)
    b.require(value['kind']=='authorized_current_boot_observation_binding' and value['boot_utc']==BOOT
        and value['machine_binding']==MACHINE and value['database_execution_allowed'] is False,'boot_record_binding')
    identities=frozenset((a['guid'],a['index'],a['type']) for a in value['binding']['before']['adapters'])
    b.require(identities==IDENTITIES and value['binding']['targets']==[],'exact_current_boot_five_adapters')
    return value
def rebind(function):
    value=record();old=frozenset((a['guid'],a['index'],a['type']) for a in value['original_plan']['before']['adapters'])
    b.require(function.__name__=='snapshot_topology_ok' and function.__globals__['ORIGINAL_ADAPTERS']==old
        and function.__globals__['MACHINE_BINDING']==MACHINE and function.__globals__['LOOPBACK_INDEX']==1,'original_policy_context')
    # No mutation of the approved module and no new algorithm. The exact code
    # object runs with the approved replacement identity set, all other globals
    # (including loopback identity, parser and deny conditions) stay unchanged.
    policy=FunctionType(function.__code__,dict(function.__globals__,ORIGINAL_ADAPTERS=IDENTITIES),function.__name__,function.__defaults__,function.__closure__)
    b.require(policy.__code__ is function.__code__ and function.__globals__['ORIGINAL_ADAPTERS']==old,'policy_code_and_original_unchanged')
    return policy
class BoundWrapper:
    def __init__(self,original):self.original=original;self.snapshot_topology_ok=rebind(original.snapshot_topology_ok)
    def __getattr__(self,name):return getattr(self.original,name)
def bind(original):return BoundWrapper(original)
