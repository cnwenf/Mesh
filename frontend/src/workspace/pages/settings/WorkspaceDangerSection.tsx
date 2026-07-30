/**
 * 工作区设置 → 危险区(/w/:slug/settings/danger,workspace.md §5.3,仅 owner)。
 * 独立分页 + danger 语义分区(§3.2:危险区与普通偏好拉开距离);归档/删除经
 * DangerZone 内置 slug 二次确认。owner 可见性由导航 hidden 门控(权限不可见)。
 */
import { SettingsSection } from '../../../design';
import { useT } from '../../../i18n';
import { DangerZone } from '../../DangerZone';
import { useWorkspace } from '../../WorkspaceProvider';

export function WorkspaceDangerSection(): React.JSX.Element {
  const { workspace, isOwner } = useWorkspace();
  const t = useT();
  if (workspace === null) return <></>;
  // 直达非 owner:导航已隐藏本页;若仍抵达(深链),呈现无权限态(后端 403 兜底)。
  if (!isOwner) {
    return (
      <div data-testid="ws-danger-denied">
        <h2>{t('state.permissionTitle')}</h2>
        <p>{t('state.permissionDescription')}</p>
      </div>
    );
  }
  return (
    <SettingsSection title={t('danger.sectionTitle')} tone="danger">
      <DangerZone workspaceId={workspace.id} workspaceSlug={workspace.slug} />
    </SettingsSection>
  );
}
