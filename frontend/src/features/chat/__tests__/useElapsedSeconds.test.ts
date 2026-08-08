/**
 * useElapsedSeconds 测试(§9.8 运行反馈):active 期间每秒 +1,失活立即归零。
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useElapsedSeconds } from '../useElapsedSeconds';

describe('useElapsedSeconds(§9.8)', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('active 期间按秒递增', () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useElapsedSeconds(true));
    expect(result.current).toBe(0);
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(result.current).toBe(3);
  });

  it('inactive 归零且不再计时', () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(
      ({ active }: { readonly active: boolean }) => useElapsedSeconds(active),
      { initialProps: { active: true } },
    );
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(result.current).toBe(2);
    rerender({ active: false });
    expect(result.current).toBe(0);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(result.current).toBe(0);
  });

  it('重新 active 从 0 重新计时', () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(
      ({ active }: { readonly active: boolean }) => useElapsedSeconds(active),
      { initialProps: { active: true } },
    );
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    rerender({ active: false });
    rerender({ active: true });
    expect(result.current).toBe(0);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current).toBe(1);
  });
});
