import { defineConfig } from '@playwright/test';

/**
 * 真实后端联调配置(MES-58 复验):后端栈端口经环境变量注入(默认 8000/8081),
 * 便于在不同端口栈(如本地 8100/8181)上复验评论/收件箱 UI。dev server 由本配置
 * 拉起并指向真实后端;--disable-web-security 仅联调用途(后端未开 CORS)。
 */
const apiBase = process.env.REAL_API_BASE_URL ?? 'http://127.0.0.1:8000';
const wsBase = process.env.REAL_WS_BASE_URL ?? 'ws://127.0.0.1:8081';

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
        VITE_MESH_API_BASE_URL: apiBase,
        VITE_MESH_WS_BASE_URL: wsBase,
        VITE_MESH_DEMO_CHANNEL: 'workspace:11111111-1111-1111-1111-111111111111:issues',
        VITE_MESH_POLLING_INTERVAL_MS: '1000',
      },
    },
  ],
});
