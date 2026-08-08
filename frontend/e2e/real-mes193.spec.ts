/**
 * MES-193 真实环境验收:看板页默认多状态分列 + 看板/泳道/列表三视图直切。
 *
 * 真实注册 → 建区 → /board 自动落一个默认共享看板视图(打开即多状态分列,
 * 不再是引导空态)→ 建 issue → 拖拽改状态(持久化)→ 三视图同页直切:
 * 切泳道(PATCH sub_group_by)→ 切列表(PATCH layout=list)→ 切回看板
 * (清空 sub_group_by),每次切换经 GET /views 校验配置真实落库,且筛选分组等
 * 状态保留;刷新后模式仍持久。截图存证 docs/evidence/mes-193。
 * 无路由拦截、无 fixture 服务:全部请求穿过 nginx 前门打真实
 * FastAPI/PostgreSQL/Redis/MinIO/worker/gateway。
 */
import { mkdir } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { Page, TestInfo } from '@playwright/test';

const HERE = dirname(fileURLToPath(import.meta.url));
const EVIDENCE_DIR = resolve(HERE, '../../docs/evidence/mes-193');
const RESPONSE_TIMEOUT = 30_000;
const RUN = String(Date.now()).slice(-7);
const EMAIL = `mes193-${RUN}@corp.example`;
const PASSWORD = 'Mesh-Mes193-Passw0rd!';
const SLUG = `mes193-${RUN}`;

interface ViewRecord {
  readonly id: string;
  readonly name: string;
  readonly layout: string;
  readonly group_by: string | null;
  readonly sub_group_by: string | null;
  readonly visibility: string;
  readonly is_default: boolean;
}

async function capture(page: Page, testInfo: TestInfo, name: string): Promise<void> {
  await mkdir(EVIDENCE_DIR, { recursive: true });
  await page.screenshot({
    path: join(EVIDENCE_DIR, `${testInfo.project.name}-${name}.png`),
    fullPage: true,
  });
}

async function registerAndCreateWorkspace(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').fill('MES193 Owner');
  await page.getByTestId('login-email').fill(EMAIL);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  await page.getByTestId('register-continue').click({ timeout: RESPONSE_TIMEOUT });
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: RESPONSE_TIMEOUT });

  await page.getByTestId('ws-switcher-button').click();
  await page.getByTestId('ws-switcher-create').click();
  await page.getByTestId('ws-wizard-name-input').fill('MES193 Board');
  await page.getByTestId('ws-wizard-next').click();
  await page.getByTestId('ws-wizard-slug-input').fill(SLUG);
  await page.waitForTimeout(700);
  await page.getByTestId('ws-wizard-next-slug').click();
  await page.getByTestId('ws-wizard-skip').click();
  await expect(page).toHaveURL(new RegExp(`/w/${SLUG}`), { timeout: 20_000 });
}

async function dismissOnboardingChecklist(page: Page): Promise<void> {
  const dismiss = page.getByTestId('onboarding-dismiss');
  try {
    // The checklist loads async after the shell; give it a moment to appear.
    await dismiss.waitFor({ state: 'visible', timeout: 10_000 });
  } catch {
    return; // already dismissed or not rendered on this page
  }
  await dismiss.click();
  await expect(page.getByTestId('onboarding-card')).toHaveCount(0);
}

async function setTheme(page: Page, theme: 'light' | 'dark'): Promise<void> {
  await page.goto('/settings/appearance');
  await page.getByTestId('theme-select').selectOption(theme);
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme, {
    timeout: RESPONSE_TIMEOUT,
  });
}

async function authToken(page: Page): Promise<string> {
  return page.evaluate(() => {
    const raw = window.localStorage.getItem('mesh.auth.v1');
    if (raw === null) return '';
    return (JSON.parse(raw) as { state?: { token?: string } }).state?.token ?? '';
  });
}

/** 经前端同源代理读取视图列表(真实落库校验)。 */
async function listViews(
  page: Page,
  token: string,
  workspaceId: string,
): Promise<readonly ViewRecord[]> {
  const response = await page.request.get(`/api/v1/workspaces/${workspaceId}/views?limit=100`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.ok(), `list views: ${response.status()}`).toBe(true);
  return ((await response.json()).data as ViewRecord[]) ?? [];
}

async function dragCard(page: Page, cardId: string, targetBodyTestId: string): Promise<void> {
  const source = page.getByTestId(`board-card-${cardId}`);
  const target = page.getByTestId(targetBodyTestId);
  await source.scrollIntoViewIfNeeded();
  await target.scrollIntoViewIfNeeded();
  const sourceBox = await source.boundingBox();
  const targetBox = await target.boundingBox();
  expect(sourceBox).not.toBeNull();
  expect(targetBox).not.toBeNull();
  await page.mouse.move(sourceBox!.x + sourceBox!.width / 2, sourceBox!.y + sourceBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(targetBox!.x + targetBox!.width / 2, targetBox!.y + 40, { steps: 14 });
  await expect(page.getByTestId('board-drag-clone')).toBeVisible();
  await page.mouse.up();
}

/** 看板模式渲染断言:桌面为多状态分列网格,手机为紧凑单列 + 列 chips。 */
async function expectBoardColumnsRendered(page: Page, isPhone: boolean): Promise<void> {
  const keys = ['todo', 'in_progress', 'in_review', 'done'];
  if (isPhone) {
    await expect(page.getByTestId('board-compact')).toBeVisible({ timeout: RESPONSE_TIMEOUT });
    for (const key of keys) {
      await expect(page.getByTestId(`compact-chip-${key}`)).toBeVisible();
    }
    return;
  }
  await expect(page.getByTestId('board-columns')).toBeVisible({ timeout: RESPONSE_TIMEOUT });
  for (const key of keys) {
    await expect(page.getByTestId(`board-column-${key}`)).toBeVisible();
  }
}

test('默认多状态分列看板 + 三视图直切真实走查', async ({ page }, testInfo) => {
  const isPhone = testInfo.project.name.startsWith('phone');
  const theme = testInfo.project.name.endsWith('dark') ? 'dark' : 'light';
  const consoleErrors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });

  await registerAndCreateWorkspace(page);
  await dismissOnboardingChecklist(page);
  await setTheme(page, theme);
  const token = await authToken(page);
  expect(token.length).toBeGreaterThan(0);

  const me = await page.request.get('/api/v1/users/me', {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(me.ok()).toBe(true);
  const workspaceId = ((await me.json()).data.memberships as Array<{ workspace_id: string }>)[0]
    .workspace_id;

  // 1. 打开 /board:无保存视图 → 自动播种默认共享看板视图,直接呈现多状态分列,
  //    不再是「The board is empty」引导空态。
  await page.goto(`/w/${SLUG}/board`);
  await dismissOnboardingChecklist(page);
  await expectBoardColumnsRendered(page, isPhone);
  expect(await page.getByText('The board is empty').count()).toBe(0);
  // 默认视图配置真实落库:共享 + board 布局 + 按状态类别分组
  const seededViews = await listViews(page, token, workspaceId);
  expect(seededViews.length).toBeGreaterThan(0);
  const seededBoard = seededViews.find((view) => view.layout === 'board');
  expect(seededBoard, JSON.stringify(seededViews)).toBeDefined();
  expect(seededBoard!.group_by).toBe('state_category');
  expect(seededBoard!.sub_group_by).toBeNull();
  expect(seededBoard!.visibility).toBe('shared');
  expect(seededBoard!.is_default).toBe(true);
  // 三视图切换器呈现,看板为当前模式
  await expect(page.getByTestId('view-mode-switcher')).toBeVisible();
  await expect(page.getByTestId('view-mode-board')).toHaveAttribute('aria-pressed', 'true');
  await capture(page, testInfo, '01-default-multi-status-board');

  // 2. 建两张 issue(真实 API),看板列即时呈现卡片。
  const createIssue = async (title: string): Promise<string> => {
    const response = await page.request.post(`/api/v1/workspaces/${workspaceId}/issues`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { title },
    });
    expect(response.ok(), `create ${title}: ${response.status()}`).toBe(true);
    return ((await response.json()).data.id as string) ?? '';
  };
  const movedCard = await createIssue('MES-193 drag me');
  await createIssue('MES-193 stays in todo');
  if (isPhone) {
    // 手机紧凑模式一次只渲染一列,默认停在 Backlog;先切到 Todo 列再等卡片。
    await page.getByTestId('compact-chip-todo').click();
  }
  await expect(page.getByTestId(`board-card-${movedCard}`)).toBeVisible({
    timeout: RESPONSE_TIMEOUT,
  });

  // 3. 拖拽改状态:todo → in_progress,刷新后仍持久。
  if (!isPhone) {
    await dragCard(page, movedCard, 'column-body-in_progress');
    await expect(
      page.getByTestId('board-column-in_progress').getByTestId(`board-card-${movedCard}`),
    ).toBeVisible({ timeout: RESPONSE_TIMEOUT });
    await page.reload();
    await expect(
      page.getByTestId('board-column-in_progress').getByTestId(`board-card-${movedCard}`),
    ).toBeVisible({ timeout: RESPONSE_TIMEOUT });
    await capture(page, testInfo, '02-drag-moved-status');
  }

  const viewId = seededBoard!.id;

  // 4. 切泳道:同页直切,sub_group_by 落库(默认 priority 轴),一级分组保留。
  await page.getByTestId('view-mode-swimlane').click();
  await expect(page.getByTestId('board-swimlanes')).toBeVisible({ timeout: RESPONSE_TIMEOUT });
  await expect(page.getByTestId('view-mode-swimlane')).toHaveAttribute('aria-pressed', 'true');
  await page.waitForTimeout(600);
  const swimlaneViews = await listViews(page, token, workspaceId);
  const swimlaneView = swimlaneViews.find((view) => view.id === viewId);
  expect(swimlaneView!.layout).toBe('board');
  expect(swimlaneView!.sub_group_by).toBe('priority');
  expect(swimlaneView!.group_by).toBe('state_category');
  await capture(page, testInfo, '03-swimlane-mode');

  // 5. 切列表:layout=list 落库,sub_group_by 保留(列表忽略二级轴)。
  await page.getByTestId('view-mode-list').click();
  await expect(page.getByTestId('board-list-view')).toBeVisible({ timeout: RESPONSE_TIMEOUT });
  await expect(page.getByTestId('view-mode-list')).toHaveAttribute('aria-pressed', 'true');
  await page.waitForTimeout(600);
  const listViewsAfter = await listViews(page, token, workspaceId);
  const listView = listViewsAfter.find((view) => view.id === viewId);
  expect(listView!.layout).toBe('list');
  expect(listView!.group_by).toBe('state_category');
  await capture(page, testInfo, '04-list-mode');

  // 6. 切回看板:layout=board 且清空 sub_group_by,回到多状态分列。
  await page.getByTestId('view-mode-board').click();
  await expectBoardColumnsRendered(page, isPhone);
  await expect(page.getByTestId('view-mode-board')).toHaveAttribute('aria-pressed', 'true');
  await page.waitForTimeout(600);
  const boardViewsAfter = await listViews(page, token, workspaceId);
  const boardView = boardViewsAfter.find((view) => view.id === viewId);
  expect(boardView!.layout).toBe('board');
  expect(boardView!.sub_group_by).toBeNull();
  expect(boardView!.group_by).toBe('state_category');

  // 7. 刷新后模式持久:仍是看板多状态分列。
  await page.reload();
  await expectBoardColumnsRendered(page, isPhone);
  await expect(page.getByTestId('view-mode-board')).toHaveAttribute('aria-pressed', 'true');
  await capture(page, testInfo, '05-board-mode-persisted');

  const unexpected = consoleErrors.filter(
    (line) =>
      !line.includes('Failed to load resource') &&
      !line.includes('WebSocket') &&
      !line.includes('ERR_'),
  );
  expect(unexpected, unexpected.join('\n')).toEqual([]);
});
