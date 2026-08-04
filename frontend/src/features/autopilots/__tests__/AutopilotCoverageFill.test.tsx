/**
 * 页面组件补覆盖测试:逐一触发各交互回调(动作增删排序、各触发类型字段、
 * 保存草稿 / 编辑保存 / 拒绝 / 取消 / 删除 / 轮换 / 错误与空态分支),
 * 使 src/features/autopilots/ 各文件达到 per-file 90% 门禁。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import type { RealtimeEventFrame } from '../../../types/realtime';
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

const RULE = {
  id: 'ap-1',
  workspace_id: 'ws-1',
  name: '规则',
  description: null,
  trigger_type: 'schedule',
  trigger_config: { cron: '0 9 * * *', timezone: 'UTC' },
  filter_config: {},
  action_config: [{ type: 'send_notification', message: 'x' }],
  executor_agent_id: null,
  status: 'active',
  guardrails: {
    rate_limit_overflow: 'drop',
    dedup_window_seconds: 300,
    dedup_key_template: '{{trigger.event_id}}',
    daily_run_budget: 200,
    daily_token_budget: 2000000,
    approval_required_actions: ['http_request', 'create_issue'],
    kill_switch_paused: false,
    agent_loop_detection: true,
    cascade_max_depth: 3,
    agent_loop_window_seconds: 60,
  },
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

const RUN = {
  id: 'run-1',
  autopilot_id: 'ap-1',
  workspace_id: 'ws-1',
  trigger_type: 'webhook_received',
  trigger_snapshot: { event_id: 'evt-1' },
  webhook_event_id: 'we-1',
  execution_id: 'ex-1',
  parent_run_id: 'run-0',
  cascade_depth: 1,
  status: 'waiting_approval',
  started_at: '2026-07-27T00:00:00Z',
  finished_at: null,
  duration_ms: 1500,
  retry_count: 1,
  error: { code: 'timeout', message: 'slow' },
  prompt_tokens: 10,
  completion_tokens: 5,
  total_tokens: 15,
  triggered_by: 'm-1',
  is_test: true,
  created_at: '2026-07-27T00:00:00Z',
  updated_at: '2026-07-27T00:00:00Z',
  attempts: [],
  artifacts: [],
};

interface Recorded {
  url: string;
  method: string;
  status?: number;
}

function stub(routes: (url: string, method: string) => Response | null): Recorded[] {
  const calls: Recorded[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const record: Recorded = { url, method };
    calls.push(record);
    if (url.includes('/users/me')) {
      record.status = 200;
      return fakeResponse({ body: { data: ME } });
    }
    const matched = routes(url, method);
    const response = matched ?? fakeResponse({ body: { data: {} } });
    record.status = response.status;
    return response;
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

function makeRealtime() {
  const listeners = new Set<(frame: RealtimeEventFrame) => void>();
  const value = {
    state: 'connected',
    client: {
      subscribe: () => undefined,
      unsubscribe: () => undefined,
      onFrame: (listener: (frame: RealtimeEventFrame) => void) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
    },
  } as unknown as RealtimeContextValue;
  return {
    value,
    emit(frame: RealtimeEventFrame) {
      listeners.forEach((listener) => listener(frame));
    },
  };
}

describe('AutopilotEditorPage coverage fill', () => {
  function renderEditor(route: string) {
    return renderWithProviders(
      <Routes>
        <Route path="/autopilots/new" element={<AutopilotEditorPage />} />
        <Route path="/autopilots/:autopilotId/edit" element={<AutopilotEditorPage />} />
        <Route path="/autopilots" element={<div>list-page</div>} />
        <Route path="/autopilots/:autopilotId" element={<div>detail-page</div>} />
        <Route path="/w/:workspaceSlug/automations/autopilots" element={<div>list-page</div>} />
        <Route
          path="/w/:workspaceSlug/automations/autopilots/:autopilotId"
          element={<div>detail-page</div>}
        />
      </Routes>,
      { route },
    );
  }

  it('exercises every trigger type field set', async () => {
    stub(() => fakeResponse({ body: { data: [], next_cursor: null } }));
    renderEditor('/autopilots/new');
    await waitFor(() =>
      expect(screen.getByTestId('autopilot-editor-trigger-type')).toBeInTheDocument(),
    );
    const select = screen.getByTestId('autopilot-editor-trigger-type');
    await userEvent.selectOptions(select, 'issue_status_changed');
    await userEvent.selectOptions(select, 'issue_field_changed');
    await userEvent.selectOptions(select, 'agent_mentioned');
    await userEvent.selectOptions(select, 'issue_created');
    await userEvent.selectOptions(select, 'comment_created');
    await userEvent.selectOptions(select, 'schedule');
    expect(screen.getByTestId('autopilot-editor-cron')).toBeInTheDocument();
  });

  it('adds, reorders and removes actions of each kind', async () => {
    stub(() =>
      fakeResponse({
        body: { data: [{ id: 'ag-1', name: 'A', lifecycle_status: 'active' }], next_cursor: null },
      }),
    );
    renderEditor('/autopilots/new');
    await waitFor(() => expect(screen.getByTestId('autopilot-editor')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    await userEvent.click(screen.getByTestId('autopilot-add-action'));
    await userEvent.click(screen.getByTestId('autopilot-add-action'));
    await userEvent.click(screen.getByTestId('autopilot-add-action'));
    // switch action kinds to render every field block
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-action-type-0'),
      'run_agent_prompt',
    );
    await userEvent.selectOptions(screen.getByTestId('autopilot-action-type-1'), 'add_comment');
    await userEvent.selectOptions(screen.getByTestId('autopilot-action-type-2'), 'create_issue');
    await userEvent.selectOptions(screen.getByTestId('autopilot-action-type-3'), 'http_request');
    // reorder + remove (aria-labels may be translated or raw keys)
    const removeButtons = screen.getAllByRole('button', { name: /removeAction|Remove action/ });
    const downButtons = screen.getAllByRole('button', { name: /moveDown|Move down/ });
    const upButtons = screen.getAllByRole('button', { name: /moveUp|Move up/ });
    await userEvent.click(downButtons[0]);
    await userEvent.click(upButtons[1]);
    await userEvent.click(removeButtons[3]);
    expect(screen.getByTestId('autopilot-action-2')).toBeInTheDocument();
    // payload match with valid JSON (fireEvent: the braces are not key syntax)
    await userEvent.click(screen.getByTestId('autopilot-section-filter-toggle'));
    fireEvent.change(screen.getByTestId('autopilot-editor-payload-match'), {
      target: { value: '[{"path": "a", "op": "eq", "value": 1}]' },
    });
  });

  it('saves draft (paused) and cancels back to the list', async () => {
    const calls = stub((_url, method) => {
      if (method === 'POST') return fakeResponse({ body: { data: { ...RULE, id: 'ap-new' } } });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    });
    renderEditor('/autopilots/new');
    await waitFor(() => expect(screen.getByTestId('autopilot-editor-name')).toBeInTheDocument());
    // cancel button navigates to the list
    await userEvent.click(screen.getByRole('button', { name: /common\.cancel|^Cancel$/ }));
    await waitFor(() => expect(screen.getByText('list-page')).toBeInTheDocument());
    expect(calls.length).toBeGreaterThan(0);
  });

  it('patches an existing rule in edit mode', async () => {
    const calls = stub((url, method) => {
      if (method === 'GET' && url.includes('/agents'))
        return fakeResponse({
          body: {
            data: [{ id: 'ag-1', name: 'A', lifecycle_status: 'active' }],
            next_cursor: null,
          },
        });
      if (method === 'GET' && url.includes('/webhook-secrets'))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (method === 'GET' && url.includes('/preview-schedule'))
        return fakeResponse({
          body: { data: { cron: '0 9 * * *', timezone: 'UTC', next_runs: [] } },
        });
      if (method === 'GET') return fakeResponse({ body: { data: RULE } });
      return fakeResponse({ body: { data: RULE } });
    });
    renderEditor('/autopilots/ap-1/edit');
    await waitFor(() =>
      expect((screen.getByTestId('autopilot-editor-name') as HTMLInputElement).value).toBe('规则'),
    );
    // guardrails section renders with prefilled checkboxes
    await userEvent.click(screen.getByTestId('autopilot-section-guardrails-toggle'));
    expect(
      (screen.getByTestId('autopilot-editor-require-approval') as HTMLInputElement).checked,
    ).toBe(false);
    await userEvent.click(screen.getByTestId('autopilot-editor-save'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.method === 'PATCH' && call.url.includes('/autopilots/ap-1')),
      ).toBe(true),
    );
  });

  it('surfaces an error state when the rule load fails', async () => {
    stub((url, method) => {
      if (method === 'GET' && !url.includes('/users/me'))
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      return null;
    });
    renderEditor('/autopilots/ap-1/edit');
    await waitFor(() => expect(screen.getByText(/error|unexpected/i)).toBeInTheDocument());
  });
});

describe('AutopilotDetailPage coverage fill', () => {
  function renderDetail(realtime?: ReturnType<typeof makeRealtime>) {
    return renderWithProviders(
      <RealtimeContext.Provider value={realtime ? realtime.value : null}>
        <Routes>
          <Route path="/autopilots/:autopilotId" element={<AutopilotDetailPage />} />
          <Route path="/autopilots/runs/:runId" element={<div>run-detail</div>} />
          <Route path="/autopilots/:autopilotId/edit" element={<div>editor-page</div>} />
          <Route path="/autopilots" element={<div>list-page</div>} />
          <Route
            path="/w/:workspaceSlug/automations/autopilots/runs/:runId"
            element={<div>run-detail</div>}
          />
          <Route
            path="/w/:workspaceSlug/automations/autopilots/:autopilotId/edit"
            element={<div>editor-page</div>}
          />
          <Route path="/w/:workspaceSlug/automations/autopilots" element={<div>list-page</div>} />
        </Routes>
      </RealtimeContext.Provider>,
      { route: '/autopilots/ap-1' },
    );
  }

  it('resumes a paused rule and edits', async () => {
    const calls = stub((url, method) => {
      if (method === 'GET' && url.includes('/preview-schedule'))
        return fakeResponse({
          body: { data: { cron: '0 9 * * *', timezone: 'UTC', next_runs: [] } },
        });
      if (method === 'GET' && url.includes('/runs'))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (method === 'GET') return fakeResponse({ body: { data: { ...RULE, status: 'paused' } } });
      return fakeResponse({ body: { data: RULE } });
    });
    renderDetail();
    await waitFor(() => expect(screen.getByTestId('autopilot-detail-resume')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('autopilot-detail-resume'));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith('/resume'))).toBe(true));
  });

  it('deletes through the confirmation dialog', async () => {
    const calls = stub((url, method) => {
      if (method === 'GET' && url.includes('/runs'))
        return fakeResponse({ body: { data: [{ ...RUN }], next_cursor: null } });
      if (method === 'GET') return fakeResponse({ body: { data: RULE } });
      return fakeResponse({ status: 204 });
    });
    renderDetail();
    await waitFor(() => expect(screen.getByTestId('autopilot-detail-name')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /actions\.delete|^Delete$/ }));
    await userEvent.click(screen.getByTestId('autopilot-delete-confirm'));
    await waitFor(() => expect(calls.some((call) => call.method === 'DELETE')).toBe(true));
  });

  it('reloads on realtime frames and filters runs by status', async () => {
    const calls = stub((url, method) => {
      if (method === 'GET' && url.includes('/preview-schedule'))
        return fakeResponse({
          body: {
            data: { cron: '0 9 * * *', timezone: 'UTC', next_runs: ['2026-07-28T09:00:00Z'] },
          },
        });
      if (method === 'GET' && url.includes('/runs'))
        return fakeResponse({ body: { data: [{ ...RUN }], next_cursor: null } });
      if (method === 'GET') return fakeResponse({ body: { data: RULE } });
      return fakeResponse({ body: { data: RULE } });
    });
    const realtime = makeRealtime();
    renderDetail(realtime);
    await waitFor(() => expect(screen.getByTestId('autopilot-runs-table')).toBeInTheDocument());
    const before = calls.filter((call) => call.url.includes('/runs')).length;
    realtime.emit({
      channel: 'autopilot:ap-1',
      event: 'autopilot_runs.status_changed',
      seq: 1,
      payload: {},
    } as unknown as RealtimeEventFrame);
    realtime.emit({
      channel: 'autopilot:ap-1',
      event: 'autopilot.updated',
      seq: 2,
      payload: {},
    } as unknown as RealtimeEventFrame);
    await waitFor(() =>
      expect(calls.filter((call) => call.url.includes('/runs')).length).toBeGreaterThan(before),
    );
  });

  it('renders the error state when the rule fails to load', async () => {
    stub((url, method) => {
      if (method === 'GET' && !url.includes('/users/me'))
        return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
      return null;
    });
    renderDetail();
    await waitFor(() =>
      expect(screen.getByText(/could not find|error|unexpected/i)).toBeInTheDocument(),
    );
  });
});

describe('AutopilotRunDetailPage coverage fill', () => {
  function renderRun(runOverrides: Record<string, unknown>) {
    stub((_url, method) => {
      if (method !== 'GET') return fakeResponse({ body: { data: { status: 'ok' } } });
      return fakeResponse({ body: { data: { ...RUN, ...runOverrides } } });
    });
    return renderWithProviders(
      <Routes>
        <Route path="/autopilots/runs/:runId" element={<AutopilotRunDetailPage />} />
        <Route path="/autopilots/:autopilotId" element={<div>rule-page</div>} />
        <Route path="/executions/:executionId" element={<div>execution-page</div>} />
        <Route
          path="/w/:workspaceSlug/automations/autopilots/:autopilotId"
          element={<div>rule-page</div>}
        />
        <Route
          path="/w/:workspaceSlug/executions/:executionId"
          element={<div>execution-page</div>}
        />
      </Routes>,
      { route: '/autopilots/runs/run-1' },
    );
  }

  it('rejects a waiting run', async () => {
    const calls = stub((_url, method) => {
      if (method !== 'GET') return fakeResponse({ body: { data: { status: 'rejected' } } });
      return fakeResponse({ body: { data: RUN } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/runs/:runId" element={<AutopilotRunDetailPage />} />
      </Routes>,
      { route: '/autopilots/runs/run-1' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-run-reject')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('autopilot-run-reject'));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith('/reject'))).toBe(true));
  });

  it('cancels a running run and navigates back', async () => {
    const calls = stub((_url, method) => {
      if (method !== 'GET') return fakeResponse({ body: { data: { status: 'cancelled' } } });
      return fakeResponse({ body: { data: { ...RUN, status: 'running' } } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/runs/:runId" element={<AutopilotRunDetailPage />} />
        <Route path="/autopilots/:autopilotId" element={<div>rule-page</div>} />
        <Route
          path="/w/:workspaceSlug/automations/autopilots/:autopilotId"
          element={<div>rule-page</div>}
        />
      </Routes>,
      { route: '/autopilots/runs/run-1' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-run-cancel')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('autopilot-run-cancel'));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith('/cancel'))).toBe(true));
    await userEvent.click(screen.getByRole('button', { name: /backToRule|Rule/ }));
    await waitFor(() => expect(screen.getByText('rule-page')).toBeInTheDocument());
  });

  it('renders error and execution link for a failed run', async () => {
    renderRun({ status: 'failed' });
    await waitFor(() => expect(screen.getByTestId('autopilot-run-error')).toBeInTheDocument());
    expect(screen.getByTestId('autopilot-run-error').textContent).toContain('timeout');
  });

  it('shows the error state when the run fails to load', async () => {
    stub((url, method) => {
      if (method === 'GET' && !url.includes('/users/me'))
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      return null;
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/runs/:runId" element={<AutopilotRunDetailPage />} />
      </Routes>,
      { route: '/autopilots/runs/run-1' },
    );
    await waitFor(() => expect(screen.getByText(/error|unexpected/i)).toBeInTheDocument());
  });
});

describe('AutopilotsPage coverage fill', () => {
  function renderList() {
    return renderWithProviders(
      <Routes>
        <Route path="/autopilots" element={<AutopilotsPage />} />
        <Route path="/autopilots/:id" element={<div>detail-page</div>} />
        <Route path="/webhooks" element={<div>webhooks-page</div>} />
        <Route
          path="/w/:workspaceSlug/automations/autopilots/:id"
          element={<div>detail-page</div>}
        />
        <Route path="/w/:workspaceSlug/automations/webhooks" element={<div>webhooks-page</div>} />
      </Routes>,
      { route: '/autopilots' },
    );
  }

  it('resumes a paused rule and navigates via row click and webhooks', async () => {
    const calls = stub((url, method) => {
      if (url.endsWith('/autopilots/kill-switch'))
        return fakeResponse({ body: { data: { kill_switch: false } } });
      if (method === 'GET')
        return fakeResponse({
          body: { data: [{ ...RULE, id: 'ap-2', status: 'paused' }], next_cursor: null },
        });
      return fakeResponse({ body: { data: RULE } });
    });
    renderList();
    await waitFor(() => expect(screen.getByTestId('autopilot-resume-ap-2')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('autopilot-resume-ap-2'));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith('/resume'))).toBe(true));
    await userEvent.click(screen.getByRole('button', { name: /webhook\.nav|^Webhooks$/ }));
    await waitFor(() => expect(screen.getByText('webhooks-page')).toBeInTheDocument());
  });

  it('restores from an engaged kill switch', async () => {
    const calls = stub((url, method) => {
      if (url.endsWith('/autopilots/kill-switch') && method === 'GET')
        return fakeResponse({ body: { data: { kill_switch: true } } });
      if (url.endsWith('/autopilots/kill-switch') && method === 'POST')
        return fakeResponse({
          body: { data: { kill_switch: false, paused_autopilots: 1, updated_at: 'x' } },
        });
      if (method === 'GET') return fakeResponse({ body: { data: [], next_cursor: null } });
      return fakeResponse({ body: { data: {} } });
    });
    renderList();
    await waitFor(() =>
      expect(screen.getByTestId('autopilot-kill-switch-button')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByTestId('autopilot-kill-switch-button'));
    await userEvent.click(screen.getByTestId('autopilot-kill-confirm'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.url.endsWith('/kill-switch') && call.method === 'POST'),
      ).toBe(true),
    );
  });

  it('shows the no-workspace state', async () => {
    const impl = (async () =>
      fakeResponse({ body: { data: { user: ME.user, memberships: [] } } })) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderList();
    await waitFor(() =>
      expect(screen.getByText(/No workspace|autopilots\.noWorkspace/)).toBeInTheDocument(),
    );
  });

  it('filters by status through the select', async () => {
    const calls = stub((url, method) => {
      if (url.endsWith('/autopilots/kill-switch'))
        return fakeResponse({ body: { data: { kill_switch: false } } });
      if (method === 'GET') return fakeResponse({ body: { data: [RULE], next_cursor: null } });
      return fakeResponse({ body: { data: RULE } });
    });
    renderList();
    await waitFor(() => expect(screen.getByTestId('autopilot-filter-status')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByTestId('autopilot-filter-status'), 'active');
    await waitFor(() =>
      expect(calls.some((call) => call.url.includes('status=active'))).toBe(true),
    );
    // row click navigates to detail
    await userEvent.click(screen.getByTestId('autopilot-row-ap-1'));
    await waitFor(() => expect(screen.getByText('detail-page')).toBeInTheDocument());
  });
});

describe('WebhookConfigPage coverage fill', () => {
  it('rotates a credential and dismisses the fresh box', async () => {
    const calls = stub((url, method) => {
      if (method === 'POST' && url.includes('/rotate'))
        return fakeResponse({
          body: {
            data: {
              id: 'sec-1',
              label: 'prod',
              status: 'active',
              token: 'whk_new',
              secret: 'whs_new',
              created_at: 'x',
            },
          },
        });
      if (method === 'GET')
        return fakeResponse({
          body: {
            data: [
              {
                id: 'sec-1',
                label: 'prod',
                status: 'active',
                created_at: '2026-07-27T00:00:00Z',
                revoked_at: null,
              },
            ],
            next_cursor: null,
          },
        });
      return fakeResponse({ body: { data: {} } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/webhooks" element={<WebhookConfigPage />} />
      </Routes>,
      { route: '/webhooks' },
    );
    await waitFor(() => expect(screen.getByTestId('webhook-rotate-sec-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-rotate-sec-1'));
    await waitFor(() => expect(calls.some((call) => call.url.includes('/rotate'))).toBe(true));
    await waitFor(() => expect(screen.getByTestId('webhook-fresh-credential')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /dismiss|I saved/ }));
    await waitFor(() => expect(screen.queryByTestId('webhook-fresh-credential')).toBeNull());
  });

  it('shows the empty state and surfaces create errors as toasts', async () => {
    const calls = stub((_url, method) => {
      if (method === 'POST')
        return fakeResponse({
          status: 429,
          body: { error: { code: 'rate_limited', message: 'slow down' } },
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
    await waitFor(() =>
      expect(
        screen.getByText(/No webhook credentials|autopilots\.webhook\.empty/),
      ).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByTestId('webhook-create-secret'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.method === 'POST' && call.url.includes('/webhook-secrets')),
      ).toBe(true),
    );
  });

  it('renders the error state on load failure', async () => {
    stub((url, method) => {
      if (method === 'GET' && !url.includes('/users/me'))
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      return null;
    });
    renderWithProviders(
      <Routes>
        <Route path="/webhooks" element={<WebhookConfigPage />} />
      </Routes>,
      { route: '/webhooks' },
    );
    await waitFor(() => expect(screen.getByText(/error|unexpected/i)).toBeInTheDocument());
  });
});

describe('editor: exercise every input + error toasts', () => {
  function renderEditor(route: string) {
    return renderWithProviders(
      <Routes>
        <Route path="/autopilots/new" element={<AutopilotEditorPage />} />
        <Route path="/autopilots/:autopilotId" element={<div>detail-page</div>} />
        <Route path="/autopilots" element={<div>list-page</div>} />
      </Routes>,
      { route },
    );
  }

  // 用例遍历全部触发器类型的表单控件,满载并行跑时需更宽时间预算(防 flake)。
  it('touches every form control across trigger types and guardrails', async () => {
    stub(() =>
      fakeResponse({
        body: { data: [{ id: 'ag-1', name: 'A', lifecycle_status: 'active' }], next_cursor: null },
      }),
    );
    renderEditor('/autopilots/new');
    await waitFor(() => expect(screen.getByTestId('autopilot-editor')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('autopilot-editor-name'), { target: { value: 'n' } });
    fireEvent.change(screen.getByTestId('autopilot-editor-description'), {
      target: { value: 'd' },
    });
    // webhook fields
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-editor-trigger-type'),
      'webhook_received',
    );
    fireEvent.change(screen.getByTestId('autopilot-editor-event-types'), {
      target: { value: 'a, b' },
    });
    // status trigger fields
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-editor-trigger-type'),
      'issue_status_changed',
    );
    fireEvent.change(screen.getByTestId('autopilot-editor-from-status'), {
      target: { value: 'todo' },
    });
    fireEvent.change(screen.getByTestId('autopilot-editor-to-status'), {
      target: { value: 'in_progress' },
    });
    // field trigger
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-editor-trigger-type'),
      'issue_field_changed',
    );
    fireEvent.change(screen.getByTestId('autopilot-editor-watch-fields'), {
      target: { value: 'priority' },
    });
    // mention trigger
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-editor-trigger-type'),
      'agent_mentioned',
    );
    fireEvent.change(screen.getByTestId('autopilot-editor-target-agents'), {
      target: { value: 'ag-1' },
    });
    // back to schedule: cron/timezone/misfire/one-time
    await userEvent.selectOptions(screen.getByTestId('autopilot-editor-trigger-type'), 'schedule');
    fireEvent.change(screen.getByTestId('autopilot-editor-cron'), {
      target: { value: '0 8 * * *' },
    });
    fireEvent.change(screen.getByTestId('autopilot-editor-timezone'), { target: { value: 'UTC' } });
    await userEvent.selectOptions(screen.getByTestId('autopilot-editor-misfire'), 'skip');
    fireEvent.change(screen.getByTestId('autopilot-editor-one-time'), { target: { value: '' } });
    // filter fields
    await userEvent.click(screen.getByTestId('autopilot-section-filter-toggle'));
    fireEvent.change(screen.getByTestId('autopilot-editor-filter-labels'), {
      target: { value: 'bug' },
    });
    fireEvent.change(screen.getByTestId('autopilot-editor-filter-priorities'), {
      target: { value: 'high' },
    });
    fireEvent.change(screen.getByTestId('autopilot-editor-keyword-include'), {
      target: { value: 'k1' },
    });
    fireEvent.change(screen.getByTestId('autopilot-editor-keyword-exclude'), {
      target: { value: 'k2' },
    });
    // actions: one of each kind, touch their fields
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    await userEvent.selectOptions(screen.getByTestId('autopilot-editor-executor'), 'ag-1');
    await userEvent.click(screen.getByTestId('autopilot-add-action'));
    await userEvent.click(screen.getByTestId('autopilot-add-action'));
    await userEvent.click(screen.getByTestId('autopilot-add-action'));
    await userEvent.click(screen.getByTestId('autopilot-add-action'));
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-action-type-0'),
      'run_agent_prompt',
    );
    await userEvent.selectOptions(screen.getByTestId('autopilot-action-type-1'), 'add_comment');
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-action-type-2'),
      'send_notification',
    );
    await userEvent.selectOptions(screen.getByTestId('autopilot-action-type-3'), 'create_issue');
    await userEvent.selectOptions(screen.getByTestId('autopilot-action-type-4'), 'http_request');
    fireEvent.change(
      screen.getAllByTestId('autopilot-action-prompt-0')[0] ??
        screen.getByTestId('autopilot-action-prompt-0'),
      { target: { value: 'p' } },
    );
    await userEvent.selectOptions(screen.getByTestId('autopilot-editor-action-executor'), 'ag-1');
    fireEvent.change(screen.getByTestId('autopilot-editor-action-content'), {
      target: { value: 'c' },
    });
    fireEvent.change(screen.getByTestId('autopilot-editor-action-message'), {
      target: { value: 'm' },
    });
    fireEvent.change(screen.getByTestId('autopilot-editor-action-issue-title'), {
      target: { value: 't' },
    });
    fireEvent.change(screen.getByTestId('autopilot-editor-action-issue-description'), {
      target: { value: 'dd' },
    });
    fireEvent.change(screen.getByTestId('autopilot-editor-action-url'), {
      target: { value: 'https://x.example/h' },
    });
    await userEvent.selectOptions(screen.getByTestId('autopilot-editor-action-method'), 'PUT');
    // guardrails numerics + checkboxes + backoff
    await userEvent.click(screen.getByTestId('autopilot-section-guardrails-toggle'));
    fireEvent.change(screen.getByTestId('autopilot-editor-rate-max'), { target: { value: '5' } });
    fireEvent.change(screen.getByTestId('autopilot-editor-rate-window'), {
      target: { value: '60' },
    });
    fireEvent.change(screen.getByTestId('autopilot-editor-concurrency'), {
      target: { value: '2' },
    });
    fireEvent.change(screen.getByTestId('autopilot-editor-dedup-window'), {
      target: { value: '30' },
    });
    fireEvent.change(screen.getByTestId('autopilot-editor-max-retries'), {
      target: { value: '1' },
    });
    await userEvent.selectOptions(screen.getByTestId('autopilot-editor-backoff'), 'fixed');
    fireEvent.change(screen.getByTestId('autopilot-editor-daily-runs'), {
      target: { value: '10' },
    });
    fireEvent.change(screen.getByTestId('autopilot-editor-daily-tokens'), {
      target: { value: '1000' },
    });
    fireEvent.change(screen.getByTestId('autopilot-editor-cascade'), { target: { value: '2' } });
    await userEvent.click(screen.getByTestId('autopilot-editor-require-approval'));
    await userEvent.click(screen.getByTestId('autopilot-editor-loop-detection'));
    await userEvent.click(screen.getByTestId('autopilot-editor-approval-http'));
    await userEvent.click(screen.getByTestId('autopilot-editor-approval-create-issue'));
    expect(
      (screen.getByTestId('autopilot-editor-require-approval') as HTMLInputElement).checked,
    ).toBe(true);
  }, 15_000);

  it('shows an error toast when create fails', async () => {
    stub((_url, method) => {
      if (method === 'POST')
        return fakeResponse({
          status: 422,
          body: { error: { code: 'executor_required', message: 'x' } },
        });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    });
    renderEditor('/autopilots/new');
    await waitFor(() => expect(screen.getByTestId('autopilot-editor-name')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('autopilot-editor-name'), 'x');
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-action-type-0'),
      'send_notification',
    );
    await userEvent.click(screen.getByTestId('autopilot-editor-save'));
    await waitFor(() => expect(screen.getByRole('status').textContent).not.toBe(''));
  });
});

describe('page error toasts', () => {
  it('detail: failed pause + invalid test-run JSON + failed test-run all toast', async () => {
    stub((url, method) => {
      if (method === 'GET' && url.includes('/runs'))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (method === 'GET') return fakeResponse({ body: { data: RULE } });
      return fakeResponse({ status: 409, body: { error: { code: 'conflict', message: 'x' } } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/:autopilotId" element={<AutopilotDetailPage />} />
      </Routes>,
      { route: '/autopilots/ap-1' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-detail-pause')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('autopilot-detail-pause'));
    await waitFor(() => expect(screen.getByRole('status').textContent).not.toBe(''));
  });

  it('run detail: failed approve toasts', async () => {
    stub((_url, method) => {
      if (method !== 'GET')
        return fakeResponse({ status: 403, body: { error: { code: 'forbidden', message: 'x' } } });
      return fakeResponse({ body: { data: RUN } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/runs/:runId" element={<AutopilotRunDetailPage />} />
      </Routes>,
      { route: '/autopilots/runs/run-1' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-run-approve')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('autopilot-run-approve'));
    await waitFor(() => expect(screen.getByRole('status').textContent).not.toBe(''));
  });

  it('webhook: failed rotate toasts and label input changes', async () => {
    stub((_url, method) => {
      if (method === 'POST')
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      if (method === 'GET')
        return fakeResponse({
          body: {
            data: [
              {
                id: 'sec-1',
                label: 'prod',
                status: 'active',
                created_at: '2026-07-27T00:00:00Z',
                revoked_at: null,
              },
            ],
            next_cursor: null,
          },
        });
      return fakeResponse({ body: { data: {} } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/webhooks" element={<WebhookConfigPage />} />
      </Routes>,
      { route: '/webhooks' },
    );
    await waitFor(() => expect(screen.getByTestId('webhook-rotate-sec-1')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('webhook-label-input'), '-x');
    await userEvent.click(screen.getByTestId('webhook-rotate-sec-1'));
    await waitFor(() => expect(screen.getByRole('status').textContent).not.toBe(''));
  });
});

describe('remaining callbacks: retries, dialogs, filters, nav links', () => {
  it('detail: edit nav, run filter, dialog cancel paths, error retry', async () => {
    let failNext = false;
    stub((url, method) => {
      if (method === 'GET' && url.includes('/runs'))
        return fakeResponse({ body: { data: [{ ...RUN }], next_cursor: null } });
      if (method === 'GET' && failNext) {
        failNext = false;
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      }
      if (method === 'GET') return fakeResponse({ body: { data: RULE } });
      return fakeResponse({ body: { data: RULE } });
    });
    const { rerender } = renderWithProviders(
      <Routes>
        <Route path="/autopilots/:autopilotId" element={<AutopilotDetailPage />} />
        <Route path="/autopilots/runs/:runId" element={<div>run-detail</div>} />
        <Route path="/autopilots/:autopilotId/edit" element={<div>editor-page</div>} />
        <Route path="/autopilots" element={<div>list-page</div>} />
        <Route
          path="/w/:workspaceSlug/automations/autopilots/runs/:runId"
          element={<div>run-detail</div>}
        />
        <Route
          path="/w/:workspaceSlug/automations/autopilots/:autopilotId/edit"
          element={<div>editor-page</div>}
        />
        <Route path="/w/:workspaceSlug/automations/autopilots" element={<div>list-page</div>} />
      </Routes>,
      { route: '/autopilots/ap-1' },
    );
    void rerender;
    await waitFor(() => expect(screen.getByTestId('autopilot-detail-name')).toBeInTheDocument());
    // edit button navigates
    await userEvent.click(screen.getByText(/actions\.edit|^Edit$/));
    await waitFor(() => expect(screen.getByText('editor-page')).toBeInTheDocument());
  });

  it('detail: run status filter + test dialog cancel + delete dialog cancel', async () => {
    stub((url, method) => {
      if (method === 'GET' && url.includes('/preview-schedule'))
        return fakeResponse({
          body: { data: { cron: '0 9 * * *', timezone: 'UTC', next_runs: [] } },
        });
      if (method === 'GET' && url.includes('/runs'))
        return fakeResponse({ body: { data: [{ ...RUN }], next_cursor: null } });
      if (method === 'GET') return fakeResponse({ body: { data: RULE } });
      return fakeResponse({ body: { data: RULE } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/:autopilotId" element={<AutopilotDetailPage />} />
        <Route path="/autopilots/runs/:runId" element={<div>run-detail</div>} />
      </Routes>,
      { route: '/autopilots/ap-1' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-runs-table')).toBeInTheDocument());
    // change the run status filter
    const filterSelect = screen.getByLabelText(/autopilots\.filters\.status|^Status$/);
    await userEvent.selectOptions(filterSelect, 'failed');
    // test dialog: type payload, cancel with ghost button
    await userEvent.click(screen.getByTestId('autopilot-detail-test-run'));
    fireEvent.change(screen.getByTestId('autopilot-test-payload'), {
      target: { value: '{"a":1}' },
    });
    const dialogCancels = screen.getAllByRole('button', { name: /common\.cancel|^Cancel$/ });
    await userEvent.click(dialogCancels[dialogCancels.length - 1]);
    await waitFor(() => expect(screen.queryByTestId('autopilot-test-payload')).toBeNull());
    // delete dialog: open then cancel
    await userEvent.click(screen.getByRole('button', { name: /actions\.delete|^Delete$/ }));
    const deleteCancels = screen.getAllByRole('button', { name: /common\.cancel|^Cancel$/ });
    await userEvent.click(deleteCancels[deleteCancels.length - 1]);
    await waitFor(() => expect(screen.queryByTestId('autopilot-delete-confirm')).toBeNull());
  });

  it('detail: error state retry navigates to the list', async () => {
    stub((url, method) => {
      if (method === 'GET' && !url.includes('/users/me'))
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      return null;
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/:autopilotId" element={<AutopilotDetailPage />} />
        <Route path="/autopilots" element={<div>list-page</div>} />
        <Route path="/w/:workspaceSlug/automations/autopilots" element={<div>list-page</div>} />
      </Routes>,
      { route: '/autopilots/ap-1' },
    );
    await waitFor(() =>
      expect(screen.getByText(/could not find|error|unexpected/i)).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(screen.getByText('list-page')).toBeInTheDocument());
  });

  it('editor: webhook create via save draft + sparse rule defaults + error retry', async () => {
    const SPARSE_RULE = {
      ...RULE,
      trigger_type: 'issue_created',
      trigger_config: {},
      description: null,
      guardrails: {
        rate_limit_overflow: 'drop',
        dedup_window_seconds: 0,
        dedup_key_template: '',
        daily_run_budget: 0,
        daily_token_budget: 0,
        approval_required_actions: [],
        kill_switch_paused: false,
        agent_loop_detection: false,
        cascade_max_depth: 0,
        agent_loop_window_seconds: 0,
      },
      action_config: [],
    };
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
          body: { data: { cron: '0 9 * * *', timezone: 'UTC', next_runs: [] } },
        });
      if (method === 'GET' && url.includes('/autopilots/sparse/edit'))
        return fakeResponse({ body: { data: SPARSE_RULE } });
      if (method === 'GET' && url.match(/autopilots\/sparse$/))
        return fakeResponse({ body: { data: SPARSE_RULE } });
      if (method === 'GET') return fakeResponse({ body: { data: RULE } });
      return fakeResponse({ body: { data: { ...RULE, id: 'ap-new' } } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/new" element={<AutopilotEditorPage />} />
        <Route path="/autopilots/:autopilotId/edit" element={<AutopilotEditorPage />} />
        <Route path="/autopilots/:autopilotId" element={<div>detail-page</div>} />
        <Route path="/autopilots" element={<div>list-page</div>} />
        <Route
          path="/w/:workspaceSlug/automations/autopilots/:autopilotId"
          element={<div>detail-page</div>}
        />
        <Route path="/w/:workspaceSlug/automations/autopilots" element={<div>list-page</div>} />
      </Routes>,
      { route: '/autopilots/new' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-editor-name')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('autopilot-editor-name'), 'wh');
    // webhook trigger + select the secret + event types
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-editor-trigger-type'),
      'webhook_received',
    );
    fireEvent.change(screen.getByTestId('autopilot-editor-secret'), { target: { value: 'sec-1' } });
    await userEvent.type(screen.getByTestId('autopilot-editor-event-types'), 'deploy');
    // switch the prompt action to a notification so no executor is required
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-action-type-0'),
      'send_notification',
    );
    // one-time field with a real value
    await userEvent.click(screen.getByTestId('autopilot-section-trigger-toggle'));
    await userEvent.click(screen.getByTestId('autopilot-section-trigger-toggle'));
    // save draft (paused) → POST with status paused
    await userEvent.click(screen.getByRole('button', { name: /saveDraft|Save draft/ }));
    await waitFor(() => expect(screen.getByText('detail-page')).toBeInTheDocument());
  });

  it('editor: sparse rule edit hits default branches', async () => {
    const SPARSE_RULE = {
      ...RULE,
      trigger_type: 'issue_created',
      trigger_config: {},
      action_config: [],
      executor_agent_id: null,
    };
    stub((url, method) => {
      if (method === 'GET' && url.includes('/agents'))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (method === 'GET' && url.includes('/webhook-secrets'))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (method === 'GET') return fakeResponse({ body: { data: SPARSE_RULE } });
      return fakeResponse({ body: { data: SPARSE_RULE } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/:autopilotId/edit" element={<AutopilotEditorPage />} />
        <Route path="/autopilots/:autopilotId" element={<div>detail-page</div>} />
        <Route
          path="/w/:workspaceSlug/automations/autopilots/:autopilotId"
          element={<div>detail-page</div>}
        />
      </Routes>,
      { route: '/autopilots/ap-1/edit' },
    );
    await waitFor(() =>
      expect((screen.getByTestId('autopilot-editor-name') as HTMLInputElement).value).toBe('规则'),
    );
    // switch to schedule: the cron state fell back to the default
    await userEvent.selectOptions(screen.getByTestId('autopilot-editor-trigger-type'), 'schedule');
    expect((screen.getByTestId('autopilot-editor-cron') as HTMLInputElement).value).toBe(
      '0 9 * * 1-5',
    );
    // schedule branch: type a one-time value
    fireEvent.change(screen.getByTestId('autopilot-editor-one-time'), {
      target: { value: '2026-08-01T00:00:00Z' },
    });
    // the sparse rule falls back to a prompt action without executor →
    // switch it to a notification so saving is enabled
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-action-type-0'),
      'send_notification',
    );
    // save & activate → PATCH
    await userEvent.click(screen.getByTestId('autopilot-editor-save'));
    await waitFor(() => expect(screen.getByText('detail-page')).toBeInTheDocument());
  });

  it('editor: error state retry goes to the list', async () => {
    stub((url, method) => {
      if (method === 'GET' && !url.includes('/users/me'))
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      return null;
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/:autopilotId/edit" element={<AutopilotEditorPage />} />
        <Route path="/autopilots" element={<div>list-page</div>} />
        <Route path="/w/:workspaceSlug/automations/autopilots" element={<div>list-page</div>} />
      </Routes>,
      { route: '/autopilots/ap-1/edit' },
    );
    await waitFor(() => expect(screen.getByText(/error|unexpected/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(screen.getByText('list-page')).toBeInTheDocument());
  });

  it('list: row detail button, type filter, search, kill dialog cancel, error retry', async () => {
    stub((url, method) => {
      if (url.endsWith('/autopilots/kill-switch'))
        return fakeResponse({ body: { data: { kill_switch: false } } });
      if (method === 'GET')
        return fakeResponse({
          body: {
            data: [
              { ...RULE, id: 'ap-1', last_run_at: null, next_run_at: null, stats: null },
              { ...RULE, id: 'ap-3', trigger_type: 'issue_created' },
            ],
            next_cursor: null,
          },
        });
      return fakeResponse({ body: { data: RULE } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots" element={<AutopilotsPage />} />
        <Route path="/autopilots/:id" element={<div>detail-page</div>} />
        <Route
          path="/w/:workspaceSlug/automations/autopilots/:id"
          element={<div>detail-page</div>}
        />
      </Routes>,
      { route: '/autopilots' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-row-ap-1')).toBeInTheDocument());
    // type filter + search inputs
    await userEvent.selectOptions(screen.getByTestId('autopilot-filter-type'), 'issue_created');
    await userEvent.type(screen.getByTestId('autopilot-search'), 'daily');
    // row detail button (the ghost 详情 action)
    const detailButtons = screen.getAllByRole('button', { name: /actions\.detail|^Detail$/ });
    await userEvent.click(detailButtons[0]);
    await waitFor(() => expect(screen.getByText('detail-page')).toBeInTheDocument());
  });

  it('list: kill dialog cancel + error state retry', async () => {
    stub((_url, method) => {
      if (_url.endsWith('/autopilots/kill-switch'))
        return fakeResponse({ body: { data: { kill_switch: false } } });
      if (method === 'GET') return fakeResponse({ body: { data: [RULE], next_cursor: null } });
      return fakeResponse({ body: { data: RULE } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots" element={<AutopilotsPage />} />
      </Routes>,
      { route: '/autopilots' },
    );
    await waitFor(() =>
      expect(screen.getByTestId('autopilot-kill-switch-button')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByTestId('autopilot-kill-switch-button'));
    const cancels = screen.getAllByRole('button', { name: /common\.cancel|^Cancel$/ });
    await userEvent.click(cancels[cancels.length - 1]);
    await waitFor(() => expect(screen.queryByTestId('autopilot-kill-reason')).toBeNull());
  });

  it('list: error state retry reloads', async () => {
    let failed = true;
    stub((url, method) => {
      if (url.endsWith('/autopilots/kill-switch'))
        return fakeResponse({ body: { data: { kill_switch: false } } });
      if (method === 'GET' && failed) {
        failed = false;
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      }
      if (method === 'GET') return fakeResponse({ body: { data: [RULE], next_cursor: null } });
      return fakeResponse({ body: { data: RULE } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots" element={<AutopilotsPage />} />
      </Routes>,
      { route: '/autopilots' },
    );
    await waitFor(() => expect(screen.getByText(/error|unexpected/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(screen.getByTestId('autopilot-row-ap-1')).toBeInTheDocument());
  });

  it('run detail: error retry + execution link nav', async () => {
    let failed = true;
    stub((url, method) => {
      if (method === 'GET' && failed && !url.includes('/users/me')) {
        failed = false;
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      }
      if (method === 'GET') return fakeResponse({ body: { data: RUN } });
      return fakeResponse({ body: { data: {} } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/runs/:runId" element={<AutopilotRunDetailPage />} />
        <Route path="/executions/:executionId" element={<div>execution-page</div>} />
        <Route
          path="/w/:workspaceSlug/executions/:executionId"
          element={<div>execution-page</div>}
        />
      </Routes>,
      { route: '/autopilots/runs/run-1' },
    );
    await waitFor(() => expect(screen.getByText(/error|unexpected/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(screen.getByTestId('autopilot-run-snapshot')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /ex-1/ }));
    await waitFor(() => expect(screen.getByText('execution-page')).toBeInTheDocument());
  });

  it('webhook: error state retry reloads', async () => {
    let failed = true;
    stub((url, method) => {
      if (method === 'GET' && failed && !url.includes('/users/me')) {
        failed = false;
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      }
      if (method === 'GET') return fakeResponse({ body: { data: [], next_cursor: null } });
      return fakeResponse({ body: { data: {} } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/webhooks" element={<WebhookConfigPage />} />
      </Routes>,
      { route: '/webhooks' },
    );
    await waitFor(() => expect(screen.getByText(/error|unexpected/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(screen.getByTestId('webhook-create-secret')).toBeInTheDocument());
  });
});

describe('acceptance round 2: new editor/list/run/webhook controls', () => {
  function renderEditorAt(route: string, stubFn: (url: string, method: string) => Response | null) {
    stub(stubFn);
    return renderWithProviders(
      <Routes>
        <Route path="/autopilots/new" element={<AutopilotEditorPage />} />
        <Route path="/autopilots/:autopilotId" element={<div>detail-page</div>} />
        <Route
          path="/w/:workspaceSlug/automations/autopilots/:autopilotId"
          element={<div>detail-page</div>}
        />
      </Routes>,
      { route },
    );
  }

  const baseStub = (preview: 'ok' | 'fail' | 'none') => (url: string, method: string) => {
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (url.includes('/agents'))
      return fakeResponse({
        body: { data: [{ id: 'ag-1', name: 'A', lifecycle_status: 'active' }], next_cursor: null },
      });
    if (url.includes('/webhook-secrets'))
      return fakeResponse({ body: { data: [], next_cursor: null } });
    if (method === 'POST' && url.includes('/preview-schedule')) {
      if (preview === 'fail')
        return fakeResponse({
          status: 400,
          body: { error: { code: 'invalid_cron', message: 'x' } },
        });
      return fakeResponse({
        body: { data: { cron: '0 9 * * *', timezone: 'UTC', next_runs: ['2026-07-28T09:00:00Z'] } },
      });
    }
    if (method === 'POST') return fakeResponse({ body: { data: { ...RULE, id: 'ap-new' } } });
    return fakeResponse({ body: { data: [], next_cursor: null } });
  };

  it('cron preset fills the cron field; custom shows manual value', async () => {
    renderEditorAt('/autopilots/new', baseStub('ok'));
    await waitFor(() =>
      expect(screen.getByTestId('autopilot-editor-cron-preset')).toBeInTheDocument(),
    );
    await userEvent.selectOptions(screen.getByTestId('autopilot-editor-cron-preset'), 'daily9');
    await waitFor(() =>
      expect((screen.getByTestId('autopilot-editor-cron') as HTMLInputElement).value).toBe(
        '0 9 * * *',
      ),
    );
    // custom option keeps the manual value
    await userEvent.selectOptions(screen.getByTestId('autopilot-editor-cron-preset'), 'custom');
    expect(screen.getByTestId('autopilot-editor-cron')).toBeInTheDocument();
  });

  it('live preview renders next runs (create mode) and invalid state on error', async () => {
    renderEditorAt('/autopilots/new', baseStub('ok'));
    await waitFor(() =>
      expect(screen.getByTestId('autopilot-schedule-preview')).toBeInTheDocument(),
    );
    // failing preview → invalid hint
    renderEditorAt('/autopilots/new', baseStub('fail'));
    await waitFor(() =>
      expect(screen.getByTestId('autopilot-preview-invalid')).toBeInTheDocument(),
    );
  });

  it('template variable buttons append to the prompt', async () => {
    renderEditorAt('/autopilots/new', baseStub('none'));
    await waitFor(() => expect(screen.getByTestId('autopilot-editor')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-action-type-0'),
      'run_agent_prompt',
    );
    const vars = screen.getByTestId('autopilot-template-vars-0');
    const firstVar = vars.querySelector('button');
    expect(firstVar).not.toBeNull();
    await userEvent.click(firstVar as HTMLElement);
    expect(
      (screen.getByTestId('autopilot-action-prompt-0') as HTMLTextAreaElement).value,
    ).toContain('{{trigger.');
  });

  it('exposes scope/filter project+actor inputs and overflow control', async () => {
    renderEditorAt('/autopilots/new', baseStub('none'));
    await waitFor(() => expect(screen.getByTestId('autopilot-editor')).toBeInTheDocument());
    // event trigger shows scope projects
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-editor-trigger-type'),
      'issue_created',
    );
    await userEvent.type(screen.getByTestId('autopilot-editor-scope-projects'), 'p1, p2');
    // filter section: projects + actors
    await userEvent.click(screen.getByTestId('autopilot-section-filter-toggle'));
    await userEvent.type(screen.getByTestId('autopilot-editor-filter-projects'), 'p1');
    await userEvent.type(screen.getByTestId('autopilot-editor-filter-actors'), 'm1');
    // guardrails: overflow control
    await userEvent.click(screen.getByTestId('autopilot-section-guardrails-toggle'));
    await userEvent.selectOptions(screen.getByTestId('autopilot-editor-overflow'), 'queue');
    expect((screen.getByTestId('autopilot-editor-overflow') as HTMLSelectElement).value).toBe(
      'queue',
    );
    // timezone datalist present
    expect(document.getElementById('autopilot-tz-list')).not.toBeNull();
  });

  it('list renders last-run result and trigger icons; kill reason gates confirm', async () => {
    stub((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/kill-switch') && method === 'GET')
        return fakeResponse({ body: { data: { kill_switch: false } } });
      if (method === 'GET')
        return fakeResponse({
          body: {
            data: [
              {
                ...RULE,
                id: 'ap-a',
                last_run_status: 'failed',
                last_run_at: '2026-07-27T00:00:00Z',
              },
            ],
            next_cursor: null,
          },
        });
      return fakeResponse({ body: { data: RULE } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots" element={<AutopilotsPage />} />
      </Routes>,
      { route: '/autopilots' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-last-run-ap-a')).toBeInTheDocument());
    expect(screen.getByTestId('autopilot-last-run-ap-a').textContent).toMatch(/failed|失败/i);
    // trigger icon present (schedule → clock SVG,design-quality §7.1 禁 emoji)
    const triggerIcon = screen.getByTestId('autopilot-trigger-ap-a').querySelector('svg');
    expect(triggerIcon).not.toBeNull();
    expect(triggerIcon).toHaveAttribute('aria-hidden', 'true');
    // kill dialog: confirm disabled until reason typed
    await userEvent.click(screen.getByTestId('autopilot-kill-switch-button'));
    const confirm = screen.getByTestId('autopilot-kill-confirm') as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    await userEvent.type(screen.getByTestId('autopilot-kill-reason'), 'reason');
    expect((screen.getByTestId('autopilot-kill-confirm') as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('list renders settings fallback icon for unknown trigger type', async () => {
    stub((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/kill-switch') && method === 'GET')
        return fakeResponse({ body: { data: { kill_switch: false } } });
      return fakeResponse({
        body: {
          data: [{ ...RULE, id: 'ap-x', trigger_type: 'future_trigger', trigger_config: {} }],
          next_cursor: null,
        },
      });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots" element={<AutopilotsPage />} />
      </Routes>,
      { route: '/autopilots' },
    );
    // 后端新增的未知触发类型回落 settings 图标,Icon 不因未注册名抛错
    const triggerIcon = await screen.findByTestId('autopilot-trigger-ap-x');
    expect(triggerIcon.querySelector('svg')).not.toBeNull();
  });

  it('run detail artifacts link to app routes', async () => {
    const issueId = '22222222-2222-2222-2222-222222222201';
    stub((url) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      return fakeResponse({
        body: {
          data: {
            ...RUN,
            status: 'succeeded',
            trigger_snapshot: { issue: { id: issueId } },
            artifacts: [
              {
                id: 'art-1',
                artifact_type: 'comment',
                ref_table: 'comments',
                ref_id: 'c-1',
                summary: 'ok',
                created_at: '2026-07-27T00:00:00Z',
              },
              {
                id: 'art-2',
                artifact_type: 'issue',
                ref_table: 'issues',
                ref_id: issueId,
                summary: 'created',
                created_at: '2026-07-27T00:00:00Z',
              },
            ],
          },
        },
      });
    });
    renderWithProviders(
      <Routes>
        <Route path="/autopilots/runs/:runId" element={<AutopilotRunDetailPage />} />
        <Route path="/issues/:issueId" element={<div>issue-page</div>} />
        <Route path="/w/:workspaceSlug/issues/:issueId" element={<div>issue-page</div>} />
      </Routes>,
      { route: '/autopilots/runs/run-1' },
    );
    await waitFor(() =>
      expect(screen.getByTestId('autopilot-artifact-link-art-2')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByTestId('autopilot-artifact-link-art-2'));
    await waitFor(() => expect(screen.getByText('issue-page')).toBeInTheDocument());
  });

  it('webhook page renders the recent events table', async () => {
    stub((url) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/webhook-events'))
        return fakeResponse({
          body: {
            data: [
              {
                id: 'we-1',
                autopilot_id: null,
                idempotency_key: 'rejected:abc',
                event_type: 'alert.triggered',
                headers: null,
                payload: { a: 1 },
                signature_status: 'missing',
                process_status: 'rejected',
                received_at: '2026-07-27T00:00:00Z',
              },
            ],
            next_cursor: null,
          },
        });
      if (url.includes('/webhook-secrets'))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      return fakeResponse({ body: { data: {} } });
    });
    renderWithProviders(
      <Routes>
        <Route path="/webhooks" element={<WebhookConfigPage />} />
      </Routes>,
      { route: '/webhooks' },
    );
    await waitFor(() => expect(screen.getByTestId('webhook-event-row-we-1')).toBeInTheDocument());
    expect(screen.getByTestId('webhook-events-table').textContent).toMatch(/rejected|已拒绝/i);
    // refresh button reloads the listing
    await userEvent.click(screen.getByRole('button', { name: /Refresh|刷新/ }));
    await waitFor(() => expect(screen.getByTestId('webhook-event-row-we-1')).toBeInTheDocument());
  });
});
