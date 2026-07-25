/**
 * 成员名册真实后端联调专用配置(验收 MES-14):
 * 前置:members-global-setup.mjs 在 mesh_test 上以 mesh_app 角色(RLS 生效)拉起真实 API(8099)
 * 并播种数据;dev server(5199)指向该真实后端。后端未开 CORS(生产经 nginx 同源反代),
 * 故以 --disable-web-security 启动浏览器 —— 仅联调验证用途,非产品行为。
 */
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  testMatch: 'real-members.spec.ts',
  globalSetup: './e2e/members-global-setup.mjs',
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5199',
    headless: true,
    launchOptions: {
      args: ['--disable-web-security'],
    },
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'npm run dev -- --port 5199 --strictPort --host 127.0.0.1',
      url: 'http://127.0.0.1:5199',
      reuseExistingServer: true,
      timeout: 90_000,
      env: {
        VITE_MESH_API_BASE_URL: 'http://127.0.0.1:8099',
        VITE_MESH_WS_BASE_URL: 'ws://127.0.0.1:8099',
      },
    },
  ],
});
