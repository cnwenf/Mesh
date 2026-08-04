import { screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { Route, Routes } from 'react-router';
import { useAuthStore } from '../../state/authStore';
import { renderWithProviders } from '../../test-utils/render';
import { RegisterPage } from '../pages/RegisterPage';

describe('RegisterPage', () => {
  beforeEach(() => {
    useAuthStore.getState().clearToken();
  });

  it('复用认证公共流程并默认打开注册模式', () => {
    renderWithProviders(
      <Routes>
        <Route path="/register" element={<RegisterPage />} />
      </Routes>,
      { route: '/register' },
    );

    expect(screen.getByRole('heading', { level: 1, name: 'Create account' })).toBeInTheDocument();
    expect(screen.getByTestId('login-mode-register')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('login-display-name')).toHaveAttribute('autocomplete', 'name');
    expect(screen.getByTestId('login-password')).toHaveAttribute('autocomplete', 'new-password');
  });
});
