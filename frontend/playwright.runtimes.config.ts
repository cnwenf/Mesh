import { defineConfig } from '@playwright/test';

/**
 * Runtimes 模块真实后端联调专用配置(runtime.md §4 验收):
 * 前置:后端栈运行中(docker compose up postgres redis api worker gateway,
 * MESH_AUTH_MODE=dev)。dev server 由本配置拉起并指向真实后端(8000/8081)。
 *
 * 与 playwright.real.config.ts 同形:workers=1 串行、--disable-web-security
 * 仅为绕过后端 v0.1.0 未开 CORS 的联调限制,非产品行为。
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-runtimes.spec.ts'],
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5174',
    headless: true,
    launchOptions: {
      args: ['--disable-web-security'],
    },
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'npm run dev -- --port 5174 --strictPort --host 127.0.0.1',
      url: 'http://127.0.0.1:5174',
      reuseExistingServer: true,
      timeout: 90_000,
      env: {
        VITE_MESH_API_BASE_URL: 'http://127.0.0.1:8000',
        VITE_MESH_WS_BASE_URL: 'ws://127.0.0.1:8081',
        VITE_MESH_DEMO_CHANNEL: 'workspace:11111111-1111-1111-1111-111111111111:issues',
        VITE_MESH_POLLING_INTERVAL_MS: '1000',
      },
    },
  ],
});
