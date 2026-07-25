import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ListEnvelope } from '../../types/envelopes';
import { MeshApiError } from '../errors';
import { fetchAllPages, useCursorPagination } from '../pagination';

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** 按入队顺序逐个消费的 fetcher,便于手动控制每页时机。 */
function queuedFetcher<T>() {
  const queue: Array<Promise<ListEnvelope<T>>> = [];
  const fetcher = vi.fn((_: string | null) => {
    const next = queue.shift();
    if (!next) {
      return Promise.reject(new Error('no queued page'));
    }
    return next;
  });
  return {
    fetcher,
    enqueue: (page: ListEnvelope<T>) => queue.push(Promise.resolve(page)),
    enqueueDeferred: () => {
      const d = deferred<ListEnvelope<T>>();
      queue.push(d.promise);
      return d;
    },
  };
}

describe('useCursorPagination(README §6.14 keyset 游标)', () => {
  it('挂载即以 cursor=null 首屏加载', async () => {
    // Arrange
    const { fetcher, enqueue } = queuedFetcher<number>();
    enqueue({ data: [1, 2], next_cursor: null });

    // Act
    const { result } = renderHook(() => useCursorPagination<number>(fetcher));
    expect(result.current.isLoading).toBe(true);

    // Assert
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(fetcher).toHaveBeenCalledWith(null);
    expect(result.current.items).toEqual([1, 2]);
    expect(result.current.hasMore).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('fetchNext 跨页累积,next_cursor=null 后 hasMore=false', async () => {
    // Arrange
    const { fetcher, enqueueDeferred } = queuedFetcher<number>();
    const first = enqueueDeferred();
    const { result } = renderHook(() => useCursorPagination<number>(fetcher));
    await act(async () => {
      first.resolve({ data: [1], next_cursor: 'c1' });
    });
    await waitFor(() => expect(result.current.items).toEqual([1]));
    expect(result.current.hasMore).toBe(true);

    // Act:先发起 fetchNext(不等待),再 resolve 在途的页,避免自锁
    const second = enqueueDeferred();
    let nextPromise!: Promise<void>;
    act(() => {
      nextPromise = result.current.fetchNext();
    });
    expect(result.current.isFetchingNext).toBe(true);
    await act(async () => {
      second.resolve({ data: [2, 3], next_cursor: null });
      await nextPromise;
    });

    // Assert
    await waitFor(() => expect(result.current.items).toEqual([1, 2, 3]));
    expect(fetcher).toHaveBeenLastCalledWith('c1');
    expect(result.current.hasMore).toBe(false);
    expect(result.current.isFetchingNext).toBe(false);
  });

  it('并发 fetchNext 去重:同时两次只触发一次请求', async () => {
    // Arrange
    const { fetcher, enqueueDeferred } = queuedFetcher<number>();
    const first = enqueueDeferred();
    const { result } = renderHook(() => useCursorPagination<number>(fetcher));
    await act(async () => {
      first.resolve({ data: [1], next_cursor: 'c1' });
    });
    await waitFor(() => expect(result.current.hasMore).toBe(true));

    // Act:不等待地连发两次
    const second = enqueueDeferred();
    let p1!: Promise<void>;
    let p2!: Promise<void>;
    act(() => {
      p1 = result.current.fetchNext();
      p2 = result.current.fetchNext();
    });

    // Assert:仅新增一次请求(首屏 1 + next 1)
    expect(result.current.isFetchingNext).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(async () => {
      second.resolve({ data: [2], next_cursor: null });
      await Promise.all([p1, p2]);
    });
    await waitFor(() => expect(result.current.items).toEqual([1, 2]));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('!hasMore 时 fetchNext 为空操作', async () => {
    // Arrange
    const { fetcher, enqueue } = queuedFetcher<number>();
    enqueue({ data: [1], next_cursor: null });
    const { result } = renderHook(() => useCursorPagination<number>(fetcher));
    await waitFor(() => expect(result.current.hasMore).toBe(false));
    const callsBefore = fetcher.mock.calls.length;

    // Act
    await act(async () => {
      await result.current.fetchNext();
    });

    // Assert
    expect(fetcher.mock.calls.length).toBe(callsBefore);
  });

  it('首屏加载期间 fetchNext 为空操作', async () => {
    // Arrange
    const { fetcher, enqueueDeferred } = queuedFetcher<number>();
    const first = enqueueDeferred();
    const { result } = renderHook(() => useCursorPagination<number>(fetcher));

    // Act:首屏仍在途
    await act(async () => {
      await result.current.fetchNext();
    });

    // Assert:未触发下一页请求
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => {
      first.resolve({ data: [1], next_cursor: null });
    });
  });

  it('reset 清空并从 null 重新加载', async () => {
    // Arrange
    const { fetcher, enqueueDeferred } = queuedFetcher<number>();
    const first = enqueueDeferred();
    const { result } = renderHook(() => useCursorPagination<number>(fetcher));
    await act(async () => {
      first.resolve({ data: [1], next_cursor: 'c1' });
    });
    await waitFor(() => expect(result.current.items).toEqual([1]));

    // Act
    const second = enqueueDeferred();
    let resetPromise!: Promise<void>;
    act(() => {
      resetPromise = result.current.reset();
    });
    expect(result.current.isLoading).toBe(true);
    expect(result.current.items).toEqual([]);
    expect(fetcher).toHaveBeenLastCalledWith(null);
    await act(async () => {
      second.resolve({ data: [9], next_cursor: null });
      await resetPromise;
    });

    // Assert
    await waitFor(() => expect(result.current.items).toEqual([9]));
    expect(result.current.hasMore).toBe(false);
  });

  it('错误显现于 error 并停止累积(MeshApiError 原样)', async () => {
    // Arrange
    const { fetcher, enqueueDeferred } = queuedFetcher<number>();
    const first = enqueueDeferred();
    const { result } = renderHook(() => useCursorPagination<number>(fetcher));

    // Act
    const boom = new MeshApiError({ status: 500, code: 'internal_error', message: 'x' });
    await act(async () => {
      first.reject(boom);
    });

    // Assert
    await waitFor(() => expect(result.current.error).toBe(boom));
    expect(result.current.hasMore).toBe(false);
    expect(result.current.items).toEqual([]);
    expect(result.current.isLoading).toBe(false);
  });

  it('非 MeshApiError 拒绝归一为 network 错误', async () => {
    // Arrange
    const { fetcher, enqueueDeferred } = queuedFetcher<number>();
    const first = enqueueDeferred();
    const { result } = renderHook(() => useCursorPagination<number>(fetcher));

    // Act
    await act(async () => {
      first.reject(new Error('boom'));
    });

    // Assert
    await waitFor(() => expect(result.current.error?.code).toBe('network'));
    expect(result.current.error?.status).toBe(0);
  });
});

describe('fetchAllPages(沿游标走到 null)', () => {
  it('聚合多页直到 next_cursor=null', async () => {
    // Arrange
    const pages = new Map<string | null, ListEnvelope<number>>([
      [null, { data: [1, 2], next_cursor: 'c1' }],
      ['c1', { data: [3], next_cursor: 'c2' }],
      ['c2', { data: [4], next_cursor: null }],
    ]);
    const fetcher = vi.fn(async (cursor: string | null) => pages.get(cursor) as ListEnvelope<number>);

    // Act
    const all = await fetchAllPages(fetcher);

    // Assert
    expect(all).toEqual([1, 2, 3, 4]);
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(fetcher.mock.calls.map(([c]) => c)).toEqual([null, 'c1', 'c2']);
  });

  it('单页(首页即末页)返回该页', async () => {
    const fetcher = vi.fn(async () => ({ data: [7], next_cursor: null }));
    await expect(fetchAllPages(fetcher)).resolves.toEqual([7]);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
