/**
 * MES-45 真实 UI 实操取证:命令面板搜索修复(Playwright + chromium 真实浏览器)。
 *
 * 像真人一样操作:Ctrl+K 打开命令面板 → 输入关键词 `issues` / `home` →
 * 选项实时命中 → Enter 跳转。截图落 e2e/evidence/ 供 Issue 附件。
 * 命令面板为纯前端逻辑,本用例对 mock 契约服务端即可运行,
 * 随默认 e2e 配置常态守护(兼取证)。
 */
import { expect, test } from '@playwright/test';

test('MES-45 取证:Ctrl+K 输入 issues/home 均命中且 Enter 可跳转', async ({ page }) => {
  await page.goto('/');
  await page.keyboard.press('Control+k');
  const palette = page.getByRole('dialog', { name: 'Command palette' });
  const input = palette.getByRole('combobox');
  await expect(palette).toBeVisible();

  // 关键词 issues:命中 Issues 导航命令(上一轮此处 label undefined → 全瘫)
  await input.fill('issues');
  await expect(palette.getByRole('option', { name: 'Issues' })).toBeVisible();
  await page.screenshot({ path: 'e2e/evidence/mes45-palette-search-issues.png' });

  // Enter 跳转 /issues
  await page.keyboard.press('Enter');
  await page.waitForURL('**/issues');
  await expect(page.locator('main')).toBeVisible();
  await page.screenshot({ path: 'e2e/evidence/mes45-palette-nav-issues.png' });

  // 关键词 home:同样命中(任意关键词不再塌成 0 条)
  await page.keyboard.press('Control+k');
  await expect(palette).toBeVisible();
  await input.fill('home');
  await expect(palette.getByRole('option').first()).toBeVisible();
  await page.screenshot({ path: 'e2e/evidence/mes45-palette-search-home.png' });
});
