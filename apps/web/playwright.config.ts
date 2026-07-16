import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../..', import.meta.url));
const webPort = process.env.LOOP_DEMO_WEB_PORT ?? '13080';
const apiPort = process.env.LOOP_DEMO_API_PORT ?? '18080';
const baseURL = `http://localhost:${webPort}`;

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? 'list' : 'line',
  use: {
    baseURL,
    browserName: 'chromium',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'bash scripts/demo.sh',
    cwd: root,
    env: {
      ...process.env,
      LOOP_DEMO_API_PORT: apiPort,
      LOOP_DEMO_WEB_PORT: webPort,
      LOOP_DEMO_OPEN: '0',
      LOOP_DEMO_SKIP_INSTALL: '1',
    },
    url: `${baseURL}/api/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: 'pipe',
    stderr: 'pipe',
    gracefulShutdown: { signal: 'SIGTERM', timeout: 5_000 },
  },
});
