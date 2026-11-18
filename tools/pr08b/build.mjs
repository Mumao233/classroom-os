import { createRequire } from 'node:module';
import { realpathSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const modules = process.env.PR08B_FRONTEND_MODULES;
const output = process.env.PR08B_OUTPUT;
if (!modules || !output) throw new Error('Guest modules and output are required');
const require = createRequire(realpathSync(join(modules, 'vite', 'package.json')));
const { build } = require('esbuild');
await build({
  stdin: {
    contents: `export { AuthClient } from './src/api/auth-client';
export { TrustedCache } from './src/cache/trusted-cache';
export { openCacheDatabase } from './src/cache/indexed-db';
export { OfflineSyncRuntime } from './src/sync/runtime';
export { AuthenticatedSyncClient, syncFingerprint } from './src/sync/client';
export { ConflictPanel, ConflictRecoveryList } from './src/sync/ConflictPanel';
export { default as React } from 'react';
export { createRoot } from 'react-dom/client';`,
    resolveDir: join(root, 'web/frontend'), loader: 'ts',
  },
  bundle: true, format: 'esm', platform: 'browser', target: 'es2022', jsx: 'automatic', nodePaths: [modules],
  outfile: join(output, 'sync.js'), sourcemap: false,
});
