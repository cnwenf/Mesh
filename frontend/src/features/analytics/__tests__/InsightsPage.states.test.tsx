/**
 * InsightsPage 状态补测(design-quality.md §3.2 验收行):KPI 数值客户端派生、
 * 口径行(时区回显 + 粒度/范围)、query_cost_exceeded 专文错误、通用错误四部分
 * (影响/重试/诊断 ID)、布局类存在(KPI 条 + 图表网格)、骨架同形。
 * 新增 i18n 键在测试环境呈回退标记,一律按 testid/class/文本标记断言。
 */
import { screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiError } from '../../../api';
import { renderWithProviders } from '../../../test-utils/render';
import { deriveWindowKpis, isWindowEmpty, InsightsPage } from '../InsightsPage';
import type { WorkspaceDashboardData } from '../types';

const state = vi.hoisted(() => ({
  dashboard: null as unknown,
  failWith: null as Error | null,
}));

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>();
  class StubMeshApiClient {
    async request(_method: string, path: string) {
      if (path === '/api/v1/users/me') {
        return {
          user: { id: 'u1', email: 'u1@x.io', display_name: 'U1' },
          memberships: [
            {
              workspace_id: 'ws1',
              workspace_name: 'WS',
              workspace_slug: 'ws',
              role: 'member',
              status: 'active',
              joined_at: null,
            },
          ],
        };
      }
      if (state.failWith !== null) throw state.failWith;
      return state.dashboard;
    }

    async list() {
      return { data: [], next_cursor: null };
    }
  }
  return { ...actual, MeshApiClient: StubMeshApiClient, getToken: () => 'token' };
});

function makeDashboard(seriesCount: number): WorkspaceDashboardData {
  const series = Array.from({ length: seriesCount }, (_, i) => ({
    label: `2026-07-2${i}`,
    bucket: `2026-07-2${i}T00:00:00Z`,
    window_start: `2026-07-2${i}T00:00:00Z`,
    window_end: `2026-07-2${i + 1}T00:00:00Z`,
    created: 3 - i, // 3, 2
    completed: 2 - i, // 2, 1
    net: 1,
  }));
  return {
    throughput: {
      granularity: 'day',
      series,
      meta: { calendar_timezone: 'Asia/Shanghai', net_window: 2 },
    },
    workload: {
      data: [
        {
          member_id: 'm1',
          display_name: 'M1',
          member_type: 'human',
          open_issues: 2,
          running: null,
          queued: null,
          awaiting_approval: null,
        },
        {
          member_id: 'm2',
          display_name: 'M2',
          member_type: 'human',
          open_issues: 4,
          running: null,
          queued: null,
          awaiting_approval: null,
        },
      ],
      next_cursor: null,
    },
    agent_stats: {
      agents: [
        {
          agent_id: 'a1',
          display_name: 'WA',
          member_type: 'agent',
          executions: 4,
          succeeded: 3,
          terminal: 4,
          cancelled_count: 0,
          success_rate: 0.9,
          timeout_rate: 0.1,
          avg_duration_seconds: 60,
          retry_rate: 0,
          tokens: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2, token_coverage: 1 },
          meta: { token_note: '' },
        },
      ],
      meta: {},
    },
    meta: { visibility_filtered: false, display_timezone: 'Asia/Shanghai' },
  } as unknown as WorkspaceDashboardData;
}

beforeEach(() => {
  state.dashboard = makeDashboard(2);
  state.failWith = null;
});

describe('deriveWindowKpis / isWindowEmpty', () => {
  it('derives window aggregates client-side from the dashboard payload', () => {
    const kpis = deriveWindowKpis(makeDashboard(2));
    expect(kpis).toEqual({ created: 5, completed: 3, net: 2, openIssues: 6, agentsTracked: 1 });
  });

  it('flags the window empty only when all sections are empty', () => {
    const empty = makeDashboard(0);
    const noRows = {
      ...empty,
      workload: { data: [], next_cursor: null },
      agent_stats: { agents: [], meta: {} },
    };
    expect(isWindowEmpty(noRows as unknown as WorkspaceDashboardData)).toBe(true);
    expect(isWindowEmpty(makeDashboard(2))).toBe(false);
  });
});

describe('InsightsPage derived KPI strip', () => {
  it('renders the five window KPIs with derived values and caliber hints', async () => {
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    const strip = await screen.findByRole('group');
    const cells = within(strip);
    // created=5 / completed=3 / net=2 / open=6 / agents=1
    expect(cells.getByText('5')).toBeInTheDocument();
    expect(cells.getByText('3')).toBeInTheDocument();
    expect(cells.getByText('2')).toBeInTheDocument();
    expect(cells.getByText('6')).toBeInTheDocument();
    expect(cells.getByText('1')).toBeInTheDocument();
    // 大数字不孤立:每个 KPI 都有口径 hint(新键回退标记)
    expect(cells.getAllByText(/analytics\.kpi\.windowHint/).length).toBeGreaterThanOrEqual(4);
  });
});

describe('InsightsPage caliber row', () => {
  it('echoes the timezone and the granularity/range context', async () => {
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await waitFor(() => {
      expect(screen.getByTestId('insights-tz-note')).toBeInTheDocument();
    });
    expect(screen.getByTestId('insights-caliber')).toBeInTheDocument();
    // 未过滤 → 无可见性提示
    expect(screen.queryByTestId('insights-visibility-note')).toBeNull();
  });

  it('shows the visibility note alongside the tz echo when filtered', async () => {
    state.dashboard = {
      ...(makeDashboard(2) as unknown as Record<string, unknown>),
      meta: { visibility_filtered: true, display_timezone: 'Asia/Shanghai' },
    };
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await waitFor(() => {
      expect(screen.getByTestId('insights-visibility-note')).toBeInTheDocument();
    });
    expect(screen.getByTestId('insights-tz-note')).toBeInTheDocument();
  });
});

describe('InsightsPage error states', () => {
  it('shows the cost-exceeded message when the query is too expensive', async () => {
    state.failWith = new MeshApiError({
      status: 422,
      code: 'query_cost_exceeded',
      message: 'too expensive',
    });
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await waitFor(() => {
      expect(screen.getByText(/analytics\.state\.costExceeded/)).toBeInTheDocument();
    });
    // 成本超限:只给收窄建议 + 重试,不给通用影响段
    expect(screen.queryByText(/analytics\.state\.errorImpact/)).toBeNull();
    expect(screen.getByText('Retry')).toBeInTheDocument();
  });

  it('renders the four-part generic error with impact and diagnostic id', async () => {
    state.failWith = new MeshApiError({
      status: 500,
      code: 'internal_error',
      message: 'boom',
      details: { diagnostic_id: 'diag-123' },
    });
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await waitFor(() => {
      expect(screen.getByText('Analytics unavailable')).toBeInTheDocument();
    });
    expect(screen.getByText('An internal error occurred. Please try again.')).toBeInTheDocument();
    expect(screen.getByText(/analytics\.state\.errorImpact/)).toBeInTheDocument();
    expect(screen.getByText('diag-123')).toBeInTheDocument();
  });

  it('recovers via retry after a transient failure', async () => {
    state.failWith = new MeshApiError({ status: 500, code: 'internal_error', message: 'boom' });
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await waitFor(() => {
      expect(screen.getByText('Analytics unavailable')).toBeInTheDocument();
    });
    state.failWith = null;
    screen.getByText('Retry').click();
    await waitFor(() => {
      expect(screen.getByTestId('insights-throughput')).toBeInTheDocument();
    });
  });
});

describe('InsightsPage layout and skeleton', () => {
  it('renders the KPI strip and charts grid containers', async () => {
    const { container } = renderWithProviders(<InsightsPage />, { route: '/insights' });
    await waitFor(() => {
      expect(screen.getByTestId('insights-throughput')).toBeInTheDocument();
    });
    expect(container.querySelector('.mesh-analytics__kpi-strip')).not.toBeNull();
    expect(container.querySelector('.mesh-analytics__charts')).not.toBeNull();
    // 图表卡保留 role=img 与可访问名(手写 SVG 约定)
    expect(screen.getByRole('img')).toBeInTheDocument();
  });

  it('shows a layout-shaped skeleton before data lands', () => {
    const { container } = renderWithProviders(<InsightsPage />, { route: '/insights' });
    const loading = screen.getByTestId('insights-loading');
    expect(loading).toBeInTheDocument();
    expect(container.querySelectorAll('.mesh-skeleton__shape').length).toBeGreaterThanOrEqual(8);
  });
});
