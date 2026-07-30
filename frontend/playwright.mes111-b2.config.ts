import { defineConfig } from '@playwright/test';

/**
 * MES-111 批次②真实后端浏览器走查(design-quality.md §13 / MES-125 自验收 #2/#3):
 * 看板拖拽(鼠标 + 键盘双路径)、快速建卡、List 布局、评论(草稿/失败重试/撤销)、
 * 附件上传失败重试——真实 api/gateway/worker + PostgreSQL + MinIO 全栈,
 * 桌面 1440×900 与手机 390×844 两档,亮/暗主题,截图存证 e2e/evidence/mes111-b2/。
 *
 * 前置:本地后端栈运行中(api 8020 / gateway 8091;mes125-* 容器),
 * MESH_AUTH_MODE=dev。dev server 由本配置拉起并指向真实后端。
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: ['mes111-b2.spec.ts'],
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5210',
    headless: true,
    launchOptions: {
      // 后端 v0.1.0 未开 CORS(生产经 nginx 反代同源部署),联调用 --disable-web-security。
      args: ['--disable-web-security'],
    },
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 } } },
    {
      name: 'mobile',
      use: {
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
  webServer: [
    {
      command: 'npm run dev -- --port 5210 --strictPort --host 127.0.0.1',
      url: 'http://127.0.0.1:5210',
      reuseExistingServer: true,
      timeout: 90_000,
      env: {
        VITE_MESH_API_BASE_URL: 'http://127.0.0.1:8020',
        VITE_MESH_WS_BASE_URL: 'ws://127.0.0.1:8091',
        VITE_MESH_POLLING_INTERVAL_MS: '1000',
      },
    },
  ],
});
