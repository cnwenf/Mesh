import { defineConfig } from '@playwright/test';

/**
 * Autopilot 模块真实后端浏览器走查专用配置(autopilot.md §4 验收):
 * 前置:后端栈运行中(docker compose up postgres redis minio api worker
 * gateway,MESH_AUTH_MODE=dev,worker 含 autopilot scheduler/executor 循环)。
 * dev server 由本配置拉起并指向真实后端。
 *
 * 与 playwright.runtimes.config.ts 同形:workers=1 串行、--disable-web-security
 * 仅为绕过后端未开 CORS 的联调限制,非产品行为。
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-autopilots.spec.ts'],
  timeout: 180_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5197',
    headless: true,
    launchOptions: {
      args: ['--disable-web-security'],
    },
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'npm run dev -- --port 5197 --strictPort --host 127.0.0.1',
      url: 'http://127.0.0.1:5197',
      reuseExistingServer: true,
      timeout: 90_000,
      env: {
        VITE_MESH_API_BASE_URL: process.env.AUTOPILOTS_API_BASE ?? 'http://127.0.0.1:8160',
        VITE_MESH_WS_BASE_URL: process.env.AUTOPILOTS_WS_BASE ?? 'ws://127.0.0.1:8161',
        VITE_MESH_POLLING_INTERVAL_MS: '1000',
      },
    },
  ],
});
