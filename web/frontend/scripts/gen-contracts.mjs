import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import openapiTS, { astToString } from 'openapi-typescript';
import { compile } from 'json-schema-to-typescript';

// The PR-01 contracts are the only source of truth; --check detects drift.
const openapi = new URL('../../../docs/web-alpha/architecture/contracts/openapi.json', import.meta.url);
const events = new URL('../../../docs/web-alpha/architecture/contracts/events.schema.json', import.meta.url);
const targets = [
  [new URL('../src/generated/openapi.ts', import.meta.url), astToString(await openapiTS(openapi))],
  [new URL('../src/generated/events.ts', import.meta.url), await compile(JSON.parse(await readFile(events, 'utf8')), 'WebEvent', { bannerComment: '/* Generated from the PR-01 events schema. Do not edit. */' })],
];
let stale = false;
for (const [path, contents] of targets) {
  if (process.argv.includes('--check')) {
    const existing = await readFile(path, 'utf8').catch(() => '');
    if (existing !== contents) { console.error(`Generated contract is stale: ${fileURLToPath(path)}`); stale = true; }
  } else {
    await writeFile(path, contents);
  }
}
if (stale) process.exitCode = 1;
