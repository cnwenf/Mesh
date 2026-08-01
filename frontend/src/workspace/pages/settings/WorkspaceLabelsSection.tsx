/**
 * 工作区设置 → 标签(/w/:slug/settings/labels,label-property.md §4.1)。
 * 复用 features/labels 的 LabelsPanel(工作区级)。
 */
import { getApiClient } from '../../../api/instance';
import { SettingsSection } from '../../../design';
import { LabelsPanel } from '../../../features/labels';
import { useT } from '../../../i18n';
import { useWorkspace } from '../../WorkspaceProvider';

export function WorkspaceLabelsSection(): React.JSX.Element {
  const { workspace } = useWorkspace();
  const t = useT();
  if (workspace === null) return <></>;
  return (
    <SettingsSection title={t('labels.pageTitle')}>
      <LabelsPanel client={getApiClient()} workspaceId={workspace.id} />
    </SettingsSection>
  );
}
