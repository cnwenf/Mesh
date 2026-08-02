/**
 * MES-79 真实栈 e2e 配置:搜索/命令面板/规范深链/快捷键走查。
 *
 * 前置:真实后端栈运行中(docker compose up -d,API 在 MESH79_API_PORT,
 * 默认 8300;网关 MESH79_WS_PORT,默认 8281),镜像含 MES-79 全量改动。
 * dev server 由本配置拉起(5199)并指向该栈(跨域,--disable-web-security)。
 */
import { defineConfig } from '@playwright/test';

const API_PORT = process.env.MES79_API_PORT ?? '8300';
const WS_PORT = process.env.MES79_WS_PORT ?? '8281';
const DEV_PORT = 5199;

export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-mes79-search.spec.ts'],
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${DEV_PORT}`,
    headless: true,
    launchOptions: { args: ['--disable-web-security'] },
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: `npm run dev -- --port ${DEV_PORT} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${DEV_PORT}`,
      reuseExistingServer: true,
      timeout: 90_000,
      env: {
        VITE_MESH_API_BASE_URL: `http://127.0.0.1:${API_PORT}`,
        VITE_MESH_WS_BASE_URL: `ws://127.0.0.1:${WS_PORT}`,
        VITE_MESH_POLLING_INTERVAL_MS: '1000',
      },
    },
  ],
});
