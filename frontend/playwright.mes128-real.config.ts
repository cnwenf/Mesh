import { defineConfig } from '@playwright/test';

/**
 * MES-128 同源 Compose 真栈键盘旅程。
 *
 * `mes128-real/run-e2e.sh` 拉起 production 鉴权的 api/worker/gateway/frontend、
 * 内网 PostgreSQL/Redis/MinIO；浏览器只访问 nginx 同源入口。桌面与两档窄屏
 * project 都完整复跑登录→建 issue→非拖拽移卡→评论→切工作区→搜索，并由
 * spec 直查容器内 PostgreSQL 验证落库。
 */
const frontendPort = process.env.MES128_FRONTEND_PORT ?? '18430';

export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-mes128-keyboard.spec.ts', 'real-mes159-projects.spec.ts'],
  timeout: 240_000,
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
      name: 'desktop-1440',
      use: { viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'phone-320',
      use: { viewport: { width: 320, height: 720 }, isMobile: true, hasTouch: true },
    },
    {
      name: 'phone-390',
      use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
    },
  ],
});
