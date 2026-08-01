import { defineConfig } from '@playwright/test';

/**
 * 真实浏览器 e2e(MES-16 验收):
 * - mock-server.mjs 提供符合 README §6.14 包络与 §6.7 实时契约的 HTTP/WS 服务端(127.0.0.1:8901)
 * - 生产构建预览提供前端(127.0.0.1:5173),经 VITE_MESH_* 指向 mock 服务端
 */
export default defineConfig({
  testDir: './e2e',
  // 真实后端联调用例走独立配置(playwright.real.config.ts / playwright.members.config.ts /
  // playwright.mes43.config.ts / playwright.mes44.config.ts),不对 mock 运行
  // 真实后端 spec 需各自专用 config(独立后端栈)运行,默认 mock 契约套件须排除。
  // 真实后端 spec 一律以 real-*.spec.ts 命名(glob 兜底,杜绝新增漏配);
  // workspace-flow.spec.ts 为历史命名特例,显式列出。
  testIgnore: [
    'real-*.spec.ts',
    'workspace-flow.spec.ts',
    'visual/**',
    'a11y/**',
    'mes111-b2.spec.ts',
  ],
  timeout: 90_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    headless: true,
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'node e2e/mock-server.mjs',
      url: 'http://127.0.0.1:8901/healthz',
      // 套件只使用本次拉起的确定性服务,不复用环境未知的本地进程。
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      // mock 门禁验证一次性生产构建。不使用 Vite dev/HMR 的源码模块图,
      // 避免导航时的大量模块请求受浏览器网络状态切换影响而部分启动。
      command: 'npm run serve:e2e',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        VITE_MESH_API_BASE_URL: 'http://127.0.0.1:8901',
        VITE_MESH_WS_BASE_URL: 'ws://127.0.0.1:8901',
        // 生产构建默认不开启任何 OAuth 提供商;契约套件显式开启内置 mock 往返。
        VITE_MESH_OAUTH_PROVIDERS: 'mock',
      },
    },
  ],
});
