/**
 * LoginPage — 提交写入 authStore 并导航首页;已登录重定向;空 token 不写入。
 * 以桩首页路由替代 AppShell,单测不触 WS/网络。
 */
import { fireEvent, screen } from '@testing-library/react';
import { beforeEach } from 'vitest';
import { describe, expect, it } from 'vitest';
import { Route, Routes } from 'react-router-dom';
import { useAuthStore } from '../../state/authStore';
import { renderWithProviders } from '../../test-utils/render';
import { LoginPage } from '../pages/LoginPage';

function renderLogin(): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<div data-testid="home-stub" />} />
    </Routes>,
    { route: '/login' },
  );
}

describe('LoginPage', () => {
  beforeEach(() => {
    useAuthStore.getState().clearToken();
  });

  it('呈现说明与阶段 2 占位提示', () => {
    renderLogin();
    expect(screen.getByTestId('login-token')).toBeInTheDocument();
    expect(screen.getByTestId('login-submit')).toBeInTheDocument();
    expect(screen.getByText(/Phase 2/)).toBeInTheDocument();
  });

  it('提交 token 后写入 authStore 并导航首页', () => {
    renderLogin();
    fireEvent.change(screen.getByTestId('login-token'), { target: { value: 'secret-token' } });
    fireEvent.click(screen.getByTestId('login-submit'));
    expect(useAuthStore.getState().token).toBe('secret-token');
    expect(screen.getByTestId('home-stub')).toBeInTheDocument();
  });

  it('空 token 提交不写入,停留登录页', () => {
    renderLogin();
    fireEvent.click(screen.getByTestId('login-submit'));
    expect(useAuthStore.getState().token).toBeNull();
    expect(screen.getByTestId('login-token')).toBeInTheDocument();
  });

  it('已登录时重定向到首页', () => {
    useAuthStore.getState().setToken('already-here');
    renderLogin();
    expect(screen.getByTestId('home-stub')).toBeInTheDocument();
    expect(screen.queryByTestId('login-token')).not.toBeInTheDocument();
  });
});
