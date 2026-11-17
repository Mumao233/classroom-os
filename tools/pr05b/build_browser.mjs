import { createRequire } from 'node:module';
import { resolve } from 'node:path';
const require = createRequire(resolve(process.argv[2], 'web/frontend/package.json'));
const esbuild = createRequire(require.resolve('vite/package.json'))('esbuild');
await esbuild.build({ entryPoints: ['tools/pr05b/browser-entry.ts'], outfile: process.argv[3], bundle: true,
  platform: 'browser', target: 'es2022', format: 'iife', plugins: [{ name: 'worklet-url', setup(build) {
    build.onResolve({ filter: /pcm-worklet\.js\?url$/ }, () => ({ path: 'pcm-worklet', namespace: 'capture-worklet' }));
    build.onLoad({ filter: /.*/, namespace: 'capture-worklet' }, () => ({ contents: 'export default "/synthetic/pcm-worklet.js"' }));
  } }] });
