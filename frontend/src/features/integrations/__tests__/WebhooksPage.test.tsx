/**
 * WebhooksPage 组件测试(integrations.md §4.1 / §3.4):出向订阅表(https URL /
 * 事件 chips / 状态含熔断)+ 创建(https 预检 + 一次性密钥)+ 投递时间线(重试)+
 * 熔断恢复 / 暂停启用 / 删除 + RBAC 只读 + 空/错误态。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { WebhooksPage } from '../WebhooksPage';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const FUTURE = new Date(Date.now() + 60_000).toISOString();

const SUB_ACTIVE = {
  id: 'sub-1',
  integration_id: null,
  url: 'https://example.com/hook',
  event_types: ['issue.updated'],
  status: 'active',
  fail_count: 0,
  has_secret: true,
  deliveries_total: 20,
  deliveries_sent: 19,
  success_rate: 0.95,
  created_by: 'm-1',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};
const SUB_TRIPPED = {
  ...SUB_ACTIVE,
  id: 'sub-2',
  url: 'https://example.com/b',
  status: 'disabled',
  fail_count: 5,
  deliveries_total: 0,
  deliveries_sent: 0,
  success_rate: null,
};
const SUB_PAUSED = { ...SUB_ACTIVE, id: 'sub-3', url: 'https://example.com/c', status: 'paused' };

const DELIVERY_FAILED = {
  id: 'del-1',
  subscription_id: 'sub-1',
  event_ref: 'evt-1',
  state: 'failed',
  attempts: 3,
  next_retry_at: null,
  response_status: 500,
  last_error: 'timeout',
  created_at: '2026-07-01T00:00:00Z',
};
const DELIVERY_PENDING = {
  id: 'del-2',
  subscription_id: 'sub-1',
  event_ref: 'evt-2',
  state: 'pending',
  attempts: 1,
  next_retry_at: FUTURE,
  response_status: null,
  last_error: null,
  created_at: '2026-07-01T00:00:00Z',
};
const DELIVERY_SENT = {
  id: 'del-3',
  subscription_id: 'sub-1',
  event_ref: 'evt-3',
  state: 'sent',
  attempts: 1,
  next_retry_at: null,
  response_status: 200,
  last_error: null,
  created_at: '2026-07-01T00:00:00Z',
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

function setup(opts: { readonly role?: string; readonly subscriptions?: unknown[] } = {}): Recorded[] {
  const calls: Recorded[] = [];
  const role = opts.role ?? 'owner';
  const subscriptions = opts.subscriptions ?? [SUB_ACTIVE, SUB_TRIPPED, SUB_PAUSED];
  const me = makeMe(role);
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
    if (/\/webhook-subscriptions\/[^/]+\/deliveries\/[^/]+\/retry/.test(url))
      return fakeResponse({ body: { data: { ...DELIVERY_FAILED, state: 'pending' } } });
    if (/\/webhook-subscriptions\/[^/]+\/resume/.test(url))
      return fakeResponse({ body: { data: { ...SUB_TRIPPED, status: 'active', fail_count: 0 } } });
    if (method === 'POST' && url.endsWith(':send-test'))
      return fakeResponse({ body: { data: { delivery_id: 'del-t1', state: 'pending' } } });
    if (/\/webhook-subscriptions\/[^/]+\/deliveries/.test(url))
      return fakeResponse({
        body: { data: [DELIVERY_FAILED, DELIVERY_PENDING, DELIVERY_SENT], next_cursor: null },
      });
    if (url.includes('/webhook-subscriptions') && method === 'GET')
      return fakeResponse({ body: { data: subscriptions, next_cursor: null } });
    if (method === 'POST' && url.includes('/webhook-subscriptions'))
      return fakeResponse({ body: { data: { ...SUB_ACTIVE, secret: 'whsec_abc123' } } });
    if (method === 'PATCH')
      return fakeResponse({ body: { data: { ...SUB_PAUSED, status: 'active' } } });
    if (method === 'DELETE') return fakeResponse({ status: 204 });
    return fakeResponse({ body: { data: [], next_cursor: null } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

function renderPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/webhook-subscriptions" element={<WebhooksPage />} />
    </Routes>,
    { route: '/webhook-subscriptions' },
  );
}

describe('WebhooksPage', () => {
  it('renders subscriptions with url, event chips and status', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-url-sub-1')).toBeInTheDocument());
    expect(screen.getByTestId('webhook-card-sub-2')).toBeInTheDocument();
    expect(screen.getAllByText('issue.updated').length).toBeGreaterThan(0);
  });

  it('shows the read-only banner and hides create for non-admins', async () => {
    setup({ role: 'member' });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhooks-readonly-banner')).toBeInTheDocument());
    expect(screen.queryByTestId('webhook-create')).toBeNull();
  });

  it('creates a subscription and shows the one-time secret', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-create'));
    await userEvent.type(screen.getByTestId('webhook-url-input'), 'https://example.com/new');
    await userEvent.type(screen.getByTestId('webhook-event-types'), 'issue.created, issue.updated');
    await userEvent.click(screen.getByTestId('webhook-create-submit'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.url.endsWith('/webhook-subscriptions') && call.method === 'POST'),
      ).toBe(true),
    );
    await waitFor(() => expect(screen.getByTestId('webhook-fresh-secret')).toBeInTheDocument());
    expect(screen.getByTestId('webhook-fresh-secret').textContent).toBe('whsec_abc123');
    await userEvent.click(screen.getByTestId('webhook-secret-done'));
    await waitFor(() => expect(screen.queryByTestId('webhook-fresh-secret')).toBeNull());
  });

  it('disables submit for a non-https url', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-create'));
    await userEvent.type(screen.getByTestId('webhook-url-input'), 'http://example.com/x');
    expect(screen.getByTestId('webhook-create-submit')).toBeDisabled();
  });

  it('copies the one-time secret', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-create'));
    await userEvent.type(screen.getByTestId('webhook-url-input'), 'https://example.com/new');
    await userEvent.click(screen.getByTestId('webhook-create-submit'));
    await waitFor(() => expect(screen.getByTestId('webhook-copy-secret')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-copy-secret'));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('whsec_abc123'));
  });

  it('expands deliveries and retries a failed one', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-expand-sub-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-expand-sub-1'));
    await waitFor(() => expect(screen.getByTestId('delivery-row-del-1')).toBeInTheDocument());
    expect(screen.getByTestId('delivery-row-del-2')).toBeInTheDocument();
    expect(screen.getByTestId('delivery-retry-del-2')).toBeInTheDocument();
    expect(screen.getByTestId('delivery-error-del-1').textContent).toBe('timeout');
    await userEvent.click(screen.getByTestId('delivery-retry-btn-del-1'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith('/deliveries/del-1/retry'))).toBe(true),
    );
  });

  it('resumes a tripped subscription via the breaker banner', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-breaker-sub-2')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-resume-sub-2'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith('/webhook-subscriptions/sub-2/resume'))).toBe(true),
    );
  });

  it('enables a paused subscription', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-enable-sub-3')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-enable-sub-3'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.url.endsWith('/webhook-subscriptions/sub-3') && call.method === 'PATCH'),
      ).toBe(true),
    );
  });

  it('deletes a subscription through the confirm dialog', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-delete-sub-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-delete-sub-1'));
    await userEvent.click(screen.getByTestId('webhook-delete-confirm'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.url.endsWith('/webhook-subscriptions/sub-1') && call.method === 'DELETE'),
      ).toBe(true),
    );
  });

  it('shows the empty state without subscriptions', async () => {
    setup({ subscriptions: [] });
    renderPage();
    await waitFor(() => expect(screen.getByText(/No outbound subscriptions/)).toBeInTheDocument());
  });

  it('shows the error state on fetch failure', async () => {
    const impl = (async () =>
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } })) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument());
  });

  it('shows empty deliveries text', async () => {
    const calls: Recorded[] = [];
    const me = makeMe('owner');
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({ url, method });
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (/\/deliveries/.test(url)) return fakeResponse({ body: { data: [], next_cursor: null } });
      return fakeResponse({ body: { data: [SUB_ACTIVE], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-expand-sub-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-expand-sub-1'));
    await waitFor(() => expect(screen.getByTestId('deliveries-empty')).toBeInTheDocument());
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

  it('closes the create dialog via the close button and delete via cancel', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-create'));
    await waitFor(() => expect(screen.getByTestId('webhook-url-input')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    await waitFor(() => expect(screen.queryByTestId('webhook-url-input')).toBeNull());
    await userEvent.click(screen.getByTestId('webhook-delete-sub-1'));
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByTestId('webhook-delete-confirm')).toBeNull());
  });

  it('closes the create dialog via cancel and the delete dialog via the close button', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-create'));
    await waitFor(() => expect(screen.getByTestId('webhook-url-input')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByTestId('webhook-url-input')).toBeNull());
    await userEvent.click(screen.getByTestId('webhook-delete-sub-1'));
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    await waitFor(() => expect(screen.queryByTestId('webhook-delete-confirm')).toBeNull());
  });

  it('closes the one-time secret dialog via the close button', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-create'));
    await userEvent.type(screen.getByTestId('webhook-url-input'), 'https://example.com/new');
    await userEvent.click(screen.getByTestId('webhook-create-submit'));
    await waitFor(() => expect(screen.getByTestId('webhook-fresh-secret')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    await waitFor(() => expect(screen.queryByTestId('webhook-fresh-secret')).toBeNull());
  });

  it('shows the empty state without a workspace membership', async () => {
    const me = { user: { id: 'u-1', email: 'o@x.com', display_name: 'Owner' }, memberships: [] };
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await waitFor(() => expect(screen.getByText(/No outbound subscriptions/)).toBeInTheDocument());
  });

  it('shows empty deliveries when the deliveries request fails', async () => {
    const me = makeMe('owner');
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (/\/deliveries/.test(url))
        return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } });
      return fakeResponse({ body: { data: [SUB_ACTIVE], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-expand-sub-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-expand-sub-1'));
    await waitFor(() => expect(screen.getByTestId('deliveries-empty')).toBeInTheDocument());
  });

  it('maps a non-API error to the unknown error key', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me'))
        return fakeResponse({ body: { data: { user: { id: 'u-1', email: 'o@x.com', display_name: 'O' } } } });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await waitFor(() => expect(screen.getByText(/unexpected error/i)).toBeInTheDocument());
  });

  it('abandons an in-flight load when unmounted', async () => {
    let resolveMe: ((value: Response) => void) | undefined;
    const pending = new Promise<Response>((resolve) => {
      resolveMe = resolve;
    });
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return pending;
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    const { unmount } = renderPage();
    unmount();
    resolveMe?.(fakeResponse({ body: { data: makeMe('owner') } }));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it('surfaces resume and create failures as toasts', async () => {
    const me = makeMe('owner');
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (method === 'GET' && url.includes('/webhook-subscriptions'))
        return fakeResponse({ body: { data: [SUB_TRIPPED], next_cursor: null } });
      if (method === 'POST')
        return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-resume-sub-2')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-resume-sub-2'));
    await waitFor(() => expect(screen.getByText(/internal error/i)).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-create'));
    await userEvent.type(screen.getByTestId('webhook-url-input'), 'https://example.com/x');
    await userEvent.click(screen.getByTestId('webhook-create-submit'));
    await waitFor(() => expect(screen.getAllByText(/internal error/i).length).toBeGreaterThan(1));
  });

  it('collapses deliveries and shows the all-events chip', async () => {
    const allEvents = { ...SUB_ACTIVE, id: 'sub-9', url: 'https://example.com/all', event_types: [] };
    setup({ subscriptions: [allEvents] });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-expand-sub-9')).toBeInTheDocument());
    expect(screen.getByText('All events')).toBeInTheDocument();
    await userEvent.click(screen.getByTestId('webhook-expand-sub-9'));
    await waitFor(() => expect(screen.getByTestId('delivery-row-del-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-expand-sub-9'));
    await waitFor(() => expect(screen.queryByTestId('delivery-row-del-1')).toBeNull());
  });

  it('surfaces a copy failure toast when the clipboard rejects', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'));
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-create'));
    await userEvent.type(screen.getByTestId('webhook-url-input'), 'https://example.com/new');
    await userEvent.click(screen.getByTestId('webhook-create-submit'));
    await waitFor(() => expect(screen.getByTestId('webhook-copy-secret')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-copy-secret'));
    await waitFor(() => expect(screen.getByText(/copy manually/)).toBeInTheDocument());
  });

  it('shows the success rate and delivery totals per subscription', async () => {
    setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-success-rate-sub-1')).toBeInTheDocument());
    expect(screen.getByTestId('webhook-success-rate-sub-1').textContent).toContain('95%');
    expect(screen.getByTestId('webhook-success-rate-sub-1').textContent).toContain('20');
    // null success_rate (zero deliveries) renders as an em dash
    expect(screen.getByTestId('webhook-success-rate-sub-2').textContent).toContain('—');
  });

  it('sends a test event through the send-test endpoint', async () => {
    const calls = setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-send-test-sub-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-send-test-sub-1'));
    await waitFor(() =>
      expect(
        calls.some(
          (call) => call.url.endsWith('/webhook-subscriptions/sub-1:send-test') && call.method === 'POST',
        ),
      ).toBe(true),
    );
    await waitFor(() => expect(screen.getByText(/Test event sent/)).toBeInTheDocument());
  });

  it('hides the send-test action for non-admins', async () => {
    setup({ role: 'member' });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-url-sub-1')).toBeInTheDocument());
    expect(screen.queryByTestId('webhook-send-test-sub-1')).toBeNull();
  });

  it('surfaces a send-test failure as a toast', async () => {
    const me = makeMe('owner');
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (method === 'GET' && url.includes('/webhook-subscriptions'))
        return fakeResponse({ body: { data: [SUB_ACTIVE], next_cursor: null } });
      if (method === 'POST')
        return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('webhook-send-test-sub-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('webhook-send-test-sub-1'));
    await waitFor(() => expect(screen.getByText(/internal error/i)).toBeInTheDocument());
  });
});
