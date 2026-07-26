/**
 * 看板视图 API 调用(契约层,kanban.md §3.1 独立端点子集 / README §6.14 包络)。
 * 定义层切片:CRUD + 配置 PATCH + WIP 配置 + 侧栏排序;
 * 投影执行(GET /views/{id}/issues)与 move 命令属 issue 耦合增量,此处不接。
 */
import type { MeshApiClient, RequestOptions } from '../../api';
import type {
  CreateViewBody,
  UpdateViewBody,
  View,
  WipBody,
} from './types';

export interface Page<T> {
  readonly data: readonly T[];
  readonly nextCursor: string | null;
}

const workspaceViewsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/views`;

const viewPath = (viewId: string): string => `/api/v1/views/${viewId}`;

/** 视图详情实时频道(kanban §3.5 topic view:{view_id})。 */
export function viewChannel(viewId: string): string {
  return `view:${viewId}`;
}

/** 工作区视图列表实时频道(侧栏增量刷新)。 */
export function workspaceViewsChannel(workspaceId: string): string {
  return `workspace:${workspaceId}:views`;
}

/** 列出可见视图(§3.1 GET /workspaces/{ws}/views,游标分页)。 */
export async function listViews(
  client: MeshApiClient,
  workspaceId: string,
  params: { projectId?: string; limit?: number; cursor?: string } = {},
): Promise<Page<View>> {
  const envelope = await client.list<View>(workspaceViewsPath(workspaceId), {
    query: { project_id: params.projectId, limit: params.limit, cursor: params.cursor },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** 创建视图(§3.2 POST /workspaces/{ws}/views)。 */
export async function createView(
  client: MeshApiClient,
  workspaceId: string,
  body: CreateViewBody,
): Promise<View> {
  return client.request<View>('POST', workspaceViewsPath(workspaceId), { body });
}

/** 获取视图配置(§3.1 GET /views/{id},无工作区前缀路径)。 */
export async function getView(client: MeshApiClient, viewId: string): Promise<View> {
  return client.request<View>('GET', viewPath(viewId));
}

/** 更新视图配置(§3.1 PATCH /views/{id};If-Match 乐观并发,§6.14)。 */
export async function updateView(
  client: MeshApiClient,
  viewId: string,
  body: UpdateViewBody,
  options: { ifMatch?: string } = {},
): Promise<View> {
  const requestOptions: RequestOptions = { body };
  if (options.ifMatch !== undefined) {
    requestOptions.ifMatch = options.ifMatch;
  }
  return client.request<View>('PATCH', viewPath(viewId), requestOptions);
}

/** 删除视图(§3.1 DELETE /views/{id} → 204)。 */
export async function deleteView(client: MeshApiClient, viewId: string): Promise<void> {
  await client.request<null>('DELETE', viewPath(viewId));
}

/** 复制视图(§3.1 POST /views/{id}/duplicate;新 owner = 当前成员)。 */
export async function duplicateView(client: MeshApiClient, viewId: string): Promise<View> {
  return client.request<View>('POST', `${viewPath(viewId)}/duplicate`);
}

/** 设置列 WIP 限制(§3.2 PATCH /views/{id}/wip;limit=null 移除)。 */
export async function setViewWip(
  client: MeshApiClient,
  viewId: string,
  body: WipBody,
): Promise<View> {
  return client.request<View>('PATCH', `${viewPath(viewId)}/wip`, { body });
}

/** 调整视图侧栏顺序(§3.1 PATCH /workspaces/{ws}/views/reorder)。 */
export async function reorderViews(
  client: MeshApiClient,
  workspaceId: string,
  viewIds: readonly string[],
): Promise<readonly View[]> {
  return client.request<readonly View[]>('PATCH', `${workspaceViewsPath(workspaceId)}/reorder`, {
    body: { view_ids: [...viewIds] },
  });
}
