/**
 * useCommentDraft 降级分支补齐(coverage fill):localStorage 读取抛错 → 降级空串;
 * 写入/移除抛错(隐私模式)→ 仅驻留内存不报错。整体替换 localStorage 对象以确保抛错生效。
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useCommentDraft } from '../useCommentDraft';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useCommentDraft storage degradation', () => {
  it('falls back to an empty string when reading localStorage throws', () => {
    vi.stubGlobal('localStorage', {
      getItem: (): string => {
        throw new Error('denied');
      },
      setItem: (): void => undefined,
      removeItem: (): void => undefined,
    });
    const { result } = renderHook(() => useCommentDraft('iss-x'));
    // 读取抛错被捕获 → 降级为空串
    expect(result.current.value).toBe('');
  });

  it('keeps the draft in memory when writing/removing throws', () => {
    vi.stubGlobal('localStorage', {
      getItem: (): string => 'pre-existing',
      setItem: (): void => {
        throw new Error('denied');
      },
      removeItem: (): void => {
        throw new Error('denied');
      },
    });
    const { result } = renderHook(() => useCommentDraft('iss-y'));
    // 初始读取成功
    expect(result.current.value).toBe('pre-existing');
    // 写入抛错被吞掉,内存值仍然更新
    act(() => result.current.setValue('in-memory'));
    expect(result.current.value).toBe('in-memory');
    // 清除(removeItem 抛错)同样不中断
    act(() => result.current.clear());
    expect(result.current.value).toBe('');
  });
});
