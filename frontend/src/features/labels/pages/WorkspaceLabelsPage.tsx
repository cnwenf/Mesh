/**
 * 工作区设置 → 标签管理页(/w/:workspaceSlug/settings/labels,label-property.md §4.1)。
 * WorkspaceGate 处理 loading/not_found/error;非 admin 呈现无权限态(后端 403 兜底)。
 */
import { getApiClient } from '../../../api/instance';
import { useT } from '../../../i18n';
import { useWorkspace, WorkspaceGate } from '../../../workspace/WorkspaceProvider';
import { LabelsPanel } from '../LabelsPanel';
import '../labels.css';

export function WorkspaceLabelsPage(): React.JSX.Element {
  return (
    <WorkspaceGate>
      <WorkspaceLabelsSections />
    </WorkspaceGate>
  );
}

function WorkspaceLabelsSections(): React.JSX.Element {
  const { workspace, isAdmin } = useWorkspace();
  const t = useT();
  if (workspace === null) return <></>;
  if (!isAdmin) {
    return (
      <div className="mesh-ws-settings" data-testid="ws-labels-denied">
        <h2>{t('state.permissionTitle')}</h2>
        <p>{t('state.permissionDescription')}</p>
        <p>{t('state.permissionHint')}</p>
      </div>
    );
  }
  return (
    <div className="mesh-ws-settings mesh-labels-page" data-testid="ws-labels-page">
      <h1>{t('labels.pageTitle')}</h1>
      <LabelsPanel client={getApiClient()} workspaceId={workspace.id} />
    </div>
  );
}
