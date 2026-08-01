/**
 * forced-colors 仿真验收(theme.md §5.4 / §4.3 评审 T1):
 * Playwright emulateMedia({forcedColors:'active'}) 覆盖核心页矩阵,断言
 * ① 语义 token 重映射系统色(Canvas/CanvasText/Highlight/GrayText/LinkText);
 * ② 结构边界经显式 border 表达(box-shadow 失效不破坏层级辨识);
 * ③ 自证对比区保留 forced-color-adjust 声明点。
 * 真机核对清单(Windows 高对比/对比主题,Edge)见 .github/pull_request_template.md。
 */
import { expect, test } from '@playwright/test';
import { PAGES, prepareVisualPage } from './visual-helpers';

test.describe('forced-colors 仿真(§5.4 验收)', () => {
  for (const [name, visualPage] of Object.entries(PAGES)) {
    for (const theme of ['light', 'dark'] as const) {
      test(`核心页「${name}」(${theme}) forced-colors 系统色重映射`, async ({ page }) => {
        await page.emulateMedia({ forcedColors: 'active' });
        await prepareVisualPage(page, theme);
        await page.goto(visualPage.path, { waitUntil: 'load' });
        await visualPage.ready(page);

        // ① 语义 token 重映射为系统色关键字(两套主题收敛到同一系统色板)。
        const tokens = await page.evaluate(() => {
          const style = getComputedStyle(document.documentElement);
          return {
            bg: style.getPropertyValue('--color-bg').trim(),
            surface: style.getPropertyValue('--color-surface').trim(),
            text: style.getPropertyValue('--color-text').trim(),
            muted: style.getPropertyValue('--color-text-muted').trim(),
            border: style.getPropertyValue('--color-border').trim(),
            focus: style.getPropertyValue('--color-focus-ring').trim(),
            primary: style.getPropertyValue('--color-primary').trim(),
            selectionBg: style.getPropertyValue('--color-selection-bg').trim(),
          };
        });
        expect(tokens.bg).toBe('Canvas');
        expect(tokens.surface).toBe('Canvas');
        expect(tokens.text).toBe('CanvasText');
        expect(tokens.muted).toBe('GrayText');
        expect(tokens.border).toBe('CanvasText');
        expect(tokens.focus).toBe('Highlight');
        expect(tokens.primary).toBe('LinkText');
        expect(tokens.selectionBg).toBe('Highlight');
      });
    }
  }

  test('forced-colors 规则集:raised 表面显式 border + 自证对比区声明点', async ({ page }) => {
    await page.emulateMedia({ forcedColors: 'active' });
    await prepareVisualPage(page, 'light');
    await page.goto('/board', { waitUntil: 'load' });
    await PAGES['看板'].ready(page);

    // ②/③ 样式规则集中存在:forced-colors 块内 .mesh-dialog 显式 border、
    // .mesh-forced-colors-keep 的 forced-color-adjust: none 声明。
    const rules = await page.evaluate(() => {
      let dialogBorder = false;
      let keepAdjust = false;
      for (const sheet of Array.from(document.styleSheets)) {
        let cssRules: CSSRuleList;
        try {
          cssRules = sheet.cssRules;
        } catch {
          continue;
        }
        for (const rule of Array.from(cssRules)) {
          const text = rule.cssText;
          if (!text.includes('forced-colors: active')) continue;
          if (text.includes('mesh-dialog') && text.includes('border')) dialogBorder = true;
          if (text.includes('mesh-forced-colors-keep') && text.includes('forced-color-adjust')) {
            keepAdjust = true;
          }
        }
      }
      return { dialogBorder, keepAdjust };
    });
    expect(rules.dialogBorder).toBe(true);
    expect(rules.keepAdjust).toBe(true);
  });
});
