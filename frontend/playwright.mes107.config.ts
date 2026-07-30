import { defineConfig, devices } from '@playwright/test';

/**
 * MES-107 真实 e2e 专用配置:production 鉴权 + 公网 HTTP,桌面 + 手机端双视口。
 *
 * 前置:隔离验收栈运行中(仓库根目录):
 *   ./frontend/e2e/mes107/gen-stack-env.sh
 *   docker compose -p mes107 \
 *     -f docker-compose.yml -f frontend/e2e/mes107/compose.override.yml \
 *     --env-file frontend/e2e/mes107/stack.env up -d --build
 * 然后(frontend/ 目录):
 *   npx playwright test --config playwright.mes107.config.ts
 *
 * 前端经 Dockerfile 以空 VITE_MESH_* 同源构建(与生产同形):API 走相对 /api,
 * WS 由页面 location 派生绝对 ws://。两个 project 复跑同一 spec:
 * desktop(1280×800)与 mobile(Pixel 7,issue 的手机端实测路径)。
 */
const frontendPort = process.env.MES107_FRONTEND_PORT ?? '18330';

export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-mes107-home.spec.ts'],
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    headless: true,
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop',
      use: { viewport: { width: 1280, height: 800 } },
    },
    {
      name: 'mobile',
      use: { ...devices['Pixel 7'] },
    },
  ],
});
