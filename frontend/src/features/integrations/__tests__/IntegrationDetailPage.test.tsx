/**
 * IntegrationDetailPage 组件测试(integrations.md §4.1):概览(只读配置 + 编辑 +
 * 状态切换 + 凭据轮换 + has_secret 指示)+ tab 切换(绑定 / 事件台账)+ 实时重拉。
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
}

interface SetupFlags {
  readonly failPatch?: boolean;
  readonly failRotate?: boolean;
  readonly failReloadGet?: boolean;
  readonly failTest?: boolean;
  readonly role?: string;
}

function setup(integration: unknown = INTEGRATION, flags: SetupFlags = {}): Recorded[] {
  const calls: Recorded[] = [];
  let getCount = 0;
  const me = makeMe(flags.role ?? 'owner');
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
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
  return renderWithProviders(
    <RealtimeContext.Provider value={realtime ? realtime.value : null}>
      <Routes>
        <Route path="/integrations/:integrationId" element={<IntegrationDetailPage />} />
      </Routes>
    </RealtimeContext.Provider>,
    { route: '/integrations/int-1' },
  );
}

describe('IntegrationDetailPage', () => {
  it('exposes the shared detail layout and a dedicated Health tab', async () => {
    setup();
    renderPage();
    expect(await screen.findByTestId('detail-layout')).toBeInTheDocument();
    await userEvent.click(screen.getByTestId('integration-tab-health'));
    const healthPanel = await screen.findByTestId('integration-health-panel');
    expect(healthPanel).toHaveTextContent('Healthy');
    expect(healthPanel).toHaveTextContent('Jul');
    expect(screen.getByTestId('integration-health-test')).toBeInTheDocument();
  });

  it('shows empty health timestamps and read-only guidance to non-admins', async () => {
    setup({ ...INTEGRATION, last_success_at: null, last_error: null }, { role: 'member' });
    renderPage();
    await userEvent.click(await screen.findByTestId('integration-tab-health'));
    const healthPanel = await screen.findByTestId('integration-health-panel');
    expect(healthPanel).toHaveTextContent('—');
    expect(healthPanel).toHaveTextContent('No recent error');
    expect(healthPanel).toHaveTextContent('read-only');
    expect(screen.queryByTestId('integration-health-test')).toBeNull();
  });

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
    realtime.emit({
      channel: 'integration:int-1',
      event: 'integration.updated',
      seq: 2,
      payload: {},
    } as unknown as RealtimeEventFrame);
    await waitFor(() =>
      expect(
        calls.filter((call) => call.url.endsWith('/integrations/int-1') && call.method === 'GET')
          .length,
      ).toBeGreaterThan(initial),
    );
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
    await waitFor(() => expect(screen.getByTestId('integration-detail-name')).toBeInTheDocument());
  });

  it('swallows a failed realtime refetch', async () => {
    setup(INTEGRATION, { failReloadGet: true });
    const realtime = makeRealtime();
    renderPage(realtime);
    await waitFor(() => expect(screen.getByTestId('integration-detail-name')).toBeInTheDocument());
    await waitFor(() => expect(realtime.subscribed).toContain('integration:int-1'));
    realtime.emit({
      channel: 'integration:int-1',
      event: 'integration.updated',
      seq: 3,
      payload: {},
    } as unknown as RealtimeEventFrame);
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
    resolveMe?.(fakeResponse({ body: { data: makeMe('owner') } }));
    await new Promise((resolve) => setTimeout(resolve, 0));
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
    resolveMe?.(fakeResponse({ body: { data: makeMe('owner') } }));
    await new Promise((resolve) => setTimeout(resolve, 0));
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
});
