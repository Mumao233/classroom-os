"""Explicit unapproved overlay. The frozen shared contract is an input only."""
import copy
import hashlib
import json
from pathlib import Path


FINE_SWITCHES = ('billing', 'new_capture', 'asr_streaming', 'asr_filetrans', 'ai_interaction', 'ai_summary')


def build(base):
    candidate = copy.deepcopy(base)
    candidate['x-pr11a-candidate'] = True
    candidate['x-review-status'] = 'unapproved, coordinator and contract review required'
    schemas = candidate['components']['schemas']
    for kind in ('AdminSwitches', 'AdminSwitchesPatch'):
        for flag in FINE_SWITCHES:
            schemas[kind]['properties'][flag+'_enabled'] = dict(type='boolean')
    # Coarse and fine updates have explicit mutually exclusive semantics.
    schemas['AdminSwitchesPatch']['allOf'] = [
        {'not': {'required': [coarse, fine+'_enabled']}}
        for coarse, names in [('asr_enabled',('asr_streaming','asr_filetrans')),
                              ('ai_enabled',('ai_interaction','ai_summary'))] for fine in names]
    operations = schemas['ReauthOperation']['oneOf']
    public_purposes = {op['properties']['purpose']['const'] for op in operations}
    for purpose, body in [('adminUpdateSwitches','AdminSwitchesPatch'),('adminAdjustQuota','QuotaAdjustment')]:
        # HTTP 0.4.0 owns these branches. Their body references already pick up the switch overlay.
        if purpose in public_purposes:
            continue
        target = dict(type='object', additionalProperties=False, required=['kind','id'],
                      properties=dict(kind=dict(const='account'),id=dict(type='string',format='uuid')))
        parameters = dict(type='object', additionalProperties=False, required=['idempotency_key','body'],
                          properties=dict(idempotency_key=dict(type='string',format='uuid'),
                                          body={'$ref':'#/components/schemas/'+body}))
        operation = dict(type='object', additionalProperties=False, required=['purpose','target','candidate_parameters'],
                         properties=dict(purpose=dict(const=purpose),target=target,candidate_parameters=parameters))
        operations.append(operation)
    return candidate


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[2]
    source = root/'docs/web-alpha/architecture/contracts/openapi.json'
    base = json.loads(source.read_text(encoding='utf-8'))
    candidate = build(base)
    schemas = candidate['components']['schemas']
    extensions = {
        'AdminSwitches': {'properties_append': {flag+'_enabled':dict(type='boolean') for flag in FINE_SWITCHES}},
        'AdminSwitchesPatch': {'properties_append': {flag+'_enabled':dict(type='boolean') for flag in FINE_SWITCHES},
                              'allOf_append':schemas['AdminSwitchesPatch']['allOf']},
    }
    original_count = len(base['components']['schemas']['ReauthOperation']['oneOf'])
    extra_operations = schemas['ReauthOperation']['oneOf'][original_count:]
    if extra_operations:
        extensions['ReauthOperation'] = {'oneOf_append': extra_operations}
    delta = dict(base_version=base['info']['version'],base_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        status='unapproved integration candidate',operations=['getQuota','cancelTask','adminGetSwitches','adminUpdateSwitches',
            'adminGetUserQuota','adminAdjustQuota','adminGetUsage','reauthenticate'],
        schema_extensions=extensions,
        compatibility='Fine-grained switches require explicit candidate composition. HTTP 0.4.0 defines the two billing reauthentication purposes and coarse switches. Build the overlay separately for its consumers.')
    (root/'docs/web-alpha/pr11a/CONTRACT-CANDIDATE.json').write_bytes((json.dumps(delta,indent=2,ensure_ascii=False)+'\n').encode('utf-8'))
