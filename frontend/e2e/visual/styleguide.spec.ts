/**
 * 组件状态 fixture 视觉回归(design-quality §13.5 / MES-115 视觉回归基础)。
 *
 * /styleguide 平铺全部基础组件与状态矩阵,静态确定性内容(无时间戳/随机量),
 * 跨 4 视口项目(desktop 1024×768 / tablet 768×1024 / wide 1440×900 / phone 390×844)
 * × light/dark 拍摄整页基线:组件换肤、令牌调整、排版变更在此显形。
 *
 * 确定性手段沿用视觉基建:显式主题偏好注入(整组 data-theme 切换)、锁定字体
 * (内置 woff2 强制覆盖 + document.fonts.ready)、animations: 'disabled'(config 上限)。
 */
import { expect, test } from '@playwright/test';
import { applyFonts, injectSession } from './visual-helpers';

const THEMES = ['light', 'dark'] as const;

for (const theme of THEMES) {
  test(`组件 fixture 全状态矩阵 ${theme}`, async ({ page }) => {
    await injectSession(page, theme);
    await page.goto('/styleguide', { waitUntil: 'domcontentloaded' });
    // 主题整组切换断言(亮/暗各自校准的 token 组生效,§5.2)
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Mesh 设计系统');
    await applyFonts(page);
    await expect(page).toHaveScreenshot(`styleguide-${theme}.png`, {
      fullPage: true,
      maxDiffPixelRatio: 0.01,
    });
  });
}
