/**
 * IntegrationsPage 组件测试(integrations.md §4.1):连接器目录 + 集成表 +
 * 添加/OAuth + 启停/删除 + RBAC 只读 + oauth 回跳横幅 + 行级实时重拉。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
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
  health_state: 'healthy',
  last_error: null,
  last_success_at: '2026-07-08T00:00:00Z',
  events_7d: 12,
  created_by: 'm-1',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};

const AUTH_FAILED_INTEGRATION = {
  ...INTEGRATION,
  id: 'int-9',
  name: 'Slack 告警',
  health_state: 'auth_failed',
  last_error: 'token_expired',
};

const DINGTALK_INTEGRATION = {
  ...INTEGRATION,
  id: 'int-dt',
  kind: 'im_dingtalk',
  name: '钉钉研发群',
  config: {
    app_key: 'ding-app',
    corp_id: 'dingCorp01',
    receive_mode: 'stream',
    inbound_queue: 'serial_conversation',
    verbosity: 'final_only',
  },
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
  body?: string;
}

interface SetupOptions {
  readonly integrations?: unknown[];
  readonly role?: string;
  readonly bindings?: unknown[];
  readonly projects?: unknown[];
  readonly withMembership?: boolean;
  readonly createErrorCode?: string;
}

function setup(opts: SetupOptions = {}): Recorded[] {
  const calls: Recorded[] = [];
  const integrations = opts.integrations ?? [INTEGRATION];
  const role = opts.role ?? 'owner';
  const bindings = opts.bindings ?? [BINDING];
  const projects = opts.projects ?? [];
  const me = makeMe(role, opts.withMembership ?? true);
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
    if (url.endsWith('/stream-status'))
      return fakeResponse({
        body: {
          data: {
            state: 'connected',
            last_frame_at: '2026-08-01T12:00:00Z',
            last_attempt_at: '2026-08-01T11:59:59Z',
            backoff_seconds: null,
          },
        },
      });
    if (url.includes('/external-identities'))
      return fakeResponse({ body: { data: [], next_cursor: null } });
    if (url.includes('/projects'))
      return fakeResponse({ body: { data: projects, next_cursor: null } });
    if (/\/integrations\/[^/]+\/bindings/.test(url))
      return fakeResponse({ body: { data: bindings, next_cursor: null } });
    if (url.includes('/integrations') && method === 'GET')
      return fakeResponse({ body: { data: integrations, next_cursor: null } });
    if (method === 'POST' && url.endsWith(':test'))
      return fakeResponse({ body: { data: { health_state: 'healthy', detail: null } } });
    if (method === 'POST' && url.includes('/integrations') && opts.createErrorCode !== undefined)
      return fakeResponse({
        status: opts.createErrorCode.endsWith('_unavailable')
          ? 502
          : opts.createErrorCode === 'dingtalk_credentials_invalid'
            ? 422
            : 409,
        body: {
          error: {
            code: opts.createErrorCode,
            message: 'neutral backend message',
            details: {},
          },
        },
      });
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
    expect(screen.getByTestId('connector-card-im_dingtalk')).toBeInTheDocument();
    expect(screen.getByTestId('connector-card-webhook_outbound')).toBeInTheDocument();
    expect(screen.getByTestId('integration-bindings-int-1').textContent).toBe('1');
    expect(screen.getByTestId('connector-count-im_slack').textContent).toContain('1');
  });

  it('does not count private-project bindings that are invisible to an ordinary member', async () => {
    setup({
      role: 'member',
      projects: [{ id: 'project-visible' }],
      bindings: [
        BINDING,
        { ...BINDING, id: 'b-visible', scope: 'project', project_id: 'project-visible' },
        { ...BINDING, id: 'b-private', scope: 'project', project_id: 'project-private' },
      ],
    });
    renderPage();

    await waitFor(() => expect(screen.getByTestId('integration-name-int-1')).toBeInTheDocument());
    expect(screen.getByTestId('integration-bindings-int-1')).toHaveTextContent('2');
  });

  it('creates DingTalk through structured secret-safe fields with Stream defaults', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('connector-connect-im_dingtalk')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByTestId('connector-connect-im_dingtalk'));
    expect(screen.getByTestId('integration-add-kind')).toHaveValue('im_dingtalk');
    expect(screen.getByTestId('integration-dingtalk-receive-mode')).toHaveValue('stream');
    expect(screen.getByTestId('integration-dingtalk-verbosity')).toHaveValue('final_only');
    expect(screen.getByTestId('integration-dingtalk-stream-hint')).toBeInTheDocument();
    expect(screen.queryByTestId('integration-add-config')).toBeNull();

    await userEvent.type(screen.getByTestId('integration-add-name'), '研发钉钉');
    await userEvent.type(screen.getByTestId('integration-dingtalk-app-key'), 'ding-app-key');
    await userEvent.type(screen.getByTestId('integration-dingtalk-corp-id'), 'dingCorp01');
    await userEvent.type(screen.getByTestId('integration-add-secret'), 'super-app-secret');
    await userEvent.click(screen.getByTestId('integration-add-submit'));

    await waitFor(() =>
      expect(
        calls.some((call) => call.url.endsWith('/integrations') && call.method === 'POST'),
      ).toBe(true),
    );
    const request = calls.find(
      (call) => call.url.endsWith('/integrations') && call.method === 'POST',
    );
    const body = JSON.parse(request?.body ?? '{}') as {
      kind: string;
      secret: string;
      config: Record<string, unknown>;
    };
    expect(body.kind).toBe('im_dingtalk');
    expect(body.secret).toBe('super-app-secret');
    expect(body.config).toMatchObject({
      app_key: 'ding-app-key',
      corp_id: 'dingCorp01',
      receive_mode: 'stream',
      inbound_queue: 'serial_conversation',
      verbosity: 'final_only',
      ack_template: '✅ 已接收，处理中',
    });
    expect(JSON.stringify(body.config)).not.toContain('super-app-secret');
    expect(Object.keys(body.config).some((key) => /secret/i.test(key))).toBe(false);
  });

  it('switches the DingTalk create flow to HTTP and shows the callback address', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('connector-connect-im_dingtalk')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByTestId('connector-connect-im_dingtalk'));
    await userEvent.selectOptions(screen.getByTestId('integration-dingtalk-receive-mode'), 'http');
    expect(screen.getByTestId('integration-dingtalk-callback-url').textContent).toContain(
      '/api/v1/integrations/dingtalk/events',
    );
    expect(screen.queryByTestId('integration-dingtalk-stream-hint')).toBeNull();

    await userEvent.type(screen.getByTestId('integration-add-name'), 'HTTP 钉钉');
    await userEvent.type(screen.getByTestId('integration-dingtalk-app-key'), 'ding-http');
    await userEvent.type(screen.getByTestId('integration-dingtalk-corp-id'), 'dingCorp02');
    await userEvent.type(screen.getByTestId('integration-add-secret'), 'http-secret');
    await userEvent.click(screen.getByTestId('integration-add-submit'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.url.endsWith('/integrations') && call.method === 'POST'),
      ).toBe(true),
    );
    const request = calls.find(
      (call) => call.url.endsWith('/integrations') && call.method === 'POST',
    );
    expect(JSON.parse(request?.body ?? '{}')).toMatchObject({
      config: { receive_mode: 'http' },
    });
  });

  it('shows persisted Stream state in connected rows and skips diagnostics for HTTP rows', async () => {
    const http = {
      ...DINGTALK_INTEGRATION,
      id: 'int-http',
      config: { ...DINGTALK_INTEGRATION.config, receive_mode: 'http' },
    };
    const calls = setup({ integrations: [DINGTALK_INTEGRATION, http] });
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('integration-stream-int-dt')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('integration-stream-int-dt').textContent).toMatch(/Connected/i);
    expect(screen.getByTestId('integration-receive-mode-int-http').textContent).toMatch(/HTTP/i);
    expect(calls.filter((call) => call.url.endsWith('/stream-status'))).toHaveLength(1);
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

  it.each([
    [
      'dingtalk_app_key_conflict',
      'This DingTalk app key is already owned by another workspace.',
    ],
    [
      'dingtalk_route_conflict',
      'This DingTalk corp and robot route is already configured.',
    ],
    [
      'dingtalk_app_credential_conflict',
      'Integrations sharing this DingTalk app must use the same credential.',
    ],
    [
      'dingtalk_stream_config_conflict',
      'Integrations sharing this DingTalk app must use the same Stream reconnect policy.',
    ],
    [
      'dingtalk_credentials_invalid',
      'DingTalk rejected these app credentials. Check the app key and app secret.',
    ],
    [
      'dingtalk_credential_verification_unavailable',
      'Mesh could not verify the DingTalk credentials. Try again when DingTalk is reachable.',
    ],
  ])('renders the localized admission error %s', async (code, expectedMessage) => {
    setup({ createErrorCode: code });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-create'));
    await userEvent.type(screen.getByTestId('integration-add-name'), 'Admission check');
    await userEvent.click(screen.getByTestId('integration-add-submit'));

    expect(await screen.findByText(expectedMessage)).toBeInTheDocument();
    expect(screen.queryByText(`error.${code}`)).toBeNull();
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
    expect(calls.some((call) => call.url.endsWith('/integrations') && call.method === 'POST')).toBe(
      false,
    );
  });

  it('starts oauth from the card and from the dialog', async () => {
    setup();
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('connector-connect-vcs_github')).toBeInTheDocument(),
    );
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
      expect(
        calls.some((call) => call.url.endsWith('/integrations/int-1') && call.method === 'PATCH'),
      ).toBe(true),
    );
  });

  it('deletes an integration through the confirm dialog', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-delete-int-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-delete-int-1'));
    await userEvent.click(screen.getByTestId('integration-delete-confirm'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.url.endsWith('/integrations/int-1') && call.method === 'DELETE'),
      ).toBe(true),
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
    act(() => {
      realtime.emit({
        channel: 'workspace:ws-1:integrations',
        event: 'integration.updated',
        seq: 2,
        payload: {},
      } as unknown as RealtimeEventFrame);
    });
    await waitFor(() =>
      expect(calls.filter((call) => call.url.includes('/integrations?')).length).toBeGreaterThan(
        initial,
      ),
    );
  });

  it('polls list truth with a healthy socket and no project bindings', async () => {
    const intervalSpy = vi.spyOn(window, 'setInterval');
    const calls = setup({ bindings: [] });
    const realtime = makeRealtime();
    const view = renderPage(realtime);
    await waitFor(() => expect(screen.getByTestId('integration-name-int-1')).toBeInTheDocument());
    await waitFor(() => expect(realtime.subscribed).toContain('workspace:ws-1:integrations'));
    const initial = calls.filter((call) => call.url.includes('/integrations?')).length;

    const pollingCallback = intervalSpy.mock.calls.find(([, delay]) => delay === 4000)?.[0];
    expect(pollingCallback).toBeTypeOf('function');
    act(() => {
      if (typeof pollingCallback === 'function') pollingCallback();
    });

    await waitFor(() =>
      expect(calls.filter((call) => call.url.includes('/integrations?')).length).toBeGreaterThan(
        initial,
      ),
    );
    view.unmount();
  });

  it('subscribes visible project binding channels and reloads on their event frames', async () => {
    const calls = setup({
      role: 'member',
      projects: [{ id: 'project-visible' }],
      bindings: [
        { ...BINDING, id: 'b-visible', scope: 'project', project_id: 'project-visible' },
        { ...BINDING, id: 'b-private', scope: 'project', project_id: 'project-private' },
      ],
    });
    const realtime = makeRealtime();
    renderPage(realtime);

    await waitFor(() => expect(realtime.subscribed).toContain('project:project-visible'));
    expect(realtime.subscribed).not.toContain('project:project-private');
    const initial = calls.filter((call) => call.url.includes('/integrations?')).length;
    act(() => {
      realtime.emit({
        channel: 'project:project-visible',
        event: 'integration.event_ingested',
        seq: 3,
        payload: { integration_id: 'int-1' },
      } as unknown as RealtimeEventFrame);
    });
    await waitFor(() =>
      expect(calls.filter((call) => call.url.includes('/integrations?')).length).toBeGreaterThan(
        initial,
      ),
    );
  });

  it('bounds list project subscriptions and polls when visible bindings exceed the socket cap', async () => {
    const intervalSpy = vi.spyOn(window, 'setInterval');
    setup({
      bindings: Array.from({ length: 140 }, (_, index) => ({
        ...BINDING,
        id: `binding-${index}`,
        scope: 'project',
        project_id: `project-${index.toString().padStart(3, '0')}`,
        external_ref: `C${index}`,
      })),
    });
    const realtime = makeRealtime();
    const view = renderPage(realtime);

    await waitFor(() =>
      expect(realtime.subscribed.filter((channel) => channel.startsWith('project:'))).toHaveLength(
        128,
      ),
    );
    expect(intervalSpy).toHaveBeenCalledWith(expect.any(Function), 4000);
    view.unmount();
  });

  it('shows the empty state without integrations', async () => {
    setup({ integrations: [] });
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/Connect your first integration/)).toBeInTheDocument(),
    );
  });

  it('shows the no-workspace state without memberships', async () => {
    setup({ withMembership: false, integrations: [] });
    renderPage();
    await waitFor(() => expect(screen.getByText(/No workspace/)).toBeInTheDocument());
  });

  it('shows the error state on fetch failure', async () => {
    const impl = (async () =>
      fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'boom' } },
      })) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument());
  });

  it('retries after a load error', async () => {
    const calls: Recorded[] = [];
    const impl = (async (input: RequestInfo | URL) => {
      calls.push({ url: String(input), method: 'GET' });
      return fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'boom' } },
      });
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
    const calls = setup({
      integrations: [INTEGRATION, { ...INTEGRATION, id: 'int-2', status: 'disabled' }],
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-toggle-int-2')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-toggle-int-2'));
    await waitFor(() => expect(screen.getByTestId('integration-toggle-text')).toBeInTheDocument());
    expect(screen.getByTestId('integration-toggle-text').textContent).toMatch(/Enable/);
    await userEvent.click(screen.getByTestId('integration-toggle-confirm'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.url.endsWith('/integrations/int-2') && call.method === 'PATCH'),
      ).toBe(true),
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
      expect(
        calls.some((call) => call.url.endsWith('/integrations') && call.method === 'POST'),
      ).toBe(true),
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
      if (url.includes('/external-identities'))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (/\/integrations\/[^/]+\/bindings/.test(url))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/integrations') && method === 'GET')
        return fakeResponse({ body: { data: [INTEGRATION], next_cursor: null } });
      if (method === 'PATCH')
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'boom' } },
        });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    const realtime = makeRealtime();
    renderPage(realtime);
    await waitFor(() => expect(screen.getByTestId('integration-toggle-int-1')).toBeInTheDocument());
    await waitFor(() => expect(realtime.subscribed).toContain('workspace:ws-1:integrations'));
    act(() => {
      realtime.emit({
        channel: 'workspace:other',
        event: 'integration.updated',
        seq: 1,
        payload: {},
      } as unknown as RealtimeEventFrame);
    });
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

  it('renders the events-7d column and a health badge per row', async () => {
    setup();
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('integration-events7d-int-1')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('integration-events7d-int-1').textContent).toBe('12');
    expect(screen.getByText('Events (7d)')).toBeInTheDocument();
    expect(screen.getByTestId('integration-health-int-1').textContent).toContain('Healthy');
  });

  it('shows the last error as a tooltip on the health badge', async () => {
    setup({ integrations: [AUTH_FAILED_INTEGRATION] });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-health-int-9')).toBeInTheDocument());
    expect(screen.getByTestId('integration-health-int-9').textContent).toContain('Auth failed');
    expect(screen.getByTestId('integration-health-int-9').getAttribute('title')).toBe(
      'token_expired',
    );
  });

  it('offers re-authorize for auth-failed oauth connectors and jumps to the authorize url', async () => {
    setup({ integrations: [AUTH_FAILED_INTEGRATION] });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-reauth-int-9')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-reauth-int-9'));
    expect(locationAssign).toHaveBeenCalledWith(
      expect.stringContaining('/integrations/oauth/im_slack/authorize'),
    );
  });

  it('hides re-authorize for auth-failed non-oauth connectors', async () => {
    setup({ integrations: [{ ...AUTH_FAILED_INTEGRATION, kind: 'webhook_outbound' }] });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-health-int-9')).toBeInTheDocument());
    expect(screen.queryByTestId('integration-reauth-int-9')).toBeNull();
  });

  it('tests the connection and reflects the returned health state', async () => {
    const calls = setup({ integrations: [INTEGRATION, AUTH_FAILED_INTEGRATION] });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-test-int-9')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-test-int-9'));
    await waitFor(() =>
      expect(
        calls.some(
          (call) => call.url.endsWith('/integrations/int-9:test') && call.method === 'POST',
        ),
      ).toBe(true),
    );
    await waitFor(() =>
      expect(screen.getByTestId('integration-health-int-9').textContent).toContain('Healthy'),
    );
    // sibling rows keep their own health state
    expect(screen.getByTestId('integration-health-int-1').textContent).toContain('Healthy');
    await waitFor(() => expect(screen.getByText(/Connection test completed/)).toBeInTheDocument());
  });

  it('keeps DingTalk outbound and inbound diagnostics out of the generic list test action', async () => {
    setup({ integrations: [DINGTALK_INTEGRATION] });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-row-int-dt')).toBeInTheDocument());
    expect(screen.queryByTestId('integration-test-int-dt')).toBeNull();
  });

  it('reflects a non-healthy test result with the error detail', async () => {
    const me = makeMe('owner');
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/external-identities'))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (/\/integrations\/[^/]+\/bindings/.test(url))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/integrations') && method === 'GET')
        return fakeResponse({ body: { data: [INTEGRATION], next_cursor: null } });
      if (method === 'POST' && url.endsWith(':test'))
        return fakeResponse({
          body: { data: { health_state: 'unreachable', detail: 'dial tcp timeout' } },
        });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-test-int-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-test-int-1'));
    await waitFor(() =>
      expect(screen.getByTestId('integration-health-int-1').textContent).toContain('Unreachable'),
    );
    await waitFor(() =>
      expect(screen.getByTestId('integration-health-int-1').getAttribute('title')).toBe(
        'dial tcp timeout',
      ),
    );
  });

  it('surfaces a connection test failure as a toast', async () => {
    const me = makeMe('owner');
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/external-identities'))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (/\/integrations\/[^/]+\/bindings/.test(url))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/integrations') && method === 'GET')
        return fakeResponse({ body: { data: [INTEGRATION], next_cursor: null } });
      if (method === 'POST' && url.endsWith(':test'))
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'boom' } },
        });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-test-int-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('integration-test-int-1'));
    await waitFor(() => expect(screen.getByText(/internal error/i)).toBeInTheDocument());
  });

  it('hides the re-authorize and test actions for non-admins', async () => {
    setup({ role: 'member', integrations: [AUTH_FAILED_INTEGRATION] });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('integration-health-int-9')).toBeInTheDocument());
    expect(screen.queryByTestId('integration-reauth-int-9')).toBeNull();
    expect(screen.queryByTestId('integration-test-int-9')).toBeNull();
  });
});
