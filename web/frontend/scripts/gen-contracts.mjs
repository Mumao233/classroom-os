import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import openapiTS, { astToString } from 'openapi-typescript';
import { compile } from 'json-schema-to-typescript';

// The PR-01 contracts are the only source of truth; --check detects drift.
const openapi = new URL('../../../docs/web-alpha/architecture/contracts/openapi.json', import.meta.url);
const events = new URL('../../../docs/web-alpha/architecture/contracts/events.schema.json', import.meta.url);
const control = new URL('../../../docs/web-alpha/architecture/contracts/identity-control.schema.json', import.meta.url);
const document = JSON.parse(await readFile(openapi, 'utf8'));
// Ship only the route-specific key constraints to the development Mock.
const v4WriteKeys = { pattern: document.components.schemas.CanonicalUuidV4.pattern, routes: [] };
for (const [path, methods] of Object.entries(document.paths)) {
  for (const [method, operation] of Object.entries(methods)) {
    const headers = (operation.parameters ?? []).map(parameter =>
      parameter.$ref ? document.components.parameters[parameter.$ref.split('/').at(-1)] : parameter);
    if (headers.some(header => header.in === 'header' && header.name === 'Idempotency-Key'
        && header.schema.$ref === '#/components/schemas/CanonicalUuidV4')) {
      v4WriteKeys.routes.push(`${method.toUpperCase()} ${path.replace(/\{[^}]+\}/g, ':id')}`);
    }
  }
}
const targets = [
  [new URL('../src/generated/openapi.ts', import.meta.url), astToString(await openapiTS(openapi))],
  [new URL('../src/generated/uuid-v4-write-keys.json', import.meta.url), JSON.stringify(v4WriteKeys, null, 2) + '\n'],
  [new URL('../src/generated/events.ts', import.meta.url), await compile(JSON.parse(await readFile(events, 'utf8')), 'WebEvent', { bannerComment: '/* Generated from the PR-01 events schema. Do not edit. */' })],
  [new URL('../src/generated/identity-control.ts', import.meta.url), await compile(JSON.parse(await readFile(control, 'utf8')), 'IdentityControlMessage', { bannerComment: '/* Generated from the approved PR-03b identity-control schema. Do not edit. */' })],
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
