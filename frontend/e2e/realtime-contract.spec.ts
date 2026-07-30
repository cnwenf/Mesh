/**
 * 实时契约真实浏览器验证(README §6.7;MES-107 起经真实首页仪表盘驱动):
 * 模拟事件增量合并(插入/更新/移除)、乱序帧丢弃、游标分页加载更多、
 * 幂等创建、断线重连 resume_from 重放、过旧游标 → resync_required →
 * REST 对账 → 无感恢复、离线横幅。
 */
import { expect, test } from '@playwright/test';
import { emit, gotoHomeReady, login, resetMockServer } from './helpers';

const CHANNEL = 'workspace:ws-1:issues';

/** created 帧载荷:嵌套 {issue} 摘要(后端同形;含 workspace_id 归属) */
function createdPayload(id: string, identifier: string, title: string): Record<string, unknown> {
  return {
    issue: {
      id,
      workspace_id: 'ws-1',
      identifier,
      title,
      state_category: 'todo',
      updated_at: '2026-07-26T00:03:00.000Z',
    },
  };
}

test.beforeEach(async () => {
  await resetMockServer();
});

test.describe('增量合并(README §6.7:完整变更字段 + 归属,禁止整板刷新)', () => {
  test('issue.created 插入行 / issue.updated 就地更新 / issue.deleted 移除', async ({
    page,
  }) => {
    await login(page);
    await gotoHomeReady(page);
    const list = page.getByTestId('home-issue-list');
    await expect(list.getByTestId('home-issue-MESH-1')).toBeVisible();

    // created:新行出现(经 WS 帧合并,不刷新页面)
    await emit(CHANNEL, 'issue.created', createdPayload('issue-100', 'MESH-100', '实时新增行'));
    const created = list.getByTestId('home-issue-MESH-100');
    await expect(created).toBeVisible();
    await expect(created).toContainText('实时新增行');

    // updated:同行就地更新标题
    await emit(CHANNEL, 'issue.updated', {
      id: 'issue-100',
      title: '实时更新的标题',
      updated_at: '2026-07-26T00:04:00.000Z',
    });
    await expect(created).toContainText('实时更新的标题');

    // deleted:行移除
    await emit(CHANNEL, 'issue.deleted', {
      id: 'issue-100',
      updated_at: '2026-07-26T00:05:00.000Z',
    });
    await expect(created).toBeHidden();
  });

  test('乱序旧帧被幂等丢弃(at-least-once 防回退)', async ({ page }) => {
    await login(page);
    await gotoHomeReady(page);
    const row = page.getByTestId('home-issue-MESH-1');
    await expect(row).toBeVisible();

    // 晚于种子数据(mock 以 2026-07-25T08:00Z 为基准播种)的新版本
    await emit(CHANNEL, 'issue.updated', {
      id: 'issue-1',
      title: '最新标题',
      updated_at: '2026-07-26T00:00:00.000Z',
    });
    await expect(row).toContainText('最新标题');

    // 旧 updated_at 的帧不得回退标题
    await emit(CHANNEL, 'issue.updated', {
      id: 'issue-1',
      title: '过期旧标题',
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
    const list = page.getByTestId('home-issue-list');
    await expect(list.getByTestId(/^home-issue-/)).toHaveCount(5);
    await page.getByTestId('home-load-more').click();
    await expect(list.getByTestId(/^home-issue-/)).toHaveCount(8);
    await expect(page.getByTestId('home-load-more')).toBeHidden();
  });

  test('UI 创建后新行出现(POST 幂等键自动携带 + created 帧去重)', async ({ page }) => {
    await login(page);
    await gotoHomeReady(page);
    await page.getByTestId('home-new-title').fill('端到端创建');
    await page.getByTestId('home-create').click();
    const list = page.getByTestId('home-issue-list');
    await expect(list.getByText('端到端创建')).toBeVisible();
  });
});

test.describe('断线重连与重放(README §6.7:每频道 last_seq / resume_from)', () => {
  test('离线显示横幅,期间事件在重连后经 resume_from 补齐', async ({ page }) => {
    await login(page);
    await gotoHomeReady(page);
    const list = page.getByTestId('home-issue-list');

    // 断网 → 离线横幅(§6.12 异常态)
    await page.context().setOffline(true);
    await expect(page.getByTestId('status-banner-resyncing')).toBeVisible({ timeout: 15_000 });

    // 离线期间服务端产生新事件(经 Node 侧注入,不经浏览器网络栈)
    await emit(CHANNEL, 'issue.created', createdPayload('issue-200', 'MESH-200', '断线期间创建'));

    // 恢复网络 → 重连带 resume_from=last_seq+1 → 重放补齐 → 横幅消失
    await page.context().setOffline(false);
    await expect(page.getByTestId('status-banner-resyncing')).toBeHidden({ timeout: 20_000 });
    await expect(list.getByTestId('home-issue-MESH-200')).toBeVisible({ timeout: 20_000 });
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
        title: '预置帧',
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
        title: `批量更新 ${i}`,
        updated_at: new Date(Date.UTC(2026, 6, 26, 0, 2, i)).toISOString(),
      });
    }
    // 模拟保留窗口清理(后端 retention purge,§6.7):删除旧事件,
    // 使客户端游标(6)早于最小可重放 seq → resume_from 过旧
    const purge = await fetch('http://127.0.0.1:8901/api/v1/mock/purge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel: CHANNEL, before_seq: 100 }),
    });
    expect(purge.status).toBe(200);

    // 恢复 → resume_from 过旧 → 服务端下发 resync_required →
    // UI 显示「正在重新同步」→ REST 对账 → 恢复 connected
    // 延迟对账请求,使「正在重新同步」横幅可被稳定断言(否则 localhost 对账
    // 亚帧完成,resyncing 态一闪而过,toBeVisible 轮询会漏 —— 时序竞态)。
    await page.route('**/api/v1/realtime/events**', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route.continue();
    });
    await page.context().setOffline(false);
    await expect(page.getByTestId('status-banner-resyncing')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId('status-banner-resyncing')).toBeHidden({ timeout: 20_000 });
    await expect(page.getByTestId('status-banner-offline')).toBeHidden();
    await expect(page.getByTestId('conn-status')).toContainText(/Connected|已连接/, {
      timeout: 20_000,
    });
  });
});
