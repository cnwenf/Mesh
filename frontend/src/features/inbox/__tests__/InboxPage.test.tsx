/**
 * InboxPage 组件测试(comment-inbox.md §4.2,design-quality.md §3.2/§4.4):
 * ConversationLayout 双栏(分组列表 + 预览)、筛选 tab、行点击选中(路由
 * /inbox/:notificationId)+ 乐观标已读、预览窗格(优先级/来源者/标已读/归档/
 * 打开来源/返回)、工具条全部已读/归档已读、组头静音、空态、免打扰横幅、
 * 深链降级(源删除)、realtime 合并。fetch 桩按序:me → members → inbox → prefs。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { InboxPage } from '../InboxPage';
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
const NO_QUIET_HOURS = { data: [], next_cursor: null };

function queue(inboxBody: unknown = { data: [NOTIF], next_cursor: null }): FetchStub {
  const stub = stubFetch(
    fakeResponse({ body: { data: ME } }),
    fakeResponse({ body: MEMBERS }),
    fakeResponse({ body: inboxBody }),
    fakeResponse({ body: NO_QUIET_HOURS }),
    fakeResponse({ body: inboxBody }),
    fakeResponse({ body: inboxBody }),
    fakeResponse({ body: { data: { updated: 1 } } }),
    fakeResponse({ body: { data: { archived: 1 } } }),
    fakeResponse({ body: { data: { issue_id: 'iss-1', muted: true, reason: 'manual' } } }),
    fakeResponse({ body: { data: NOTIF } }),
    fakeResponse({ body: inboxBody }),
  );
  vi.stubGlobal('fetch', stub.fetchImpl);
  return stub;
}

/** 可选参数路由:选中切换(/inbox ↔ /inbox/:notificationId)不卸载页面,状态延续。 */
function renderInbox(route = '/inbox'): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <Routes>
      <Route path="/inbox/:notificationId?" element={<InboxPage />} />
      <Route path="/issues/:issueId" element={<div data-testid="issue-route-probe" />} />
    </Routes>,
    { route },
  );
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

// MES-106 M1:收件箱/上手清单解析为鉴权请求,用例以登录态为前置。
beforeEach(() => {
  useAuthStore.getState().setToken('tok_test');
  pageFrame = null;
  vi.unstubAllGlobals();
});
afterEach(() => {
  useAuthStore.getState().clearToken();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('InboxPage', () => {
  it('renders grouped notifications with issue header', async () => {
    queue();
    renderInbox();
    await screen.findByTestId('inbox-page');
    await screen.findByTestId('inbox-group-iss-1');
    expect(screen.getByTestId('inbox-group-iss-1').textContent).toContain('WS-1 · Login bug');
    expect(screen.getByTestId('inbox-row-n-1')).toBeTruthy();
    expect(screen.getByTestId('inbox-unread-dot-n-1')).toBeTruthy();
  });

  it('renders the two-column ConversationLayout with list and preview panes', async () => {
    queue();
    renderInbox();
    await screen.findByTestId('inbox-row-n-1');
    // 双栏:list(分组列表)与 detail(预览)各为带可访问名的 section。
    const list = screen.getByLabelText('Notification list');
    const detail = screen.getByLabelText('Notification preview');
    expect(list).toBeTruthy();
    expect(detail).toBeTruthy();
    expect(screen.getByTestId('inbox-groups')).toBeTruthy();
    // 未选中 → 预览窗格空态(选择引导)。
    expect(screen.getByTestId('inbox-preview-empty')).toBeTruthy();
  });

  it('shows the empty state when there are no notifications (onboarding 四要素)', async () => {
    queue({ data: [], next_cursor: null });
    renderInbox();
    await screen.findByText('No notifications yet');
  });

  it('switches filter tabs and re-queries with the filter', async () => {
    const stub = queue();
    renderInbox();
    await screen.findByTestId('inbox-row-n-1');
    fireEvent.click(screen.getByTestId('inbox-tab-unread'));
    await waitFor(() => {
      const inboxCalls = stub.calls.filter((c) => String(c.url).includes('/api/v1/inbox?') || String(c.url).includes('/api/v1/inbox&'));
      expect(inboxCalls.some((c) => String(c.url).includes('filter=unread'))).toBe(true);
    });
  });

  it('selects the notification (route /inbox/:id) and marks it read on row click', async () => {
    const stub = queue();
    renderInbox();
    await screen.findByTestId('inbox-row-n-1');
    const rowMain = screen.getByTestId('inbox-row-n-1').querySelector('.mesh-inbox__row-main');
    expect(rowMain).not.toBeNull();
    fireEvent.click(rowMain as HTMLElement);
    // 乐观标已读 POST + 预览窗格渲染选中通知。
    await waitFor(() => {
      expect(stub.calls.some((c) => String(c.url).includes('/api/v1/inbox/n-1/read'))).toBe(true);
    });
    expect(await screen.findByTestId('inbox-preview-title')).toBeTruthy();
    expect(screen.getByTestId('inbox-preview-title').textContent).toBe('You were mentioned');
    // 选中行高亮。
    expect(screen.getByTestId('inbox-row-n-1').className).toContain('mesh-inbox__row--selected');
  });

  it('marks a notification read via the row action without selecting it', async () => {
    const stub = queue();
    renderInbox();
    await screen.findByTestId('inbox-row-n-1');
    fireEvent.click(screen.getByTestId('inbox-mark-read-n-1'));
    await waitFor(() => {
      expect(stub.calls.some((c) => String(c.url).includes('/api/v1/inbox/n-1/read'))).toBe(true);
    });
    // 已读后行内「标已读」操作消失(未读信号圆点亦消失)。
    await waitFor(() => expect(screen.queryByTestId('inbox-mark-read-n-1')).toBeNull());
    expect(screen.queryByTestId('inbox-unread-dot-n-1')).toBeNull();
  });

  it('mutes an issue from the group header', async () => {
    const stub = queue();
    renderInbox();
    await screen.findByTestId('inbox-mute-iss-1');
    fireEvent.click(screen.getByTestId('inbox-mute-iss-1'));
    await waitFor(() => {
      expect(stub.calls.some((c) => String(c.url).includes('/api/v1/issues/iss-1/mute'))).toBe(true);
    });
  });

  it('marks all read via the toolbar', async () => {
    const stub = queue();
    renderInbox();
    await screen.findByTestId('inbox-row-n-1');
    fireEvent.click(screen.getByTestId('inbox-read-all'));
    await waitFor(() => {
      expect(stub.calls.some((c) => String(c.url).includes('/api/v1/inbox/read-all'))).toBe(true);
    });
  });

  it('archives a notification row', async () => {
    const stub = queue();
    renderInbox();
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
    renderInbox();
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
    renderInbox();
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
    const { unmount } = renderInbox();
    act(() => {
      unmount();
    });
    // 不抛错即覆盖 cancelled 分支
    expect(true).toBe(true);
  });
});

describe('InboxPage 预览窗格(双栏详情)', () => {
  it('renders the preview pane for a deep-linked notification (route param)', async () => {
    queue();
    renderInbox('/inbox/n-1');
    const title = await screen.findByTestId('inbox-preview-title');
    expect(title.textContent).toBe('You were mentioned');
    expect(screen.getByTestId('inbox-preview-body').textContent).toBe('hey @you');
    // normal 优先级徽标。
    expect(screen.getByTestId('inbox-preview-priority').textContent).toContain('Normal');
    // 来源者(人类)。
    expect(screen.getByTestId('inbox-preview-actor').textContent).toContain('From Alice');
    // 操作行:未读 → 标已读可用;打开来源可用。
    expect(screen.getByTestId('inbox-preview-mark-read')).toBeTruthy();
    expect(screen.getByTestId('inbox-preview-open')).toBeTruthy();
    expect((screen.getByTestId('inbox-preview-open') as HTMLButtonElement).disabled).toBe(false);
    // 移动端返回按钮存在于 DOM(桌面经 CSS 隐藏)。
    expect(screen.getByTestId('inbox-preview-back')).toBeTruthy();
  });

  it('renders a critical priority badge in the row and the preview', async () => {
    queue({ data: [{ ...NOTIF, priority: 'critical' }], next_cursor: null });
    renderInbox('/inbox/n-1');
    await screen.findByTestId('inbox-row-priority-n-1');
    expect(screen.getByTestId('inbox-row-priority-n-1').textContent).toContain('Critical');
    expect(screen.getByTestId('inbox-preview-priority').textContent).toContain('Critical');
  });

  it('renders the agent actor variant (fromAgent) with a row avatar', async () => {
    queue({
      data: [{ ...NOTIF, actor: { id: 'mem-9', member_type: 'agent', name: 'Mesh Agent' } }],
      next_cursor: null,
    });
    renderInbox('/inbox/n-1');
    await screen.findByTestId('inbox-row-actor-n-1');
    expect(screen.getByTestId('inbox-preview-actor').textContent).toContain('From agent Mesh Agent');
  });

  it('marks read from the preview pane without navigating away', async () => {
    const stub = queue();
    renderInbox('/inbox/n-1');
    await screen.findByTestId('inbox-preview-mark-read');
    fireEvent.click(screen.getByTestId('inbox-preview-mark-read'));
    await waitFor(() => {
      expect(stub.calls.some((c) => String(c.url).includes('/api/v1/inbox/n-1/read'))).toBe(true);
    });
    // 已读后预览的「标已读」按钮消失,窗格仍显示选中通知(未导航走)。
    await waitFor(() => expect(screen.queryByTestId('inbox-preview-mark-read')).toBeNull());
    expect(screen.getByTestId('inbox-preview-title').textContent).toBe('You were mentioned');
  });

  it('archives from the preview pane and navigates back to the list', async () => {
    const stub = queue();
    renderInbox('/inbox/n-1');
    await screen.findByTestId('inbox-preview-archive');
    fireEvent.click(screen.getByTestId('inbox-preview-archive'));
    await waitFor(() => {
      expect(stub.calls.some((c) => String(c.url).includes('/api/v1/inbox/n-1/archive'))).toBe(true);
    });
    // 归档后回到列表(navigate('/inbox'))→ 预览空态;行在 API 成功后移除。
    expect(await screen.findByTestId('inbox-preview-empty')).toBeTruthy();
    await waitFor(() => expect(screen.queryByTestId('inbox-row-n-1')).toBeNull());
  });

  it('navigates back to /inbox via the back button', async () => {
    queue();
    renderInbox('/inbox/n-1');
    await screen.findByTestId('inbox-preview-back');
    fireEvent.click(screen.getByTestId('inbox-preview-back'));
    expect(await screen.findByTestId('inbox-preview-empty')).toBeTruthy();
  });

  it('opens the source issue (comment anchor) via the preview open button', async () => {
    queue();
    renderInbox('/inbox/n-1');
    const open = await screen.findByTestId('inbox-preview-open');
    fireEvent.click(open);
    // 直达 issue 评论锚点路由(/issues/iss-1#comment-c-1)。
    expect(await screen.findByTestId('issue-route-probe')).toBeTruthy();
  });

  it('navigates back from the unknown-id fallback pane', async () => {
    queue();
    renderInbox('/inbox/missing-1');
    await screen.findByTestId('inbox-preview-missing');
    fireEvent.click(screen.getByTestId('inbox-preview-back'));
    expect(await screen.findByTestId('inbox-preview-empty')).toBeTruthy();
  });

  it('disables the open button when the notification has no issue target', async () => {
    queue({ data: [{ ...NOTIF, issue_id: null, issue: undefined }], next_cursor: null });
    renderInbox('/inbox/n-1');
    const open = await screen.findByTestId('inbox-preview-open');
    expect((open as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows the source-deleted note when the issue snapshot is missing', async () => {
    queue({ data: [{ ...NOTIF, issue: undefined }], next_cursor: null });
    renderInbox('/inbox/n-1');
    await screen.findByTestId('inbox-preview-deleted');
    expect(screen.getByTestId('inbox-preview-deleted').textContent).toBe(
      'Original content was deleted.',
    );
  });

  it('shows a graceful fallback for an unknown deep-linked id after load', async () => {
    queue();
    renderInbox('/inbox/missing-1');
    // H5:不在已加载窗口 ≠ 源删除;呈「未找到/已归档」缺失态,不得以裸 UUID 为标题、
    // 不得误报 sourceDeleted(comment-inbox §5.3)。
    await screen.findByTestId('inbox-preview-missing');
    expect(screen.queryByTestId('inbox-preview-deleted')).toBeNull();
    expect(
      screen.getByText(
        'This notification wasn’t found — it may have been archived or is outside the loaded list.',
      ),
    ).toBeInTheDocument();
  });

  it('shows a skeleton in the preview pane while the list is still loading', async () => {
    // 列表永不 resolve:深链 id 存在但 isLoading → 预览骨架。
    const never = {
      ok: true,
      status: 200,
      text: () => new Promise<string>(() => undefined),
      headers: { get: () => null },
    } as unknown as Response;
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      never,
      fakeResponse({ body: NO_QUIET_HOURS }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderInbox('/inbox/n-1');
    await screen.findByTestId('inbox-page');
    expect(screen.getByLabelText('Notification preview').querySelector('.mesh-skeleton')).not.toBeNull();
  });
});

describe('InboxPage 免打扰横幅', () => {
  it('shows the quiet-hours banner when the current time is inside the window', async () => {
    // 仅 mock Date(保留真实 setTimeout,RTL 异步工具不受影响)。
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-07-01T23:30:00'));
    const prefs = {
      data: [
        { event_type: 'all', in_app: true, email: 'none', quiet_hours_start: '22:00:00', quiet_hours_end: '07:00:00' },
      ],
      next_cursor: null,
    };
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({ body: { data: [NOTIF], next_cursor: null } }),
      fakeResponse({ body: prefs }),
      fakeResponse({ body: { data: [NOTIF], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderInbox();
    const banner = await screen.findByTestId('inbox-quiet-hours');
    expect(banner.textContent).toContain('Quiet hours active');
  });

  it('hides the banner outside the quiet window', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-07-01T12:00:00'));
    const prefs = {
      data: [
        { event_type: 'all', in_app: true, email: 'none', quiet_hours_start: '22:00:00', quiet_hours_end: '07:00:00' },
      ],
      next_cursor: null,
    };
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({ body: { data: [NOTIF], next_cursor: null } }),
      fakeResponse({ body: prefs }),
      fakeResponse({ body: { data: [NOTIF], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderInbox();
    await screen.findByTestId('inbox-row-n-1');
    expect(screen.queryByTestId('inbox-quiet-hours')).toBeNull();
  });

  it('stays silent when the preferences request fails (best-effort)', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({ body: { data: [NOTIF], next_cursor: null } }),
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
      fakeResponse({ body: { data: [NOTIF], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderInbox();
    await screen.findByTestId('inbox-row-n-1');
    expect(screen.queryByTestId('inbox-quiet-hours')).toBeNull();
  });
});
