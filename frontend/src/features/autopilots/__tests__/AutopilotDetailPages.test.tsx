/**
 * AutopilotDetailPage / AutopilotRunDetailPage / WebhookConfigPage 组件测试
 * (autopilot.md §4.1 / §4.2 / §4.3):配置卡片 + 运行时间线 + test-run,
 * 运行详情审批/取消,webhook 凭据仅显示一次。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { AutopilotDetailPage } from '../AutopilotDetailPage';
import { AutopilotRunDetailPage } from '../AutopilotRunDetailPage';
import { WebhookConfigPage } from '../WebhookConfigPage';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const ME = {
  user: { id: 'u-1', email: 'o@x.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'T',
      workspace_slug: 't',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};

const RULE = {
  id: 'ap-1',
  workspace_id: 'ws-1',
  name: '每日汇总',
  description: null,
  trigger_type: 'schedule',
  trigger_config: { cron: '0 9 * * *', timezone: 'UTC' },
  filter_config: {},
  action_config: [{ type: 'send_notification', message: 'x' }],
  executor_agent_id: null,
  status: 'active',
  guardrails: { cascade_max_depth: 3 },
  max_retries: 3,
  retry_backoff: 'exponential',
  retry_base_seconds: 30,
  retry_max_seconds: 1800,
  rate_limit_max: 10,
  rate_limit_window_seconds: 3600,
  concurrency_limit: 1,
  require_approval: false,
  next_run_at: null,
  last_run_at: null,
  created_by: 'm-1',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  stats: null,
};

const RUN_WAITING = {
  id: 'run-1',
  autopilot_id: 'ap-1',
  workspace_id: 'ws-1',
  trigger_type: 'schedule',
  trigger_snapshot: { event_id: 'evt-1' },
  webhook_event_id: null,
  execution_id: null,
  parent_run_id: null,
  cascade_depth: 0,
  status: 'waiting_approval',
  started_at: null,
  finished_at: null,
  duration_ms: null,
  retry_count: 0,
  error: null,
  prompt_tokens: null,
  completion_tokens: null,
  total_tokens: 0,
  triggered_by: null,
  is_test: false,
  created_at: '2026-07-27T00:00:00Z',
  updated_at: '2026-07-27T00:00:00Z',
  attempts: [
    {
      attempt_number: 1,
      status: 'running',
      execution_id: null,
      started_at: null,
      finished_at: null,
      error: null,
      prompt_tokens: null,
      completion_tokens: null,
    },
  ],
  artifacts: [
    {
      id: 'a-1',
      artifact_type: 'comment',
      ref_table: 'comments',
      ref_id: 'c-1',
      summary: 'ok',
      created_at: '2026-07-27T00:00:00Z',
    },
  ],
};

interface Recorded {
  url: string;
  method: string;
}

function setupDetail(me = ME, runsResponse?: Promise<Response>): Recorded[] {
  const calls: Recorded[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
    if (url.includes('/preview-schedule'))
      return fakeResponse({
        body: { data: { cron: '0 9 * * *', timezone: 'UTC', next_runs: ['2026-07-28T09:00:00Z'] } },
      });
    if (url.includes('/runs'))
      return runsResponse ?? fakeResponse({ body: { data: [RUN_WAITING], next_cursor: null } });
    if (method !== 'GET') return fakeResponse({ body: { data: { ...RULE, status: 'paused' } } });
    return fakeResponse({ body: { data: RULE } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

describe('AutopilotDetailPage', () => {
  it('renders config card and run history', async () => {
    setupDetail();
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/:autopilotId" element={<AutopilotDetailPage />} />
        <Route path="/autopilots/runs/:runId" element={<div>run-detail</div>} />
        <Route path="/autopilots/:autopilotId/edit" element={<div>editor</div>} />
        <Route
          path="/w/:workspaceSlug/automations/autopilots/runs/:runId"
          element={<div>run-detail</div>}
        />
        <Route
          path="/w/:workspaceSlug/automations/autopilots/:autopilotId/edit"
          element={<div>editor</div>}
        />
      </Routes>,
      { route: '/autopilots/ap-1' },
    );
    await waitFor(() =>
      expect(screen.getByTestId('autopilot-detail-name')).toHaveTextContent('每日汇总'),
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-runs-table')).toBeInTheDocument());
    expect(screen.getAllByRole('heading', { level: 2 })).toHaveLength(2);
    expect(screen.queryByRole('heading', { level: 3 })).toBeNull();
    expect(document.querySelectorAll('.mesh-autopilots__json')).toHaveLength(3);
    for (const jsonRegion of document.querySelectorAll('.mesh-autopilots__json')) {
      expect(jsonRegion).toHaveAttribute('tabindex', '0');
    }
    expect(screen.getByTestId('autopilot-run-row-run-1')).toBeInTheDocument();
    // clicking a run row navigates to the run detail
    await userEvent.click(screen.getByTestId('autopilot-run-row-run-1'));
    await waitFor(() => expect(screen.getByText('run-detail')).toBeInTheDocument());
  });

  it('submits a dry-run test', async () => {
    const calls = setupDetail();
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/:autopilotId" element={<AutopilotDetailPage />} />
        <Route path="/autopilots/runs/:runId" element={<div>run-detail</div>} />
        <Route
          path="/w/:workspaceSlug/automations/autopilots/runs/:runId"
          element={<div>run-detail</div>}
        />
      </Routes>,
      { route: '/autopilots/ap-1' },
    );
    await waitFor(() =>
      expect(screen.getByTestId('autopilot-detail-test-run')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByTestId('autopilot-detail-test-run'));
    await userEvent.click(screen.getByTestId('autopilot-test-dry-run'));
    await userEvent.click(screen.getByTestId('autopilot-test-submit'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.includes('/test-run') && call.method === 'POST')).toBe(
        true,
      ),
    );
  });

  it('pauses from the detail header', async () => {
    const calls = setupDetail();
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/:autopilotId" element={<AutopilotDetailPage />} />
      </Routes>,
      { route: '/autopilots/ap-1' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-detail-pause')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('autopilot-detail-pause'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith('/autopilots/ap-1/pause'))).toBe(true),
    );
  });

  it('keeps the detail read-only for members', async () => {
    setupDetail({
      ...ME,
      memberships: [{ ...ME.memberships[0], role: 'member' }],
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/:autopilotId" element={<AutopilotDetailPage />} />
      </Routes>,
      { route: '/autopilots/ap-1' },
    );

    expect(await screen.findByTestId('autopilot-detail-name')).toHaveTextContent('每日汇总');
    expect(screen.queryByTestId('autopilot-detail-pause')).toBeNull();
    expect(screen.queryByTestId('autopilot-detail-test-run')).toBeNull();
  });

  it('shows the no-workspace state before loading a rule', async () => {
    const calls = setupDetail({ ...ME, memberships: [] });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/:autopilotId" element={<AutopilotDetailPage />} />
      </Routes>,
      { route: '/autopilots/ap-1' },
    );

    expect(await screen.findByText('No workspace')).toBeInTheDocument();
    expect(calls.some((call) => call.url.includes('/autopilots/ap-1'))).toBe(false);
  });

  it('keeps a rule-less route in its loading shell', async () => {
    setupDetail();
    renderWithProviders(
      <Routes>
        <Route path="/autopilots" element={<AutopilotDetailPage />} />
      </Routes>,
      { route: '/autopilots' },
    );

    expect(await screen.findByText('Loading automation rules…')).toBeInTheDocument();
  });

  it('shows the run-history skeleton while the run request is pending', async () => {
    let resolveRuns!: (response: Response) => void;
    const runsResponse = new Promise<Response>((resolve) => {
      resolveRuns = resolve;
    });
    setupDetail(ME, runsResponse);
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/:autopilotId" element={<AutopilotDetailPage />} />
      </Routes>,
      { route: '/autopilots/ap-1' },
    );

    await screen.findByTestId('autopilot-detail-name');
    expect(screen.queryByTestId('autopilot-runs-table')).toBeNull();
    expect(screen.getByText('Loading automation rules…')).toBeInTheDocument();

    resolveRuns(fakeResponse({ body: { data: [RUN_WAITING], next_cursor: null } }));
    expect(await screen.findByTestId('autopilot-runs-table')).toBeInTheDocument();
  });
});

function setupRunDetail(runOverrides: Record<string, unknown> = {}, me = ME): Recorded[] {
  const calls: Recorded[] = [];
  const run = { ...RUN_WAITING, ...runOverrides };
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
    if (method !== 'GET') return fakeResponse({ body: { data: { status: 'approved' } } });
    return fakeResponse({ body: { data: run } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

describe('AutopilotRunDetailPage', () => {
  it('shows approve/reject for waiting_approval runs', async () => {
    const calls = setupRunDetail();
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/runs/:runId" element={<AutopilotRunDetailPage />} />
        <Route path="/autopilots/:autopilotId" element={<div>rule</div>} />
        <Route
          path="/w/:workspaceSlug/automations/autopilots/:autopilotId"
          element={<div>rule</div>}
        />
      </Routes>,
      { route: '/autopilots/runs/run-1' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-run-snapshot')).toBeInTheDocument());
    expect(screen.getByTestId('autopilot-run-snapshot')).toHaveAttribute('tabindex', '0');
    expect(screen.getAllByRole('heading', { level: 2 })).toHaveLength(4);
    expect(screen.queryByRole('heading', { level: 3 })).toBeNull();
    expect(screen.getByTestId('autopilot-run-approve')).toBeInTheDocument();
    expect(screen.getByTestId('autopilot-run-reject')).toBeInTheDocument();
    expect(screen.getByTestId('autopilot-run-attempts')).toBeInTheDocument();
    expect(screen.getByTestId('autopilot-run-artifacts')).toBeInTheDocument();
    await userEvent.click(screen.getByTestId('autopilot-run-approve'));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith('/approve'))).toBe(true));
  });

  it('shows cancel for running runs and hides approval actions', async () => {
    setupRunDetail({ status: 'running' });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/runs/:runId" element={<AutopilotRunDetailPage />} />
      </Routes>,
      { route: '/autopilots/runs/run-1' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-run-cancel')).toBeInTheDocument());
    expect(screen.queryByTestId('autopilot-run-approve')).toBeNull();
  });

  it('hides actions for terminal runs', async () => {
    setupRunDetail({ status: 'succeeded' });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/runs/:runId" element={<AutopilotRunDetailPage />} />
      </Routes>,
      { route: '/autopilots/runs/run-1' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-run-status')).toBeInTheDocument());
    expect(screen.queryByTestId('autopilot-run-cancel')).toBeNull();
    expect(screen.queryByTestId('autopilot-run-approve')).toBeNull();
  });

  it('keeps approval visible to members while hiding management and maps artifact routes', async () => {
    setupRunDetail(
      {
        artifacts: [
          {
            id: 'a-task',
            artifact_type: 'agent_output',
            ref_table: 'task_executions',
            ref_id: 'exec-1',
            summary: null,
            created_at: '2026-07-27T00:00:00Z',
          },
          {
            id: 'a-notification',
            artifact_type: 'notification',
            ref_table: 'notifications',
            ref_id: 'notification-1',
            summary: 'sent',
            created_at: '2026-07-27T00:00:00Z',
          },
          {
            id: 'a-unknown',
            artifact_type: 'http_response',
            ref_table: 'external_results',
            ref_id: 'external-1',
            summary: null,
            created_at: '2026-07-27T00:00:00Z',
          },
        ],
      },
      {
        ...ME,
        memberships: [{ ...ME.memberships[0], role: 'member' }],
      },
    );
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/runs/:runId" element={<AutopilotRunDetailPage />} />
      </Routes>,
      { route: '/autopilots/runs/run-1' },
    );

    expect(await screen.findByTestId('autopilot-run-approve')).toBeInTheDocument();
    expect(screen.queryByTestId('autopilot-run-cancel')).toBeNull();
    expect(screen.getByTestId('autopilot-artifact-link-a-task')).toBeInTheDocument();
    expect(screen.getByTestId('autopilot-artifact-link-a-notification')).toBeInTheDocument();
    expect(screen.queryByTestId('autopilot-artifact-link-a-unknown')).toBeNull();
  });

  it('shows the no-workspace state without requesting a run', async () => {
    const calls = setupRunDetail({}, { ...ME, memberships: [] });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/runs/:runId" element={<AutopilotRunDetailPage />} />
      </Routes>,
      { route: '/autopilots/runs/run-1' },
    );

    expect(await screen.findByText('No workspace')).toBeInTheDocument();
    expect(calls.some((call) => call.url.includes('/autopilot-runs/run-1'))).toBe(false);
  });

  it('shows an error when workspace membership cannot be resolved', async () => {
    vi.stubGlobal('fetch', (async () =>
      fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'boom' } },
      })) as typeof fetch);
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/runs/:runId" element={<AutopilotRunDetailPage />} />
      </Routes>,
      { route: '/autopilots/runs/run-1' },
    );

    expect(
      await screen.findByText('An unexpected error occurred. Please try again.'),
    ).toBeInTheDocument();
  });
});

describe('WebhookConfigPage', () => {
  it('creates a credential and shows it exactly once', async () => {
    const calls: Recorded[] = [];
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({ url, method });
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'POST')
        return fakeResponse({
          body: {
            data: {
              id: 'sec-1',
              label: 'default',
              status: 'active',
              token: 'whk_tok',
              secret: 'whs_sec',
              created_at: '2026-07-27T00:00:00Z',
            },
          },
        });
      return fakeResponse({
        body: {
          data: [
            {
              id: 'sec-1',
              label: 'default',
              status: 'active',
              created_at: '2026-07-27T00:00:00Z',
              revoked_at: null,
            },
          ],
          next_cursor: null,
        },
      });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(
      <Routes>
        <Route path="/webhooks" element={<WebhookConfigPage />} />
      </Routes>,
      { route: '/webhooks' },
    );
    await waitFor(() => expect(screen.getByTestId('webhook-create-secret')).toBeInTheDocument());
    expect(screen.getAllByRole('heading', { level: 2 })).toHaveLength(1);
    expect(screen.queryByRole('heading', { level: 3 })).toBeNull();
    await userEvent.click(screen.getByTestId('webhook-create-secret'));
    await waitFor(() => expect(screen.getByTestId('webhook-fresh-credential')).toBeInTheDocument());
    expect(screen.getByTestId('webhook-fresh-secret')).toHaveTextContent('whs_sec');
    expect(screen.getByTestId('webhook-fresh-url').textContent).toContain('whk_tok');
    expect(
      calls.some((call) => call.url.includes('/webhook-secrets') && call.method === 'POST'),
    ).toBe(true);
  });

  it('lists existing secrets without echoing material', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      return fakeResponse({
        body: {
          data: [
            {
              id: 'sec-9',
              label: 'prod',
              status: 'active',
              created_at: '2026-07-27T00:00:00Z',
              revoked_at: null,
            },
          ],
          next_cursor: null,
        },
      });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(
      <Routes>
        <Route path="/webhooks" element={<WebhookConfigPage />} />
      </Routes>,
      { route: '/webhooks' },
    );
    await waitFor(() => expect(screen.getByTestId('webhook-secret-row-sec-9')).toBeInTheDocument());
    expect(screen.getByTestId('webhook-secrets-table').textContent).not.toContain('whs_');
  });
});
