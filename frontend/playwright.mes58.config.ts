import { defineConfig } from '@playwright/test';

// 后端栈地址可经环境覆盖(默认 compose Quick Start 端口 8000/8081)。
const API_BASE_URL = process.env.VITE_MESH_API_BASE_URL ?? 'http://127.0.0.1:8000';
const WS_BASE_URL = process.env.VITE_MESH_WS_BASE_URL ?? 'ws://127.0.0.1:8081';

/**
 * comment-inbox 真实后端联调专用配置(MES-58):
 * 前置:后端栈运行中(docker compose up postgres redis api worker gateway,
 * MESH_AUTH_MODE=dev,8000/8081)。dev server 由本配置拉起并指向真实后端。
 *
 * 与 playwright.real.config.ts 同源(同 base/同 --disable-web-security 联调约束),
 * 仅 testMatch 收口到 real-comments-inbox.spec.ts。本配置不由本任务执行(无后端环境),
 * 由编排器在真实后端栈就绪后运行。
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-comments-inbox.spec.ts', 'real-inbox-notify.spec.ts'],
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5174',
    headless: true,
    launchOptions: {
      args: ['--disable-web-security', '--no-sandbox'],
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
        VITE_MESH_API_BASE_URL: API_BASE_URL,
        VITE_MESH_WS_BASE_URL: WS_BASE_URL,
        VITE_MESH_POLLING_INTERVAL_MS: '1000',
      },
    },
  ],
});
