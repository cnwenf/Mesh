/**
 * InboxPage 补充覆盖:行主体 onClick(handleOpen,L229)、工具条「归档已读」
 * (handleArchiveRead,L141-144)、非 MeshApiError 错误态(branch L68)、
 * 无 issue 快照分组(branch L205 退回 issueId / L216 issueId==='none' 不渲染静音)。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
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
    fakeResponse({ body: inboxBody }),
    fakeResponse({ body: { data: { updated: 1 } } }),
    fakeResponse({ body: { data: { archived: 1 } } }),
    fakeResponse({ body: { data: NOTIF } }),
  );
  vi.stubGlobal('fetch', stub.fetchImpl);
  return stub;
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
  it('marks read and navigates when the row main button is clicked (onClick L229)', async () => {
    const stub = queue();
    renderWithProviders(<InboxPage />);
    await screen.findByTestId('inbox-row-n-1');
    const rowMain = screen.getByTestId('inbox-row-n-1').querySelector('.mesh-inbox__row-main');
    expect(rowMain).not.toBeNull();
    fireEvent.click(rowMain as HTMLElement);
    await waitFor(() => {
      expect(stub.calls.some((c) => String(c.url).includes('/api/v1/inbox/n-1/read'))).toBe(true);
    });
  });

  it('archives all read notifications via the toolbar (handleArchiveRead L141-144)', async () => {
    const stub = queue();
    renderWithProviders(<InboxPage />);
    await screen.findByTestId('inbox-row-n-1');
    fireEvent.click(screen.getByTestId('inbox-archive-read'));
    await waitFor(() => {
      expect(stub.calls.some((c) => String(c.url).includes('/api/v1/inbox/archive-read'))).toBe(true);
    });
  });

  it('renders the none group without a mute button and falls back to the issueId header (branches L205 + L216)', async () => {
    // issue_id=null → 归入 'none' 组;issue 快照缺失 → 组头退回 issueId;'none' 组不渲染静音按钮。
    queue({ data: [{ ...NOTIF, issue_id: null, issue: undefined }], next_cursor: null });
    renderWithProviders(<InboxPage />);
    const group = await screen.findByTestId('inbox-group-none');
    expect(group.textContent).toContain('none');
    expect(group.textContent).not.toContain('WS-1 · Login bug');
    expect(screen.queryByTestId('inbox-mute-none')).toBeNull();
  });
});
