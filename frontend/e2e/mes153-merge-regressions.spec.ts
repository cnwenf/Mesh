/**
 * MES-153 合并回归的真实浏览器走查:
 * - URL 工作区优先于 memberships 顺序;
 * - favorites 真实契约仅含 target id 时仍可展示并直达;
 * - agent recent 走名册 ID、403 recent 打开即剪枝且不误删并发新增项;
 * - 旧 window.host 存储惰性迁移;
 * - 长列表键盘选择始终滚入可视区。
 */
import { expect, test } from '@playwright/test';
import { gotoHomeReady, login } from './helpers';

test('URL 工作区优先于 memberships 顺序,搜索请求落当前 workspace id', async ({ page }) => {
  await login(page);
  await page.route('**/api/v1/users/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: {
          user: { id: 'user-1', email: 'jane@corp.com', display_name: 'Jane Doe' },
          memberships: [
            {
              workspace_id: 'ws-alpha',
              workspace_name: 'Alpha',
              workspace_slug: 'alpha',
              role: 'admin',
              status: 'active',
              joined_at: null,
            },
            {
              workspace_id: 'ws-beta',
              workspace_name: 'Beta',
              workspace_slug: 'beta',
              role: 'admin',
              status: 'active',
              joined_at: null,
            },
          ],
        },
      }),
    });
  });
  await page.route('**/api/v1/workspaces/by-slug/beta', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: {
          id: 'ws-beta',
          name: 'Beta',
          slug: 'beta',
          logo_url: null,
          timezone: 'UTC',
          my_role: 'admin',
        },
      }),
    });
  });

  await page.goto('/w/beta/board');
  await page.getByTestId('board-page').waitFor({ state: 'visible' });
  const requestPromise = page.waitForRequest((request) =>
    request.url().includes('/api/v1/workspaces/ws-beta/search'),
  );
  await page.keyboard.press('Control+K');
  await page.getByRole('dialog', { name: 'Command palette' }).getByRole('combobox').fill('Login');

  const request = await requestPromise;
  expect(new URL(request.url()).searchParams.get('q')).toBe('Login');
});

test('仅含 target id 的真实收藏行解析标题并直达规范深链', async ({ page }) => {
  await login(page);
  await gotoHomeReady(page);
  await page.keyboard.press('Control+K');
  const palette = page.getByRole('dialog', { name: 'Command palette' });

  const favorite = palette.getByRole('option', { name: /Login page crashes on Safari/ });
  await expect(favorite).toBeVisible();
  await expect(palette.getByText('sr-issue-1', { exact: true })).toHaveCount(0);
  await favorite.click();

  await expect(page).toHaveURL(/\/w\/acme\/issues\/by-identifier\/WEB-124$/);
});

test('403 recent 打开即从界面与持久化同步剪枝', async ({ page }) => {
  await login(page);
  await page.route('**/api/v1/issues/private-1', async (route) => {
    await route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'forbidden', message: 'forbidden' } }),
    });
  });
  await page.evaluate(() => {
    window.localStorage.setItem(
      'mesh.recents:http://127.0.0.1:8901:user-1:ws-1',
      JSON.stringify([
        {
          kind: 'object',
          type: 'issue',
          id: 'private-1',
          title: 'Revoked private issue',
          url: '/w/acme/issues/private-1',
          at: Date.now(),
        },
      ]),
    );
  });
  await page.goto('/w/acme/board');
  const detailRequest = page.waitForRequest((request) =>
    request.url().includes('/api/v1/issues/private-1'),
  );
  await page.keyboard.press('Control+K');
  await detailRequest;

  const palette = page.getByRole('dialog', { name: 'Command palette' });
  await expect(palette.getByText('Revoked private issue', { exact: true })).toHaveCount(0);
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.localStorage.getItem('mesh.recents:http://127.0.0.1:8901:user-1:ws-1'),
      ),
    )
    .toBe('[]');
});

test('agent recent 以 member id 核验并直达名册深链', async ({ page }) => {
  await login(page);
  await page.route('**/api/v1/workspaces/ws-1/members/member-agent-1', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: {
          id: 'member-agent-1',
          workspace_id: 'ws-1',
          member_type: 'agent',
          display_name: 'Roster agent',
          role: 'member',
          status: 'active',
        },
      }),
    });
  });
  await page.evaluate(() => {
    window.localStorage.setItem(
      'mesh.recents:http://127.0.0.1:8901:user-1:ws-1',
      JSON.stringify([
        {
          kind: 'object',
          type: 'agent',
          id: 'member-agent-1',
          title: 'Roster agent',
          url: '/w/acme/members/member-agent-1',
          at: Date.now(),
        },
      ]),
    );
  });
  await page.goto('/w/acme/board');
  const detailRequest = page.waitForRequest((request) =>
    request.url().includes('/api/v1/workspaces/ws-1/members/member-agent-1'),
  );
  await page.keyboard.press('Control+K');
  await detailRequest;

  await page.getByRole('dialog', { name: 'Command palette' }).getByText('Roster agent').click();
  await expect(page).toHaveURL(/\/w\/acme\/members\/member-agent-1$/);
});

test('recent 核验期间新增项不会被旧快照误删', async ({ page }) => {
  await login(page);
  let releaseDetail: (() => void) | undefined;
  const detailGate = new Promise<void>((resolve) => {
    releaseDetail = resolve;
  });
  await page.route('**/api/v1/issues/private-1', async (route) => {
    await detailGate;
    await route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'forbidden', message: 'forbidden' } }),
    });
  });
  await page.evaluate(() => {
    window.localStorage.setItem(
      'mesh.recents:http://127.0.0.1:8901:user-1:ws-1',
      JSON.stringify([
        {
          kind: 'object',
          type: 'issue',
          id: 'private-1',
          title: 'Revoked issue',
          url: '/w/acme/issues/private-1',
          at: 9,
        },
      ]),
    );
  });
  await page.goto('/w/acme/board');
  const detailRequest = page.waitForRequest((request) =>
    request.url().includes('/api/v1/issues/private-1'),
  );
  await page.keyboard.press('Control+K');
  await detailRequest;
  await page.evaluate(() => {
    const key = 'mesh.recents:http://127.0.0.1:8901:user-1:ws-1';
    const existing = JSON.parse(window.localStorage.getItem(key) ?? '[]') as unknown[];
    window.localStorage.setItem(
      key,
      JSON.stringify([
        {
          kind: 'object',
          type: 'issue',
          id: 'concurrent-2',
          title: 'Concurrent recent',
          url: '/w/acme/issues/concurrent-2',
          at: 10,
        },
        ...existing,
      ]),
    );
  });
  releaseDetail?.();

  const palette = page.getByRole('dialog', { name: 'Command palette' });
  await expect(palette.getByText('Revoked issue', { exact: true })).toHaveCount(0);
  await expect(palette.getByText('Concurrent recent', { exact: true })).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.localStorage.getItem('mesh.recents:http://127.0.0.1:8901:user-1:ws-1'),
      ),
    )
    .toContain('concurrent-2');
});

test('旧 window.host recent 形状读取后迁移到 API origin 键', async ({ page }) => {
  await login(page);
  await page.evaluate(() => {
    window.localStorage.setItem(
      'mesh.recents:127.0.0.1:5173:user-1:ws-1',
      JSON.stringify([
        {
          type: 'issue',
          id: 'sr-issue-2',
          title: 'Login rate limiting',
          url: '/w/acme/issues/by-identifier/WEB-130',
          at: '2026-01-02T03:04:05.000Z',
        },
      ]),
    );
  });
  await page.goto('/w/acme/board');
  await page.keyboard.press('Control+K');
  const palette = page.getByRole('dialog', { name: 'Command palette' });

  await expect(palette.getByRole('option', { name: /Login rate limiting/ })).toBeVisible();
  const migrated = await page.evaluate(() =>
    window.localStorage.getItem('mesh.recents:http://127.0.0.1:8901:user-1:ws-1'),
  );
  expect(migrated).toContain('"kind":"object"');
  expect(
    await page.evaluate(() =>
      window.localStorage.getItem('mesh.recents:127.0.0.1:5173:user-1:ws-1'),
    ),
  ).toBeNull();
});

test('键盘选择长命令列表时选中项始终滚入可视区', async ({ page }) => {
  await login(page);
  await gotoHomeReady(page);
  await page.setViewportSize({ width: 390, height: 640 });
  await page.keyboard.press('Control+K');
  const palette = page.getByRole('dialog', { name: 'Command palette' });
  const input = palette.getByRole('combobox');
  for (let index = 0; index < 18; index += 1) {
    await input.press('ArrowDown');
  }

  const geometry = await palette.evaluate((root) => {
    const list = root.querySelector('[role="listbox"]');
    const selected = root.querySelector('[role="option"][aria-selected="true"]');
    if (!(list instanceof HTMLElement) || !(selected instanceof HTMLElement)) return null;
    const listBox = list.getBoundingClientRect();
    const selectedBox = selected.getBoundingClientRect();
    return {
      listTop: listBox.top,
      listBottom: listBox.bottom,
      selectedTop: selectedBox.top,
      selectedBottom: selectedBox.bottom,
    };
  });
  expect(geometry).not.toBeNull();
  expect(geometry?.selectedTop).toBeGreaterThanOrEqual((geometry?.listTop ?? 0) - 1);
  expect(geometry?.selectedBottom).toBeLessThanOrEqual((geometry?.listBottom ?? 0) + 1);
});
