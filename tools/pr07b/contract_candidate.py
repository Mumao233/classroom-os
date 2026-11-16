"""PR-07b explicit composition overlay on the PR-07a source contract."""
import importlib.util
from pathlib import Path


# The final lookahead also rejects a trailing newline under JSON Schema regex rules.
REVIEW_ALIAS_PATTERN = r'^S(?:[1-9]|[1-9][0-9]|[1-4][0-9]{2}|50[0-9]|51[0-2])(?![\s\S])'


def build(base):
    path = Path(__file__).parents[1] / 'pr07a' / 'contract_candidate.py'
    spec = importlib.util.spec_from_file_location('pr07b_qa_contract', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidate = module.build(base)
    candidate['x-pr07b-candidate'] = 'pr07b-1'
    candidate['x-pr07b-review-status'] = 'candidate, explicit composition only'
    schemas = candidate['components']['schemas']
    for name in ('ReviewCard', 'ReviewNoteSection'):
        aliases = schemas[name]['properties']['source_ids']
        aliases.update(minItems=1, maxItems=512, uniqueItems=True)
        aliases['items']['pattern'] = REVIEW_ALIAS_PATTERN
    schemas['SourceSnapshot']['properties']['items']['maxItems'] = 512
    schemas['QaSourceItem']['properties']['alias']['pattern'] = REVIEW_ALIAS_PATTERN
    schemas['ReviewRevision']['properties']['generation_task_id'] = dict(
        anyOf=[dict(type='string', format='uuid'), dict(type='null')],
        description='Root generation task for GET /tasks/{task_id}/raw-output. Personal revisions expose null.')
    schemas['Task']['properties']['batch_tasks'] = dict(type='array', maxItems=32, items=dict(
        type='object', additionalProperties=False,
        properties=dict(batch_index=dict(type='integer', minimum=0, maximum=31),
            task_id=dict(anyOf=[dict(type='string', format='uuid'), dict(type='null')]),
            state=dict(type='string', enum=['planned', 'queued', 'running', 'succeeded', 'failed',
                                          'unknown', 'cancel_requested', 'cancelled']),
            source_ids=dict(type='array', minItems=1, maxItems=64, uniqueItems=True,
                            items=dict(type='string', pattern=REVIEW_ALIAS_PATTERN))),
        required=['batch_index', 'task_id', 'state', 'source_ids']))
    request = schemas['GenerationPlanRequest']
    request['properties'].update(
        target_document_id=dict(type='string', format='uuid'),
        base_revision=dict(type='integer', minimum=1),
        granularity=dict(type='string', enum=['concise', 'standard', 'detailed']))
    request['dependentRequired'] = {'target_document_id': ['base_revision'],
                                    'base_revision': ['target_document_id']}
    request['allOf'] = [dict(
        **{'if': dict(anyOf=[dict(required=[name]) for name in
                           ('target_document_id', 'base_revision', 'granularity')]),
           'then': dict(properties=dict(kind=dict(const='review_summary')))}),
        {'if': {'properties': {'kind': {'const': 'review_summary'}}},
         'then': {'properties': {'card_types': {'uniqueItems': True}}}}]
    return candidate
