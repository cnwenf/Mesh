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

/**
 * 鉴权豁免端点(与 src/api/unauthorized.ts 的 AUTH_EXEMPT_PATHS 同义):
 * 这些端点的 401 是业务错误(如登录凭证错),不属「会话失效」信号。
 * 收窄的过滤(M1 验收教训:只滤 /workspaces 与 /me,漏掉真实端点
 * /users/me)曾让匿名 shell 的受保护请求逃过断言。
 */
const AUTH_EXEMPT_API_PATHS: readonly string[] = [
  '/api/v1/auth/login',
  '/api/v1/auth/register',
  '/api/v1/auth/mfa/verify',
  '/api/v1/auth/forgot-password',
  '/api/v1/auth/reset-password',
  '/api/v1/auth/verify-email',
  '/api/v1/auth/oauth/',
];

/** 判定 401 响应 URL 是否来自受保护 API(全部 /api/ 路径 - 鉴权豁免小集合) */
function isProtectedApi401(url: string): boolean {
  const pathname = new URL(url).pathname;
  if (!pathname.startsWith('/api/')) return false;
  return !AUTH_EXEMPT_API_PATHS.some((exempt) =>
    exempt.endsWith('/') ? pathname.startsWith(exempt) : pathname === exempt,
  );
}

/** 收集页面全部 401 响应 URL(断言侧经 isProtectedApi401 收敛) */
function collect401(page: Page): string[] {
  const urls: string[] = [];
  page.on('response', (response) => {
    if (response.status() === 401) urls.push(response.url());
  });
  return urls;
}

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
    const unauthorizedUrls = collect401(page);

    await page.goto('/');
    await page.waitForURL(/\/login\?next=/);
    expect(new URL(page.url()).pathname).toBe('/login');
    expect(new URL(page.url()).searchParams.get('next')).toBe('/');
    await expect(page.getByTestId('login-email')).toBeVisible();
    await expectNoLoadFailure(page);
    // 守卫在请求前拦截:全部受保护端点(含 /users/me、/workspaces 与 shell
    // 挂载的收件箱铃铛 / 上手清单解析)根本未被调用(不再有 401 风暴)
    expect(unauthorizedUrls.filter(isProtectedApi401)).toEqual([]);
  });

  test('未登录访问公开邀请预览页 → 可达且无受保护请求(M1 回归)', async ({ page }) => {
    const unauthorizedUrls = collect401(page);

    await page.goto('/invite/invtk_nonexistent');
    // 预览恒 200;不存在的令牌按 not_found 同形呈现(公开路由,守卫不拦)
    await expect(page.getByTestId('invite-reason-not_found')).toBeVisible();
    await expectNoLoadFailure(page);
    expect(unauthorizedUrls.filter(isProtectedApi401)).toEqual([]);
  });

  test('未登录访问深层受保护路径 → next 携带原路径(含查询串)', async ({ page }) => {
    await page.goto('/issues?focus=1');
    await page.waitForURL(/\/login\?next=/);
    expect(new URL(page.url()).searchParams.get('next')).toBe('/issues?focus=1');
    await expect(page.getByTestId('login-email')).toBeVisible();
  });

  test('登录后回跳原页面,内容正常加载(无加载失败、无 401)', async ({ page }) => {
    const unauthorizedUrls = collect401(page);
    await registerAndContinue(page, uniqueEmail('back'), '/settings/profile');
    await page.waitForURL((url) => new URL(url).pathname === '/settings/profile');
    await expect(page.getByLabel('Name')).toBeVisible();
    await expectNoLoadFailure(page);
    // 登录态全程无受保护端点 401(会话凭证有效)
    expect(unauthorizedUrls.filter(isProtectedApi401)).toEqual([]);
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
