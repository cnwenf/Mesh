/**
 * 工作区设置 → 审计日志(/w/:slug/settings/audit,auth.md §4.4,admin+)。
 * 复用 features/auth 的 AuditSettings(自带标题分组,只读),直接呈现。
 */
import { getApiClient } from '../../../api/instance';
import { AuditSettings } from '../../../features/auth';
import { useWorkspace } from '../../WorkspaceProvider';

export function WorkspaceAuditSection(): React.JSX.Element {
  const { workspace } = useWorkspace();
  if (workspace === null) return <></>;
  return <AuditSettings client={getApiClient()} workspaceId={workspace.id} />;
}
