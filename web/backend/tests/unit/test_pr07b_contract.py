"""Candidate schema compatibility checks, executed by the VMware test suite."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import pytest


ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    'pr07b_contract_checks', ROOT / 'tools/pr07b/check_contract_candidate.py')
CHECKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKS)
BASE = json.loads((ROOT / 'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf-8'))
FIXTURES = json.loads((ROOT / 'tools/pr07b/fixtures.json').read_text(encoding='utf-8'))['cases']


@pytest.fixture(scope='module')
def candidate():
    return CHECKS.load_builder(ROOT / 'tools/pr07b/contract_candidate.py')(BASE)


def validator(candidate, name):
    return Draft202012Validator({'$ref': '#/components/schemas/' + name,
        'components': candidate['components']}, format_checker=FormatChecker())


@pytest.mark.parametrize('case', FIXTURES, ids=[case['name'] for case in FIXTURES])
def test_candidate_positive_and_negative_fixtures(candidate, case):
    result = CHECKS.fixture_result(candidate, case)
    assert result['passed'], result


def test_candidate_composition_and_frozen_base_are_compatible():
    report = CHECKS.validate_candidate(ROOT)
    assert report['passed'], report
    assert [item['name'] for item in report['compositions']] == ['review', 'billing-and-review']


def test_composed_candidate_preserves_manifest_operations_and_page_fixtures(candidate):
    for path in ('/class-sessions/{class_session_id}/audio-manifest', '/class-sessions/{class_session_id}/audio-manifests'):
        assert candidate['paths'][path] == BASE['paths'][path]
    examples = json.loads((ROOT / 'docs/web-alpha/architecture/contracts/examples.json').read_text(encoding='utf-8'))
    cases = {case['name']: case for case in examples['cases']}
    page = validator(candidate, 'AudioManifestPage')
    for name in ('manifests_retired_then_ended', 'manifests_missing_cursor', 'manifests_empty_continuation'):
        assert page.is_valid(cases[name]['value']) == cases[name]['expected_valid']


def test_unapproved_patch_version_is_rejected_by_http_and_candidate():
    from classroom_web.entrypoints.http import create_app

    unapproved = {**BASE, 'info': {**BASE['info'], 'version': '0.3.1'}}
    with pytest.raises(ValueError, match='approved contract'):
        create_app(auth=None, contract=unapproved, origin='https://testserver')
    with pytest.raises(ValueError, match='PR-07a requires contract'):
        CHECKS.load_builder(ROOT / 'tools/pr07b/contract_candidate.py')(unapproved)


def test_batch_extension_is_optional_and_personal_save_contract_is_preserved(candidate):
    schemas = candidate['components']['schemas']
    assert 'batch_tasks' not in schemas['Task']['required']
    for name in ('PersonalRevisionWrite', 'ReviewDocumentCreate', 'AdoptRequest', 'ReviewDocument'):
        assert schemas[name] == BASE['components']['schemas'][name]
    legacy = next(case['value'] for case in FIXTURES if case['name'] == 'legacy_qa_task_without_batch_tasks')
    assert validator(candidate, 'Task').is_valid(legacy)


def test_generation_task_pointer_is_optional_nullable_and_keeps_formal_base_version(candidate):
    revision = candidate['components']['schemas']['ReviewRevision']
    assert 'generation_task_id' not in revision['required']
    assert revision['properties']['generation_task_id']['anyOf'] == [
        {'type': 'string', 'format': 'uuid'}, {'type': 'null'}]
    assert 'generation_task_id' not in BASE['components']['schemas']['ReviewRevision']['properties']
    assert candidate['info']['version'] == BASE['info']['version']
    assert BASE['info']['version'] == '0.4.0'
    legacy = next(case['value'] for case in FIXTURES if case['name'] == 'ai_candidate_revision')
    assert 'generation_task_id' not in legacy
    assert validator(candidate, 'ReviewRevision').is_valid(legacy)


@pytest.mark.parametrize('alias', ['S1', 'S9', 'S10', 'S99', 'S100', 'S499', 'S500', 'S509', 'S510', 'S512'])
def test_global_alias_boundaries_accept_canonical_values(candidate, alias):
    item = deepcopy(next(case['value'] for case in FIXTURES
                         if case['name'] == 'snapshot_item_global_alias_boundary'))
    item['alias'] = alias
    assert validator(candidate, 'QaSourceItem').is_valid(item)


@pytest.mark.parametrize('alias', ['S0', 'S01', 'S513', 'S999', 'S1000', 'P1', 's1', 'S1\n'])
def test_global_alias_boundaries_reject_noncanonical_values(candidate, alias):
    item = deepcopy(next(case['value'] for case in FIXTURES
                         if case['name'] == 'snapshot_item_global_alias_boundary'))
    item['alias'] = alias
    assert not validator(candidate, 'QaSourceItem').is_valid(item)


@pytest.mark.parametrize('count,valid', [(32, True), (33, False)])
def test_batch_count_boundary(candidate, count, valid):
    task = deepcopy(next(case['value'] for case in FIXTURES if case['name'] == 'review_parent_pending_batches'))
    entry = task['batch_tasks'][0]
    task['batch_tasks'] = [deepcopy(entry) for _ in range(count)]
    assert validator(candidate, 'Task').is_valid(task) == valid


@pytest.mark.parametrize('count,valid', [(64, True), (65, False)])
def test_batch_source_unit_boundary(candidate, count, valid):
    task = deepcopy(next(case['value'] for case in FIXTURES if case['name'] == 'review_parent_pending_batches'))
    task['batch_tasks'][0]['source_ids'] = [f'S{index + 1}' for index in range(count)]
    assert validator(candidate, 'Task').is_valid(task) == valid


@pytest.mark.parametrize('count,valid', [(512, True), (513, False)])
def test_snapshot_source_unit_boundary(candidate, count, valid):
    item = deepcopy(next(case['value'] for case in FIXTURES
                         if case['name'] == 'snapshot_item_global_alias_boundary'))
    items_schema = candidate['components']['schemas']['SourceSnapshot']['properties']['items']
    schema = {**items_schema, 'components': candidate['components']}
    assert Draft202012Validator(schema, format_checker=FormatChecker()).is_valid([item] * count) == valid


@pytest.mark.parametrize('route,method,request_name,response_code,response_name', [
    ('/review-documents', 'post', 'ReviewDocumentCreate', '201', 'ReviewDocument'),
    ('/review-documents/{document_id}/personal-revisions', 'post', 'PersonalRevisionWrite', '201', 'ReviewRevision'),
    ('/review-documents/{document_id}/adopt', 'post', 'AdoptRequest', '200', 'ReviewDocument'),
    ('/class-sessions/{class_session_id}/generation-plans', 'post', 'GenerationPlanRequest', '201', 'GenerationPlan'),
])
def test_public_write_paths_bind_expected_dtos(candidate, route, method, request_name, response_code, response_name):
    operation = candidate['paths'][route][method]
    assert operation['requestBody']['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/' + request_name}
    assert operation['responses'][response_code]['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/' + response_name}


@pytest.mark.parametrize('route,response_name', [
    ('/review-documents/{document_id}', 'ReviewDocument'),
    ('/review-revisions/{revision_id}', 'ReviewRevision'),
    ('/tasks/{task_id}', 'Task'),
    ('/source-snapshots/{snapshot_id}', 'SourceSnapshot'),
    ('/questions/{question_id}', 'Question'),
])
def test_public_read_paths_bind_expected_dtos(candidate, route, response_name):
    operation = candidate['paths'][route]['get']
    assert operation['responses']['200']['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/' + response_name}
