import { defineConfig } from '@playwright/test';

const mockPort = Number(process.env.MESH_MOCK_VISUAL_PORT ?? 8911);
const frontendPort = Number(process.env.MESH_VISUAL_FRONTEND_PORT ?? 5199);
const mockBaseUrl = `http://127.0.0.1:${mockPort}`;
const frontendBaseUrl = `http://127.0.0.1:${frontendPort}`;

/**
 * 暗色视觉回归门禁(theme.md §5.4 / Task 21)。
 *
 * 固定矩阵:design-quality §13.5 的 13 核心页 ×
 * light/dark × 390×844 / 768×1024 / 1024×768 / 1440×900 = 104 个
 * toHaveScreenshot 用例(四个 project 提供视口维度)。
 *
 * 确定性优先(详见 spec 内注释):
 * - 冻结时钟(page.clock.install 固定时间戳)→ 相对时间恒定;
 * - locale zh-CN / timezoneId UTC;
 * - 字体锁定:e2e/fixtures/fonts/ 内置 OFL woff2,经 @font-face 注入 +
 *   `* { font-family: 'Mesh Visual Font' }` 强制覆盖 + document.fonts.ready 等待,
 *   杜绝字体回退抖动;
 * - 动态区(时间戳 / presence / 头像底色)以 locator mask 排除(随用例登记);
 * - toHaveScreenshot({ maxDiffPixelRatio: 0.01 }) 为上限,逐用例只可收紧不可放宽。
 *
 * 独立端口(8911 / 5199):避免与默认 mock 套件(8901 / 5173)在共享环境冲突。
 * mock 走 e2e/fixtures/mock-server-visual.mjs(mock-server.mjs 的 fork + 核心页恒定
 * fixture + 内置字体分发),不触碰默认套件对 mock-server.mjs 的既有消费。
 */
export default defineConfig({
  testDir: './e2e/visual',
  workers: 1,
  retries: 0,
  timeout: 90_000,
  // 基线快照随项目名(视口)区分,避免桌面/平板同名覆盖。
  snapshotPathTemplate: '{testDir}/{testFileDir}/{testFileName}-snapshots/{projectName}/{arg}{ext}',
  reporter: [['list']],
  expect: {
    // 上限阈值;逐用例可在 toHaveScreenshot 内收紧,绝不可放宽。
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.01,
      animations: 'disabled',
      caret: 'hide',
    },
  },
  use: {
    baseURL: frontendBaseUrl,
    headless: true,
    locale: 'zh-CN',
    timezoneId: 'UTC',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop',
      testIgnore: /state-matrix\.spec\.ts/,
      use: { viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'tablet',
      testIgnore: /state-matrix\.spec\.ts/,
      use: { viewport: { width: 768, height: 1024 } },
    },
    {
      name: 'wide',
      testIgnore: /state-matrix\.spec\.ts/,
      use: { viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'phone',
      testIgnore: /state-matrix\.spec\.ts/,
      use: { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true },
    },
    {
      name: 'state-phone',
      testMatch: /state-matrix\.spec\.ts/,
      use: { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true },
    },
  ],
  webServer: [
    {
      // 视觉专用 mock(8911):mock-server.mjs fork + 核心页恒定 fixture + 字体分发。
      command: 'node e2e/fixtures/mock-server-visual.mjs',
      url: `${mockBaseUrl}/healthz`,
      reuseExistingServer: false,
      timeout: 30_000,
      env: { MESH_MOCK_VISUAL_PORT: String(mockPort) },
    },
    {
      // 每次门禁新建生产包并启动 preview,不复用 dev/HMR 模块图。
      command: `npm run build && npm run preview -- --port ${frontendPort} --strictPort --host 127.0.0.1`,
      url: frontendBaseUrl,
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        VITE_MESH_API_BASE_URL: mockBaseUrl,
        VITE_MESH_WS_BASE_URL: `ws://127.0.0.1:${mockPort}`,
      },
    },
  ],
});
