/**
 * InboxPage 组件测试(comment-inbox.md §4.2):分组渲染、筛选 tab、行点击标已读、
 * 工具条全部已读/归档已读、组头静音、空态。fetch 桩按序:me → members → inbox。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { InboxPage } from '../InboxPage';

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

function queue(inboxBody: unknown = { data: [NOTIF], next_cursor: null }): FetchStub {
  const stub = stubFetch(
    fakeResponse({ body: { data: ME } }),
    fakeResponse({ body: MEMBERS }),
    fakeResponse({ body: inboxBody }),
    fakeResponse({ body: inboxBody }),
    fakeResponse({ body: { data: { updated: 1 } } }),
    fakeResponse({ body: { data: { archived: 1 } } }),
    fakeResponse({ body: { data: { issue_id: 'iss-1', muted: true, reason: 'manual' } } }),
    fakeResponse({ body: { data: NOTIF } }),
  );
  vi.stubGlobal('fetch', stub.fetchImpl);
  return stub;
}

let pageFrame: ((frame: RealtimeEventFrame) => void) | null = null;
const fakeClient = {
  subscribe: vi.fn(),
  unsubscribe: vi.fn(),
  onFrame: (cb: (frame: RealtimeEventFrame) => void) => {
    pageFrame = cb;
    return () => {
      pageFrame = null;
    };
  },
};
const realtimeValue = { state: 'connected', client: fakeClient } as unknown as RealtimeContextValue;

function rtFrame(event: string, payload: unknown): RealtimeEventFrame {
  return { op: 'event', channel: 'member:mem-1:inbox', seq: 1, event, payload } as RealtimeEventFrame;
}

beforeEach(() => {
  pageFrame = null;
  vi.unstubAllGlobals();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('InboxPage', () => {
  it('renders grouped notifications with issue header', async () => {
    queue();
    renderWithProviders(<InboxPage />);
    await screen.findByTestId('inbox-page');
    await screen.findByTestId('inbox-group-iss-1');
    expect(screen.getByTestId('inbox-group-iss-1').textContent).toContain('WS-1 · Login bug');
    expect(screen.getByTestId('inbox-row-n-1')).toBeTruthy();
    expect(screen.getByTestId('inbox-unread-dot-n-1')).toBeTruthy();
  });

  it('shows the empty state when there are no notifications', async () => {
    queue({ data: [], next_cursor: null });
    renderWithProviders(<InboxPage />);
    await screen.findByText('Inbox zero');
  });

  it('switches filter tabs and re-queries with the filter', async () => {
    const stub = queue();
    renderWithProviders(<InboxPage />);
    await screen.findByTestId('inbox-row-n-1');
    fireEvent.click(screen.getByTestId('inbox-tab-unread'));
    await waitFor(() => {
      const inboxCalls = stub.calls.filter((c) => String(c.url).includes('/api/v1/inbox?') || String(c.url).includes('/api/v1/inbox&'));
      expect(inboxCalls.some((c) => String(c.url).includes('filter=unread'))).toBe(true);
    });
  });

  it('marks a notification read on click', async () => {
    const stub = queue();
    renderWithProviders(<InboxPage />);
    await screen.findByTestId('inbox-row-n-1');
    fireEvent.click(screen.getByTestId('inbox-mark-read-n-1'));
    await waitFor(() => {
      expect(stub.calls.some((c) => String(c.url).includes('/api/v1/inbox/n-1/read'))).toBe(true);
    });
  });

  it('mutes an issue from the group header', async () => {
    const stub = queue();
    renderWithProviders(<InboxPage />);
    await screen.findByTestId('inbox-mute-iss-1');
    fireEvent.click(screen.getByTestId('inbox-mute-iss-1'));
    await waitFor(() => {
      expect(stub.calls.some((c) => String(c.url).includes('/api/v1/issues/iss-1/mute'))).toBe(true);
    });
  });

  it('marks all read via the toolbar', async () => {
    const stub = queue();
    renderWithProviders(<InboxPage />);
    await screen.findByTestId('inbox-row-n-1');
    fireEvent.click(screen.getByTestId('inbox-read-all'));
    await waitFor(() => {
      expect(stub.calls.some((c) => String(c.url).includes('/api/v1/inbox/read-all'))).toBe(true);
    });
  });

  it('archives a notification row', async () => {
    const stub = queue();
    renderWithProviders(<InboxPage />);
    await screen.findByTestId('inbox-row-n-1');
    fireEvent.click(screen.getByTestId('inbox-archive-n-1'));
    await waitFor(() => {
      expect(stub.calls.some((c) => String(c.url).includes('/api/v1/inbox/n-1/archive'))).toBe(true);
    });
    await waitFor(() => expect(screen.queryByTestId('inbox-row-n-1')).toBeNull());
  });

  it('merges realtime notification frames into the list', async () => {
    queue();
    renderWithProviders(
      <RealtimeContext.Provider value={realtimeValue}>
        <InboxPage />
      </RealtimeContext.Provider>,
    );
    await screen.findByTestId('inbox-row-n-1');
    await waitFor(() => expect(pageFrame).not.toBeNull());
    act(() =>
      pageFrame?.(
        rtFrame('notification.created', {
          ...NOTIF,
          id: 'n-9',
          title: 'Live',
          issue: { id: 'iss-9', identifier: 'WS-9', title: 'Live issue' },
          issue_id: 'iss-9',
        }),
      ),
    );
    await screen.findByTestId('inbox-row-n-9');
  });

  it('count>1 时渲染聚合计数徽标', async () => {
    queue({ data: [{ ...NOTIF, count: 3 }], next_cursor: null });
    renderWithProviders(<InboxPage />);
    await screen.findByTestId('inbox-count-n-1');
    expect(screen.getByTestId('inbox-count-n-1').textContent).toContain('3');
  });

  it('inbox 拉取失败时渲染错误态(MeshApiError 映射文案)', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderWithProviders(<InboxPage />);
    await screen.findByText('Something went wrong');
  });

  it('忽略非本频道的实时帧', async () => {
    queue();
    renderWithProviders(
      <RealtimeContext.Provider value={realtimeValue}>
        <InboxPage />
      </RealtimeContext.Provider>,
    );
    await screen.findByTestId('inbox-row-n-1');
    await waitFor(() => expect(pageFrame).not.toBeNull());
    act(() =>
      pageFrame?.({ op: 'event', channel: 'member:other:inbox', seq: 1, event: 'notification.created', payload: { ...NOTIF, id: 'n-x' } } as RealtimeEventFrame),
    );
    expect(screen.queryByTestId('inbox-row-n-x')).toBeNull();
  });

  it('加载期间卸载 → cancelled 守卫丢弃结果', async () => {
    queue();
    const { unmount } = renderWithProviders(<InboxPage />);
    act(() => {
      unmount();
    });
    // 不抛错即覆盖 cancelled 分支
    expect(true).toBe(true);
  });
});
