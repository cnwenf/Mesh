/**
 * MES-111 批次②真实后端浏览器走查(MES-125 自验收 #2/#3):
 * 看板拖拽(鼠标 + 键盘双路径)+ 快速建卡 + List 布局、issue 列表 DataView、
 * issue 详情 DetailLayout(属性抽屉/Tab/内联标题)、评论(草稿自动保存/失败重试/
 * 删除撤销)、附件上传失败重试——桌面 1440×900 与手机 390×844,亮/暗双主题,
 * 截图存证 e2e/evidence/mes111-b2/(md5 唯一性由 check-evidence-unique 门禁)。
 *
 * 前置:本地后端栈运行中(api 8020 / gateway 8091;mes125-* 容器,dev 鉴权)。
 */
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const RUN = String(Date.now()).slice(-7);
const PASSWORD = 'secret123';
const SLUG = `mesb2${RUN}`;
const EVIDENCE_DIR = process.env.MES111B2_EVIDENCE_DIR ?? resolve(HERE, 'evidence', 'mes111-b2');
const UPLOAD_FILE = resolve(HERE, 'fixtures', 'mesh-upload.png');

test.describe.configure({ mode: 'serial', timeout: 300_000 });

function emailFor(project: string): string {
  return `mesb2-${project}-${RUN}@corp.example`;
}

/** 主题预置(theme.md 协商链:账号偏好经 mesh.settings.v1 持久化镜像,防闪烁首帧)。 */
async function applyTheme(page: Page, theme: 'light' | 'dark'): Promise<void> {
  await page.evaluate((mode) => {
    localStorage.setItem(
      'mesh.settings.v1',
      JSON.stringify({
        state: { preferences: { theme: mode, locale: null, timezone: 'UTC' } },
        version: 2,
      }),
    );
  }, theme);
  await page.reload();
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: 30_000 });
}

async function registerAndLogin(page: Page, email: string): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').fill('Batch Two');
  await page.getByTestId('login-email').fill(email);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  await page.getByTestId('register-continue').click({ timeout: 30_000 });
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: 30_000 });
}

async function createWorkspace(page: Page): Promise<void> {
  await page.getByTestId('ws-switcher-button').click();
  await page.getByTestId('ws-switcher-create').click();
  await page.getByTestId('ws-wizard-name-input').fill('Batch Two Workspace');
  await page.getByTestId('ws-wizard-next').click();
  await page.getByTestId('ws-wizard-slug-input').fill(SLUG);
  await page.waitForTimeout(800);
  await page.getByTestId('ws-wizard-next-slug').click();
  await page.waitForTimeout(500);
  await page.getByTestId('ws-wizard-skip').click();
  await expect(page).toHaveURL(new RegExp(`/w/${SLUG}`), { timeout: 15_000 });
}

async function createBoardView(page: Page, name: string): Promise<void> {
  await page.goto('/board');
  await page.getByTestId('view-create-open').click();
  await page.getByTestId('view-create-name').fill(name);
  await page.getByTestId('view-create-submit').click();
  // board-columns-wrap 在桌面(board-columns)与手机(board-compact)两种形态下均在场。
  await expect(page.getByTestId('board-columns-wrap')).toBeVisible({ timeout: 20_000 });
}

async function quickAdd(page: Page, columnKey: string, title: string): Promise<void> {
  const input = page.getByTestId(`quick-add-${columnKey}`);
  await input.fill(title);
  await input.press('Enter');
  await expect(page.getByTestId(`column-body-${columnKey}`).getByText(title)).toBeVisible({
    timeout: 15_000,
  });
}

test('批次②桌面走查:拖拽(鼠标+键盘)/List/详情/评论/附件 + 亮暗存证', async ({ page }, testInfo) => {
  if (testInfo.project.name === 'mobile') {
    test.skip(true, 'mobile 走查在下一用例');
  }
  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  // 诊断辅助(建议排查项 3):记录非 2xx 响应的 URL,定位偶发 409 来源。
  const failedResponses: string[] = [];
  page.on('response', (resp) => {
    if (resp.status() >= 400) failedResponses.push(`${resp.status()} ${resp.url()}`);
  });

  await registerAndLogin(page, emailFor('desktop'));
  await createWorkspace(page);
  await createBoardView(page, '冲刺看板');

  // 1. 快速建卡(继承列分组值,§4.5)
  await quickAdd(page, 'todo', '评审设计稿');
  await quickAdd(page, 'todo', '联调接口');

  // 2. 鼠标拖拽(§9.4:阈值进入 → 浮层 → 目标列高亮/指示线 → 乐观落位)
  const cardBox = await page
    .getByTestId('column-body-todo')
    .locator('[data-testid^="board-card-"]')
    .filter({ hasText: '评审设计稿' })
    .boundingBox();
  const targetBox = await page.getByTestId('column-body-in_progress').boundingBox();
  if (cardBox === null || targetBox === null) throw new Error('drag boxes missing');
  await page.mouse.move(cardBox.x + cardBox.width / 2, cardBox.y + cardBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(targetBox.x + targetBox.width / 2, targetBox.y + 40, { steps: 14 });
  // 拖拽过程反馈:浮层克隆在场(§9.4.2 阴影 + 微缩放)
  await expect(page.getByTestId('board-drag-clone')).toBeVisible();
  await page.mouse.up();
  await expect(
    page
      .getByTestId('column-body-in_progress')
      .locator('[data-testid^="board-card-"]')
      .filter({ hasText: '评审设计稿' }),
  ).toBeVisible({ timeout: 15_000 });
  // live region 播报落位(§10.2;鼠标路径播报 dragDropped)
  await expect(page.getByTestId('board-live')).toContainText('Dropped');
  await page.screenshot({ path: `${EVIDENCE_DIR}/desktop-light-01-board-drag.png` });

  // 3. 键盘移动模式(§9.4.5:聚焦 → 方向键选目标 → Enter 确认)
  const keyboardCard = page
    .getByTestId('column-body-in_progress')
    .locator('[data-testid^="board-card-"]')
    .filter({ hasText: '评审设计稿' });
  await keyboardCard.focus();
  await page.keyboard.press('ArrowRight'); // 第一下:进入移动模式
  await expect(page.getByTestId('board-live')).toContainText(/Move mode|Target column/);
  await page.keyboard.press('ArrowRight'); // 第二下:目标列切到 in_review
  await page.keyboard.press('Enter');
  await expect(
    page
      .getByTestId('column-body-in_review')
      .locator('[data-testid^="board-card-"]')
      .filter({ hasText: '评审设计稿' }),
  ).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/desktop-light-02-board-keyboard-move.png` });

  // 4. List 布局(G7 必修:占位 → 真实表格)+ 视图切换数据竞态回归(验收必修 1):
  //    新视图必须经新视图 id 的投影请求收敛到正确数据;切换落定后旧视图 issues
  //    端点不得再被请求(在途旧响应一律被丢弃,不得覆盖新视图数据)。
  const oldViewId = page.url().split('/views/')[1];
  await page.getByTestId('view-create-open').click();
  await page.getByTestId('view-create-name').fill('全部议题(List)');
  await page.getByTestId('view-create-layout').selectOption('list');
  await page.getByTestId('view-create-submit').click();
  // 等待 URL 切到新视图 id,并以新视图投影响应(而非残留数据)为数据到位信号。
  await page.waitForURL(
    (url) =>
      url.toString().includes('/views/') && !url.toString().includes(oldViewId ?? '__none__'),
    { timeout: 20_000 },
  );
  const newViewId = page.url().split('/views/')[1];
  await page.waitForResponse(
    (resp) => resp.url().includes(`/views/${newViewId}/issues`) && resp.status() === 200,
    { timeout: 20_000 },
  );
  const listTable = page.locator('table').first();
  await expect(listTable).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText('联调接口').first()).toBeVisible();
  // 切换落定后监听:旧视图 issues 端点不得再被请求(实时/重载均不得回流旧视图)。
  if (oldViewId !== undefined) {
    const staleAfterSwitch: string[] = [];
    page.on('request', (req) => {
      if (req.url().includes(`/views/${oldViewId}/issues`)) staleAfterSwitch.push(req.url());
    });
    await page.waitForTimeout(2000);
    expect(
      staleAfterSwitch,
      `切换落定后旧视图 issues 端点仍被请求: ${staleAfterSwitch.join(', ')}`,
    ).toEqual([]);
  }
  await page.screenshot({ path: `${EVIDENCE_DIR}/desktop-light-03-list-layout.png` });

  // 4b. 虚拟化真实布局回归(验收必修 2 / kanban §5.3):经真实 API 批量造 ≥200 卡,
  //     回到看板视图:列体确定高度链生效 → 内滚成立(scrollHeight > clientHeight),
  //     窗口化只渲染一部分,滚动后渲染窗口迁移(首卡 posinset > 1)。
  // 真实 API 基址(与本配置的 webServer env VITE_MESH_API_BASE_URL 一致)。
  // 写端点限流 120/60s(issue/routes.ts WRITE_LIMIT):分两段(115 + 等待 62s + 95)
  // 创建 210 卡(≥200 虚拟化阈值),429 时按 Retry-After 退避重试。
  const API_BASE = 'http://127.0.0.1:8020';
  const bulk = await page.evaluate(async (base) => {
    const auth = JSON.parse(localStorage.getItem('mesh.auth.v1') ?? '{}') as {
      state?: { token?: string };
    };
    const token = auth.state?.token ?? '';
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
    const me = (await (await fetch(`${base}/api/v1/users/me`, { headers })).json()) as {
      data: { memberships: { workspace_id: string }[] };
    };
    const workspaceId = me.data.memberships[0].workspace_id;
    const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
    const createOne = async (i: number): Promise<number> => {
      for (let attempt = 0; attempt < 3; attempt++) {
        const resp = await fetch(`${base}/api/v1/workspaces/${workspaceId}/issues`, {
          method: 'POST',
          headers,
          body: JSON.stringify({ title: `批量卡 ${i}` }),
        });
        if (resp.status !== 429) return resp.status;
        const retryAfter = Number(resp.headers.get('retry-after') ?? '5');
        await sleep(Math.min(retryAfter, 62) * 1000 + 500);
      }
      return 429;
    };
    const statuses: number[] = [];
    for (let start = 0; start < 115; start += 15) {
      const batch = Array.from({ length: Math.min(15, 115 - start) }, (_, j) => start + j);
      statuses.push(...(await Promise.all(batch.map(createOne))));
    }
    await sleep(62_000); // 等限流窗口滑过
    for (let start = 115; start < 210; start += 15) {
      const batch = Array.from({ length: Math.min(15, 210 - start) }, (_, j) => start + j);
      statuses.push(...(await Promise.all(batch.map(createOne))));
    }
    const notCreated = statuses.filter((s) => s !== 201);
    return { ok: notCreated.length === 0, total: statuses.length, notCreated };
  }, API_BASE);
  expect(bulk, `批量建卡(210)应全部 201,异常状态: ${bulk.notCreated.join(',')}`).toEqual({
    ok: true,
    total: 210,
    notCreated: [],
  });
  if (oldViewId === undefined) throw new Error('board view id missing');
  await page.goto(`/views/${oldViewId}`);
  const virtual = page.getByTestId('virtual-column-body').first();
  await expect(virtual).toBeVisible({ timeout: 30_000 });
  const metrics = await virtual.evaluate((el) => ({
    scrollHeight: el.scrollHeight,
    clientHeight: el.clientHeight,
    rendered: el.querySelectorAll('[role="listitem"]').length,
    setsize: Number(el.querySelector('[role="listitem"]')?.getAttribute('aria-setsize') ?? '0'),
  }));
  expect(metrics.setsize).toBeGreaterThanOrEqual(200);
  expect(metrics.rendered).toBeLessThan(metrics.setsize);
  expect(metrics.scrollHeight).toBeGreaterThan(metrics.clientHeight);
  await virtual.evaluate((el) => {
    el.scrollTop = 3000;
    el.dispatchEvent(new Event('scroll', { bubbles: true }));
  });
  await expect
    .poll(
      () =>
        virtual.evaluate((el) =>
          Number(el.querySelector('[role="listitem"]')?.getAttribute('aria-posinset') ?? '1'),
        ),
      { timeout: 10_000 },
    )
    .toBeGreaterThan(1);
  await page.screenshot({ path: `${EVIDENCE_DIR}/desktop-light-03b-virtualization.png` });

  // 5. Issue 列表 DataView(标题栏/过滤 chips/表头/批量条/键盘选择)
  await page.goto('/issues');
  await expect(page.getByTestId('data-view')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('issue-table')).toBeVisible();
  await page.getByTestId('issue-select-all').check();
  await expect(page.getByTestId('bulk-bar')).toBeVisible();
  await page.screenshot({ path: `${EVIDENCE_DIR}/desktop-light-04-issues-bulkbar.png` });
  // 行主操作:标题链接进详情
  await page.locator('[data-testid^="issue-row-"]').first().getByRole('link').first().click();
  await expect(page.getByTestId('issue-detail')).toBeVisible({ timeout: 20_000 });

  // 6. 详情 DetailLayout(summary chips + 内联标题 + 活动/评论 Tab + 保存态)
  await expect(page.getByTestId('issue-chip-status')).toBeVisible();
  const titleInput = page.getByTestId('issue-detail-title');
  await titleInput.fill('联调接口(改名)');
  await titleInput.press('Enter');
  await expect(page.getByTestId('issue-save-indicator')).toBeVisible();
  await page.getByRole('tab', { name: 'Activity' }).click();
  await expect(page.getByTestId('issue-detail-activity')).toBeVisible();
  await page.getByRole('tab', { name: 'Comments' }).click();
  await page.screenshot({ path: `${EVIDENCE_DIR}/desktop-light-05-detail-tabs.png` });

  // 7. 评论草稿自动保存 + 失败重试(§9.5.1/§9.5.4)
  const composer = page.getByTestId('composer-input');
  await composer.fill('来自 e2e 的第一条评论');
  await expect(page.getByTestId('draft-status')).toContainText(/Draft saved|草稿已保存/, {
    timeout: 8_000,
  });
  // 注入一次提交失败:中断 POST /comments 一次
  let aborted = false;
  await page.route('**/comments', async (route) => {
    if (!aborted && route.request().method() === 'POST') {
      aborted = true;
      await route.abort();
    } else {
      await route.continue();
    }
  });
  await page.getByTestId('composer-submit').click();
  await expect(page.getByTestId('composer-error')).toBeVisible({ timeout: 15_000 });
  // 失败保留正文(§9.5.4)
  await expect(composer).toHaveValue('来自 e2e 的第一条评论');
  await page.getByTestId('composer-retry').click();
  await expect(page.getByText('来自 e2e 的第一条评论').first()).toBeVisible({ timeout: 20_000 });
  await page.unroute('**/comments');
  await page.screenshot({ path: `${EVIDENCE_DIR}/desktop-light-06-comment-retry.png` });

  // 8. 删除短时撤销(§9.5.5)
  const commentCard = page
    .locator('[data-testid^="comment-card-"]')
    .filter({ hasText: '来自 e2e 的第一条评论' });
  const deleteButton = commentCard.locator('[data-testid^="comment-delete-"]');
  await commentCard.hover();
  await deleteButton.click();
  const undoButton = page.locator('.mesh-toast__action');
  await expect(undoButton).toBeVisible({ timeout: 10_000 });
  await undoButton.click();
  await expect(commentCard).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/desktop-light-07-comment-undo.png` });

  // 9. 附件上传失败重试(§3.2 附件行 + parity §2.22)
  let uploadAborted = false;
  await page.route('**/upload-requests**', async (route) => {
    if (!uploadAborted && route.request().method() === 'POST') {
      uploadAborted = true;
      await route.abort();
    } else {
      await route.continue();
    }
  });
  const fileChooserPromise = page.waitForEvent('filechooser');
  await page.getByTestId('attachment-paperclip').click();
  const chooser = await fileChooserPromise;
  await chooser.setFiles(UPLOAD_FILE);
  const retryButton = page.locator('[data-testid^="upload-retry-"]').first();
  await expect(retryButton).toBeVisible({ timeout: 15_000 });
  await page.unroute('**/upload-requests**');
  await retryButton.click();
  await expect(page.locator('[data-testid^="upload-error-"]')).toHaveCount(0, { timeout: 30_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/desktop-light-08-attachment-retry.png` });

  // 10. 灯箱(触控工具栏基础:缩放/旋转/下载按钮在场)
  const thumb = page.locator('[data-testid^="attachment-"]').locator('img').first();
  if ((await thumb.count()) > 0) {
    await thumb.click();
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 15_000 });
    await page.screenshot({ path: `${EVIDENCE_DIR}/desktop-light-09-lightbox.png` });
    await page.keyboard.press('Escape');
  }

  // 11. 暗色主题存证(协商链经 mesh.settings.v1 预置)
  await applyTheme(page, 'dark');
  await page.goto('/board');
  await expect(page.getByTestId('board-columns-wrap')).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/desktop-dark-01-board.png` });
  await page.goto('/issues');
  await expect(page.getByTestId('issue-table')).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/desktop-dark-02-issues.png` });
  await page.locator('[data-testid^="issue-row-"]').first().getByRole('link').first().click();
  await expect(page.getByTestId('issue-detail')).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/desktop-dark-03-detail.png` });

  // 控制台无应用错误(资源 404/favicon 噪音,与本用例故意注入的请求中断
  // ERR_FAILED 除外)。断言消息附带非 2xx 响应清单以定位偶发 409 来源(建议项 3)。
  const appErrors = consoleErrors.filter(
    (text) => !text.includes('favicon') && !text.includes('404') && !text.includes('ERR_FAILED'),
  );
  expect(
    appErrors,
    `console errors: ${appErrors.join(' | ')}; non-2xx responses: ${failedResponses.join(' | ')}`,
  ).toEqual([]);
});

test('批次②手机走查:紧凑看板/长按移动/属性抽屉 + 亮暗存证', async ({ page }, testInfo) => {
  if (testInfo.project.name !== 'mobile') {
    test.skip(true, 'desktop 走查在上一用例');
  }

  await registerAndLogin(page, emailFor('mobile'));
  await createWorkspace(page);
  await createBoardView(page, '手机看板');

  // 1. 紧凑模式(§8.3:单泳道 + chips 切列)
  await expect(page.getByTestId('board-compact')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('compact-chip-todo')).toBeVisible();
  await page.getByTestId('compact-chip-todo').click();
  await quickAdd(page, 'todo', '手机卡片');
  await page.getByTestId('compact-chip-in_progress').click();
  await expect(page.getByTestId('board-column-in_progress')).toBeVisible();
  await page.getByTestId('compact-chip-todo').click();
  await page.screenshot({ path: `${EVIDENCE_DIR}/mobile-light-01-compact-board.png` });

  // 2. 长按 → 目标列 sheet(§9.4.6:不依赖精细横向拖动)
  const cardLocator = page
    .getByTestId('column-body-todo')
    .locator('[data-testid^="board-card-"]')
    .filter({ hasText: '手机卡片' });
  const box = await cardLocator.boundingBox();
  if (box === null) throw new Error('card box missing');
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  await cardLocator.dispatchEvent('pointerdown', {
    clientX: cx,
    clientY: cy,
    button: 0,
    buttons: 1,
    pointerType: 'touch',
  });
  await page.waitForTimeout(500);
  await expect(page.getByTestId('board-touch-sheet')).toBeVisible({ timeout: 5_000 });
  await page.getByTestId('touch-column-done').click();
  // 紧凑模式当前列仍为 todo:卡片应已离开;切到 done 列确认落位。
  await expect(
    page
      .getByTestId('column-body-todo')
      .locator('[data-testid^="board-card-"]')
      .filter({ hasText: '手机卡片' }),
  ).toHaveCount(0, { timeout: 15_000 });
  await page.getByTestId('compact-chip-done').click();
  await expect(
    page
      .getByTestId('column-body-done')
      .locator('[data-testid^="board-card-"]')
      .filter({ hasText: '手机卡片' }),
  ).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/mobile-light-02-touch-move.png` });

  // 3. issue 详情:属性入底部抽屉(§8.3)
  await page.goto('/issues');
  await expect(page.getByTestId('data-view')).toBeVisible({ timeout: 20_000 });
  await page.locator('[data-testid^="issue-row-"]').first().getByRole('link').first().click();
  await expect(page.getByTestId('issue-detail')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('detail-summary-chips')).toBeVisible();
  await page.getByTestId('detail-aside-trigger').click();
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/mobile-light-03-properties-drawer.png` });
  await page.keyboard.press('Escape');

  // 4. 评论 + 撤销(手机路径)
  await page.getByRole('tab', { name: 'Comments' }).click();
  await page.getByTestId('composer-input').fill('手机评论');
  await page.getByTestId('composer-submit').click();
  await expect(page.getByText('手机评论').first()).toBeVisible({ timeout: 20_000 });
  // 等乐观本地卡被服务端副本替换(testid 前缀 local- 消失),避免卡片 rekey 抽走菜单
  await expect(page.locator('[data-testid^="comment-card-local-"]')).toHaveCount(0, {
    timeout: 20_000,
  });
  // 触控:次要操作收进常驻「更多」菜单(§9.5.6/§8.2)
  const commentCard = page
    .locator('[data-testid^="comment-card-"]')
    .filter({ hasText: '手机评论' });
  await commentCard.getByRole('button', { name: 'More actions' }).click();
  await page.getByRole('menuitem', { name: 'Delete' }).click();
  await page.locator('.mesh-toast__action').click();
  await expect(commentCard).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/mobile-light-04-comment-undo.png` });

  // 5. 暗色存证
  await applyTheme(page, 'dark');
  await page.goto('/board');
  await expect(page.getByTestId('board-compact')).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/mobile-dark-01-compact-board.png` });
  await page.goto('/issues');
  await expect(page.getByTestId('data-view')).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/mobile-dark-02-issues.png` });
  await page.locator('[data-testid^="issue-row-"]').first().getByRole('link').first().click();
  await expect(page.getByTestId('issue-detail')).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/mobile-dark-03-detail.png` });
});
