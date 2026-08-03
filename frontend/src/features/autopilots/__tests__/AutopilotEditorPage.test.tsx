/**
 * AutopilotEditorPage 组件测试(autopilot.md §4.2):四段折叠区块、
 * 保存并启用(创建)、校验门(名称 / executor)、动作增删、编辑预填。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { AutopilotEditorPage } from '../AutopilotEditorPage';

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

const AGENTS = [{ id: 'ag-1', name: '值班 agent', lifecycle_status: 'active' }];

const RULE = {
  id: 'ap-1',
  workspace_id: 'ws-1',
  name: '既有规则',
  description: 'desc',
  trigger_type: 'schedule',
  trigger_config: { cron: '0 8 * * *', timezone: 'UTC', misfire_policy: 'skip' },
  filter_config: { labels: ['bug'] },
  action_config: [{ type: 'send_notification', message: 'hi' }],
  executor_agent_id: 'ag-1',
  status: 'active',
  guardrails: {
    rate_limit_overflow: 'drop',
    dedup_window_seconds: 300,
    dedup_key_template: '{{trigger.event_id}}',
    daily_run_budget: 200,
    daily_token_budget: 2000000,
    approval_required_actions: ['http_request'],
    kill_switch_paused: false,
    agent_loop_detection: true,
    cascade_max_depth: 3,
    agent_loop_window_seconds: 60,
  },
  max_retries: 2,
  retry_backoff: 'linear',
  retry_base_seconds: 10,
  retry_max_seconds: 600,
  rate_limit_max: 5,
  rate_limit_window_seconds: 1800,
  concurrency_limit: 2,
  require_approval: true,
  next_run_at: null,
  last_run_at: null,
  created_by: 'm-1',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  stats: null,
};

interface Recorded {
  url: string;
  method: string;
  body?: unknown;
}

function setup(existing: boolean, me = ME): Recorded[] {
  const calls: Recorded[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    let body: unknown;
    if (init?.body) {
      try {
        body = JSON.parse(String(init.body));
      } catch {
        body = undefined;
      }
    }
    calls.push({ url, method, body });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
    if (url.includes('/agents')) return fakeResponse({ body: { data: AGENTS, next_cursor: null } });
    if (url.includes('/webhook-secrets'))
      return fakeResponse({ body: { data: [], next_cursor: null } });
    if (url.includes('/preview-schedule'))
      return fakeResponse({
        body: { data: { cron: '0 8 * * *', timezone: 'UTC', next_runs: ['2026-07-28T08:00:00Z'] } },
      });
    if (method === 'GET' && existing) return fakeResponse({ body: { data: RULE } });
    return fakeResponse({ body: { data: { ...RULE, id: 'ap-new', name: 'new' } } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

function renderEditor(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/autopilots/new" element={<AutopilotEditorPage />} />
      <Route path="/autopilots/:autopilotId/edit" element={<AutopilotEditorPage />} />
      <Route path="/autopilots/:autopilotId" element={<div>detail-page</div>} />
      <Route
        path="/w/:workspaceSlug/automations/autopilots/:autopilotId"
        element={<div>detail-page</div>}
      />
    </Routes>,
    { route },
  );
}

describe('AutopilotEditorPage', () => {
  it('creates a rule with save & activate', async () => {
    const calls = setup(false);
    renderEditor('/autopilots/new');
    await waitFor(() => expect(screen.getByTestId('autopilot-editor')).toBeInTheDocument());
    // save disabled until the name is filled
    expect((screen.getByTestId('autopilot-editor-save') as HTMLButtonElement).disabled).toBe(true);
    await userEvent.type(screen.getByTestId('autopilot-editor-name'), '每日汇总');
    // open the actions section and point the prompt action at an agent
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    await userEvent.selectOptions(screen.getByTestId('autopilot-editor-executor'), 'ag-1');
    await userEvent.click(screen.getByTestId('autopilot-editor-save'));
    await waitFor(() =>
      expect(calls.some((call) => call.method === 'POST' && call.url.endsWith('/autopilots'))).toBe(
        true,
      ),
    );
    const create = calls.find((call) => call.method === 'POST' && call.url.endsWith('/autopilots'));
    expect(create?.body).toMatchObject({
      name: '每日汇总',
      trigger_type: 'schedule',
      status: 'active',
    });
    await waitFor(() => expect(screen.getByText('detail-page')).toBeInTheDocument());
  });

  it('rejects malformed payload_match JSON with a dedicated toast (R2 LOW)', async () => {
    const calls = setup(false);
    renderEditor('/autopilots/new');
    await waitFor(() => expect(screen.getByTestId('autopilot-editor')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('autopilot-editor-name'), '过滤坏JSON');
    // make the save gate pass (the default prompt action needs an executor)
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    await userEvent.selectOptions(screen.getByTestId('autopilot-editor-executor'), 'ag-1');
    // open the filter section and type invalid JSON into payload_match
    await userEvent.click(screen.getByTestId('autopilot-section-filter-toggle'));
    await userEvent.type(screen.getByTestId('autopilot-editor-payload-match'), 'not-json');
    await userEvent.click(screen.getByTestId('autopilot-editor-save'));
    // dedicated validation toast, not the generic error
    await waitFor(() =>
      expect(
        screen.getByText('Payload match rules must be a valid JSON array'),
      ).toBeInTheDocument(),
    );
    // nothing was submitted
    expect(calls.some((call) => call.method === 'POST' && call.url.endsWith('/autopilots'))).toBe(
      false,
    );
  });

  it('prefills from an existing rule in edit mode', async () => {
    setup(true);
    renderEditor('/autopilots/ap-1/edit');
    await waitFor(() =>
      expect((screen.getByTestId('autopilot-editor-name') as HTMLInputElement).value).toBe(
        '既有规则',
      ),
    );
    expect((screen.getByTestId('autopilot-editor-cron') as HTMLInputElement).value).toBe(
      '0 8 * * *',
    );
    expect((screen.getByTestId('autopilot-editor-timezone') as HTMLInputElement).value).toBe('UTC');
  });

  it('adds and removes action items', async () => {
    setup(false);
    renderEditor('/autopilots/new');
    await waitFor(() => expect(screen.getByTestId('autopilot-editor')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('autopilot-section-actions-toggle'));
    expect(screen.getByTestId('autopilot-action-0')).toBeInTheDocument();
    await userEvent.click(screen.getByTestId('autopilot-add-action'));
    expect(screen.getByTestId('autopilot-action-1')).toBeInTheDocument();
  });

  it('toggles sections open and closed', async () => {
    setup(false);
    renderEditor('/autopilots/new');
    await waitFor(() =>
      expect(screen.getByTestId('autopilot-section-trigger-body')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByTestId('autopilot-section-trigger-toggle'));
    expect(screen.queryByTestId('autopilot-section-trigger-body')).toBeNull();
    await userEvent.click(screen.getByTestId('autopilot-section-guardrails-toggle'));
    expect(screen.getByTestId('autopilot-section-guardrails-body')).toBeInTheDocument();
    expect(
      (screen.getByTestId('autopilot-editor-require-approval') as HTMLInputElement).checked,
    ).toBe(false);
  });

  it('shows webhook secret selector for webhook triggers', async () => {
    setup(false);
    renderEditor('/autopilots/new');
    await waitFor(() =>
      expect(screen.getByTestId('autopilot-editor-trigger-type')).toBeInTheDocument(),
    );
    await userEvent.selectOptions(
      screen.getByTestId('autopilot-editor-trigger-type'),
      'webhook_received',
    );
    expect(screen.getByTestId('autopilot-editor-secret')).toBeInTheDocument();
    // without a secret selected, save stays disabled even with a name
    await userEvent.type(screen.getByTestId('autopilot-editor-name'), 'wh rule');
    expect((screen.getByTestId('autopilot-editor-save') as HTMLButtonElement).disabled).toBe(true);
  });

  it('blocks a member from opening the management editor directly', async () => {
    const calls: string[] = [];
    const memberMe = {
      ...ME,
      memberships: [{ ...ME.memberships[0], role: 'member' }],
    };
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: memberMe } });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch);

    renderEditor('/autopilots/new');

    await waitFor(() => expect(screen.getByText(/No permission/i)).toBeInTheDocument());
    expect(screen.queryByTestId('autopilot-editor')).toBeNull();
    expect(calls.some((url) => url.includes('/agents'))).toBe(false);
    expect(calls.some((url) => url.includes('/webhook-secrets'))).toBe(false);
  });

  it('shows the no-workspace state without loading editor dependencies', async () => {
    const calls = setup(false, { ...ME, memberships: [] });

    renderEditor('/autopilots/new');

    expect(await screen.findByText('No workspace')).toBeInTheDocument();
    expect(screen.queryByTestId('autopilot-editor')).toBeNull();
    expect(calls.some((call) => call.url.includes('/agents'))).toBe(false);
    expect(calls.some((call) => call.url.includes('/webhook-secrets'))).toBe(false);
  });
});
