// Syntax validation for documentation only; does not render or run product UI.
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const root = process.cwd();
const modules = path.join(root, 'work/pr01/diagram-deps/node_modules');
const { JSDOM } = await import(pathToFileURL(path.join(modules, 'jsdom/lib/api.js')));
const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.DOMParser = dom.window.DOMParser;
const { default: mermaid } = await import(pathToFileURL(path.join(modules, 'mermaid/dist/mermaid.esm.mjs')));
mermaid.initialize({ startOnLoad: false, securityLevel: 'strict' });
const dir = path.join(root, 'docs/web-alpha/architecture');
const results = [];
for (const name of (await fs.readdir(dir)).filter(x => x.endsWith('.md')).sort()) {
  const source = await fs.readFile(path.join(dir, name), 'utf8');
  let index = 0;
  for (const match of source.matchAll(/```mermaid\r?\n([\s\S]*?)```/g)) {
    index++;
    try {
      const parsed = await mermaid.parse(match[1]);
      results.push({ file: name, diagram: index, status: 'passed', type: parsed.diagramType });
    } catch (error) {
      results.push({ file: name, diagram: index, status: 'failed', error: String(error) });
    }
  }
}
const report = { at_utc: new Date().toISOString(), node: process.version, scope: 'Mermaid syntax parse, not visual/product validation', passed: results.every(r => r.status === 'passed'), results };
await fs.mkdir(path.join(root, 'outputs/pr01'), { recursive: true });
await fs.writeFile(path.join(root, 'outputs/pr01/diagram-validation.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
process.exitCode = report.passed ? 0 : 1;
