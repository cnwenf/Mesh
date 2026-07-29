/**
 * 统计报表真实后端联调专用配置(MES-71 验收):
 * 前置:analytics-global-setup.mjs 在目标库上以 mesh_app 角色(RLS 生效)
 * 拉起真实 API(默认 8123)并播种统计源数据;dev server(默认 5231)指向
 * 该真实后端。后端未开 CORS(生产经 nginx 同源反代),故以
 * --disable-web-security 启动浏览器 —— 仅联调验证用途,非产品行为。
 */
import { defineConfig } from '@playwright/test';

const API_PORT = Number(process.env.MESH_E2E_API_PORT ?? 8123);
const WEB_PORT = Number(process.env.MESH_E2E_WEB_PORT ?? 5231);

export default defineConfig({
  testDir: './e2e',
  testMatch: 'real-analytics.spec.ts',
  globalSetup: './e2e/analytics-global-setup.mjs',
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    headless: true,
    launchOptions: {
      args: ['--disable-web-security'],
    },
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: `npm run dev -- --port ${WEB_PORT} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: true,
      timeout: 90_000,
      env: {
        VITE_MESH_API_BASE_URL: `http://127.0.0.1:${API_PORT}`,
        VITE_MESH_WS_BASE_URL: `ws://127.0.0.1:${API_PORT}`,
      },
    },
  ],
});
