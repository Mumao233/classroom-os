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
    for purpose, body in [('adminUpdateSwitches','AdminSwitchesPatch'),('adminAdjustQuota','QuotaAdjustment')]:
        target = dict(type='object', additionalProperties=False, required=['kind','id'],
                      properties=dict(kind=dict(const='account'),id=dict(type='string',format='uuid')))
        parameters = dict(type='object', additionalProperties=False, required=['idempotency_key','body'],
                          properties=dict(idempotency_key=dict(type='string',format='uuid'),
                                          body={'$ref':'#/components/schemas/'+body}))
        operation = dict(type='object', additionalProperties=False, required=['purpose','target','candidate_parameters'],
                         properties=dict(purpose=dict(const=purpose),target=target,candidate_parameters=parameters))
        schemas['ReauthOperation']['oneOf'].append(operation)
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
        'ReauthOperation': {'oneOf_append':schemas['ReauthOperation']['oneOf'][-2:]},
    }
    delta = dict(base_version=base['info']['version'],base_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        status='unapproved integration candidate',operations=['getQuota','cancelTask','adminGetSwitches','adminUpdateSwitches',
            'adminGetUserQuota','adminAdjustQuota','adminGetUsage','reauthenticate'],
        schema_extensions=extensions,
        compatibility='Admin routes require explicit candidate composition. Shared OpenAPI and generated frontend types are unchanged.')
    (root/'docs/web-alpha/pr11a/CONTRACT-CANDIDATE.json').write_text(json.dumps(delta,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
