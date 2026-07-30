/**
 * 401 全局兜底(MES-106)— 豁免判定 / 跳转 URL 构造 / handleUnauthorized 动作。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useAuthStore } from '../../state/authStore';
import {
  LOGIN_PATH,
  buildLoginRedirectUrl,
  handleUnauthorized,
  isAuthExemptPath,
} from '../unauthorized';

const FAKE_LOCATION = { pathname: '/issues', search: '?focus=1' };

beforeEach(() => {
  useAuthStore.getState().setToken('tok_alive');
});

afterEach(() => {
  useAuthStore.getState().clearToken();
  vi.unstubAllGlobals();
});

describe('isAuthExemptPath(鉴权豁免端点不触发全局兜底)', () => {
  it('登录前可调用的精确端点 → 豁免', () => {
    expect(isAuthExemptPath('/api/v1/auth/login')).toBe(true);
    expect(isAuthExemptPath('/api/v1/auth/register')).toBe(true);
    expect(isAuthExemptPath('/api/v1/auth/mfa/verify')).toBe(true);
    expect(isAuthExemptPath('/api/v1/auth/forgot-password')).toBe(true);
    expect(isAuthExemptPath('/api/v1/auth/reset-password')).toBe(true);
    expect(isAuthExemptPath('/api/v1/auth/verify-email')).toBe(true);
  });

  it('OAuth 往返端点按前缀豁免(回调页有自己的失败 UI)', () => {
    expect(isAuthExemptPath('/api/v1/auth/oauth/mock/callback')).toBe(true);
    expect(isAuthExemptPath('/api/v1/auth/oauth/google/start')).toBe(true);
  });

  it('受保护端点 → 不豁免(401 即会话失效)', () => {
    expect(isAuthExemptPath('/api/v1/workspaces')).toBe(false);
    expect(isAuthExemptPath('/api/v1/me')).toBe(false);
    expect(isAuthExemptPath('/api/v1/sessions')).toBe(false);
    expect(isAuthExemptPath('/api/v1/auth/mfa/setup')).toBe(false);
    expect(isAuthExemptPath('/api/v1/auth/change-password')).toBe(false);
    expect(isAuthExemptPath('/api/v1/auth/refresh')).toBe(false);
  });

  it('精确项不做前缀匹配(防 /auth/login-x 误豁免)', () => {
    expect(isAuthExemptPath('/api/v1/auth/login-device')).toBe(false);
    expect(isAuthExemptPath('/api/v1/auth/logink')).toBe(false);
  });
});

describe('buildLoginRedirectUrl(§4.1 ?next= 回跳契约)', () => {
  it('编码原路径(含查询串)', () => {
    expect(buildLoginRedirectUrl('/issues?focus=1')).toBe(
      `${LOGIN_PATH}?next=${encodeURIComponent('/issues?focus=1')}`,
    );
  });

  it('首页路径', () => {
    expect(buildLoginRedirectUrl('/')).toBe(`${LOGIN_PATH}?next=${encodeURIComponent('/')}`);
  });
});

describe('handleUnauthorized(401 兜底动作)', () => {
  it('清除 access token 并跳登录页(携带当前路径)', () => {
    const redirect = vi.fn();
    handleUnauthorized(redirect, FAKE_LOCATION);
    expect(useAuthStore.getState().token).toBeNull();
    expect(redirect).toHaveBeenCalledTimes(1);
    expect(redirect).toHaveBeenCalledWith(buildLoginRedirectUrl('/issues?focus=1'));
  });

  it('查询串为空时仅携带 pathname', () => {
    const redirect = vi.fn();
    handleUnauthorized(redirect, { pathname: '/board', search: '' });
    expect(redirect).toHaveBeenCalledWith(buildLoginRedirectUrl('/board'));
  });

  it('已在登录页 → 仅清 token,不再跳转(防重定向成环 / 丢表单态)', () => {
    const redirect = vi.fn();
    useAuthStore.getState().setToken('tok_alive');
    handleUnauthorized(redirect, { pathname: '/login', search: '?next=%2Fissues' });
    expect(useAuthStore.getState().token).toBeNull();
    expect(redirect).not.toHaveBeenCalled();
  });

  it('缺省导航经 window.location.assign(注入式替身验证)', () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { ...FAKE_LOCATION, assign });
    handleUnauthorized();
    expect(assign).toHaveBeenCalledWith(buildLoginRedirectUrl('/issues?focus=1'));
  });

  it('token 已为空时幂等(仍跳转,不抛错)', () => {
    useAuthStore.getState().clearToken();
    const redirect = vi.fn();
    expect(() => handleUnauthorized(redirect, FAKE_LOCATION)).not.toThrow();
    expect(redirect).toHaveBeenCalledTimes(1);
  });
});
