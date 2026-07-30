import { defineConfig } from '@playwright/test';

/**
 * 暗色视觉回归门禁(theme.md §5.4 / Task 21)。
 *
 * 固定矩阵:6 核心页(看板 / issue 详情 / 成员 / 聊天 / 运行详情 / 收件箱)×
 * light/dark × 桌面 1024×768 / 平板 768×1024 = 24 个 toHaveScreenshot 用例
 * (两个 project 提供视口维度,spec 内 12 个 page×theme 用例)。
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
 * mock 走 e2e/fixtures/mock-server-visual.mjs(mock-server.mjs 的 fork + 六页恒定
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
    baseURL: 'http://127.0.0.1:5199',
    headless: true,
    locale: 'zh-CN',
    timezoneId: 'UTC',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop',
      use: { viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'tablet',
      use: { viewport: { width: 768, height: 1024 } },
    },
    // MES-115 视觉矩阵扩充:1440 宽屏与 390 手机仅拍摄组件 fixture 页
    // (核心页矩阵维持 desktop/tablet 双视口基线,不随扩充翻倍)。
    {
      name: 'wide',
      use: { viewport: { width: 1440, height: 900 } },
      testMatch: /styleguide\.spec\.ts/,
    },
    {
      name: 'phone',
      use: { viewport: { width: 390, height: 844 } },
      testMatch: /styleguide\.spec\.ts/,
    },
  ],
  webServer: [
    {
      // 视觉专用 mock(8911):mock-server.mjs fork + 六页恒定 fixture + 字体分发。
      command: 'node e2e/fixtures/mock-server-visual.mjs',
      url: 'http://127.0.0.1:8911/healthz',
      reuseExistingServer: true,
      timeout: 30_000,
    },
    {
      // vite dev(5199),经 VITE_MESH_* 指向视觉 mock(8911)。
      // --host 127.0.0.1 显式绑 IPv4,规避 CI 上 localhost→::1 解析导致就绪探测超时。
      command: 'npm run dev -- --port 5199 --strictPort --host 127.0.0.1',
      url: 'http://127.0.0.1:5199',
      reuseExistingServer: true,
      timeout: 90_000,
      env: {
        VITE_MESH_API_BASE_URL: 'http://127.0.0.1:8911',
        VITE_MESH_WS_BASE_URL: 'ws://127.0.0.1:8911',
      },
    },
  ],
});
