/**
 * SkillsPage 新建技能对话框脏态离开保护测试(L242):
 * 对话框有输入即脏——站内导航弹 stay/discard 确认;空表单直接放行。
 */
import { fireEvent, screen } from '@testing-library/react';
import { Link, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { SkillsPage } from '../SkillsPage';

afterEach(() => {
  vi.unstubAllGlobals();
});

const ME = {
  user: { id: 'u-1', email: 'o@x.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'T',
      workspace_slug: 't',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};

function stubAll(): void {
  const impl = (async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (url.includes('/skills')) return fakeResponse({ body: { data: [], next_cursor: null } });
    return fakeResponse({ body: { data: [] } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
}

function renderSkillsWithExit() {
  return renderWithProviders(
    <>
      <Routes>
        <Route path="/skills" element={<SkillsPage />} />
        <Route path="/somewhere" element={<div data-testid="left-page">left-page</div>} />
      </Routes>
      <Link to="/somewhere" data-testid="leave-link">
        leave
      </Link>
    </>,
    { route: '/skills' },
  );
}

describe('SkillsPage create-dialog dirty guard (L242)', () => {
  it('blocks navigation while the create dialog has input; stay keeps it open', async () => {
    stubAll();
    renderSkillsWithExit();

    fireEvent.click(await screen.findByTestId('skills-create-open'));
    fireEvent.change(screen.getByTestId('skill-create-name'), { target: { value: 'N' } });
    fireEvent.click(screen.getByTestId('leave-link'));

    expect(await screen.findByTestId('dirty-guard-stay')).toBeTruthy();
    fireEvent.click(screen.getByTestId('dirty-guard-stay'));
    expect(screen.queryByTestId('left-page')).toBeNull();
    // 对话框仍在,输入未丢
    expect((screen.getByTestId('skill-create-name') as HTMLInputElement).value).toBe('N');
  });

  it('discard leaves the page', async () => {
    stubAll();
    renderSkillsWithExit();

    fireEvent.click(await screen.findByTestId('skills-create-open'));
    fireEvent.change(screen.getByTestId('skill-create-name'), { target: { value: 'N' } });
    fireEvent.click(screen.getByTestId('leave-link'));

    fireEvent.click(await screen.findByTestId('dirty-guard-discard'));
    expect(await screen.findByTestId('left-page')).toBeTruthy();
  });

  it('empty dialog does not block navigation', async () => {
    stubAll();
    renderSkillsWithExit();

    fireEvent.click(await screen.findByTestId('skills-create-open'));
    fireEvent.click(screen.getByTestId('leave-link'));

    expect(await screen.findByTestId('left-page')).toBeTruthy();
    expect(screen.queryByTestId('dirty-guard-stay')).toBeNull();
  });
});
