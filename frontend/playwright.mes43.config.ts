import { defineConfig } from '@playwright/test';

/**
 * MES-43 看板视图定义层真实 UI 走查配置:前置为 MES-43 专属后端栈
 * (API 8100 + 独立测试库 mesh_test_mes43 + redis db3,MESH_AUTH_MODE=dev)。
 * dev server 已在 5273 运行(reuseExistingServer),指向 8100 后端。
 * 后端未开 CORS(生产经反代同源),故 --disable-web-security 仅联调用途。
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-board.spec.ts'],
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5273',
    headless: true,
    launchOptions: {
      args: ['--disable-web-security'],
    },
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'npm run dev -- --port 5273 --strictPort --host 127.0.0.1',
      url: 'http://127.0.0.1:5273',
      reuseExistingServer: true,
      timeout: 90_000,
      env: {
        VITE_MESH_API_BASE_URL: 'http://127.0.0.1:8100',
        VITE_MESH_WS_BASE_URL: 'ws://127.0.0.1:8181',
      },
    },
  ],
});
