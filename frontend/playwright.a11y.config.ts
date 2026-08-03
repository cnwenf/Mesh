import { defineConfig } from '@playwright/test';

/**
 * MES-128 运行时 WCAG 门禁:确定性视觉 fixture + fresh production preview。
 * 两个 project 同时覆盖桌面与真实触控手机能力,零重试且不复用服务。
 */
export default defineConfig({
  testDir: './e2e/a11y',
  workers: 1,
  retries: 0,
  timeout: 90_000,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5200',
    headless: true,
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop',
      use: { viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'phone-touch',
      use: {
        viewport: { width: 390, height: 844 },
        hasTouch: true,
        isMobile: true,
      },
    },
  ],
  webServer: [
    {
      command: 'node e2e/fixtures/mock-server-visual.mjs',
      url: 'http://127.0.0.1:8912/healthz',
      reuseExistingServer: false,
      timeout: 30_000,
      env: { MESH_MOCK_VISUAL_PORT: '8912' },
    },
    {
      command: 'npm run build && npm run preview -- --port 5200 --strictPort --host 127.0.0.1',
      url: 'http://127.0.0.1:5200',
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        VITE_MESH_API_BASE_URL: 'http://127.0.0.1:8912',
        VITE_MESH_WS_BASE_URL: 'ws://127.0.0.1:8912',
      },
    },
  ],
});
