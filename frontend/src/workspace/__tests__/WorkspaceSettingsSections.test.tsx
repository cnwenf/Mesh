/**
 * 工作区设置薄包装分区覆盖:Roles/Labels/CustomFields/Data/Tokens/Audit/Invitations/Danger。
 * 这些分区仅「读 useWorkspace → 空值早退 → 以 SettingsSection 包裹业务面板」,
 * 故将重型面板 mock 为桩,专注验证包装逻辑(传入 workspaceId、空工作区早退)。
 */
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import { MeshApiError } from '../../api/errors';
import { ThemeProvider, ToastProvider } from '../../design';
import { I18nProvider } from '../../i18n';
import { WorkspaceProvider } from '../WorkspaceProvider';
import { WorkspaceAuditSection } from '../pages/settings/WorkspaceAuditSection';
import { WorkspaceCustomFieldsSection } from '../pages/settings/WorkspaceCustomFieldsSection';
import { WorkspaceDangerSection } from '../pages/settings/WorkspaceDangerSection';
import { WorkspaceDataSection } from '../pages/settings/WorkspaceDataSection';
import { WorkspaceInvitationsSection } from '../pages/settings/WorkspaceInvitationsSection';
import { WorkspaceLabelsSection } from '../pages/settings/WorkspaceLabelsSection';
import { WorkspaceRolesSection } from '../pages/settings/WorkspaceRolesSection';
import { WorkspaceTokensSection } from '../pages/settings/WorkspaceTokensSection';

vi.mock('../RolesMatrix', () => ({ RolesMatrix: () => <div data-testid="roles-panel" /> }));
vi.mock('../InvitationCreatePanel', () => ({
  InvitationCreatePanel: () => <div data-testid="invite-create" />,
}));
vi.mock('../InvitationList', () => ({ InvitationList: () => <div data-testid="invite-list" /> }));
vi.mock('../../features/labels', () => ({
  LabelsPanel: () => <div data-testid="labels-panel" />,
  CustomFieldsPanel: () => <div data-testid="fields-panel" />,
}));
vi.mock('../../features/auth', () => ({
  ApiTokensSettings: () => <div data-testid="tokens-panel" />,
  AuditSettings: () => <div data-testid="audit-panel" />,
}));
vi.mock('../../features/data-jobs/DataManagementPage', () => ({
  DataManagementPage: () => <div data-testid="data-panel" />,
}));

const DETAIL = {
  id: 'ws-1',
  name: 'Acme',
  slug: 'acme',
  logo_url: null,
  timezone: 'UTC',
  settings: { default_locale: 'en', default_theme: 'system' },
  my_role: 'owner',
  created_at: '2026-07-25T00:00:00Z',
  updated_at: '2026-07-25T00:00:00Z',
};

function readyClient() {
  return {
    request: async (_method: string, path: string) => {
      if (path.includes('/by-slug/')) return { ...DETAIL };
      return {};
    },
    list: async () => ({ data: [] }),
  };
}

function nullClient() {
  const fail = async (): Promise<never> => {
    throw new MeshApiError({ status: 404, code: 'not_found', message: 'x' });
  };
  return { request: fail, list: fail };
}

function renderSection(ui: React.JSX.Element, client: unknown): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={['/w/acme/settings']}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
          <ToastProvider regionLabel="notifications">
            <WorkspaceProvider slug="acme" client={client as never}>
              {ui}
            </WorkspaceProvider>
          </ToastProvider>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe('工作区设置薄包装分区(就绪态渲染面板)', () => {
  const cases: ReadonlyArray<{ name: string; ui: React.JSX.Element; panel: string }> = [
    { name: 'roles', ui: <WorkspaceRolesSection />, panel: 'roles-panel' },
    { name: 'labels', ui: <WorkspaceLabelsSection />, panel: 'labels-panel' },
    { name: 'custom-fields', ui: <WorkspaceCustomFieldsSection />, panel: 'fields-panel' },
    { name: 'data', ui: <WorkspaceDataSection />, panel: 'data-panel' },
    { name: 'tokens', ui: <WorkspaceTokensSection />, panel: 'tokens-panel' },
    { name: 'audit', ui: <WorkspaceAuditSection />, panel: 'audit-panel' },
  ];

  for (const testCase of cases) {
    it(`${testCase.name} 分区包裹业务面板`, async () => {
      renderSection(testCase.ui, readyClient());
      await waitFor(() => expect(screen.getByTestId(testCase.panel)).toBeTruthy());
    });
  }

  it('invitations 分区渲染创建面板与列表', async () => {
    renderSection(<WorkspaceInvitationsSection />, readyClient());
    await waitFor(() => expect(screen.getByTestId('invite-create')).toBeTruthy());
    expect(screen.getByTestId('invite-list')).toBeTruthy();
  });

  it('danger 分区(owner)渲染 DangerZone', async () => {
    renderSection(<WorkspaceDangerSection />, readyClient());
    await waitFor(() => expect(screen.getByTestId('danger-zone')).toBeTruthy());
  });
});

describe('工作区设置薄包装分区(空工作区早退)', () => {
  it('工作区不可达 → 分区早退渲染空(不崩溃)', async () => {
    const { container } = renderSection(<WorkspaceRolesSection />, nullClient());
    // not_found 后 workspace 为 null,分区返回空片段
    await waitFor(() => expect(container.querySelector('[data-testid="roles-panel"]')).toBeNull());
  });
});
