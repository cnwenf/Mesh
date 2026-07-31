/**
 * useSaveIndicator 状态机单测:idle→saving→saved/conflict,saved 自动淡出,
 * 迁移取消旧计时器,卸载不泄漏。
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SAVE_INDICATOR_FADE_MS, useSaveIndicator } from '../useSaveIndicator';

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useSaveIndicator', () => {
  it('初始 idle 且无保存时间', () => {
    const { result } = renderHook(() => useSaveIndicator());
    expect(result.current.phase).toBe('idle');
    expect(result.current.savedAt).toBeNull();
  });

  it('begin 进入 saving', () => {
    const { result } = renderHook(() => useSaveIndicator());
    act(() => result.current.begin());
    expect(result.current.phase).toBe('saving');
  });

  it('succeed 记录时间戳并在淡出后回落 idle', () => {
    const { result } = renderHook(() => useSaveIndicator());
    act(() => result.current.succeed('2026-07-30T10:00:00Z'));
    expect(result.current.phase).toBe('saved');
    expect(result.current.savedAt).toBe('2026-07-30T10:00:00Z');
    act(() => {
      vi.advanceTimersByTime(SAVE_INDICATOR_FADE_MS);
    });
    expect(result.current.phase).toBe('idle');
  });

  it('succeed 缺省时间戳取当前时间', () => {
    const { result } = renderHook(() => useSaveIndicator());
    act(() => result.current.succeed());
    expect(result.current.savedAt).not.toBeNull();
  });

  it('conflict 弱提示同样自动淡出', () => {
    const { result } = renderHook(() => useSaveIndicator());
    act(() => result.current.conflict());
    expect(result.current.phase).toBe('conflict');
    act(() => {
      vi.advanceTimersByTime(SAVE_INDICATOR_FADE_MS);
    });
    expect(result.current.phase).toBe('idle');
  });

  it('begin 取消挂起的淡出计时器(保持 saving)', () => {
    const { result } = renderHook(() => useSaveIndicator());
    act(() => result.current.succeed());
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    act(() => result.current.begin());
    act(() => {
      vi.advanceTimersByTime(SAVE_INDICATOR_FADE_MS);
    });
    // 淡出计时器已被 begin 清除,phase 停留 saving
    expect(result.current.phase).toBe('saving');
  });

  it('reset 立即复位 idle', () => {
    const { result } = renderHook(() => useSaveIndicator());
    act(() => result.current.begin());
    act(() => result.current.reset());
    expect(result.current.phase).toBe('idle');
  });

  it('卸载时清理计时器,不再触发状态更新', () => {
    const { result, unmount } = renderHook(() => useSaveIndicator());
    act(() => result.current.succeed());
    unmount();
    expect(() => {
      act(() => {
        vi.advanceTimersByTime(SAVE_INDICATOR_FADE_MS);
      });
    }).not.toThrow();
  });
});
