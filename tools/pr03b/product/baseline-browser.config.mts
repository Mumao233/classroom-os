import { defineConfig, devices } from '../../../web/frontend/node_modules/@playwright/test/index.mjs';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';
const here=dirname(fileURLToPath(import.meta.url)), root=resolve(here,'../../..'), work=resolve(root,'work/pr03b/product-completion-20261110-01'), frontend=resolve(root,'web/frontend');
export default defineConfig({
  testDir:resolve(frontend,'tests'),testMatch:'*.browser.spec.ts',workers:1,
  outputDir:resolve(work,'baseline-browser-results'),
  use:{...devices['Desktop Chrome'],channel:'msedge',trace:'off',launchOptions:{args:['--disable-gpu','--disable-background-networking','--no-first-run']}},
  webServer:[
    {command:'node node_modules/vite/bin/vite.js --mode mock --host 127.0.0.1 --port 19309 --strictPort',cwd:frontend,url:'http://127.0.0.1:19309',reuseExistingServer:false},
    {command:'node node_modules/vite/bin/vite.js preview --mode production --host 127.0.0.1 --port 19310 --strictPort',cwd:frontend,url:'http://127.0.0.1:19310',reuseExistingServer:false},
  ],
});
