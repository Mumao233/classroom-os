import { createRequire } from 'node:module';
import { realpathSync, mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const modules = process.env.PR08A_FRONTEND_MODULES;
const output = process.env.PR08A_OUTPUT;
if (!modules || !output) throw new Error('Guest modules and output are required');
const require = createRequire(realpathSync(join(modules, 'vite', 'package.json')));
const { build } = require('esbuild');
mkdirSync(output, { recursive: true });
await build({
  stdin: { contents: `export { AuthClient } from './src/api/auth-client'; export { TrustedCache } from './src/cache/trusted-cache'; export { openCacheDatabase } from './src/cache/indexed-db';`,
    resolveDir: join(root, 'web/frontend'), loader: 'ts' },
  bundle: true, format: 'esm', platform: 'browser', target: 'es2022', nodePaths: [modules],
  outfile: join(output, 'cache.js'), sourcemap: false,
});
