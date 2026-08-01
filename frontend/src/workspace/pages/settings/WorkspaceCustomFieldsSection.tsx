/**
 * 工作区设置 → 自定义字段(/w/:slug/settings/custom-fields,label-property.md §4.1)。
 * 复用 features/labels 的 CustomFieldsPanel(工作区级)。
 */
import { getApiClient } from '../../../api/instance';
import { SettingsSection } from '../../../design';
import { CustomFieldsPanel } from '../../../features/labels';
import { useT } from '../../../i18n';
import { useWorkspace } from '../../WorkspaceProvider';

export function WorkspaceCustomFieldsSection(): React.JSX.Element {
  const { workspace } = useWorkspace();
  const t = useT();
  if (workspace === null) return <></>;
  return (
    <SettingsSection title={t('fields.pageTitle')}>
      <CustomFieldsPanel client={getApiClient()} workspaceId={workspace.id} />
    </SettingsSection>
  );
}
