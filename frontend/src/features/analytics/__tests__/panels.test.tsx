/**
 * ProjectDashboardPanel / AgentStatsCard 组件测试(analytics.md §4.2/§4.4)。
 * client 以 prop 注入最小桩(无模块级 mock)。
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { Route, Routes, useLocation } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import { MeshApiError, type MeshApiClient } from '../../../api';
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
      {
        cycle_id: 'c0',
        name: 'Sprint 0',
        starts_at: '2026-06-29',
        ends_at: '2026-07-05',
        state: 'completed',
        completed_issues: 1,
        completed_points: 2,
        completed_points_by_unit: { points: 2, hours: 0 },
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

function ProjectScopeHarness(props: { readonly client: MeshApiClient }): React.JSX.Element {
  const [projectId, setProjectId] = useState('p1');
  return (
    <>
      <button data-testid="switch-project" onClick={() => setProjectId('p2')}>
        switch
      </button>
      <ProjectDashboardPanel client={props.client} workspaceId="ws1" projectId={projectId} />
    </>
  );
}

function LocationProbe(): React.JSX.Element {
  const location = useLocation();
  return <span data-testid="location-probe">{location.pathname + location.search}</span>;
}

describe('ProjectDashboardPanel', () => {
  it('renders velocity bars, burndown lines and cycle time KPIs', async () => {
    const client = makeStubClient(() => PROJECT_DASHBOARD);
    renderWithProviders(<ProjectDashboardPanel client={client} workspaceId="ws1" projectId="p1" />);
    await waitFor(() => {
      expect(screen.getByTestId('project-dashboard')).toBeInTheDocument();
    });
    expect(screen.getByTestId('analytics-bar-chart')).toBeInTheDocument();
    expect(screen.getByTestId('analytics-line-chart')).toBeInTheDocument();
    // cycle time KPI:2 天 / 5.6 天 / 样本 3
    const cycleCard = within(screen.getByTestId('project-dashboard-cycletime'));
    expect(cycleCard.getAllByText('2d 0h')).toHaveLength(2);
    expect(cycleCard.getAllByText('5d 14h')).toHaveLength(2);
    expect(cycleCard.getByText('3')).toBeInTheDocument();
    expect(screen.getByTestId('project-dashboard-cycle-distribution')).toHaveAttribute(
      'role',
      'img',
    );
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
    renderWithProviders(<ProjectDashboardPanel client={client} workspaceId="ws1" projectId="p1" />);
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

  it('switching the time range refetches the project dashboard', async () => {
    const client = {
      request: vi.fn(async () => PROJECT_DASHBOARD),
      list: vi.fn(async () => ({ data: [], next_cursor: null })),
    } as unknown as MeshApiClient;
    renderWithProviders(<ProjectDashboardPanel client={client} workspaceId="ws1" projectId="p1" />);
    await waitFor(() => {
      expect(screen.getByTestId('project-dashboard-range')).toBeInTheDocument();
    });
    await waitFor(() => expect(client.request).toHaveBeenCalled());
    const before = (client.request as ReturnType<typeof vi.fn>).mock.calls.length;
    fireEvent.change(screen.getByTestId('project-dashboard-range'), { target: { value: '90' } });
    await waitFor(() => {
      expect((client.request as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(
        before,
      );
    });
  });

  it('preserves rendered cards while the selected range refreshes', async () => {
    let dashboardCalls = 0;
    let release: () => void = () => undefined;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    const client = {
      request: vi.fn(async (_method: string, path: string) => {
        if (path.includes('/dashboards/project/')) {
          dashboardCalls += 1;
          if (dashboardCalls > 1) await pending;
        }
        return PROJECT_DASHBOARD;
      }),
      list: vi.fn(async () => ({ data: [], next_cursor: null })),
    } as unknown as MeshApiClient;
    renderWithProviders(<ProjectDashboardPanel client={client} workspaceId="ws1" projectId="p1" />);
    await screen.findByTestId('project-dashboard');

    fireEvent.change(screen.getByTestId('project-dashboard-range'), { target: { value: '90' } });
    await waitFor(() => expect(dashboardCalls).toBe(2));
    expect(screen.getByTestId('project-dashboard')).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByTestId('project-dashboard-velocity')).toBeInTheDocument();

    release();
    await waitFor(() => {
      expect(screen.getByTestId('project-dashboard')).not.toHaveAttribute('aria-busy', 'true');
    });
  });

  it('keeps successful project figures after a refresh error and retries inline', async () => {
    let dashboardCalls = 0;
    let failRefresh = false;
    const client = {
      request: vi.fn(async (_method: string, path: string) => {
        if (path.includes('/dashboards/project/')) {
          dashboardCalls += 1;
          if (failRefresh) throw new Error('refresh failed');
        }
        return PROJECT_DASHBOARD;
      }),
      list: vi.fn(async () => ({ data: [], next_cursor: null })),
    } as unknown as MeshApiClient;
    renderWithProviders(<ProjectDashboardPanel client={client} workspaceId="ws1" projectId="p1" />);
    await screen.findByTestId('project-dashboard');

    failRefresh = true;
    fireEvent.change(screen.getByTestId('project-dashboard-range'), { target: { value: '90' } });

    expect(await screen.findByTestId('project-dashboard-refresh-error')).toBeInTheDocument();
    expect(screen.getByTestId('project-dashboard-velocity')).toBeInTheDocument();

    failRefresh = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(dashboardCalls).toBeGreaterThanOrEqual(3));
    await waitFor(() => expect(screen.queryByTestId('project-dashboard-refresh-error')).toBeNull());
  });

  it('does not expose the previous project while a new project scope loads', async () => {
    let releaseSecond: () => void = () => undefined;
    const secondPending = new Promise<void>((resolve) => {
      releaseSecond = resolve;
    });
    const secondDashboard = {
      ...PROJECT_DASHBOARD,
      project_id: 'p2',
      velocity: {
        ...PROJECT_DASHBOARD.velocity,
        cycles: [{ ...PROJECT_DASHBOARD.velocity.cycles[0], cycle_id: 'c2', name: 'Sprint 2' }],
      },
    };
    const client = {
      request: vi.fn(async (_method: string, path: string) => {
        if (path.endsWith('/p2')) {
          await secondPending;
          return secondDashboard;
        }
        return PROJECT_DASHBOARD;
      }),
      list: vi.fn(async () => ({ data: [], next_cursor: null })),
    } as unknown as MeshApiClient;
    renderWithProviders(<ProjectScopeHarness client={client} />);
    expect(await screen.findByText('Sprint 1 · Active')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('switch-project'));
    await waitFor(() => {
      expect(
        (client.request as ReturnType<typeof vi.fn>).mock.calls.some((call) =>
          String(call[1]).endsWith('/p2'),
        ),
      ).toBe(true);
    });
    expect(screen.getByText('Loading analytics')).toBeInTheDocument();
    expect(screen.queryByText('Sprint 1 · Active')).toBeNull();

    await act(async () => releaseSecond());
    expect(await screen.findByText('Sprint 2 · Active')).toBeInTheDocument();
  });

  it('renders the velocity empty state when there are no cycles', async () => {
    const client = makeStubClient(() => ({
      ...PROJECT_DASHBOARD,
      velocity: { ...PROJECT_DASHBOARD.velocity, cycles: [] },
    }));
    renderWithProviders(<ProjectDashboardPanel client={client} workspaceId="ws1" projectId="p1" />);
    await waitFor(() => {
      expect(screen.getByTestId('project-dashboard-velocity')).toBeInTheDocument();
    });
    // velocity 卡内空态(非整页空态)
    expect(
      within(screen.getByTestId('project-dashboard-velocity')).getByText('No data in this window.'),
    ).toBeInTheDocument();
  });

  it('renders the no-scope empty state when burndown is null', async () => {
    const client = makeStubClient(() => ({ ...PROJECT_DASHBOARD, burndown: null }));
    renderWithProviders(<ProjectDashboardPanel client={client} workspaceId="ws1" projectId="p1" />);
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
    renderWithProviders(<ProjectDashboardPanel client={client} workspaceId="ws1" projectId="p1" />);
    await waitFor(() => {
      expect(screen.getByText('Analytics unavailable')).toBeInTheDocument();
    });
    fail = false;
    fireEvent.click(screen.getByText('Retry'));
    await waitFor(() => {
      expect(screen.getByTestId('project-dashboard')).toBeInTheDocument();
    });
  });

  it('routes project_not_visible to the isolated permission recovery page', async () => {
    const client = {
      request: vi.fn(async () => {
        throw new MeshApiError({
          status: 403,
          code: 'project_not_visible',
          message: 'hidden',
        });
      }),
      list: vi.fn(async () => ({ data: [], next_cursor: null })),
    } as unknown as MeshApiClient;
    renderWithProviders(
      <Routes>
        <Route
          path="/project"
          element={
            <ProjectDashboardPanel
              client={client}
              workspaceId="ws1"
              workspaceSlug="acme"
              projectId="p1"
            />
          }
        />
        <Route path="/forbidden" element={<LocationProbe />} />
      </Routes>,
      { route: '/project' },
    );

    expect(await screen.findByTestId('location-probe')).toHaveTextContent(
      '/forbidden?workspace=%2Fw%2Facme',
    );
    expect(screen.queryByText('Analytics unavailable')).toBeNull();
  });

  it('removes stale project figures when a refresh reports lost permission', async () => {
    let dashboardCalls = 0;
    const client = {
      request: vi.fn(async (_method: string, path: string) => {
        if (path.includes('/dashboards/project/')) {
          dashboardCalls += 1;
          if (dashboardCalls > 1) {
            throw new MeshApiError({
              status: 403,
              code: 'project_not_visible',
              message: 'permission revoked',
            });
          }
        }
        return PROJECT_DASHBOARD;
      }),
      list: vi.fn(async () => ({ data: [], next_cursor: null })),
    } as unknown as MeshApiClient;
    renderWithProviders(
      <Routes>
        <Route
          path="/project"
          element={
            <ProjectDashboardPanel
              client={client}
              workspaceId="ws1"
              workspaceSlug="acme"
              projectId="p1"
            />
          }
        />
        <Route path="/forbidden" element={<LocationProbe />} />
      </Routes>,
      { route: '/project' },
    );
    await screen.findByTestId('project-dashboard');

    fireEvent.change(screen.getByTestId('project-dashboard-range'), { target: { value: '90' } });

    expect(await screen.findByTestId('location-probe')).toHaveTextContent(
      '/forbidden?workspace=%2Fw%2Facme',
    );
    expect(screen.queryByTestId('project-dashboard')).toBeNull();
  });

  it('milestone-scoped burndown refetches with milestoneId (not cycleId)', async () => {
    const requests: string[] = [];
    const milestoneDashboard = {
      ...PROJECT_DASHBOARD,
      burndown: { ...PROJECT_DASHBOARD.burndown, scope: { type: 'milestone', id: 'm1' } },
    };
    const client = {
      request: vi.fn(async (_method: string, path: string, opts?: { query?: unknown }) => {
        requests.push(`${path}?${JSON.stringify(opts?.query ?? {})}`);
        if (path.endsWith('/analytics/burndown')) {
          return { ...milestoneDashboard.burndown, metric: 'count', total: 3 };
        }
        return milestoneDashboard;
      }),
      list: vi.fn(async () => ({ data: [], next_cursor: null })),
    } as unknown as MeshApiClient;
    renderWithProviders(<ProjectDashboardPanel client={client} workspaceId="ws1" projectId="p1" />);
    await waitFor(() => {
      expect(screen.getByTestId('project-dashboard-metric')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('project-dashboard-metric'), {
      target: { value: 'count' },
    });
    await waitFor(() => {
      expect(
        requests.some(
          (r) =>
            r.includes('/analytics/burndown') &&
            r.includes('"milestone_id":"m1"') &&
            !r.includes('"cycle_id":"c1"'),
        ),
      ).toBe(true);
    });
  });

  it('null dashboard payload renders the error state (data===null, no error)', async () => {
    const client = makeStubClient(() => null);
    renderWithProviders(<ProjectDashboardPanel client={client} workspaceId="ws1" projectId="p1" />);
    await waitFor(() => {
      expect(screen.getByText('Analytics unavailable')).toBeInTheDocument();
    });
  });

  it('omits the insufficient-data note when insufficient_data is 0', async () => {
    const client = makeStubClient(() => ({
      ...PROJECT_DASHBOARD,
      cycle_time: {
        ...PROJECT_DASHBOARD.cycle_time,
        meta: { insufficient_data: 0, display_timezone: 'UTC' },
      },
    }));
    renderWithProviders(<ProjectDashboardPanel client={client} workspaceId="ws1" projectId="p1" />);
    await waitFor(() => {
      expect(screen.getByTestId('project-dashboard')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('project-dashboard-insufficient')).toBeNull();
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
    expect(screen.getAllByText('90.0%')).toHaveLength(2); // KPI + outcome legend
    expect(screen.getByText('20.0%')).toBeInTheDocument(); // retry
    expect(screen.getAllByText('10.0%')).toHaveLength(2); // KPI + outcome legend
    expect(screen.getByText('1h 1m')).toBeInTheDocument(); // avg duration
    expect(screen.getByTestId('agent-stats-token-note')).toBeInTheDocument();
    expect(screen.getByTestId('agent-stats-executions')).toBeInTheDocument();
    expect(screen.getByTestId('agent-stats-outcomes')).toHaveAttribute('role', 'img');
    expect(screen.getByTestId('agent-token-prompt')).toHaveTextContent('1,000');
    expect(screen.getByTestId('agent-token-completion')).toHaveTextContent('500');
    expect(screen.getByTestId('agent-token-coverage')).toHaveTextContent('40.0%');
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
      expect(screen.getByText('Statistics are not available for this agent.')).toBeInTheDocument();
    });
  });

  it('shows the no-data note when the multi-agent shape comes back', async () => {
    const client = makeStubClient(() => ({ agents: [], meta: {} }));
    renderWithProviders(<AgentStatsCard client={client} workspaceId="ws1" agentId="a1" />);
    await waitFor(() => {
      expect(screen.getByText('No data in this window.')).toBeInTheDocument();
    });
  });

  it('mid success rate (0.7–0.9) renders the warn-tone KPI', async () => {
    const client = makeStubClient(() => ({ ...AGENT_STATS, success_rate: 0.8 }));
    const { container } = renderWithProviders(
      <AgentStatsCard client={client} workspaceId="ws1" agentId="a1" />,
    );
    await waitFor(() => {
      expect(screen.getByTestId('agent-stats-card')).toBeInTheDocument();
    });
    expect(container.querySelector('.mesh-analytics__kpi-big--warning')).not.toBeNull();
  });

  it('low success rate (<0.7) renders the danger-tone KPI', async () => {
    const client = makeStubClient(() => ({ ...AGENT_STATS, success_rate: 0.5 }));
    const { container } = renderWithProviders(
      <AgentStatsCard client={client} workspaceId="ws1" agentId="a1" />,
    );
    await waitFor(() => {
      expect(screen.getByTestId('agent-stats-card')).toBeInTheDocument();
    });
    expect(container.querySelector('.mesh-analytics__kpi-big--danger')).not.toBeNull();
  });

  it('renders unavailable rates without inventing outcome percentages', async () => {
    const client = makeStubClient(() => ({
      ...AGENT_STATS,
      success_rate: Number.NaN,
      timeout_rate: null,
    }));
    renderWithProviders(<AgentStatsCard client={client} workspaceId="ws1" agentId="a1" />);

    const outcomes = await screen.findByTestId('agent-stats-outcomes');
    expect(outcomes).toHaveAccessibleName(/success —, failed —, timeout —/i);
    expect(outcomes.querySelectorAll('[style="inline-size: 0%;"]')).toHaveLength(3);
  });

  it('unmount in flight is a no-op (cancelled guard; no post-unmount state)', async () => {
    let resolveStats: (value: unknown) => void = () => undefined;
    const client = {
      request: vi.fn(
        (_method: string, _path: string, opts?: { readonly signal?: AbortSignal }) =>
          new Promise((resolve) => {
            resolveStats = resolve;
            expect(opts?.signal?.aborted).toBe(false);
          }),
      ),
      list: vi.fn(async () => ({ data: [], next_cursor: null })),
    } as unknown as MeshApiClient;
    const { unmount } = renderWithProviders(
      <AgentStatsCard client={client} workspaceId="ws1" agentId="a1" />,
    );
    await waitFor(() => expect(client.request).toHaveBeenCalled());
    const signal = (client.request as ReturnType<typeof vi.fn>).mock.calls[0][2]?.signal as
      AbortSignal | undefined;
    unmount();
    expect(signal?.aborted).toBe(true);
    // 卸载后落定:cancelled 分支吞掉结果,不抛错
    resolveStats(AGENT_STATS);
    await Promise.resolve();
  });

  it('non-Error rejection falls back to the generic error message', async () => {
    const client = {
      request: vi.fn(async () => {
        throw 'raw failure';
      }),
      list: vi.fn(async () => ({ data: [], next_cursor: null })),
    } as unknown as MeshApiClient;
    renderWithProviders(<AgentStatsCard client={client} workspaceId="ws1" agentId="a1" />);
    await waitFor(() => {
      expect(screen.getByText('Statistics are not available for this agent.')).toBeInTheDocument();
    });
  });
});
