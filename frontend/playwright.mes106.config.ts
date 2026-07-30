import { defineConfig, devices } from '@playwright/test';

/**
 * MES-106 真实 e2e 专用配置:production 鉴权 + 公网 HTTP 体验(手机端宽度)。
 *
 * 前置:隔离验收栈运行中(仓库根目录):
 *   ./frontend/e2e/mes106/gen-stack-env.sh
 *   docker compose -p mes106 \
 *     -f docker-compose.yml -f frontend/e2e/mes106/compose.override.yml \
 *     --env-file frontend/e2e/mes106/stack.env up -d --build
 * 然后(frontend/ 目录):
 *   MES106_FRONTEND_PORT=18310 npx playwright test --config playwright.mes106.config.ts
 *
 * 前端经 Dockerfile 以空 VITE_MESH_* 同源构建(与生产同形):API 走相对 /api,
 * WS 由页面 location 派生绝对 ws://(MES-106 修复点)。视口取 Pixel 7,
 * 复现 issue 的手机端实测路径。
 */
const frontendPort = process.env.MES106_FRONTEND_PORT ?? '18310';

export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-mes106-auth-guard.spec.ts'],
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    headless: true,
    trace: 'retain-on-failure',
    // 手机端(issue 复现路径):Pixel 7 视口 / UA / 触摸
    ...devices['Pixel 7'],
  },
});
