/**
 * 工作区设置 → 自定义字段页(/w/:workspaceSlug/settings/custom-fields,label-property.md §4.1)。
 */
import { getApiClient } from '../../../api/instance';
import { useT } from '../../../i18n';
import { useWorkspace, WorkspaceGate } from '../../../workspace/WorkspaceProvider';
import { CustomFieldsPanel } from '../CustomFieldsPanel';
import '../labels.css';

export function WorkspaceCustomFieldsPage(): React.JSX.Element {
  return (
    <WorkspaceGate>
      <WorkspaceCustomFieldsSections />
    </WorkspaceGate>
  );
}

function WorkspaceCustomFieldsSections(): React.JSX.Element {
  const { workspace, isAdmin } = useWorkspace();
  const t = useT();
  if (workspace === null) return <></>;
  if (!isAdmin) {
    return (
      <div className="mesh-ws-settings" data-testid="ws-fields-denied">
        <h2>{t('state.permissionTitle')}</h2>
        <p>{t('state.permissionDescription')}</p>
        <p>{t('state.permissionHint')}</p>
      </div>
    );
  }
  return (
    <div className="mesh-ws-settings mesh-labels-page" data-testid="ws-fields-page">
      <h1>{t('fields.pageTitle')}</h1>
      <CustomFieldsPanel client={getApiClient()} workspaceId={workspace.id} />
    </div>
  );
}
