import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { Route, Routes } from 'react-router';
import { renderWithProviders } from '../../test-utils/render';
import { ForbiddenPage } from '../pages/ForbiddenPage';

describe('ForbiddenPage', () => {
  it('从安全 query 恢复工作区上下文，并可进入成员名册联系管理员', async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <Routes>
        <Route path="/forbidden" element={<ForbiddenPage />} />
        <Route path="/w/team" element={<span data-testid="workspace-home" />} />
        <Route path="/w/team/members" element={<span data-testid="workspace-members" />} />
      </Routes>,
      { route: '/forbidden?workspace=%2Fw%2Fteam' },
    );

    expect(screen.getByRole('heading', { level: 1, name: 'No permission' })).toBeInTheDocument();
    expect(screen.getByText('You do not have permission to view this page.')).toBeInTheDocument();
    expect(screen.getByTestId('forbidden-contact')).toHaveTextContent(
      'Ask a workspace admin for access.',
    );
    expect(screen.getByTestId('forbidden-page')).toHaveAttribute('id', 'mesh-main-content');

    const workspaceAction = screen.getByTestId('forbidden-workspace');
    expect(workspaceAction).toHaveAttribute('href', '/w/team');
    const contactAction = screen.getByRole('link', { name: 'View workspace members' });
    expect(contactAction).toHaveAttribute('href', '/w/team/members');
    await user.click(contactAction);
    expect(screen.getByTestId('workspace-members')).toBeInTheDocument();
  });

  it('无工作区上下文时提供返回首页出口', async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <Routes>
        <Route path="/forbidden" element={<ForbiddenPage />} />
        <Route path="/" element={<span data-testid="home" />} />
      </Routes>,
      { route: '/forbidden' },
    );

    const homeAction = screen.getByTestId('forbidden-home');
    expect(screen.queryByTestId('forbidden-contact-action')).toBeNull();
    expect(homeAction).toHaveAttribute('href', '/');
    await user.click(homeAction);
    expect(screen.getByTestId('home')).toBeInTheDocument();
  });

  it('拒绝 query 中的跨站恢复路径', () => {
    renderWithProviders(<ForbiddenPage />, {
      route: '/forbidden?workspace=https%3A%2F%2Fevil.example%2Fw%2Fteam',
    });
    expect(screen.queryByTestId('forbidden-contact-action')).toBeNull();
    expect(screen.queryByTestId('forbidden-workspace')).toBeNull();
  });
});
