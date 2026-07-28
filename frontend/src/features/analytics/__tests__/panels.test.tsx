/**
 * ProjectDashboardPanel / AgentStatsCard 组件测试(analytics.md §4.2/§4.4)。
 * client 以 prop 注入最小桩(无模块级 mock)。
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import { renderWithProviders } from '../../../test-utils/render';
import { AgentStatsCard } from '../AgentStatsCard';
import { ProjectDashboardPanel } from '../ProjectDashboardPanel';

const PROJECT_DASHBOARD = {
  project_id: 'p1',
  velocity: {
    cycles: [
      {
        cycle_id: 'c1',
        name: 'Sprint 1',
        starts_at: '2026-07-06',
        ends_at: '2026-07-12',
        state: 'active',
        completed_issues: 2,
        completed_points: 5,
        completed_points_by_unit: { points: 3, hours: 2 },
      },
    ],
    meta: { display_timezone: 'UTC', scope_caliber: 'current_attribution' },
  },
  burndown: {
    scope: { type: 'cycle', id: 'c1' },
    window: { start: '2026-07-06', end: '2026-07-12' },
    metric: 'points',
    total: 6,
    ideal: [
      { date: '2026-07-06', remaining: 6 },
      { date: '2026-07-07', remaining: 5 },
      { date: '2026-07-08', remaining: 4 },
    ],
    actual: [
      { date: '2026-07-06', remaining: 6 },
      { date: '2026-07-07', remaining: 6 },
    ],
    meta: { display_timezone: 'UTC', scope_caliber: 'current_attribution' },
  },
  cycle_time: {
    project_id: 'p1',
    from_category: 'in_progress',
    p50_seconds: 172800,
    p90_seconds: 483840,
    sample_size: 3,
    meta: { insufficient_data: 2, display_timezone: 'UTC' },
  },
};

function makeStubClient(handler: (path: string) => unknown): MeshApiClient {
  return {
    request: vi.fn(async (_method: string, path: string) => handler(path)),
    list: vi.fn(async () => ({ data: [], next_cursor: null })),
  } as unknown as MeshApiClient;
}

describe('ProjectDashboardPanel', () => {
  it('renders velocity bars, burndown lines and cycle time KPIs', async () => {
    const client = makeStubClient(() => PROJECT_DASHBOARD);
    renderWithProviders(
      <ProjectDashboardPanel client={client} workspaceId="ws1" projectId="p1" />,
    );
    await waitFor(() => {
      expect(screen.getByTestId('project-dashboard')).toBeInTheDocument();
    });
    expect(screen.getByTestId('analytics-bar-chart')).toBeInTheDocument();
    expect(screen.getByTestId('analytics-line-chart')).toBeInTheDocument();
    // cycle time KPI:2 天 / 5.6 天 / 样本 3
    const cycleCard = within(screen.getByTestId('project-dashboard-cycletime'));
    expect(cycleCard.getByText('2d 0h')).toBeInTheDocument();
    expect(cycleCard.getByText('5d 14h')).toBeInTheDocument();
    expect(cycleCard.getByText('3')).toBeInTheDocument();
    // insufficient 标注
    expect(screen.getByTestId('project-dashboard-insufficient')).toBeInTheDocument();
  });

  it('switching metric refetches burndown in the new metric', async () => {
    const requests: string[] = [];
    const client = {
      request: vi.fn(async (_method: string, path: string, opts?: { query?: unknown }) => {
        requests.push(`${path}?${JSON.stringify(opts?.query ?? {})}`);
        if (path.endsWith('/analytics/burndown')) {
          return { ...PROJECT_DASHBOARD.burndown, metric: 'count', total: 3 };
        }
        return PROJECT_DASHBOARD;
      }),
      list: vi.fn(async () => ({ data: [], next_cursor: null })),
    } as unknown as MeshApiClient;
    renderWithProviders(
      <ProjectDashboardPanel client={client} workspaceId="ws1" projectId="p1" />,
    );
    await waitFor(() => {
      expect(screen.getByTestId('project-dashboard-metric')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('project-dashboard-metric'), {
      target: { value: 'count' },
    });
    await waitFor(() => {
      expect(requests.some((r) => r.includes('/analytics/burndown'))).toBe(true);
    });
  });

  it('renders the no-scope empty state when burndown is null', async () => {
    const client = makeStubClient(() => ({ ...PROJECT_DASHBOARD, burndown: null }));
    renderWithProviders(
      <ProjectDashboardPanel client={client} workspaceId="ws1" projectId="p1" />,
    );
    await waitFor(() => {
      expect(screen.getByText('No cycle or milestone to burn down.')).toBeInTheDocument();
    });
  });

  it('renders the error state with retry', async () => {
    let fail = true;
    const client = {
      request: vi.fn(async () => {
        if (fail) throw new Error('boom');
        return PROJECT_DASHBOARD;
      }),
      list: vi.fn(async () => ({ data: [], next_cursor: null })),
    } as unknown as MeshApiClient;
    renderWithProviders(
      <ProjectDashboardPanel client={client} workspaceId="ws1" projectId="p1" />,
    );
    await waitFor(() => {
      expect(screen.getByText('Analytics unavailable')).toBeInTheDocument();
    });
    fail = false;
    fireEvent.click(screen.getByText('Retry'));
    await waitFor(() => {
      expect(screen.getByTestId('project-dashboard')).toBeInTheDocument();
    });
  });
});

const AGENT_STATS = {
  agent_id: 'a1',
  display_name: 'WA',
  member_type: 'agent',
  executions: 10,
  succeeded: 9,
  terminal: 10,
  cancelled_count: 0,
  success_rate: 0.9,
  timeout_rate: 0.1,
  avg_duration_seconds: 3661,
  retry_rate: 0.2,
  tokens: { prompt_tokens: 1000, completion_tokens: 500, total_tokens: 1500, token_coverage: 0.4 },
  meta: { token_note: 'tokens cover autopilot runs only' },
};

describe('AgentStatsCard', () => {
  it('renders KPIs and the token coverage note when coverage < 1', async () => {
    const client = makeStubClient(() => AGENT_STATS);
    renderWithProviders(<AgentStatsCard client={client} workspaceId="ws1" agentId="a1" />);
    await waitFor(() => {
      expect(screen.getByTestId('agent-stats-card')).toBeInTheDocument();
    });
    expect(screen.getByText('90.0%')).toBeInTheDocument(); // success
    expect(screen.getByText('20.0%')).toBeInTheDocument(); // retry
    expect(screen.getByText('10.0%')).toBeInTheDocument(); // timeout
    expect(screen.getByText('1h 1m')).toBeInTheDocument(); // avg duration
    expect(screen.getByTestId('agent-stats-token-note')).toBeInTheDocument();
    expect(screen.getByTestId('agent-stats-executions')).toBeInTheDocument();
  });

  it('omits the token note at full coverage', async () => {
    const client = makeStubClient(() => ({
      ...AGENT_STATS,
      tokens: { ...AGENT_STATS.tokens, token_coverage: 1 },
    }));
    renderWithProviders(<AgentStatsCard client={client} workspaceId="ws1" agentId="a1" />);
    await waitFor(() => {
      expect(screen.getByTestId('agent-stats-card')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('agent-stats-token-note')).toBeNull();
  });

  it('shows the unavailable note on error (private agent 403)', async () => {
    const client = {
      request: vi.fn(async () => {
        throw new Error('agent_not_visible');
      }),
      list: vi.fn(async () => ({ data: [], next_cursor: null })),
    } as unknown as MeshApiClient;
    renderWithProviders(<AgentStatsCard client={client} workspaceId="ws1" agentId="a1" />);
    await waitFor(() => {
      expect(
        screen.getByText('Statistics are not available for this agent.'),
      ).toBeInTheDocument();
    });
  });

  it('shows the no-data note when the multi-agent shape comes back', async () => {
    const client = makeStubClient(() => ({ agents: [], meta: {} }));
    renderWithProviders(<AgentStatsCard client={client} workspaceId="ws1" agentId="a1" />);
    await waitFor(() => {
      expect(screen.getByText('No data in this window.')).toBeInTheDocument();
    });
  });
});
