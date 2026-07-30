/**
 * 工作区设置 → 邀请(/w/:slug/settings/invitations,workspace.md §4.2)。
 * 邀请创建面板(邮箱/链接双模式 + caps)+ 待处理邀请列表。
 */
import { useState } from 'react';
import { SettingsSection } from '../../../design';
import { useT } from '../../../i18n';
import { InvitationCreatePanel } from '../../InvitationCreatePanel';
import { InvitationList } from '../../InvitationList';
import { useWorkspace } from '../../WorkspaceProvider';

export function WorkspaceInvitationsSection(): React.JSX.Element {
  const { workspace } = useWorkspace();
  const t = useT();
  const [refreshTick, setRefreshTick] = useState(0);

  if (workspace === null) return <></>;

  const caps = {
    maxUsesCap:
      typeof workspace.settings.invitation_max_uses_cap === 'number'
        ? workspace.settings.invitation_max_uses_cap
        : 100,
    lifetimeHoursCap:
      typeof workspace.settings.invitation_max_lifetime_hours_cap === 'number'
        ? workspace.settings.invitation_max_lifetime_hours_cap
        : 720,
  };

  return (
    <SettingsSection title={t('invitations.sectionTitle')}>
      <InvitationCreatePanel
        workspaceId={workspace.id}
        caps={caps}
        onCreated={() => setRefreshTick((tick) => tick + 1)}
      />
      <InvitationList workspaceId={workspace.id} refreshSignal={refreshTick} />
    </SettingsSection>
  );
}
