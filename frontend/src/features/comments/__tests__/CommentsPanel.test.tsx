/**
 * CommentsPanel 组件测试(comment-inbox.md §4.1):时间线(系统活动行 + 评论卡片)、
 * 线程折叠展开、空态、错误态重试。fetch 桩供 useCommentsData 首屏拉取与展开拉回复。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { CommentsPanel } from '../CommentsPanel';
import type { Comment } from '../types';

const MEMBER_COMMENT: Comment = {
  id: 'c-1',
  issue_id: 'iss-1',
  parent_id: null,
  thread_root_id: null,
  author_kind: 'member',
  author: { id: 'mem-1', member_type: 'human', name: 'Owner' },
  body_markdown: 'top',
  body_html: '<p>top</p>',
  body_text: 'top',
  reactions: [],
  reply_count: 1,
  resolved_at: null,
  resolved_by: null,
  mentions: [],
  triggered_execution_ids: [],
  deleted_at: null,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  edited_at: null,
  preview_replies: [
    {
      id: 'c-2',
      issue_id: 'iss-1',
      parent_id: 'c-1',
      thread_root_id: 'c-1',
      author_kind: 'member',
      author: { id: 'mem-2', member_type: 'human', name: 'Bob' },
      body_markdown: 'reply',
      body_html: '<p>reply</p>',
      body_text: 'reply',
      reactions: [],
      reply_count: 0,
      resolved_at: null,
      resolved_by: null,
      mentions: [],
      triggered_execution_ids: [],
      deleted_at: null,
      created_at: '2026-07-01T01:00:00Z',
      updated_at: '2026-07-01T01:00:00Z',
      edited_at: null,
    },
  ],
};

const SYSTEM_COMMENT: Comment = {
  ...MEMBER_COMMENT,
  id: 'c-sys',
  author_kind: 'system',
  author: null,
  body_text: 'status changed to done',
  reply_count: 0,
  preview_replies: undefined,
};

function queueComments(data: readonly Comment[]): void {
  const stub = stubFetch(
    fakeResponse({ body: { data, next_cursor: null } }),
    fakeResponse({ body: { data: MEMBER_COMMENT.preview_replies, next_cursor: null } }),
  );
  vi.stubGlobal('fetch', stub.fetchImpl);
}

beforeEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

afterEach(() => {
  window.history.replaceState(null, '', window.location.pathname);
  vi.unstubAllGlobals();
});

function renderPanel(): void {
  renderWithProviders(
    <CommentsPanel
      issueId="iss-1"
      workspaceId="ws-1"
      locale="en"
      candidates={[]}
      currentMember={null}
    />,
  );
}

describe('CommentsPanel', () => {
  it('renders member comment cards and system activity rows', async () => {
    queueComments([MEMBER_COMMENT, SYSTEM_COMMENT]);
    renderPanel();
    await screen.findByTestId('comments-timeline');
    expect(screen.getByTestId('comment-card-c-1')).toBeTruthy();
    expect(screen.getByTestId('activity-c-sys').textContent).toContain('status changed to done');
  });

  it('shows the empty state when there are no comments', async () => {
    queueComments([]);
    renderPanel();
    await screen.findByTestId('comments-empty');
  });

  it('folds and expands a thread', async () => {
    queueComments([MEMBER_COMMENT]);
    renderPanel();
    await screen.findByTestId('thread-toggle-c-1');
    expect(screen.getByTestId('thread-toggle-c-1').textContent).toContain('1');
    // reply hidden until expanded
    expect(screen.queryByTestId('comment-card-c-2')).toBeNull();
    fireEvent.click(screen.getByTestId('thread-toggle-c-1'));
    await waitFor(() => expect(screen.getByTestId('comment-card-c-2')).toBeTruthy());
    // 再次点击折叠(已展开分支:set 已含 root.id → delete)
    fireEvent.click(screen.getByTestId('thread-toggle-c-1'));
    await waitFor(() => expect(screen.queryByTestId('comment-card-c-2')).toBeNull());
  });

  it('puts resolved threads in a collapsed section with resolver trace and supports reopen', async () => {
    const resolved: Comment = {
      ...MEMBER_COMMENT,
      resolved_at: '2026-07-02T00:00:00Z',
      resolved_by: { id: 'mem-9', member_type: 'human', name: 'Reviewer' },
    };
    queueComments([resolved]);
    renderPanel();

    const sectionToggle = await screen.findByTestId('resolved-threads-toggle');
    expect(sectionToggle).toHaveAttribute('aria-expanded', 'false');
    expect(sectionToggle.textContent).toContain('1');
    expect(screen.queryByTestId('comment-card-c-1')).toBeNull();

    fireEvent.click(sectionToggle);
    const card = await screen.findByTestId('comment-card-c-1');
    expect(screen.getByTestId('comment-resolved-meta').textContent).toContain('Reviewer');
    expect(card.querySelector('[data-testid="comment-reopen-c-1"]')).not.toBeNull();
  });

  it('currentMember 为作者时评论可修改(显示 edit/delete)', async () => {
    queueComments([MEMBER_COMMENT]);
    renderWithProviders(
      <CommentsPanel
        issueId="iss-1"
        workspaceId="ws-1"
        locale="en"
        candidates={[]}
        currentMember={{ id: 'mem-1', member_type: 'human', name: 'Owner' }}
      />,
    );
    await screen.findByTestId('comment-card-c-1');
    expect(screen.getByTestId('comment-edit-c-1')).toBeTruthy();
    expect(screen.getByTestId('comment-delete-c-1')).toBeTruthy();
  });

  it('opens a reply composer (expanding the thread) when reply is clicked', async () => {
    queueComments([MEMBER_COMMENT]);
    renderPanel();
    await screen.findByTestId('comment-reply-c-1');
    fireEvent.click(screen.getByTestId('comment-reply-c-1'));
    // 回复输入框出现并标明回复对象;线程随之展开
    await screen.findByTestId('reply-hint');
    expect(screen.getByTestId('thread-replies-c-1')).toBeTruthy();
  });

  it('hydrates a deep-linked reply outside the first page and expands its thread', async () => {
    const root: Comment = {
      ...MEMBER_COMMENT,
      id: 'c-root-deep',
      reply_count: 1,
      preview_replies: undefined,
    };
    const reply: Comment = {
      ...MEMBER_COMMENT,
      id: 'c-reply-deep',
      parent_id: root.id,
      thread_root_id: root.id,
      reply_count: 0,
      preview_replies: undefined,
    };
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/comments/c-reply-deep')) {
        return fakeResponse({ body: { data: reply } });
      }
      if (url.endsWith('/comments/c-root-deep')) {
        return fakeResponse({ body: { data: root } });
      }
      if (url.includes('/comments/c-root-deep/replies')) {
        return fakeResponse({ body: { data: [reply], next_cursor: null } });
      }
      return fakeResponse({ body: { data: [MEMBER_COMMENT], next_cursor: null } });
    });
    vi.stubGlobal('fetch', fetchImpl as typeof fetch);
    window.history.replaceState(null, '', `${window.location.pathname}#comment-${reply.id}`);

    renderPanel();

    const card = await screen.findByTestId(`comment-card-${reply.id}`);
    expect(screen.getByTestId(`thread-replies-${root.id}`)).toBeTruthy();
    expect(card.className).toContain('mesh-comments__card--highlight');
    expect(
      fetchImpl.mock.calls.some(([input]) => String(input).endsWith(`/comments/${reply.id}`)),
    ).toBe(true);
  });

  it('shows the error state with retry when the fetch fails', async () => {
    const stub = stubFetch(
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
      fakeResponse({ body: { data: [], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPanel();
    const retry = await screen.findByText('Retry');
    fireEvent.click(retry);
    await screen.findByTestId('comments-timeline');
  });
});
