/**
 * 分支补覆盖:以数据变体(稀疏 / 富配置 / 空值)与卸载竞争态触达各
 * `?? / ? :` 的负分支,使 autopilots 各页面 branches 达 per-file 90%。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { AutopilotDetailPage } from '../AutopilotDetailPage';
import { AutopilotEditorPage } from '../AutopilotEditorPage';
import { AutopilotRunDetailPage } from '../AutopilotRunDetailPage';
import { AutopilotsPage } from '../AutopilotsPage';
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

const RICH_RULE = {
  id: 'ap-rich',
  workspace_id: 'ws-1',
  name: '富配置',
  description: 'has description',
  trigger_type: 'issue_status_changed',
  trigger_config: {
    from_status: ['todo'],
    to_status: ['in_progress'],
    watch_fields: ['priority'],
    target_agent_ids: ['ag-1'],
    secret_id: 'sec-1',
    event_types: ['deploy'],
    cron: '0 7 * * *',
    timezone: 'UTC',
    misfire_policy: 'run_all',
    one_time_at: '2026-08-01T00:00:00Z',
  },
  filter_config: {
    labels: ['bug'],
    priorities: ['high'],
    keyword_include: ['a'],
    keyword_exclude: ['b'],
    payload_match: [{ path: 'x', op: 'eq', value: 1 }],
  },
  action_config: [
    { type: 'run_agent_prompt', executor_agent_id: 'ag-1', prompt: 'p' },
    { type: 'create_issue', title: 't', description: 'd', priority: 'high' },
  ],
  executor_agent_id: 'ag-1',
  status: 'active',
  guardrails: {
    rate_limit_overflow: 'queue',
    dedup_window_seconds: 60,
    dedup_key_template: 'k',
    daily_run_budget: 5,
    daily_token_budget: 50,
    approval_required_actions: [],
    kill_switch_paused: false,
    agent_loop_detection: true,
    cascade_max_depth: 1,
    agent_loop_window_seconds: 5,
  },
  max_retries: 1,
  retry_backoff: 'fixed',
  retry_base_seconds: 5,
  retry_max_seconds: 10,
  rate_limit_max: 2,
  rate_limit_window_seconds: 60,
  concurrency_limit: 3,
  require_approval: true,
  next_run_at: null,
  last_run_at: null,
  created_by: 'm-1',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  stats: { runs_30d: 3, success_rate: 0.5 },
};

function stub(routes: (url: string, method: string) => Response | null) {
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    return routes(url, method) ?? fakeResponse({ body: { data: {} } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
}

describe('editor branch fill', () => {
  function renderNew() {
    return renderWithProviders(
      <Routes>
        <Route path="/autopilots/new" element={<AutopilotEditorPage />} />
        <Route path="/autopilots/:autopilotId" element={<div>detail-page</div>} />
        <Route
          path="/w/:workspaceSlug/automations/autopilots/:autopilotId"
          element={<div>detail-page</div>}
        />
      </Routes>,
      { route: '/autopilots/new' },
    );
  }

  function editorStubs() {
    stub((url, method) => {
      if (method === 'GET' && url.includes('/agents'))
        return fakeResponse({
          body: {
            data: [{ id: 'ag-1', name: 'A', lifecycle_status: 'active' }],
            next_cursor: null,
          },
        });
      if (method === 'GET' && url.includes('/webhook-secrets'))
        return fakeResponse({
          body: {
            data: [
              { id: 'sec-1', label: 'prod', status: 'active', created_at: 'x', revoked_at: null },
            ],
            next_cursor: null,
          },
        });
      if (method === 'GET') return fakeResponse({ body: { data: [], next_cursor: null } });
      return fakeResponse({ body: { data: { ...RICH_RULE, id: 'ap-new' } } });
    });
  }

  it('saves issue_status_changed with from/to filters', async () => {
    editorStubs();
    renderNew();
    await waitFor(() => expect(screen.getByTestId('autopilot-editor-name')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('autopilot-editor-name'), 'n');
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-editor-trigger-type'),
      'issue_status_changed',
    );
    await userEvent.type(screen.getByTestId('autopilot-editor-from-status'), 'todo');
    await userEvent.type(screen.getByTestId('autopilot-editor-to-status'), 'in_progress');
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-action-type-0'),
      'send_notification',
    );
    await userEvent.click(screen.getByTestId('autopilot-editor-save'));
    await waitFor(() => expect(screen.getByText('detail-page')).toBeInTheDocument());
  });

  it('saves issue_field_changed and agent_mentioned variants', async () => {
    editorStubs();
    renderNew();
    await waitFor(() => expect(screen.getByTestId('autopilot-editor-name')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('autopilot-editor-name'), 'n');
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-editor-trigger-type'),
      'issue_field_changed',
    );
    await userEvent.type(screen.getByTestId('autopilot-editor-watch-fields'), 'priority');
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-editor-trigger-type'),
      'agent_mentioned',
    );
    await userEvent.type(screen.getByTestId('autopilot-editor-target-agents'), 'ag-1');
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-action-type-0'),
      'send_notification',
    );
    await userEvent.click(screen.getByTestId('autopilot-editor-save'));
    await waitFor(() => expect(screen.getByText('detail-page')).toBeInTheDocument());
  });

  it('saves create_issue with description + action-level executor override', async () => {
    editorStubs();
    renderNew();
    await waitFor(() => expect(screen.getByTestId('autopilot-editor-name')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('autopilot-editor-name'), 'n');
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    await userEvent.selectOptions(screen.getByTestId('autopilot-action-type-0'), 'create_issue');
    await userEvent.type(screen.getByTestId('autopilot-editor-action-issue-title'), 'T');
    await userEvent.type(screen.getByTestId('autopilot-editor-action-issue-description'), 'D');
    await userEvent.click(screen.getByTestId('autopilot-editor-save'));
    await waitFor(() => expect(screen.getByText('detail-page')).toBeInTheDocument());
  });

  it('prefills every rich trigger/filter/guardrail branch in edit mode', async () => {
    stub((url, method) => {
      if (method === 'GET' && url.includes('/agents'))
        return fakeResponse({
          body: {
            data: [{ id: 'ag-1', name: 'A', lifecycle_status: 'active' }],
            next_cursor: null,
          },
        });
      if (method === 'GET' && url.includes('/webhook-secrets'))
        return fakeResponse({
          body: {
            data: [
              { id: 'sec-1', label: 'prod', status: 'active', created_at: 'x', revoked_at: null },
            ],
            next_cursor: null,
          },
        });
      if (method === 'GET' && url.includes('/preview-schedule'))
        return fakeResponse({
          body: { status: 400, body: { error: { code: 'invalid_trigger_config', message: 'x' } } },
        });
      if (method === 'GET') return fakeResponse({ body: { data: RICH_RULE } });
      return fakeResponse({ body: { data: RICH_RULE } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/:autopilotId/edit" element={<AutopilotEditorPage />} />
        <Route path="/autopilots/:autopilotId" element={<div>detail-page</div>} />
      </Routes>,
      { route: '/autopilots/ap-rich/edit' },
    );
    await waitFor(() =>
      expect((screen.getByTestId('autopilot-editor-name') as HTMLInputElement).value).toBe(
        '富配置',
      ),
    );
    // rich trigger → status fields prefilled; switch to schedule to see cron/one-time
    await userEvent.selectOptions(screen.getByTestId('autopilot-editor-trigger-type'), 'schedule');
    expect((screen.getByTestId('autopilot-editor-cron') as HTMLInputElement).value).toBe(
      '0 7 * * *',
    );
    expect((screen.getByTestId('autopilot-editor-one-time') as HTMLInputElement).value).toBe(
      '2026-08-01T00:00:00Z',
    );
    // rich filter prefilled
    await userEvent.click(screen.getByTestId('autopilot-section-filter-toggle'));
    expect((screen.getByTestId('autopilot-editor-filter-labels') as HTMLInputElement).value).toBe(
      'bug',
    );
    expect(screen.getByTestId('autopilot-editor-payload-match')).toBeInTheDocument();
    // guardrails prefilled (require_approval true)
    await userEvent.click(screen.getByTestId('autopilot-section-guardrails-toggle'));
    expect(
      (screen.getByTestId('autopilot-editor-require-approval') as HTMLInputElement).checked,
    ).toBe(true);
    // switch to webhook: secret prefilled (reopen the trigger accordion first)
    await userEvent.click(screen.getByTestId('autopilot-section-trigger-toggle'));
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-editor-trigger-type'),
      'webhook_received',
    );
    expect((screen.getByTestId('autopilot-editor-secret') as HTMLSelectElement).value).toBe(
      'sec-1',
    );
  });
});

describe('detail/list/run/webhook branch fill', () => {
  it('list: schedule rule without cron summary + pause failure toast + dialog X close', async () => {
    stub((url, method) => {
      if (url.endsWith('/autopilots/kill-switch'))
        return fakeResponse({ body: { data: { kill_switch: false } } });
      if (method === 'POST')
        return fakeResponse({ status: 409, body: { error: { code: 'conflict', message: 'x' } } });
      if (method === 'GET')
        return fakeResponse({
          body: {
            data: [{ ...RICH_RULE, id: 'ap-x', trigger_type: 'schedule', trigger_config: {} }],
            next_cursor: null,
          },
        });
      return fakeResponse({ body: { data: {} } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots" element={<AutopilotsPage />} />
      </Routes>,
      { route: '/autopilots' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-row-ap-x')).toBeInTheDocument());
    // kill dialog opened then closed via its X (closeLabel) button — no toast
    // present yet, so the only Close button belongs to the dialog
    await userEvent.click(screen.getByTestId('autopilot-kill-switch-button'));
    await waitFor(() => expect(screen.getByTestId('autopilot-kill-reason')).toBeInTheDocument());
    const closeButtons = screen.getAllByRole('button', { name: /common\.close|^Close$/ });
    await userEvent.click(closeButtons[closeButtons.length - 1]);
    await waitFor(() => expect(screen.queryByTestId('autopilot-kill-reason')).toBeNull());
    // pause fails → toast
    await userEvent.click(screen.getByTestId('autopilot-pause-ap-x'));
    await waitFor(() => expect(screen.getByRole('status').textContent).not.toBe(''));
  });

  it('detail: run with null dates/error/summary + dialog X closes', async () => {
    const NULLY_RUN = {
      id: 'run-2',
      autopilot_id: 'ap-rich',
      workspace_id: 'ws-1',
      trigger_type: 'comment_created',
      trigger_snapshot: {},
      webhook_event_id: null,
      execution_id: null,
      parent_run_id: null,
      cascade_depth: 0,
      status: 'succeeded',
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
          status: 'succeeded',
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
          id: 'a-2',
          artifact_type: 'issue',
          ref_table: 'issues',
          ref_id: 'i-1',
          summary: null,
          created_at: '2026-07-27T00:00:00Z',
        },
      ],
    };
    stub((url, method) => {
      if (method === 'GET' && url.includes('/preview-schedule'))
        return fakeResponse({
          body: { data: { cron: '0 9 * * *', timezone: 'UTC', next_runs: [] } },
        });
      if (method === 'GET' && url.includes('/runs'))
        return fakeResponse({ body: { data: [NULLY_RUN], next_cursor: null } });
      if (method === 'GET')
        return fakeResponse({ body: { data: { ...RICH_RULE, trigger_type: 'schedule' } } });
      return fakeResponse({ body: { data: {} } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/:autopilotId" element={<AutopilotDetailPage />} />
        <Route path="/autopilots/runs/:runId" element={<div>run-detail</div>} />
      </Routes>,
      { route: '/autopilots/ap-rich' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-run-row-run-2')).toBeInTheDocument());
    // open + X-close the test dialog
    await userEvent.click(screen.getByTestId('autopilot-detail-test-run'));
    const closes = screen.getAllByRole('button', { name: /common\.close|^Close$/ });
    await userEvent.click(closes[closes.length - 1]);
    await waitFor(() => expect(screen.queryByTestId('autopilot-test-payload')).toBeNull());
    // open + X-close the delete dialog
    await userEvent.click(screen.getByRole('button', { name: /actions\.delete|^Delete$/ }));
    const closes2 = screen.getAllByRole('button', { name: /common\.close|^Close$/ });
    await userEvent.click(closes2[closes2.length - 1]);
    await waitFor(() => expect(screen.queryByTestId('autopilot-delete-confirm')).toBeNull());
  });

  it('run detail: nully run renders dashes; execution-less run', async () => {
    const NULLY_RUN = {
      id: 'run-3',
      autopilot_id: 'ap-rich',
      workspace_id: 'ws-1',
      trigger_type: 'issue_created',
      trigger_snapshot: {},
      webhook_event_id: null,
      execution_id: null,
      parent_run_id: null,
      cascade_depth: 0,
      status: 'pending',
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
    };
    stub((_url, method) => {
      if (method === 'GET') return fakeResponse({ body: { data: NULLY_RUN } });
      return fakeResponse({ body: { data: {} } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/runs/:runId" element={<AutopilotRunDetailPage />} />
        <Route path="/autopilots/:autopilotId" element={<div>rule</div>} />
      </Routes>,
      { route: '/autopilots/runs/run-3' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-run-status')).toBeInTheDocument());
    // pending run: cancellable, no approval buttons, no attempts/artifacts sections populated
    expect(screen.getByTestId('autopilot-run-cancel')).toBeInTheDocument();
    expect(screen.getByText(/noAttempts|No attempts/i)).toBeInTheDocument();
  });

  it('webhook: blank label falls back to default', async () => {
    stub((_url, method) => {
      if (method === 'POST')
        return fakeResponse({
          body: {
            data: {
              id: 'sec-2',
              label: 'default',
              status: 'active',
              token: 'whk_x',
              secret: 'whs_x',
              created_at: 'x',
            },
          },
        });
      if (method === 'GET') return fakeResponse({ body: { data: [], next_cursor: null } });
      return fakeResponse({ body: { data: {} } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/webhooks" element={<WebhookConfigPage />} />
      </Routes>,
      { route: '/webhooks' },
    );
    await waitFor(() => expect(screen.getByTestId('webhook-create-secret')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('webhook-label-input'), { target: { value: '   ' } });
    await userEvent.click(screen.getByTestId('webhook-create-secret'));
    await waitFor(() => expect(screen.getByTestId('webhook-fresh-credential')).toBeInTheDocument());
  });
});
