/**
 * 工作区设置 → 成员与角色(/w/:slug/settings/roles,auth.md §2.7)。
 * 角色能力矩阵(owner/admin/member/guest × 资源权限)。
 */
import { SettingsSection } from '../../../design';
import { useT } from '../../../i18n';
import { RolesMatrix } from '../../RolesMatrix';
import { useWorkspace } from '../../WorkspaceProvider';

export function WorkspaceRolesSection(): React.JSX.Element {
  const { workspace } = useWorkspace();
  const t = useT();
  if (workspace === null) return <></>;
  return (
    <SettingsSection title={t('roles.sectionTitle')}>
      <RolesMatrix workspaceId={workspace.id} />
    </SettingsSection>
  );
}
