/**
 * 工作区 API — workspace.md(workspace 列表与设置读取)。
 *
 * 阶段 2 前端仅需读取当前工作区的 `settings.default_locale`
 * 以接通 i18n 协商链第三级(§6.18)。
 */
import type { MeshApiClient } from './client';

/** 工作区 settings 已知键(workspace.md §2.3) */
export interface WorkspaceSettings {
  default_locale?: string;
  default_theme?: string;
  [key: string]: unknown;
}

/** 工作区实体(列表响应元素,仅前端需要的字段) */
export interface WorkspaceSummary {
  id: string;
  name: string;
  slug: string;
  settings: WorkspaceSettings;
}

const WORKSPACES_PATH = '/api/v1/workspaces';

/**
 * 列出当前用户所属工作区(workspace.md W1)。
 * 返回首个可达工作区的 settings.default_locale 供协商链使用。
 */
export async function fetchWorkspaces(client: MeshApiClient): Promise<WorkspaceSummary[]> {
  const envelope = await client.list<WorkspaceSummary>(WORKSPACES_PATH);
  return envelope.data;
}

/**
 * 获取当前工作区的默认 locale(协商链第三级,§6.18)。
 * 无工作区或 settings 未设 default_locale 时返回 null(协商链跳过本级)。
 */
export async function fetchWorkspaceDefaultLocale(
  client: MeshApiClient,
): Promise<string | null> {
  const workspaces = await fetchWorkspaces(client);
  if (workspaces.length === 0) return null;
  const locale = workspaces[0].settings?.default_locale;
  return typeof locale === 'string' && locale.length > 0 ? locale : null;
}
