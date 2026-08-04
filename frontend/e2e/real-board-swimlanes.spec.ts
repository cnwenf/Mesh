/**
 * 二维看板真实浏览器验收：真实注册/工作区/API 数据、二维渲染、斜向指针移动、
 * 持久化、单元格 quick-create、亮暗主题、compact 泳道切换及离线状态。
 * 截图只写 test-results（gitignored），不把运行时账号或凭据持久化为证据。
 */
import { expect, test } from '@playwright/test';
import type { APIResponse, Page } from '@playwright/test';
import { randomBytes } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const RUN = `${Date.now()}-${randomBytes(4).toString('hex')}`;
const EMAIL = `mes130-${RUN}@example.com`;
const PASSWORD = `${randomBytes(24).toString('base64url')}Aa1!`;
const SLUG = `mes130-${RUN}`.slice(0, 48);
const API = process.env.MES130_API_BASE_URL ?? 'http://127.0.0.1:8100';
const EVIDENCE_DIR =
  process.env.MES130_EVIDENCE_DIR ?? resolve(HERE, '..', 'test-results', 'mes130-swimlanes');

async function expectOk(response: APIResponse, operation: string): Promise<void> {
  expect(response.ok(), `${operation}: ${response.status()} ${await response.text()}`).toBe(true);
}

async function registerAndCreateWorkspace(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').fill('MES130 Owner');
  await page.getByTestId('login-email').fill(EMAIL);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  await page.getByTestId('register-continue').click({ timeout: 30_000 });
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: 30_000 });

  await page.getByTestId('ws-switcher-button').click();
  await page.getByTestId('ws-switcher-create').click();
  await page.getByTestId('ws-wizard-name-input').fill('MES130 Swimlanes');
  await page.getByTestId('ws-wizard-next').click();
  await page.getByTestId('ws-wizard-slug-input').fill(SLUG);
  await page.waitForTimeout(700);
  await page.getByTestId('ws-wizard-next-slug').click();
  await page.getByTestId('ws-wizard-skip').click();
  await expect(page).toHaveURL(new RegExp(`/w/${SLUG}`), { timeout: 20_000 });
  await page.getByTestId('onboarding-dismiss').click();
  await expect(page.getByTestId('onboarding-card')).toHaveCount(0);
}

async function authToken(page: Page): Promise<string> {
  return page.evaluate(() => {
    const raw = window.localStorage.getItem('mesh.auth.v1');
    if (raw === null) return '';
    return (JSON.parse(raw) as { state?: { token?: string } }).state?.token ?? '';
  });
}

async function dragPointer(page: Page, cardId: string, targetTestId: string): Promise<void> {
  const source = page.getByTestId(`board-card-${cardId}`);
  const target = page.getByTestId(targetTestId);
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

test('二维泳道真实交互与视觉矩阵', async ({ page }) => {
  let token = '';
  let workspaceId = '';
  let offline = false;
  const consoleErrors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });

  try {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await registerAndCreateWorkspace(page);
    token = await authToken(page);
    expect(token).not.toBe('');
    const headers = { Authorization: `Bearer ${token}` };

    const me = await page.request.get(`${API}/api/v1/users/me`, { headers });
    await expectOk(me, 'read current workspace');
    workspaceId = (await me.json()).data.memberships[0].workspace_id as string;

    const members = await page.request.get(`${API}/api/v1/workspaces/${workspaceId}/members`, {
      headers,
    });
    await expectOk(members, 'list members');
    const owner = ((await members.json()).data as Array<{ id: string; member_type: string }>).find(
      (member) => member.member_type === 'human',
    );
    expect(owner).toBeDefined();
    const ownerId = owner!.id;

    const viewResponse = await page.request.post(`${API}/api/v1/workspaces/${workspaceId}/views`, {
      headers,
      data: {
        name: 'Delivery swimlanes',
        layout: 'board',
        visibility: 'shared',
        group_by: 'priority',
        sub_group_by: 'assignee',
        board_settings: { wip: { low: { limit: 1, enforcement: 'warn' } } },
      },
    });
    await expectOk(viewResponse, 'create swimlane view');
    const viewId = (await viewResponse.json()).data.id as string;

    const createIssue = async (title: string, priority: string, assigneeId?: string) => {
      const response = await page.request.post(`${API}/api/v1/workspaces/${workspaceId}/issues`, {
        headers,
        data: {
          title,
          priority,
          ...(assigneeId === undefined ? {} : { assignee_id: assigneeId }),
        },
      });
      await expectOk(response, `create ${title}`);
      return (await response.json()).data.id as string;
    };
    const diagonalCard = await createIssue('Diagonal move', 'high');
    await createIssue('Owned baseline', 'low', ownerId);
    await createIssue('Backlog context', 'medium');

    await page.goto(`/views/${viewId}`);
    await expect(page.getByTestId('board-swimlanes')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId(`board-card-${diagonalCard}`)).toBeVisible();
    await page.screenshot({ path: `${EVIDENCE_DIR}/01-desktop-light.png`, fullPage: true });

    await dragPointer(page, diagonalCard, `column-body-${ownerId}-low`);
    const targetCell = page.getByTestId(`board-column-${ownerId}-low`);
    await expect(targetCell.getByTestId(`board-card-${diagonalCard}`)).toBeVisible({
      timeout: 15_000,
    });
    await page.reload();
    await expect(targetCell.getByTestId(`board-card-${diagonalCard}`)).toBeVisible({
      timeout: 20_000,
    });
    await expect(targetCell.getByTestId('wip-badge-low')).toHaveText('2/1');

    const quickCreate = page.getByTestId(`quick-add-${ownerId}-low`);
    await quickCreate.fill('Cell quick create');
    await quickCreate.press('Enter');
    await expect(targetCell.getByText('Cell quick create')).toBeVisible({ timeout: 15_000 });

    await page.goto('/settings/appearance');
    await page.getByTestId('theme-select').selectOption('dark');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.goto(`/views/${viewId}`);
    await expect(page.getByTestId('board-swimlanes')).toBeVisible({ timeout: 20_000 });
    await page.screenshot({ path: `${EVIDENCE_DIR}/02-desktop-dark-wip.png`, fullPage: true });

    await page.goto('/settings/appearance');
    await page.getByTestId('theme-select').selectOption('light');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/views/${viewId}`);
    await expect(page.getByTestId('board-swimlanes')).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('.mesh-board__lane-tabs')).toBeVisible();
    await page.getByTestId('compact-chip-low').click();
    await expect(targetCell.getByTestId(`board-card-${diagonalCard}`)).toBeVisible();
    await expect(targetCell.getByText('Cell quick create')).toBeVisible();
    await expect(targetCell.locator('.mesh-board__column-name')).toBeInViewport();
    await expect(targetCell.getByText('Diagonal move')).toBeInViewport();
    await page.screenshot({ path: `${EVIDENCE_DIR}/03-mobile-light.png`, fullPage: true });

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.context().setOffline(true);
    offline = true;
    const statusBanner = page
      .getByTestId('status-banner-offline')
      .or(page.getByTestId('status-banner-resyncing'));
    await expect(statusBanner).toBeVisible({ timeout: 20_000 });
    await page.screenshot({ path: `${EVIDENCE_DIR}/04-offline-state.png`, fullPage: true });
    await page.context().setOffline(false);
    offline = false;

    const unexpected = consoleErrors.filter(
      (line) =>
        !line.includes('Failed to load resource') &&
        !line.includes('WebSocket') &&
        !line.includes('ERR_INTERNET_DISCONNECTED'),
    );
    expect(unexpected, unexpected.join('\n')).toEqual([]);
  } finally {
    if (offline) await page.context().setOffline(false);
    if (token !== '' && workspaceId !== '') {
      await page.request.delete(`${API}/api/v1/workspaces/${workspaceId}`, {
        headers: { Authorization: `Bearer ${token}` },
        data: { confirm_slug: SLUG },
      });
    }
  }
});
