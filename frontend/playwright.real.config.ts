import { defineConfig } from '@playwright/test';

/**
 * 真实后端联调专用配置(复验 MES-16 B1):
 * 前置:origin/main 后端栈运行中(docker compose up postgres redis api worker gateway,
 * MESH_AUTH_MODE=dev)。dev server 由本配置拉起并指向真实后端(8000/8081)。
 *
 * 后端 v0.1.0 未开 CORS(生产经 nginx 反代同源部署),故以 --disable-web-security
 * 启动浏览器 —— 仅联调验证用途,非产品行为。
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: 'real-backend.spec.ts',
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
      command: 'npm run dev -- --port 5174 --strictPort',
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
