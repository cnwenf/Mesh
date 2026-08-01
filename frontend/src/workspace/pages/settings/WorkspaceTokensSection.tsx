/**
 * 工作区设置 → API Tokens(/w/:slug/settings/tokens,auth.md §4.3)。
 * 复用 features/auth 的 ApiTokensSettings(自带标题分组,明文仅一次),直接呈现。
 */
import { getApiClient } from '../../../api/instance';
import { ApiTokensSettings } from '../../../features/auth';
import { useWorkspace } from '../../WorkspaceProvider';

export function WorkspaceTokensSection(): React.JSX.Element {
  const { workspace } = useWorkspace();
  if (workspace === null) return <></>;
  return <ApiTokensSettings client={getApiClient()} workspaceId={workspace.id} />;
}
