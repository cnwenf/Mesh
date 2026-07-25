import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { getToken as getTokenFromSource, useAuthStore as sourceAuthStore } from '../../state/authStore';
import { AUTH_HEADER, bearerHeader, getToken, useAuthStore } from '../tokenStore';

describe('tokenStore(README §6.14 鉴权,薄再导出)', () => {
  it('再导出 authStore 的同一 getToken 与 useAuthStore(DRY)', () => {
    expect(getToken).toBe(getTokenFromSource);
    expect(useAuthStore).toBe(sourceAuthStore);
  });

  it('AUTH_HEADER 为标准 Authorization 头名', () => {
    expect(AUTH_HEADER).toBe('Authorization');
  });

  it('bearerHeader 构造 "Bearer <token>" 值', () => {
    expect(bearerHeader('tok_abc')).toBe('Bearer tok_abc');
  });

  it('getToken 反映 authStore 中的当前 token', () => {
    // Arrange
    const { result } = renderHook(() => useAuthStore());

    // Act
    act(() => result.current.setToken('tok_xyz'));

    // Assert
    expect(getToken()).toBe('tok_xyz');
  });
});
