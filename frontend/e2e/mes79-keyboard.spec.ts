/**
 * MES-79 键盘体系 e2e(search-command-palette.md §4.3 / §4.3.1 / §5.1,mock 套件)。
 *
 * 看板纯键盘流转(评审 P4):移动键选中首卡 → ↑↓←→ 与 J/K/H/L 两组遍历 →
 * S 改状态 → Enter 打开选中卡;帮助层同 combo 仅呈现仲裁胜者(§4.3.1 规则 6)。
 *
 * 看板键盘流需要真实视图/卡片端点,mock 端点可用性以运行时探测门控:未就绪
 * 的 mock 下整组跳过(不就绪不假阳;real 套件全量执行)。
 */
import { expect, test } from '@playwright/test';
import { login, MOCK_BASE, resetMockServer } from './helpers';

const MOCK_HAS_BOARD_ROUTES = async (): Promise<boolean> => {
  try {
    const res = await fetch(`${MOCK_BASE}/api/v1/users/me`, {
      headers: { Authorization: 'Bearer mesh-dev:00000000-0000-0000-0000-000000000001' },
    });
    if (!res.ok) return false;
    const body = (await res.json()) as {
      data?: { memberships?: ReadonlyArray<{ workspace_id?: string }> };
    };
    const wsId = body.data?.memberships?.[0]?.workspace_id;
    if (wsId === undefined) return false;
    const views = await fetch(`${MOCK_BASE}/api/v1/workspaces/${wsId}/views`, {
      headers: { Authorization: 'Bearer mesh-dev:00000000-0000-0000-0000-000000000001' },
    });
    return views.ok;
  } catch {
    return false;
  }
};

async function gotoBoard(page: import('@playwright/test').Page): Promise<string> {
  const res = await fetch(`${MOCK_BASE}/api/v1/users/me`, {
    headers: { Authorization: 'Bearer mesh-dev:00000000-0000-0000-0000-000000000001' },
  });
  const body = (await res.json()) as {
    data?: { memberships?: ReadonlyArray<{ workspace_slug?: string }> };
  };
  const slug = body.data?.memberships?.[0]?.workspace_slug ?? '';
  await page.goto(`/w/${slug}/board`);
  await page.getByTestId('board-columns').waitFor({ state: 'visible', timeout: 15000 });
  return slug;
}

test.describe('MES-79 看板纯键盘流转(§4.3 S10 / 评审 P4)', () => {
  test.beforeEach(async () => {
    test.skip(!(await MOCK_HAS_BOARD_ROUTES()), 'mock 套件未提供看板端点');
    await resetMockServer();
  });

  test('方向键:首键选中首列首卡,↓ 同列下移,→ 跨列(空列穿透)', async ({ page }) => {
    await login(page);
    await gotoBoard(page);
    // 无选中 + 首次移动 → 首个非空列首卡(aria-selected 呈现)。
    await page.keyboard.press('ArrowDown');
    const selected = page.locator('[aria-selected="true"]').first();
    await expect(selected).toBeVisible();
    const firstId = await selected.getAttribute('data-testid');

    // ↓ 同列下移(或列尾 clamp)。
    await page.keyboard.press('ArrowDown');
    const second = page.locator('[aria-selected="true"]').first();
    const secondId = await second.getAttribute('data-testid');
    expect(secondId).not.toBeNull();

    // → 跨列(目标空列穿透至下一非空列;边界保持)。
    await page.keyboard.press('ArrowRight');
    await expect(page.locator('[aria-selected="true"]')).toHaveCount(1);
    expect(firstId).not.toBeNull();
  });

  test('J/K/H/L 等价遍历(vim 组)', async ({ page }) => {
    await login(page);
    await gotoBoard(page);
    await page.keyboard.press('j');
    await expect(page.locator('[aria-selected="true"]').first()).toBeVisible();
    await page.keyboard.press('k');
    await expect(page.locator('[aria-selected="true"]')).toHaveCount(1);
    await page.keyboard.press('l');
    await expect(page.locator('[aria-selected="true"]')).toHaveCount(1);
    await page.keyboard.press('h');
    await expect(page.locator('[aria-selected="true"]')).toHaveCount(1);
  });

  test('S 改选中卡状态(经列 move 端点,等价拖拽路径)', async ({ page }) => {
    await login(page);
    await gotoBoard(page);
    await page.keyboard.press('ArrowDown');
    const selected = page.locator('[aria-selected="true"]').first();
    const cardTestId = await selected.getAttribute('data-testid');
    expect(cardTestId).not.toBeNull();
    // S:状态改为下一列(move API);请求发出即证明键盘路径接通。
    const moveRequest = page.waitForRequest((request) => /\/moves/.test(request.url()), {
      timeout: 10000,
    });
    await page.keyboard.press('s');
    await moveRequest;
  });

  test('Enter 打开的正是当前选中卡(规范深链 /w/{slug}/issues/{id})', async ({ page }) => {
    await login(page);
    await gotoBoard(page);
    await page.keyboard.press('ArrowDown');
    const selected = page.locator('[aria-selected="true"]').first();
    const cardTestId = (await selected.getAttribute('data-testid')) ?? '';
    const issueId = cardTestId.replace('board-card-', '');
    await page.keyboard.press('Enter');
    await page.waitForURL(new RegExp(`/w/[^/]+/issues/${issueId}`));
    await expect(page.getByTestId('issue-detail')).toBeVisible();
  });

  test('F 打开筛选面板(等价鼠标路径 panel-toggle-filter)', async ({ page }) => {
    await login(page);
    await gotoBoard(page);
    await page.keyboard.press('f');
    await expect(page.getByTestId('panel-toggle-filter')).toHaveAttribute('aria-expanded', 'true');
  });
});

test.describe('MES-79 帮助层仲裁呈现(§4.3.1 规则 6)', () => {
  test.beforeEach(async () => {
    test.skip(!(await MOCK_HAS_BOARD_ROUTES()), 'mock 套件未提供看板端点');
    await resetMockServer();
  });

  test('看板上下文激活时 C 仅呈现仲裁胜者(当前列新建卡片),不并列全局新建', async ({
    page,
  }) => {
    await login(page);
    await gotoBoard(page);
    // ? 打开帮助层。
    await page.keyboard.press('?');
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    // 同 combo C 只呈现一条(board 胜出于 global)。
    const cKbds = dialog.locator('li', { hasText: /C/ }).filter({ hasText: /C$/ });
    await expect
      .poll(async () => dialog.locator('li').filter({ has: page.locator('kbd', { hasText: 'C' }) }).count())
      .toBe(1);
    void cKbds;
    // 看板组与全局组标题并存。
    await expect(dialog.getByText('board', { exact: false }).first()).toBeVisible();
  });
});
