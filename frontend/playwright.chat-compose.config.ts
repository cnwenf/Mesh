import { defineConfig } from '@playwright/test';

/**
 * Compose 真栈 /chat 冒烟配置(H5)。
 * 前置:`docker compose up --build -d` 全栈运行(含 frontend 同源反代)。
 * 无 webServer —— 直接打 compose 的 :3001(同源,无需 disable-web-security)。
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-chat-compose.spec.ts'],
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: process.env.PLAYWRIGHT_COMPOSE_BASE ?? 'http://127.0.0.1:3001',
    headless: true,
    trace: 'retain-on-failure',
  },
});
