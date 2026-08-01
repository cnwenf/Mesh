import { defineConfig } from '@playwright/test';

/**
 * MES-116 remaining page families: real API + real browser reachability,
 * narrow-screen overflow pressure, and desktop/tablet/mobile × light/dark
 * evidence. The existing development stack exposes API/Gateway on 8000/8081;
 * this config starts only the current-source Vite frontend.
 */
const frontendPort = process.env.MES116_FRONTEND_PORT ?? '5206';
const apiBase = process.env.MES116_API_BASE ?? 'http://127.0.0.1:8000';
const wsBase = process.env.MES116_WS_BASE ?? 'ws://127.0.0.1:8081';

export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-mes116-page-families.spec.ts'],
  timeout: 300_000,
  expect: { timeout: 20_000 },
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    headless: true,
    launchOptions: { args: ['--disable-web-security'] },
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `npm run dev -- --port ${frontendPort} --strictPort --host 127.0.0.1`,
    url: `http://127.0.0.1:${frontendPort}`,
    reuseExistingServer: true,
    timeout: 90_000,
    env: {
      VITE_MESH_API_BASE_URL: apiBase,
      VITE_MESH_WS_BASE_URL: wsBase,
      VITE_MESH_POLLING_INTERVAL_MS: '1000',
    },
  },
});
