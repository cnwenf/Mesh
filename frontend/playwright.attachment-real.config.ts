import { defineConfig } from '@playwright/test';

/**
 * 附件模块真实后端浏览器走查专用配置(MES-59 §5 验收):
 * 前置:真实后端栈运行中(api / worker / gateway / MinIO / PostgreSQL,
 * MESH_AUTH_MODE=dev)。端口经环境变量参数化:CI 用默认值(8000/8081/5199);
 * 多栈共存的联调机可覆盖避免端口争夺(与 playwright.real.config.ts 的 5174
 * 隔离,不与其他走查相互踩踏)。
 *
 * 后端未开 CORS(生产经 nginx 反代同源部署),故以 --disable-web-security
 * 启动浏览器 —— 仅联调验证用途,非产品行为(同 playwright.real.config.ts 口径)。
 */
const API_BASE = process.env.MESH_E2E_API_BASE_URL ?? 'http://127.0.0.1:8000';
const WS_BASE = process.env.MESH_E2E_WS_BASE_URL ?? 'ws://127.0.0.1:8081';
const DEV_PORT = process.env.MESH_E2E_FRONTEND_PORT ?? '5199';

export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-attachment.spec.ts'],
  timeout: 300_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${DEV_PORT}`,
    headless: true,
    launchOptions: {
      args: ['--disable-web-security'],
    },
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: `npm run dev -- --port ${DEV_PORT} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${DEV_PORT}`,
      reuseExistingServer: true,
      timeout: 90_000,
      env: {
        VITE_MESH_API_BASE_URL: API_BASE,
        VITE_MESH_WS_BASE_URL: WS_BASE,
        VITE_MESH_POLLING_INTERVAL_MS: '1000',
      },
    },
  ],
});
