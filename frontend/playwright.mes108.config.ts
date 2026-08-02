import { defineConfig } from '@playwright/test';

/**
 * MES-108 release evidence runs only through this deterministic configuration.
 * Evidence specs freeze the model-card timestamp and load the vendored font
 * fixture before they exercise a declared interaction or capture a screenshot.
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
    browserName: 'chromium',
    deviceScaleFactor: 1,
    headless: true,
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
});
