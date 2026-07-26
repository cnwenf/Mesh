/**
 * 真实后端看板视图定义层浏览器走查(MES-43 §4 验收):注册/登录 → 建区 →
 * /board 空态 → 新建视图 → 7 状态类别列骨架 → 切换 group_by=priority(5 列 +
 * 保存条)→ WIP 配置持久化(徽章 0/5)→ 筛选面板增条件 → 保存草稿(PATCH +
 * If-Match)→ 视图复制 → 折叠列。每步截图存证 e2e/evidence/board。
 *
 * 前置:MES-43 后端栈运行中(API 8100 / 库 mesh_test_mes43,MESH_AUTH_MODE=dev)。
 */
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const RUN = String(Date.now()).slice(-7);
const EMAIL = `board-${RUN}@corp.example`;
const PASSWORD = 'secret123';
const SLUG = `board${RUN}`;
const EVIDENCE_DIR = process.env.MES43_EVIDENCE_DIR ?? resolve(HERE, 'evidence', 'board');

test.describe.configure({ mode: 'serial' });

async function registerAndLogin(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').fill('Board Owner');
  await page.getByTestId('login-email').fill(EMAIL);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  // 注册后出现邮箱验证过渡页(已登录未验证),按「继续」进入主壳
  await page.getByTestId('register-continue').click({ timeout: 30_000 });
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: 30_000 });
}

async function createWorkspace(page: Page): Promise<void> {
  await page.getByTestId('ws-switcher-button').click();
  await page.getByTestId('ws-switcher-create').click();
  await page.getByTestId('ws-wizard-name-input').fill('Board Walkthrough');
  await page.getByTestId('ws-wizard-next').click();
  await page.getByTestId('ws-wizard-slug-input').fill(SLUG);
  await page.waitForTimeout(800);
  await page.getByTestId('ws-wizard-next-slug').click();
  await page.waitForTimeout(500);
  await page.getByTestId('ws-wizard-skip').click();
  await expect(page).toHaveURL(new RegExp(`/w/${SLUG}`), { timeout: 15_000 });
}

test('看板视图定义层真实走查 + 截图存证', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  await registerAndLogin(page);
  await createWorkspace(page);

  // 1. /board 空态(§6.12 empty:空态标题 + 新建主操作)
  await page.goto('/board');
  await expect(page.getByText('No views yet')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('view-create-open')).toBeVisible();
  await page.screenshot({ path: `${EVIDENCE_DIR}/01-board-empty.png` });

  // 2. 新建视图 → 7 个状态类别列骨架
  await page.getByTestId('view-create-open').click();
  await page.getByTestId('view-create-name').fill('冲刺看板');
  await page.getByTestId('view-create-submit').click();
  await expect(page.getByTestId('board-columns')).toBeVisible({ timeout: 15_000 });
  for (const key of ['backlog', 'todo', 'in_progress', 'in_review', 'blocked', 'done', 'cancelled']) {
    await expect(page.getByTestId(`board-column-${key}`)).toBeVisible();
  }
  await expect(page.getByTestId('board-title')).toContainText('冲刺看板');
  // URL 同步 /views/{id}(§4.2 可分享/收藏)
  await expect(page).toHaveURL(/\/views\/[0-9a-f-]+$/, { timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/02-board-columns.png` });

  // 3. 切换 group_by=priority → 5 档列 + 保存条(§4.2 未保存提示)→ 保存持久化
  await page.getByTestId('group-by-select').selectOption('priority');
  await expect(page.getByTestId('view-save-bar')).toBeVisible();
  await expect(page.getByTestId('board-column-urgent')).toBeVisible();
  await expect(page.getByTestId('board-column-todo')).toHaveCount(0);
  await page.screenshot({ path: `${EVIDENCE_DIR}/03-group-priority-dirty.png` });
  await page.getByTestId('view-save').click();
  await expect(page.getByTestId('view-save-bar')).toHaveCount(0, { timeout: 10_000 });

  // 再改分组为 assignee 后丢弃 → 回到已保存的 priority 列(§4.2 丢弃语义)
  await page.getByTestId('group-by-select').selectOption('assignee');
  await expect(page.getByTestId('view-save-bar')).toBeVisible();
  await page.getByTestId('view-discard').click();
  await expect(page.getByTestId('view-save-bar')).toHaveCount(0);
  await expect(page.getByTestId('board-column-urgent')).toBeVisible();

  // 4. WIP 配置面板:high 列设 5/block → 即时持久化,列头徽章 0/5
  //    (当前 group_by=priority,列为 5 档优先级)
  await page.getByTestId('panel-toggle-wip').click();
  await expect(page.getByTestId('wip-config-panel')).toBeVisible();
  await page.getByTestId('wip-limit-high').fill('5');
  await page.getByTestId('wip-enforcement-high').selectOption('block');
  await page.getByTestId('wip-save-high').click();
  await expect(page.getByTestId('wip-badge-high')).toHaveText('0/5', { timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/04-wip-badge.png` });

  // 5. 筛选面板:增条件并上报结构化配置
  await page.getByTestId('panel-toggle-filter').click();
  await expect(page.getByTestId('filter-config-panel')).toBeVisible();
  await page.getByTestId('filter-add-condition').click();
  await expect(page.getByTestId('filter-row-0')).toBeVisible();
  await page.screenshot({ path: `${EVIDENCE_DIR}/05-filter-panel.png` });

  // 6. 保存草稿(PATCH 成功后保存条消失;重新加载仍为 priority 分组 = 已持久化)
  await page.getByTestId('view-save').click();
  await expect(page.getByTestId('view-save-bar')).toHaveCount(0, { timeout: 10_000 });
  await page.reload();
  await expect(page.getByTestId('board-column-urgent')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('wip-badge-high')).toHaveText('0/5');
  await page.screenshot({ path: `${EVIDENCE_DIR}/06-saved-reloaded.png` });

  // 7. 视图菜单:复制 → 新视图「(copy)」出现并被选中
  const viewId = (page.url().match(/\/views\/([0-9a-f-]+)$/) ?? [])[1];
  expect(viewId).toBeTruthy();
  await page.getByTestId(`view-menu-${viewId}`).click();
  await page.getByTestId(`view-menu-list-${viewId}`).getByText('Duplicate').click();
  await expect(page.getByText('冲刺看板 (copy)').first()).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/07-duplicated.png` });

  // 8. 折叠列(本视图 group_by=priority → 折叠 urgent)
  await page
    .getByTestId('board-column-urgent')
    .getByRole('button', { name: /Collapse column|折叠列/ })
    .click();
  await expect(page.getByTestId('board-column-urgent').getByTestId('quick-add-urgent')).toHaveCount(0);
  await page.screenshot({ path: `${EVIDENCE_DIR}/08-collapsed.png` });

  // 9. 侧栏导航入口可达(README §6.12 顶层入口:看板)
  await page.getByTestId('nav-board').click();
  await expect(page.getByTestId('board-page')).toBeVisible();

  // 控制台无错误(过滤 favicon/资源类噪音;本走查栈未起 realtime 网关,
  // WebSocket 连接拒绝属环境噪音 —— 前端按 §3.5 降级轮询,非产品缺陷)
  const realErrors = consoleErrors.filter(
    (text) =>
      !text.includes('favicon') &&
      !text.includes('Failed to load resource') &&
      !text.includes('WebSocket connection'),
  );
  expect(realErrors).toEqual([]);
});
