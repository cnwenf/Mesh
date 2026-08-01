/**
 * 工作区设置薄包装分区覆盖:Roles/Labels/CustomFields/Data/Tokens/Audit/Invitations/Danger。
 * 这些分区仅「读 useWorkspace → 空值早退 → 以 SettingsSection 包裹业务面板」,
 * 故将重型面板 mock 为桩,专注验证包装逻辑(传入 workspaceId、空工作区早退)。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
import { WorkspaceGeneralSection } from '../pages/settings/WorkspaceGeneralSection';
import { WorkspaceInvitationsSection } from '../pages/settings/WorkspaceInvitationsSection';
import { WorkspaceLabelsSection } from '../pages/settings/WorkspaceLabelsSection';
import { WorkspaceRolesSection } from '../pages/settings/WorkspaceRolesSection';
import { WorkspaceTokensSection } from '../pages/settings/WorkspaceTokensSection';

vi.mock('../RolesMatrix', () => ({ RolesMatrix: () => <div data-testid="roles-panel" /> }));
// 邀请面板桩:透传 caps(验证上限取自 settings)并暴露 onCreated 触发器(验证刷新联动)。
vi.mock('../InvitationCreatePanel', () => ({
  InvitationCreatePanel: (props: {
    caps: { maxUsesCap: number; lifetimeHoursCap: number };
    onCreated(): void;
  }) => (
    <div data-testid="invite-create">
      <span data-testid="invite-caps">
        {`${props.caps.maxUsesCap}/${props.caps.lifetimeHoursCap}`}
      </span>
      <button type="button" data-testid="invite-create-trigger" onClick={props.onCreated}>
        create
      </button>
    </div>
  ),
}));
// 列表桩:暴露 refreshSignal(创建成功后应递增触发重拉)。
vi.mock('../InvitationList', () => ({
  InvitationList: (props: { refreshSignal?: number }) => (
    <div data-testid="invite-list" data-refresh={String(props.refreshSignal ?? 0)} />
  ),
}));
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

  it('invitations 分区渲染创建面板与列表(默认上限 100/720)', async () => {
    renderSection(<WorkspaceInvitationsSection />, readyClient());
    await waitFor(() => expect(screen.getByTestId('invite-create')).toBeTruthy());
    expect(screen.getByTestId('invite-list')).toBeTruthy();
    expect(screen.getByTestId('invite-caps').textContent).toBe('100/720');
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

  it('general 分区:工作区不可达 → 早退渲染空(不崩溃)', async () => {
    const { container } = renderSection(<WorkspaceGeneralSection />, nullClient());
    // not_found 后 workspace 为 null,general 分区返回空片段(不渲染基本信息表单)
    await waitFor(() => expect(container.querySelector('[data-testid="ws-basic-info"]')).toBeNull());
  });
});

describe('invitations 分区上限与刷新联动(§4.2/§2.3)', () => {
  function capsClient(settings: Record<string, unknown>) {
    const detail = { ...DETAIL, settings: { ...DETAIL.settings, ...settings } };
    return {
      request: async (_method: string, path: string) => {
        if (path.includes('/by-slug/')) return { ...detail };
        return {};
      },
      list: async () => ({ data: [] }),
    };
  }

  it('上限取自 settings 数值 caps → 透传创建面板', async () => {
    renderSection(
      <WorkspaceInvitationsSection />,
      capsClient({ invitation_max_uses_cap: 50, invitation_max_lifetime_hours_cap: 100 }),
    );
    await waitFor(() => expect(screen.getByTestId('invite-caps').textContent).toBe('50/100'));
  });

  it('创建成功(onCreated)→ refreshSignal 递增触发列表重拉', async () => {
    const user = userEvent.setup();
    renderSection(<WorkspaceInvitationsSection />, readyClient());
    await waitFor(() => expect(screen.getByTestId('invite-create')).toBeTruthy());
    expect(screen.getByTestId('invite-list').getAttribute('data-refresh')).toBe('0');

    await user.click(screen.getByTestId('invite-create-trigger'));

    await waitFor(() =>
      expect(screen.getByTestId('invite-list').getAttribute('data-refresh')).toBe('1'),
    );
  });
});
