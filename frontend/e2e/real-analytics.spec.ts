/**
 * 统计报表真实栈 UI 走查(analytics.md §4 / §5,验收「真人实操」项):
 * 真实 uvicorn(RLS mesh_app)+ 真实 PG 播种数据 + 真实浏览器操作——
 * 洞察页渲染/时间窗切换/暗色主题、普通成员与 admin 的可见性差异、
 * 项目仪表盘页签(velocity/burndown/cycle time + metric 切换)、
 * 成员名册 → agent 详情统计卡(唯一深链入口)。evidence 截图留证。
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const context = JSON.parse(
  readFileSync(resolve(ROOT, 'e2e', '.analytics-context.json'), 'utf8'),
) as {
  apiBase: string;
  ownerToken: string;
  m1Token: string;
  workspaceId: string;
  pubProjectId: string;
  agentId: string;
};

const EVIDENCE = resolve(ROOT, 'e2e', 'evidence', 'analytics');

async function login(page: Page, token: string): Promise<void> {
  await page.goto('/login');
  // 开发用 token 直填入口:<details> 折叠面板,点 summary 展开后填 token。
  await page.locator('details.mesh-login__dev summary').click();
  await page.getByTestId('login-token').fill(token);
  await page.getByTestId('login-submit').click();
  await page.waitForURL(/\/$|\/inbox|\/projects/, { timeout: 30_000 });
}

test.describe('analytics dashboards (real stack)', () => {
  test('owner: insights renders, range switch refetches, dark theme', async ({ page }) => {
    await login(page, context.ownerToken);
    await page.goto('/insights');
    const throughput = page.getByTestId('insights-throughput');
    await expect(throughput).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('insights-workload')).toBeVisible();
    await expect(page.getByTestId('insights-agents')).toBeVisible();
    await expect(page.getByTestId('analytics-line-chart')).toBeVisible();
    // admin 全量:执行数含私有项目执行(pub completed + priv completed + queued = 3)
    await expect(page.getByText('Runs: 3')).toBeVisible();
    await page.screenshot({ path: resolve(EVIDENCE, '01-insights-overview.png') });

    // 时间窗切换 → 重新取数(捕获 /dashboards/workspace 响应)
    const refetch = page.waitForResponse(
      (r) => r.url().includes('/dashboards/workspace') && r.status() === 200,
    );
    await page.getByTestId('insights-range').selectOption('90');
    await refetch;
    await expect(page.getByTestId('insights-throughput')).toBeVisible();

    // 暗色主题(生产机制:data-theme 整体替换语义 token 集)
    await page.evaluate(() => {
      document.documentElement.dataset.theme = 'dark';
    });
    await page.waitForTimeout(300);
    await page.screenshot({ path: resolve(EVIDENCE, '02-insights-dark.png') });
  });

  test('member vs owner: visibility-filtered aggregates differ', async ({ page }) => {
    await login(page, context.m1Token);
    await page.goto('/insights');
    await expect(page.getByTestId('insights-agents')).toBeVisible({ timeout: 30_000 });
    // 轻提示:按可见范围统计
    await expect(page.getByTestId('insights-visibility-note')).toBeVisible();
    // m1 不可见私有项目执行 → 执行数为 2(pub completed + queued)
    await expect(page.getByText('Runs: 2')).toBeVisible();
    expect(await page.getByText('Runs: 3').count()).toBe(0);
    await page.screenshot({ path: resolve(EVIDENCE, '03-insights-member.png') });
  });

  test('owner: project dashboard tab renders all three cards, metric switch refetches', async ({
    page,
  }) => {
    await login(page, context.ownerToken);
    await page.goto(`/projects/${context.pubProjectId}?tab=dashboard`);
    const dashboard = page.getByTestId('project-dashboard');
    await expect(dashboard).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('project-dashboard-velocity')).toBeVisible();
    await expect(page.getByTestId('project-dashboard-burndown')).toBeVisible();
    await expect(page.getByTestId('project-dashboard-cycletime')).toBeVisible();
    // cycle time P50(2 天留痕 → 完成)= 样本 1
    await expect(page.getByTestId('analytics-bar-chart')).toBeVisible();
    await expect(page.getByTestId('analytics-line-chart')).toBeVisible();
    await page.screenshot({ path: resolve(EVIDENCE, '04-project-dashboard.png') });

    // metric 切换 count → burndown 重取
    const refetch = page.waitForResponse(
      (r) => r.url().includes('/analytics/burndown') && r.status() === 200,
    );
    await page.getByTestId('project-dashboard-metric').selectOption('count');
    await refetch;
    await expect(page.getByTestId('project-dashboard-burndown')).toBeVisible();
  });

  test('owner: roster agent projection → agent detail stats card', async ({ page }) => {
    await login(page, context.ownerToken);
    // 成员名册「仅 Agent」筛选投影(唯一入口,无第二 Agents 导航)
    await page.goto('/members?member_type=agent');
    const agentRow = page.getByText('PW Agent').first();
    await expect(agentRow).toBeVisible({ timeout: 30_000 });
    await agentRow.click();
    await page.waitForURL(/\/agents\//);
    const card = page.getByTestId('agent-stats-card');
    await expect(card).toBeVisible({ timeout: 30_000 });
    // KPI 与 token 口径标注(coverage 0.33 < 1)
    await expect(page.getByTestId('agent-stats-executions')).toBeVisible();
    await expect(page.getByTestId('agent-stats-token-note')).toBeVisible();
    await page.screenshot({ path: resolve(EVIDENCE, '05-agent-stats-card.png') });
  });
});
