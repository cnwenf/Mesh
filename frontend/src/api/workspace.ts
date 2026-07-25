/**
 * 工作区 API — workspace.md(workspace 列表与设置读取)。
 *
 * 阶段 2 前端仅需读取当前工作区的 `settings.default_locale`
 * 以接通 i18n 协商链第三级(§6.18)。
 *
 * 注意:列表接口(GET /workspaces)的 list_view 不含 settings 字段(workspace.md §3.2),
 * 必须经单对象接口(GET /workspaces/{id})获取完整工作区(含 settings)。
 */
import type { MeshApiClient } from './client';

/** 工作区 settings 已知键(workspace.md §2.3) */
export interface WorkspaceSettings {
  default_locale?: string;
  default_theme?: string;
  [key: string]: unknown;
}

/** 工作区列表响应元素(list_view,不含 settings) */
export interface WorkspaceListItem {
  id: string;
  name: string;
  slug: string;
  logo_url: string | null;
  my_role: string;
  created_at: string;
}

/** 工作区完整对象(单对象 GET 响应,含 settings) */
export interface WorkspaceDetail {
  id: string;
  name: string;
  slug: string;
  logo_url: string | null;
  timezone: string | null;
  settings: WorkspaceSettings;
  my_role: string;
  created_at: string;
  updated_at: string;
}

const WORKSPACES_PATH = '/api/v1/workspaces';

/**
 * 列出当前用户所属工作区(workspace.md W1,列表视图不含 settings)。
 */
export async function fetchWorkspaces(client: MeshApiClient): Promise<WorkspaceListItem[]> {
  const envelope = await client.list<WorkspaceListItem>(WORKSPACES_PATH);
  return envelope.data;
}

/**
 * 获取单个工作区完整信息(workspace.md §3.2,含 settings)。
 */
export async function fetchWorkspaceById(
  client: MeshApiClient,
  workspaceId: string,
): Promise<WorkspaceDetail> {
  return client.request<WorkspaceDetail>('GET', `${WORKSPACES_PATH}/${workspaceId}`);
}

/**
 * 获取当前工作区的默认 locale(协商链第三级,§6.18)。
 * 先经列表接口获取首个工作区 id,再经单对象接口读取 settings.default_locale。
 * 无工作区或 settings 未设 default_locale 时返回 null(协商链跳过本级)。
 */
export async function fetchWorkspaceDefaultLocale(
  client: MeshApiClient,
): Promise<string | null> {
  const workspaces = await fetchWorkspaces(client);
  if (workspaces.length === 0) return null;
  const detail = await fetchWorkspaceById(client, workspaces[0].id);
  const locale = detail.settings?.default_locale;
  return typeof locale === 'string' && locale.length > 0 ? locale : null;
}
