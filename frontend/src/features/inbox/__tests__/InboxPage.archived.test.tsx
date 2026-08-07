/**
 * InboxPage 归档视图测试(L202,comment-inbox.md §4.4「移出主视图,可回查」):
 * 已归档 tab 以 archived=true 拉取、归档行无归档操作与主视图工具条、
 * 归档空态、URL ?filter=archived 深链、命令面板口径回落 all。
 * fetch 桩按序:me → members → inbox → prefs(后续交互按需追加)。
 */
import { fireEvent, screen } from '@testing-library/react';
import { Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { InboxPage } from '../InboxPage';
import { getCurrentInboxView } from '../currentFilter';
import { useAuthStore } from '../../../state/authStore';

const ME = {
  user: { id: 'usr-1', email: 'o@c.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'WS',
      workspace_slug: 'ws',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};
const MEMBERS = {
  data: [
    {
      id: 'mem-1',
      member_type: 'human',
      role: 'owner',
      status: 'active',
      display_name: 'Owner',
      joined_at: null,
      profile: { id: 'usr-1', full_name: 'Owner', email: 'o@c.com', avatar_url: null },
    },
  ],
  next_cursor: null,
};
const LIVE_NOTIF = {
  id: 'n-1',
  type: 'mentioned',
  priority: 'normal',
  issue_id: 'iss-1',
  comment_id: 'c-1',
  execution_id: null,
  group_key: 'issue:iss-1:mentioned',
  actor: { id: 'mem-2', member_type: 'human', name: 'Alice' },
  preview: 'hey @you',
  title: 'You were mentioned',
  count: 1,
  read_at: null,
  archived_at: null,
  created_at: '2026-07-01T00:00:00Z',
  latest_comment_id: 'c-1',
  issue: { id: 'iss-1', identifier: 'WS-1', title: 'Login bug' },
};
const ARCHIVED_NOTIF = {
  ...LIVE_NOTIF,
  id: 'n-2',
  read_at: '2026-07-02T00:00:00Z',
  archived_at: '2026-07-03T00:00:00Z',
};
const NO_PREFS = { data: [], next_cursor: null };

function queue(
  inboxBody: unknown = { data: [LIVE_NOTIF], next_cursor: null },
  ...extra: Response[]
): FetchStub {
  const stub = stubFetch(
    fakeResponse({ body: { data: ME } }),
    fakeResponse({ body: MEMBERS }),
    fakeResponse({ body: inboxBody }),
    fakeResponse({ body: NO_PREFS }),
    ...extra,
  );
  vi.stubGlobal('fetch', stub.fetchImpl);
  return stub;
}

function renderInbox(route = '/inbox'): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <Routes>
      <Route path="/inbox/:notificationId?" element={<InboxPage />} />
    </Routes>,
    { route },
  );
}

function inboxCalls(stub: FetchStub): string[] {
  return stub.calls
    .map((call) => call.url)
    .filter((url) => url.includes('/api/v1/inbox') && !url.includes('unread-count'));
}

beforeEach(() => {
  useAuthStore.getState().setToken('tok_test');
  vi.unstubAllGlobals();
});
afterEach(() => {
  useAuthStore.getState().clearToken();
  vi.unstubAllGlobals();
});

describe('InboxPage archived view (L202)', () => {
  it('archived tab fetches with archived=true and hides main-view affordances', async () => {
    const stub = queue(undefined, fakeResponse({ body: { data: [ARCHIVED_NOTIF], next_cursor: null } }));
    renderInbox();
    await screen.findByTestId('inbox-row-n-1');

    fireEvent.click(screen.getByTestId('inbox-tab-archived'));

    await screen.findByTestId('inbox-row-n-2');
    // 拉取走 archived=true
    const urls = inboxCalls(stub);
    expect(urls.some((url) => url.includes('archived=true'))).toBe(true);
    // 归档行无「归档」操作(已归档,不再提供)
    expect(screen.queryByTestId('inbox-archive-n-2')).toBeNull();
    // 归档视图不渲染主视图工具条(全部已读 / 归档已读)
    expect(screen.queryByTestId('inbox-read-all')).toBeNull();
    expect(screen.queryByTestId('inbox-archive-read')).toBeNull();
  });

  it('renders the archived empty state when nothing is archived', async () => {
    queue(undefined, fakeResponse({ body: { data: [], next_cursor: null } }));
    renderInbox();
    await screen.findByTestId('inbox-row-n-1');

    fireEvent.click(screen.getByTestId('inbox-tab-archived'));

    expect(await screen.findByTestId('inbox-archived-empty')).toBeTruthy();
  });

  it('honors ?filter=archived deep link and keeps command-palette filter at all', async () => {
    // 深链直达归档视图:首拉即 archived=true(队列第 3 位即归档列表)
    const stub = queue({ data: [ARCHIVED_NOTIF], next_cursor: null });
    renderInbox('/inbox?filter=archived');

    await screen.findByTestId('inbox-row-n-2');
    const urls = inboxCalls(stub);
    expect(urls[0]).toContain('archived=true');
    // 命令面板「标记全部已读」口径:归档视图回落 all,不发非法 filter
    expect(getCurrentInboxView().filter).toBe('all');
  });
});
