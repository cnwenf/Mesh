/**
 * ExternalIdentitiesPanel 组件测试(integrations.md §3.1 建链/解链):列表 + 两步
 * 建链(:link 下发验证码 → :link-confirm)+ 解链确认 + 403 解链禁止 toast + 空态。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { ExternalIdentitiesPanel } from '../ExternalIdentitiesPanel';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const IDENTITY = {
  id: 'id-1',
  provider: 'slack',
  provider_tenant_key: 'T1',
  external_user_key: 'U123',
  user_id: 'u-1',
  created_in_workspace_id: 'ws-1',
  verified_at: '2026-07-01T00:00:00Z',
  created_at: '2026-07-01T00:00:00Z',
};

const INTEGRATION = {
  id: 'int-1',
  workspace_id: 'ws-1',
  kind: 'im_slack',
  name: 'Slack 值班',
  status: 'active',
  config: {},
  has_secret: true,
  health_state: 'healthy',
  last_error: null,
  last_success_at: null,
  events_7d: 0,
  created_by: 'm-1',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};

interface Recorded {
  url: string;
  method: string;
}

function setup(opts: { readonly identities?: unknown[]; readonly unlinkStatus?: number } = {}): Recorded[] {
  const calls: Recorded[] = [];
  const identities = opts.identities ?? [IDENTITY];
  const unlinkStatus = opts.unlinkStatus ?? 204;
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.includes('/external-identities') && method === 'GET')
      return fakeResponse({ body: { data: identities, next_cursor: null } });
    if (url.includes('/integrations') && method === 'GET')
      return fakeResponse({ body: { data: [INTEGRATION], next_cursor: null } });
    if (url.endsWith(':link')) return fakeResponse({ body: { data: { sent: true } } });
    if (url.endsWith(':link-confirm')) return fakeResponse({ body: { data: IDENTITY } });
    if (method === 'DELETE') {
      if (unlinkStatus !== 204)
        return fakeResponse({
          status: unlinkStatus,
          body: { error: { code: 'identity_unlink_forbidden', message: 'forbidden' } },
        });
      return fakeResponse({ status: 204 });
    }
    return fakeResponse({ body: { data: [], next_cursor: null } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

function renderPanel() {
  return renderWithProviders(<ExternalIdentitiesPanel workspaceId="ws-1" />);
}

describe('ExternalIdentitiesPanel', () => {
  it('renders linked identities', async () => {
    setup();
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('identity-row-id-1')).toBeInTheDocument());
    expect(screen.getByText('U123')).toBeInTheDocument();
  });

  it('runs the two-step link flow', async () => {
    const calls = setup({ identities: [] });
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('identity-link-open')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('identity-link-open'));
    await userEvent.selectOptions(screen.getByTestId('identity-provider'), 'slack');
    await userEvent.selectOptions(screen.getByTestId('identity-integration'), 'int-1');
    await userEvent.type(screen.getByTestId('identity-external-key'), 'U999');
    await userEvent.click(screen.getByTestId('identity-link-submit'));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith(':link'))).toBe(true));
    await waitFor(() => expect(screen.getByTestId('identity-code')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('identity-code'), '123456');
    await userEvent.click(screen.getByTestId('identity-confirm-submit'));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith(':link-confirm'))).toBe(true));
  });

  it('unlinks an identity through the confirm dialog', async () => {
    const calls = setup();
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('identity-unlink-id-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('identity-unlink-id-1'));
    await userEvent.click(screen.getByTestId('identity-unlink-confirm'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith('/external-identities/id-1') && call.method === 'DELETE')).toBe(true),
    );
  });

  it('surfaces identity_unlink_forbidden on a 403', async () => {
    setup({ unlinkStatus: 403 });
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('identity-unlink-id-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('identity-unlink-id-1'));
    await userEvent.click(screen.getByTestId('identity-unlink-confirm'));
    await waitFor(() => expect(screen.getByText(/only unlink external identities/)).toBeInTheDocument());
  });

  it('shows the empty state without identities', async () => {
    setup({ identities: [] });
    renderPanel();
    await waitFor(() => expect(screen.getByText(/No external accounts linked/)).toBeInTheDocument());
  });

  it('closes the link and unlink dialogs via cancel/close', async () => {
    setup();
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('identity-link-open')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('identity-link-open'));
    await waitFor(() => expect(screen.getByTestId('identity-provider')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByTestId('identity-provider')).toBeNull());
    await userEvent.click(screen.getByTestId('identity-unlink-id-1'));
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    await waitFor(() => expect(screen.queryByTestId('identity-unlink-confirm')).toBeNull());
  });

  it('falls back to an empty list when loading fails', async () => {
    const impl = (async () =>
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } })) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPanel();
    await waitFor(() => expect(screen.getByText(/No external accounts linked/)).toBeInTheDocument());
  });

  it('closes the link dialog via the close button, cancels at code step and unlink', async () => {
    setup();
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('identity-link-open')).toBeInTheDocument());
    // close link dialog via X
    await userEvent.click(screen.getByTestId('identity-link-open'));
    await waitFor(() => expect(screen.getByTestId('identity-provider')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    await waitFor(() => expect(screen.queryByTestId('identity-provider')).toBeNull());
    // reach code step then cancel
    await userEvent.click(screen.getByTestId('identity-link-open'));
    await userEvent.selectOptions(screen.getByTestId('identity-integration'), 'int-1');
    await userEvent.type(screen.getByTestId('identity-external-key'), 'U999');
    await userEvent.click(screen.getByTestId('identity-link-submit'));
    await waitFor(() => expect(screen.getByTestId('identity-code')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByTestId('identity-code')).toBeNull());
    // unlink confirm cancel
    await userEvent.click(screen.getByTestId('identity-unlink-id-1'));
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByTestId('identity-unlink-confirm')).toBeNull());
  });

  it('surfaces a link failure as a toast', async () => {
    const me = { user: { id: 'u-1', email: 'o@x.com', display_name: 'Owner' }, memberships: [] };
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/external-identities') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/integrations') && method === 'GET')
        return fakeResponse({ body: { data: [INTEGRATION], next_cursor: null } });
      if (url.endsWith(':link'))
        return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } });
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('identity-link-open')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('identity-link-open'));
    await userEvent.selectOptions(screen.getByTestId('identity-integration'), 'int-1');
    await userEvent.type(screen.getByTestId('identity-external-key'), 'U999');
    await userEvent.click(screen.getByTestId('identity-link-submit'));
    await waitFor(() => expect(screen.getByText(/internal error/i)).toBeInTheDocument());
  });

  it('surfaces a link-confirm failure as a toast', async () => {
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/external-identities') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/integrations') && method === 'GET')
        return fakeResponse({ body: { data: [INTEGRATION], next_cursor: null } });
      if (url.endsWith(':link')) return fakeResponse({ body: { data: { sent: true } } });
      if (url.endsWith(':link-confirm'))
        return fakeResponse({ status: 409, body: { error: { code: 'identity_already_linked', message: 'dup' } } });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('identity-link-open')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('identity-link-open'));
    await userEvent.selectOptions(screen.getByTestId('identity-integration'), 'int-1');
    await userEvent.type(screen.getByTestId('identity-external-key'), 'U999');
    await userEvent.click(screen.getByTestId('identity-link-submit'));
    await waitFor(() => expect(screen.getByTestId('identity-code')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('identity-code'), '123456');
    await userEvent.click(screen.getByTestId('identity-confirm-submit'));
    await waitFor(() => expect(screen.getByText(/already linked/)).toBeInTheDocument());
  });
});
