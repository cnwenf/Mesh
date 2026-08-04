import { defineConfig } from '@playwright/test';

const externalBaseUrl = process.env.MES130_BASE_URL;

/**
 * MES-130 二维看板真实浏览器验收。后端使用隔离的 PostgreSQL/Redis，浏览器
 * 仅连接回环端口；前端开发服务因后端无 CORS 而在此专用配置中关闭跨源限制。
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-board-swimlanes.spec.ts'],
  timeout: 150_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  outputDir: 'test-results/mes130-swimlanes-artifacts',
  use: {
    baseURL: externalBaseUrl ?? 'http://127.0.0.1:5274',
    headless: true,
    launchOptions: externalBaseUrl === undefined ? { args: ['--disable-web-security'] } : undefined,
    trace: 'retain-on-failure',
  },
  webServer:
    externalBaseUrl === undefined
      ? {
          command: 'npm run dev -- --port 5274 --strictPort --host 127.0.0.1',
          url: 'http://127.0.0.1:5274',
          reuseExistingServer: false,
          timeout: 90_000,
          env: {
            VITE_MESH_API_BASE_URL: 'http://127.0.0.1:8100',
            VITE_MESH_WS_BASE_URL: 'ws://127.0.0.1:8181',
          },
        }
      : undefined,
});
