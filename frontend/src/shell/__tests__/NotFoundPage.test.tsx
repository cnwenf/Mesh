/**
 * NotFoundPage — 404 文案 + 回首页链接可导航。
 */
import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Route, Routes } from 'react-router';
import { renderWithProviders } from '../../test-utils/render';
import { NotFoundPage } from '../pages/NotFoundPage';

describe('NotFoundPage', () => {
  it('呈现标题与说明,回首页链接可导航', () => {
    renderWithProviders(
      <Routes>
        <Route path="*" element={<NotFoundPage />} />
        <Route path="/" element={<div data-testid="home-stub" />} />
      </Routes>,
      { route: '/definitely-not-a-route' },
    );
    expect(screen.getByText('Page not found')).toBeInTheDocument();
    expect(screen.getByText(/does not exist/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('notfound-home'));
    expect(screen.getByTestId('home-stub')).toBeInTheDocument();
  });
});
