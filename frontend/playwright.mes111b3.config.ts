/**
 * MES-111 批次③ 真实 e2e + 四组合走查存证配置。
 * 前置:`docker compose -p mes126b3 up -d --build postgres redis minio api worker gateway frontend`
 * (MESH_API_PORT=18126 / MESH_WS_PORT=18127 / MESH_STORAGE_PORT=18128 /
 *  MESH_FRONTEND_PORT=18130 / MESH_STORAGE_PUBLIC_ENDPOINT=http://127.0.0.1:18128)。
 * 前端经 nginx 在 :18130 同源承载并反代 /api→api、/ws→gateway,浏览器全程同源
 * (SSE 流式经 stream_url 相对路径直连,同 compose 部署形态,无需 --disable-web-security;
 *  这正是真实部署形态的验收口径,同 playwright.chat-compose.config.ts)。
 * spec 内 API 播种经 MESH_E2E_API_BASE 直打 api(:18126)。
 */
import { defineConfig } from '@playwright/test';

const BASE = process.env.MESH_E2E_BASE ?? 'http://127.0.0.1:18130';

export default defineConfig({
  testDir: './e2e',
  testMatch: 'mes111-b3-evidence.spec.ts',
  timeout: 300_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: BASE,
    headless: true,
    // 主走查经 compose 同源(:18130),无需跨域;附件直传步另开 dev server 页面
    // (:5326 → 真实后端 :18126,后端未开 CORS),故浏览器整体以
    // --disable-web-security 启动 —— 仅联调验证用途,非产品行为
    // (同 playwright.members.config.ts / playwright.attachment-real.config.ts 口径)。
    launchOptions: {
      args: ['--disable-web-security'],
    },
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop-light',
      use: { viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'desktop-dark',
      use: { viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'phone-light',
      use: { viewport: { width: 390, height: 844 } },
    },
    {
      name: 'phone-dark',
      use: { viewport: { width: 390, height: 844 } },
    },
  ],
});
