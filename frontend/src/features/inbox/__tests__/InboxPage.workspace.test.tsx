import { fireEvent, screen, waitFor } from '@testing-library/react';
import { useLocation } from 'react-router';
import { Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import { WorkspaceProvider } from '../../../workspace/WorkspaceProvider';
import { useAuthStore } from '../../../state/authStore';
import { InboxPage } from '../InboxPage';

const ME = {
  user: { id: 'usr-1', email: 'owner@example.test', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-a',
      workspace_name: 'Alpha',
      workspace_slug: 'alpha',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
    {
      workspace_id: 'ws-b',
      workspace_name: 'Beta',
      workspace_slug: 'beta',
      role: 'admin',
      status: 'active',
      joined_at: null,
    },
  ],
};

const BETA = {
  id: 'ws-b',
  name: 'Beta',
  slug: 'beta',
  logo_url: null,
  timezone: 'UTC',
  settings: {},
  my_role: 'admin',
  created_at: '2026-08-04T00:00:00Z',
  updated_at: '2026-08-04T00:00:00Z',
};

const NOTIFICATION = {
  id: 'notif-1',
  type: 'mentioned',
  priority: 'normal',
  issue_id: 'issue-1',
  issue: { id: 'issue-1', identifier: 'BET-1', title: 'Scoped issue' },
  comment_id: 'comment-1',
  execution_id: null,
  group_key: 'issue:issue-1:mentioned',
  actor: { id: 'member-other', member_type: 'human', name: 'Other' },
  preview: 'Scoped notification',
  title: 'Mentioned in Beta',
  count: 1,
  read_at: null,
  archived_at: null,
  created_at: '2026-08-04T00:00:00Z',
  latest_comment_id: 'comment-1',
};

interface FetchCall {
  readonly url: string;
  readonly method: string;
}

function LocationProbe(): React.JSX.Element {
  const location = useLocation();
  return <span data-testid="location-probe">{location.pathname + location.hash}</span>;
}

function renderScopedInbox(
  router: (url: string, method: string) => Response | null,
  realtime: RealtimeContextValue | null = null,
): { readonly calls: FetchCall[] } {
  const calls: FetchCall[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({ url, method });
      return (
        router(url, method) ??
        fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'missing' } } })
      );
    }) as unknown as typeof fetch,
  );
  renderWithProviders(
    <RealtimeContext.Provider value={realtime}>
      <WorkspaceProvider slug="beta">
        <Routes>
          <Route
            path="/w/:workspaceSlug/inbox/:notificationId?"
            element={
              <>
                <LocationProbe />
                <InboxPage />
              </>
            }
          />
          <Route path="/w/:workspaceSlug/issues/:issueId" element={<LocationProbe />} />
        </Routes>
      </WorkspaceProvider>
    </RealtimeContext.Provider>,
    { route: '/w/beta/inbox' },
  );
  return { calls };
}

function betaRouter(url: string, method: string): Response | null {
  if (url.includes('/workspaces/by-slug/beta')) return fakeResponse({ body: { data: BETA } });
  if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
  if (url.includes('/workspaces/ws-b/members'))
    return fakeResponse({
      body: {
        data: [
          {
            id: 'member-beta',
            member_type: 'human',
            role: 'admin',
            status: 'active',
            display_name: 'Owner',
            joined_at: null,
            profile: {
              id: 'usr-1',
              full_name: 'Owner',
              email: 'owner@example.test',
              avatar_url: null,
            },
          },
        ],
        next_cursor: null,
      },
    });
  if (url.includes('/notification-preferences'))
    return fakeResponse({ body: { data: [], next_cursor: null } });
  if (url.includes('/api/v1/inbox/notif-1/read') && method === 'POST')
    return fakeResponse({ body: { data: { ...NOTIFICATION, read_at: '2026-08-04T00:01:00Z' } } });
  if (url.includes('/api/v1/inbox') && method === 'GET')
    return fakeResponse({ body: { data: [NOTIFICATION], next_cursor: null } });
  return null;
}

beforeEach(() => useAuthStore.getState().setToken('tok_test'));
afterEach(() => {
  useAuthStore.getState().clearToken();
  vi.unstubAllGlobals();
});

describe('InboxPage canonical workspace scope', () => {
  it('uses the provider workspace/member and keeps detail + issue navigation under its slug', async () => {
    const realtime = {
      state: 'connected',
      client: {
        subscribe: vi.fn(),
        unsubscribe: vi.fn(),
        onFrame: vi.fn(() => () => undefined),
      },
    } as unknown as RealtimeContextValue;
    const { calls } = renderScopedInbox(betaRouter, realtime);

    const row = await screen.findByTestId('inbox-row-notif-1');
    await waitFor(() =>
      expect(realtime.client.subscribe).toHaveBeenCalledWith('member:member-beta:inbox'),
    );
    expect(calls.some((call) => call.url.includes('workspace_id=ws-b'))).toBe(true);
    expect(calls.some((call) => call.url.includes('workspace_id=ws-a'))).toBe(false);

    fireEvent.click(row.querySelector('.mesh-inbox__row-main') as HTMLElement);
    await waitFor(() =>
      expect(screen.getByTestId('location-probe')).toHaveTextContent('/w/beta/inbox/notif-1'),
    );

    fireEvent.click(await screen.findByTestId('inbox-preview-open'));
    await waitFor(() =>
      expect(screen.getByTestId('location-probe')).toHaveTextContent(
        '/w/beta/issues/issue-1#comment-comment-1',
      ),
    );
  });

  it('renders an error state instead of an endless skeleton when membership resolution fails', async () => {
    renderScopedInbox((url) => {
      if (url.includes('/workspaces/by-slug/beta')) return fakeResponse({ body: { data: BETA } });
      if (url.includes('/users/me'))
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'offline' } },
        });
      return null;
    });

    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
    expect(screen.queryByText('Loading…')).not.toBeInTheDocument();
  });
});
