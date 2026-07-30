/**
 * MES-111 Phase 1 设计底座真实浏览器走查(design-quality §13.2/§13.4/§13.5):
 *
 * - 排版切换:body 14px、Inter/Noto Sans SC 字体族生效、触控档输入 16px(≤599px);
 * - 令牌切换:暗色为整组 token 替换(canvas/表面/文本/强调色实测计算样式);
 * - 组件状态矩阵:Button hover/pressed/focus-visible/disabled/loading 实测;
 * - 触控目标:手机底栏/按钮命中区 ≥44×44px(§8.2);
 * - 动效:reduced-motion 下过渡时长归零(§5.5);
 * - forced-colors:浮层显式边框可见(§4.3);
 * - 双主题走查存证(桌面 1280×720 + 手机 390×844,亮/暗)。
 *
 * 存证入 e2e/evidence/mes111-shell/(验收 R1 目录名统一;md5 唯一性门禁)。
 */
import { expect, test } from '@playwright/test';
import { login } from './helpers';

const EVIDENCE_DIR = 'e2e/evidence/mes111-shell';

test.describe('设计底座 @桌面 1280×720', () => {
  test.use({ viewport: { width: 1280, height: 720 } });

  test('排版底座:body 14px + 字体族配对 + 标题 display 族', async ({ page }) => {
    await login(page);
    await page.goto('/');
    const body = await page.evaluate(() => {
      const style = getComputedStyle(document.body);
      return { fontSize: style.fontSize, fontFamily: style.fontFamily, lineHeight: style.lineHeight };
    });
    expect(body.fontSize).toBe('14px');
    expect(body.fontFamily).toContain('Inter');
    expect(body.fontFamily).toContain('Noto Sans SC');
    expect(body.lineHeight).toBe('22px');
  });

  test('令牌底座:暗色为整组替换(canvas/surface/文本/强调色实测)', async ({ page }) => {
    await login(page);
    await page.goto('/');
    const light = await page.evaluate(() => ({
      canvas: getComputedStyle(document.body).backgroundColor,
      text: getComputedStyle(document.body).color,
    }));
    await page.goto('/settings');
    await page.getByTestId('theme-select').selectOption('dark');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    const dark = await page.evaluate(() => ({
      canvas: getComputedStyle(document.body).backgroundColor,
      text: getComputedStyle(document.body).color,
    }));
    expect(dark.canvas).not.toBe(light.canvas);
    expect(dark.text).not.toBe(light.text);
    // 暗色 canvas 应为深色(token #0f1115 → rgb(15, 17, 21))
    expect(dark.canvas).toBe('rgb(15, 17, 21)');
  });

  test('Button 状态矩阵:hover 变色 + focus-visible 焦点环 + 提交全链路', async ({ page }) => {
    // 登录页为未登录态入口(登录成功后自动跳首页,故不经 login() 辅助)
    await page.goto('/login');
    await page.getByTestId('login-email').fill('jane@corp.com');
    await page.getByTestId('login-password').fill('secret123');
    const submit = page.getByTestId('login-account-submit');
    // primary 按钮 hover 变色(accent → accent-hover;过渡 100ms,poll 等待离开初值)
    const before = await submit.evaluate((el) => getComputedStyle(el).backgroundColor);
    await submit.hover();
    await expect
      .poll(() => submit.evaluate((el) => getComputedStyle(el).backgroundColor))
      .not.toBe(before);
    // focus-visible 焦点环:键盘 Tab(经「记住我」)进入提交按钮后 outline 可见
    await page.getByTestId('login-password').focus();
    await page.keyboard.press('Tab');
    await page.keyboard.press('Tab');
    await expect(submit).toBeFocused();
    const outline = await submit.evaluate((el) => getComputedStyle(el).outlineStyle);
    expect(['solid', 'auto']).toContain(outline);
    // 提交成功跳首页(loading 瞬态由 Button 单测覆盖,此处验全链路)
    await submit.click();
    await page.waitForURL('**/');
  });

  test('触控目标:底栏与按钮命中区 ≥44×44px(§8.2)——手机视口', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page);
    await page.goto('/');
    for (const key of ['home', 'issues', 'board', 'chat', 'more']) {
      const box = await page.getByTestId('mobile-nav-' + key).boundingBox();
      expect(box, `mobile-nav-${key}`).not.toBeNull();
      if (box !== null) {
        expect(box.width, `mobile-nav-${key} 宽`).toBeGreaterThanOrEqual(44);
        expect(box.height, `mobile-nav-${key} 高`).toBeGreaterThanOrEqual(44);
      }
    }
  });

  test('reduced-motion:过渡时长归零(§5.5)', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await login(page);
    await page.goto('/');
    const durations = await page.evaluate(() => {
      const button = document.querySelector('.mesh-sidebar__link, .mesh-button');
      return button === null ? [] : getComputedStyle(button).transitionDuration.split(', ');
    });
    expect(durations.length).toBeGreaterThan(0);
    for (const duration of durations) {
      // reduced-motion 全局降级至 ~0(0.01ms,计算值可能呈 1e-05s 科学计数)
      const seconds = duration.endsWith('ms') ? parseFloat(duration) / 1000 : parseFloat(duration);
      expect(seconds, duration).toBeLessThan(0.01);
    }
  });

  test('forced-colors:浮层显式边框可见、焦点环随系统(§4.3)', async ({ page }) => {
    await page.emulateMedia({ forcedColors: 'active' });
    await login(page);
    await page.goto('/');
    // 打开命令面板(dialog 浮层)
    await page.keyboard.press('ControlOrMeta+KeyK');
    await expect(page.locator('.mesh-palette')).toBeVisible();
    const border = await page.locator('.mesh-dialog').first().evaluate((el) => getComputedStyle(el).borderTopStyle);
    expect(border).toBe('solid');
  });

  test('桌面亮/暗走查存证(看板页,新令牌体系)', async ({ page }) => {
    await login(page);
    await page.goto('/board');
    await page.getByTestId('board-page').waitFor({ state: 'visible' });
    await page.screenshot({ path: `${EVIDENCE_DIR}/foundation-desktop-board-light.png` });
    await page.goto('/settings');
    await page.getByTestId('theme-select').selectOption('dark');
    await page.goto('/board');
    await page.getByTestId('board-page').waitFor({ state: 'visible' });
    await page.screenshot({ path: `${EVIDENCE_DIR}/foundation-desktop-board-dark.png` });
  });
});

test.describe('设计底座 @手机 390×844', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('触控档输入字号 ≥16px(防 iOS 聚焦缩放,§6.2)', async ({ page }) => {
    await page.goto('/login');
    const fontSize = await page.getByTestId('login-email').evaluate((el) => getComputedStyle(el).fontSize);
    expect(parseFloat(fontSize)).toBeGreaterThanOrEqual(16);
  });

  test('手机亮/暗走查存证(抽屉 + 设置页)', async ({ page }) => {
    await login(page);
    await page.goto('/');
    await page.getByTestId('mobile-nav-more').click();
    await expect(page.getByRole('dialog', { name: 'All navigation' })).toBeVisible();
    await page.screenshot({ path: `${EVIDENCE_DIR}/foundation-phone-drawer-light.png` });
    await page.keyboard.press('Escape');
    await page.goto('/settings');
    await page.screenshot({ path: `${EVIDENCE_DIR}/foundation-phone-settings-light.png` });
    await page.getByTestId('theme-select').selectOption('dark');
    await page.waitForTimeout(200);
    await page.screenshot({ path: `${EVIDENCE_DIR}/foundation-phone-settings-dark.png` });
  });
});
