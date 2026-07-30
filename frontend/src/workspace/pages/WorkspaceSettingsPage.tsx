/**
 * 工作区设置页(/w/:workspaceSlug/settings,workspace.md §4.1/§4.2,design-quality.md §4.4)。
 *
 * SettingsLayout 二级导航 + 内容列;内容按子路由分页(经 <Outlet />):
 *   index → Navigate replace → general
 *   general(基本信息 + G11 默认主题 + dirty/save + 离开守卫)、invitations、roles、
 *   labels、custom-fields、data、tokens、audit、danger(owner)。
 * 门控:WorkspaceGate(loading/not_found/error)+ 非 admin「无权限」态(呈现级,后端 403 兜底);
 * 危险区仅 owner 可见(导航 hidden,权限不可见)。各子页自包含(读 useWorkspace/useParams)。
 */
import { Outlet } from 'react-router';
import { useT } from '../../i18n';
import { useWorkspace, WorkspaceGate } from '../WorkspaceProvider';
import { WorkspaceSettingsLayout } from '../WorkspaceSettingsLayout';

export function WorkspaceSettingsPage(): React.JSX.Element {
  return (
    <WorkspaceGate>
      <SettingsHost />
    </WorkspaceGate>
  );
}

function SettingsHost(): React.JSX.Element {
  const { workspace, isAdmin, isOwner } = useWorkspace();
  const t = useT();

  if (workspace === null) return <></>;
  if (!isAdmin) {
    return (
      <div className="mesh-ws-settings" data-testid="ws-settings-denied">
        <h2>{t('state.permissionTitle')}</h2>
        <p>{t('state.permissionDescription')}</p>
        <p>{t('state.permissionHint')}</p>
      </div>
    );
  }

  return (
    <div className="mesh-page" data-testid="ws-settings">
      <WorkspaceSettingsLayout slug={workspace.slug} isOwner={isOwner}>
        <Outlet />
      </WorkspaceSettingsLayout>
    </div>
  );
}
