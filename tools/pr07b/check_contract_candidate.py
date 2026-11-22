"""Validate the explicit PR-07b OpenAPI composition and synthetic fixtures."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def load_builder(path):
    spec = importlib.util.spec_from_file_location('candidate_' + path.parent.name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build


def pointer(document, reference):
    if not reference.startswith('#/'):
        raise ValueError('Only local contract references are accepted')
    for part in reference[2:].split('/'):
        document = document[part.replace('~1', '/').replace('~0', '~')]
    return document


def check_references(document):
    references = 0

    def visit(value):
        nonlocal references
        if isinstance(value, dict):
            if '$ref' in value:
                pointer(document, value['$ref'])
                references += 1
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    return references


def fixture_result(candidate, case):
    from jsonschema import Draft202012Validator, FormatChecker

    if case['schema_file'] != 'candidate-openapi.json':
        raise ValueError('Fixture must reference the explicit composed candidate')
    pointer(candidate, case['schema_pointer'])
    schema = {'$schema': 'https://json-schema.org/draft/2020-12/schema',
              '$ref': case['schema_pointer'], 'components': candidate['components']}
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(case['value']))
    valid = not errors
    return dict(name=case['name'], expected_valid=case['expected_valid'], actual_valid=valid,
                passed=valid == case['expected_valid'],
                errors=[dict(path=list(error.absolute_path), validator=error.validator) for error in errors])


def validate_candidate(root):
    from jsonschema import Draft202012Validator
    from openapi_spec_validator import validate

    root = root.resolve(strict=True)
    base_path = root / 'docs/web-alpha/architecture/contracts/openapi.json'
    fixture_path = root / 'tools/pr07b/fixtures.json'
    base = read_json(base_path)
    original = deepcopy(base)
    fixtures = read_json(fixture_path)
    names = [case['name'] for case in fixtures['cases']]
    if len(names) != len(set(names)) or not names:
        raise ValueError('Fixture names must be nonempty and unique')
    if any(type(case['expected_valid']) is not bool for case in fixtures['cases']):
        raise ValueError('Every fixture declares a boolean expectation')
    if {case['expected_valid'] for case in fixtures['cases']} != {True, False}:
        raise ValueError('Both positive and negative contract fixtures are required')
    if fixtures['contract_version'] != base['info']['version'] or fixtures['candidate'] != 'pr07b-1':
        raise ValueError('Fixture contract version does not match the candidate')
    review_builder = load_builder(root / 'tools/pr07b/contract_candidate.py')
    billing_builder = load_builder(root / 'tools/pr11a/contract_candidate.py')
    report = dict(schema_version=1, at_utc=datetime.now(timezone.utc).isoformat(), passed=False,
                  scope='Static candidate OpenAPI and fixtures',
                  base_sha256=hashlib.sha256(base_path.read_bytes()).hexdigest(),
                  fixture_sha256=hashlib.sha256(fixture_path.read_bytes()).hexdigest(), compositions=[])
    for name, input_contract in (('review', base), ('billing-and-review', billing_builder(base))):
        before = deepcopy(input_contract)
        candidate = review_builder(input_contract)
        if (candidate['info']['version'] != fixtures['contract_version']
                or candidate.get('x-pr07b-candidate') != fixtures['candidate']):
            raise ValueError('Fixture contract version does not match the candidate')
        if input_contract != before or base != original:
            raise ValueError('Candidate composition mutated its input contract')
        references = check_references(candidate)
        validate(candidate)
        for schema in candidate['components']['schemas'].values():
            Draft202012Validator.check_schema(schema)
        operations = [operation['operationId'] for path in candidate['paths'].values()
                      for method, operation in path.items()
                      if method in ('get', 'post', 'put', 'patch', 'delete')]
        if len(operations) != len(set(operations)):
            raise ValueError('Candidate contains duplicate operation IDs')
        results = [fixture_result(candidate, case) for case in fixtures['cases']]
        report['compositions'].append(dict(name=name, local_references=references,
            operations=len(operations), schemas=len(candidate['components']['schemas']),
            positive_cases=sum(case['expected_valid'] for case in fixtures['cases']),
            negative_cases=sum(not case['expected_valid'] for case in fixtures['cases']),
            passed=all(result['passed'] for result in results), fixtures=results))
    report['passed'] = all(item['passed'] for item in report['compositions'])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--deps', type=Path, help='Optional isolated static-validation dependencies')
    parser.add_argument('--output', type=Path, help='Optional JSON evidence path')
    args = parser.parse_args()
    if args.deps:
        sys.path.insert(0, str(args.deps.resolve(strict=True)))
    try:
        report = validate_candidate(args.repo)
    except Exception as error:
        report = dict(schema_version=1, passed=False, error=str(error))
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    print(text, end='')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
