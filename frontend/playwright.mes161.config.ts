import { defineConfig } from '@playwright/test';

/**
 * MES-161 全局辅助页族真实浏览器验收：production 鉴权、真实 API 与数据库，
 * 桌面及手机双视口。复用批次④隔离栈，端口仍只绑定 127.0.0.1。
 */
const frontendPort = process.env.MES161_FRONTEND_PORT ?? '18430';

export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-mes161-global.spec.ts'],
  timeout: 120_000,
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
      name: 'desktop',
      use: { viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'mobile',
      use: {
        viewport: { width: 390, height: 844 },
        hasTouch: true,
        isMobile: true,
      },
    },
  ],
});
