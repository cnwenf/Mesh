/**
 * 评论草稿本地暂存 hook 测试(comment-inbox.md C14):持久化 / 清除 / key 切换。
 */
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { useCommentDraft } from '../useCommentDraft';

beforeEach(() => {
  window.localStorage.clear();
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
});
