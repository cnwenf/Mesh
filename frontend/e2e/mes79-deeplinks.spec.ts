/**
 * MES-79 规范深链 e2e(search-command-palette.md §3.4 测试矩阵,mock 套件)。
 *
 * 场景 ①:旧书签 /board 直接刷新 → 入口出 SPA,前端路由 replace navigation
 * 落 active workspace 的 /w/{slug}/board(query/hash 保留),数据加载无白屏。
 * 场景 ②(过期 slug HTTP 301)为真实后端专属(nginx 入口文档处理器),mock
 * 套件中占位跳过,见 real 套件。
 *
 * mock 端点可用性以运行时探测门控:MES-79 路由(/users/me 等)未就绪的旧 mock
 * 下整组跳过,不就绪不假阳。
 */
import { expect, test } from '@playwright/test';
import { login, MOCK_BASE, resetMockServer } from './helpers';

const MOCK_HAS_MES79_ROUTES = async (): Promise<boolean> => {
  try {
    const res = await fetch(`${MOCK_BASE}/api/v1/users/me`, {
      headers: { Authorization: 'Bearer mesh-dev:00000000-0000-0000-0000-000000000001' },
    });
    return res.ok;
  } catch {
    return false;
  }
};

test.describe('MES-79 规范深链 / 扁平路由迁移(§3.4)', () => {
  test.beforeEach(async () => {
    test.skip(!(await MOCK_HAS_MES79_ROUTES()), 'mock 套件未提供 MES-79 /users/me 路由');
    await resetMockServer();
  });

  test('旧书签 /board 刷新 → replace navigation 落 /w/{slug}/board 且 query/hash 保留', async ({
    page,
  }) => {
    await login(page);
    // 直接访问旧扁平路由(模拟书签刷新)。
    await page.goto('/board?view=x#card-1');
    // 迁移为路由器 replace navigation:URL 规范化且不入历史栈。
    await page.waitForURL(/\/w\/[^/]+\/board\?view=x/);
    expect(page.url()).toContain('#card-1');
    // 历史栈无新增(替换语义):后退不回到 /board。
    await page.goBack();
    await expect
      .poll(() => page.url(), { timeout: 5000 })
      .not.toMatch(/http:\/\/127\.0\.0\.1:5173\/board\?view=x/);
  });

  test('多工作区无上下文 → 工作区选择页(解析序 ⑤)', async ({ page }) => {
    // 需要 mock 以多工作区成员身份响应 /users/me;单一归属 mock 下本用例跳过。
    await login(page);
    const res = await fetch(`${MOCK_BASE}/api/v1/users/me`, {
      headers: { Authorization: 'Bearer mesh-dev:00000000-0000-0000-0000-000000000001' },
    });
    const body = (await res.json()) as {
      data?: { memberships?: ReadonlyArray<unknown> };
    };
    const memberships = body.data?.memberships ?? [];
    test.skip(memberships.length < 2, 'mock 用户单一归属,解析序 ④ 直达,不经选择页');

    await page.goto('/inbox');
    await page.waitForURL(/\/workspace-picker\?next=/);
    await expect(page.getByTestId('workspace-picker')).toBeVisible();
  });

  test('规范深链 /w/{slug}/approvals 直达审批页(§3.4 九条清单)', async ({ page }) => {
    await login(page);
    const me = await fetch(`${MOCK_BASE}/api/v1/users/me`, {
      headers: { Authorization: 'Bearer mesh-dev:00000000-0000-0000-0000-000000000001' },
    });
    const body = (await me.json()) as {
      data?: { memberships?: ReadonlyArray<{ workspace_slug?: string }> };
    };
    const slug = body.data?.memberships?.[0]?.workspace_slug;
    test.skip(slug === undefined, 'mock 未提供成员资格');
    await page.goto(`/w/${slug}/approvals`);
    await expect(page.getByTestId('approvals-page')).toBeVisible();
  });

  test('认证内页面 noindex + canonical(§3.4 SEO)', async ({ page }) => {
    await login(page);
    await page.goto('/');
    await expect
      .poll(async () =>
        page.evaluate(() => document.querySelector('meta[name="robots"]')?.getAttribute('content')),
      )
      .toBe('noindex');
    await expect
      .poll(async () =>
        page.evaluate(() => document.querySelector('link[rel="canonical"]')?.getAttribute('href')),
      )
      .toContain('http');
  });
});

test.describe('MES-79 过期 slug HTTP 301(真实后端专属,§3.4 场景 ②)', () => {
  // 过期 slug 的 301 由 nginx 入口文档处理器 + 后端 /__mesh_entry 探针产生,
  // mock 套件无此链路 —— 占位跳过,断言在 real 套件以 cURL 级执行
  // (301 状态码 + Location 路径与 query;hash 不断言,服务端无从得知)。
  test.skip('过期 slug 深链刷新 → 入口 301 至新 slug(保留 query)', () => undefined);
});
