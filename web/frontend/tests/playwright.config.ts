import { defineConfig, devices } from '@playwright/test';
import { resolve } from 'node:path';

const frontend = process.cwd();

export default defineConfig({
  testDir: '.', testMatch: '*.browser.spec.ts',
  outputDir: resolve(frontend, '../../work/pr09a/playwright'),
  use: { ...devices['Desktop Chrome'], channel: 'chrome', trace: 'retain-on-failure' },
  webServer: [
    { command: 'node node_modules/vite/bin/vite.js --mode mock --host 127.0.0.1 --port 19309 --strictPort', cwd: frontend, url: 'http://127.0.0.1:19309', reuseExistingServer: false },
    { command: 'node node_modules/vite/bin/vite.js preview --mode production --host 127.0.0.1 --port 19310 --strictPort', cwd: frontend, url: 'http://127.0.0.1:19310', reuseExistingServer: false },
  ],
});
