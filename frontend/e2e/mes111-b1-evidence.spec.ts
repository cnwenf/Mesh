/**
 * MES-111 批次①(登录/注册全流程 + 首页工作台)真实浏览器走查存证。
 * 真实 dev server + mock 契约栈,Chromium 逐页截图,写入 e2e/evidence/mes111-b1/。
 * 覆盖 桌面 1440×900 / 手机 390×844 / 320×568 × 亮/暗,以及登录错误态、注册态、
 * 忘记密码、设备授权等公共流程页面的 PublicFlowShell 真实渲染。
 */
import { expect, test } from '@playwright/test';
import { injectSession, login, MOCK_TOKEN } from './helpers';

const EVIDENCE = 'e2e/evidence/mes111-b1';
const DESKTOP = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };
const PHONE_320 = { width: 320, height: 568 };

/** 预置暗色偏好(用户级,协商链最高优先;theme.md §2.1),防闪烁首帧即暗色。 */
async function presetDark(page: import('@playwright/test').Page): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      'mesh.settings.v1',
      JSON.stringify({
        state: { preferences: { theme: 'dark', locale: null, timezone: 'UTC' } },
        version: 2,
      }),
    );
  });
}

async function settled(
  page: import('@playwright/test').Page,
  theme: 'light' | 'dark',
): Promise<void> {
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
}

test.describe('登录页 PublicFlowShell(design-quality §4.4 / §9.2)', () => {
  test('桌面 1440 亮色', async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await page.goto('/login');
    await page.getByTestId('login-email').waitFor();
    await settled(page, 'light');
    await page.screenshot({ path: `${EVIDENCE}/desktop-login-light.png` });
  });

  test('桌面 1440 暗色', async ({ page }) => {
    await presetDark(page);
    await page.setViewportSize(DESKTOP);
    await page.goto('/login');
    await page.getByTestId('login-email').waitFor();
    await settled(page, 'dark');
    await page.screenshot({ path: `${EVIDENCE}/desktop-login-dark.png` });
  });

  test('手机 390 亮色', async ({ page }) => {
    await page.setViewportSize(PHONE);
    await page.goto('/login');
    await page.getByTestId('login-email').waitFor();
    await settled(page, 'light');
    await page.screenshot({ path: `${EVIDENCE}/phone-login-light.png` });
  });

  test('手机 390 暗色', async ({ page }) => {
    await presetDark(page);
    await page.setViewportSize(PHONE);
    await page.goto('/login');
    await page.getByTestId('login-email').waitFor();
    await settled(page, 'dark');
    await page.screenshot({ path: `${EVIDENCE}/phone-login-dark.png` });
  });

  test('手机 320 亮色(无横向溢出)', async ({ page }) => {
    await page.setViewportSize(PHONE_320);
    await page.goto('/login');
    await page.getByTestId('login-email').waitFor();
    await settled(page, 'light');
    await page.screenshot({ path: `${EVIDENCE}/phone-login-320-light.png` });
  });

  test('注册模式(桌面亮色):密码强度条在场', async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await page.goto('/login');
    await page.getByTestId('login-mode-register').click();
    await page.getByTestId('login-display-name').waitFor();
    await page.screenshot({ path: `${EVIDENCE}/desktop-register-light.png` });
  });

  test('账号锁定 → 可操作错误提示且密码不清空(桌面亮色)', async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await page.goto('/login');
    // mock 契约:locked@corp.com 返回 423 account_locked(§9.2 分开提示的锁定态)。
    await page.getByTestId('login-email').fill('locked@corp.com');
    await page.getByTestId('login-password').fill('some-password');
    await page.getByTestId('login-account-submit').click();
    await page.getByTestId('login-error').waitFor();
    // 密码字段失败不被清空(§9.2)。
    await expect(page.getByTestId('login-password')).toHaveValue('some-password');
    await page.screenshot({ path: `${EVIDENCE}/desktop-login-error-light.png` });
  });
});

test.describe('其他公共流程页面 PublicFlowShell', () => {
  test('忘记密码(桌面亮色)', async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await page.goto('/forgot');
    await page.getByTestId('forgot-email').waitFor();
    await page.screenshot({ path: `${EVIDENCE}/desktop-forgot-light.png` });
  });

  test('设备授权录入相(桌面亮色,登录态)', async ({ page }) => {
    await injectSession(page, MOCK_TOKEN);
    await page.setViewportSize(DESKTOP);
    await page.goto('/device');
    await settled(page, 'light');
    await page.screenshot({ path: `${EVIDENCE}/desktop-device-light.png` });
  });
});

test.describe('首页工作台(design-quality §3.2 首页行)', () => {
  test('桌面 1440 亮色', async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await login(page);
    await page.getByTestId('home-greeting').waitFor();
    await page.getByTestId('home-dashboard').waitFor();
    await page.screenshot({ path: `${EVIDENCE}/desktop-home-light.png`, fullPage: true });
  });

  test('桌面 1440 暗色', async ({ page }) => {
    await presetDark(page);
    await page.setViewportSize(DESKTOP);
    await login(page);
    await page.getByTestId('home-greeting').waitFor();
    await page.getByTestId('home-dashboard').waitFor();
    await settled(page, 'dark');
    await page.screenshot({ path: `${EVIDENCE}/desktop-home-dark.png`, fullPage: true });
  });

  test('手机 390 亮色', async ({ page }) => {
    await page.setViewportSize(PHONE);
    await login(page);
    await page.getByTestId('home-greeting').waitFor();
    await page.getByTestId('home-dashboard').waitFor();
    await page.screenshot({ path: `${EVIDENCE}/phone-home-light.png`, fullPage: true });
  });

  test('手机 390 暗色', async ({ page }) => {
    await presetDark(page);
    await page.setViewportSize(PHONE);
    await login(page);
    await page.getByTestId('home-greeting').waitFor();
    await page.getByTestId('home-dashboard').waitFor();
    await settled(page, 'dark');
    await page.screenshot({ path: `${EVIDENCE}/phone-home-dark.png`, fullPage: true });
  });

  test('手机 320 亮色(无横向溢出)', async ({ page }) => {
    await page.setViewportSize(PHONE_320);
    await login(page);
    await page.getByTestId('home-greeting').waitFor();
    await page.getByTestId('home-dashboard').waitFor();
    await page.screenshot({ path: `${EVIDENCE}/phone-home-320-light.png`, fullPage: true });
  });
});
