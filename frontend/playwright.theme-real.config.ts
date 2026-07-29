import { defineConfig } from '@playwright/test';

/**
 * 主题首帧链路真实 e2e(theme.md §5.1 无闪错三场景 + §2.3 注入/locator 验收)。
 *
 * 前置:同构生产形态栈运行中(nginx 前门 → API HTML 入口中间件):
 *   docker compose -p mesh81 --env-file <env> up -d --build
 *   (API 8051 / 前门 3051;env 提供强口令与 MESH_AUTH_MODE=dev)
 *
 * 注入链路经真实路径验证:mesh_session HttpOnly cookie(Playwright 以登录响应
 * 的 refresh token 植入)→ 服务端 sessions 哈希定位 → 协商链解析 →
 * __MESH_APPEARANCE__ 注入 + private,no-store + per-request nonce CSP。
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-theme.spec.ts'],
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:3051',
    headless: true,
    trace: 'retain-on-failure',
  },
});
