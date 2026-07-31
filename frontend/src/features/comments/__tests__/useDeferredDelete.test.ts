/**
 * useDeferredDelete 单测(design-quality.md §9.5.5):窗口内/后撤销、commit 成功与失败回滚、
 * 双重删除守卫、卸载清理。计时器注入以彻底覆盖状态机。
 */
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { UNDO_WINDOW_MS, useDeferredDelete } from '../useDeferredDelete';

interface ManualTimers {
  setTimeout: (handler: () => void, ms: number) => number;
  clearTimeout: (handle: number) => void;
  fire: () => void;
  readonly size: number;
}

function createTimers(): ManualTimers {
  const scheduled = new Map<number, () => void>();
  let nextId = 1;
  return {
    setTimeout: (handler) => {
      const id = nextId;
      nextId += 1;
      scheduled.set(id, handler);
      return id;
    },
    clearTimeout: (handle) => {
      scheduled.delete(handle);
    },
    fire: () => {
      const first = scheduled.keys().next();
      if (first.done === true) return;
      const handler = scheduled.get(first.value);
      scheduled.delete(first.value);
      handler?.();
    },
    get size() {
      return scheduled.size;
    },
  };
}

describe('useDeferredDelete', () => {
  it('opens a pending window and commits after it expires', async () => {
    const timers = createTimers();
    const commit = vi.fn().mockResolvedValue(undefined);
    const onCommitted = vi.fn();
    const { result } = renderHook(() => useDeferredDelete<string>({ commit, onCommitted, timers }));

    act(() => result.current.request('c-1'));
    expect(result.current.phase).toBe('pending');
    expect(result.current.pending).toBe('c-1');
    expect(commit).not.toHaveBeenCalled();

    // 窗口到期 → commit
    act(() => timers.fire());
    expect(commit).toHaveBeenCalledWith('c-1');
    await act(async () => {
      await Promise.resolve();
    });
    expect(onCommitted).toHaveBeenCalledWith('c-1');
    expect(result.current.pending).toBeNull();
    expect(result.current.phase).toBeNull();
  });

  it('schedules the commit with the configured window', () => {
    const timers = createTimers();
    const setTimeout = vi.spyOn(timers, 'setTimeout');
    const { result } = renderHook(() =>
      useDeferredDelete<string>({ commit: vi.fn(), timers, windowMs: UNDO_WINDOW_MS }),
    );
    act(() => result.current.request('c-1'));
    expect(setTimeout).toHaveBeenCalledWith(expect.any(Function), UNDO_WINDOW_MS);
  });

  it('undo before the window closes cancels the delete', () => {
    const timers = createTimers();
    const commit = vi.fn();
    const { result } = renderHook(() => useDeferredDelete<string>({ commit, timers }));
    act(() => result.current.request('c-1'));
    let undone = false;
    act(() => {
      undone = result.current.undo();
    });
    expect(undone).toBe(true);
    expect(result.current.pending).toBeNull();
    expect(result.current.phase).toBeNull();
    // 计时器已清,窗口到期也不 commit
    act(() => timers.fire());
    expect(commit).not.toHaveBeenCalled();
  });

  it('undo after the window has committed returns false', async () => {
    const timers = createTimers();
    const commit = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useDeferredDelete<string>({ commit, timers }));
    act(() => result.current.request('c-1'));
    act(() => timers.fire());
    await act(async () => {
      await Promise.resolve();
    });
    let undone = true;
    act(() => {
      undone = result.current.undo();
    });
    expect(undone).toBe(false);
  });

  it('undo with no active delete returns false', () => {
    const timers = createTimers();
    const { result } = renderHook(() => useDeferredDelete<string>({ commit: vi.fn(), timers }));
    expect(result.current.undo()).toBe(false);
  });

  it('invokes onFailed and clears pending when commit rejects', async () => {
    const timers = createTimers();
    const error = new Error('server');
    const commit = vi.fn().mockRejectedValue(error);
    const onFailed = vi.fn();
    const { result } = renderHook(() => useDeferredDelete<string>({ commit, onFailed, timers }));
    act(() => result.current.request('c-1'));
    act(() => timers.fire());
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(onFailed).toHaveBeenCalledWith('c-1', error);
    expect(result.current.pending).toBeNull();
    expect(result.current.phase).toBeNull();
  });

  it('guards against a double delete while one is pending', () => {
    const timers = createTimers();
    const commit = vi.fn();
    const { result } = renderHook(() => useDeferredDelete<string>({ commit, timers }));
    act(() => result.current.request('c-1'));
    act(() => result.current.request('c-2'));
    expect(result.current.pending).toBe('c-1');
    // 只应有一个挂起计时器
    expect(timers.size).toBe(1);
  });

  it('reset returns to idle and clears the timer', () => {
    const timers = createTimers();
    const { result } = renderHook(() => useDeferredDelete<string>({ commit: vi.fn(), timers }));
    act(() => result.current.request('c-1'));
    act(() => result.current.reset());
    expect(result.current.pending).toBeNull();
    expect(result.current.phase).toBeNull();
    expect(timers.size).toBe(0);
  });

  it('clears the pending timer on unmount', () => {
    const timers = createTimers();
    const { result, unmount } = renderHook(() => useDeferredDelete<string>({ commit: vi.fn(), timers }));
    act(() => result.current.request('c-1'));
    expect(timers.size).toBe(1);
    unmount();
    expect(timers.size).toBe(0);
  });
});
