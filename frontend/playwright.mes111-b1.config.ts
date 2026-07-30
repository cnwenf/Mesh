import { defineConfig } from '@playwright/test';

/**
 * MES-111 批次①(登录/注册全流程 + 首页工作台)真实浏览器走查存证。
 * 复用 mock 契约栈(127.0.0.1:8901)+ dev server(127.0.0.1:5173),
 * 截图写入 e2e/evidence/mes111-b1/。桌面 1440×900 + 手机 390×844 / 320×568,亮/暗。
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: ['mes111-b1-evidence.spec.ts'],
  timeout: 90_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    headless: true,
    trace: 'retain-on-failure',
    // 稳定化截图基线:禁用动画/过渡,配合 settle()(networkidle + fonts.ready)。
    reducedMotion: 'reduce',
  },
  webServer: [
    {
      command: 'node e2e/mock-server.mjs',
      url: 'http://127.0.0.1:8901/healthz',
      reuseExistingServer: true,
      timeout: 30_000,
    },
    {
      command: 'npm run dev -- --port 5173 --strictPort --host 127.0.0.1',
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
