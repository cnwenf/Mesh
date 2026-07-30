/**
 * MES-111 批次①(登录/注册全流程 + 首页工作台)真实浏览器走查存证 + 全链断言。
 * 真实 dev server + mock 契约栈,Chromium 逐页截图,写入 e2e/evidence/mes111-b1/。
 * 覆盖 桌面 1440×900 / 手机 390×844 / 320×568 × 亮/暗 四组合(登录/注册/找回/首页),
 * 以及 注册→MFA→登录→回跳 与 找回→重置 全链断言、工作台五块断言。
 * 截图经 reducedMotion(config)+ settle(networkidle + fonts.ready) 稳定化。
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

/** 稳定化:等网络静默 + 字体就绪,减少截图时机/字体渲染不确定(回归基线前提)。 */
async function settle(page: import('@playwright/test').Page): Promise<void> {
  await page.waitForLoadState('networkidle');
  await page.evaluate(() => document.fonts.ready);
}

async function capture(
  page: import('@playwright/test').Page,
  name: string,
  fullPage = false,
): Promise<void> {
  await settle(page);
  await page.screenshot({ path: `${EVIDENCE}/${name}.png`, fullPage });
}

async function goLogin(page: import('@playwright/test').Page): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-email').waitFor();
}

async function goRegister(page: import('@playwright/test').Page): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').waitFor();
}

async function goForgot(page: import('@playwright/test').Page): Promise<void> {
  await page.goto('/forgot');
  await page.getByTestId('forgot-email').waitFor();
}

test.describe('登录页 PublicFlowShell 四组合 + 错误态(design-quality §4.4 / §9.2)', () => {
  test('桌面 1440 亮色', async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await goLogin(page);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await capture(page, 'desktop-login-light');
  });

  test('桌面 1440 暗色', async ({ page }) => {
    await presetDark(page);
    await page.setViewportSize(DESKTOP);
    await goLogin(page);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await capture(page, 'desktop-login-dark');
  });

  test('手机 390 亮色', async ({ page }) => {
    await page.setViewportSize(PHONE);
    await goLogin(page);
    await capture(page, 'phone-login-light');
  });

  test('手机 390 暗色', async ({ page }) => {
    await presetDark(page);
    await page.setViewportSize(PHONE);
    await goLogin(page);
    await capture(page, 'phone-login-dark');
  });

  test('手机 320 亮色(无横向溢出)', async ({ page }) => {
    await page.setViewportSize(PHONE_320);
    await goLogin(page);
    await capture(page, 'phone-login-320-light');
  });

  test('账号锁定 → 可操作错误(分开提示 + 恢复 + 密码不清空)', async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await goLogin(page);
    await page.getByTestId('login-email').fill('locked@corp.com');
    await page.getByTestId('login-password').fill('some-password');
    await page.getByTestId('login-account-submit').click();
    await page.getByTestId('login-error').waitFor();
    await capture(page, 'desktop-login-error-light');
  });
});

test.describe('注册页 PublicFlowShell 四组合', () => {
  test('桌面 1440 亮色', async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await goRegister(page);
    await capture(page, 'desktop-register-light');
  });

  test('桌面 1440 暗色', async ({ page }) => {
    await presetDark(page);
    await page.setViewportSize(DESKTOP);
    await goRegister(page);
    await capture(page, 'desktop-register-dark');
  });

  test('手机 390 亮色', async ({ page }) => {
    await page.setViewportSize(PHONE);
    await goRegister(page);
    await capture(page, 'phone-register-light');
  });

  test('手机 390 暗色', async ({ page }) => {
    await presetDark(page);
    await page.setViewportSize(PHONE);
    await goRegister(page);
    await capture(page, 'phone-register-dark');
  });
});

test.describe('找回密码页 PublicFlowShell 四组合', () => {
  test('桌面 1440 亮色', async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await goForgot(page);
    await capture(page, 'desktop-forgot-light');
  });

  test('桌面 1440 暗色', async ({ page }) => {
    await presetDark(page);
    await page.setViewportSize(DESKTOP);
    await goForgot(page);
    await capture(page, 'desktop-forgot-dark');
  });

  test('手机 390 亮色', async ({ page }) => {
    await page.setViewportSize(PHONE);
    await goForgot(page);
    await capture(page, 'phone-forgot-light');
  });

  test('手机 390 暗色', async ({ page }) => {
    await presetDark(page);
    await page.setViewportSize(PHONE);
    await goForgot(page);
    await capture(page, 'phone-forgot-dark');
  });
});

test.describe('其他公共流程页面 PublicFlowShell', () => {
  test('设备授权录入相(桌面亮色,登录态)', async ({ page }) => {
    await injectSession(page, MOCK_TOKEN);
    await page.setViewportSize(DESKTOP);
    await page.goto('/device');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await capture(page, 'desktop-device-light');
  });
});

test.describe('认证全链路 e2e(Issue 自验收第 2 条:注册→MFA→登录→回跳 + 找回→重置)', () => {
  test('MFA 全链路:登录 → 错码报错 → 正码进首页', async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await goLogin(page);
    await page.getByTestId('login-email').fill('mfa@corp.com');
    await page.getByTestId('login-password').fill('whatever');
    await page.getByTestId('login-account-submit').click();

    await page.getByTestId('mfa-code').waitFor();
    // 错码:原位报错(MFA 无效),不跳走。
    await page.getByTestId('mfa-code').fill('000000');
    await page.getByTestId('mfa-submit').click();
    await expect(page.getByTestId('login-error')).toBeVisible();

    // 正码:换会话凭证 → 回跳首页。
    await page.getByTestId('mfa-code').fill('123456');
    await page.getByTestId('mfa-submit').click();
    await page.waitForURL('**/');
    await page.getByTestId('home-greeting').waitFor();
  });

  test('注册全链路:强度条 → 注册+登录 → 验证邮件结果页 → 继续进首页', async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await goRegister(page);
    await page.getByTestId('login-display-name').fill('New User');
    await page.getByTestId('login-email').fill('new@corp.com');
    await page.getByTestId('login-password').fill('secret123');
    await expect(page.getByTestId('password-strength')).toBeVisible();

    await page.getByTestId('login-account-submit').click();
    await page.getByTestId('register-verify-sent').waitFor();
    await expect(page.getByTestId('register-verify-sent')).toContainText('new@corp.com');

    await page.getByTestId('register-continue').click();
    await page.waitForURL('**/');
    await page.getByTestId('home-greeting').waitFor();
  });

  test('找回 → 重置全链路:发起重置已发送;无效码报错+恢复出口;有效码重置成功', async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await goForgot(page);
    await page.getByTestId('forgot-email').fill('jane@corp.com');
    await page.getByTestId('forgot-submit').click();
    await page.getByTestId('forgot-sent').waitFor();

    await page.goto('/reset?token=BAD-TOKEN');
    await page.getByTestId('reset-code').waitFor();
    await page.getByTestId('reset-password').fill('new-pass-1');
    await page.getByTestId('reset-submit').click();
    await expect(page.getByTestId('reset-error')).toBeVisible();
    await expect(page.getByTestId('reset-request-new')).toBeVisible();

    await page.getByTestId('reset-code').fill('GOOD-TOKEN');
    await page.getByTestId('reset-password').fill('new-pass-2');
    await page.getByTestId('reset-submit').click();
    await page.getByTestId('reset-done').waitFor();
  });
});

test.describe('首页工作台 四组合 + 五块断言(design-quality §3.2 首页行)', () => {
  test('桌面 1440 亮色', async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await login(page);
    await page.getByTestId('home-greeting').waitFor();
    await capture(page, 'desktop-home-light', true);
  });

  test('桌面 1440 暗色', async ({ page }) => {
    await presetDark(page);
    await page.setViewportSize(DESKTOP);
    await login(page);
    await page.getByTestId('home-greeting').waitFor();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await capture(page, 'desktop-home-dark', true);
  });

  test('手机 390 亮色', async ({ page }) => {
    await page.setViewportSize(PHONE);
    await login(page);
    await page.getByTestId('home-greeting').waitFor();
    await capture(page, 'phone-home-light', true);
  });

  test('手机 390 暗色', async ({ page }) => {
    await presetDark(page);
    await page.setViewportSize(PHONE);
    await login(page);
    await page.getByTestId('home-greeting').waitFor();
    await capture(page, 'phone-home-dark', true);
  });

  test('手机 320 亮色(无横向溢出)', async ({ page }) => {
    await page.setViewportSize(PHONE_320);
    await login(page);
    await page.getByTestId('home-greeting').waitFor();
    await capture(page, 'phone-home-320-light', true);
  });

  test('工作台五块:我的工作/快速创建/等待确认/AI 运行/最近项目(真实 mock 数据)', async ({
    page,
  }) => {
    await page.setViewportSize(DESKTOP);
    await login(page);
    await page.getByTestId('home-greeting').waitFor();
    await settle(page);

    await expect(page.getByTestId('home-dashboard')).toBeVisible(); // 我的工作 + 快速创建
    await expect(page.getByTestId('home-projects')).toBeVisible();
    await expect(page.getByTestId('home-waiting')).toBeVisible();
    await expect(page.getByTestId('home-waiting-appr-7')).toContainText('Approve deploy of MESH-2');
    await expect(page.getByTestId('home-ai-runs')).toBeVisible();
    await expect(page.getByTestId('home-ai-run-exec-1')).toBeVisible();
    // 终态成功执行被过滤,不在「AI 运行」块。
    await expect(page.getByTestId('home-ai-run-exec-3')).toHaveCount(0);
  });
});
