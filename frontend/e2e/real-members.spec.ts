/**
 * 成员名册真实浏览器 e2e(验收:像真人一样实际操作)。
 * 前置:membros-global-setup.mjs 已拉起真实 API(mesh_app/RLS)+ 播种数据并写出上下文。
 * 走查:名册渲染(人+agent + AI 徽章)→「仅 Agent」同路由投影 → 单一 [+ 新建 Agent] 占位入口
 * → 角色变更 → 停用 → 移除(软删除后默认名册不再展示)。
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const context = JSON.parse(readFileSync(resolve(ROOT, 'e2e', '.members-context.json'), 'utf8')) as {
  ownerToken: string;
  workspaceId: string;
  joinerMemberId: string;
};

async function login(page: import('@playwright/test').Page): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-token').fill(context.ownerToken);
  await page.getByTestId('login-submit').click();
  await page.waitForURL('**/');
}

test.describe('成员名册页真实操作(member.md §4 / README §6.12)', () => {
  test('名册渲染人+agent、AI 徽章、仅 Agent 同路由投影、单一新建入口', async ({ page }) => {
    await login(page);
    await page.goto('/members');

    // 标题「成员」
    await expect(page.getByRole('heading', { level: 1 })).toContainText(/Members|成员/);
    // 人类成员(Joiner)与 agent(代码助手)同表,agent 带 AI 徽章
    await expect(page.getByText('Joiner').first()).toBeVisible();
    await expect(page.getByText('代码助手')).toBeVisible();
    await expect(page.getByText('AI').first()).toBeVisible();

    // 「仅 Agent」是同一路由的筛选投影
    await page.getByTestId('tab-agent').click();
    await expect(page).toHaveURL(/member_type=agent/);
    await expect(page.getByText('代码助手')).toBeVisible();
    await expect(page.getByText('Joiner')).toHaveCount(0);
    // 同一 [+ 新建 Agent ] 入口仍在
    await expect(page.getByTestId('new-agent-button')).toBeVisible();

    // [+ 新建 Agent] → 占位态(即将上线),不跳转第二页面
    await page.getByTestId('new-agent-button').click();
    await page.getByTestId('add-tab-agent').click();
    await expect(page.getByTestId('agent-coming-soon')).toBeVisible();
    await page.keyboard.press('Escape');
  });

  test('角色变更 / 停用 / 移除走真实后端并反映到名册', async ({ page }) => {
    await login(page);
    await page.goto('/members');
    const joiner = context.joinerMemberId;

    // 角色变更:member → admin,落库后名册重拉显示 admin
    await page.getByTestId(`role-select-${joiner}`).selectOption('admin');
    await expect(page.getByTestId(`role-select-${joiner}`)).toHaveValue('admin');

    // 停用:二次确认后状态变为已停用(行内出现「启用」)
    await page.getByTestId(`disable-${joiner}`).click();
    await page.getByTestId('remove-confirm').click();
    await expect(page.getByTestId(`enable-${joiner}`)).toBeVisible();

    // 移除:确认后软删除,默认名册不再展示该成员
    await page.getByTestId(`remove-${joiner}`).click();
    await page.getByTestId('remove-confirm').click();
    await expect(page.getByTestId(`role-select-${joiner}`)).toHaveCount(0);
  });
});
