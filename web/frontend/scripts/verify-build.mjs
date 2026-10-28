import { readdir, readFile } from 'node:fs/promises';

const root = new URL('../../../work/pr09a/dist/', import.meta.url);
async function files(dir) {
  const all = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const child = new URL(`${entry.name}${entry.isDirectory() ? '/' : ''}`, dir);
    if (entry.isDirectory()) all.push(...await files(child));
    else all.push(child);
  }
  return all;
}
const violations = [];
const output = await files(root);
let buildIdFound = false;
for (const path of output) {
  if (/mockServiceWorker|MockWorkbench/i.test(path.pathname)) violations.push(`Mock file: ${path.pathname}`);
  if (/\.(?:js|html|css|json)$/.test(path.pathname)) {
    const contents = await readFile(path, 'utf8');
    if (contents.includes('web-alpha-0.1.0-pr09a')) buildIdFound = true;
    for (const marker of ['mockServiceWorker.js', 'MOCK-01', '开发模拟数据', 'MOCKING_ENABLED']) {
      if (contents.includes(marker)) violations.push(`${marker} in ${path.pathname}`);
    }
  }
}
if (!output.some(path => path.pathname.endsWith('.js'))) violations.push('No production JavaScript asset');
if (!buildIdFound) violations.push('Public build identifier missing');
if (violations.length) { console.error(violations.join('\n')); process.exitCode = 1; }
else console.log(`Production output checked (${output.length} files): no known mock entry markers.`);
