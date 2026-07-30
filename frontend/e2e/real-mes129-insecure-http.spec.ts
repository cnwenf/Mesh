/**
 * MES-129 真实 e2e:HTTP 非安全上下文(http://<LAN IP>,非 localhost / 非 HTTPS)下
 * 登录及一切写请求可用性验证。
 *
 * 缺陷背景:client.ts 给所有 POST/PUT/PATCH/DELETE 自动加 Idempotency-Key,裸调
 * `crypto.randomUUID()`——该 API 仅安全上下文可用,HTTP 部署下为 undefined,抛
 * TypeError 使 fetch 根本不发出 → 登录及全部写操作报「网络错误」,GET(页面加载)
 * 正常。修复:`frontend/src/api/uuid.ts` 的安全上下文无关 uuidv4(getRandomValues
 * 兜底,任意上下文可用)。
 *
 * 本验证像真人一样操作:经 LAN IP 打开登录页(前置断言 isSecureContext=false、
 * crypto.randomUUID=undefined,确保真的处于故障上下文)→ 注册(register + login
 * 两次写 POST)→ 创建工作区 → 首页建 issue → 详情页发评论,并 psql 校验落库。
 * 写请求全部经真实后端(18100)真实入库,非 mock。
 *
 * 回归复现模式(仅验证用,不属产品断言):MES129_EXPECT_BUG=1 且源码回滚修复前
 * (如 git stash),注册应报网络错误、停在登录页——证明本用例确能捕获该缺陷。
 * 聊天同走 MeshApiClient 写路径(同一 Idempotency-Key 链路),其可用性由登录/
 * 评论等写请求的通过等价覆盖;新工作区无 agent 运行时,故不单列聊天用例。
 */
import { execFileSync } from 'node:child_process';
import { expect, test } from '@playwright/test';

const PG_CONTAINER = 'mesh-postgres-1';
const EXPECT_BUG = process.env.MES129_EXPECT_BUG === '1';
/** 网络错误提示(中英文目录均覆盖)。 */
const NETWORK_ERROR_RE = /Network error|网络错误/;
const RUN_ID = Date.now().toString(36);
const EMAIL = `mes129-${RUN_ID}@example.com`;
const PASSWORD = 'mes129-passw0rd-X9';
const DISPLAY_NAME = 'MES129 E2E';
const WORKSPACE_NAME = `MES129 WS ${RUN_ID}`;
const WORKSPACE_SLUG = `mes129-${RUN_ID}`;
const ISSUE_TITLE = `mes129 issue ${RUN_ID}`;
const COMMENT_TEXT = `mes129 comment ${RUN_ID}`;

function psql(sql: string): string {
  return execFileSync(
    'docker',
    ['exec', '-i', PG_CONTAINER, 'psql', '-U', 'mesh', '-d', 'mesh', '-tAc', sql],
    {
      encoding: 'utf8',
      timeout: 30_000,
    },
  ).trim();
}

test.describe('MES-129:HTTP 非安全上下文写请求', () => {
  test('前置:页面确实处于非安全上下文(randomUUID 缺失)', async ({ page, baseURL }) => {
    expect(
      baseURL,
      'baseURL 必须是 LAN IP(非 localhost / 127.0.0.1),否则不构成非安全上下文',
    ).not.toMatch(/localhost|127\.0\.0\.1/);
    await page.goto('/login');
    const context = await page.evaluate(() => ({
      isSecureContext: window.isSecureContext,
      hasRandomUUID: typeof crypto.randomUUID === 'function',
    }));
    expect(context.isSecureContext, 'isSecureContext 应为 false(HTTP + 非回环主机)').toBe(false);
    expect(context.hasRandomUUID, 'crypto.randomUUID 应缺失(故障现场)').toBe(false);
  });

  test('注册 + 登录(两次写 POST)在非安全上下文下成功', async ({ page }) => {
    await page.goto('/login');
    await page.getByTestId('login-mode-register').click();
    await page.getByTestId('login-display-name').fill(DISPLAY_NAME);
    await page.getByTestId('login-email').fill(EMAIL);
    await page.getByTestId('login-password').fill(PASSWORD);
    await page.getByTestId('login-account-submit').click();

    if (EXPECT_BUG) {
      // 回归复现:randomUUID 缺失 → POST 发不出去 → 归一 error.network,停在登录页。
      await expect(page.getByTestId('login-error')).toContainText(NETWORK_ERROR_RE);
      expect(page.url()).toContain('/login');
      return;
    }

    // 修复后:register + login 均成功 → 注册结果态(会话已建立)。
    await expect(page.getByTestId('register-verify-sent')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('login-error')).toHaveCount(0);
    expect(psql(`SELECT count(*) FROM users WHERE email = '${EMAIL}'`)).toBe('1');
  });

  test('续写链路:建工作区 → 建 issue → 发评论,全部真实落库', async ({ page }) => {
    test.skip(EXPECT_BUG, '回归复现模式下无登录态,后续写链路不适用');

    // 用上一条已注册账号登录(独立浏览器上下文,token 不共享;登录亦为写 POST)。
    await page.goto('/login');
    await page.getByTestId('login-email').fill(EMAIL);
    await page.getByTestId('login-password').fill(PASSWORD);
    await page.getByTestId('login-account-submit').click();
    await page.waitForURL('**/', { timeout: 30_000 });

    // 新用户无工作区 → 走向导(名字 → slug → 跳过邀请 → 创建,创建为写 POST)。
    await expect(page.getByTestId('home-no-workspaces')).toBeVisible({ timeout: 30_000 });
    await page.getByTestId('home-create-workspace').click();
    await page.getByTestId('ws-wizard-name-input').fill(WORKSPACE_NAME);
    await page.getByTestId('ws-wizard-next').click();
    await page.getByTestId('ws-wizard-slug-input').fill(WORKSPACE_SLUG);
    await page.getByTestId('ws-wizard-next-slug').click();
    await page.getByTestId('ws-wizard-skip').click();
    await page.waitForURL('**/w/' + WORKSPACE_SLUG);
    expect(psql(`SELECT count(*) FROM workspaces WHERE slug = '${WORKSPACE_SLUG}'`)).toBe('1');

    // 首页快捷建 issue(写 POST)。
    await page.goto('/');
    await page.getByTestId('home-new-title').fill(ISSUE_TITLE);
    await page.getByTestId('home-create').click();
    await expect(page.getByTestId('home-issue-list').getByText(ISSUE_TITLE)).toBeVisible({
      timeout: 30_000,
    });
    expect(psql(`SELECT count(*) FROM issues WHERE title = '${ISSUE_TITLE}'`)).toBe('1');

    // 详情页发评论(写 POST;经 localId 乐观插入 + 真实落库)。
    await page.getByTestId('home-issue-list').getByText(ISSUE_TITLE).click();
    await expect(page.getByTestId('issue-detail')).toBeVisible();
    await page.getByTestId('composer-input').fill(COMMENT_TEXT);
    await page.getByTestId('composer-submit').click();
    await expect(page.getByTestId('comments-timeline').getByText(COMMENT_TEXT)).toBeVisible({
      timeout: 30_000,
    });
    expect(psql(`SELECT count(*) FROM comments WHERE body_markdown = '${COMMENT_TEXT}'`)).toBe('1');
  });
});
