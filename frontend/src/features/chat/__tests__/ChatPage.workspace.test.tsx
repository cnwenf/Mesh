import { fireEvent, screen, waitFor } from '@testing-library/react';
import { useLocation } from 'react-router';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import { WorkspaceProvider } from '../../../workspace/WorkspaceProvider';
import { ChatPage } from '../ChatPage';

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

const AGENT = { id: 'agent-beta', display_name: 'Beta Builder', name: 'beta-builder' };

const CREATED_SESSION = {
  id: 'session-new',
  workspace_id: 'ws-b',
  owner_id: 'member-beta',
  agent_id: 'agent-beta',
  agent: { id: 'agent-beta', name: 'Beta Builder', avatar_url: null },
  title: 'New conversation',
  title_is_auto: true,
  context_issue_id: null,
  context_project_id: null,
  status: 'active',
  pinned: false,
  last_message_at: null,
  last_message_preview: null,
  message_count: 0,
  created_at: '2026-08-04T00:00:00Z',
  updated_at: '2026-08-04T00:00:00Z',
};

function LocationProbe(): React.JSX.Element {
  const location = useLocation();
  return <span data-testid="location-probe">{location.pathname}</span>;
}

afterEach(() => vi.unstubAllGlobals());

describe('ChatPage canonical workspace scope', () => {
  it('uses the second/provider workspace, subscribes with an empty list, and keeps new detail canonical', async () => {
    const calls: Array<{ url: string; method: string }> = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? 'GET';
        calls.push({ url, method });
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
        if (url.includes('/workspaces/ws-b/agents'))
          return fakeResponse({ body: { data: [AGENT], next_cursor: null } });
        if (url.includes('/favorites') && method === 'GET')
          return fakeResponse({ body: { data: [], next_cursor: null } });
        if (url.includes('/workspaces/ws-b/chat-sessions') && method === 'POST')
          return fakeResponse({ status: 201, body: { data: CREATED_SESSION } });
        if (url.includes('/workspaces/ws-b/chat-sessions/session-new/messages'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        if (url.includes('/workspaces/ws-b/chat-sessions') && method === 'GET')
          return fakeResponse({ body: { data: [], next_cursor: null } });
        if (url.includes('/projects') || url.includes('/issues'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({
          status: 404,
          body: { error: { code: 'not_found', message: 'missing' } },
        });
      }) as unknown as typeof fetch,
    );

    const realtime = {
      state: 'connected',
      client: {
        subscribe: vi.fn(),
        unsubscribe: vi.fn(),
        onFrame: vi.fn(() => () => undefined),
      },
    } as unknown as RealtimeContextValue;
    renderWithProviders(
      <RealtimeContext.Provider value={realtime}>
        <WorkspaceProvider slug="beta">
          <Routes>
            <Route
              path="/w/:workspaceSlug/chat/:sessionId?"
              element={
                <>
                  <LocationProbe />
                  <ChatPage />
                </>
              }
            />
          </Routes>
        </WorkspaceProvider>
      </RealtimeContext.Provider>,
      { route: '/w/beta/chat' },
    );

    await screen.findByTestId('chat-new-session');
    await waitFor(() =>
      expect(realtime.client.subscribe).toHaveBeenCalledWith('chat_list:member-beta'),
    );
    expect(calls.some((call) => call.url.includes('/workspaces/ws-b/chat-sessions'))).toBe(true);
    expect(calls.some((call) => call.url.includes('/workspaces/ws-a/'))).toBe(false);

    fireEvent.click(screen.getByTestId('chat-new-session'));
    fireEvent.change(await screen.findByTestId('chat-new-session-agent'), {
      target: { value: 'agent-beta' },
    });
    fireEvent.click(screen.getByTestId('chat-new-session-create'));
    await waitFor(() =>
      expect(screen.getByTestId('location-probe')).toHaveTextContent('/w/beta/chat/session-new'),
    );
  });
});
