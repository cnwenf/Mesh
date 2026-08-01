/**
 * useTaskStream hook 测试(squad.md §3.5):非终态时消费编排流,命中五类事件即触发
 * 重取回调;非编排事件不触发;流不可用(无主体)时静默退出(由轮询兜底);url=null /
 * enabled=false 时不连接。
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useTaskStream } from '../useTaskStream';

const encoder = new TextEncoder();

/** 吐出一帧后保持挂起(不收尾),避免测试期内触发重连。 */
function liveStream(frame: string): Response {
  let emitted = false;
  const reader = {
    read: (): Promise<{ done: boolean; value?: Uint8Array }> => {
      if (!emitted) {
        emitted = true;
        return Promise.resolve({ done: false, value: encoder.encode(frame) });
      }
      return new Promise(() => undefined);
    },
  };
  return {
    ok: true,
    status: 200,
    body: { getReader: () => reader },
    headers: { get: () => null },
  } as unknown as Response;
}

function closedStream(): Response {
  return {
    ok: true,
    status: 200,
    body: { getReader: () => ({ read: async () => ({ done: true }) }) },
    headers: { get: () => null },
  } as unknown as Response;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('useTaskStream', () => {
  it('invokes onEvent when an orchestration frame arrives', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => liveStream('event: task.status\nid: 1\ndata: {}\n\n')),
    );
    const onEvent = vi.fn();
    const { unmount } = renderHook(() =>
      useTaskStream({ url: 'http://api/stream', enabled: true, onEvent }),
    );
    await waitFor(() => expect(onEvent).toHaveBeenCalledTimes(1));
    unmount();
  });

  it('ignores frames outside the five orchestration events', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => liveStream('event: error\nid: 1\ndata: {}\n\n')),
    );
    const onEvent = vi.fn();
    const fetchImpl = vi.mocked(globalThis.fetch);
    const { unmount } = renderHook(() =>
      useTaskStream({ url: 'http://api/stream', enabled: true, onEvent }),
    );
    await waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    // 给帧解析一个 microtask 窗口,确认不触发
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(onEvent).not.toHaveBeenCalled();
    unmount();
  });

  it('silently degrades (no throw, no event) when the stream body is unavailable', async () => {
    const noBody = {
      ok: true,
      status: 200,
      body: undefined,
      headers: { get: () => null },
    } as unknown as Response;
    const fetchImpl = vi.fn(async () => noBody);
    vi.stubGlobal('fetch', fetchImpl);
    const onEvent = vi.fn();
    const { unmount } = renderHook(() =>
      useTaskStream({ url: 'http://api/stream', enabled: true, onEvent }),
    );
    await waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    expect(onEvent).not.toHaveBeenCalled();
    unmount();
  });

  it('does not connect when url is null or the task is terminal', () => {
    const fetchImpl = vi.fn();
    vi.stubGlobal('fetch', fetchImpl);
    renderHook(() => useTaskStream({ url: null, enabled: true, onEvent: () => undefined }));
    renderHook(() =>
      useTaskStream({ url: 'http://api/stream', enabled: false, onEvent: () => undefined }),
    );
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('reconnects a normally closed stream up to the bounded attempt limit', async () => {
    vi.useFakeTimers();
    const fetchImpl = vi.fn(async () => closedStream());
    vi.stubGlobal('fetch', fetchImpl);
    const { unmount } = renderHook(() =>
      useTaskStream({ url: 'http://api/stream', enabled: true, onEvent: vi.fn() }),
    );

    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(2));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(3));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(fetchImpl).toHaveBeenCalledTimes(3);
    unmount();
  });
});
