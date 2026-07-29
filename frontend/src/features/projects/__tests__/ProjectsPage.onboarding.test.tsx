/**
 * 项目空态四要素测试(onboarding.md §1.2.2):插画 + 引导文案 + 主操作打开既有创建对话框。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { ProjectsPage } from '../ProjectsPage';

const ME = {
  user: { id: 'usr-owner', email: 'owner@acme.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'Team',
      workspace_slug: 'team',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};

beforeEach(() => {
  vi.unstubAllGlobals();
  vi.stubGlobal(
    'fetch',
    (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/projects')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nope' } } });
    }) as typeof fetch,
  );
});
afterEach(() => vi.unstubAllGlobals());

describe('ProjectsPage onboarding empty state', () => {
  it('renders illustration + copy + a primary action that opens the create dialog', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ProjectsPage />);

    await waitFor(() => expect(screen.getByTestId('illustration-folder')).toBeInTheDocument());
    expect(screen.getByText('No projects yet')).toBeInTheDocument();
    expect(screen.getByText('Group related issues with a project.')).toBeInTheDocument();

    // 主操作深链既有创建向导(打开 CreateProjectDialog,而非另建向导)
    await user.click(screen.getByTestId('projects-empty-create'));
    expect(
      await screen.findByRole('dialog', { name: /new project/i }),
    ).toBeInTheDocument();
  });
});
