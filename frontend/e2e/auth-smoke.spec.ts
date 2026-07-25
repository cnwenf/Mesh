/**
 * auth §4.1 真实浏览器冒烟(MES-38 / N5):mock 服务端提供 auth 契约路由
 * (login/register/mfa·verify/oauth start·callback),像真人一样操作登录页:
 * 邮箱/密码登录、锁定错误态、MFA 二步、注册结果态、第三方(mock)登录往返。
 */
import { expect, test } from '@playwright/test';

test.describe('auth §4.1 登录冒烟', () => {
  test('邮箱/密码登录成功 → 回首页', async ({ page }) => {
    await page.goto('/login');
    await page.getByTestId('login-email').fill('jane@corp.com');
    await page.getByTestId('login-password').fill('secret123');
    await page.getByTestId('login-account-submit').click();
    await page.waitForURL('**/');
    await expect(page.getByTestId('demo-theme')).toBeVisible();
  });

  test('账号锁定 → 具名错误文案(§6.14)', async ({ page }) => {
    await page.goto('/login');
    await page.getByTestId('login-email').fill('locked@corp.com');
    await page.getByTestId('login-password').fill('whatever');
    await page.getByTestId('login-account-submit').click();
    await expect(page.getByTestId('login-error')).toContainText('Too many failed attempts');
  });

  test('MFA 二步:质询 → 错码具名错误 → 正确码登录成功', async ({ page }) => {
    await page.goto('/login');
    await page.getByTestId('login-email').fill('mfa@corp.com');
    await page.getByTestId('login-password').fill('secret123');
    await page.getByTestId('login-account-submit').click();

    await expect(page.getByTestId('mfa-code')).toBeVisible();
    await page.getByTestId('mfa-code').fill('000000');
    await page.getByTestId('mfa-submit').click();
    await expect(page.getByTestId('login-error')).toContainText('not valid');

    await page.getByTestId('mfa-code').fill('123456');
    await page.getByTestId('mfa-submit').click();
    await page.waitForURL('**/');
    await expect(page.getByTestId('demo-theme')).toBeVisible();
  });

  test('注册成功 → 「已发验证邮件」结果页 → 继续回跳', async ({ page }) => {
    await page.goto('/login');
    await page.getByTestId('login-mode-register').click();
    await page.getByTestId('login-display-name').fill('Smoke');
    await page.getByTestId('login-email').fill('smoke@corp.com');
    await page.getByTestId('login-password').fill('secret123');
    await page.getByTestId('login-account-submit').click();

    await expect(page.getByTestId('register-verify-sent')).toContainText('smoke@corp.com');
    await page.getByTestId('register-continue').click();
    await page.waitForURL('**/');
    await expect(page.getByTestId('demo-theme')).toBeVisible();
  });

  test('第三方登录按钮组:mock 提供商全往返(§4.5 step 5,?next= 回跳原路径)', async ({
    page,
  }) => {
    await page.goto('/login?next=/settings');
    await expect(page.getByTestId('oauth-provider-mock')).toBeVisible();
    await page.getByTestId('oauth-provider-mock').click();

    // 登录页 → 后端 start 302 → mock 即刻"授权"回跳前端回调页 → 交换会话凭证 → 回跳 next
    await page.waitForURL('**/settings');
  });
});
