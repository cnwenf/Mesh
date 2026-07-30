/**
 * WorkspaceSettingsLayout — 工作区设置二级导航外壳(design-quality.md §4.4 / §3.2)。
 *
 * 复用 design SettingsLayout;导航分组按角色计算:
 * - 整体设置页经 WorkspaceGate + 非 admin「无权限」态门控(呈现级,后端 403 兜底);
 * - 危险区仅 owner 可见:非 owner 经 `hidden` 跳过渲染(权限不可见,§3.2,而非禁用)。
 * 子页经 children(<Outlet />)呈现,各自读 useWorkspace/useParams。
 */
import type { ReactNode } from 'react';
import { SettingsLayout } from '../design';
import type { SettingsNavGroup } from '../design';
import { useT } from '../i18n';

export interface WorkspaceSettingsLayoutProps {
  /** 当前工作区 slug(构造子路由) */
  slug: string;
  /** 是否 owner(决定危险区可见性) */
  isOwner: boolean;
  /** 内容列(<Outlet />) */
  children: ReactNode;
}

export function WorkspaceSettingsLayout(props: WorkspaceSettingsLayoutProps): React.JSX.Element {
  const { slug, isOwner, children } = props;
  const t = useT();
  const base = `/w/${slug}/settings`;

  const groups: ReadonlyArray<SettingsNavGroup> = [
    {
      label: t('workspaceSettings.groupWorkspace'),
      items: [
        { key: 'general', label: t('workspace.basicSection'), to: `${base}/general`, icon: 'settings' },
        { key: 'invitations', label: t('invitations.sectionTitle'), to: `${base}/invitations`, icon: 'send' },
        { key: 'roles', label: t('roles.sectionTitle'), to: `${base}/roles`, icon: 'user' },
      ],
    },
    {
      label: t('workspaceSettings.groupCustomize'),
      items: [
        { key: 'labels', label: t('labels.pageTitle'), to: `${base}/labels`, icon: 'folder' },
        { key: 'custom-fields', label: t('fields.pageTitle'), to: `${base}/custom-fields`, icon: 'filter' },
        { key: 'data', label: t('dataJobs.page.title'), to: `${base}/data`, icon: 'external' },
      ],
    },
    {
      label: t('workspaceSettings.groupAdvanced'),
      items: [
        { key: 'tokens', label: t('tokens.title'), to: `${base}/tokens`, icon: 'edit' },
        { key: 'audit', label: t('audit.title'), to: `${base}/audit`, icon: 'info' },
        {
          key: 'danger',
          label: t('danger.sectionTitle'),
          to: `${base}/danger`,
          icon: 'warning',
          hidden: !isOwner,
        },
      ],
    },
  ];

  return (
    <SettingsLayout
      title={t('workspace.settingsTitle')}
      groups={groups}
      navLabel={t('workspaceSettings.navLabel')}
    >
      {children}
    </SettingsLayout>
  );
}
