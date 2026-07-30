/**
 * 真实后端联调验证(MES-16 复验核心 / README §6.7 §6.16):
 * 对 origin/main 的后端 v0.1.0(postgres+redis+api+worker+gateway,MESH_AUTH_MODE=dev,
 * token mesh-dev:<workspace-uuid>)以真实浏览器走通:
 *   ① 首帧鉴权(auth/auth_ok,token 不进 URL)与订阅
 *   ② 经真实 outbox → relay → projector → Redis fan-out 的实时帧投递与增量合并
 *   ③ 断线重连 resume_from 重放补齐
 *   ④ 游标过旧 → resync_required → REST /api/v1/realtime/events 对账 → 无感恢复
 *
 * 事件注入经真实生产路径:INSERT outbox_events(realtime.publish)→ worker 投影。
 * 保留窗口清理(触发 resync)经 SQL DELETE realtime_events(与后端 e2e T6 同法)。
 *
 * 注:后端 v0.1.0 未开 CORS(生产经 nginx 反代同源),本验证以
 * --disable-web-security 启动浏览器(见 playwright.real.config.ts),仅联调用。
 */
import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { injectSession } from './helpers';

const WORKSPACE_ID = '11111111-1111-1111-1111-111111111111';
const CHANNEL = `workspace:${WORKSPACE_ID}:issues`;
const TOKEN = `mesh-dev:${WORKSPACE_ID}`;
const PG_CONTAINER = 'mesh-postgres-1';

let eventCounter = 0;

function psql(sql: string): string {
  return execFileSync('docker', ['exec', '-i', PG_CONTAINER, 'psql', '-U', 'mesh', '-d', 'mesh', '-tAc', sql], {
    encoding: 'utf8',
    timeout: 30_000,
  });
}

/** 经真实生产路径注入实时事件:outbox → relay → projector → realtime_events + Redis fan-out */
function publishEvent(identifier: string, title: string): void {
  eventCounter += 1;
  const updatedAt = new Date(Date.UTC(2026, 6, 26, 12, 0, eventCounter)).toISOString();
  const payload = {
    channel: CHANNEL,
    event: 'issue.created',
    data: {
      issue: {
        id: `real-${identifier}`,
        workspace_id: WORKSPACE_ID,
        identifier,
        title,
        state_category: 'in_progress',
        updated_at: updatedAt,
      },
    },
  };
  const json = JSON.stringify(payload).replace(/'/g, "''");
  psql(
    `INSERT INTO outbox_events (workspace_id, event_type, payload) VALUES ('${WORKSPACE_ID}', 'realtime.publish', '${json}');`,
  );
}

/** 模拟保留窗口清理(后端 retention purge;与后端 e2e T6 的 SQL DELETE 同法) */
function purgeEventsBefore(seq: number): void {
  psql(`DELETE FROM realtime_events WHERE channel = '${CHANNEL}' AND seq < ${seq};`);
}

function watermark(): number {
  const out = psql(`SELECT last_seq FROM realtime_channels WHERE channel = '${CHANNEL}';`);
  return Number(out.trim() || 0);
}

async function loginReal(page: import('@playwright/test').Page): Promise<void> {
  // dev-auth 栈无表单登录:会话经 authStore 持久化键注入(MES-107 起登录页无 dev 入口)
  await injectSession(page, TOKEN);
  await page.goto('/');
}

test.describe('真实后端 v0.1.0 联调(§6.7 / §6.16)', () => {
  test('首帧鉴权 + 订阅 + outbox→projector→fan-out 实时帧增量合并', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await loginReal(page);
    await page.goto('/');

    // 首帧鉴权通过(连接成功即证明 auth/auth_ok 握手与真实后端互通)
    // 稳定态连接指示为状态点 + aria-label(§4.2 减常态噪音,Stage 1 起无可见
    // 文本),经可访问名断言(与 TopBar 单测同口径)。
    await expect(
      page.getByTestId('conn-status').getByRole('img', { name: /Connected|已连接/ }),
    ).toBeVisible({ timeout: 20_000 });
    await page.screenshot({ path: 'test-results/real-01-connected.png' });

    // settle:`Connected` 仅表示 auth_ok 握手完成;客户端随后才发 subscribe,网关侧
    // 订阅注册经 Redis fan-out 有短暂传播窗口。冷库下若立即 publishEvent 会抢在订阅
    // 注册之前而丢首帧(暖库/已持久化时由重放补齐)。等待订阅就绪使 live fan-out 稳定。
    await page.waitForTimeout(800);

    // 经真实生产路径注入事件 → 增量合并出行
    publishEvent('REAL-1', '真实后端实时帧');
    const row = page.getByTestId('home-issue-REAL-1');
    await expect(row).toBeVisible({ timeout: 20_000 });
    await expect(row).toContainText('真实后端实时帧');
    await page.screenshot({ path: 'test-results/real-02-live-merged.png' });
    expect(errors).toEqual([]);
  });

  test('新连接全量重放 + 断线重连 resume_from 补齐', async ({ page }) => {
    // 登录落地即首页:全新上下文无游标 → 订阅不带 resume_from → 服务端全量重放
    // (不 reload:游标已持久化,reload 后 resume_from 会跳过存量事件 —— §6.7 游标语义使然)
    await loginReal(page);
    // 稳定态连接指示为状态点 + aria-label(§4.2 减常态噪音,Stage 1 起无可见
    // 文本),经可访问名断言(与 TopBar 单测同口径)。
    await expect(
      page.getByTestId('conn-status').getByRole('img', { name: /Connected|已连接/ }),
    ).toBeVisible({ timeout: 20_000 });

    // 服务端全量重放存储事件 → 合并出行
    await expect(page.getByTestId('home-issue-REAL-1')).toBeVisible({ timeout: 20_000 });

    // 断网期间注入新事件
    await page.context().setOffline(true);
    await expect(page.getByTestId('status-banner-resyncing')).toBeVisible({ timeout: 15_000 });
    publishEvent('REAL-2', '断线期间真实事件');

    // 恢复 → 重连带 resume_from=last_seq+1 → 服务端顺序补发 → 无感合并
    await page.context().setOffline(false);
    await expect(page.getByTestId('status-banner-resyncing')).toBeHidden({ timeout: 30_000 });
    await expect(page.getByTestId('home-issue-REAL-2')).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByTestId('conn-status').getByRole('img', { name: /Connected|已连接/ }),
    ).toBeVisible();
    await page.screenshot({ path: 'test-results/real-03-resume-replay.png' });
  });

  test('游标过旧 → resync_required → REST 对账 → 无感恢复(§6.7 / T6)', async ({ page }) => {
    await loginReal(page);
    // 稳定态连接指示为状态点 + aria-label(§4.2 减常态噪音,Stage 1 起无可见
    // 文本),经可访问名断言(与 TopBar 单测同口径)。
    await expect(
      page.getByTestId('conn-status').getByRole('img', { name: /Connected|已连接/ }),
    ).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId('home-issue-REAL-1')).toBeVisible({ timeout: 20_000 });

    const before = watermark();
    expect(before).toBeGreaterThan(0);

    // 保留窗口清理:删除低 seq 事件,使客户端游标(1)早于最小可重放 seq
    purgeEventsBefore(before);
    publishEvent('REAL-3', '清理后的事件');

    // 触发重连:断网 → 恢复 → subscribe 带旧 resume_from → resync_required
    await page.context().setOffline(true);
    await expect(page.getByTestId('status-banner-resyncing')).toBeVisible({ timeout: 15_000 });
    await page.context().setOffline(false);

    // 重连后 resume_from 过旧 → 服务端下发 resync_required →
    // 前端 REST /api/v1/realtime/events 对账(Bearer 鉴权)→ 水位对齐重订阅 → connected
    await expect(
      page.getByTestId('conn-status').getByRole('img', { name: /Connected|已连接/ }),
    ).toBeVisible({ timeout: 30_000 });
    // 对账拉回的事件完成合并
    await expect(page.getByTestId('home-issue-REAL-3')).toBeVisible({ timeout: 30_000 });
    await page.screenshot({ path: 'test-results/real-04-resync-reconciled.png' });
  });
});
