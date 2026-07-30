/**
 * 路由守卫(MES-106)— 未登录跳 /login?next=<原路径>;登录态渲染子路由;
 * token 经 zustand 外部变更(401 兜底清 token / 登录写 token)时随订阅重渲。
 */
import { act, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Route, Routes, useLocation } from 'react-router';
import { useAuthStore } from '../../state/authStore';
import { renderWithProviders } from '../../test-utils/render';
import { RequireAuth } from '../RequireAuth';

/** 登录页桩:回显当前完整路径,供断言守卫携带的 ?next= 值。 */
function LoginStub(): React.JSX.Element {
  const location = useLocation();
  return (
    <div>
      <span data-testid="login-page" />
      <span data-testid="login-location">{location.pathname + location.search}</span>
    </div>
  );
}

function renderGuarded(route = '/'): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <Routes>
      <Route element={<RequireAuth />}>
        <Route index element={<div data-testid="protected-home" />} />
        <Route path="issues" element={<div data-testid="protected-issues" />} />
      </Route>
      <Route path="/login" element={<LoginStub />} />
    </Routes>,
    { route },
  );
}

beforeEach(() => {
  useAuthStore.getState().clearToken();
});

afterEach(() => {
  useAuthStore.getState().clearToken();
});

describe('RequireAuth 路由守卫', () => {
  it('未登录访问受保护首页 → 跳 /login,next=/(编码)', () => {
    renderGuarded('/');
    expect(screen.getByTestId('login-page')).toBeInTheDocument();
    expect(screen.getByTestId('login-location').textContent).toBe('/login?next=%2F');
    expect(screen.queryByTestId('protected-home')).not.toBeInTheDocument();
  });

  it('未登录访问深层路径 → next 携带原路径(含查询串,编码)', () => {
    renderGuarded('/issues?focus=abc');
    expect(screen.getByTestId('login-page')).toBeInTheDocument();
    expect(screen.getByTestId('login-location').textContent).toBe(
      `/login?next=${encodeURIComponent('/issues?focus=abc')}`,
    );
    expect(screen.queryByTestId('protected-issues')).not.toBeInTheDocument();
  });

  it('登录态 → 渲染受保护子路由(不跳登录页)', () => {
    useAuthStore.getState().setToken('tok_valid');
    renderGuarded('/issues');
    expect(screen.getByTestId('protected-issues')).toBeInTheDocument();
    expect(screen.queryByTestId('login-page')).not.toBeInTheDocument();
  });

  it('守卫跳转后写入 token 不回退路由(回跳由 LoginPage navigate 完成,§4.1)', () => {
    renderGuarded('/');
    expect(screen.getByTestId('login-page')).toBeInTheDocument();
    act(() => {
      useAuthStore.getState().setToken('tok_new');
    });
    // 路由已落 /login,守卫不对登录页生效;回跳是登录页的职责(LoginPageReal 覆盖)。
    expect(screen.getByTestId('login-page')).toBeInTheDocument();
  });

  it('token 被清除(401 全局兜底)→ 受保护页即时跳登录页', () => {
    useAuthStore.getState().setToken('tok_valid');
    renderGuarded('/issues');
    expect(screen.getByTestId('protected-issues')).toBeInTheDocument();
    act(() => {
      useAuthStore.getState().clearToken();
    });
    expect(screen.getByTestId('login-page')).toBeInTheDocument();
    expect(screen.queryByTestId('protected-issues')).not.toBeInTheDocument();
  });
});
