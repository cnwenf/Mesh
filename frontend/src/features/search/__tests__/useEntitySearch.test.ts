/**
 * useEntitySearch 单测(§4.7:防抖 150ms / identifier 跳过防抖 / 过期请求取消 / 重试)。
 * 以受控 fetch 桩(注入 MeshApiClient.fetchImpl)+ 假时钟驱动。
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api/client';
import { MeshApiError } from '../../../api/errors';
import { SEARCH_DEBOUNCE_MS, useEntitySearch } from '../useEntitySearch';
import type { SearchResultItem } from '../types';

interface PendingCall {
  url: string;
  signal: AbortSignal | undefined;
  resolve: (response: Response) => void;
  reject: (error: unknown) => void;
}

const ISSUE_ITEM: SearchResultItem = {
  type: 'issue',
  id: 'i1',
  title: 'Login page',
  context: { identifier: 'WEB-1', project: null, status: { id: 's', name: 'Todo', category: 'todo' } },
  icon: 'issue',
  url: '/w/acme/issues/i1',
};

function okEnvelope(data: readonly SearchResultItem[]): Response {
  return new Response(JSON.stringify({ data, next_cursor: null }), { status: 200 });
}

describe('useEntitySearch(防抖 + 过期取消,§4.7)', () => {
  const calls: PendingCall[] = [];
  const fetchImpl = vi.fn((url: string | URL | Request, init?: RequestInit) => {
    let resolve!: (response: Response) => void;
    let reject!: (error: unknown) => void;
    const promise = new Promise<Response>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    calls.push({ url: String(url), signal: init?.signal ?? undefined, resolve, reject });
    return promise;
  });
  const client = new MeshApiClient({ baseUrl: 'http://api.test', getToken: () => null, fetchImpl });

  beforeEach(() => {
    vi.useFakeTimers();
    calls.length = 0;
    fetchImpl.mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function setup(initialQuery = '') {
    return renderHook(
      ({ q, enabled }: { q: string; enabled: boolean }) =>
        useEntitySearch({ client, workspaceId: 'ws-1', query: q, enabled }),
      { initialProps: { q: initialQuery, enabled: true } },
    );
  }

  it('空查询不发请求,结果为空', () => {
    const { result } = setup('');
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(result.current.entityResults).toEqual([]);
    expect(result.current.loading).toBe(false);
  });

  it('普通查询防抖 150ms:窗口内不发,到点即发', () => {
    const { rerender } = setup('');
    rerender({ q: 'abc', enabled: true });
    expect(fetchImpl).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS - 1);
    });
    expect(fetchImpl).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(calls[0]?.url).toContain('/api/v1/workspaces/ws-1/search');
    expect(calls[0]?.url).toContain('q=abc');
  });

  it('连续输入只发最后一次(防抖合并)', () => {
    const { rerender } = setup('');
    rerender({ q: 'a', enabled: true });
    act(() => {
      vi.advanceTimersByTime(60);
    });
    rerender({ q: 'ab', enabled: true });
    act(() => {
      vi.advanceTimersByTime(60);
    });
    rerender({ q: 'abc', enabled: true });
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(calls[0]?.url).toContain('q=abc');
  });

  it('完整 identifier 形态跳过防抖即刻请求(大小写/空白不敏感)', () => {
    const { rerender } = setup('');
    rerender({ q: ' web-124 ', enabled: true });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(calls[0]?.url).toContain('q=web-124');
  });

  it('新请求发出时 abort 上一在途请求(过期取消)', () => {
    const { rerender } = setup('');
    rerender({ q: 'abc', enabled: true });
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const firstSignal = calls[0]?.signal;
    rerender({ q: 'def', enabled: true });
    expect(firstSignal?.aborted).toBe(true);
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it('响应到达后落结果并退出 loading', async () => {
    const { result, rerender } = setup('');
    rerender({ q: 'abc', enabled: true });
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    });
    expect(result.current.loading).toBe(true);
    await act(async () => {
      calls[0]?.resolve(okEnvelope([ISSUE_ITEM]));
    });
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.entityResults).toEqual([ISSUE_ITEM]);
  });

  it('查询清空后重置结果且不发新请求', async () => {
    const { result, rerender } = setup('');
    rerender({ q: 'abc', enabled: true });
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    });
    await act(async () => {
      calls[0]?.resolve(okEnvelope([ISSUE_ITEM]));
    });
    rerender({ q: '', enabled: true });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(result.current.entityResults).toEqual([]);
    expect(result.current.loading).toBe(false);
  });

  it('enabled=false 时停检索', () => {
    const { rerender } = setup('');
    rerender({ q: 'abc', enabled: false });
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS * 3);
    });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('失败上报 error 态;retry 不改变 query 即重发并恢复', async () => {
    const { result, rerender } = setup('');
    rerender({ q: 'abc', enabled: true });
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    });
    await act(async () => {
      calls[0]?.reject(new Error('boom'));
    });
    expect(result.current.error).not.toBeNull();
    expect(result.current.loading).toBe(false);

    act(() => {
      result.current.retry();
    });
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    await act(async () => {
      calls[1]?.resolve(okEnvelope([ISSUE_ITEM]));
    });
    expect(result.current.error).toBeNull();
    expect(result.current.entityResults).toEqual([ISSUE_ITEM]);
  });

  it('被 abort 的在途失败不上报为错误态', async () => {
    const { result, rerender } = setup('');
    rerender({ q: 'abc', enabled: true });
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    });
    const first = calls[0];
    rerender({ q: 'def', enabled: true }); // abort 第一请求
    await act(async () => {
      first?.reject(new Error('aborted'));
    });
    expect(result.current.error).toBeNull();
  });

  it('MeshApiError(非 2xx 信封)原样上报,区别于网络错误归一为 network', async () => {
    const { result, rerender } = setup('');
    rerender({ q: 'abc', enabled: true });
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    });
    // 非 2xx 错误信封 → 客户端抛 MeshApiError(code=envelope.code)→ hook 原样置 error
    await act(async () => {
      calls[0]?.resolve(
        new Response(JSON.stringify({ error: { code: 'internal', message: 'x' } }), {
          status: 500,
        }),
      );
    });
    expect(result.current.error).toBeInstanceOf(MeshApiError);
    expect(result.current.error?.code).toBe('internal');
  });

  it('迟到响应经代次守卫丢弃,不覆盖新结果(竞态治理)', async () => {
    const { result, rerender } = setup('');
    rerender({ q: 'abc', enabled: true });
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    });
    const first = calls[0];
    rerender({ q: 'def', enabled: true });
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    });
    const second = calls[1];
    await act(async () => {
      second?.resolve(okEnvelope([ISSUE_ITEM]));
    });
    expect(result.current.entityResults).toEqual([ISSUE_ITEM]);
    // 过期(首请求)响应迟到 → 代次不符丢弃,不覆盖现行结果
    const stale: SearchResultItem = { ...ISSUE_ITEM, id: 'stale', title: 'Stale' };
    await act(async () => {
      first?.resolve(okEnvelope([stale]));
    });
    expect(result.current.entityResults).toEqual([ISSUE_ITEM]);
  });

  it('settled:空 query 无需请求即为已完成', () => {
    const { result } = setup('');
    expect(result.current.settled).toBe(true);
  });

  it('settled:防抖窗口与在途期间为 false,成功落定后为 true(§4.2 no-results 门控)', async () => {
    const { result, rerender } = setup('');
    rerender({ q: 'abc', enabled: true });
    // 防抖窗口:请求未发,settled 已失效(杜绝 no-results 瞬态闪现)
    expect(result.current.settled).toBe(false);
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS - 1);
    });
    expect(result.current.settled).toBe(false);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    // 在途:请求已发未决,settled 仍为 false
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(result.current.settled).toBe(false);
    await act(async () => {
      calls[0]?.resolve(okEnvelope([ISSUE_ITEM]));
    });
    expect(result.current.settled).toBe(true);
  });

  it('settled:query 变化即失效,回到先前已完成 query 亦须本轮检索完成', async () => {
    const { result, rerender } = setup('');
    rerender({ q: 'abc', enabled: true });
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    });
    await act(async () => {
      calls[0]?.resolve(okEnvelope([]));
    });
    expect(result.current.settled).toBe(true);
    // 清空 → settled 回归(空 query 无需请求)
    rerender({ q: '', enabled: true });
    expect(result.current.settled).toBe(true);
    // 重新输入同一 query:上一完成态已失效,新请求完成前 settled=false
    rerender({ q: 'abc', enabled: true });
    expect(result.current.settled).toBe(false);
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    });
    expect(result.current.settled).toBe(false);
    await act(async () => {
      calls[1]?.resolve(okEnvelope([ISSUE_ITEM]));
    });
    expect(result.current.settled).toBe(true);
  });

  it('settled:失败亦属已完成(错误态由 error 驱动);retry 成功后恢复', async () => {
    const { result, rerender } = setup('');
    rerender({ q: 'abc', enabled: true });
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    });
    await act(async () => {
      calls[0]?.reject(new Error('boom'));
    });
    expect(result.current.settled).toBe(true);
    expect(result.current.error).not.toBeNull();
    // retry:query 未变,重发期间 loading 覆盖;成功后 settled 维持 true
    act(() => {
      result.current.retry();
    });
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    });
    await act(async () => {
      calls[1]?.resolve(okEnvelope([ISSUE_ITEM]));
    });
    expect(result.current.settled).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it('settled:迟到响应(代次不符)不标记新 query 已完成', async () => {
    const { result, rerender } = setup('');
    rerender({ q: 'abc', enabled: true });
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    });
    const first = calls[0];
    rerender({ q: 'def', enabled: true });
    expect(result.current.settled).toBe(false);
    // 过期响应迟到 → 丢弃,且不得把 'def' 误报为已完成
    await act(async () => {
      first?.resolve(okEnvelope([ISSUE_ITEM]));
    });
    expect(result.current.settled).toBe(false);
  });
});
