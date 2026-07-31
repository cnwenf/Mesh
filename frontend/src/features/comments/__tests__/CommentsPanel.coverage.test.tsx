/**
 * CommentsPanel 回调与分支补齐(coverage fill):以 currentMember==作者渲染使操作按钮可见,
 * 驱动 复制成功/失败、解决/重开、反应切换/新增、编辑保存、删除、深链初始高亮 + hashchange、
 * 空回复线程、展开(预览缺省 / 拉取失败回退)、回复空作者、顶层与回复两种提交。
 * 全部经路由化 fetch 桩,无真实网络。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { CommentsPanel } from '../CommentsPanel';
import type { Comment } from '../types';

const CURRENT_MEMBER = { id: 'mem-1', member_type: 'human' as const, name: 'Owner' };

interface FetchConfig {
  comments: Comment[];
  replies: Comment[];
  failReplies: boolean;
  failDelete: boolean;
  reactions: unknown[];
  writeResult: Comment;
}

let cfg: FetchConfig;
let calls: { url: string; method: string }[] = [];

function mkComment(id: string, extra: Partial<Comment> = {}): Comment {
  return {
    id,
    issue_id: 'iss-1',
    parent_id: null,
    thread_root_id: null,
    author_kind: 'member',
    author: { id: 'mem-1', member_type: 'human', name: 'Owner' },
    body_markdown: 'body',
    body_html: '<p>body</p>',
    body_text: 'body',
    reactions: [],
    reply_count: 0,
    resolved_at: null,
    resolved_by: null,
    mentions: [],
    triggered_execution_ids: [],
    deleted_at: null,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-02T00:00:00Z',
    edited_at: null,
    ...extra,
  };
}

function routingFetch(): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.includes('/replies')) {
      if (cfg.failReplies) {
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      }
      return fakeResponse({ body: { data: cfg.replies, next_cursor: null } });
    }
    if (method === 'GET') {
      return fakeResponse({ body: { data: cfg.comments, next_cursor: null } });
    }
    if (method === 'DELETE') {
      if (cfg.failDelete) {
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      }
      return fakeResponse({ status: 204 });
    }
    if (url.includes('/reactions')) {
      return fakeResponse({ body: { data: cfg.reactions } });
    }
    return fakeResponse({ body: { data: cfg.writeResult } });
  }) as typeof fetch;
}

function renderPanel(): void {
  renderWithProviders(
    <CommentsPanel
      issueId="iss-1"
      workspaceId="ws-1"
      locale="en"
      candidates={[]}
      currentMember={CURRENT_MEMBER}
    />,
  );
}

function setClipboard(writeText: (text: string) => Promise<void>): void {
  Object.defineProperty(window.navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  });
}

beforeEach(() => {
  calls = [];
  cfg = {
    comments: [mkComment('c-1')],
    replies: [],
    failReplies: false,
    failDelete: false,
    reactions: [],
    writeResult: mkComment('c-server'),
  };
  vi.stubGlobal('fetch', routingFetch());
});

afterEach(() => {
  vi.unstubAllGlobals();
  // @ts-expect-error 清理测试挂载的 clipboard 桩
  delete window.navigator.clipboard;
  // @ts-expect-error 清理 scrollIntoView 桩
  delete window.HTMLElement.prototype.scrollIntoView;
  window.location.hash = '';
});

describe('CommentsPanel copy link', () => {
  it('copies the link and shows a success toast', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard(writeText);
    renderPanel();
    await screen.findByTestId('comment-copy-c-1');
    fireEvent.click(screen.getByTestId('comment-copy-c-1'));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('#comment-c-1'));
    await screen.findByText('Comment link copied.');
  });

  it('shows a failure toast when the clipboard rejects', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'));
    setClipboard(writeText);
    renderPanel();
    await screen.findByTestId('comment-copy-c-1');
    fireEvent.click(screen.getByTestId('comment-copy-c-1'));
    await screen.findByText('Could not copy the link.');
  });
});

describe('CommentsPanel moderation callbacks', () => {
  it('resolves a thread via the resolve button', async () => {
    cfg.writeResult = mkComment('c-1', { resolved_at: '2026-07-03T00:00:00Z' });
    renderPanel();
    await screen.findByTestId('comment-resolve-c-1');
    fireEvent.click(screen.getByTestId('comment-resolve-c-1'));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/resolve'))).toBe(true));
  });

  it('reopens a resolved thread via the reopen button', async () => {
    cfg.comments = [mkComment('c-1', { resolved_at: '2026-07-02T00:00:00Z' })];
    cfg.writeResult = mkComment('c-1', { resolved_at: null });
    renderPanel();
    await screen.findByTestId('comment-reopen-c-1');
    fireEvent.click(screen.getByTestId('comment-reopen-c-1'));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/reopen'))).toBe(true));
  });

  it('toggles an existing reaction chip', async () => {
    cfg.comments = [
      mkComment('c-1', {
        reactions: [{ emoji: '👍', count: 1, reacted_by_me: false, actors: [] }],
      }),
    ];
    renderPanel();
    await screen.findByTestId('reaction-👍');
    fireEvent.click(screen.getByTestId('reaction-👍'));
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'POST' && c.url.includes('/reactions'))).toBe(true),
    );
  });

  it('adds a reaction from the picker', async () => {
    renderPanel();
    await screen.findByTestId('reaction-add');
    fireEvent.click(screen.getByTestId('reaction-add'));
    fireEvent.click(screen.getByTestId('reaction-pick-🎉'));
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'POST' && c.url.includes('/reactions'))).toBe(true),
    );
  });

  it('saves an edit through the card edit form', async () => {
    cfg.writeResult = mkComment('c-1', {
      body_markdown: 'edited',
      edited_at: '2026-07-03T00:00:00Z',
    });
    renderPanel();
    await screen.findByTestId('comment-edit-c-1');
    fireEvent.click(screen.getByTestId('comment-edit-c-1'));
    fireEvent.change(screen.getByTestId('comment-edit-input-c-1'), { target: { value: 'edited' } });
    fireEvent.click(screen.getByTestId('comment-edit-save-c-1'));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
  });

  it('deletes a comment via the delete button (deferred, §9.5.5)', async () => {
    renderPanel();
    await screen.findByTestId('comment-delete-c-1');
    fireEvent.click(screen.getByTestId('comment-delete-c-1'));
    // 乐观隐藏:卡片立即移除;撤销窗口内尚未真正调用 DELETE
    await waitFor(() => expect(screen.queryByTestId('comment-card-c-1')).toBeNull());
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    // 撤销提示 toast 出现(含撤销动作与实际文案)
    expect(screen.getByRole('button', { name: 'Undo' })).toBeTruthy();
    expect(screen.getByRole('status').textContent).toContain('Comment deleted');
  });
});

describe('CommentsPanel deep-link highlight', () => {
  it('highlights the anchored comment on initial load', async () => {
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    window.location.hash = '#comment-c-1';
    renderPanel();
    await screen.findByTestId('comment-card-c-1');
    await waitFor(() =>
      expect(screen.getByTestId('comment-card-c-1').className).toContain(
        'mesh-comments__card--highlight',
      ),
    );
    expect(window.HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it('updates the highlight on hashchange', async () => {
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    renderPanel();
    await screen.findByTestId('comment-card-c-1');
    expect(screen.getByTestId('comment-card-c-1').className).not.toContain('--highlight');
    act(() => {
      window.location.hash = '#comment-c-1';
      window.dispatchEvent(new window.Event('hashchange'));
    });
    await waitFor(() =>
      expect(screen.getByTestId('comment-card-c-1').className).toContain(
        'mesh-comments__card--highlight',
      ),
    );
  });
});

describe('CommentsPanel threads', () => {
  it('omits the toggle for a thread with no replies', async () => {
    cfg.comments = [mkComment('c-1', { reply_count: 0 })];
    renderPanel();
    await screen.findByTestId('comment-card-c-1');
    expect(screen.queryByTestId('thread-toggle-c-1')).toBeNull();
  });

  it('expands a thread whose preview_replies is undefined', async () => {
    cfg.comments = [mkComment('c-1', { reply_count: 1, preview_replies: undefined })];
    cfg.replies = [];
    renderPanel();
    await screen.findByTestId('thread-toggle-c-1');
    fireEvent.click(screen.getByTestId('thread-toggle-c-1'));
    await screen.findByTestId('thread-replies-c-1');
  });

  it('falls back to preview_replies when fetching replies fails', async () => {
    const reply = mkComment('c-2', {
      parent_id: 'c-1',
      thread_root_id: 'c-1',
      author: { id: 'mem-2', member_type: 'human', name: 'Bob' },
    });
    cfg.comments = [mkComment('c-1', { reply_count: 1, preview_replies: [reply] })];
    cfg.failReplies = true;
    renderPanel();
    await screen.findByTestId('thread-toggle-c-1');
    fireEvent.click(screen.getByTestId('thread-toggle-c-1'));
    // 拉取失败退回预览回复,线程仍展开
    await screen.findByTestId('comment-card-c-2');
  });

  it('opens a reply composer with a null replyToName for an author-less comment', async () => {
    cfg.comments = [mkComment('c-1', { author: null })];
    renderPanel();
    await screen.findByTestId('comment-reply-c-1');
    fireEvent.click(screen.getByTestId('comment-reply-c-1'));
    // 回复输入框出现;作者为 null → replyToName 为 null(无 reply-hint)
    await waitFor(() => expect(screen.getAllByTestId('composer-input').length).toBe(2));
    expect(screen.queryByTestId('reply-hint')).toBeNull();
  });
});

describe('CommentsPanel submit', () => {
  it('creates a top-level comment from the bottom composer', async () => {
    renderPanel();
    await screen.findByTestId('composer-input');
    fireEvent.change(screen.getByTestId('composer-input'), {
      target: { value: 'a top-level comment' },
    });
    fireEvent.click(screen.getByTestId('composer-submit'));
    await waitFor(() =>
      expect(
        calls.some((c) => c.method === 'POST' && c.url.includes('/issues/iss-1/comments')),
      ).toBe(true),
    );
  });

  it('creates a reply from the in-thread reply composer', async () => {
    renderPanel();
    await screen.findByTestId('comment-reply-c-1');
    fireEvent.click(screen.getByTestId('comment-reply-c-1'));
    const hint = await screen.findByTestId('reply-hint');
    const composer = hint.closest('[data-testid="comment-composer"]') as HTMLElement;
    const input = composer.querySelector('[data-testid="composer-input"]') as HTMLTextAreaElement;
    const submit = composer.querySelector('[data-testid="composer-submit"]') as HTMLButtonElement;
    fireEvent.change(input, { target: { value: 'a reply body' } });
    fireEvent.click(submit);
    await waitFor(() =>
      expect(
        calls.some((c) => c.method === 'POST' && c.url.includes('/issues/iss-1/comments')),
      ).toBe(true),
    );
  });
});
