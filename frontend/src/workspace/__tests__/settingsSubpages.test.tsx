/**
 * 工作区设置薄子页(§6.12 管理员区):admin+ 门控(无权 → permission denied),
 * 成员角色 / 审批策略 / 状态与字段 / 危险操作。
 */
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../api/__tests__/fetchStub';
import { resetApiClient } from '../../api/instance';
import { ToastProvider } from '../../design';
import { I18nProvider } from '../../i18n';
import { WorkspaceProvider } from '../WorkspaceProvider';
import {
  WorkspaceApprovalsSettingsPage,
  WorkspaceDangerSettingsPage,
  WorkspaceFieldsSettingsPage,
  WorkspaceMembersSettingsPage,
} from '../pages/settingsSubpages';

function detailWithRole(role: string): Record<string, unknown> {
  return {
    id: 'ws-1',
    name: 'Acme',
    slug: 'acme',
    logo_url: null,
    timezone: 'UTC',
    settings: { default_locale: 'en' },
    my_role: role,
    created_at: '2026-07-25T00:00:00Z',
    updated_at: '2026-07-25T00:00:00Z',
  };
}

const STATUSES = [
  { id: 's-1', name: 'Todo', category: 'todo' },
  { id: 's-2', name: 'In Progress', category: 'in_progress' },
];

function stubBackend(role: string): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/workspaces/by-slug/')) {
        return fakeResponse({ body: { data: detailWithRole(role) } });
      }
      if (url.includes('/statuses')) {
        return fakeResponse({ body: { data: STATUSES, next_cursor: null } });
      }
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as unknown as typeof fetch,
  );
  resetApiClient();
}

function renderPage(page: React.JSX.Element, role = 'admin'): void {
  stubBackend(role);
  render(
    <I18nProvider requested={null} systemLocales={[]}>
      <ToastProvider regionLabel="notifications">
        <MemoryRouter initialEntries={['/w/acme/settings']}>
          <WorkspaceProvider slug="acme">{page}</WorkspaceProvider>
        </MemoryRouter>
      </ToastProvider>
    </I18nProvider>,
  );
}

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  resetApiClient();
});

describe('设置子页 admin 门控(§6.12:guest/member 不可见)', () => {
  it('非 admin → permission denied 异常态(成员角色页)', async () => {
    renderPage(<WorkspaceMembersSettingsPage />, 'member');
    await waitFor(() => expect(screen.getByTestId('ws-settings-members-denied')).toBeInTheDocument());
    expect(screen.queryByTestId('ws-settings-members')).not.toBeInTheDocument();
  });

  it('非 admin → permission denied(危险操作页)', async () => {
    renderPage(<WorkspaceDangerSettingsPage />, 'guest');
    await waitFor(() => expect(screen.getByTestId('ws-settings-danger-denied')).toBeInTheDocument());
  });
});

describe('设置子页内容(admin+)', () => {
  it('成员角色页呈现角色矩阵区', async () => {
    renderPage(<WorkspaceMembersSettingsPage />, 'admin');
    await waitFor(() => expect(screen.getByTestId('ws-settings-members')).toBeInTheDocument());
  });

  it('审批策略页呈现待审批清单入口', async () => {
    renderPage(<WorkspaceApprovalsSettingsPage />, 'admin');
    await waitFor(() => expect(screen.getByTestId('ws-settings-approvals')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('ws-approvals-inbox-link')).toBeInTheDocument());
  });

  it('状态与字段页呈现工作区状态清单 + 标签/自定义字段入口', async () => {
    renderPage(<WorkspaceFieldsSettingsPage />, 'admin');
    await waitFor(() => expect(screen.getByTestId('ws-settings-fields')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('ws-field-status-s-1')).toBeInTheDocument());
    expect(screen.getByTestId('ws-field-status-s-2').textContent).toBe('In Progress');
    expect(screen.getByTestId('ws-fields-labels-link')).toBeInTheDocument();
    expect(screen.getByTestId('ws-fields-custom-link')).toBeInTheDocument();
  });

  it('危险操作页:owner 呈现 DangerZone;admin(非 owner)呈现仅 owner 提示', async () => {
    renderPage(<WorkspaceDangerSettingsPage />, 'admin');
    await waitFor(() => expect(screen.getByTestId('ws-settings-danger')).toBeInTheDocument());
    expect(screen.getByTestId('ws-settings-danger-owner-only')).toBeInTheDocument();
  });
});
