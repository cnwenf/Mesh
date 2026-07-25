import { defineConfig } from '@playwright/test';

/**
 * 真实浏览器 e2e(MES-16 验收):
 * - mock-server.mjs 提供符合 README §6.14 包络与 §6.7 实时契约的 HTTP/WS 服务端(127.0.0.1:8901)
 * - dev server 提供前端(127.0.0.1:5173),经 VITE_MESH_* 指向 mock 服务端
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    headless: true,
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'node e2e/mock-server.mjs',
      url: 'http://127.0.0.1:8901/healthz',
      reuseExistingServer: true,
      timeout: 30_000,
    },
    {
      command: 'npm run dev -- --port 5173 --strictPort',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: true,
      timeout: 90_000,
      env: {
        VITE_MESH_API_BASE_URL: 'http://127.0.0.1:8901',
        VITE_MESH_WS_BASE_URL: 'ws://127.0.0.1:8901',
      },
    },
  ],
});
