/**
 * MES-107 真实 e2e — 去脚手架化验收(production 鉴权 + 公网 HTTP,桌面/手机双视口,
 * 见 playwright.mes107.config.ts 的 projects)。
 *
 * 验收项(issue 逐条对应):
 * 1. 未登录访问首页 → 守卫跳 /login(与 MES-106 协同),登录页无 dev 令牌块 /
 *    过时 phaseNote 文案;
 * 2. 注册登录后首页是真实产品页:问候语 + 工作区列表/空态,无演示组件
 *    (任何 data-testid^=demo- 零残留)、无「加载失败」;
 * 3. 无工作区用户:空态 + 创建工作区向导可用;创建后首页出现工作区卡片,
 *    点击进入工作区首页;
 * 4. 有工作区用户:仪表盘(真实 issue API)加载,无加载失败。
 *
 * 前置:mes107 隔离栈运行中(playwright.mes107.config.ts 头部注释)。
 */
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const PASSWORD = 'Mesh-Demo#2026x';
const LOAD_FAILED_TEXT = 'We could not load this content. Please try again.';

/** 每用例唯一邮箱(注册即登录;同邮箱重注会 409) */
function uniqueEmail(suffix: string): string {
  return `mes107-${suffix}-${String(process.pid)}@example.com`;
}

/** 注册新账号并经「已发验证邮件」结果页继续(生产模式注册自动登录) */
async function registerAndContinue(page: Page, email: string, next?: string): Promise<void> {
  await page.goto(next !== undefined ? `/login?next=${encodeURIComponent(next)}` : '/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').fill('MES-107 E2E');
  await page.getByTestId('login-email').fill(email);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  await expect(page.getByTestId('register-verify-sent')).toContainText(email);
  await page.getByTestId('register-continue').click();
}

test.describe('MES-107 首页去脚手架化 / 生产就绪', () => {
  test('未登录访问首页 → 守卫跳登录页;登录页无 dev 令牌块与阶段文案', async ({ page }) => {
    await page.goto('/');
    await page.waitForURL(/\/login\?next=/);
    await expect(page.getByTestId('login-email')).toBeVisible();
    // 脚手架残留清理:dev 令牌直填块与过时 phaseNote 已移除
    await expect(page.locator('.mesh-login__dev')).toHaveCount(0);
    await expect(page.locator('[data-testid="login-token"]')).toHaveCount(0);
    await expect(page.getByText(/Phase 2/)).toHaveCount(0);
    await expect(page.getByText(/placeholder/i)).toHaveCount(0);
  });

  test('注册登录后首页真实加载:问候语 + 无演示组件 + 无加载失败', async ({ page }) => {
    await registerAndContinue(page, uniqueEmail('home'));
    await page.waitForURL((url) => new URL(url).pathname === '/');
    await expect(page.getByTestId('home-greeting')).toBeVisible();
    await expect(page.getByTestId('home-greeting')).toContainText('MES-107 E2E');
    // 演示组件零残留(骨架演示区已整体替换为真实仪表盘)
    await expect(page.locator('[data-testid^="demo-"]')).toHaveCount(0);
    await expect(page.getByText(LOAD_FAILED_TEXT)).toHaveCount(0);
    await expect(page.getByText(/演示|demo/i)).toHaveCount(0);
  });

  test('无工作区 → 空态 + 向导创建工作区 → 首页卡片可进入工作区', async ({ page }) => {
    await registerAndContinue(page, uniqueEmail('create'));
    await page.waitForURL((url) => new URL(url).pathname === '/');

    // 新用户无成员身份:空态 + 创建入口(仪表盘不渲染)
    await expect(page.getByTestId('home-no-workspaces')).toBeVisible();
    await expect(page.getByTestId('home-dashboard')).toHaveCount(0);

    // 走真实创建向导(name → slug → 跳过邀请)
    const slug = `mes107-${String(Date.now()).slice(-8)}`;
    await page.getByTestId('home-create-workspace').click();
    await page.getByTestId('ws-wizard-name-input').fill('MES-107 Home WS');
    await page.getByTestId('ws-wizard-next').click();
    await page.getByTestId('ws-wizard-slug-input').fill(slug);
    await page.getByTestId('ws-wizard-next-slug').click();
    await page.getByTestId('ws-wizard-skip').click();

    // 回首页:工作区卡片出现,仪表盘随之加载(真实 issue API,无加载失败)
    await page.goto('/');
    const card = page.getByTestId(`home-workspace-${slug}`);
    await expect(card).toBeVisible();
    await expect(card).toContainText('MES-107 Home WS');
    await expect(page.getByTestId('home-dashboard')).toBeVisible();
    await expect(page.getByText(LOAD_FAILED_TEXT)).toHaveCount(0);

    // 卡片深链进入工作区首页
    await card.locator('a').click();
    await page.waitForURL(`**/w/${slug}`);
    await expect(page.getByTestId('ws-home-name')).toContainText('MES-107 Home WS');
  });

  test('仪表盘快捷创建 issue 真实落库并经列表回显', async ({ page }) => {
    await registerAndContinue(page, uniqueEmail('dash'));
    await page.waitForURL((url) => new URL(url).pathname === '/');

    const slug = `mes107d-${String(Date.now()).slice(-8)}`;
    await page.getByTestId('home-create-workspace').click();
    await page.getByTestId('ws-wizard-name-input').fill('MES-107 Dash WS');
    await page.getByTestId('ws-wizard-next').click();
    await page.getByTestId('ws-wizard-slug-input').fill(slug);
    await page.getByTestId('ws-wizard-next-slug').click();
    await page.getByTestId('ws-wizard-skip').click();

    await page.goto('/');
    await expect(page.getByTestId('home-dashboard')).toBeVisible();
    // 空工作区仪表盘:空态(无加载失败)
    await expect(page.getByText(LOAD_FAILED_TEXT)).toHaveCount(0);

    // 快捷创建 → 行回显
    await page.getByTestId('home-new-title').fill('首页仪表盘创建的工作项');
    await page.getByTestId('home-create').click();
    await expect(page.getByTestId('home-issue-list')).toContainText('首页仪表盘创建的工作项');
  });
});
