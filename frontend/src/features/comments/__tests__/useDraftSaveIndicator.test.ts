/**
 * useDraftSaveIndicator 单测(design-quality.md §9.5.1):dirty→saving→saved 状态机、
 * 防抖时序、值未变不保存、清空回 idle、初次挂载不提示。
 */
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useDraftSaveIndicator } from '../useDraftSaveIndicator';

interface Scheduled {
  readonly handler: () => void;
  readonly ms: number;
}

/** 手动计时器:记录调度,按插入序逐个触发,便于断言中间态。 */
function createTimers(): {
  setTimeout: (handler: () => void, ms: number) => number;
  clearTimeout: (handle: number) => void;
  fireNext: () => boolean;
  pending: number;
} {
  const scheduled = new Map<number, Scheduled>();
  let nextId = 1;
  return {
    setTimeout: (handler, ms) => {
      const id = nextId;
      nextId += 1;
      scheduled.set(id, { handler, ms });
      return id;
    },
    clearTimeout: (handle) => {
      scheduled.delete(handle);
    },
    fireNext: () => {
      const first = scheduled.keys().next();
      if (first.done === true) return false;
      const entry = scheduled.get(first.value);
      scheduled.delete(first.value);
      entry?.handler();
      return true;
    },
    get pending() {
      return scheduled.size;
    },
  };
}

describe('useDraftSaveIndicator', () => {
  it('stays idle on first mount even with a restored value', () => {
    const timers = createTimers();
    const { result } = renderHook(() => useDraftSaveIndicator('restored', { timers }));
    expect(result.current.status).toBe('idle');
    expect(timers.pending).toBe(0);
  });

  it('transitions dirty → saving → saved after the debounce window', () => {
    const timers = createTimers();
    const now = (): number => 1700000000000;
    const { result, rerender } = renderHook(({ value }) => useDraftSaveIndicator(value, { timers, now }), {
      initialProps: { value: '' },
    });
    act(() => rerender({ value: 'hello' }));
    expect(result.current.status).toBe('dirty');
    // 防抖窗口到期 → saving
    act(() => {
      timers.fireNext();
    });
    expect(result.current.status).toBe('saving');
    // saving 过渡结束 → saved + savedAt
    act(() => {
      timers.fireNext();
    });
    expect(result.current.status).toBe('saved');
    expect(result.current.savedAt).toBe(1700000000000);
  });

  it('debounces rapid changes (no save while still typing)', () => {
    const timers = createTimers();
    const { result, rerender } = renderHook(({ value }) => useDraftSaveIndicator(value, { timers }), {
      initialProps: { value: '' },
    });
    act(() => rerender({ value: 'h' }));
    act(() => rerender({ value: 'he' }));
    act(() => rerender({ value: 'hel' }));
    // 每次变更都重设防抖,尚未到期 → 仍 dirty
    expect(result.current.status).toBe('dirty');
    expect(result.current.savedAt).toBeNull();
    // 让最后一次防抖与 saving 依次到期
    act(() => {
      while (timers.fireNext()) {
        /* drain */
      }
    });
    expect(result.current.status).toBe('saved');
  });

  it('returns to idle when the value is cleared', () => {
    const timers = createTimers();
    const { result, rerender } = renderHook(({ value }) => useDraftSaveIndicator(value, { timers }), {
      initialProps: { value: '' },
    });
    act(() => rerender({ value: 'x' }));
    act(() => {
      while (timers.fireNext()) {
        /* drain */
      }
    });
    expect(result.current.status).toBe('saved');
    act(() => rerender({ value: '' }));
    expect(result.current.status).toBe('idle');
  });

  it('does not schedule a save when the value is unchanged across rerenders', () => {
    const timers = createTimers();
    const { rerender } = renderHook(({ value }) => useDraftSaveIndicator(value, { timers }), {
      initialProps: { value: '' },
    });
    act(() => rerender({ value: 'same' }));
    const afterChange = timers.pending;
    expect(afterChange).toBeGreaterThan(0);
    // 同值重渲染不应新增调度
    act(() => rerender({ value: 'same' }));
    expect(timers.pending).toBe(afterChange);
  });
});
