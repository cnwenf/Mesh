import { defineConfig } from '@playwright/test';

/**
 * MES-111 批次④ 真实 e2e 配置:设置 + 搜索/命令面板 + Analytics + /approvals。
 * production 鉴权 + 公网 HTTP,桌面(1440×900)+ 手机(390×844)双视口。
 *
 * 前置:隔离验收栈运行中(仓库根目录):
 *   ./frontend/e2e/mes111-b4/gen-stack-env.sh
 *   docker compose -p mes111-b4 \
 *     -f docker-compose.yml -f frontend/e2e/mes111-b4/compose.override.yml \
 *     --env-file frontend/e2e/mes111-b4/stack.env up -d --build
 * 然后(frontend/ 目录):
 *   npx playwright test --config playwright.mes111-b4.config.ts
 *
 * 前端经 Dockerfile 以空 VITE_MESH_* 同源构建(与生产同形):API 走相对 /api,
 * WS 由页面 location 派生绝对 ws://。两个 project 复跑同一 spec:
 * desktop(1440×900)与 mobile(390×844,触控)。
 */
const frontendPort = process.env.MES111_B4_FRONTEND_PORT ?? '18350';

export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-mes111-b4.spec.ts'],
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
      use: { viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'mobile',
      use: {
        viewport: { width: 390, height: 844 },
        hasTouch: true,
        isMobile: true,
      },
    },
  ],
});
