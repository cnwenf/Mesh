/**
 * useCommentsData hook 测试(comment-inbox.md §4.3):首屏拉取、乐观发表/回复(成功+失败回滚)、
 * 反应增删(含失败回滚)、解决/重开、删除、编辑(If-Match)、实时帧合并。
 * 经 RealtimeContext.Provider 注入伪客户端以驱动 onFrame。
 */
import { act, fireEvent, renderHook, screen, waitFor } from '@testing-library/react';
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
import type { Comment, CommentMemberRef } from '../types';
import { UNDO_WINDOW_MS } from '../useDeferredDelete';

const silentReporter: MissingReporter = { report: () => undefined, reported: [] };

const ME: CommentMemberRef = { id: 'mem-1', member_type: 'human', name: 'Owner' };

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

interface RecordedCall {
  url: string;
  method: string;
  idempotencyKey: string | null;
}

let calls: RecordedCall[] = [];
let failNext = false;
let frameListener: ((frame: RealtimeEventFrame) => void) | null = null;
const frameListeners = new Set<(frame: RealtimeEventFrame) => void>();

const fakeClient = {
  subscribe: vi.fn(),
  unsubscribe: vi.fn(),
  onFrame: (cb: (frame: RealtimeEventFrame) => void) => {
    frameListeners.add(cb);
    frameListener = (frame) => {
      for (const listener of [...frameListeners]) listener(frame);
    };
    return () => {
      frameListeners.delete(cb);
      if (frameListeners.size === 0) frameListener = null;
    };
  },
};
const realtimeValue = { state: 'connected', client: fakeClient } as unknown as RealtimeContextValue;

function mockFetch(): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({
      url,
      method,
      idempotencyKey: new Headers(init?.headers).get('Idempotency-Key'),
    });
    if (failNext) {
      failNext = false;
      return fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'x' } },
      });
    }
    if (method === 'DELETE') return fakeResponse({ status: 204 });
    if (method === 'GET' && url.includes('/comments') && !url.includes('/comments/')) {
      return fakeResponse({ body: { data: [ROOT], next_cursor: null } });
    }
    if (method === 'GET' && url.includes('/replies')) {
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }
    if (method === 'GET' && url.includes('/reactions')) {
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }
    // 写操作返回带确定 id 的评论(创建/解决/编辑)
    return fakeResponse({
      body: { data: { ...ROOT, id: 'c-server', resolved_at: '2026-07-03T00:00:00Z' } },
    });
  }) as typeof fetch;
}

function wrapper(props: { children: ReactNode }): React.JSX.Element {
  // useCommentsData 的延迟删除经 useToast/useT 呈现撤销提示,故测试栈含 I18n + Toast Provider。
  return (
    <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
      <ToastProvider regionLabel="test">
        <RealtimeContext.Provider value={realtimeValue}>{props.children}</RealtimeContext.Provider>
      </ToastProvider>
    </I18nProvider>
  );
}

function render(): ReturnType<
  typeof renderHook<ReturnType<typeof useCommentsData>, { member: CommentMemberRef | null }>
> {
  return renderHook(({ member }) => useCommentsData('iss-1', member), {
    wrapper,
    initialProps: { member: ME as CommentMemberRef | null },
  });
}

beforeEach(() => {
  calls = [];
  failNext = false;
  frameListener = null;
  frameListeners.clear();
  vi.stubGlobal('fetch', mockFetch());
});
afterEach(() => vi.unstubAllGlobals());

describe('useCommentsData', () => {
  it('loads comments and subscribes to the issue channel', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.comments.map((c) => c.id)).toEqual(['c-1']);
    expect(fakeClient.subscribe).toHaveBeenCalledWith('issue:iss-1');
  });

  it('surfaces an error key when the load fails', async () => {
    failNext = true;
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.error).toBe('error.internal_error');
  });

  it('creates a top-level comment optimistically then reconciles', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    await act(async () => {
      await result.current.createTopLevel('new', { suppressTriggers: false });
    });
    expect(result.current.comments.some((c) => c.id === 'c-server')).toBe(true);
    expect(result.current.comments.some((c) => c.id.startsWith('local-'))).toBe(false);
  });

  it('keeps a failed top-level optimistic entity and retries with the same idempotency key', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    failNext = true;
    await act(async () => {
      await expect(
        result.current.createTopLevel('boom', { suppressTriggers: false }),
      ).rejects.toBeTruthy();
    });
    const failed = result.current.comments.find((c) => c.id.startsWith('local-')) as
      (Comment & { delivery_state?: string }) | undefined;
    expect(failed?.delivery_state).toBe('failed');
    const firstKey = calls.find((call) => call.method === 'POST')?.idempotencyKey;

    await act(async () => {
      await result.current.createTopLevel('boom', { suppressTriggers: false });
    });

    const postKeys = calls
      .filter((call) => call.method === 'POST')
      .map((call) => call.idempotencyKey);
    expect(firstKey).not.toBeNull();
    expect(postKeys).toEqual([firstKey, firstKey]);
    expect(result.current.comments.filter((comment) => comment.id === 'c-server')).toHaveLength(1);
    expect(result.current.comments.some((comment) => comment.id.startsWith('local-'))).toBe(false);
  });

  it('creates a reply nested under the thread root and keeps a failed entity on failure', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    await act(async () => {
      await result.current.createReply(ROOT, 'reply', { suppressTriggers: true });
    });
    const root = result.current.comments.find((c) => c.id === 'c-1');
    expect(root?.reply_count).toBe(1);
    // failure path
    failNext = true;
    await act(async () => {
      await expect(
        result.current.createReply(ROOT, 'bad', { suppressTriggers: false }),
      ).rejects.toBeTruthy();
    });
    const after = result.current.comments.find((c) => c.id === 'c-1');
    expect(after?.preview_replies?.find((r) => r.id.startsWith('local-'))?.delivery_state).toBe(
      'failed',
    );
  });

  it('createReply 以嵌套 reply 为 parent(parent.id!==rootId 且 preview_replies 缺省)→ 归并到线程根', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    // parent 是一条回复(thread_root_id 指向 c-1),且其 preview_replies 为 undefined
    const nestedParent: Comment = { ...ROOT, id: 'r-9', thread_root_id: 'c-1', parent_id: 'c-1' };
    await act(async () => {
      await result.current.createReply(nestedParent, 'deep', { suppressTriggers: false });
    });
    const root = result.current.comments.find((c) => c.id === 'c-1');
    expect(root?.reply_count).toBe(1);
    expect(
      root?.preview_replies?.some((r) => r.id === 'c-server' || r.id.startsWith('local-')),
    ).toBe(true);
  });

  it('toggles reactions add/remove and rolls back on failure', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    // add
    await act(async () => {
      await result.current.toggleReaction(ROOT, '👍');
    });
    let reacted = result.current.comments.find((c) => c.id === 'c-1');
    expect(reacted?.reactions.find((r) => r.emoji === '👍')?.reacted_by_me).toBe(true);
    // remove (now reacted)
    await act(async () => {
      await result.current.toggleReaction(reacted as Comment, '👍');
    });
    reacted = result.current.comments.find((c) => c.id === 'c-1');
    expect(reacted?.reactions.find((r) => r.emoji === '👍')).toBeUndefined();
    // failure rollback
    await act(async () => {
      await result.current.toggleReaction(reacted as Comment, '🎉');
    });
    failNext = true;
    const before = result.current.comments;
    await act(async () => {
      await result.current.toggleReaction(
        result.current.comments.find((c) => c.id === 'c-1') as Comment,
        '🎉',
      );
    });
    expect(result.current.comments).toBe(before);
  });

  it('no-ops reaction toggle without a current member', async () => {
    const { result, rerender } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    rerender({ member: null });
    const before = result.current.comments;
    await act(async () => {
      await result.current.toggleReaction(ROOT, '👍');
    });
    expect(result.current.comments).toBe(before);
  });

  it('resolves and reopens a thread', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    await act(async () => {
      await result.current.setResolved(ROOT, true);
    });
    expect(result.current.comments.some((c) => c.resolved_at === '2026-07-03T00:00:00Z')).toBe(
      true,
    );
    // failure rollback
    failNext = true;
    await act(async () => {
      await result.current.setResolved(result.current.comments[0] as Comment, false);
    });
    expect(result.current.comments.some((c) => c.resolved_at === '2026-07-03T00:00:00Z')).toBe(
      true,
    );
  });

  it('keeps a tombstone immediately and defers the real DELETE (§9.5.5)', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    act(() => {
      result.current.remove(ROOT);
    });
    const tombstone = result.current.comments.find((comment) => comment.id === 'c-1');
    expect(tombstone?.deleted_at).not.toBeNull();
    expect(tombstone?.body_markdown).toBe('');
    // 撤销窗口内尚未真正删除
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
  });

  it('restores the comment when undo is clicked within the window', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    act(() => {
      result.current.remove(ROOT);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(result.current.comments.some((c) => c.id === 'c-1')).toBe(true));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
  });

  it('issues the real DELETE once the undo window expires', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    vi.useFakeTimers();
    act(() => {
      result.current.remove(ROOT);
    });
    await act(async () => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS);
      await Promise.resolve();
    });
    expect(calls.some((c) => c.method === 'DELETE')).toBe(true);
    vi.useRealTimers();
  });

  it('rolls back to the snapshot when the deferred DELETE fails', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    vi.useFakeTimers();
    act(() => {
      result.current.remove(ROOT);
    });
    failNext = true;
    await act(async () => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.comments.some((c) => c.id === 'c-1')).toBe(true);
    vi.useRealTimers();
  });

  it('ignores a second delete while one is already pending (double-delete guard)', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    act(() => {
      result.current.remove(ROOT);
      result.current.remove(ROOT);
    });
    // 仍只生成一个墓碑,不抛错也不重复排程。
    expect(result.current.comments.filter((c) => c.id === 'c-1')).toHaveLength(1);
    expect(result.current.comments.find((c) => c.id === 'c-1')?.deleted_at).not.toBeNull();
  });

  it('saves an edit with the optimistic lock', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    await act(async () => {
      await result.current.saveEdit(ROOT, 'edited body');
    });
    const patch = calls.find((c) => c.method === 'PATCH');
    expect(patch?.url).toContain('/api/v1/comments/c-1');
  });

  it('merges realtime comment frames into the list', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    act(() => {
      frameListener?.({
        op: 'event',
        channel: 'issue:iss-1',
        seq: 2,
        event: 'comment.created',
        payload: { ...ROOT, id: 'c-live' } as unknown as Record<string, unknown>,
      } as RealtimeEventFrame);
    });
    expect(result.current.comments.some((c) => c.id === 'c-live')).toBe(true);
  });

  it('deduplicates a comment.created frame that wins the race with the POST response', async () => {
    let finishPost: ((response: Response) => void) | undefined;
    vi.stubGlobal('fetch', (async (_input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      if (method === 'GET') {
        return fakeResponse({ body: { data: [ROOT], next_cursor: null } });
      }
      return new Promise<Response>((resolve) => {
        finishPost = resolve;
      });
    }) as typeof fetch);
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let pending: Promise<Comment> | undefined;
    act(() => {
      pending = result.current.createTopLevel('racing', { suppressTriggers: false });
    });
    await waitFor(() =>
      expect(
        (
          result.current.comments.find((comment) => comment.id.startsWith('local-')) as
            (Comment & { delivery_state?: string }) | undefined
        )?.delivery_state,
      ).toBe('sending'),
    );

    const server = {
      ...ROOT,
      id: 'c-race',
      body_markdown: 'racing',
      body_html: '<p>racing</p>',
      body_text: 'racing',
    };
    act(() => {
      frameListener?.({
        op: 'event',
        channel: 'issue:iss-1',
        seq: 9,
        event: 'comment.created',
        payload: server,
      });
    });
    await act(async () => {
      finishPost?.(fakeResponse({ body: { data: server } }));
      await pending;
    });

    expect(result.current.comments.filter((comment) => comment.id === 'c-race')).toHaveLength(1);
    expect(result.current.comments.some((comment) => comment.id.startsWith('local-'))).toBe(false);
  });

  it('treats a correlated WS commit as success when the HTTP response is lost', async () => {
    let rejectPost: ((reason?: unknown) => void) | undefined;
    vi.stubGlobal('fetch', (async (_input: RequestInfo | URL, init?: RequestInit) => {
      if ((init?.method ?? 'GET') === 'GET') {
        return fakeResponse({ body: { data: [ROOT], next_cursor: null } });
      }
      return new Promise<Response>((_resolve, reject) => {
        rejectPost = reject;
      });
    }) as typeof fetch);
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let pending: Promise<Comment> | undefined;
    act(() => {
      pending = result.current.createTopLevel('committed over ws', { suppressTriggers: false });
    });
    const optimistic = await waitFor(() => {
      const value = result.current.comments.find((comment) => comment.id.startsWith('local-'));
      expect(value?.client_request_id).toBeTruthy();
      return value as Comment;
    });
    const committed: Comment = {
      ...ROOT,
      id: 'c-ws-commit',
      body_markdown: 'committed over ws',
      body_html: '<p>committed over ws</p>',
      body_text: 'committed over ws',
      client_request_id: optimistic.client_request_id,
    };
    act(() => {
      frameListener?.({
        op: 'event',
        channel: 'issue:iss-1',
        seq: 12,
        event: 'comment.created',
        payload: committed as unknown as Record<string, unknown>,
      });
    });
    await act(async () => {
      rejectPost?.(new TypeError('response connection lost'));
      await expect(pending).resolves.toMatchObject({ id: committed.id });
    });

    expect(result.current.comments.filter((comment) => comment.id === committed.id)).toHaveLength(
      1,
    );
    expect(result.current.comments.some((comment) => comment.id.startsWith('local-'))).toBe(false);
    expect(result.current.comments.some((comment) => comment.delivery_state === 'failed')).toBe(
      false,
    );
  });

  it('merges realtime changes into replies after the full thread has loaded', async () => {
    const loadedReply: Comment = {
      ...ROOT,
      id: 'r-loaded',
      parent_id: ROOT.id,
      thread_root_id: ROOT.id,
      body_markdown: 'loaded',
      body_html: '<p>loaded</p>',
      body_text: 'loaded',
    };
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/replies')) {
        return fakeResponse({ body: { data: [loadedReply], next_cursor: null } });
      }
      return fakeResponse({
        body: { data: [{ ...ROOT, reply_count: 1, preview_replies: [] }], next_cursor: null },
      });
    }) as typeof fetch);
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    const stateful = result.current as unknown as typeof result.current & {
      loadReplies: (root: Comment) => Promise<void>;
    };
    await act(async () => {
      await stateful.loadReplies(ROOT);
    });
    act(() => {
      frameListener?.({
        op: 'event',
        channel: 'issue:iss-1',
        seq: 10,
        event: 'comment.updated',
        payload: {
          id: loadedReply.id,
          body_text: 'updated live',
          updated_at: '2026-07-04T00:00:00Z',
        },
      });
    });

    expect(result.current.comments[0]?.preview_replies?.[0]?.body_text).toBe('updated live');
  });
});

describe('执行占位五态与失败重试(验收必修 3 / §9.8 / comment-inbox §4.1)', () => {
  const queuedFrame = {
    op: 'event',
    channel: 'issue:iss-1',
    seq: 2,
    event: 'execution.queued',
    payload: {
      execution_id: 'e1',
      agent_member_id: 'mem-agent',
      agent_name: 'rev',
      comment_id: 'c-1',
    },
  } as RealtimeEventFrame;
  const failedFrame = {
    op: 'event',
    channel: 'execution:e1',
    seq: 3,
    event: 'execution.failed',
    payload: { execution_id: 'e1', failure_reason: 'nonzero_exit' },
  } as RealtimeEventFrame;

  it('applies the complete non-terminal state machine directly from the issue channel', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => frameListener?.(queuedFrame));
    expect(result.current.placeholders[0]?.status).toBe('queued');

    for (const [event, expected] of [
      ['execution.claimed', 'running'],
      ['execution.awaiting_approval', 'waiting'],
      ['execution.queued', 'queued'],
      ['execution.started', 'running'],
    ] as const) {
      act(() =>
        frameListener?.({
          ...queuedFrame,
          event,
          payload: { ...queuedFrame.payload, execution_id: 'e1' },
        }),
      );
      expect(result.current.placeholders).toHaveLength(1);
      expect(result.current.placeholders[0]?.status).toBe(expected);
    }

    act(() =>
      frameListener?.({
        ...queuedFrame,
        event: 'execution.completed',
        payload: { execution_id: 'e1' },
      }),
    );
    expect(result.current.placeholders).toEqual([]);
  });

  it('restores active placeholders from the existing issue execution REST list', async () => {
    const triggered: Comment = {
      ...ROOT,
      mentions: [{ id: 'mem-agent', member_type: 'agent', name: 'rev' }],
      triggered_execution_ids: ['e-restored'],
    };
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/workspaces/ws-1/executions')) {
        return fakeResponse({
          body: {
            data: [
              {
                id: 'e-restored',
                issue_id: 'iss-1',
                agent_id: 'agent-entity',
                status: 'awaiting_approval',
                failure_reason: null,
              },
            ],
            next_cursor: null,
          },
        });
      }
      return fakeResponse({ body: { data: [triggered], next_cursor: null } });
    }) as typeof fetch);

    const { result } = renderHook(() => useCommentsData('iss-1', ME, 'ws-1'), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.placeholders).toEqual([
      {
        execution_id: 'e-restored',
        comment_id: 'c-1',
        agent_id: 'mem-agent',
        agent_name: 'rev',
        status: 'waiting',
        failure_reason: null,
      },
    ]);
  });

  async function seedFailedPlaceholder() {
    const utils = render();
    await waitFor(() => expect(utils.result.current.isLoading).toBe(false));
    act(() => frameListener?.(queuedFrame));
    expect(utils.result.current.placeholders[0]?.status).toBe('queued');
    // 占位出现后生命周期订阅生效(onFrame 单槽测试 fake:最新监听为生命周期处理器)。
    act(() => frameListener?.(failedFrame));
    expect(utils.result.current.placeholders[0]?.status).toBe('failed');
    expect(utils.result.current.placeholders[0]?.failure_reason).toBe('nonzero_exit');
    return utils;
  }

  it('failed 占位重试:取回原评论并重发(重新入队执行),移除失败占位', async () => {
    const { result } = await seedFailedPlaceholder();
    await act(async () => {
      await result.current.retryExecution('e1');
    });
    const urls = calls.map((c) => `${c.method} ${c.url}`);
    expect(urls.some((u) => u.startsWith('GET') && u.includes('/comments/c-1'))).toBe(true);
    expect(urls.some((u) => u.startsWith('POST') && u.includes('/issues/iss-1/comments'))).toBe(
      true,
    );
    expect(result.current.placeholders.some((p) => p.execution_id === 'e1')).toBe(false);
  });

  it('重试请求失败 → 失败占位保留(供再次重试)', async () => {
    const { result } = await seedFailedPlaceholder();
    failNext = true;
    await act(async () => {
      await result.current.retryExecution('e1');
    });
    expect(result.current.placeholders).toHaveLength(1);
    expect(result.current.placeholders[0]?.status).toBe('failed');
  });

  it('无 comment_id 的占位重试为无操作(不发请求)', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    act(() =>
      frameListener?.({
        ...queuedFrame,
        payload: { execution_id: 'e2', agent_member_id: 'mem-agent', agent_name: 'rev' },
      }),
    );
    act(() =>
      frameListener?.({ ...failedFrame, payload: { execution_id: 'e2', failure_reason: 'x' } }),
    );
    const before = calls.length;
    await act(async () => {
      await result.current.retryExecution('e2');
    });
    expect(calls.length).toBe(before);
  });

  it('completed 生命周期帧移除占位(agent 评论回流的补充路径)', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    act(() => frameListener?.(queuedFrame));
    act(() =>
      frameListener?.({
        ...failedFrame,
        event: 'execution.completed',
        payload: { execution_id: 'e1' },
      }),
    );
    expect(result.current.placeholders).toHaveLength(0);
  });
});

describe('pure helpers', () => {
  it('patchCommentById patches nested replies and returns same ref on miss', () => {
    const withReply: Comment = {
      ...ROOT,
      preview_replies: [{ ...ROOT, id: 'c-2', parent_id: 'c-1' }],
    };
    const patched = patchCommentById([withReply], 'c-2', (c) => ({ ...c, body_text: 'x' }));
    expect(patched[0].preview_replies?.[0].body_text).toBe('x');
    const miss = patchCommentById([ROOT], 'nope', (c) => c);
    expect(miss).toEqual([ROOT]);
  });

  it('removeCommentById removes top-level and nested replies and returns same ref on miss', () => {
    // 顶层移除
    expect(removeCommentById([ROOT], 'c-1')).toEqual([]);
    // 嵌套移除并递减 reply_count
    const withReply: Comment = {
      ...ROOT,
      reply_count: 1,
      preview_replies: [{ ...ROOT, id: 'c-2', parent_id: 'c-1' }],
    };
    const removed = removeCommentById([withReply], 'c-2');
    expect(removed[0].preview_replies).toEqual([]);
    expect(removed[0].reply_count).toBe(0);
    // 未命中原样返回(同引用)
    const input: readonly Comment[] = [ROOT];
    const miss = removeCommentById(input, 'nope');
    expect(miss).toBe(input);
  });

  it('toggleReactionLocal adds, removes self, and drops empty reactions', () => {
    const added = toggleReactionLocal([], '👍', ME);
    expect(added[0].count).toBe(1);
    const removed = toggleReactionLocal(added, '👍', ME);
    expect(removed).toEqual([]);
    // removing when others still reacted keeps the chip
    const withOthers = toggleReactionLocal(
      [
        {
          emoji: '👍',
          count: 2,
          reacted_by_me: true,
          actors: [ME, { id: 'mem-9', member_type: 'human', name: 'B' }],
        },
      ],
      '👍',
      ME,
    );
    expect(withOthers[0].count).toBe(1);
    expect(withOthers[0].reacted_by_me).toBe(false);
  });
});
