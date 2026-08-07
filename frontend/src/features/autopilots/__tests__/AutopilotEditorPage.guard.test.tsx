/**
 * AutopilotEditorPage 脏态离开保护测试(L242,autopilot.md §4.2 保存草稿):
 * 表单与保存快照不同即为脏——脏态下站内导航弹 stay/discard 确认,
 * stay 保留输入、discard 放弃并前往;取消按钮走 requestLeave 同一确认;
 * 干净表单(未改动)直接放行不弹确认。
 */
import { fireEvent, screen } from '@testing-library/react';
import { Link, Route, Routes } from 'react-router';
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

function stubAll(): void {
  const impl = (async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (url.includes('/agents')) return fakeResponse({ body: { data: AGENTS, next_cursor: null } });
    if (url.includes('/webhook-secrets'))
      return fakeResponse({ body: { data: [], next_cursor: null } });
    if (url.includes('/preview-schedule'))
      return fakeResponse({
        body: { data: { cron: '0 8 * * *', timezone: 'UTC', next_runs: [] } },
      });
    return fakeResponse({ body: { data: RULE } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
}

function renderEditorWithExit() {
  return renderWithProviders(
    <>
      <Routes>
        <Route path="/autopilots/:autopilotId/edit" element={<AutopilotEditorPage />} />
        <Route path="/autopilots/new" element={<AutopilotEditorPage />} />
        <Route path="/somewhere" element={<div data-testid="left-page">left-page</div>} />
      </Routes>
      <Link to="/somewhere" data-testid="leave-link">
        leave
      </Link>
    </>,
    { route: '/autopilots/ap-1/edit' },
  );
}

describe('AutopilotEditorPage dirty navigation guard (L242)', () => {
  it('blocks in-app navigation while the form is dirty; stay keeps the edits', async () => {
    stubAll();
    renderEditorWithExit();
    const nameInput = (await screen.findByTestId('autopilot-editor-name')) as HTMLInputElement;

    fireEvent.change(nameInput, { target: { value: '改动未保存' } });
    fireEvent.click(screen.getByTestId('leave-link'));

    expect(await screen.findByTestId('dirty-guard-stay')).toBeTruthy();
    // stay:留在原页,输入仍在
    fireEvent.click(screen.getByTestId('dirty-guard-stay'));
    expect(screen.queryByTestId('left-page')).toBeNull();
    expect((screen.getByTestId('autopilot-editor-name') as HTMLInputElement).value).toBe(
      '改动未保存',
    );
  });

  it('discard leaves the page and drops the edits', async () => {
    stubAll();
    renderEditorWithExit();
    const nameInput = (await screen.findByTestId('autopilot-editor-name')) as HTMLInputElement;

    fireEvent.change(nameInput, { target: { value: '要放弃的改动' } });
    fireEvent.click(screen.getByTestId('leave-link'));

    fireEvent.click(await screen.findByTestId('dirty-guard-discard'));
    expect(await screen.findByTestId('left-page')).toBeTruthy();
  });

  it('clean form navigates without the confirm dialog', async () => {
    stubAll();
    renderEditorWithExit();
    await screen.findByTestId('autopilot-editor-name');

    fireEvent.click(screen.getByTestId('leave-link'));

    expect(await screen.findByTestId('left-page')).toBeTruthy();
    expect(screen.queryByTestId('dirty-guard-stay')).toBeNull();
  });

  it('cancel button asks for confirmation when dirty (requestLeave)', async () => {
    stubAll();
    renderEditorWithExit();
    const nameInput = (await screen.findByTestId('autopilot-editor-name')) as HTMLInputElement;

    fireEvent.change(nameInput, { target: { value: '未保存' } });
    fireEvent.click(screen.getByTestId('autopilot-editor-cancel'));

    expect(await screen.findByTestId('dirty-guard-stay')).toBeTruthy();
    fireEvent.click(screen.getByTestId('dirty-guard-stay'));
    expect(screen.queryByTestId('left-page')).toBeNull();
  });
});
