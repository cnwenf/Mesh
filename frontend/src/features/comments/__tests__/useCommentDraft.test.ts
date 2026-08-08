/**
 * 评论草稿本地暂存 hook 测试(comment-inbox.md C14):持久化 / 清除 / key 切换。
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useCommentDraft } from '../useCommentDraft';

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('useCommentDraft', () => {
  it('persists value to localStorage and clears', () => {
    const { result } = renderHook(() => useCommentDraft('iss-1'));
    act(() => result.current.setValue('draft text'));
    expect(window.localStorage.getItem('mesh.comments.draft.iss-1')).toBe('draft text');
    act(() => result.current.clear());
    expect(result.current.value).toBe('');
    expect(window.localStorage.getItem('mesh.comments.draft.iss-1')).toBeNull();
  });

  it('rehydrates an existing draft on mount', () => {
    window.localStorage.setItem('mesh.comments.draft.iss-2', 'saved');
    const { result } = renderHook(() => useCommentDraft('iss-2'));
    expect(result.current.value).toBe('saved');
  });

  it('reloads the draft when the key changes', () => {
    window.localStorage.setItem('mesh.comments.draft.a', 'A');
    window.localStorage.setItem('mesh.comments.draft.b', 'B');
    const { result, rerender } = renderHook(({ key }) => useCommentDraft(key), {
      initialProps: { key: 'a' },
    });
    expect(result.current.value).toBe('A');
    rerender({ key: 'b' });
    expect(result.current.value).toBe('B');
  });

  it('reports persisted=true when writes reach localStorage (L242)', () => {
    const { result } = renderHook(() => useCommentDraft('iss-p'));
    expect(result.current.persisted).toBe(true);
    act(() => result.current.setValue('x'));
    expect(result.current.persisted).toBe(true);
  });

  it('reports persisted=false when storage is unavailable; clear restores it (L242)', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota/private mode');
    });
    const { result } = renderHook(() => useCommentDraft('iss-np'));
    act(() => result.current.setValue('memory only'));
    expect(result.current.persisted).toBe(false);
    expect(result.current.value).toBe('memory only');
    setItem.mockRestore();
    // 清空后无内容可丢:persisted 回归 true
    act(() => result.current.clear());
    expect(result.current.persisted).toBe(true);
  });

  it('key switch resets persisted (fresh load has no unsaved write) (L242)', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('unavailable');
    });
    const { result, rerender } = renderHook(({ key }) => useCommentDraft(key), {
      initialProps: { key: 'k1' },
    });
    act(() => result.current.setValue('v'));
    expect(result.current.persisted).toBe(false);
    setItem.mockRestore();
    rerender({ key: 'k2' });
    expect(result.current.persisted).toBe(true);
  });
});
