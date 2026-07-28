/**
 * IntegrationsPage 组件测试(integrations.md §4.1):连接器目录 + 集成表 +
 * 添加/OAuth + 启停/删除 + RBAC 只读 + oauth 回跳横幅 + 行级实时重拉。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { IntegrationsPage } from '../IntegrationsPage';

const originalLocation = window.location;
const locationAssign = vi.fn();

beforeEach(() => {
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { assign: locationAssign, origin: 'http://localhost', search: '' },
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  locationAssign.mockReset();
  Object.defineProperty(window, 'location', { configurable: true, value: originalLocation });
});

const INTEGRATION = {
  id: 'int-1',
  workspace_id: 'ws-1',
  kind: 'im_slack',
  name: 'Slack 值班',
  status: 'active',
  config: { app_id: 'A1' },
  has_secret: true,
  created_by: 'm-1',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};

const BINDING = {
  id: 'b-1',
  integration_id: 'int-1',
  provider: 'slack',
  provider_tenant_key: 'T1',
  scope: 'workspace',
  project_id: null,
  external_ref: 'C123',
  match_config: {},
  bound_agent_id: null,
  status: 'active',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};

function makeMe(role: string, withMembership = true) {
  return {
    user: { id: 'u-1', email: 'o@x.com', display_name: 'Owner' },
    memberships: withMembership
      ? [
          {
            workspace_id: 'ws-1',
            workspace_name: 'T',
            workspace_slug: 't',
            role,
            status: 'active',
            joined_at: null,
          },
        ]
      : [],
  };
}

interface Recorded {
  url: string;
  method: string;
}

interface SetupOptions {
  readonly integrations?: unknown[];
  readonly role?: string;
  readonly bindings?: unknown[];
  readonly withMembership?: boolean;
}

function setup(opts: SetupOptions = {}): Recorded[] {
  const calls: Recorded[] = [];
  const integrations = opts.integrations ?? [INTEGRATION];
  const role = opts.role ?? 'owner';
  const bindings = opts.bindings ?? [BINDING];
  const me = makeMe(role, opts.withMembership ?? true);
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
    if (url.includes('/external-identities'))
      return fakeResponse({ body: { data: [], next_cursor: null } });
    if (/\/integrations\/[^/]+\/bindings/.test(url))
      return fakeResponse({ body: { data: bindings, next_cursor: null } });
    if (url.includes('/integrations') && method === 'GET')
      return fakeResponse({ body: { data: integrations, next_cursor: null } });
    if (method === 'POST' && url.includes('/integrations'))
      return fakeResponse({
        body: { data: { integration: INTEGRATION, secret_accepted: true } },
      });
    if (method === 'PATCH')
      return fakeResponse({ body: { data: { ...INTEGRATION, status: 'disabled' } } });
    if (method === 'DELETE') return fakeResponse({ status: 204 });
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

function renderPage(realtime?: ReturnType<typeof makeRealtime>, route = '/integrations') {
  const page = (
    <RealtimeContext.Provider value={realtime ? realtime.value : null}>
      <Routes>
        <Route path="/integrations" element={<IntegrationsPage />} />
        <Route path="/integrations/:id" element={<div>detail</div>} />
      </Routes>
    </RealtimeContext.Provider>
  );
  return renderWithProviders(page, { route });
}

describe('IntegrationsPage', () => {
  it('renders the connector catalog and the connected table with binding counts', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-name-int-1')).toBeInTheDocument());
    expect(screen.getByTestId('connector-card-im_feishu')).toBeInTheDocument();
    expect(screen.getByTestId('connector-card-webhook_outbound')).toBeInTheDocument();
    expect(screen.getByTestId('integration-bindings-int-1').textContent).toBe('1');
    expect(screen.getByTestId('connector-count-im_slack').textContent).toContain('1');
  });

  it('shows the read-only banner and hides write actions for non-admins', async () => {
    setup({ role: 'member' });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-name-int-1')).toBeInTheDocument());
    expect(screen.getByTestId('integrations-readonly-banner')).toBeInTheDocument();
    expect(screen.queryByTestId('integration-create')).toBeNull();
    expect(screen.queryByTestId('connector-connect-im_slack')).toBeNull();
    expect(screen.queryByTestId('integration-toggle-int-1')).toBeNull();
  });

  it('creates an integration through the add dialog', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-create'));
    await userEvent.type(screen.getByTestId('integration-add-name'), '新集成');
    await userEvent.click(screen.getByTestId('integration-add-submit'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.url.endsWith('/integrations') && call.method === 'POST'),
      ).toBe(true),
    );
  });

  it('rejects invalid config JSON without posting', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-create'));
    await userEvent.type(screen.getByTestId('integration-add-name'), '新集成');
    fireEvent.change(screen.getByTestId('integration-add-config'), { target: { value: '{bad' } });
    await userEvent.click(screen.getByTestId('integration-add-submit'));
    await waitFor(() => expect(screen.getByTestId('integrations-table')).toBeInTheDocument());
    expect(
      calls.some((call) => call.url.endsWith('/integrations') && call.method === 'POST'),
    ).toBe(false);
  });

  it('starts oauth from the card and from the dialog', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('connector-connect-vcs_github')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('connector-connect-vcs_github'));
    expect(locationAssign).toHaveBeenCalledWith(
      expect.stringContaining('/integrations/oauth/vcs_github/authorize'),
    );
    // webhook_outbound opens the manual dialog instead of oauth (no oauth button)
    await userEvent.click(screen.getByTestId('connector-connect-webhook_outbound'));
    await waitFor(() => expect(screen.getByTestId('integration-add-kind')).toBeInTheDocument());
    expect(screen.queryByTestId('integration-add-oauth')).toBeNull();
  });

  it('toggles an integration status through the confirm dialog', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-toggle-int-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-toggle-int-1'));
    await userEvent.click(screen.getByTestId('integration-toggle-confirm'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith('/integrations/int-1') && call.method === 'PATCH')).toBe(true),
    );
  });

  it('deletes an integration through the confirm dialog', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-delete-int-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-delete-int-1'));
    await userEvent.click(screen.getByTestId('integration-delete-confirm'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith('/integrations/int-1') && call.method === 'DELETE')).toBe(true),
    );
  });

  it('shows oauth success and error banners from the search param', async () => {
    setup();
    const { unmount } = renderPage(undefined, '/integrations?oauth=success');
    await waitFor(() => expect(screen.getByTestId('oauth-success-banner')).toBeInTheDocument());
    unmount();
    renderPage(undefined, '/integrations?oauth=error');
    await waitFor(() => expect(screen.getByTestId('oauth-error-banner')).toBeInTheDocument());
  });

  it('dismisses the oauth banner', async () => {
    setup();
    renderPage(undefined, '/integrations?oauth=success');
    await waitFor(() => expect(screen.getByTestId('oauth-success-banner')).toBeInTheDocument());
    const dismiss = screen.getByRole('button', { name: /close/i });
    await userEvent.click(dismiss);
    await waitFor(() => expect(screen.queryByTestId('oauth-success-banner')).toBeNull());
  });

  it('reloads when an integrations realtime frame arrives', async () => {
    const calls = setup();
    const realtime = makeRealtime();
    renderPage(realtime);
    await waitFor(() => expect(screen.getByTestId('integration-name-int-1')).toBeInTheDocument());
    await waitFor(() => expect(realtime.subscribed).toContain('workspace:ws-1:integrations'));
    const initial = calls.filter((call) => call.url.includes('/integrations?')).length;
    realtime.emit({
      channel: 'workspace:ws-1:integrations',
      event: 'integration.updated',
      seq: 2,
      payload: {},
    } as unknown as RealtimeEventFrame);
    await waitFor(() =>
      expect(calls.filter((call) => call.url.includes('/integrations?')).length).toBeGreaterThan(initial),
    );
  });

  it('shows the empty state without integrations', async () => {
    setup({ integrations: [] });
    renderPage();
    await waitFor(() => expect(screen.getByText(/Connect your first integration/)).toBeInTheDocument());
  });

  it('shows the no-workspace state without memberships', async () => {
    setup({ withMembership: false, integrations: [] });
    renderPage();
    await waitFor(() => expect(screen.getByText(/No workspace/)).toBeInTheDocument());
  });

  it('shows the error state on fetch failure', async () => {
    const impl = (async () =>
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } })) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument());
  });

  it('retries after a load error', async () => {
    const calls: Recorded[] = [];
    const impl = (async (input: RequestInfo | URL) => {
      calls.push({ url: String(input), method: 'GET' });
      return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument());
    const before = calls.length;
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(before));
  });

  it('changes kind, masks the secret and starts oauth from the add dialog', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-create'));
    await userEvent.selectOptions(screen.getByTestId('integration-add-kind'), 'vcs_github');
    await userEvent.type(screen.getByTestId('integration-add-secret'), 'shh');
    expect(screen.getByText('••••')).toBeInTheDocument();
    await userEvent.click(screen.getByTestId('integration-add-oauth'));
    expect(locationAssign).toHaveBeenCalledWith(
      expect.stringContaining('/integrations/oauth/vcs_github/authorize'),
    );
    // cancel closes the dialog
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByTestId('integration-add-kind')).toBeNull());
  });

  it('closes the add dialog via the close button', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-create'));
    await waitFor(() => expect(screen.getByTestId('integration-add-kind')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    await waitFor(() => expect(screen.queryByTestId('integration-add-kind')).toBeNull());
  });

  it('enables a disabled integration through the toggle dialog', async () => {
    const calls = setup({ integrations: [INTEGRATION, { ...INTEGRATION, id: 'int-2', status: 'disabled' }] });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-toggle-int-2')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-toggle-int-2'));
    await waitFor(() => expect(screen.getByTestId('integration-toggle-text')).toBeInTheDocument());
    expect(screen.getByTestId('integration-toggle-text').textContent).toMatch(/Enable/);
    await userEvent.click(screen.getByTestId('integration-toggle-confirm'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith('/integrations/int-2') && call.method === 'PATCH')).toBe(true),
    );
  });

  it('navigates to the detail page from the row and the detail icon', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-row-int-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-detail-int-1'));
    await waitFor(() => expect(screen.getByText('detail')).toBeInTheDocument());
  });

  it('navigates to the detail page when the row is clicked', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-row-int-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-row-int-1'));
    await waitFor(() => expect(screen.getByText('detail')).toBeInTheDocument());
  });

  it('submits a secret when creating an integration', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-create'));
    await userEvent.type(screen.getByTestId('integration-add-name'), '带凭据');
    await userEvent.type(screen.getByTestId('integration-add-secret'), 'topsecret');
    await userEvent.click(screen.getByTestId('integration-add-submit'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith('/integrations') && call.method === 'POST')).toBe(true),
    );
  });

  it('surfaces a toggle failure as a toast and ignores foreign-channel frames', async () => {
    const calls: Recorded[] = [];
    const me = makeMe('owner');
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({ url, method });
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/external-identities')) return fakeResponse({ body: { data: [], next_cursor: null } });
      if (/\/integrations\/[^/]+\/bindings/.test(url)) return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/integrations') && method === 'GET')
        return fakeResponse({ body: { data: [INTEGRATION], next_cursor: null } });
      if (method === 'PATCH')
        return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    const realtime = makeRealtime();
    renderPage(realtime);
    await waitFor(() => expect(screen.getByTestId('integration-toggle-int-1')).toBeInTheDocument());
    await waitFor(() => expect(realtime.subscribed).toContain('workspace:ws-1:integrations'));
    realtime.emit({ channel: 'workspace:other', event: 'integration.updated', seq: 1, payload: {} } as unknown as RealtimeEventFrame);
    await userEvent.click(screen.getByTestId('integration-toggle-int-1'));
    await userEvent.click(screen.getByTestId('integration-toggle-confirm'));
    await waitFor(() => expect(screen.getByText(/internal error/i)).toBeInTheDocument());
  });

  it('closes the delete dialog without deleting', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-delete-int-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-delete-int-1'));
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    await waitFor(() => expect(screen.queryByTestId('integration-delete-confirm')).toBeNull());
  });

  it('closes the delete dialog via cancel', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-delete-int-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-delete-int-1'));
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByTestId('integration-delete-confirm')).toBeNull());
  });

  it('closes the toggle dialog via the close button and cancel', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-toggle-int-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-toggle-int-1'));
    await waitFor(() => expect(screen.getByTestId('integration-toggle-text')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    await waitFor(() => expect(screen.queryByTestId('integration-toggle-text')).toBeNull());
    await userEvent.click(screen.getByTestId('integration-toggle-int-1'));
    await waitFor(() => expect(screen.getByTestId('integration-toggle-text')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByTestId('integration-toggle-text')).toBeNull());
  });
});
