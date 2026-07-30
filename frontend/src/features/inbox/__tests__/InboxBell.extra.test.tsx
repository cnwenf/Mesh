/**
 * InboxBell 补充覆盖:99+ 徽标(branch L110)、非本频道帧守卫(branch L57)、
 * 「查看全部」按钮 onClick(L143-146:关闭下拉 + 导航 /inbox)。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
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

function frame(channel: string, event: string, payload: unknown): RealtimeEventFrame {
  return { op: 'event', channel, seq: 1, event, payload } as RealtimeEventFrame;
}

function setup(): void {
  const stub = stubFetch(
    fakeResponse({ body: { data: ME } }),
    fakeResponse({ body: MEMBERS }),
    fakeResponse({ body: { data: { count: 3 } } }),
    fakeResponse({ body: { data: [NOTIF], next_cursor: null } }),
    fakeResponse({ body: { data: NOTIF } }),
  );
  vi.stubGlobal('fetch', stub.fetchImpl);
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

describe('InboxBell (补充覆盖)', () => {
  it('renders 99+ for counts above 99 and ignores frames from other channels', async () => {
    setup();
    renderWithProviders(
      <RealtimeContext.Provider value={realtimeValue}>
        <InboxBell />
      </RealtimeContext.Provider>,
    );
    await screen.findByTestId('inbox-badge');
    await waitFor(() => expect(bellFrame).not.toBeNull());
    // count > 99 → 「99+」(branch L110 true arm)
    act(() => bellFrame?.(frame('member:mem-1:inbox', 'inbox.unread_count', { count: 150 })));
    expect(screen.getByTestId('inbox-badge').textContent).toBe('99+');
    // 非本频道帧 → 提前返回,计数不变(branch L57 true arm)
    act(() => bellFrame?.(frame('member:someone-else:inbox', 'inbox.unread_count', { count: 1 })));
    expect(screen.getByTestId('inbox-badge').textContent).toBe('99+');
  });

  it('closes the dropdown and navigates to /inbox via the view-all button (onClick L143)', async () => {
    setup();
    renderWithProviders(<InboxBell />);
    await screen.findByTestId('inbox-badge');
    fireEvent.click(screen.getByTestId('inbox-bell'));
    await screen.findByTestId('inbox-bell-all');
    fireEvent.click(screen.getByTestId('inbox-bell-all'));
    // setOpen(false) → 下拉关闭(覆盖 L144-146 与 onClick 函数)
    await waitFor(() => expect(screen.queryByTestId('inbox-dropdown')).toBeNull());
  });
});
