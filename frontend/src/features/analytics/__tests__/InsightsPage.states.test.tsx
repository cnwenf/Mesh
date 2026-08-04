/**
 * InsightsPage 状态补测(design-quality.md §3.2 验收行):KPI 数值客户端派生、
 * 口径行(时区回显 + 粒度/范围)、query_cost_exceeded 专文错误、通用错误四部分
 * (影响/重试/诊断 ID)、布局类存在(KPI 条 + 图表网格)、骨架同形。
 * 新增 i18n 键在测试环境呈回退标记,一律按 testid/class/文本标记断言。
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { Route, Routes, useLocation } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiError } from '../../../api';
import { renderWithProviders } from '../../../test-utils/render';
import { deriveWindowKpis, isWindowEmpty, InsightsPage } from '../InsightsPage';
import type { WorkspaceDashboardData } from '../types';

const state = vi.hoisted(() => ({
  dashboard: null as unknown,
  failWith: null as Error | null,
  /** 设置后仪表盘请求挂起等待(观测 loading 骨架;React 19 act 会冲刷即刻 Promise) */
  holdDashboard: null as Promise<unknown> | null,
  workspaceStatus: 'ready' as 'loading' | 'ready',
  dashboardSignals: [] as Array<AbortSignal | undefined>,
}));

const WORKSPACE = {
  id: 'ws1',
  name: 'WS',
  slug: 'ws',
  logo_url: null,
  timezone: 'UTC',
  settings: {},
  my_role: 'member' as const,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

vi.mock('../../../workspace/WorkspaceProvider', () => ({
  useWorkspace: () => ({
    status: state.workspaceStatus,
    workspace: state.workspaceStatus === 'ready' ? WORKSPACE : null,
    error: null,
    isAdmin: false,
    isOwner: false,
    refresh: async () => undefined,
    patch: async () => WORKSPACE,
  }),
  WorkspaceGate: ({ children }: { children: ReactNode }) =>
    state.workspaceStatus === 'ready' ? (
      <>{children}</>
    ) : (
      <div data-testid="ws-loading">Loading</div>
    ),
}));

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>();
  class StubMeshApiClient {
    async request(_method: string, _path: string, opts?: { readonly signal?: AbortSignal }) {
      state.dashboardSignals.push(opts?.signal);
      if (state.holdDashboard !== null) {
        await state.holdDashboard;
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
  state.holdDashboard = null;
  state.workspaceStatus = 'ready';
  state.dashboardSignals.length = 0;
});

function LocationProbe(): React.JSX.Element {
  const location = useLocation();
  return <span data-testid="location-probe">{location.pathname + location.search}</span>;
}

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
    // 大数字不孤立:每个 KPI 都有口径 hint(近 30 天窗)
    expect(cells.getAllByText('Last 30 days').length).toBeGreaterThanOrEqual(4);
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
      expect(
        screen.getByText('Narrow the time range or dimensions, then retry.'),
      ).toBeInTheDocument();
    });
    // 成本超限:只给收窄建议 + 重试,不给通用影响段
    expect(screen.queryByText(/Charts and key metrics/)).toBeNull();
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
    expect(screen.getByText(/Charts and key metrics are unavailable/)).toBeInTheDocument();
    expect(screen.getByText('diag-123')).toBeInTheDocument();
  });

  it('routes a forbidden aggregate to the isolated permission recovery page', async () => {
    state.failWith = new MeshApiError({
      status: 403,
      code: 'project_not_visible',
      message: 'hidden',
    });
    renderWithProviders(
      <Routes>
        <Route path="/insights" element={<InsightsPage />} />
        <Route path="/forbidden" element={<LocationProbe />} />
      </Routes>,
      { route: '/insights' },
    );

    expect(await screen.findByTestId('location-probe')).toHaveTextContent(
      '/forbidden?workspace=%2Fw%2Fws',
    );
    expect(screen.queryByText('Analytics unavailable')).toBeNull();
  });

  it('recovers via retry after a transient failure', async () => {
    state.failWith = new MeshApiError({ status: 500, code: 'internal_error', message: 'boom' });
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await waitFor(() => {
      expect(screen.getByText('Analytics unavailable')).toBeInTheDocument();
    });
    state.failWith = null;
    fireEvent.click(screen.getByText('Retry'));
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

  it('shows a layout-shaped skeleton before data lands', async () => {
    // WorkspaceGate 已 ready，挂起 analytics 请求后页面停在数据骨架态。
    let release: () => void = () => undefined;
    state.holdDashboard = new Promise((resolve) => {
      release = () => resolve(undefined);
    });
    try {
      const { container } = renderWithProviders(<InsightsPage />, { route: '/insights' });
      await act(async () => {
        // 冲刷 analytics effect；请求仍挂起，骨架应在场。
      });
      const loading = screen.getByTestId('insights-loading');
      expect(loading).toBeInTheDocument();
      expect(container.querySelectorAll('.mesh-skeleton__shape').length).toBeGreaterThanOrEqual(8);
    } finally {
      state.holdDashboard = null;
      await act(async () => release());
    }
  });

  it('defers analytics reads while WorkspaceGate is loading', () => {
    state.workspaceStatus = 'loading';
    renderWithProviders(<InsightsPage />, { route: '/insights' });

    expect(screen.getByTestId('ws-loading')).toBeInTheDocument();
    expect(screen.queryByTestId('insights-content')).toBeNull();
    expect(state.dashboardSignals).toHaveLength(0);
  });

  it('preserves the current dashboard during a range refresh', async () => {
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await screen.findByTestId('insights-throughput');

    let release: () => void = () => undefined;
    state.holdDashboard = new Promise((resolve) => {
      release = () => resolve(undefined);
    });
    fireEvent.change(screen.getByTestId('insights-range'), { target: { value: '90' } });

    await waitFor(() => {
      expect(screen.getByTestId('insights-content')).toHaveAttribute('aria-busy', 'true');
    });
    expect(screen.getByTestId('insights-throughput')).toBeInTheDocument();
    expect(screen.getByTestId('insights-refreshing')).toBeInTheDocument();

    state.holdDashboard = null;
    await act(async () => release());
    await waitFor(() => {
      expect(screen.getByTestId('insights-content')).not.toHaveAttribute('aria-busy', 'true');
    });
  });

  it('keeps stale data visible when a refresh fails and offers retry', async () => {
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await screen.findByTestId('insights-throughput');

    state.failWith = new MeshApiError({ status: 500, code: 'internal_error', message: 'boom' });
    fireEvent.change(screen.getByTestId('insights-granularity'), {
      target: { value: 'week' },
    });

    expect(await screen.findByTestId('insights-refresh-error')).toBeInTheDocument();
    expect(screen.getByTestId('insights-throughput')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('stops rendering stale figures when a refresh reports lost permission', async () => {
    renderWithProviders(
      <Routes>
        <Route path="/insights" element={<InsightsPage />} />
        <Route path="/forbidden" element={<LocationProbe />} />
      </Routes>,
      { route: '/insights' },
    );
    await screen.findByTestId('insights-throughput');

    state.failWith = new MeshApiError({
      status: 403,
      code: 'forbidden',
      message: 'permission revoked',
    });
    fireEvent.change(screen.getByTestId('insights-granularity'), {
      target: { value: 'week' },
    });

    expect(await screen.findByTestId('location-probe')).toHaveTextContent(
      '/forbidden?workspace=%2Fw%2Fws',
    );
    expect(screen.queryByTestId('insights-throughput')).toBeNull();
  });

  it('aborts the previous in-flight dashboard read when filters change', async () => {
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await screen.findByTestId('insights-throughput');

    let release: () => void = () => undefined;
    state.holdDashboard = new Promise((resolve) => {
      release = () => resolve(undefined);
    });
    try {
      fireEvent.change(screen.getByTestId('insights-range'), { target: { value: '90' } });
      await waitFor(() => expect(state.dashboardSignals).toHaveLength(2));

      const staleSignal = state.dashboardSignals[1];
      expect(staleSignal?.aborted).toBe(false);

      fireEvent.change(screen.getByTestId('insights-granularity'), {
        target: { value: 'week' },
      });
      await waitFor(() => expect(state.dashboardSignals).toHaveLength(3));

      expect(staleSignal?.aborted).toBe(true);
      expect(state.dashboardSignals[2]?.aborted).toBe(false);
    } finally {
      state.holdDashboard = null;
      await act(async () => release());
    }
  });

  it('aborts an in-flight dashboard read on unmount', async () => {
    state.holdDashboard = new Promise(() => undefined);
    const { unmount } = renderWithProviders(<InsightsPage />, { route: '/insights' });
    await waitFor(() => expect(state.dashboardSignals).toHaveLength(1));

    const signal = state.dashboardSignals[0];
    expect(signal?.aborted).toBe(false);
    unmount();
    expect(signal?.aborted).toBe(true);
  });
});
