/**
 * 主题首帧链路真实 e2e(theme.md §5.1 / §5.3 / §2.3)。
 *
 * 验收矩阵:
 * ① 已登录正常导航命中注入链路(入口 HTML 含 __MESH_APPEARANCE__ 且与协商结果一致);
 * ② A(默认暗)→ B(默认浅)切换首帧即 B 主题,无「先暗后浅」闪错;
 * ③ 换账号:残留 locator(id 不符)不被读取,不串用上一账号主题;
 * ④ 邀请接受页(未登录)首帧即邀请工作区默认主题(preview 同源注入);
 * ⑤ locator 白名单/分区校验(非法 mode、id 不符 → skeleton → 协商后正确);
 * ⑥ 缓存边界:匿名 shell public + sha256 CSP;个性化 private,no-store + nonce CSP;
 *    注入值仅二值,不含工作区标识等可枚举信息。
 */
import { expect, test } from '@playwright/test';

const BASE = 'http://127.0.0.1:3051';
const PASSWORD = 'a-strong-passw0rd';

let seq = 0;
function uniqueSuffix(): string {
  seq += 1;
  return `${Date.now().toString(36)}${seq}`;
}

async function api(
  path: string,
  options: { method?: string; body?: unknown; token?: string } = {},
): Promise<{ status: number; data: Record<string, unknown> }> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (options.token !== undefined) headers.authorization = `Bearer ${options.token}`;
  const resp = await fetch(`${BASE}${path}`, {
    method: options.method ?? 'GET',
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const json = (await resp.json()) as { data: Record<string, unknown> };
  return { status: resp.status, data: json.data };
}

async function registerLogin(email: string): Promise<{ access: string; refresh: string }> {
  await api('/api/v1/auth/register', {
    method: 'POST',
    body: { email, password: PASSWORD, display_name: email.split('@')[0] },
  });
  const login = await api('/api/v1/auth/login', {
    method: 'POST',
    body: { email, password: PASSWORD },
  });
  return {
    access: login.data.access_token as string,
    refresh: login.data.refresh_token as string,
  };
}

async function createWorkspace(
  access: string,
  slug: string,
  defaultTheme: 'light' | 'dark' | 'system',
): Promise<string> {
  const created = await api('/api/v1/workspaces', {
    method: 'POST',
    token: access,
    body: { name: `Theme WS ${slug}`, slug, settings: { default_theme: defaultTheme } },
  });
  return created.data.id as string;
}

async function plantSession(
  context: import('@playwright/test').BrowserContext,
  tokens: { access: string; refresh: string },
): Promise<void> {
  // HttpOnly 会话 cookie(入口中间件读取)+ SPA Bearer 存取(localStorage,
  // 应用 API 调用读取)——两者同源一致方为完整登录态。
  await context.addCookies([{ name: 'mesh_session', value: tokens.refresh, url: BASE }]);
  await context.addInitScript((access: string) => {
    window.localStorage.setItem(
      'mesh.auth.v1',
      JSON.stringify({ state: { token: access, refreshToken: null }, version: 0 }),
    );
  }, tokens.access);
}

test.describe('theme.md §5.1 无闪错三场景 + 注入链路(真实栈)', () => {
  test('① 已登录导航命中注入:入口 HTML 含与协商一致的 __MESH_APPEARANCE__(dark)', async ({ browser }) => {
    const sfx = uniqueSuffix();
    const { access, refresh } = await registerLogin(`theme-inject-${sfx}@corp.com`);
    await createWorkspace(access, `theme-inject-${sfx}`, 'dark');
    await api('/api/v1/users/me', {
      method: 'PATCH',
      token: access,
      body: { settings: { theme: null } }, // absent → 继承工作区默认(第 2 级)
    });

    const context = await browser.newContext();
    await plantSession(context, { access, refresh });
    const page = await context.newPage();
    const response = await page.goto(`/w/theme-inject-${sfx}`, { waitUntil: 'domcontentloaded' });
    expect(response?.status()).toBe(200);
    const html = await response?.text();
    // 注入值仅二值模式,不含工作区标识/名称(§5.3 枚举面收敛)。
    expect(html).toContain('window.__MESH_APPEARANCE__ = {"mode":"dark"};');
    expect(html).not.toContain(`theme-inject-${sfx}`); // 注入脚本不含 slug
    await expect.poll(async () => page.locator('html').getAttribute('data-theme')).toBe('dark');
    // 个性化响应缓存边界 + nonce CSP(绝不 unsafe-inline 于 script-src)。
    const cacheControl = response?.headers()['cache-control'] ?? '';
    expect(cacheControl).toContain('private');
    expect(cacheControl).toContain('no-store');
    const csp = response?.headers()['content-security-policy'] ?? '';
    expect(csp).toMatch(/script-src 'self' 'nonce-[^']+'/);
    expect(csp.split('script-src')[1].split(';')[0]).not.toContain('unsafe-inline');
    await context.close();
  });

  test('② A(默认暗)→ B(默认浅)切换:首帧即 B 主题,无先暗后浅', async ({ browser }) => {
    const sfx = uniqueSuffix();
    const { access, refresh } = await registerLogin(`theme-ab-${sfx}@corp.com`);
    await createWorkspace(access, `theme-a-${sfx}`, 'dark');
    await createWorkspace(access, `theme-b-${sfx}`, 'light');
    await api('/api/v1/users/me', {
      method: 'PATCH',
      token: access,
      body: { settings: { theme: null } },
    });

    const context = await browser.newContext();
    await plantSession(context, { access, refresh });
    const page = await context.newPage();
    // 每个文档记录 data-theme 变更历史(首帧顺序取证)。
    await page.addInitScript(() => {
      const w = window as unknown as { __themeFrames: string[]; __themeObserver: MutationObserver };
      w.__themeFrames = [];
      const record = (): void => {
        const theme = document.documentElement.getAttribute('data-theme');
        if (theme !== null) w.__themeFrames.push(theme);
      };
      record();
      w.__themeObserver = new MutationObserver(record);
      w.__themeObserver.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ['data-theme'],
      });
    });

    await page.goto(`/w/theme-a-${sfx}`, { waitUntil: 'load' });
    await expect.poll(async () => page.locator('html').getAttribute('data-theme')).toBe('dark');

    await page.goto(`/w/theme-b-${sfx}`, { waitUntil: 'load' });
    await expect.poll(async () => page.locator('html').getAttribute('data-theme')).toBe('light');
    // 读取时把当前值补入帧序列(兜底观察器未及登记的末帧),再断言无闪错。
    const frames = await page.evaluate(() => {
      const w = window as unknown as { __themeFrames: string[] };
      const current = document.documentElement.getAttribute('data-theme');
      if (current !== null && w.__themeFrames[w.__themeFrames.length - 1] !== current) {
        w.__themeFrames.push(current);
      }
      return w.__themeFrames;
    });
    // B 文档的主题帧序列不得出现 dark(无「先暗后浅」闪错)。
    expect(frames).not.toContain('dark');
    expect(frames[frames.length - 1]).toBe('light');
    await context.close();
  });

  test('③ 换账号不串用:残留 locator(id 不符)不被读取', async ({ browser }) => {
    const sfx = uniqueSuffix();
    const { access, refresh } = await registerLogin(`theme-switch-${sfx}@corp.com`);
    await createWorkspace(access, `theme-switch-${sfx}`, 'light');
    await api('/api/v1/users/me', {
      method: 'PATCH',
      token: access,
      body: { settings: { theme: null } },
    });

    const context = await browser.newContext();
    await plantSession(context, { access, refresh });
    const page = await context.newPage();
    // 预置上一账号/工作区的残留 locator(暗色,其他分区 id)——模拟串用攻击面。
    await page.addInitScript(() => {
      window.localStorage.setItem(
        'mesh.theme.active',
        JSON.stringify({ id: 'http://127.0.0.1:3051:w:other-workspace', mode: 'dark' }),
      );
    });
    await page.goto(`/w/theme-switch-${sfx}`, { waitUntil: 'load' });
    // id 校验先于 mode:残留 locator 被丢弃 → 注入链路解析为 light。
    await expect.poll(async () => page.locator('html').getAttribute('data-theme')).toBe('light');
    // 解析完成后 locator 以当前路由身份重写。
    const locator = await page.evaluate(() =>
      JSON.parse(window.localStorage.getItem('mesh.theme.active') ?? 'null') as {
        id: string;
        mode: string;
      } | null,
    );
    expect(locator?.id).toBe(`127.0.0.1:3051:w:theme-switch-${sfx}`);
    expect(locator?.mode).toBe('light');
    await context.close();
  });

  test('④ 邀请接受页(未登录)首帧即邀请工作区默认主题(preview 同源注入)', async ({ browser }) => {
    const sfx = uniqueSuffix();
    const { access } = await registerLogin(`theme-invite-${sfx}@corp.com`);
    const workspaceId = await createWorkspace(access, `theme-invite-ws-${sfx}`, 'dark');
    const invitation = await api(`/api/v1/workspaces/${workspaceId}/invitations`, {
      method: 'POST',
      token: access,
      body: {},
    });
    const link = (invitation.data as unknown as Array<{ invite_link: string }>)[0]
      .invite_link;
    const token = link.split('/').pop();
    expect(token).toBeTruthy();

    // 全新上下文:无 cookie、无 localStorage——纯未登录首帧。
    const context = await browser.newContext();
    const page = await context.newPage();
    const response = await page.goto(`/invite/${token}`, { waitUntil: 'domcontentloaded' });
    const html = await response?.text();
    expect(html).toContain('window.__MESH_APPEARANCE__ = {"mode":"dark"};');
    await expect.poll(async () => page.locator('html').getAttribute('data-theme')).toBe('dark');
    await context.close();
  });

  test('⑤ locator 白名单:非法 mode 丢弃 → 注入/协商后正确主题', async ({ browser }) => {
    const sfx = uniqueSuffix();
    const { access, refresh } = await registerLogin(`theme-locator-${sfx}@corp.com`);
    await createWorkspace(access, `theme-locator-${sfx}`, 'dark');
    await api('/api/v1/users/me', {
      method: 'PATCH',
      token: access,
      body: { settings: { theme: null } },
    });
    const context = await browser.newContext();
    await plantSession(context, { access, refresh });
    const page = await context.newPage();
    await page.addInitScript(() => {
      window.localStorage.setItem(
        'mesh.theme.active',
        JSON.stringify({ id: location.host + ':w:theme-locator-x', mode: 'javascript:alert(1)' }),
      );
    });
    await page.goto(`/w/theme-locator-${sfx}`, { waitUntil: 'load' });
    // 非法 mode 绝不落 data-theme;注入链路仍解析出 dark。
    await expect.poll(async () => page.locator('html').getAttribute('data-theme')).toBe('dark');
    const dataTheme = await page.locator('html').getAttribute('data-theme');
    expect(['light', 'dark']).toContain(dataTheme);
    await context.close();
  });

  test('⑥ 匿名 shell:public 缓存 + sha256 CSP,无注入', async ({ browser }) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    const response = await page.goto('/', { waitUntil: 'domcontentloaded' });
    expect(response?.status()).toBe(200);
    const html = await response?.text();
    // 无实际注入(脚本内注释提到变量名属正常,断言以「= {」赋值模式为准)。
    expect(html).not.toContain('window.__MESH_APPEARANCE__ = {');
    const cacheControl = response?.headers()['cache-control'] ?? '';
    expect(cacheControl).toContain('public');
    const csp = response?.headers()['content-security-policy'] ?? '';
    expect(csp).toMatch(/script-src 'self' 'sha256-[^']+'/);
    // script-src 绝不允许 unsafe-inline(style-src 的 unsafe-inline 为 React
    // 运行期内联样式所需,与脚本注入面无关)。
    expect(csp.split('script-src')[1].split(';')[0]).not.toContain('unsafe-inline');
    await context.close();
  });
});
