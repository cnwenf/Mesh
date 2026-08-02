import { defineConfig } from '@playwright/test';

const apiBaseUrl = process.env.MES108_API_BASE ?? 'http://127.0.0.1:8000';
const wsBaseUrl = process.env.MES108_WS_BASE ?? 'ws://127.0.0.1:8081';
const webPort = process.env.MES108_WEB_PORT ?? '5175';
const webBaseUrl = `http://127.0.0.1:${webPort}`;

/**
 * MES-108 real-browser and release-evidence runs use this deterministic config.
 * The Vite server points at a real API/WS stack; evidence specs additionally
 * freeze the model-card timestamp and load the vendored font fixture before
 * they exercise a declared interaction or capture a screenshot.
 *
 * The backend does not enable CORS because production is same-origin behind the
 * reverse proxy. The Chromium web-security override is restricted to this local
 * cross-origin integration harness and does not change product behavior.
 */
export default defineConfig({
  testDir: './e2e',
  forbidOnly: true,
  fullyParallel: false,
  retries: 0,
  workers: 1,
  timeout: 90_000,
  reporter: [['list']],
  expect: {
    toHaveScreenshot: {
      animations: 'disabled',
      caret: 'hide',
    },
  },
  use: {
    baseURL: webBaseUrl,
    browserName: 'chromium',
    deviceScaleFactor: 1,
    headless: true,
    launchOptions: {
      args: ['--disable-web-security'],
    },
    locale: 'zh-CN',
    timezoneId: 'UTC',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'phone',
      use: { viewport: { width: 390, height: 844 } },
    },
    {
      name: 'wide',
      use: { viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: {
    command: `npm run dev -- --port ${webPort} --strictPort --host 127.0.0.1`,
    url: webBaseUrl,
    reuseExistingServer: false,
    timeout: 90_000,
    env: {
      VITE_MESH_API_BASE_URL: apiBaseUrl,
      VITE_MESH_WS_BASE_URL: wsBaseUrl,
      VITE_MESH_POLLING_INTERVAL_MS: '1000',
    },
  },
});
