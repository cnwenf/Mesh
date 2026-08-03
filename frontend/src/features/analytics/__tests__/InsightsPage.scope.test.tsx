/**
 * Insights 工作区 scope 回归:URL / WorkspaceProvider 是唯一身份源，切换期间不得
 * 展示上一工作区数据；上一 scope 的迟到响应也不得覆盖当前工作区。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { useNavigate, useParams, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import type { WorkspaceDetail } from '../../../api/workspace';
import { renderWithProviders } from '../../../test-utils/render';
import { WorkspaceProvider } from '../../../workspace/WorkspaceProvider';
import { InsightsPage } from '../InsightsPage';
import type { WorkspaceDashboardData } from '../types';

const state = vi.hoisted(() => ({
  aCalls: 0,
  aLate: null as Promise<void> | null,
  analyticsCalls: [] as Array<{ workspaceId: string; signal?: AbortSignal }>,
  dashboardA: null as unknown,
  dashboardALate: null as unknown,
  dashboardB: null as unknown,
}));

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>();
  class StubMeshApiClient {
    async request(
      _method: string,
      path: string,
      opts?: { readonly signal?: AbortSignal },
    ): Promise<unknown> {
      const match = path.match(/\/workspaces\/([^/]+)\/dashboards\/workspace$/);
      if (match === null) throw new Error(`unexpected analytics path: ${path}`);
      const workspaceId = match[1];
      state.analyticsCalls.push({ workspaceId, signal: opts?.signal });
      if (workspaceId === 'ws-a') {
        state.aCalls += 1;
        if (state.aCalls > 1 && state.aLate !== null) await state.aLate;
        return state.aCalls > 1 ? state.dashboardALate : state.dashboardA;
      }
      return state.dashboardB;
    }

    async list(): Promise<{ data: never[]; next_cursor: null }> {
      return { data: [], next_cursor: null };
    }
  }
  return { ...actual, MeshApiClient: StubMeshApiClient, getToken: () => 'token' };
});

function workspace(id: string, slug: string): WorkspaceDetail {
  return {
    id,
    slug,
    name: slug.toUpperCase(),
    logo_url: null,
    timezone: 'UTC',
    settings: {},
    my_role: 'member',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

function dashboard(memberName: string): WorkspaceDashboardData {
  return {
    throughput: {
      granularity: 'day',
      series: [
        {
          label: '2026-08-01',
          bucket: '2026-08-01T00:00:00Z',
          window_start: '2026-08-01T00:00:00Z',
          window_end: '2026-08-02T00:00:00Z',
          created: 1,
          completed: 1,
          net: 0,
        },
      ],
      meta: { calendar_timezone: 'UTC', net_window: 0 },
    },
    workload: {
      data: [
        {
          member_id: memberName,
          display_name: memberName,
          member_type: 'human',
          open_issues: 1,
          running: null,
          queued: null,
          awaiting_approval: null,
        },
      ],
      next_cursor: null,
    },
    agent_stats: { agents: [], meta: {} },
    meta: { visibility_filtered: false, display_timezone: 'UTC' },
  };
}

function RoutedInsights(props: { readonly workspaceClient: MeshApiClient }): React.JSX.Element {
  const navigate = useNavigate();
  const { workspaceSlug = '' } = useParams<{ workspaceSlug: string }>();
  return (
    <>
      <button type="button" onClick={() => navigate('/w/b/insights')}>
        Switch to B
      </button>
      <WorkspaceProvider slug={workspaceSlug} client={props.workspaceClient}>
        <InsightsPage />
      </WorkspaceProvider>
    </>
  );
}

beforeEach(() => {
  state.aCalls = 0;
  state.aLate = null;
  state.analyticsCalls.length = 0;
  state.dashboardA = dashboard('A member');
  state.dashboardALate = dashboard('A late member');
  state.dashboardB = dashboard('B member');
});

describe('InsightsPage workspace scope', () => {
  it('isolates A→B navigation and ignores the aborted A refresh response', async () => {
    let releaseA: () => void = () => undefined;
    state.aLate = new Promise<void>((resolve) => {
      releaseA = resolve;
    });
    const workspaceClient = {
      request: vi.fn(async (_method: string, path: string) => {
        if (path.endsWith('/by-slug/a')) return workspace('ws-a', 'a');
        if (path.endsWith('/by-slug/b')) return workspace('ws-b', 'b');
        throw new Error(`unexpected workspace path: ${path}`);
      }),
      list: vi.fn(async () => ({ data: [], next_cursor: null })),
    } as unknown as MeshApiClient;

    renderWithProviders(
      <Routes>
        <Route
          path="/w/:workspaceSlug/insights"
          element={<RoutedInsights workspaceClient={workspaceClient} />}
        />
      </Routes>,
      { route: '/w/a/insights' },
    );
    expect(await screen.findByText('A member (human)')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('insights-range'), { target: { value: '90' } });
    await waitFor(() => expect(state.aCalls).toBe(2));
    const staleSignal = state.analyticsCalls.at(-1)?.signal;

    fireEvent.click(screen.getByRole('button', { name: 'Switch to B' }));
    expect(screen.queryByText('A member (human)')).toBeNull();
    expect(await screen.findByText('B member (human)')).toBeInTheDocument();
    expect(state.analyticsCalls.some((call) => call.workspaceId === 'ws-b')).toBe(true);
    expect(staleSignal?.aborted).toBe(true);

    await act(async () => releaseA());
    expect(screen.queryByText('A late member (human)')).toBeNull();
    expect(screen.getByText('B member (human)')).toBeInTheDocument();
  });
});
