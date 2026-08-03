/**
 * AutopilotsPage 组件测试(autopilot.md §4.1):行渲染、筛选、kill switch
 * 二次确认流程、暂停/启用、空态与错误态、行级实时重拉。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { AutopilotsPage } from '../AutopilotsPage';

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

const RULE_ACTIVE = {
  id: 'ap-1',
  workspace_id: 'ws-1',
  name: '每日站会前汇总',
  description: null,
  trigger_type: 'schedule',
  trigger_config: { cron: '0 9 * * 1-5', timezone: 'Asia/Shanghai' },
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
  next_run_at: new Date(Date.now() + 3_600_000).toISOString(),
  last_run_at: new Date(Date.now() - 60_000).toISOString(),
  created_by: 'm-1',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  stats: { runs_30d: 20, success_rate: 0.95 },
};

const RULE_PAUSED = {
  ...RULE_ACTIVE,
  id: 'ap-2',
  name: '值班介入',
  status: 'paused',
  trigger_type: 'agent_mentioned',
};

interface Recorded {
  url: string;
  method: string;
}

function setup(rules: unknown[] = [RULE_ACTIVE, RULE_PAUSED], killSwitch = false): Recorded[] {
  const calls: Recorded[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (url.endsWith('/autopilots/kill-switch') && method === 'GET')
      return fakeResponse({ body: { data: { kill_switch: killSwitch } } });
    if (url.endsWith('/autopilots/kill-switch') && method === 'POST')
      return fakeResponse({
        body: {
          data: { kill_switch: true, paused_autopilots: 2, updated_at: '2026-07-27T00:00:00Z' },
        },
      });
    if (method !== 'GET') return fakeResponse({ body: { data: RULE_PAUSED } });
    return fakeResponse({ body: { data: rules, next_cursor: null } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

type FrameListener = (frame: RealtimeEventFrame) => void;

function makeRealtime() {
  const listeners = new Set<FrameListener>();
  const subscribed: string[] = [];
  const value: RealtimeContextValue = {
    state: 'connected',
    client: {
      subscribe: (channel: string) => {
        subscribed.push(channel);
      },
      unsubscribe: () => undefined,
      onFrame: (listener: FrameListener) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
    },
  } as unknown as RealtimeContextValue;
  return {
    value,
    subscribed,
    emit(frame: RealtimeEventFrame) {
      listeners.forEach((listener) => listener(frame));
    },
  };
}

function renderPage(realtime?: ReturnType<typeof makeRealtime>) {
  const page = (
    <RealtimeContext.Provider value={realtime ? realtime.value : null}>
      <Routes>
        <Route path="/autopilots" element={<AutopilotsPage />} />
        <Route path="/autopilots/new" element={<div>editor</div>} />
        <Route path="/autopilots/:id" element={<div>detail</div>} />
        <Route path="/webhooks" element={<div>webhooks</div>} />
        <Route path="/w/:workspaceSlug/automations/autopilots/new" element={<div>editor</div>} />
        <Route path="/w/:workspaceSlug/automations/autopilots/:id" element={<div>detail</div>} />
        <Route path="/w/:workspaceSlug/automations/webhooks" element={<div>webhooks</div>} />
      </Routes>
    </RealtimeContext.Provider>
  );
  return renderWithProviders(page, { route: '/autopilots' });
}

describe('AutopilotsPage', () => {
  it('renders rules with status, stats and schedule summary', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('autopilot-name-ap-1')).toBeInTheDocument());
    expect(screen.getByTestId('data-view')).toHaveClass('mesh-autopilots__page');
    expect(screen.getByTestId('autopilot-name-ap-2')).toBeInTheDocument();
    expect(screen.getByTestId('autopilot-success-ap-1').textContent).toContain('95%');
    expect(screen.getByTestId('autopilot-trigger-ap-1').textContent).toContain('Asia/Shanghai');
    // active rule shows pause; paused rule shows resume
    expect(screen.getByTestId('autopilot-pause-ap-1')).toBeInTheDocument();
    expect(screen.getByTestId('autopilot-resume-ap-2')).toBeInTheDocument();
  });

  it('navigates to the editor from the create button', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('autopilot-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('autopilot-create'));
    await waitFor(() => expect(screen.getByText('editor')).toBeInTheDocument());
  });

  it('pauses a rule through the row action', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('autopilot-pause-ap-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('autopilot-pause-ap-1'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith('/autopilots/ap-1/pause'))).toBe(true),
    );
  });

  it('runs the kill switch confirmation flow', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('autopilot-kill-switch-button')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByTestId('autopilot-kill-switch-button'));
    await userEvent.type(screen.getByTestId('autopilot-kill-reason'), '紧急止血');
    await userEvent.click(screen.getByTestId('autopilot-kill-confirm'));
    await waitFor(() =>
      expect(
        calls.some(
          (call) => call.url.endsWith('/autopilots/kill-switch') && call.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  it('reloads when an autopilot realtime frame arrives', async () => {
    const calls = setup();
    const realtime = makeRealtime();
    renderPage(realtime);
    await waitFor(() => expect(screen.getByTestId('autopilot-name-ap-1')).toBeInTheDocument());
    // wait for the subscription effect to register before emitting (otherwise
    // the frame can arrive before the listener exists — full-suite load race)
    await waitFor(() => expect(realtime.subscribed).toContain('workspace:ws-1:autopilots'));
    const initialListCalls = calls.filter((call) => call.url.includes('/autopilots?')).length;
    realtime.emit({
      channel: 'workspace:ws-1:autopilots',
      event: 'autopilot.updated',
      seq: 2,
      payload: {},
    } as unknown as RealtimeEventFrame);
    await waitFor(() =>
      expect(calls.filter((call) => call.url.includes('/autopilots?')).length).toBeGreaterThan(
        initialListCalls,
      ),
    );
    expect(realtime.subscribed).toContain('workspace:ws-1:autopilots');
  });

  it('shows the empty state without rules (onboarding 四要素空态:插画 + 文案 + 深链既有向导)', async () => {
    setup([]);
    renderPage();
    await waitFor(() => expect(screen.getByText(/No autopilots yet/)).toBeInTheDocument());
    // 主操作深链 autopilot 创建向导(onboarding.md §1.2.2)
    expect(screen.getByTestId('autopilot-empty-create')).toBeInTheDocument();
  });

  it('shows the error state on fetch failure', async () => {
    const impl = (async () =>
      fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'boom' } },
      })) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await waitFor(() => expect(screen.getByText(/error|unexpected/i)).toBeInTheDocument());
  });

  it('member sees rules without autopilot management controls', async () => {
    const memberMe = {
      ...ME,
      memberships: [{ ...ME.memberships[0], role: 'member' }],
    };
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: memberMe } });
      if (url.endsWith('/autopilots/kill-switch')) {
        return fakeResponse({ body: { data: { kill_switch: false } } });
      }
      return fakeResponse({ body: { data: [RULE_ACTIVE], next_cursor: null } });
    }) as typeof fetch);
    renderPage();
    await screen.findByTestId('autopilot-name-ap-1');
    expect(screen.queryByTestId('autopilot-create')).toBeNull();
    expect(screen.queryByTestId('autopilot-kill-switch-button')).toBeNull();
    expect(screen.queryByTestId('autopilot-pause-ap-1')).toBeNull();
  });
});
