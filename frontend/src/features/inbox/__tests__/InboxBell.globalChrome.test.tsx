/**
 * InboxBell 全局 chrome 镜像测试(MES-189 L93):铃铛作为未读计数权威持有者,
 * 把计数镜像到 unreadStore(标签页标题前缀消费)并同步 favicon 徽标;
 * 卸载时清零并恢复原始 favicon。fetch 桩按序:me → members → unread-count。
 * (不注 realtime 上下文 → 铃铛走 REST 快照路径,与 InboxBell.test.tsx 一致。)
 */
import { screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import { useAuthStore } from '../../../state/authStore';
import { useUnreadStore } from '../../../state/unreadStore';
import { renderWithProviders } from '../../../test-utils/render';
import { InboxBell } from '../InboxBell';

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

function queueUnread(count: number): void {
  const stub = stubFetch(
    fakeResponse({ body: { data: ME } }),
    fakeResponse({ body: MEMBERS }),
    fakeResponse({ body: { data: { count } } }),
  );
  vi.stubGlobal('fetch', stub.fetchImpl);
}

let faviconLink: HTMLLinkElement;

beforeEach(() => {
  useAuthStore.getState().setToken('tok_test');
  faviconLink = document.createElement('link');
  faviconLink.rel = 'icon';
  faviconLink.type = 'image/svg+xml';
  faviconLink.href = '/favicon.svg';
  document.head.appendChild(faviconLink);
  vi.unstubAllGlobals();
});

afterEach(() => {
  useAuthStore.getState().clearToken();
  useUnreadStore.setState({ count: 0 });
  faviconLink.remove();
  vi.unstubAllGlobals();
});

describe('InboxBell 未读计数全局 chrome 镜像(L93)', () => {
  it('REST 快照到达后镜像到 unreadStore 并覆盖 favicon 徽标', async () => {
    queueUnread(3);
    renderWithProviders(<InboxBell />);
    await screen.findByTestId('inbox-badge');
    expect(useUnreadStore.getState().count).toBe(3);
    expect(faviconLink.href.startsWith('data:image/svg+xml,')).toBe(true);
    expect(decodeURIComponent(faviconLink.href)).toContain('>3</text>');
  });

  it('卸载时清零 store 并恢复原始 favicon(登出不残留)', async () => {
    queueUnread(2);
    const { unmount } = renderWithProviders(<InboxBell />);
    await screen.findByTestId('inbox-badge');
    expect(useUnreadStore.getState().count).toBe(2);
    unmount();
    expect(useUnreadStore.getState().count).toBe(0);
    expect(faviconLink.href.endsWith('/favicon.svg')).toBe(true);
  });
});
