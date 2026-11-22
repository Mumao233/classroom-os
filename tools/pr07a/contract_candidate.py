"""Explicit PR-07a overlay on the approved shared HTTP contract."""
from copy import deepcopy


QA_OPERATIONS = {
    '/class-sessions/{class_session_id}/generation-plans': 'prepareGeneration',
    '/generation-plans/{plan_id}': 'getGenerationPlan',
    '/generation-plans/{plan_id}/execute': 'executeGeneration',
    '/source-snapshots/{snapshot_id}': 'getSourceSnapshot',
    '/questions': 'listQuestions',
    '/questions/{question_id}': 'getQuestion',
    '/tasks/{task_id}': 'getTask',
    '/tasks/{task_id}/cancel': 'cancelTask',
    '/tasks/{task_id}/reconcile': 'reconcileTask',
}


def build(base):
    if base.get('info', {}).get('version') not in {'0.2.0', '0.2.1', '0.3.0'}:
        raise ValueError('PR-07a requires contract 0.2.0, 0.2.1 or 0.3.0')
    candidate = deepcopy(base)
    candidate['x-pr07a-candidate'] = 'pr07a-1'
    candidate['x-pr07a-review-status'] = 'candidate, explicit composition only'
    schemas = candidate['components']['schemas']
    schemas['SourceRef']['properties']['kind']['enum'].append('course_material_version')
    identity = dict(type='string', format='uuid')
    digest = dict(type='string', pattern='^[a-f0-9]{64}$')
    offset = dict(type='integer', minimum=0)
    classroom_time = dict(anyOf=[offset, dict(type='null')])
    properties = dict(
        alias=dict(type='string', pattern='^S[1-9][0-9]*$'),
        kind=dict(type='string', enum=['transcript_segment', 'course_material_version']),
        source_id=identity, fragment_id=identity, object_id=identity, revision_id=identity,
        class_session_id=identity, content_sha256=digest, excerpt_sha256=digest,
        excerpt=dict(type='string', maxLength=32768),
        excerpt_start=offset, excerpt_end=offset, start_ms=classroom_time, end_ms=classroom_time,
        visible_at=dict(type='string', format='date-time'), origin=dict(type='string', maxLength=64),
    )
    schemas['QaSourceItem'] = dict(type='object', additionalProperties=False,
                                  properties=properties, required=list(properties))
    snapshot = schemas['SourceSnapshot']
    snapshot['properties']['start_ms'] = classroom_time
    snapshot['properties']['end_ms'] = classroom_time
    snapshot['properties']['items'] = dict(type='array', maxItems=12,
                                           items={'$ref': '#/components/schemas/QaSourceItem'})
    snapshot['required'].append('items')
    question = schemas['Question']
    question['properties']['cited_source_ids'] = dict(
        type='array', maxItems=12, uniqueItems=True,
        items=dict(type='string', pattern='^S[1-9][0-9]*$'))
    question['required'].append('cited_source_ids')
    route = deepcopy(candidate['paths']['/generation-plans/{plan_id}']['get'])
    route['operationId'] = 'getQuestion'
    route['summary'] = 'Read an authorized question and its validated source aliases'
    route['tags'] = ['questions']
    route['parameters'] = [dict(name='question_id', **{'in': 'path'}, required=True, schema=identity)]
    route['responses']['200']['content']['application/json']['schema'] = {
        '$ref': '#/components/schemas/Question'}
    candidate['paths']['/questions/{question_id}']['get'] = route
    return candidate
