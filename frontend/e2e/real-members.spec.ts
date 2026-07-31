/**
 * 成员名册真实浏览器 e2e(验收:像真人一样实际操作)。
 * 前置:members-global-setup.mjs 已拉起真实 API(mesh_app/RLS)+ 播种数据并写出上下文。
 * 走查:名册渲染(人+agent + AI 徽章)→「仅 Agent」同路由投影 → 单一 [+ 新建 Agent] 入口
 * 打开四步向导并真实创建一个 agent → agent 行深链详情页 → 角色变更 → 停用 → 移除。
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import { injectSession } from './helpers';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const context = JSON.parse(readFileSync(resolve(ROOT, 'e2e', '.members-context.json'), 'utf8')) as {
  ownerToken: string;
  workspaceId: string;
  joinerMemberId: string;
};

async function login(page: import('@playwright/test').Page): Promise<void> {
  // dev-auth 栈无表单登录:会话经 authStore 持久化键注入(MES-107 起登录页无 dev 入口)
  await injectSession(page, context.ownerToken);
  await page.goto('/');
}

/**
 * 行文本定位助手(验收 B1):A-05 双渲染(桌面表格 + 手机卡片同名节点同 DOM 共存)使裸
 * `getByText` 在 strict-mode 下命中 2 节点而抛错;统一经 `.first()` 取首匹配消歧。
 * 全 spec 同类裸 `getByText` 均改走本助手(仅 `toHaveCount(0)` 这类「计数=0」断言保留裸
 * 查询,因其语义为「无任何匹配」,经 `.first()` 会破坏计数语义)。
 */
function rst(page: import('@playwright/test').Page, text: string) {
  return page.getByText(text).first();
}

test.describe('成员名册页真实操作(member.md §4 / README §6.12)', () => {
  test('名册渲染人+agent、AI 徽章、仅 Agent 同路由投影、单一新建入口', async ({ page }) => {
    await login(page);
    await page.goto('/members');

    // 标题「成员」
    await expect(page.getByRole('heading', { level: 1 })).toContainText(/Members|成员/);
    // 人类成员(Joiner)与 agent(代码助手)同表,agent 带 AI 徽章。
    // A-05 双渲染(桌面表格 + 手机卡片同名节点同 DOM 共存)→ 文本查询命中 2 节点,
    // 用 .first() 消歧(strict-mode 验收 R4)。
    await expect(rst(page, 'Joiner')).toBeVisible();
    await expect(rst(page, '代码助手')).toBeVisible();
    await expect(rst(page, 'AI')).toBeVisible();

    // 「仅 Agent」是同一路由的筛选投影
    await page.getByTestId('tab-agent').click();
    await expect(page).toHaveURL(/member_type=agent/);
    await expect(rst(page, '代码助手')).toBeVisible();
    await expect(page.getByText('Joiner')).toHaveCount(0);
    // 同一 [+ 新建 Agent ] 入口仍在
    await expect(page.getByTestId('new-agent-button')).toBeVisible();

    // [+ 新建 Agent] → 四步向导(唯一创建入口,agent.md §4.4),不跳转第二页面
    await page.getByTestId('new-agent-button').click();
    await expect(page.getByTestId('agent-wizard-basic')).toBeVisible();

    // ① 基本信息
    await page.getByTestId('agent-wizard-name').fill('小测');
    await page.getByTestId('agent-wizard-role-tag').fill('测试工程师');
    await page.getByTestId('agent-wizard-next').click();

    // ② 模型与指令
    await expect(page.getByTestId('agent-wizard-model')).toBeVisible();
    await page.getByTestId('agent-wizard-instructions').fill('你是测试工程师,收到 issue 先复现。');
    await page.getByTestId('agent-wizard-next').click();

    // ③ 技能与工具(稍后配置占位)
    await expect(page.getByTestId('agent-wizard-skills')).toBeVisible();
    await page.getByTestId('agent-wizard-next').click();

    // ④ 可见性 → 完成:真实 POST /agents,新 agent 出现在同一名册
    await expect(page.getByTestId('agent-wizard-visibility')).toBeVisible();
    await page.getByTestId('agent-wizard-finish').click();
    await expect(rst(page, '小测')).toBeVisible();
  });

  test('agent 行深链进入详情页(配置 / 历史 Tab 可用)', async ({ page }) => {
    await login(page);
    await page.goto('/members?member_type=agent');
    await rst(page, '代码助手').click();
    await expect(page.getByTestId('agent-detail-page')).toBeVisible();
    await expect(page.getByTestId('agent-detail-name')).toContainText('代码助手');
    await expect(page.getByTestId('agent-detail-badge')).toBeVisible();

    // 配置 Tab:保存生成新版本
    await page.getByTestId('agent-tab-config').click();
    await expect(page.getByTestId('agent-panel-config')).toBeVisible();

    // 历史 Tab:至少一个初始版本
    await page.getByTestId('agent-tab-history').click();
    await expect(page.getByTestId('agent-panel-history')).toContainText(/initial configuration/);

    // 返回名册
    await page.getByTestId('agent-detail-back').click();
    await expect(page).toHaveURL(/\/members/);
  });

  test('角色变更 / 停用 / 移除走真实后端并反映到名册', async ({ page }) => {
    await login(page);
    await page.goto('/members');
    const joiner = context.joinerMemberId;

    // 角色变更:member → admin,落库后名册重拉显示 admin
    await page.getByTestId(`role-select-${joiner}`).selectOption('admin');
    await expect(page.getByTestId(`role-select-${joiner}`)).toHaveValue('admin');

    // 行操作已迁入底座 Menu(验收 H1/B2):展开该行「Row actions」后按 menuitem 文案点击。
    // openRowMenu 幂等化(验收 B2):开菜单前若菜单已开(残留 menuitem)先 Esc 关闭并等其缺席,
    // 规避「触发钮再点即关」的 toggle 时序 / 行状态刷新后旧菜单实例残留的竞态。
    const row = page.getByTestId(`member-row-${joiner}`);
    const trigger = row.getByRole('button', { name: 'Row actions' });
    const openRowMenu = async () => {
      // 幂等化(验收 B2):若菜单已开(role=menu 仍在 DOM——上一次 menuitem 点击未关或旧实例
      // 残留),用 trigger 的 toggle 确定性地关掉它(Esc 依赖焦点在菜单内,菜单外按 Esc 不关,
      // 不可靠),等其卸载后再点一次确定性地展开,等首条 menuitem 可见。
      const openMenu = row.locator('[role="menu"]');
      if (await openMenu.isVisible().catch(() => false)) {
        await trigger.click();
        await expect(openMenu).toBeHidden({ timeout: 5_000 });
      }
      await trigger.click();
      await expect(page.getByRole('menuitem').first()).toBeVisible({ timeout: 10_000 });
    };

    // 停用:二次确认后状态变为已停用(行菜单出现「Enable」)
    await openRowMenu();
    await page
      .getByRole('menuitem', { name: 'Disable' })
      .click({ timeout: 10_000 });
    await page.getByTestId('remove-confirm').click();
    await openRowMenu();
    await expect(page.getByRole('menuitem', { name: 'Enable' })).toBeVisible({
      timeout: 10_000,
    });

    // 移除:确认后软删除,默认名册不再展示该成员(Remove 等待加明确超时 + 诊断,验收 B2)
    await openRowMenu();
    const removeItem = page.getByRole('menuitem', { name: 'Remove' });
    await expect(removeItem, {
      message:
        'Remove 菜单项未在超时内出现:行菜单可能未展开或行状态刷新竞态;诊断 row 存在性=' +
        (await row.count()),
    }).toBeVisible({ timeout: 10_000 });
    await removeItem.click({ timeout: 10_000 });
    await page.getByTestId('remove-confirm').click();
    await expect(page.getByTestId(`role-select-${joiner}`)).toHaveCount(0);
  });
});
