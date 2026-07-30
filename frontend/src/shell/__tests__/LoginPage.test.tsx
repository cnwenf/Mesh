/**
 * LoginPage — 产品级登录页基线(MES-107 去脚手架化):
 * 呈现标题/说明与账号表单;开发用 token 直填入口已移除;已登录重定向。
 * 真实邮箱/密码 · MFA · OAuth 流程见 LoginPageReal.test.tsx。
 */
import { screen } from '@testing-library/react';
import { beforeEach } from 'vitest';
import { describe, expect, it } from 'vitest';
import { Route, Routes } from 'react-router';
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

  it('呈现标题/说明与账号登录表单,无开发用 token 直填入口', () => {
    renderLogin();
    expect(screen.getByTestId('login-email')).toBeInTheDocument();
    expect(screen.getByTestId('login-password')).toBeInTheDocument();
    expect(screen.getByTestId('login-account-submit')).toBeInTheDocument();
    // 脚手架残留清理(MES-107):dev 令牌块与过时 phaseNote 已移除
    expect(screen.queryByTestId('login-token')).not.toBeInTheDocument();
    expect(screen.queryByTestId('login-submit')).not.toBeInTheDocument();
    expect(screen.queryByText(/Phase 2/)).not.toBeInTheDocument();
  });

  it('已登录时重定向到首页', () => {
    useAuthStore.getState().setToken('already-here');
    renderLogin();
    expect(screen.getByTestId('home-stub')).toBeInTheDocument();
    expect(screen.queryByTestId('login-email')).not.toBeInTheDocument();
  });
});
