/**
 * InboxPage × 内联审批接线测试(L206):review_requested 行携带 approval_id 时,
 * 行操作区渲染 InboxApprovalActions(pending → 批准/拒绝按钮);
 * 无 approval_id 的通知不渲染内联审批入口。fetch 桩按 URL 路由。
 */
import { screen } from '@testing-library/react';
import { Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { InboxPage } from '../InboxPage';
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
const REVIEW_NOTIF = {
  id: 'n-review',
  type: 'review_requested',
  priority: 'critical',
  issue_id: 'iss-1',
  comment_id: null,
  execution_id: 'exec-1',
  group_key: 'issue:iss-1:review',
  actor: { id: 'mem-2', member_type: 'agent', name: 'Runner' },
  preview: 'needs approval',
  title: 'Approval requested',
  count: 1,
  read_at: null,
  archived_at: null,
  created_at: '2026-08-07T00:00:00Z',
  latest_comment_id: null,
  issue: { id: 'iss-1', identifier: 'WS-1', title: 'Login bug' },
  approval_id: 'ap-1',
};
const PENDING_APPROVAL = {
  id: 'ap-1',
  subject_type: 'tool_call',
  subject_execution_id: 'exec-1',
  subject_task_id: null,
  status: 'pending',
  action_summary: { action: 'shell.execute' },
  requested_at: '2026-08-07T00:00:00Z',
  expires_at: '2099-01-01T00:00:00Z',
  decided_at: null,
  decision_comment: null,
  execution_status: 'awaiting_approval',
};

function routeFetch(
  overrides: { readonly notification?: unknown; readonly approval?: unknown } = {},
): void {
  const notification = overrides.notification ?? REVIEW_NOTIF;
  const approval = overrides.approval ?? PENDING_APPROVAL;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/notification-preferences')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      if (url.includes('/approvals/ap-1')) {
        return fakeResponse({ body: { data: approval } });
      }
      if (url.includes('/inbox')) {
        return fakeResponse({ body: { data: [notification], next_cursor: null } });
      }
      if (url.includes('/members')) {
        return fakeResponse({ body: MEMBERS });
      }
      return fakeResponse({ body: { data: ME } });
    }),
  );
}

function renderInbox(): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <Routes>
      <Route path="/inbox/:notificationId?" element={<InboxPage />} />
    </Routes>,
    { route: '/inbox' },
  );
}

beforeEach(() => {
  useAuthStore.getState().setToken('tok_test');
  vi.unstubAllGlobals();
});

afterEach(() => {
  useAuthStore.getState().clearToken();
  vi.unstubAllGlobals();
});

describe('InboxPage 内联审批接线(L206)', () => {
  it('review_requested 行携带 approval_id 且审批 pending → 行内出现批准/拒绝', async () => {
    routeFetch();
    renderInbox();

    expect(await screen.findByTestId('inbox-row-n-review')).toBeTruthy();
    expect(await screen.findByTestId('inbox-approval-approve-ap-1')).toBeTruthy();
    expect(screen.getByTestId('inbox-approval-reject-ap-1')).toBeTruthy();
  });

  it('无 approval_id 的通知不渲染内联审批入口', async () => {
    routeFetch({ notification: { ...REVIEW_NOTIF, approval_id: null } });
    renderInbox();

    expect(await screen.findByTestId('inbox-row-n-review')).toBeTruthy();
    expect(screen.queryByTestId('inbox-approval-approve-ap-1')).toBeNull();
    expect(screen.queryByTestId('inbox-approval-reject-ap-1')).toBeNull();
  });
});
