/**
 * authStore 分支补测(R4-H1 access-only 契约):setSession 仅写 access(JS 永不
 * 持有 refresh——refresh 仅存 HttpOnly cookie,state 无 refreshToken 字段);
 * clearToken 清空 access 并触发 theme.md §2.3 登出清理(偏好回「未表达」、
 * active user/workspace 置空)。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { getToken, useAuthStore } from '../authStore';
import { useSettingsStore } from '../settingsStore';

beforeEach(() => {
  localStorage.clear();
  useAuthStore.setState({ token: null });
});

describe('authStore 会话凭证分支(R4-H1)', () => {
  it('setSession 仅写 access——state 无 refreshToken 字段', () => {
    useAuthStore.getState().setSession({ accessToken: 'access-1' });
    expect(getToken()).toBe('access-1');
    expect('refreshToken' in useAuthStore.getState()).toBe(false);
  });

  it('clearToken 清空 access 并触发登出清理(theme.md §2.3)', () => {
    useAuthStore.getState().setSession({ accessToken: 'access-2' });
    useSettingsStore.setState((state) => ({
      preferences: { ...state.preferences, theme: 'dark' },
      sessionProbed: true,
    }));

    useAuthStore.getState().clearToken();

    expect(getToken()).toBeNull();
    // onLogoutCleanup 的可观察效果:偏好回「未表达」,同步态复位。
    const prefs = useSettingsStore.getState();
    expect(prefs.preferences.theme).toBeNull();
    expect(prefs.preferences.locale).toBeNull();
    expect(prefs.sessionProbed).toBe(false);
  });
});
