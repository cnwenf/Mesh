import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AUTH_STORAGE_KEY, getToken, useAuthStore } from '../authStore';

describe('authStore(Bearer token,README §6.14/§6.16)', () => {
  it('初始无 token', () => {
    const { result } = renderHook(() => useAuthStore());
    expect(result.current.token).toBeNull();
    expect(getToken()).toBeNull();
  });

  it('setToken 写入并可经 getToken 在非 React 上下文读取', () => {
    const { result } = renderHook(() => useAuthStore());
    act(() => result.current.setToken('tok_abc'));
    expect(result.current.token).toBe('tok_abc');
    expect(getToken()).toBe('tok_abc');
  });

  it('clearToken 清空', () => {
    const { result } = renderHook(() => useAuthStore());
    act(() => result.current.setToken('tok_abc'));
    act(() => result.current.clearToken());
    expect(result.current.token).toBeNull();
  });

  it('持久化到 mesh.auth.v1', () => {
    const { result } = renderHook(() => useAuthStore());
    act(() => result.current.setToken('tok_abc'));
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    expect(raw).toContain('tok_abc');
  });
});
