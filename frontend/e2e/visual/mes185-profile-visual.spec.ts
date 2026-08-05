/** MES-185 账号资料页固定视口 × 双主题视觉门禁。 */
import { expect, test } from '@playwright/test';
import { commonMasks, prepareVisualPage, waitForStable } from './visual-helpers';

const THEMES = ['light', 'dark'] as const;

for (const theme of THEMES) {
  test(`profile settings ${theme}`, async ({ page }) => {
    await prepareVisualPage(page, theme);
    await page.goto('/settings/profile');
    await page.locator('.mesh-settings-section__identity-row').waitFor({ state: 'visible' });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await waitForStable(page);

    await expect(page).toHaveScreenshot(`profile-${theme}.png`, {
      maxDiffPixelRatio: 0.01,
      mask: commonMasks(page),
      maskColor: '#000000',
    });
  });
}
