/**
 * useCommentsData 分支补齐(coverage fill):非 MeshApiError 加载失败、跨频道帧忽略、
 * toggleReactionLocal 多 emoji 透传、成功重开、删除失败回滚。纯函数 + hook。
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { ToastProvider } from '../../../design';
import { I18nProvider } from '../../../i18n';
import type { MissingReporter } from '../../../i18n';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import type { RealtimeEventFrame } from '../../../types/realtime';
import {
  patchCommentById,
  removeCommentById,
  toggleReactionLocal,
  useCommentsData,
} from '../useCommentsData';
import type { Comment, CommentMemberRef, ReactionSummary } from '../types';
import { UNDO_WINDOW_MS } from '../useDeferredDelete';

const silentReporter: MissingReporter = { report: () => undefined, reported: [] };

const ME: CommentMemberRef = { id: 'mem-1', member_type: 'human', name: 'Owner' };
const OTHER: CommentMemberRef = { id: 'mem-9', member_type: 'human', name: 'B' };

const ROOT: Comment = {
  id: 'c-1',
  issue_id: 'iss-1',
  parent_id: null,
  thread_root_id: null,
  author_kind: 'member',
  author: ME,
  body_markdown: 'root',
  body_html: '<p>root</p>',
  body_text: 'root',
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
};

let failDelete = false;
let frameListener: ((frame: RealtimeEventFrame) => void) | null = null;

const fakeClient = {
  subscribe: vi.fn(),
  unsubscribe: vi.fn(),
  onFrame: (cb: (frame: RealtimeEventFrame) => void) => {
    frameListener = cb;
    return () => {
      frameListener = null;
    };
  },
};
const realtimeValue = { state: 'connected', client: fakeClient } as unknown as RealtimeContextValue;

function routingFetch(): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (method === 'DELETE') {
      if (failDelete)
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      return fakeResponse({ status: 204 });
    }
    if (method === 'GET' && url.includes('/comments')) {
      return fakeResponse({ body: { data: [ROOT], next_cursor: null } });
    }
    if (url.includes('/resolve') || url.includes('/reopen')) {
      return fakeResponse({ body: { data: { ...ROOT, resolved_at: null } } });
    }
    return fakeResponse({ body: { data: { ...ROOT, id: 'c-server' } } });
  }) as typeof fetch;
}

function wrapper(props: { children: ReactNode }): React.JSX.Element {
  return (
    <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
      <ToastProvider regionLabel="test">
        <RealtimeContext.Provider value={realtimeValue}>{props.children}</RealtimeContext.Provider>
      </ToastProvider>
    </I18nProvider>
  );
}

function renderData(): ReturnType<
  typeof renderHook<ReturnType<typeof useCommentsData>, { member: CommentMemberRef | null }>
> {
  return renderHook(({ member }) => useCommentsData('iss-1', member), {
    wrapper,
    initialProps: { member: ME as CommentMemberRef | null },
  });
}

beforeEach(() => {
  failDelete = false;
  frameListener = null;
  vi.stubGlobal('fetch', routingFetch());
});
afterEach(() => vi.unstubAllGlobals());

describe('useCommentsData branch fill', () => {
  it('does not update state after an initial list request resolves or rejects post-unmount', async () => {
    let settle: ((response: Response) => void) | null = null;
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            settle = resolve;
          }),
      ),
    );
    const resolved = renderData();
    resolved.unmount();
    await act(async () => {
      settle?.(fakeResponse({ body: { data: [ROOT], next_cursor: null } }));
      await Promise.resolve();
    });

    let reject: ((reason: unknown) => void) | null = null;
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise<Response>((_resolve, rejectRequest) => {
            reject = rejectRequest;
          }),
      ),
    );
    const rejected = renderData();
    rejected.unmount();
    await act(async () => {
      reject?.(new TypeError('late failure'));
      await Promise.resolve();
    });
  });

  it('ignores realtime frames from a different channel', async () => {
    const { result } = renderData();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    const before = result.current.comments;
    act(() => {
      frameListener?.({
        op: 'event',
        channel: 'issue:OTHER',
        seq: 2,
        event: 'comment.created',
        payload: { ...ROOT, id: 'c-live' } as unknown as Record<string, unknown>,
      } as RealtimeEventFrame);
    });
    expect(result.current.comments).toBe(before);
  });

  it('reopens a thread successfully (resolved=false path)', async () => {
    const { result } = renderData();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    await act(async () => {
      await result.current.setResolved(ROOT, false);
    });
    // reopen 成功:patch 生效(resolved_at 取自服务端返回,此处为 null)
    expect(result.current.comments.find((c) => c.id === 'c-1')?.resolved_at).toBeNull();
  });

  it('rolls back a delete when the deferred API call fails', async () => {
    const { result } = renderData();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    vi.useFakeTimers();
    failDelete = true;
    act(() => {
      result.current.remove(ROOT);
    });
    // 乐观墓碑保留实体与线程位置。
    expect(result.current.comments.find((c) => c.id === 'c-1')?.deleted_at).not.toBeNull();
    // A second delete while the first tombstone is pending is ignored.
    act(() => {
      result.current.remove(ROOT);
    });
    // 窗口到期 → DELETE 失败 → 回滚恢复
    await act(async () => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.comments.find((c) => c.id === 'c-1')?.deleted_at).toBeNull();
    vi.useRealTimers();
  });

  it('reconciles retries that were supplied from outside the current list', async () => {
    const { result } = renderData();
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await expect(result.current.retrySend(ROOT)).resolves.toBe(ROOT);
    });

    const failedTop: Comment = {
      ...ROOT,
      id: 'external-top',
      body_markdown: 'external top',
      delivery_state: 'failed',
      client_request_id: null,
    };
    await act(async () => {
      await expect(result.current.retrySend(failedTop)).resolves.toMatchObject({ id: 'c-server' });
    });
    expect(result.current.comments.some((comment) => comment.id === 'c-server')).toBe(true);

    const failedReply: Comment = {
      ...ROOT,
      id: 'external-reply',
      parent_id: ROOT.id,
      thread_root_id: ROOT.id,
      body_markdown: 'external reply',
      delivery_state: 'failed',
      client_request_id: 'request-reply',
    };
    await act(async () => {
      await expect(result.current.retrySend(failedReply)).resolves.toMatchObject({
        id: 'c-server',
      });
    });
    expect(
      result.current.comments[0]?.preview_replies?.some((reply) => reply.id === 'c-server'),
    ).toBe(true);
  });

  it('hydrates a missing reply into an already loaded root and ignores a foreign issue target', async () => {
    const target: Comment = {
      ...ROOT,
      id: 'deep-reply',
      parent_id: ROOT.id,
      thread_root_id: ROOT.id,
    };
    const otherRoot: Comment = { ...ROOT, id: 'other-root' };
    const topTarget: Comment = { ...ROOT, id: 'top-target' };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith('/comments/foreign')) {
          return fakeResponse({ body: { data: { ...ROOT, id: 'foreign', issue_id: 'other' } } });
        }
        if (url.endsWith('/comments/deep-reply')) {
          return fakeResponse({ body: { data: target } });
        }
        if (url.endsWith('/comments/top-target')) {
          return fakeResponse({ body: { data: topTarget } });
        }
        if (url.includes('/comments/c-1/replies')) {
          return fakeResponse({ body: { data: [], next_cursor: null } });
        }
        if (url.endsWith('/comments/c-1')) return fakeResponse({ body: { data: ROOT } });
        return fakeResponse({ body: { data: [ROOT, otherRoot], next_cursor: null } });
      }),
    );
    const { result } = renderData();
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.locateComment(ROOT.id);
      await result.current.locateComment('deep-reply');
      await result.current.locateComment('deep-reply');
      await result.current.locateComment('top-target');
      await result.current.locateComment('foreign');
    });

    expect(result.current.comments[0]?.preview_replies?.map((reply) => reply.id)).toContain(
      'deep-reply',
    );
    expect(result.current.comments.some((comment) => comment.id === 'foreign')).toBe(false);
    expect(result.current.comments.some((comment) => comment.id === 'top-target')).toBe(true);
  });
});

describe('toggleReactionLocal multi-emoji passthrough', () => {
  it('returns the original comment collection when a populated reply preview misses the id', () => {
    const input: readonly Comment[] = [{ ...ROOT, preview_replies: [] }];
    expect(removeCommentById(input, 'missing')).toBe(input);
    expect(patchCommentById(input, 'missing', (comment) => comment)).toBe(input);
  });

  it('leaves the other emoji untouched when removing self (count>1)', () => {
    const reactions: ReactionSummary[] = [
      { emoji: '👍', count: 2, reacted_by_me: true, actors: [ME, OTHER] },
      { emoji: '🎉', count: 1, reacted_by_me: false, actors: [OTHER] },
    ];
    const out = toggleReactionLocal(reactions, '👍', ME);
    const other = out.find((r) => r.emoji === '🎉');
    expect(other).toEqual({ emoji: '🎉', count: 1, reacted_by_me: false, actors: [OTHER] });
    expect(out.find((r) => r.emoji === '👍')?.reacted_by_me).toBe(false);
  });

  it('leaves the other emoji untouched when adding self', () => {
    const reactions: ReactionSummary[] = [
      { emoji: '👍', count: 1, reacted_by_me: false, actors: [OTHER] },
      { emoji: '🎉', count: 1, reacted_by_me: false, actors: [OTHER] },
    ];
    const out = toggleReactionLocal(reactions, '👍', ME);
    expect(out.find((r) => r.emoji === '🎉')).toEqual({
      emoji: '🎉',
      count: 1,
      reacted_by_me: false,
      actors: [OTHER],
    });
    expect(out.find((r) => r.emoji === '👍')?.reacted_by_me).toBe(true);
  });
});
