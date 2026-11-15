"""Monotonic M0-M4 orchestration; external deployment authority owns D0 writes.

This port does not install roots or authorize a maintainer from request JSON.
An implementation must use the independently registered deployment/IAM channel,
durably reserve outside application PG, and publish only the same transition.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
from typing import Protocol

from ..domain.auth import AuthRejected
from ..infrastructure.auth_crypto import random_token,digest
from ..infrastructure.recovery_authority import control_hash,instant,verify_control_signature,InstalledAuthorityRoot
from .recovery_control import wire_time


@dataclass(frozen=True,repr=False)
class AcceptedTransition:
    transition_id:str
    old_manifest:dict
    request:dict
    audit_id:str
    installed_old_root:InstalledAuthorityRoot|None=None


class ExternalMaintenance(Protocol):
    def reserve(self,request:dict,*,peer:dict,deadline:datetime)->AcceptedTransition:
        """Current independent maintainer + approved exact CAS, persisted nonce."""
        ...
    def resume(self,request:dict,*,peer:dict,deadline:datetime)->AcceptedTransition:
        """Reauthorize only the immutable existing transition; no new version."""
        ...
    def transition_current(self,transition_id:str,nonce:str)->dict: ...
    def publish(self,transition:AcceptedTransition)->InstalledAuthorityRoot:
        """Idempotently publish approved bytes/pin then mark same ledger ready."""
        ...


def validate_change(verifier,old,request,*,installed_old_root=None,committed=False):
    verifier.schema.check('D0ChangeRequest',request);verifier.schema.check('ManifestBody',old)
    approval=request['approval'];change=approval['body'];new=request['new_manifest']['body']
    pin=installed_old_root or verifier.root
    if not isinstance(pin,InstalledAuthorityRoot) or pin.deployment_id!=verifier.root.deployment_id:raise AuthRejected('FORBIDDEN',403)
    verify_control_signature(pin.key,request['new_manifest']['signature'],'classroom.identity.manifest.v1',new)
    verify_control_signature(old['change_key'],approval['signature'],'classroom.identity.change.v1',change)
    now=datetime.now(timezone.utc)
    if (old['deployment_id']!=verifier.root.deployment_id or old['root_key']!=pin.key
            or change['deployment_id']!=old['deployment_id'] or new['deployment_id']!=old['deployment_id']
            or change['old_version']!=old['version'] or change['old_manifest_hash']!=control_hash(old)
            or change['new_version']!=new['version'] or new['version']<=old['version'] or new['previous_manifest_hash']!=control_hash(old)
            or change['new_manifest_hash']!=control_hash(new) or change['old_binding_hash']!=control_hash(old['authority'])
            or change['new_binding_hash']!=control_hash(new['authority']) or change['authority_id']!=old['authority']['authority_id']
            or change['authority_id']!=new['authority']['authority_id'] or change['maintainer_ref']!=old['maintainer_ref']
            or instant(change['issued_at'])>now or (not committed and now>=instant(change['expires_at']))
            or (change['action']=='revoke' and new['authority']['enabled'])
            or (change['action']!='root_rotate' and new['root_key']!=old['root_key'])):
        raise AuthRejected('FORBIDDEN',403)
    keys=(new['root_key'],new['change_key'],new['authority']['recover_key'])
    if len({k['key_id'] for k in keys})!=3 or len({k['public_key'] for k in keys})!=3:raise AuthRejected('FORBIDDEN',403)
    changed={purpose:key for purpose,key,old_key in zip(('R0','K_change','K_recover'),keys,(old['root_key'],old['change_key'],old['authority']['recover_key'])) if key!=old_key}
    proofs=request['new_key_proofs']
    if len(proofs)!=len(changed) or {p['key_purpose'] for p in proofs}!=set(changed):raise AuthRejected('FORBIDDEN',403)
    for proof in proofs:
        if proof['key_id']!=changed[proof['key_purpose']]['key_id'] or proof['new_binding_hash']!=change['new_binding_hash'] or proof['approval_nonce']!=change['approval_nonce']:
            raise AuthRejected('FORBIDDEN',403)
        body=dict(deployment_id=change['deployment_id'],**{k:proof[k] for k in ('key_purpose','key_id','new_binding_hash','approval_nonce')})
        verify_control_signature(changed[proof['key_purpose']],proof['signature'],'classroom.identity.key-pop.v1',body)
    return change,new


def apply_change(broker,peer,request,*,external:ExternalMaintenance,resume=False):
    """Called only by the separate ACL-limited maintenance pipe adapter."""
    from datetime import timedelta
    if external is None:raise AuthRejected('SERVICE_DISABLED',503)
    broker.schema.check('D0ChangeRequest',request);broker.lease.check();subject=peer.subject()
    with broker.gate.exclusive():
        # Stay closed on any accepted/unknown external transition. No exception
        # handler returns to an old high water or reopens a mismatched system.
        old=None
        if not resume:
            current=broker.verifier.current(require_enabled=False);old=current.manifest
            validate_change(broker.verifier,old,request)
        broker.closed=True
        # The exclusive gate drained real operations. Close their idle pools as
        # well before a restart/transition may adopt the sole broker binding.
        broker.repository.dispose()
        transition=(external.resume if resume else external.reserve)(request,peer=subject,deadline=datetime.now(timezone.utc)+timedelta(seconds=5))
        if not isinstance(transition,AcceptedTransition) or transition.request!=request or (old is not None and transition.old_manifest!=old):raise AuthRejected('FORBIDDEN',403)
        candidate=request['approval']['body'];new=request['new_manifest']['body']
        identity=dict(deployment_id=candidate['deployment_id'],transition_id=transition.transition_id,new_version=new['version'],new_manifest_hash=control_hash(new),
            old_version=candidate['old_version'],old_manifest_hash=candidate['old_manifest_hash'],approval_hash=control_hash(candidate),nonce_hash=digest('maintenance_nonce',candidate['approval_nonce']))
        committed=broker.repository.call('registration_change_status',identity) is True
        change,new=validate_change(broker.verifier,transition.old_manifest,request,installed_old_root=transition.installed_old_root,committed=committed)
        nonce=random_token();evidence=external.transition_current(transition.transition_id,nonce)
        broker.schema.check('D0CurrentEvidence',evidence);body=evidence['body'];now=datetime.now(timezone.utc)
        verify_control_signature(broker.verifier.root.key,evidence['signature'],'classroom.identity.current.v1',body)
        if (body['request_nonce']!=nonce or body['deployment_id']!=change['deployment_id']
                or not ((body['state']=='transition_reserved' and body['transition_id']==transition.transition_id) or (committed and body['state']=='ready' and body['transition_id'] is None))
                or body['high_water_version']!=new['version'] or body['high_water_hash']!=control_hash(new)
                or not instant(body['issued_at'])<=now<instant(body['expires_at'])):raise AuthRejected('FORBIDDEN',403)
        payload=dict(deployment_id=change['deployment_id'],broker_instance_id=broker.instance_id,transition_id=transition.transition_id,
            old_version=change['old_version'],old_manifest_hash=change['old_manifest_hash'],new_version=new['version'],new_manifest_hash=control_hash(new),
            authority_id=change['authority_id'],os_mapping_hash=control_hash(new['authority']),enabled=new['authority']['enabled'],
            approval_hash=control_hash(change),nonce_hash=digest('maintenance_nonce',change['approval_nonce']),approval_deadline=change['expires_at'],audit_id=transition.audit_id)
        broker._need(broker.repository.call('adopt_registration_transition',{**payload,'external_current_deadline':body['expires_at'],'service_logins':list(broker.repository.service_logins)}))
        broker._need(broker.repository.call('apply_registration_change',payload))
        # Only the separately trusted installer may return a published pin.
        pin=external.publish(transition)
        if not isinstance(pin,InstalledAuthorityRoot) or pin.deployment_id!=change['deployment_id'] or pin.key!=new['root_key']:raise AuthRejected('FORBIDDEN',403)
        broker.verifier.root=pin
        current=broker.verifier.current(require_enabled=False)
        if current.version!=new['version'] or current.manifest_hash!=control_hash(new):raise AuthRejected('FORBIDDEN',403)
        broker._need(broker.repository.call('publish_registration_ready',{**broker._base(current),'transition_id':transition.transition_id}))
        broker.closed=False
        # Old authenticated contexts/proofs do not survive a registry change.
        for connection in list(broker.connections.values()):broker.disconnect(connection)
        for waiter in list(broker.delivery.values()):broker.cancel_wait(waiter)
        broker.receivers.clear()
        return broker._emit('D0ChangeResponse',dict(deployment_id=change['deployment_id'],transition_id=transition.transition_id,version=new['version'],manifest_hash=control_hash(new),phase='published_ready',audit_id=transition.audit_id))
