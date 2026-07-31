/**
 * InboxPage 补充覆盖:工具条「归档已读」(handleArchiveRead)、无 issue 快照分组
 * (组头退回 issueId / 'none' 组不渲染静音)、已读行无圆点(仅归档/已读态分支)。
 * fetch 桩按序:me → members → inbox → prefs。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
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
  id: 'n-1', type: 'mentioned', priority: 'normal', issue_id: 'iss-1', comment_id: 'c-1',
  execution_id: null, group_key: 'issue:iss-1:mentioned',
  actor: { id: 'mem-2', member_type: 'human', name: 'Alice' }, preview: 'hey @you', title: 'You were mentioned',
  count: 1, read_at: null, archived_at: null, created_at: '2026-07-01T00:00:00Z', latest_comment_id: 'c-1',
  issue: { id: 'iss-1', identifier: 'WS-1', title: 'Login bug' },
};

function queue(inboxBody: unknown = { data: [NOTIF], next_cursor: null }): FetchStub {
  const stub = stubFetch(
    fakeResponse({ body: { data: ME } }),
    fakeResponse({ body: MEMBERS }),
    fakeResponse({ body: inboxBody }),
    fakeResponse({ body: { data: [], next_cursor: null } }),
    fakeResponse({ body: inboxBody }),
    fakeResponse({ body: inboxBody }),
    fakeResponse({ body: { data: { archived: 1 } } }),
    fakeResponse({ body: { data: NOTIF } }),
    fakeResponse({ body: inboxBody }),
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

// MES-106 M1:收件箱/上手清单解析为鉴权请求,用例以登录态为前置。
beforeEach(() => {
  vi.unstubAllGlobals();
  useAuthStore.getState().setToken('tok_test');
});
afterEach(() => {
  useAuthStore.getState().clearToken();
  vi.unstubAllGlobals();
});

describe('InboxPage (补充覆盖)', () => {
  it('archives all read notifications via the toolbar (handleArchiveRead)', async () => {
    const stub = queue();
    renderInbox();
    await screen.findByTestId('inbox-row-n-1');
    fireEvent.click(screen.getByTestId('inbox-archive-read'));
    await waitFor(() => {
      expect(stub.calls.some((c) => String(c.url).includes('/api/v1/inbox/archive-read'))).toBe(true);
    });
  });

  it('renders the none group without a mute button and falls back to the issueId header', async () => {
    // issue_id=null → 归入 'none' 组;issue 快照缺失 → 组头退回 issueId;'none' 组不渲染静音按钮。
    queue({ data: [{ ...NOTIF, issue_id: null, issue: undefined }], next_cursor: null });
    renderInbox();
    const group = await screen.findByTestId('inbox-group-none');
    expect(group.textContent).toContain('none');
    expect(group.textContent).not.toContain('WS-1 · Login bug');
    expect(screen.queryByTestId('inbox-mute-none')).toBeNull();
  });

  it('renders a read notification without the unread dot or mark-read action', async () => {
    queue({ data: [{ ...NOTIF, read_at: '2026-07-02T00:00:00Z' }], next_cursor: null });
    renderInbox();
    await screen.findByTestId('inbox-row-n-1');
    expect(screen.queryByTestId('inbox-unread-dot-n-1')).toBeNull();
    expect(screen.queryByTestId('inbox-mark-read-n-1')).toBeNull();
    // 归档操作仍在。
    expect(screen.getByTestId('inbox-archive-n-1')).toBeTruthy();
    // 已读行不带 --unread 修饰类。
    expect(screen.getByTestId('inbox-row-n-1').className).not.toContain('mesh-inbox__row--unread');
  });

  it('does not re-POST read when selecting an already-read notification', async () => {
    const stub = queue({ data: [{ ...NOTIF, read_at: '2026-07-02T00:00:00Z' }], next_cursor: null });
    renderInbox();
    await screen.findByTestId('inbox-row-n-1');
    const rowMain = screen.getByTestId('inbox-row-n-1').querySelector('.mesh-inbox__row-main');
    fireEvent.click(rowMain as HTMLElement);
    // 选中路由生效(预览窗格出现),但不发标已读请求。
    expect(await screen.findByTestId('inbox-preview-title')).toBeTruthy();
    expect(stub.calls.some((c) => String(c.url).includes('/api/v1/inbox/n-1/read'))).toBe(false);
  });
});
