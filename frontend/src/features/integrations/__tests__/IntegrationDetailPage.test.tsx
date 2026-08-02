/**
 * IntegrationDetailPage 组件测试(integrations.md §4.1):概览(只读配置 + 编辑 +
 * 状态切换 + 凭据轮换 + has_secret 指示)+ tab 切换(绑定 / 事件台账)+ 实时重拉。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { IntegrationDetailPage } from '../IntegrationDetailPage';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const INTEGRATION = {
  id: 'int-1',
  workspace_id: 'ws-1',
  kind: 'im_slack',
  name: 'Slack 值班',
  status: 'active',
  config: { app_id: 'A1' },
  has_secret: true,
  health_state: 'healthy',
  last_error: null,
  last_success_at: '2026-07-08T00:00:00Z',
  events_7d: 12,
  created_by: 'm-1',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};

const DINGTALK_INTEGRATION = {
  ...INTEGRATION,
  id: 'int-1',
  kind: 'im_dingtalk',
  name: 'DingTalk R&D',
  config: {
    app_key: 'ding-app',
    corp_id: 'dingCorp01',
    receive_mode: 'stream',
    inbound_queue: 'serial_conversation',
    verbosity: 'final_only',
    ack_template: '✅ 已接收，处理中',
  },
};

function makeMe(role: string) {
  return {
    user: { id: 'u-1', email: 'o@x.com', display_name: 'Owner' },
    memberships: [
      {
        workspace_id: 'ws-1',
        workspace_name: 'T',
        workspace_slug: 't',
        role,
        status: 'active',
        joined_at: null,
      },
    ],
  };
}

interface Recorded {
  url: string;
  method: string;
  body?: string;
}

interface SetupFlags {
  readonly bindings?: unknown[];
  readonly failBindingsOnce?: boolean;
  readonly failPatch?: boolean;
  readonly failRotate?: boolean;
  readonly failReloadGet?: boolean;
  readonly failTest?: boolean;
  readonly role?: string;
}

function setup(integration: unknown = INTEGRATION, flags: SetupFlags = {}): Recorded[] {
  const calls: Recorded[] = [];
  let getCount = 0;
  let bindingsGetCount = 0;
  const me = makeMe(flags.role ?? 'owner');
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
    if (url.endsWith('/integrations/int-1') && method === 'GET') {
      getCount += 1;
      if (flags.failReloadGet && getCount > 1)
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'boom' } },
        });
      return fakeResponse({ body: { data: integration } });
    }
    if (url.endsWith('/stream-status')) {
      return fakeResponse({
        body: {
          data: {
            state: 'connected',
            last_frame_at: '2026-08-01T10:00:00Z',
            last_attempt_at: '2026-08-01T09:59:59Z',
            backoff_seconds: null,
          },
        },
      });
    }
    if (url.endsWith('/integrations/int-1/bindings')) {
      bindingsGetCount += 1;
      if (flags.failBindingsOnce && bindingsGetCount === 1) {
        return fakeResponse({
          status: 503,
          body: { error: { code: 'service_unavailable', message: 'retry' } },
        });
      }
      return fakeResponse({ body: { data: flags.bindings ?? [], next_cursor: null } });
    }
    if (method === 'PATCH') {
      if (flags.failPatch)
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'boom' } },
        });
      return fakeResponse({
        body: { data: { ...INTEGRATION, name: '新名称', status: 'disabled' } },
      });
    }
    if (method === 'POST' && url.endsWith(':test')) {
      if (flags.failTest)
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'boom' } },
        });
      return fakeResponse({ body: { data: { health_state: 'healthy', detail: null } } });
    }
    if (method === 'POST' && url.endsWith('/rotate-secret')) {
      if (flags.failRotate)
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'boom' } },
        });
      return fakeResponse({ body: { data: { ...INTEGRATION, has_secret: true } } });
    }
    return fakeResponse({ body: { data: [], next_cursor: null } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

type FrameListener = (frame: RealtimeEventFrame) => void;
type ErrorListener = (frame: { code: string; channel?: string }) => void;

function makeRealtime() {
  const listeners = new Set<FrameListener>();
  const errorListeners = new Set<ErrorListener>();
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
      onError: (listener: ErrorListener) => {
        errorListeners.add(listener);
        return () => errorListeners.delete(listener);
      },
    },
  } as unknown as RealtimeContextValue;
  return {
    value,
    subscribed,
    emit(frame: RealtimeEventFrame) {
      listeners.forEach((listener) => listener(frame));
    },
    emitError(frame: { code: string; channel?: string }) {
      errorListeners.forEach((listener) => listener(frame));
    },
  };
}

function renderPage(realtime?: ReturnType<typeof makeRealtime>) {
  return renderWithProviders(
    <RealtimeContext.Provider value={realtime ? realtime.value : null}>
      <Routes>
        <Route path="/integrations" element={<div>integrations</div>} />
        <Route path="/integrations/:integrationId" element={<IntegrationDetailPage />} />
      </Routes>
    </RealtimeContext.Provider>,
    { route: '/integrations/int-1' },
  );
}

describe('IntegrationDetailPage', () => {
  it('renders overview with config and has-secret indicator', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-detail-name')).toBeInTheDocument());
    expect(screen.getByTestId('integration-config').textContent).toContain('A1');
    expect(screen.getByTestId('integration-has-secret').textContent).toContain('Stored');
  });

  it('shows the disabled note for a disabled integration', async () => {
    setup({ ...INTEGRATION, status: 'disabled' });
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('integration-disabled-note')).toBeInTheDocument(),
    );
  });

  it('edits the integration through the dialog', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-edit')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-edit'));
    fireEvent.change(screen.getByTestId('integration-edit-name'), { target: { value: '新名称' } });
    await userEvent.click(screen.getByTestId('integration-edit-submit'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.url.endsWith('/integrations/int-1') && call.method === 'PATCH'),
      ).toBe(true),
    );
  });

  it('rejects invalid config JSON when editing', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-edit')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-edit'));
    fireEvent.change(screen.getByTestId('integration-edit-config'), { target: { value: '{bad' } });
    await userEvent.click(screen.getByTestId('integration-edit-submit'));
    await waitFor(() => expect(screen.getByTestId('integration-overview')).toBeInTheDocument());
    expect(
      calls.some((call) => call.url.endsWith('/integrations/int-1') && call.method === 'PATCH'),
    ).toBe(false);
  });

  it('toggles the integration status', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('integration-status-toggle')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByTestId('integration-status-toggle'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.url.endsWith('/integrations/int-1') && call.method === 'PATCH'),
      ).toBe(true),
    );
  });

  it('rotates the credential through the dialog', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-rotate')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-rotate'));
    await userEvent.type(screen.getByTestId('integration-rotate-secret'), 'newsecret');
    await userEvent.click(screen.getByTestId('integration-rotate-submit'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith('/integrations/int-1/rotate-secret'))).toBe(
        true,
      ),
    );
  });

  it('switches to the bindings and events tabs', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-tab-bindings')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-tab-bindings'));
    await waitFor(() => expect(screen.getByTestId('binding-drawer')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-tab-events'));
    await waitFor(() => expect(screen.getByTestId('event-ledger')).toBeInTheDocument());
  });

  it('refetches on an integration.updated realtime frame', async () => {
    const calls = setup();
    const realtime = makeRealtime();
    renderPage(realtime);
    await waitFor(() => expect(screen.getByTestId('integration-detail-name')).toBeInTheDocument());
    await waitFor(() => expect(realtime.subscribed).toContain('integration:int-1'));
    const initial = calls.filter(
      (call) => call.url.endsWith('/integrations/int-1') && call.method === 'GET',
    ).length;
    act(() => {
      realtime.emit({
        channel: 'integration:int-1',
        event: 'integration.updated',
        seq: 2,
        payload: {},
      } as unknown as RealtimeEventFrame);
    });
    await waitFor(() =>
      expect(
        calls.filter((call) => call.url.endsWith('/integrations/int-1') && call.method === 'GET')
          .length,
      ).toBeGreaterThan(initial),
    );
  });

  it('subscribes authorized project bindings and refetches the ledger on project frames', async () => {
    const calls = setup(INTEGRATION, {
      bindings: [
        {
          id: 'binding-project',
          integration_id: 'int-1',
          provider: 'slack',
          provider_tenant_key: 'T1',
          scope: 'project',
          project_id: 'project-visible',
          external_ref: 'C1',
          match_config: {},
          bound_agent_id: null,
          status: 'active',
          created_at: '2026-08-01T00:00:00Z',
          updated_at: '2026-08-01T00:00:00Z',
        },
      ],
    });
    const realtime = makeRealtime();
    renderPage(realtime);
    await waitFor(() => expect(screen.getByTestId('integration-tab-events')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-tab-events'));
    await waitFor(() => expect(realtime.subscribed).toContain('project:project-visible'));
    const initial = calls.filter((call) => call.url.includes('/integrations/int-1/events')).length;

    act(() => {
      realtime.emit({
        channel: 'project:project-visible',
        event: 'integration.event_ingested',
        seq: 4,
        payload: { integration_id: 'int-1' },
      } as unknown as RealtimeEventFrame);
    });
    await waitFor(() =>
      expect(
        calls.filter((call) => call.url.includes('/integrations/int-1/events')).length,
      ).toBeGreaterThan(initial),
    );
  });

  it('polls ledger truth with a healthy socket and no project bindings', async () => {
    const intervalSpy = vi.spyOn(window, 'setInterval');
    const calls = setup(INTEGRATION, { bindings: [] });
    const realtime = makeRealtime();
    const view = renderPage(realtime);
    await waitFor(() => expect(screen.getByTestId('integration-tab-events')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-tab-events'));
    await waitFor(() => expect(screen.getByTestId('event-ledger')).toBeInTheDocument());
    const initial = calls.filter((call) => call.url.includes('/integrations/int-1/events')).length;

    const pollingCallback = intervalSpy.mock.calls.find(([, delay]) => delay === 4000)?.[0];
    expect(pollingCallback).toBeTypeOf('function');
    act(() => {
      if (typeof pollingCallback === 'function') pollingCallback();
    });

    await waitFor(() =>
      expect(
        calls.filter((call) => call.url.includes('/integrations/int-1/events')).length,
      ).toBeGreaterThan(initial),
    );
    view.unmount();
  });

  it('bounds project subscriptions and polls when the visible binding set exceeds the socket cap', async () => {
    const intervalSpy = vi.spyOn(window, 'setInterval');
    const bindings = Array.from({ length: 140 }, (_, index) => ({
      id: `binding-${index}`,
      integration_id: 'int-1',
      provider: 'slack',
      provider_tenant_key: 'T1',
      scope: 'project',
      project_id: `project-${index.toString().padStart(3, '0')}`,
      external_ref: `C${index}`,
      match_config: {},
      bound_agent_id: null,
      status: 'active',
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-01T00:00:00Z',
    }));
    setup(INTEGRATION, { bindings });
    const realtime = makeRealtime();
    const view = renderPage(realtime);
    await waitFor(() => expect(screen.getByTestId('integration-tab-events')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-tab-events'));

    await waitFor(() =>
      expect(realtime.subscribed.filter((channel) => channel.startsWith('project:'))).toHaveLength(
        128,
      ),
    );
    expect(intervalSpy).toHaveBeenCalledWith(expect.any(Function), 4000);
    view.unmount();
  });

  it('polls the ledger when the gateway rejects a project subscription at capacity', async () => {
    const intervalSpy = vi.spyOn(window, 'setInterval');
    setup(INTEGRATION, {
      bindings: [
        {
          id: 'binding-project',
          integration_id: 'int-1',
          provider: 'slack',
          provider_tenant_key: 'T1',
          scope: 'project',
          project_id: 'project-visible',
          external_ref: 'C1',
          match_config: {},
          bound_agent_id: null,
          status: 'active',
          created_at: '2026-08-01T00:00:00Z',
          updated_at: '2026-08-01T00:00:00Z',
        },
      ],
    });
    const realtime = makeRealtime();
    const view = renderPage(realtime);
    await waitFor(() => expect(screen.getByTestId('integration-tab-events')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-tab-events'));
    await waitFor(() => expect(realtime.subscribed).toContain('project:project-visible'));

    act(() => {
      realtime.emitError({
        code: 'too_many_subscriptions',
        channel: 'project:project-visible',
      });
    });
    await waitFor(() => expect(intervalSpy).toHaveBeenCalledWith(expect.any(Function), 4000));
    view.unmount();
  });

  it('polls and retries project bindings after a transient binding read failure', async () => {
    const intervalSpy = vi.spyOn(window, 'setInterval');
    const calls = setup(INTEGRATION, {
      failBindingsOnce: true,
      bindings: [
        {
          id: 'binding-project',
          integration_id: 'int-1',
          provider: 'slack',
          provider_tenant_key: 'T1',
          scope: 'project',
          project_id: 'project-visible',
          external_ref: 'C1',
          match_config: {},
          bound_agent_id: null,
          status: 'active',
          created_at: '2026-08-01T00:00:00Z',
          updated_at: '2026-08-01T00:00:00Z',
        },
      ],
    });
    const realtime = makeRealtime();
    const view = renderPage(realtime);
    await waitFor(() => expect(screen.getByTestId('integration-tab-events')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-tab-events'));
    await waitFor(() => expect(intervalSpy).toHaveBeenCalledWith(expect.any(Function), 4000));

    const pollingCallback = intervalSpy.mock.calls.find(([, delay]) => delay === 4000)?.[0];
    expect(pollingCallback).toBeTypeOf('function');
    act(() => {
      if (typeof pollingCallback === 'function') pollingCallback();
    });

    await waitFor(() =>
      expect(
        calls.filter((call) => call.url.endsWith('/integrations/int-1/bindings')).length,
      ).toBeGreaterThanOrEqual(2),
    );
    await waitFor(() => expect(realtime.subscribed).toContain('project:project-visible'));
    view.unmount();
  });

  it('shows the error state when the integration cannot load', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: makeMe('owner') } });
      return fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'boom' } },
      });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
  });

  it('shows the no-credential indicator when has_secret is false', async () => {
    setup({ ...INTEGRATION, has_secret: false });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-has-secret')).toBeInTheDocument());
    expect(screen.getByTestId('integration-has-secret').textContent).toContain('No credential');
  });

  it('closes the edit and rotate dialogs via cancel', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-edit')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-edit'));
    await waitFor(() => expect(screen.getByTestId('integration-edit-name')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByTestId('integration-edit-name')).toBeNull());
    await userEvent.click(screen.getByTestId('integration-rotate'));
    await waitFor(() =>
      expect(screen.getByTestId('integration-rotate-secret')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByTestId('integration-rotate-secret')).toBeNull());
  });

  it('keeps rotate submit disabled until a secret is entered', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-rotate')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-rotate'));
    await waitFor(() =>
      expect(screen.getByTestId('integration-rotate-submit')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('integration-rotate-submit')).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
  });

  it('surfaces edit / toggle / rotate failures as toasts', async () => {
    setup(INTEGRATION, { failPatch: true, failRotate: true });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-edit')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-edit'));
    await userEvent.click(screen.getByTestId('integration-edit-submit'));
    await waitFor(() => expect(screen.getByText(/internal error/i)).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-status-toggle'));
    await waitFor(() => expect(screen.getAllByText(/internal error/i).length).toBeGreaterThan(0));
    await userEvent.click(screen.getByTestId('integration-rotate'));
    await userEvent.type(screen.getByTestId('integration-rotate-secret'), 'newsecret');
    await userEvent.click(screen.getByTestId('integration-rotate-submit'));
    await waitFor(() =>
      expect(screen.getByTestId('integration-rotate-secret')).toBeInTheDocument(),
    );
  });

  it('enables a disabled integration (toggle to active)', async () => {
    const calls = setup({ ...INTEGRATION, status: 'disabled' });
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('integration-status-toggle')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('integration-status-toggle').textContent).toMatch(/Enable/);
    await userEvent.click(screen.getByTestId('integration-status-toggle'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.url.endsWith('/integrations/int-1') && call.method === 'PATCH'),
      ).toBe(true),
    );
  });

  it('ignores foreign-channel frames and bumps on event_ingested', async () => {
    setup();
    const realtime = makeRealtime();
    renderPage(realtime);
    await waitFor(() => expect(screen.getByTestId('integration-detail-name')).toBeInTheDocument());
    await waitFor(() => expect(realtime.subscribed).toContain('integration:int-1'));
    act(() => {
      realtime.emit({
        channel: 'workspace:other',
        event: 'integration.updated',
        seq: 1,
        payload: {},
      } as unknown as RealtimeEventFrame);
      realtime.emit({
        channel: 'integration:int-1',
        event: 'integration.event_ingested',
        seq: 2,
        payload: {},
      } as unknown as RealtimeEventFrame);
    });
    await waitFor(() => expect(screen.getByTestId('integration-detail-name')).toBeInTheDocument());
  });

  it('swallows a failed realtime refetch', async () => {
    setup(INTEGRATION, { failReloadGet: true });
    const realtime = makeRealtime();
    renderPage(realtime);
    await waitFor(() => expect(screen.getByTestId('integration-detail-name')).toBeInTheDocument());
    await waitFor(() => expect(realtime.subscribed).toContain('integration:int-1'));
    act(() => {
      realtime.emit({
        channel: 'integration:int-1',
        event: 'integration.updated',
        seq: 3,
        payload: {},
      } as unknown as RealtimeEventFrame);
    });
    await waitFor(() => expect(screen.getByTestId('integration-detail-name')).toBeInTheDocument());
  });

  it('abandons an in-flight load when unmounted', async () => {
    let resolveMe: ((value: Response) => void) | undefined;
    const pending = new Promise<Response>((resolve) => {
      resolveMe = resolve;
    });
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return pending;
      return fakeResponse({ body: { data: INTEGRATION } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    const { unmount } = renderPage();
    unmount();
    resolveMe?.(fakeResponse({ body: { data: makeMe('owner') } }));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it('discards a late integration response after unmount (success path)', async () => {
    let resolveMe: ((value: Response) => void) | undefined;
    let resolveGet: ((value: Response) => void) | undefined;
    const meP = new Promise<Response>((resolve) => {
      resolveMe = resolve;
    });
    const getP = new Promise<Response>((resolve) => {
      resolveGet = resolve;
    });
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return meP;
      if (url.endsWith('/integrations/int-1')) return getP;
      return fakeResponse({ body: { data: [] } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    const { unmount } = renderPage();
    await act(async () => {
      resolveMe?.(fakeResponse({ body: { data: makeMe('owner') } }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    unmount();
    resolveGet?.(fakeResponse({ body: { data: INTEGRATION } }));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it('discards a late integration error after unmount (error path)', async () => {
    let resolveMe: ((value: Response) => void) | undefined;
    let resolveGet: ((value: Response) => void) | undefined;
    const meP = new Promise<Response>((resolve) => {
      resolveMe = resolve;
    });
    const getP = new Promise<Response>((resolve) => {
      resolveGet = resolve;
    });
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return meP;
      if (url.endsWith('/integrations/int-1')) return getP;
      return fakeResponse({ body: { data: [] } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    const { unmount } = renderPage();
    await act(async () => {
      resolveMe?.(fakeResponse({ body: { data: makeMe('owner') } }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    unmount();
    resolveGet?.(
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it('closes the edit dialog via the close button', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-edit')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-edit'));
    await waitFor(() => expect(screen.getByTestId('integration-edit-name')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    await waitFor(() => expect(screen.queryByTestId('integration-edit-name')).toBeNull());
  });

  it('maps a non-API error to the unknown error key', async () => {
    // fetchMe 缺 memberships → activeWorkspace 抛 TypeError(非 MeshApiError)→ error.unknown
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me'))
        return fakeResponse({
          body: { data: { user: { id: 'u-1', email: 'o@x.com', display_name: 'O' } } },
        });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await waitFor(() => expect(screen.getByText(/unexpected error/i)).toBeInTheDocument());
  });

  it('renders the health badge in the header', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-health')).toBeInTheDocument());
    expect(screen.getByTestId('integration-health').textContent).toContain('Healthy');
  });

  it('shows the auth-failed banner with last error and a re-authorize jump', async () => {
    const locationAssign = vi.fn();
    const originalLocation = window.location;
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { assign: locationAssign, origin: 'http://localhost', search: '' },
    });
    try {
      setup({ ...INTEGRATION, health_state: 'auth_failed', last_error: 'token_expired' });
      renderPage();
      await waitFor(() =>
        expect(screen.getByTestId('integration-auth-failed-banner')).toBeInTheDocument(),
      );
      expect(screen.getByTestId('integration-last-error').textContent).toBe('token_expired');
      await userEvent.click(screen.getByTestId('integration-reauthorize'));
      expect(locationAssign).toHaveBeenCalledWith(
        expect.stringContaining('/integrations/oauth/im_slack/authorize'),
      );
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: originalLocation });
    }
  });

  it('hides the re-authorize button and error subtext when not applicable', async () => {
    setup({ ...INTEGRATION, health_state: 'auth_failed', last_error: null }, { role: 'member' });
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('integration-auth-failed-banner')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('integration-reauthorize')).toBeNull();
    expect(screen.queryByTestId('integration-last-error')).toBeNull();
  });

  it('hides re-authorize for auth-failed non-oauth connectors', async () => {
    setup({
      ...INTEGRATION,
      kind: 'webhook_outbound',
      health_state: 'auth_failed',
      last_error: 'bad',
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('integration-auth-failed-banner')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('integration-reauthorize')).toBeNull();
  });

  it('shows the auth-failed banner after a failed connection test reveals revoked credentials', async () => {
    const me = makeMe('owner');
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.endsWith('/integrations/int-1') && method === 'GET')
        return fakeResponse({ body: { data: INTEGRATION } });
      if (method === 'POST' && url.endsWith(':test'))
        return fakeResponse({
          body: { data: { health_state: 'auth_failed', detail: 'token_revoked' } },
        });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-test')).toBeInTheDocument());
    expect(screen.queryByTestId('integration-auth-failed-banner')).toBeNull();
    await userEvent.click(screen.getByTestId('integration-test'));
    await waitFor(() =>
      expect(screen.getByTestId('integration-auth-failed-banner')).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByTestId('integration-health').textContent).toContain('Auth failed'),
    );
    expect(screen.getByTestId('integration-last-error').textContent).toBe('token_revoked');
  });

  it('tests the connection from the overview and updates the health badge', async () => {
    const calls = setup({ ...INTEGRATION, health_state: 'unreachable', last_error: 'timeout' });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-test')).toBeInTheDocument());
    expect(screen.getByTestId('integration-health').textContent).toContain('Unreachable');
    await userEvent.click(screen.getByTestId('integration-test'));
    await waitFor(() =>
      expect(
        calls.some(
          (call) => call.url.endsWith('/integrations/int-1:test') && call.method === 'POST',
        ),
      ).toBe(true),
    );
    await waitFor(() =>
      expect(screen.getByTestId('integration-health').textContent).toContain('Healthy'),
    );
    await waitFor(() => expect(screen.getByText(/Connection test completed/)).toBeInTheDocument());
  });

  it('surfaces a failed connection test as a toast', async () => {
    setup(INTEGRATION, { failTest: true });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-test')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-test'));
    await waitFor(() => expect(screen.getByText(/internal error/i)).toBeInTheDocument());
  });

  it('maps a malformed test envelope to the unknown error key', async () => {
    const me = makeMe('owner');
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.endsWith('/integrations/int-1') && method === 'GET')
        return fakeResponse({ body: { data: INTEGRATION } });
      if (method === 'POST' && url.endsWith(':test')) return fakeResponse({ body: { data: null } });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-test')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-test'));
    await waitFor(() => expect(screen.getByText(/unexpected error/i)).toBeInTheDocument());
  });

  it('hides the test button for non-admins', async () => {
    setup(INTEGRATION, { role: 'member' });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-detail-name')).toBeInTheDocument());
    expect(screen.queryByTestId('integration-test')).toBeNull();
  });

  it('renders DingTalk-specific connection and queue navigation without the generic test action', async () => {
    setup(DINGTALK_INTEGRATION);
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('dingtalk-connection-panel')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('integration-tab-queue')).toBeInTheDocument();
    expect(screen.queryByTestId('integration-test')).toBeNull();
    expect(screen.getByTestId('dingtalk-test-send')).toBeInTheDocument();
    expect(screen.getByTestId('dingtalk-diagnose')).toBeInTheDocument();
  });

  it('edits DingTalk through structured fields and never writes a secret-like config key', async () => {
    const calls = setup({
      ...DINGTALK_INTEGRATION,
      config: {
        ...DINGTALK_INTEGRATION.config,
        robot_code: 'ding-custom-robot',
        stream_reconnect: {
          base_seconds: 3,
          max_seconds: 120,
          heartbeat_timeout_seconds: 45,
        },
        card_template_id: 'mesh.custom.approval',
        app_secret_ref: '***',
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-edit')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-edit'));
    expect(screen.queryByTestId('integration-edit-config')).toBeNull();
    expect(screen.getByTestId('integration-edit-dingtalk-app-key')).toHaveValue('ding-app');
    expect(screen.getByTestId('integration-edit-dingtalk-receive-mode')).toHaveValue('stream');
    await userEvent.selectOptions(
      screen.getByTestId('integration-edit-dingtalk-receive-mode'),
      'http',
    );
    await userEvent.selectOptions(
      screen.getByTestId('integration-edit-dingtalk-verbosity'),
      'progress',
    );
    await userEvent.click(screen.getByTestId('integration-edit-submit'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.url.endsWith('/integrations/int-1') && call.method === 'PATCH'),
      ).toBe(true),
    );
    const patchCall = calls.find(
      (call) => call.url.endsWith('/integrations/int-1') && call.method === 'PATCH',
    );
    const body = JSON.parse(patchCall?.body ?? '{}') as { config: Record<string, unknown> };
    expect(body.config).toMatchObject({
      app_key: 'ding-app',
      corp_id: 'dingCorp01',
      receive_mode: 'http',
      inbound_queue: 'serial_conversation',
      verbosity: 'progress',
      ack_template: '✅ 已接收，处理中',
      robot_code: 'ding-custom-robot',
      stream_reconnect: {
        base_seconds: 3,
        max_seconds: 120,
        heartbeat_timeout_seconds: 45,
      },
      card_template_id: 'mesh.custom.approval',
    });
    expect(Object.keys(body.config).some((key) => /secret/i.test(key))).toBe(false);
  });

  it('initializes and edits the alternate DingTalk mode, verbosity, and acknowledgement', async () => {
    setup({
      ...DINGTALK_INTEGRATION,
      config: {
        ...DINGTALK_INTEGRATION.config,
        receive_mode: 'http',
        verbosity: 'progress',
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-edit')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-edit'));
    expect(screen.getByTestId('integration-edit-dingtalk-receive-mode')).toHaveValue('http');
    expect(screen.getByTestId('integration-edit-dingtalk-verbosity')).toHaveValue('progress');

    await userEvent.selectOptions(
      screen.getByTestId('integration-edit-dingtalk-receive-mode'),
      'stream',
    );
    await userEvent.selectOptions(
      screen.getByTestId('integration-edit-dingtalk-verbosity'),
      'final_only',
    );
    await userEvent.clear(screen.getByTestId('integration-edit-dingtalk-ack-template'));
    await userEvent.type(screen.getByTestId('integration-edit-dingtalk-ack-template'), 'Received');

    expect(screen.getByTestId('integration-edit-dingtalk-receive-mode')).toHaveValue('stream');
    expect(screen.getByTestId('integration-edit-dingtalk-verbosity')).toHaveValue('final_only');
    expect(screen.getByTestId('integration-edit-dingtalk-ack-template')).toHaveValue('Received');
  });

  it('turns queue_updated frames into authorized refetches instead of local queue patches', async () => {
    const calls = setup(DINGTALK_INTEGRATION);
    const realtime = makeRealtime();
    renderPage(realtime);
    await waitFor(() => expect(screen.getByTestId('integration-tab-queue')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-tab-queue'));
    await waitFor(() => expect(screen.getByTestId('integration-queue-panel')).toBeInTheDocument());

    const conversation = 'dingtalk:dingCorp01:cid-visible';
    const targetedBefore = calls.length;
    act(() => {
      realtime.emit({
        op: 'event',
        channel: 'workspace:ws-1:integrations',
        event: 'integration.queue_updated',
        seq: 10,
        payload: { integration_id: 'int-1', conversation_key: conversation },
      });
    });
    await waitFor(() =>
      expect(
        calls
          .slice(targetedBefore)
          .some((call) =>
            call.url.includes(`conversation_key=${encodeURIComponent(conversation)}`),
          ),
      ).toBe(true),
    );

    const batchedBefore = calls.length;
    const secondConversation = 'dingtalk:dingCorp01:cid-second';
    const thirdConversation = 'dingtalk:dingCorp01:cid-third';
    act(() => {
      realtime.emit({
        op: 'event',
        channel: 'workspace:ws-1:integrations',
        event: 'integration.queue_updated',
        seq: 12,
        payload: { integration_id: 'int-1', conversation_key: secondConversation },
      });
      realtime.emit({
        op: 'event',
        channel: 'workspace:ws-1:integrations',
        event: 'integration.queue_updated',
        seq: 13,
        payload: { integration_id: 'int-1', conversation_key: thirdConversation },
      });
    });
    await waitFor(() => {
      const newCalls = calls.slice(batchedBefore);
      expect(
        newCalls.some((call) =>
          call.url.includes(`conversation_key=${encodeURIComponent(secondConversation)}`),
        ),
      ).toBe(true);
      expect(
        newCalls.some((call) =>
          call.url.includes(`conversation_key=${encodeURIComponent(thirdConversation)}`),
        ),
      ).toBe(true);
    });

    const projectBefore = calls.length;
    act(() => {
      realtime.emit({
        op: 'event',
        channel: 'workspace:ws-1:integrations',
        event: 'integration.queue_updated',
        seq: 11,
        payload: { integration_id: 'int-1', scope: 'project' },
      });
    });
    await waitFor(() =>
      expect(
        calls
          .slice(projectBefore)
          .some(
            (call) =>
              call.url.includes('/queue?limit=100') && !call.url.includes('conversation_key='),
          ),
      ).toBe(true),
    );
  });

  it('consumes the initial queue load without starting a refetch loop', async () => {
    const calls = setup(DINGTALK_INTEGRATION);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-tab-queue')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-tab-queue'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.includes('/queue?limit=100'))).toBe(true),
    );
    const stableCount = calls.filter((call) => call.url.includes('/queue?limit=100')).length;
    expect(stableCount).toBe(1);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(calls.filter((call) => call.url.includes('/queue?limit=100'))).toHaveLength(stableCount);
  });

  it('bounds hidden-tab invalidations and lets the mount-wide fetch supersede them', async () => {
    const calls = setup(DINGTALK_INTEGRATION);
    const realtime = makeRealtime();
    renderPage(realtime);
    await waitFor(() => expect(screen.getByTestId('integration-tab-queue')).toBeInTheDocument());

    act(() => {
      for (const [seq, conversation] of [
        [20, 'dingtalk:dingCorp01:hidden-a'],
        [21, 'dingtalk:dingCorp01:hidden-b'],
        [22, 'dingtalk:dingCorp01:hidden-c'],
      ] as const) {
        realtime.emit({
          op: 'event',
          channel: 'workspace:ws-1:integrations',
          event: 'integration.queue_updated',
          seq,
          payload: { integration_id: 'int-1', conversation_key: conversation },
        });
      }
    });

    const beforeOpen = calls.length;
    await userEvent.click(screen.getByTestId('integration-tab-queue'));
    await waitFor(() =>
      expect(
        calls.slice(beforeOpen).filter((call) => call.url.includes('/queue?limit=100')),
      ).toHaveLength(1),
    );
    const queueCalls = calls.slice(beforeOpen).filter((call) => call.url.includes('/queue?'));
    expect(queueCalls).toHaveLength(1);
    expect(queueCalls[0]?.url).not.toContain('conversation_key=');
  });
});
