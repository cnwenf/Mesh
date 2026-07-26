/**
 * 真实后端看板投影层浏览器走查(MES-33 §4 验收):注册/登录 → 建区 → REST 播种
 * issue 与视图 → 真实卡片渲染 → 跨列拖拽(乐观落位 + 服务端持久化)→ 列底快速创建
 * → WIP block 已满列禁用落点 → 断线「网络已断开」横幅与恢复(§6.12)。每步截图存证。
 *
 * 前置:MES-33 后端栈运行中(API 8100 / gateway 8181 / worker / 库 mesh_mes33,
 * redis db4,MESH_AUTH_MODE=dev),dev server 在 5274。
 */
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const RUN = String(Date.now()).slice(-7);
const EMAIL = `boardproj-${RUN}@corp.example`;
const PASSWORD = 'secret123';
const SLUG = `boardproj${RUN}`;
const EVIDENCE_DIR = process.env.MES33_EVIDENCE_DIR ?? resolve(HERE, 'evidence', 'board-projection');
const API = 'http://127.0.0.1:8100';

test.describe.configure({ mode: 'serial' });

async function registerAndLogin(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').fill('Board Proj Owner');
  await page.getByTestId('login-email').fill(EMAIL);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  await page.getByTestId('register-continue').click({ timeout: 30_000 });
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: 30_000 });
}

async function createWorkspace(page: Page): Promise<void> {
  await page.getByTestId('ws-switcher-button').click();
  await page.getByTestId('ws-switcher-create').click();
  await page.getByTestId('ws-wizard-name-input').fill('Board Proj Walkthrough');
  await page.getByTestId('ws-wizard-next').click();
  await page.getByTestId('ws-wizard-slug-input').fill(SLUG);
  await page.waitForTimeout(800);
  await page.getByTestId('ws-wizard-next-slug').click();
  await page.waitForTimeout(500);
  await page.getByTestId('ws-wizard-skip').click();
  await expect(page).toHaveURL(new RegExp(`/w/${SLUG}`), { timeout: 15_000 });
}

async function getAuthToken(page: Page): Promise<string> {
  return page.evaluate(() => {
    const raw = window.localStorage.getItem('mesh.auth.v1');
    if (raw === null) return '';
    return (JSON.parse(raw) as { state?: { token?: string } }).state?.token ?? '';
  });
}

/** 用真实 DataTransfer 触发 HTML5 拖拽(dragstart → dragover → drop)。 */
async function dragCardToColumn(page: Page, cardId: string, columnKey: string): Promise<void> {
  await page.evaluate(
    ([cid, key]) => {
      const card = document.querySelector(`[data-testid="board-card-${cid}"]`);
      const body = document.querySelector(`[data-testid="column-body-${key}"]`);
      if (card === null || body === null) throw new Error('drag target missing');
      const dt = new DataTransfer();
      card.dispatchEvent(new DragEvent('dragstart', { dataTransfer: dt, bubbles: true }));
      body.dispatchEvent(
        new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true }),
      );
      body.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true }));
    },
    [cardId, columnKey],
  );
}

test('看板投影层真实走查 + 截图存证', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  await registerAndLogin(page);
  await createWorkspace(page);
  const token = await getAuthToken(page);
  expect(token).not.toBe('');
  const auth = { Authorization: `Bearer ${token}` };

  // 工作区 id(经 /users/me)。
  const me = await page.request.get(`${API}/api/v1/users/me`, { headers: auth });
  const wsId = (await me.json()).data.memberships[0].workspace_id as string;

  // 视图(WIP:in_progress 上限 1 / block)+ 3 个 issue(默认 todo 列)。
  const viewResp = await page.request.post(`${API}/api/v1/workspaces/${wsId}/views`, {
    headers: auth,
    data: {
      name: '投影走查看板',
      layout: 'board',
      visibility: 'shared',
      group_by: 'state_category',
      board_settings: { wip: { in_progress: { limit: 1, enforcement: 'block' } } },
    },
  });
  const viewId = (await viewResp.json()).data.id as string;
  const issueIds: string[] = [];
  for (const title of ['卡片 A', '卡片 B', '卡片 C']) {
    const r = await page.request.post(`${API}/api/v1/workspaces/${wsId}/issues`, {
      headers: auth,
      data: { title },
    });
    issueIds.push((await r.json()).data.id as string);
  }
  const [cardA, cardB] = issueIds;

  // 1. 打开视图 → 真实卡片渲染到 todo 列。
  await page.goto(`/views/${viewId}`);
  await expect(page.getByTestId('board-columns')).toBeVisible({ timeout: 20_000 });
  for (const id of issueIds) {
    await expect(page.getByTestId(`board-card-${id}`)).toBeVisible();
  }
  await expect(page.getByTestId('count-todo')).toHaveText('3');
  await page.screenshot({ path: `${EVIDENCE_DIR}/01-cards-rendered.png` });

  // 2. 跨列拖拽 A → in_progress(乐观落位)。
  await dragCardToColumn(page, cardA, 'in_progress');
  await expect(page.getByTestId('board-card-' + cardA)).toBeVisible();
  const inProgressCol = page.getByTestId('board-column-in_progress');
  await expect(inProgressCol.getByTestId('board-card-' + cardA)).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/02-dragged-to-in-progress.png` });

  // 3. 刷新页面 → 拖拽已持久化(A 仍在 in_progress,服务端真实落库)。
  await page.reload();
  await expect(page.getByTestId('board-columns')).toBeVisible({ timeout: 20_000 });
  await expect(
    page.getByTestId('board-column-in_progress').getByTestId('board-card-' + cardA),
  ).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId('count-in_progress')).toHaveText('1');
  await expect(page.getByTestId('wip-badge-in_progress')).toHaveText('1/1');
  await page.screenshot({ path: `${EVIDENCE_DIR}/03-drag-persisted.png` });

  // 4. 列底快速创建(回车)→ todo 列出现新卡。
  const quickAdd = page.getByTestId('quick-add-todo');
  await quickAdd.fill('快速创建卡片');
  await quickAdd.press('Enter');
  await expect(page.getByText('快速创建卡片')).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/04-quick-create.png` });

  // 5. WIP block 已满列(in_progress 1/1)→ 拖 B 被禁用落点,B 仍在 todo(§4.4)。
  // 先经 REST 再建一张卡,使本步 todo 计数(4)与上一步(3)不同,存证截图可与 04 区分
  // (blocked 拖拽本身不改变板面,需借计数差异证明「尝试拖拽但被拦」的真实状态)。
  const extraResp = await page.request.post(`${API}/api/v1/workspaces/${wsId}/issues`, {
    headers: auth,
    data: { title: '阻塞验证卡' },
  });
  const cardExtra = (await extraResp.json()).data.id as string;
  await page.reload();
  await expect(page.getByTestId('board-columns')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('board-card-' + cardExtra)).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId('count-todo')).toHaveText('4');
  await dragCardToColumn(page, cardB, 'in_progress');
  // block 拒收 → 卡片弹回原列 + 顶部 danger toast(§4.4)。
  await expect(page.locator('.mesh-toast--danger')).toBeVisible({ timeout: 10_000 });
  await expect(
    page.getByTestId('board-column-todo').getByTestId('board-card-' + cardB),
  ).toBeVisible();
  await expect(
    page.getByTestId('board-column-in_progress').getByTestId('board-card-' + cardB),
  ).toHaveCount(0);
  // 与 04 的视觉差异:todo 计数 4(04 为 3)+ in_progress 满载徽标 1/1 + 拒收 toast。
  await expect(page.getByTestId('count-todo')).toHaveText('4');
  await expect(page.getByTestId('wip-badge-in_progress')).toHaveText('1/1');
  await page.screenshot({ path: `${EVIDENCE_DIR}/05-wip-blocked.png` });

  // 6. 断线 → 「网络已断开 / 正在重新同步」横幅;恢复后消失(§6.12 offline/reconnect)。
  const connBanner = page
    .getByTestId('status-banner-offline')
    .or(page.getByTestId('status-banner-resyncing'));
  await page.context().setOffline(true);
  await expect(connBanner).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/06-offline-banner.png` });
  await page.context().setOffline(false);
  await expect(connBanner).toBeHidden({ timeout: 20_000 });

  // 控制台无意外应用错误(过滤浏览器级网络噪声:favicon 404、WebSocket 断连/重连)。
  const unexpected = consoleErrors.filter(
    (line) =>
      !line.includes('Failed to load resource') &&
      !line.includes('WebSocket') &&
      !line.includes('favicon') &&
      !line.includes('ws://'),
  );
  expect(unexpected, unexpected.join('\n')).toEqual([]);
});
