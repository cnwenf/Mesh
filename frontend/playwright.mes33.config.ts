import { defineConfig } from '@playwright/test';

/**
 * MES-33 看板投影层真实 UI 走查配置:前置为 MES-33 专属后端栈
 * (API 8100 + gateway 8181 + worker + 独立测试库 mesh_mes33 + redis db4,
 * MESH_AUTH_MODE=dev)。dev server 已在 5274 运行(reuseExistingServer),指向 8100/8181。
 * 后端未开 CORS(生产经反代同源),故 --disable-web-security 仅联调用途。
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-board-projection.spec.ts'],
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5274',
    headless: true,
    launchOptions: {
      args: ['--disable-web-security'],
    },
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'npm run dev -- --port 5274 --strictPort --host 127.0.0.1',
      url: 'http://127.0.0.1:5274',
      reuseExistingServer: true,
      timeout: 90_000,
      env: {
        VITE_MESH_API_BASE_URL: 'http://127.0.0.1:8100',
        VITE_MESH_WS_BASE_URL: 'ws://127.0.0.1:8181',
      },
    },
  ],
});
