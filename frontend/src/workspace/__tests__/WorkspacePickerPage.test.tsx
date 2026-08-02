/**
 * 工作区选择页(§3.4 解析序 ⑤):列出所属工作区,选定后记忆 last_workspace
 * 并回跳 ?next= 意图路径(仅接受站内 `/` 开头相对路径,防开放重定向)。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../api/__tests__/fetchStub';
import { resetApiClient } from '../../api/instance';
import { I18nProvider } from '../../i18n';
import { WorkspacePickerPage } from '../WorkspacePickerPage';

const ME_TWO = {
  user: { id: 'u1', email: 'u@example.com', display_name: 'U', last_active_workspace_id: null },
  memberships: [
    {
      workspace_id: 'ws-a',
      workspace_name: 'Alpha',
      workspace_slug: 'alpha',
      role: 'member',
      status: 'active',
      joined_at: null,
    },
    {
      workspace_id: 'ws-b',
      workspace_name: 'Beta',
      workspace_slug: 'beta',
      role: 'admin',
      status: 'active',
      joined_at: null,
    },
  ],
};

function LandingProbe(): React.JSX.Element {
  const location = useLocation();
  return <span data-testid="landed">{location.pathname + location.search}</span>;
}

function renderAt(initial: string): void {
  render(
    <I18nProvider requested={null} systemLocales={[]}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/workspace-picker" element={<WorkspacePickerPage />} />
          <Route path="*" element={<LandingProbe />} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

function stubMe(body: unknown, status = 200): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => fakeResponse({ status, body })),
  );
  resetApiClient();
}

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  resetApiClient();
});

describe('WorkspacePickerPage', () => {
  it('列出所属工作区卡片(testid ws-picker-{slug})', async () => {
    stubMe({ data: ME_TWO });
    renderAt('/workspace-picker?next=%2Fboard');
    await waitFor(() => expect(screen.getByTestId('ws-picker-alpha')).toBeInTheDocument());
    expect(screen.getByTestId('ws-picker-beta')).toBeInTheDocument();
    expect(screen.getByTestId('workspace-picker')).toBeInTheDocument();
  });

  it('选定后记忆 last_workspace 并回跳 ?next= 意图路径', async () => {
    stubMe({ data: ME_TWO });
    renderAt('/workspace-picker?next=%2Fboard%3Fview%3Dx');
    await waitFor(() => expect(screen.getByTestId('ws-picker-beta')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('ws-picker-beta'));
    await waitFor(() => expect(screen.getByTestId('landed').textContent).toBe('/board?view=x'));
    expect(window.localStorage.getItem(`mesh.last_workspace:${window.location.host}:u1`)).toBe('beta');
  });

  it('无 ?next= 时落选中工作区的规范收件箱', async () => {
    stubMe({ data: ME_TWO });
    renderAt('/workspace-picker');
    await waitFor(() => expect(screen.getByTestId('ws-picker-alpha')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('ws-picker-alpha'));
    await waitFor(() => expect(screen.getByTestId('landed').textContent).toBe('/w/alpha/inbox'));
  });

  it('next 参数防开放重定向:protocol-relative / 绝对 URL 一律忽略', async () => {
    stubMe({ data: ME_TWO });
    renderAt('/workspace-picker?next=%2F%2Fevil.example.com%2Fphish');
    await waitFor(() => expect(screen.getByTestId('ws-picker-alpha')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('ws-picker-beta'));
    await waitFor(() => expect(screen.getByTestId('landed').textContent).toBe('/w/beta/inbox'));
  });

  it('用户信息加载失败 → 错误态(可重试入口)', async () => {
    stubMe({ error: { code: 'internal_error', message: 'x' } }, 500);
    renderAt('/workspace-picker');
    await waitFor(() => expect(screen.getByTestId('ws-picker-error')).toBeInTheDocument());
    // 重试入口可点击(onRetry → navigate(0) 刷新重载)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
  });
});
