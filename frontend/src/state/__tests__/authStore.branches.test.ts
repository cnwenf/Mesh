/**
 * authStore 分支补测:setSession 缺省 refresh 收敛为 null、getRefreshToken 读取。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { getRefreshToken, getToken, useAuthStore } from '../authStore';

beforeEach(() => {
  localStorage.clear();
  useAuthStore.setState({ token: null, refreshToken: null });
});

describe('authStore 会话凭证分支', () => {
  it('setSession 未带 refreshToken → 收敛为 null(而非 undefined)', () => {
    useAuthStore.getState().setSession({ accessToken: 'access-1' });
    expect(getToken()).toBe('access-1');
    expect(useAuthStore.getState().refreshToken).toBeNull();
  });

  it('setSession 带 refreshToken → 写入并经 getRefreshToken 读取', () => {
    useAuthStore.getState().setSession({ accessToken: 'access-2', refreshToken: 'refresh-2' });
    expect(getRefreshToken()).toBe('refresh-2');
  });
});
