/**
 * InboxBell 组件测试(comment-inbox.md §4.2):未读徽标、下拉最近通知、查看全部。
 * fetch 桩按序:me → members → unread-count → inbox 列表。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { InboxBell } from '../InboxBell';
import { useAuthStore } from '../../../state/authStore';

const ME = {
  user: { id: 'usr-1', email: 'o@c.com', display_name: 'Owner' },
  memberships: [
    { workspace_id: 'ws-1', workspace_name: 'WS', workspace_slug: 'ws', role: 'owner', status: 'active', joined_at: null },
  ],
};
const MEMBERS = {
  data: [
    { id: 'mem-1', member_type: 'human', role: 'owner', status: 'active', display_name: 'Owner', joined_at: null,
      profile: { id: 'usr-1', full_name: 'Owner', email: 'o@c.com', avatar_url: null } },
  ],
  next_cursor: null,
};
const NOTIF = {
  id: 'n-1', type: 'mentioned', priority: 'normal', issue_id: 'iss-1', comment_id: 'c-1',
  execution_id: null, group_key: null, actor: null, preview: 'hey', title: 'Mentioned',
  count: 1, read_at: null, archived_at: null, created_at: '2026-07-01T00:00:00Z', latest_comment_id: 'c-1',
};

function queue(): FetchStub {
  const stub = stubFetch(
    fakeResponse({ body: { data: ME } }),
    fakeResponse({ body: MEMBERS }),
    fakeResponse({ body: { data: { count: 3 } } }),
    fakeResponse({ body: { data: [NOTIF], next_cursor: null } }),
    fakeResponse({ body: { data: NOTIF } }),
  );
  vi.stubGlobal('fetch', stub.fetchImpl);
  return stub;
}

let bellFrame: ((frame: RealtimeEventFrame) => void) | null = null;
const fakeClient = {
  subscribe: vi.fn(),
  unsubscribe: vi.fn(),
  onFrame: (cb: (frame: RealtimeEventFrame) => void) => {
    bellFrame = cb;
    return () => {
      bellFrame = null;
    };
  },
};
const realtimeValue = { state: 'connected', client: fakeClient } as unknown as RealtimeContextValue;

function frame(event: string, payload: unknown): RealtimeEventFrame {
  return { op: 'event', channel: 'member:mem-1:inbox', seq: 1, event, payload } as RealtimeEventFrame;
}

// MES-106 M1:收件箱/上手清单解析为鉴权请求,用例以登录态为前置。
beforeEach(() => {
  useAuthStore.getState().setToken('tok_test');
  bellFrame = null;
  vi.unstubAllGlobals();
});
afterEach(() => {
  useAuthStore.getState().clearToken();
  vi.unstubAllGlobals();
});

describe('InboxBell', () => {
  it('shows the unread badge from the unread-count endpoint', async () => {
    queue();
    renderWithProviders(<InboxBell />);
    await screen.findByTestId('inbox-badge');
    expect(screen.getByTestId('inbox-badge').textContent).toBe('3');
  });

  it('opens the dropdown with latest notifications', async () => {
    queue();
    renderWithProviders(<InboxBell />);
    await screen.findByTestId('inbox-badge');
    fireEvent.click(screen.getByTestId('inbox-bell'));
    await screen.findByTestId('inbox-dropdown');
    await waitFor(() => expect(screen.getByTestId('inbox-bell-item-n-1')).toBeTruthy());
  });

  it('has an accessible label and a view-all link', async () => {
    queue();
    renderWithProviders(<InboxBell />);
    await screen.findByTestId('inbox-badge');
    expect(screen.getByTestId('inbox-bell').getAttribute('aria-label')).toBe('Notifications');
    fireEvent.click(screen.getByTestId('inbox-bell'));
    await screen.findByTestId('inbox-bell-all');
  });

  it('marks a notification read and navigates on click', async () => {
    const stub = queue();
    renderWithProviders(<InboxBell />);
    await screen.findByTestId('inbox-badge');
    fireEvent.click(screen.getByTestId('inbox-bell'));
    await screen.findByTestId('inbox-bell-item-n-1');
    fireEvent.click(screen.getByTestId('inbox-bell-item-n-1'));
    await waitFor(() => {
      expect(stub.calls.some((c) => String(c.url).includes('/api/v1/inbox/n-1/read'))).toBe(true);
    });
  });

  it('updates the badge from realtime frames', async () => {
    queue();
    renderWithProviders(
      <RealtimeContext.Provider value={realtimeValue}>
        <InboxBell />
      </RealtimeContext.Provider>,
    );
    await screen.findByTestId('inbox-badge');
    await waitFor(() => expect(bellFrame).not.toBeNull());
    act(() => bellFrame?.(frame('inbox.unread_count', { count: 9 })));
    expect(screen.getByTestId('inbox-badge').textContent).toBe('9');
    // notification.created 不再改变未读数(后端以 inbox.unread_count 为准),
    // 仅更新下拉预览列表 —— 打开下拉确认新通知前置。
    act(() => bellFrame?.(frame('notification.created', { ...NOTIF, id: 'n-2' })));
    expect(screen.getByTestId('inbox-badge').textContent).toBe('9');
    fireEvent.click(screen.getByTestId('inbox-bell'));
    await waitFor(() => expect(screen.getByTestId('inbox-bell-item-n-2')).toBeInTheDocument());
  });
});
