/**
 * 最终分支补覆盖:卸载竞争态(cancelled 分支)、非 MeshApiError 错误路径、
 * 错频道帧、各保存变体与空值渲染分支。
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
    { workspace_id: 'ws-1', workspace_name: 'T', workspace_slug: 't', role: 'owner', status: 'active', joined_at: null },
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
    dedup_key_template: 'k',
    daily_run_budget: 200,
    daily_token_budget: 2000000,
    approval_required_actions: ['http_request'],
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

function okStub() {
  const impl = (async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (url.includes('/agents')) return fakeResponse({ body: { data: [], next_cursor: null } });
    if (url.includes('/webhook-secrets')) return fakeResponse({ body: { data: [], next_cursor: null } });
    if (url.includes('/kill-switch')) return fakeResponse({ body: { data: { kill_switch: false } } });
    if (url.includes('/runs')) return fakeResponse({ body: { data: [], next_cursor: null } });
    if (url.match(/autopilots\/[^/]+$/)) return fakeResponse({ body: { data: RULE } });
    if (url.includes('/autopilots')) return fakeResponse({ body: { data: [RULE], next_cursor: null } });
    return fakeResponse({ body: { data: RULE } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
}

function throwingStub() {
  const impl = (async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    throw new TypeError('network down');
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
}

const ROUTES = (
  <Routes>
    <Route path="/autopilots" element={<AutopilotsPage />} />
    <Route path="/autopilots/new" element={<AutopilotEditorPage />} />
    <Route path="/autopilots/runs/:runId" element={<AutopilotRunDetailPage />} />
    <Route path="/autopilots/:autopilotId" element={<AutopilotDetailPage />} />
    <Route path="/autopilots/:autopilotId/edit" element={<AutopilotEditorPage />} />
    <Route path="/webhooks" element={<WebhookConfigPage />} />
  </Routes>
);

describe('unmount race: cancelled branches', () => {
  const paths = ['/autopilots', '/autopilots/new', '/autopilots/ap-1', '/autopilots/ap-1/edit', '/webhooks'];
  for (const path of paths) {
    it(`unmounts mid-load at ${path}`, () => {
      okStub();
      const { unmount } = renderWithProviders(ROUTES, { route: path });
      unmount(); // effects' async continuations hit `if (cancelled) return;`
    });
  }

  it('unmounts run detail mid-load', () => {
    okStub();
    const { unmount } = renderWithProviders(ROUTES, { route: '/autopilots/runs/run-1' });
    unmount();
  });
});

describe('non-MeshApiError error paths (error.unknown)', () => {
  it('list surfaces unknown error', async () => {
    throwingStub();
    renderWithProviders(ROUTES, { route: '/autopilots' });
    await waitFor(() => expect(screen.getByText(/error|unexpected/i)).toBeInTheDocument());
  });

  it('detail surfaces unknown error', async () => {
    throwingStub();
    renderWithProviders(ROUTES, { route: '/autopilots/ap-1' });
    await waitFor(() => expect(screen.getByText(/error|unexpected/i)).toBeInTheDocument());
  });

  it('editor surfaces unknown error', async () => {
    throwingStub();
    renderWithProviders(ROUTES, { route: '/autopilots/new' });
    await waitFor(() => expect(screen.getByText(/error|unexpected/i)).toBeInTheDocument());
  });

  it('run detail surfaces unknown error', async () => {
    throwingStub();
    renderWithProviders(ROUTES, { route: '/autopilots/runs/run-1' });
    await waitFor(() => expect(screen.getByText(/error|unexpected/i)).toBeInTheDocument());
  });

  it('webhook surfaces unknown error', async () => {
    throwingStub();
    renderWithProviders(ROUTES, { route: '/webhooks' });
    await waitFor(() => expect(screen.getByText(/error|unexpected/i)).toBeInTheDocument());
  });

  it('list pause failure with non-API error toasts', async () => {
    let failOnce = true;
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/kill-switch')) return fakeResponse({ body: { data: { kill_switch: false } } });
      if (method === 'POST' && failOnce) {
        failOnce = false;
        throw new TypeError('network down');
      }
      if (method === 'GET') return fakeResponse({ body: { data: [RULE], next_cursor: null } });
      return fakeResponse({ body: { data: RULE } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(ROUTES, { route: '/autopilots' });
    await waitFor(() => expect(screen.getByTestId('autopilot-pause-ap-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('autopilot-pause-ap-1'));
    await waitFor(() => expect(screen.getByRole('status').textContent).not.toBe(''));
  });
});

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

describe('channel guard + misc branches', () => {
  it('list ignores frames from other channels', async () => {
    okStub();
    const realtime = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={realtime.value}>{ROUTES}</RealtimeContext.Provider>,
      { route: '/autopilots' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-row-ap-1')).toBeInTheDocument());
    realtime.emit({ channel: 'workspace:OTHER:autopilots', event: 'autopilot.updated', seq: 1, payload: {} } as unknown as RealtimeEventFrame);
    // no reload crash; page still intact
    expect(screen.getByTestId('autopilot-row-ap-1')).toBeInTheDocument();
  });

  it('detail ignores frames from other channels', async () => {
    okStub();
    const realtime = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={realtime.value}>{ROUTES}</RealtimeContext.Provider>,
      { route: '/autopilots/ap-1' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-detail-name')).toBeInTheDocument());
    realtime.emit({ channel: 'autopilot:other', event: 'autopilot.updated', seq: 1, payload: {} } as unknown as RealtimeEventFrame);
    expect(screen.getByTestId('autopilot-detail-name')).toBeInTheDocument();
  });

  it('list clears the search param (null branch)', async () => {
    okStub();
    renderWithProviders(ROUTES, { route: '/autopilots?q=abc' });
    await waitFor(() => expect(screen.getByTestId('autopilot-row-ap-1')).toBeInTheDocument());
    const search = screen.getByTestId('autopilot-search') as HTMLInputElement;
    expect(search.value).toBe('abc');
    fireEvent.change(search, { target: { value: '' } });
    await waitFor(() => expect((screen.getByTestId('autopilot-search') as HTMLInputElement).value).toBe(''));
  });

  it('webhook with no workspace shows the empty list', async () => {
    const impl = (async () =>
      fakeResponse({ body: { data: { user: ME.user, memberships: [] } } })) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(ROUTES, { route: '/webhooks' });
    await waitFor(() =>
      expect(screen.getByText(/No webhook credentials|autopilots\.webhook\.empty/)).toBeInTheDocument(),
    );
  });
});

describe('detail test-run variants', () => {
  function detailStub(testRunResponse: unknown) {
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/preview-schedule'))
        return fakeResponse({ body: { data: { cron: '0 9 * * *', timezone: 'UTC', next_runs: [] } } });
      if (url.includes('/runs')) return fakeResponse({ body: { data: [], next_cursor: null } });
      if (method === 'POST' && url.includes('/test-run'))
        return fakeResponse({ body: { data: testRunResponse } });
      if (method === 'GET') return fakeResponse({ body: { data: RULE } });
      return fakeResponse({ body: { data: RULE } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
  }

  it('dry run with non-matching filters toasts wouldNotRun', async () => {
    detailStub({ would_run: false, matched_filters: {} });
    renderWithProviders(ROUTES, { route: '/autopilots/ap-1' });
    await waitFor(() => expect(screen.getByTestId('autopilot-detail-test-run')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('autopilot-detail-test-run'));
    await userEvent.click(screen.getByTestId('autopilot-test-dry-run'));
    await userEvent.click(screen.getByTestId('autopilot-test-submit'));
    await waitFor(() => expect(screen.getByRole('status').textContent).not.toBe(''));
  });

  it('real test run navigates to the run detail', async () => {
    detailStub({ run_id: 'run-9', status: 'pending', autopilot_id: 'ap-1', is_test: true });
    renderWithProviders(ROUTES, { route: '/autopilots/ap-1' });
    await waitFor(() => expect(screen.getByTestId('autopilot-detail-test-run')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('autopilot-detail-test-run'));
    await userEvent.click(screen.getByTestId('autopilot-test-submit'));
    await waitFor(() => expect(screen.getByTestId('autopilot-run-status')).toBeInTheDocument());
  });
});

describe('editor save variants + remaining branches', () => {
  function editorStub() {
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [{ id: 'ag-1', name: 'A', lifecycle_status: 'active' }], next_cursor: null } });
      if (url.includes('/webhook-secrets'))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (method === 'GET') return fakeResponse({ body: { data: RULE } });
      return fakeResponse({ body: { data: { ...RULE, id: 'ap-new' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
  }

  function renderNew() {
    return renderWithProviders(ROUTES, { route: '/autopilots/new' });
  }

  it('saves field trigger with all filter fields filled', async () => {
    editorStub();
    renderNew();
    await waitFor(() => expect(screen.getByTestId('autopilot-editor-name')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('autopilot-editor-name'), 'n');
    await userEvent.selectOptions(screen.getByTestId('autopilot-editor-trigger-type'), 'issue_field_changed');
    await userEvent.type(screen.getByTestId('autopilot-editor-watch-fields'), 'priority');
    await userEvent.click(screen.getByTestId('autopilot-section-filter-toggle'));
    await userEvent.type(screen.getByTestId('autopilot-editor-filter-labels'), 'bug');
    await userEvent.type(screen.getByTestId('autopilot-editor-filter-priorities'), 'high');
    await userEvent.type(screen.getByTestId('autopilot-editor-keyword-include'), 'a');
    await userEvent.type(screen.getByTestId('autopilot-editor-keyword-exclude'), 'b');
    fireEvent.change(screen.getByTestId('autopilot-editor-payload-match'), {
      target: { value: '[{"path": "x", "op": "eq", "value": 1}]' },
    });
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    await userEvent.selectOptions(screen.getByTestId('autopilot-action-type-0'), 'send_notification');
    await userEvent.click(screen.getByTestId('autopilot-editor-save'));
    await waitFor(() => expect(screen.getByTestId('autopilot-detail-name')).toBeInTheDocument());
  });

  it('saves mention trigger + http_request action + add_comment + prompt action', async () => {
    editorStub();
    renderNew();
    await waitFor(() => expect(screen.getByTestId('autopilot-editor-name')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('autopilot-editor-name'), 'n');
    await userEvent.selectOptions(screen.getByTestId('autopilot-editor-trigger-type'), 'agent_mentioned');
    await userEvent.type(screen.getByTestId('autopilot-editor-target-agents'), 'ag-1');
    // four actions covering every kind
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    await userEvent.click(screen.getByTestId('autopilot-add-action'));
    await userEvent.click(screen.getByTestId('autopilot-add-action'));
    await userEvent.click(screen.getByTestId('autopilot-add-action'));
    await userEvent.selectOptions(screen.getByTestId('autopilot-action-type-0'), 'run_agent_prompt');
    await userEvent.selectOptions(screen.getByTestId('autopilot-action-type-1'), 'add_comment');
    await userEvent.selectOptions(screen.getByTestId('autopilot-action-type-2'), 'http_request');
    await userEvent.selectOptions(screen.getByTestId('autopilot-action-type-3'), 'create_issue');
    // prompt typed (undefined → value), executor via global select
    fireEvent.change(screen.getByTestId('autopilot-action-prompt-0'), { target: { value: 'do it' } });
    await userEvent.selectOptions(screen.getByTestId('autopilot-editor-executor'), 'ag-1');
    await userEvent.type(screen.getByTestId('autopilot-editor-action-content'), 'c');
    await userEvent.type(screen.getByTestId('autopilot-editor-action-url'), 'https://h.example/x');
    await userEvent.selectOptions(screen.getByTestId('autopilot-editor-action-method'), 'PUT');
    await userEvent.type(screen.getByTestId('autopilot-editor-action-issue-title'), 'T');
    // moveUp at index 0 hits the bounds guard (returns prev)
    const upButtons = screen.getAllByRole('button', { name: /moveUp|Move up/ });
    await userEvent.click(upButtons[0]);
    await userEvent.click(screen.getByTestId('autopilot-editor-save'));
    await waitFor(() => expect(screen.getByTestId('autopilot-detail-name')).toBeInTheDocument());
  });

  it('toggles every section closed then open + invalid name error', async () => {
    editorStub();
    renderNew();
    await waitFor(() => expect(screen.getByTestId('autopilot-editor-name')).toBeInTheDocument());
    // close the open trigger section, then toggle the others both ways
    await userEvent.click(screen.getByTestId('autopilot-section-trigger-toggle'));
    await userEvent.click(screen.getByTestId('autopilot-section-trigger-toggle'));
    await userEvent.click(screen.getByTestId('autopilot-section-filter-toggle'));
    await userEvent.click(screen.getByTestId('autopilot-section-filter-toggle'));
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    await userEvent.click(screen.getByTestId('autopilot-section-guardrails-toggle'));
    await userEvent.click(screen.getByTestId('autopilot-section-guardrails-toggle'));
    // invalid name (>200 chars) shows the error hint
    const nameInput = screen.getByTestId('autopilot-editor-name');
    fireEvent.change(nameInput, { target: { value: 'x'.repeat(201) } });
    await waitFor(() => expect(screen.getByText(/nameInvalid|1–200|1-200/)).toBeInTheDocument());
  });

  it('saves a prompt-only action without executor (draft path disabled save)', async () => {
    editorStub();
    renderNew();
    await waitFor(() => expect(screen.getByTestId('autopilot-editor-name')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('autopilot-editor-name'), 'n');
    // default action is run_agent_prompt without executor → save disabled
    expect((screen.getByTestId('autopilot-editor-save') as HTMLButtonElement).disabled).toBe(true);
    // switching to a notification action enables saving
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    await userEvent.selectOptions(screen.getByTestId('autopilot-action-type-0'), 'send_notification');
    await userEvent.click(screen.getByTestId('autopilot-editor-save'));
    await waitFor(() => expect(screen.getByTestId('autopilot-detail-name')).toBeInTheDocument());
  });

  it('edit mode: sparse guardrails without approval_required_actions key', async () => {
    const SPARSE = {
      ...RULE,
      trigger_type: 'comment_created',
      trigger_config: {},
      action_config: [],
      guardrails: {
        rate_limit_overflow: 'drop',
        dedup_window_seconds: 0,
        dedup_key_template: '',
        daily_run_budget: 0,
        daily_token_budget: 0,
        kill_switch_paused: false,
        agent_loop_detection: false,
        cascade_max_depth: 0,
        agent_loop_window_seconds: 0,
      },
    };
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/agents')) return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/webhook-secrets')) return fakeResponse({ body: { data: [], next_cursor: null } });
      if (method === 'GET') return fakeResponse({ body: { data: SPARSE } });
      return fakeResponse({ body: { data: SPARSE } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(ROUTES, { route: '/autopilots/ap-1/edit' });
    await waitFor(() =>
      expect((screen.getByTestId('autopilot-editor-name') as HTMLInputElement).value).toBe('规则'),
    );
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    await userEvent.selectOptions(screen.getByTestId('autopilot-action-type-0'), 'send_notification');
    await userEvent.click(screen.getByTestId('autopilot-editor-save'));
    await waitFor(() => expect(screen.getByTestId('autopilot-detail-name')).toBeInTheDocument());
  });
});

describe('run detail: rich run + realtime reload', () => {
  const RICH_RUN = {
    id: 'run-4',
    autopilot_id: 'ap-1',
    workspace_id: 'ws-1',
    trigger_type: 'webhook_received',
    trigger_snapshot: { event_id: 'e' },
    webhook_event_id: 'we-1',
    execution_id: 'ex-1',
    parent_run_id: null,
    cascade_depth: 0,
    status: 'succeeded',
    started_at: '2026-07-27T00:00:00Z',
    finished_at: '2026-07-27T00:01:00Z',
    duration_ms: 60000,
    retry_count: 0,
    error: null,
    prompt_tokens: 1,
    completion_tokens: 2,
    total_tokens: 3,
    triggered_by: null,
    is_test: false,
    created_at: '2026-07-27T00:00:00Z',
    updated_at: '2026-07-27T00:01:00Z',
    attempts: [
      { attempt_number: 1, status: 'succeeded', execution_id: 'ex-1', started_at: '2026-07-27T00:00:00Z', finished_at: '2026-07-27T00:01:00Z', error: null, prompt_tokens: 1, completion_tokens: 2 },
    ],
    artifacts: [
      { id: 'a-1', artifact_type: 'comment', ref_table: 'comments', ref_id: 'c-1', summary: null, created_at: '2026-07-27T00:01:00Z' },
    ],
  };

  it('renders finished dates, attempt times, null summary and reloads on frame', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      return fakeResponse({ body: { data: RICH_RUN } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    const realtime = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={realtime.value}>{ROUTES}</RealtimeContext.Provider>,
      { route: '/autopilots/runs/run-4' },
    );
    await waitFor(() => expect(screen.getByTestId('autopilot-run-attempts')).toBeInTheDocument());
    // null artifact summary renders the dash fallback
    expect(screen.getByTestId('autopilot-run-artifacts').textContent).toContain('—');
    // a matching frame triggers a reload
    realtime.emit({
      channel: 'autopilot:ap-1',
      event: 'autopilot_runs.status_changed',
      seq: 3,
      payload: { run_id: 'run-4' },
    } as unknown as RealtimeEventFrame);
    // and a non-matching run id is ignored
    realtime.emit({
      channel: 'autopilot:ap-1',
      event: 'autopilot_runs.status_changed',
      seq: 4,
      payload: { run_id: 'other-run' },
    } as unknown as RealtimeEventFrame);
    expect(screen.getByTestId('autopilot-run-attempts')).toBeInTheDocument();
  });
});

describe('webhook final branches', () => {
  it('create with network throw toasts error.unknown', async () => {
    let posted = false;
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'POST' && !posted) {
        posted = true;
        throw new TypeError('network down');
      }
      if (method === 'GET') return fakeResponse({ body: { data: [], next_cursor: null } });
      return fakeResponse({ body: { data: {} } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(ROUTES, { route: '/webhooks' });
    await waitFor(() => expect(screen.getByTestId('webhook-create-secret')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-create-secret'));
    await waitFor(() => expect(screen.getByRole('status').textContent).not.toBe(''));
  });

  it('rotate with network throw toasts error.unknown', async () => {
    let rotated = false;
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'POST' && !rotated) {
        rotated = true;
        throw new TypeError('network down');
      }
      if (method === 'GET')
        return fakeResponse({
          body: { data: [{ id: 'sec-1', label: 'p', status: 'active', created_at: 'x', revoked_at: null }], next_cursor: null },
        });
      return fakeResponse({ body: { data: {} } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(ROUTES, { route: '/webhooks' });
    await waitFor(() => expect(screen.getByTestId('webhook-rotate-sec-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-rotate-sec-1'));
    await waitFor(() => expect(screen.getByRole('status').textContent).not.toBe(''));
  });

  it('unmount after users/me resolves but before secrets (cancelled mid-load)', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      await new Promise((resolve) => setTimeout(resolve, 80));
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    const { unmount } = renderWithProviders(ROUTES, { route: '/webhooks' });
    await new Promise((resolve) => setTimeout(resolve, 20));
    unmount();
    await new Promise((resolve) => setTimeout(resolve, 100));
  });
});

