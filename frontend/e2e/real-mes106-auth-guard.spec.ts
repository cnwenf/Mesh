/**
 * MES-106 真实 e2e — production 鉴权 + 公网 HTTP(手机端宽度,Pixel 7 视口)。
 *
 * 验收项(issue 逐条对应):
 * 1. 未登录访问首页(及受保护页)→ 自动跳 /login?next=<原路径>,不发起受保护
 *    请求(不再满屏「内容加载失败」);
 * 2. 登录(注册即登录)后回跳原页面,内容正常加载,无加载失败;
 * 3. token 失效(篡改 access → 受保护端点 401)→ 全局跳登录页;
 * 4. WebSocket 在公网 HTTP 下正常连接(绝对 ws:// 地址 + 首帧 auth_ok)。
 *
 * 前置:mes106 隔离栈运行中(playwright.mes106.config.ts 头部注释)。
 */
import { expect, test } from '@playwright/test';
import type { Page, WebSocket } from '@playwright/test';

const FRONTEND_PORT = process.env.MES106_FRONTEND_PORT ?? '18310';
const PASSWORD = 'Mesh-Demo#2026x';
const LOAD_FAILED_TEXT = 'We could not load this content. Please try again.';

/** 每用例唯一邮箱(注册即登录;同邮箱重注会 409) */
function uniqueEmail(suffix: string): string {
  return `mes106-${suffix}-${String(process.pid)}@example.com`;
}

/** 注册新账号并经「已发验证邮件」结果页继续(生产模式注册自动登录) */
async function registerAndContinue(page: Page, email: string, next?: string): Promise<void> {
  await page.goto(next !== undefined ? `/login?next=${encodeURIComponent(next)}` : '/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').fill('MES-106 E2E');
  await page.getByTestId('login-email').fill(email);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  await expect(page.getByTestId('register-verify-sent')).toContainText(email);
  await page.getByTestId('register-continue').click();
}

/** 断言:页面无「加载失败」错误态文案 */
async function expectNoLoadFailure(page: Page): Promise<void> {
  await expect(page.getByText(LOAD_FAILED_TEXT)).toHaveCount(0);
}

test.describe('MES-106 登录守卫 / 401 兜底 / WS 公网 HTTP', () => {
  test('未登录访问首页 → 自动跳 /login 并携带 next,不发起受保护请求', async ({ page }) => {
    const unauthorizedUrls: string[] = [];
    page.on('response', (response) => {
      if (response.status() === 401) unauthorizedUrls.push(response.url());
    });

    await page.goto('/');
    await page.waitForURL(/\/login\?next=/);
    expect(new URL(page.url()).pathname).toBe('/login');
    expect(new URL(page.url()).searchParams.get('next')).toBe('/');
    await expect(page.getByTestId('login-email')).toBeVisible();
    await expectNoLoadFailure(page);
    // 守卫在请求前拦截:受保护端点根本未被调用(不再有 401 风暴)
    expect(
      unauthorizedUrls.filter(
        (url) => url.includes('/api/v1/workspaces') || url.includes('/api/v1/me'),
      ),
    ).toEqual([]);
  });

  test('未登录访问深层受保护路径 → next 携带原路径(含查询串)', async ({ page }) => {
    await page.goto('/issues?focus=1');
    await page.waitForURL(/\/login\?next=/);
    expect(new URL(page.url()).searchParams.get('next')).toBe('/issues?focus=1');
    await expect(page.getByTestId('login-email')).toBeVisible();
  });

  test('登录后回跳原页面,内容正常加载(无加载失败、无 401)', async ({ page }) => {
    await registerAndContinue(page, uniqueEmail('back'), '/settings');
    await page.waitForURL((url) => new URL(url).pathname === '/settings');
    await expect(page.getByTestId('theme-select')).toBeVisible();
    await expectNoLoadFailure(page);
  });

  test('已登录访问 /login → 回跳首页(避免重复登录)', async ({ page }) => {
    await registerAndContinue(page, uniqueEmail('relogin'));
    await page.waitForURL((url) => new URL(url).pathname === '/');
    await page.goto('/login');
    await page.waitForURL((url) => new URL(url).pathname === '/');
    await expect(page.getByText('Welcome to Mesh')).toBeVisible();
  });

  test('token 失效(401)→ 全局跳登录页', async ({ page }) => {
    await registerAndContinue(page, uniqueEmail('expired'));
    await page.waitForURL((url) => new URL(url).pathname === '/');
    // 篡改本地 access token 模拟过期:受保护端点将回 401 → 全局兜底
    await page.evaluate(() => {
      const raw = localStorage.getItem('mesh.auth.v1');
      if (raw === null) throw new Error('auth store missing');
      const parsed = JSON.parse(raw) as { state?: { token?: string } };
      parsed.state = { ...parsed.state, token: 'tampered.invalid.token' };
      localStorage.setItem('mesh.auth.v1', JSON.stringify(parsed));
    });
    await page.goto('/');
    await page.waitForURL(/\/login\?next=/);
    expect(new URL(page.url()).pathname).toBe('/login');
    expect(new URL(page.url()).searchParams.get('next')).toBe('/');
    await expect(page.getByTestId('login-email')).toBeVisible();
  });

  test('WebSocket 公网 HTTP 正常连接(绝对 ws:// + 首帧 auth_ok)', async ({ page }) => {
    const wsPromise = page.waitForEvent('websocket');
    await registerAndContinue(page, uniqueEmail('ws'));
    const ws: WebSocket = await wsPromise;

    // 修复点:相对 '/ws' 会被 WebSocket 构造器拒绝;同源部署须派生绝对地址
    expect(ws.url()).toBe(`ws://127.0.0.1:${FRONTEND_PORT}/ws`);

    // 首帧认证(§6.16):客户端发 {op:auth},服务端回 auth_ok
    const authOk = await new Promise<boolean>((resolve) => {
      const timer = setTimeout(() => resolve(false), 30_000);
      ws.on('framereceived', (frame) => {
        const text = typeof frame.payload === 'string' ? frame.payload : '';
        if (text.includes('auth_ok')) {
          clearTimeout(timer);
          resolve(true);
        }
      });
      ws.on('close', () => {
        clearTimeout(timer);
        resolve(false);
      });
    });
    expect(authOk).toBe(true);
    expect(ws.isClosed()).toBe(false);
  });
});
