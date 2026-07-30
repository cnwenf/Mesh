/**
 * useEntitySearch — 防抖时序(120ms)、identifier 跳过防抖、AbortController 中止、
 * 单调令牌丢弃陈旧响应、错误保留 + retry、空工作区/空 query/未启用不请求。
 *
 * 经全局 fetch 桩驱动(MeshApiClient 默认实现于调用时读全局 fetch);
 * 假时钟下以 `vi.advanceTimersByTimeAsync` 冲刷微任务,断言确定性落地。
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetApiClient } from '../../api/instance';
import { fakeResponse } from '../../api/__tests__/fetchStub';
import type { SearchItem } from '../../api/search';
import { DEBOUNCE_MS, useEntitySearch } from '../useEntitySearch';

function item(id: string, title: string): SearchItem {
  return {
    type: 'issue',
    id,
    title,
    context: {
      identifier: `K-${id}`,
      project: null,
      status: { id: 's', name: 'Todo', category: 'todo' },
    },
    icon: 'issue',
    url: `/issues/${id}`,
  };
}

function searchBody(...items: SearchItem[]): unknown {
  return { data: items, next_cursor: null };
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}

function deferred<T>(): Deferred<T> {
  let resolveFn: (value: T) => void = () => undefined;
  const promise = new Promise<T>((resolve) => {
    resolveFn = resolve;
  });
  return { promise, resolve: resolveFn };
}

/** 推进假时钟并冲刷微任务(让已解析的 fetch 链路落到 state) */
async function tick(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  resetApiClient();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  resetApiClient();
});

describe('useEntitySearch', () => {
  it('普通查询防抖 120ms:窗口内不请求,到点请求一次', async () => {
    const fetchImpl = vi.fn(async () => fakeResponse({ body: searchBody(item('1', 'a')) }));
    vi.stubGlobal('fetch', fetchImpl);

    const { result } = renderHook(() =>
      useEntitySearch({ workspaceId: 'ws-1', query: '登录', enabled: true }),
    );
    expect(result.current.isSearching).toBe(true); // 防抖等待即检索态
    await tick(DEBOUNCE_MS - 1);
    expect(fetchImpl).not.toHaveBeenCalled();
    await tick(1);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const url = String(fetchImpl.mock.calls[0][0]);
    expect(url).toContain('/api/v1/workspaces/ws-1/search');
    expect(url).toContain(encodeURIComponent('登录'));
    await tick();
    expect(result.current.isSearching).toBe(false);
    expect(result.current.items).toHaveLength(1);
    expect(result.current.error).toBeNull();
  });

  it('identifier 查询跳过防抖:立即请求', async () => {
    const fetchImpl = vi.fn(async () => fakeResponse({ body: searchBody(item('1', 'a')) }));
    vi.stubGlobal('fetch', fetchImpl);
    renderHook(() => useEntitySearch({ workspaceId: 'ws-1', query: ' web-124 ', enabled: true }));
    await tick(0);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('新查询中止旧请求(AbortController)', async () => {
    const signals: AbortSignal[] = [];
    const fetchImpl = vi.fn(async (_url: string, init?: RequestInit) => {
      signals.push(init?.signal as AbortSignal);
      return fakeResponse({ body: searchBody() });
    });
    vi.stubGlobal('fetch', fetchImpl);
    const { rerender } = renderHook(
      (props: { query: string }) =>
        useEntitySearch({ workspaceId: 'ws-1', query: props.query, enabled: true }),
      { initialProps: { query: 'WEB-1' } }, // identifier → 立即请求
    );
    await tick(0);
    rerender({ query: 'WEB-2' });
    expect(signals[0]?.aborted).toBe(true);
    await tick(0);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it('陈旧响应丢弃:先发的慢响应后到不覆盖新查询结果', async () => {
    const d1 = deferred<Response>();
    const d2 = deferred<Response>();
    let call = 0;
    const fetchImpl = vi.fn(async () => {
      call += 1;
      return call === 1 ? d1.promise : d2.promise;
    });
    vi.stubGlobal('fetch', fetchImpl);
    const { result, rerender } = renderHook(
      (props: { query: string }) =>
        useEntitySearch({ workspaceId: 'ws-1', query: props.query, enabled: true }),
      { initialProps: { query: 'AAA-1' } },
    );
    await tick(0); // 请求 1 发出
    rerender({ query: 'BBB-2' }); // 中止请求 1 并发出请求 2
    await tick(0);
    // 新查询(请求 2)先解析
    await act(async () => {
      d2.resolve(fakeResponse({ body: searchBody(item('new', 'new')) }));
    });
    await tick(0);
    // 旧查询(请求 1)后解析 → 必须被丢弃(令牌已前进)
    await act(async () => {
      d1.resolve(fakeResponse({ body: searchBody(item('old', 'old')) }));
    });
    await tick(0);
    expect(result.current.items.map((entry) => entry.id)).toEqual(['new']);
    expect(result.current.isSearching).toBe(false);
  });

  it('错误保留为 MeshApiError;retry 以当前 query 重新请求', async () => {
    let call = 0;
    const fetchImpl = vi.fn(async () => {
      call += 1;
      return call === 1
        ? fakeResponse({ status: 422, body: { error: { code: 'query_cost_exceeded', message: 'x' } } })
        : fakeResponse({ body: searchBody(item('1', 'a')) });
    });
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() =>
      useEntitySearch({ workspaceId: 'ws-1', query: 'ABC-1', enabled: true }),
    );
    await tick(0);
    expect(result.current.error).not.toBeNull();
    expect(result.current.error?.code).toBe('query_cost_exceeded');
    expect(result.current.items).toHaveLength(0);

    act(() => result.current.retry());
    await tick(0);
    expect(result.current.error).toBeNull();
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(result.current.items).toHaveLength(1);
  });

  it('网络失败 → network 错误(供 offline 降级判定)', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error('boom');
    });
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() =>
      useEntitySearch({ workspaceId: 'ws-1', query: 'ABC-1', enabled: true }),
    );
    await tick(0);
    expect(result.current.error).not.toBeNull();
    expect(result.current.error?.code).toBe('network');
  });

  it('workspaceId null / enabled false / 空 query → 不请求且空结果', async () => {
    const fetchImpl = vi.fn();
    vi.stubGlobal('fetch', fetchImpl);
    const { result, rerender } = renderHook(
      (props: { workspaceId: string | null; query: string; enabled: boolean }) =>
        useEntitySearch(props),
      { initialProps: { workspaceId: null as string | null, query: 'abc', enabled: true } },
    );
    await tick(DEBOUNCE_MS * 2);
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(result.current.items).toEqual([]);
    expect(result.current.isSearching).toBe(false);

    rerender({ workspaceId: 'ws-1', query: 'abc', enabled: false });
    await tick(DEBOUNCE_MS * 2);
    expect(fetchImpl).not.toHaveBeenCalled();

    rerender({ workspaceId: 'ws-1', query: '   ', enabled: true });
    await tick(DEBOUNCE_MS * 2);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('卸载中止在途请求(无 setState-after-unmount)', async () => {
    const d = deferred<Response>();
    const fetchImpl = vi.fn(async () => d.promise);
    vi.stubGlobal('fetch', fetchImpl);
    const { unmount } = renderHook(() =>
      useEntitySearch({ workspaceId: 'ws-1', query: 'ABC-1', enabled: true }),
    );
    await tick(0);
    const signal = (fetchImpl.mock.calls[0][1] as RequestInit).signal as AbortSignal;
    unmount();
    expect(signal.aborted).toBe(true);
    // 卸载后解析不抛错
    await act(async () => {
      d.resolve(fakeResponse({ body: searchBody(item('1', 'a')) }));
    });
  });
});
