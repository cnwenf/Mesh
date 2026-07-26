/**
 * 标签 / 自定义字段设置子页测试:WorkspaceGate 上下文装载、admin 门禁、面板接通。
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import { ThemeProvider, ToastProvider } from '../../../design';
import { I18nProvider } from '../../../i18n';
import { WorkspaceProvider } from '../../../workspace/WorkspaceProvider';
import { WorkspaceCustomFieldsPage } from '../pages/WorkspaceCustomFieldsPage';
import { WorkspaceLabelsPage } from '../pages/WorkspaceLabelsPage';

const WORKSPACE_DETAIL = {
  id: 'ws-1',
  name: 'Acme',
  slug: 'acme',
  logo_url: null,
  timezone: 'UTC',
  settings: { default_locale: 'en' },
  my_role: 'owner',
  created_at: '2026-07-26T00:00:00Z',
  updated_at: '2026-07-26T00:00:00Z',
};

function stubWorkspaceClient(role: string): MeshApiClient {
  return {
    list: vi.fn().mockResolvedValue({ data: [], next_cursor: null }),
    request: vi.fn().mockImplementation((method: string, path: string) => {
      if (path.includes('/by-slug/')) {
        return Promise.resolve({ ...WORKSPACE_DETAIL, my_role: role });
      }
      if (method === 'GET') return Promise.resolve(WORKSPACE_DETAIL);
      return Promise.resolve({});
    }),
  } as unknown as MeshApiClient;
}

function renderPage(path: string, role: string): ReturnType<typeof render> {
  const client = stubWorkspaceClient(role);
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
          <ToastProvider regionLabel="notifications">
            <WorkspaceProvider slug="acme" client={client as never}>
              <Routes>
                <Route path="/w/:workspaceSlug/settings/labels" element={<WorkspaceLabelsPage />} />
                <Route
                  path="/w/:workspaceSlug/settings/custom-fields"
                  element={<WorkspaceCustomFieldsPage />}
                />
              </Routes>
            </WorkspaceProvider>
          </ToastProvider>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe('WorkspaceLabelsPage', () => {
  it('renders the labels panel for admins', async () => {
    renderPage('/w/acme/settings/labels', 'admin');
    expect(await screen.findByTestId('ws-labels-page')).toBeTruthy();
    expect(screen.getByTestId('labels-panel')).toBeTruthy();
  });

  it('shows the permission block for plain members', async () => {
    renderPage('/w/acme/settings/labels', 'member');
    expect(await screen.findByTestId('ws-labels-denied')).toBeTruthy();
    expect(screen.queryByTestId('labels-panel')).toBeNull();
  });
});

describe('WorkspaceCustomFieldsPage', () => {
  it('renders the custom fields panel for owners', async () => {
    renderPage('/w/acme/settings/custom-fields', 'owner');
    expect(await screen.findByTestId('ws-fields-page')).toBeTruthy();
    expect(screen.getByTestId('custom-fields-panel')).toBeTruthy();
  });

  it('shows the permission block for guests', async () => {
    renderPage('/w/acme/settings/custom-fields', 'guest');
    expect(await screen.findByTestId('ws-fields-denied')).toBeTruthy();
  });
});
