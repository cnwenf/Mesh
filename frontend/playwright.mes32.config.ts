import { defineConfig } from '@playwright/test';

/**
 * MES-32 label-property issue 关联层真实 UI 走查配置:前置为 MES-32 专属后端栈
 * (API 8132 / gateway 8182 + 独立测试库 mesh_ui_mes32 + redis db4,
 * MESH_AUTH_MODE=dev)。dev server 由本配置拉起(5232),指向 8132 后端。
 * 后端未开 CORS(生产经反代同源),故 --disable-web-security 仅联调用途。
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-assoc.spec.ts'],
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5232',
    headless: true,
    launchOptions: {
      args: ['--disable-web-security'],
    },
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'npm run dev -- --port 5232 --strictPort --host 127.0.0.1',
      url: 'http://127.0.0.1:5232',
      reuseExistingServer: true,
      timeout: 90_000,
      env: {
        VITE_MESH_API_BASE_URL: 'http://127.0.0.1:8132',
        VITE_MESH_WS_BASE_URL: 'ws://127.0.0.1:8182',
      },
    },
  ],
});
