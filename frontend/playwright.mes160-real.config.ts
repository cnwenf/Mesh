import { defineConfig } from '@playwright/test';

const frontendPort = process.env.MES160_FRONTEND_PORT ?? '18530';

export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-mes160.spec.ts'],
  timeout: 240_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    headless: true,
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop-light',
      use: { viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'desktop-dark',
      use: { viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'phone-light',
      use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
    },
    {
      name: 'phone-dark',
      use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
    },
  ],
});
