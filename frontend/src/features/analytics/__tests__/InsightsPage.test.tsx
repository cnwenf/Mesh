/**
 * InsightsPage 页面测试(analytics.md §4.3):聚合端点取数、可见性轻提示、
 * 时间窗/粒度切换重查、空态/错误态。MeshApiClient 经 vi.mock 以桩替代。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { InsightsPage } from '../InsightsPage';

const requestCalls: Array<{ path: string; query?: Record<string, unknown> }> = [];

const DASHBOARD = {
  throughput: {
    granularity: 'day',
    series: [
      {
        label: '2026-07-24',
        bucket: '2026-07-24T00:00:00Z',
        window_start: '2026-07-23T16:00:00Z',
        window_end: '2026-07-24T16:00:00Z',
        created: 3,
        completed: 2,
        net: 1,
      },
    ],
    meta: { calendar_timezone: 'Asia/Shanghai', display_timezone: 'UTC', net_window: 1 },
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
        member_id: 'wa',
        display_name: 'WA',
        member_type: 'agent',
        open_issues: 0,
        running: 1,
        queued: 1,
        awaiting_approval: 0,
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
        success_rate: 0.75,
        timeout_rate: 0.25,
        avg_duration_seconds: 845,
        retry_rate: 0.0,
        tokens: { prompt_tokens: 100, completion_tokens: 50, total_tokens: 150, token_coverage: 0.25 },
        meta: { token_note: 'tokens cover autopilot runs only' },
      },
    ],
    meta: {},
  },
  meta: { visibility_filtered: true },
};

let dashboardToReturn: unknown = DASHBOARD;
let shouldFail = false;

vi.mock('../../../api', async (importOriginal) => {
  // 保留真实 MeshApiError / errorToI18nKey(页面经 instanceof 归一错误),
  // 仅替换 MeshApiClient 与 getToken。
  const actual = await importOriginal<typeof import('../../../api')>();
  return {
    ...actual,
    MeshApiClient: class {
      async request(_method: string, path: string, opts?: { query?: Record<string, unknown> }) {
        requestCalls.push({ path, query: opts?.query });
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
        if (shouldFail) throw new Error('boom');
        return dashboardToReturn;
      }
      async list(path: string) {
        requestCalls.push({ path });
        return { data: [], next_cursor: null };
      }
    },
    getToken: () => 'token',
  };
});

beforeEach(() => {
  requestCalls.length = 0;
  dashboardToReturn = DASHBOARD;
  shouldFail = false;
});

describe('InsightsPage', () => {
  it('renders throughput, workload and agent sections with the visibility note', async () => {
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await waitFor(() => {
      expect(screen.getByTestId('insights-throughput')).toBeInTheDocument();
    });
    expect(screen.getByTestId('insights-workload')).toBeInTheDocument();
    expect(screen.getByTestId('insights-agents')).toBeInTheDocument();
    expect(screen.getByTestId('insights-visibility-note')).toBeInTheDocument();
    // workload 表:人类行执行列为占位,agent 行带在途计数(WA 同时出现在统计卡)
    expect(screen.getByText('M1 (human)')).toBeInTheDocument();
    expect(screen.getByText('WA (agent)')).toBeInTheDocument();
    // token 覆盖率 < 1 → 口径标注
    expect(screen.getByText('Tokens cover autopilot-triggered runs only.')).toBeInTheDocument();
  });

  it('omits the visibility note for unfiltered (admin) aggregates', async () => {
    dashboardToReturn = {
      ...DASHBOARD,
      meta: { visibility_filtered: false },
    };
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await waitFor(() => {
      expect(screen.getByTestId('insights-throughput')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('insights-visibility-note')).toBeNull();
  });

  it('shows the window-level empty state with a create-issue action when all sections are empty', async () => {
    dashboardToReturn = {
      throughput: {
        granularity: 'day',
        series: [],
        meta: { calendar_timezone: 'UTC', display_timezone: 'UTC', net_window: 0 },
      },
      workload: { data: [], next_cursor: null },
      agent_stats: { agents: [], meta: {} },
      meta: { visibility_filtered: false },
    };
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await waitFor(() => {
      // 空窗 → 页面级空态(§4.6),工具栏保留以便调整范围
      expect(screen.getByTestId('insights-range')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('insights-throughput')).toBeNull();
    const action = screen.getByRole('link');
    expect(action).toHaveAttribute('href', '/issues?create=1');
  });

  it('keeps per-section empty states when only some sections are empty', async () => {
    dashboardToReturn = {
      ...DASHBOARD,
      workload: { data: [], next_cursor: null },
      agent_stats: { agents: [], meta: {} },
    };
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await waitFor(() => {
      expect(screen.getByTestId('insights-throughput')).toBeInTheDocument();
    });
    // workload/agents 卡内空态(吞吐有数据,不触发整窗空态)
    expect(screen.getAllByText('No data in this window.').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('No agent executions in this window.')).toBeInTheDocument();
  });

  it('omits the per-agent token note when coverage is full (ternary false branch)', async () => {
    dashboardToReturn = {
      ...DASHBOARD,
      agent_stats: {
        agents: [
          {
            ...DASHBOARD.agent_stats.agents[0],
            tokens: {
              prompt_tokens: 100,
              completion_tokens: 50,
              total_tokens: 150,
              token_coverage: 1,
            },
          },
        ],
        meta: {},
      },
    };
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await waitFor(() => {
      expect(screen.getByTestId('insights-agents')).toBeInTheDocument();
    });
    expect(screen.queryByText('Tokens cover autopilot-triggered runs only.')).toBeNull();
  });

  it('switching the time range refetches the dashboard', async () => {
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await waitFor(() => {
      expect(screen.getByTestId('insights-range')).toBeInTheDocument();
    });
    const before = requestCalls.length;
    const refetch = waitFor(() => {
      expect(requestCalls.length).toBeGreaterThan(before);
    });
    fireEvent.change(screen.getByTestId('insights-range'), { target: { value: '90' } });
    await refetch;
  });

  it('switching granularity refetches with the new query', async () => {
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await waitFor(() => {
      expect(screen.getByTestId('insights-granularity')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('insights-granularity'), { target: { value: 'week' } });
    await waitFor(() => {
      expect(requestCalls.some((c) => c.query?.granularity === 'week')).toBe(true);
    });
  });

  it('renders the error state and retries', async () => {
    shouldFail = true;
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await waitFor(() => {
      expect(screen.getByText('Analytics unavailable')).toBeInTheDocument();
    });
    shouldFail = false;
    fireEvent.click(screen.getByText('Retry'));
    await waitFor(() => {
      expect(screen.getByTestId('insights-throughput')).toBeInTheDocument();
    });
  });
});
