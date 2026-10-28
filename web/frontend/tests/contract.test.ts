import { describe, expect, it } from 'vitest';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { coursePage, meta, semesterPage } from '../src/mock/fixtures';

const source = resolve(process.cwd(), '../../docs/web-alpha/architecture/contracts');
const read = (name: string) => JSON.parse(readFileSync(resolve(source, name), 'utf8'));
const openapi = read('openapi.json');
const events = read('events.schema.json');
const cases = read('examples.json').cases as Array<{name: string; schema_file: string; schema_pointer: string; expected_valid: boolean; value: unknown}>;
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema({ $id: 'https://classroom.invalid/openapi', components: openapi.components });
ajv.addSchema(events, 'https://classroom.invalid/events');

describe('approved PR-01 contract examples', () => {
  for (const item of cases) {
    it(item.name, () => {
      const base = item.schema_file === 'openapi.json' ? 'https://classroom.invalid/openapi' : 'https://classroom.invalid/events';
      const validate = ajv.compile({ $ref: base + item.schema_pointer });
      expect(validate(item.value), JSON.stringify(validate.errors)).toBe(item.expected_valid);
    });
  }
});

describe('mock HTTP fixtures', () => {
  for (const [schema, fixture] of [['CoursePage', coursePage], ['SemesterPage', semesterPage], ['Meta', meta]] as const) {
    it(`${schema} conforms`, () => {
      const validate = ajv.compile({ $ref: `https://classroom.invalid/openapi#/components/schemas/${schema}` });
      expect(validate(fixture), JSON.stringify(validate.errors)).toBe(true);
    });
  }
});
