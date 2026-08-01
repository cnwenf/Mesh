/**
 * 暗色视觉回归门禁(theme.md §5.4 / Task 21)。
 *
 * 固定矩阵:13 核心页 × light/dark × 390×844 / 768×1024 / 1024×768 /
 * 1440×900 = 104 个 toHaveScreenshot 用例。
 *
 * 页面注册表与导航契约与 forced-colors.spec.ts 共享(同一 `PAGES`,§5.4 同一页面集)。
 *
 * 确定性手段(详见 visual-helpers.ts):
 * - beforeAll 预热全部核心路由,消除 Vite 冷编译竞争;
 * - 冻结时钟(page.clock.install 固定 2026-07-25T12:00:00Z)→ 相对时间恒定;
 * - 偏好经 localStorage 显式注入 light/dark(不经协商链),整组切换 data-theme;
 * - 字体锁定(内置 OFL woff2 + 强制覆盖 + fonts.ready);
 * - 动态区 mask(时间戳 / presence / 头像底色 / 连接态等),遮罩清单随用例登记于 PAGES。
 *
 * 阈值:maxDiffPixelRatio 0.01 为上限,逐用例只可收紧不可放宽。
 */
import { expect, test } from '@playwright/test';
import {
  commonMasks,
  navigateToPage,
  PAGES,
  prepareVisualPage,
  waitForStable,
  warmUpPages,
} from './visual-helpers';

const THEMES = ['light', 'dark'] as const;

// 首跑预热:在测量导航前编译全部核心路由模块,杜绝冷启动偶发 ErrorBoundary。
test.beforeAll(async ({ browser }) => {
  await warmUpPages(browser);
});

for (const name of Object.keys(PAGES)) {
  const spec = PAGES[name];
  for (const theme of THEMES) {
    test(`${name} ${theme}`, async ({ page }) => {
      await prepareVisualPage(page, theme);
      await navigateToPage(page, name);

      // 主题整组切换断言:暗色用例额外校验 data-theme=dark(亮色同样校验,更严)。
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);

      await waitForStable(page);

      const mask = [...commonMasks(page), ...spec.masks(page)];
      await expect(page).toHaveScreenshot(`${spec.snapshotKey}-${theme}.png`, {
        maxDiffPixelRatio: 0.01,
        mask,
        maskColor: '#000000',
      });
    });
  }
}
