/**
 * 实时契约真实浏览器验证(MES-16 §真实操作验证 / README §6.7):
 * 模拟事件增量合并(插入/更新/移除)、断线重连 resume_from 重放、
 * 过旧游标 → resync_required → REST 对账 → 无感恢复、离线横幅、
 * 游标分页加载更多、乐观更新 409 收敛、创建幂等。
 */
import { expect, test } from '@playwright/test';
import { emit, gotoHomeReady, login, resetMockServer } from './helpers';

const CHANNEL = 'workspace:ws-1:issues';

test.beforeEach(async () => {
  await resetMockServer();
});

test.describe('增量合并(README §6.7:完整变更字段 + visibility 归属,禁止整板刷新)', () => {
  test('issue.created 插入卡片 / issue.updated 就地更新 / issue.deleted 移除', async ({
    page,
  }) => {
    await login(page);
    await gotoHomeReady(page);
    const list = page.getByTestId('demo-issue-list');
    await expect(list.getByTestId('demo-issue-MESH-1')).toBeVisible();

    // created:新卡片出现(经 WS 帧合并,不刷新页面)
    await emit(CHANNEL, 'issue.created', {
      id: 'issue-100',
      identifier: 'MESH-100',
      title: '实时新增卡片',
      status_category: 'todo',
      updated_at: '2026-07-26T00:03:00.000Z',
    });
    const created = list.getByTestId('demo-issue-MESH-100');
    await expect(created).toBeVisible();
    await expect(created).toContainText('实时新增卡片');

    // updated:同卡就地更新标题
    await emit(CHANNEL, 'issue.updated', {
      id: 'issue-100',
      identifier: 'MESH-100',
      title: '实时更新的标题',
      status_category: 'todo',
      updated_at: '2026-07-26T00:04:00.000Z',
    });
    await expect(created).toContainText('实时更新的标题');

    // deleted:卡片移除
    await emit(CHANNEL, 'issue.deleted', {
      id: 'issue-100',
      identifier: 'MESH-100',
      updated_at: '2026-07-26T00:05:00.000Z',
    });
    await expect(created).toBeHidden();
  });

  test('乱序旧帧被幂等丢弃(at-least-once 防回退)', async ({ page }) => {
    await login(page);
    await gotoHomeReady(page);
    const row = page.getByTestId('demo-issue-MESH-1');
    await expect(row).toBeVisible();

    // 晚于种子数据(mock 以 2026-07-25T08:00Z 为基准播种)的新版本
    await emit(CHANNEL, 'issue.updated', {
      id: 'issue-1',
      identifier: 'MESH-1',
      title: '最新标题',
      status_category: 'todo',
      updated_at: '2026-07-26T00:00:00.000Z',
    });
    await expect(row).toContainText('最新标题');

    // 旧 updated_at 的帧不得回退标题
    await emit(CHANNEL, 'issue.updated', {
      id: 'issue-1',
      identifier: 'MESH-1',
      title: '过期旧标题',
      status_category: 'todo',
      updated_at: '2026-07-24T00:00:00.000Z',
    });
    await page.waitForTimeout(500);
    await expect(row).toContainText('最新标题');
  });
});

test.describe('游标分页与幂等创建(README §6.14)', () => {
  test('首屏 5 条 + 加载更多补齐剩余(整体游标)', async ({ page }) => {
    await login(page);
    await gotoHomeReady(page);
    const list = page.getByTestId('demo-issue-list');
    await expect(list.getByTestId(/^demo-issue-/)).toHaveCount(5);
    await page.getByTestId('demo-load-more').click();
    await expect(list.getByTestId(/^demo-issue-/)).toHaveCount(8);
    await expect(page.getByTestId('demo-load-more')).toBeHidden();
  });

  test('UI 创建后新卡片出现(POST 幂等键自动携带 + created 帧合并)', async ({ page }) => {
    await login(page);
    await gotoHomeReady(page);
    await page.getByTestId('demo-new-title').fill('端到端创建');
    await page.getByTestId('demo-create').click();
    const list = page.getByTestId('demo-issue-list');
    await expect(list.getByText('端到端创建')).toBeVisible();
  });
});

test.describe('乐观更新与服务端版本校验(README §6.14:If-Match / 409 收敛)', () => {
  test('正常改名成功', async ({ page }) => {
    await login(page);
    await gotoHomeReady(page);
    const row = page.getByTestId('demo-issue-MESH-3');
    await row.getByTestId('demo-rename-MESH-3').click();
    await expect(row).toContainText('✓');
  });

  test('并发改写触发 409 → 收敛到服务端最新并重试成功', async ({ page }) => {
    await login(page);
    await gotoHomeReady(page);
    const row = page.getByTestId('demo-issue-MESH-2');

    // 取当前版本,经服务端带 If-Match 并发改写(制造版本漂移)
    const cur = await fetch('http://127.0.0.1:8901/api/v1/demo/issues/issue-2');
    const curBody = (await cur.json()) as { data: { updated_at: string } };
    const race = await fetch('http://127.0.0.1:8901/api/v1/demo/issues/issue-2', {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        'If-Match': curBody.data.updated_at,
      },
      body: JSON.stringify({ title: '并发修改' }),
    });
    expect(race.status).toBe(200);

    // UI 持有旧版本 → 首次 PATCH 409 → 客户端拉最新 → 携带新版本重试 → 成功
    await row.getByTestId('demo-rename-MESH-2').click();
    await expect(row).toContainText('✓');
  });
});

test.describe('断线重连与重放(README §6.7:每频道 last_seq / resume_from)', () => {
  test('离线显示横幅,期间事件在重连后经 resume_from 补齐', async ({ page }) => {
    await login(page);
    await gotoHomeReady(page);
    const list = page.getByTestId('demo-issue-list');

    // 断网 → 离线横幅(§6.12 异常态)
    await page.context().setOffline(true);
    await expect(page.getByTestId('status-banner-resyncing')).toBeVisible({ timeout: 15_000 });

    // 离线期间服务端产生新事件(经 Node 侧注入,不经浏览器网络栈)
    await emit(CHANNEL, 'issue.created', {
      id: 'issue-200',
      identifier: 'MESH-200',
      title: '断线期间创建',
      status_category: 'todo',
      updated_at: '2026-07-26T00:01:00.000Z',
    });

    // 恢复网络 → 重连带 resume_from=last_seq+1 → 重放补齐 → 横幅消失
    await page.context().setOffline(false);
    await expect(page.getByTestId('status-banner-resyncing')).toBeHidden({ timeout: 20_000 });
    await expect(list.getByTestId('demo-issue-MESH-200')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId('conn-status')).toContainText(/Connected|已连接/);
  });
});

test.describe('游标过旧 → resync_required → REST 对账(README §6.7)', () => {
  test('重放窗口外重连触发重新同步横幅,对账后无感恢复', async ({ page }) => {
    await login(page);
    await gotoHomeReady(page);

    // 先收几帧建立频道游标(重连时才会带 resume_from)
    await expect(page.getByTestId('conn-status')).toContainText(/Connected|已连接/);
    for (let i = 0; i < 5; i++) {
      await emit(CHANNEL, 'issue.updated', {
        id: `issue-${i + 1}`,
        identifier: `MESH-${i + 1}`,
        title: '预置帧',
        status_category: 'todo',
        updated_at: '2026-07-26T00:00:30.000Z',
      });
    }
    await page.waitForTimeout(400);

    // 断网
    await page.context().setOffline(true);
    await expect(page.getByTestId('status-banner-resyncing')).toBeVisible({ timeout: 15_000 });

    // 离线期间产生 >100 帧
    for (let i = 0; i < 120; i++) {
      await emit(CHANNEL, 'issue.updated', {
        id: `issue-${(i % 8) + 1}`,
        identifier: `MESH-${(i % 8) + 1}`,
        title: `批量更新 ${i}`,
        status_category: 'todo',
        updated_at: new Date(Date.UTC(2026, 6, 26, 0, 2, i)).toISOString(),
      });
    }
    // 模拟保留窗口清理(后端 retention purge,§6.7):删除旧事件,
    // 使客户端游标(6)早于最小可重放 seq → resume_from 过旧
    const purge = await fetch('http://127.0.0.1:8901/api/v1/demo/purge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel: CHANNEL, before_seq: 100 }),
    });
    expect(purge.status).toBe(200);

    // 恢复 → resume_from 过旧 → 服务端下发 resync_required →
    // UI 显示「正在重新同步」→ REST 对账 → 恢复 connected
    await page.context().setOffline(false);
    await expect(page.getByTestId('status-banner-resyncing')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId('status-banner-resyncing')).toBeHidden({ timeout: 20_000 });
    await expect(page.getByTestId('status-banner-offline')).toBeHidden();
    await expect(page.getByTestId('conn-status')).toContainText(/Connected|已连接/, {
      timeout: 20_000,
    });
  });
});
