/**
 * 工作区设置 → 数据管理(/w/:slug/settings/data,import-export.md §4.1)。
 * 复用 features/data-jobs 的 DataManagementPage(自包含:读 useWorkspace/useParams)。
 */
import { DataManagementPage } from '../../../features/data-jobs/DataManagementPage';

export function WorkspaceDataSection(): React.JSX.Element {
  return <DataManagementPage />;
}
