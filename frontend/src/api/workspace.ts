/**
 * 工作区 API — workspace.md §3(workspace 全量端点)。
 *
 * 消费后端 v0.4.0:列表(短项,无 settings)/ detail(全量,含 settings 与 my_role)/
 * by-slug(含历史 slug 重定向语义,W6)/ 创建 / 更新(浅合并 settings)/ 软删除(slug 二次确认)/ 恢复。
 * i18n 协商链第三级经 `fetchWorkspaceDefaultLocale` 读取(列表项不含 settings,须读 detail)。
 */
import type { MeshApiClient } from './client';
import type { ListEnvelope } from '../types/envelopes';
import { fetchAllPages } from './pagination';

/** 工作区 settings 已知键(workspace.md §2.3;按键浅合并写入) */
export interface WorkspaceSettings {
  default_locale?: string;
  default_theme?: string;
  invitation_max_uses_cap?: number;
  invitation_max_lifetime_hours_cap?: number;
  [key: string]: unknown;
}

/** 工作区角色(owner/admin/member/guest,auth.md RBAC) */
export type WorkspaceRole = 'owner' | 'admin' | 'member' | 'guest';

/** 列表项(GET /workspaces 短响应:不含 settings/timezone/updated_at) */
export interface WorkspaceSummary {
  id: string;
  name: string;
  slug: string;
  logo_url: string | null;
  my_role: WorkspaceRole;
  created_at: string;
}

/** 工作区全量对象(GET /workspaces/{id}、by-slug、创建/更新/恢复响应) */
export interface WorkspaceDetail {
  id: string;
  name: string;
  slug: string;
  logo_url: string | null;
  timezone: string;
  settings: WorkspaceSettings;
  my_role: WorkspaceRole;
  created_at: string;
  updated_at: string;
}

/** 创建工作区请求体(workspace.md §3.2) */
export interface CreateWorkspaceInput {
  name: string;
  slug: string;
  timezone?: string;
  logo_url?: string | null;
  settings?: WorkspaceSettings;
}

/** 更新工作区请求体(PATCH,字段均可选;settings 按键浅合并) */
export type WorkspacePatch = Partial<
  Pick<WorkspaceDetail, 'name' | 'slug' | 'logo_url' | 'timezone'>
> & { settings?: WorkspaceSettings };

/** 列表查询参数(游标分页,README §6.14) */
export interface WorkspaceListQuery {
  limit?: number;
  cursor?: string;
}

const WORKSPACES_PATH = '/api/v1/workspaces';

/**
 * 列出当前用户所属工作区(首页,游标分页)。
 * 注意:列表项为短响应,不含 settings(读 settings 须 getWorkspace detail)。
 */
export async function listWorkspaces(
  client: MeshApiClient,
  query?: WorkspaceListQuery,
): Promise<ListEnvelope<WorkspaceSummary>> {
  return client.list<WorkspaceSummary>(WORKSPACES_PATH, {
    query:
      query !== undefined
        ? { limit: query.limit, cursor: query.cursor }
        : undefined,
  });
}

/** 列出工作区(仅首页)—— 兼容旧调用方(i18n 协商链)。 */
export async function fetchWorkspaces(client: MeshApiClient): Promise<WorkspaceSummary[]> {
  const envelope = await listWorkspaces(client);
  return envelope.data;
}

/** 列出全部工作区(自动翻页,供切换器)。 */
export async function fetchAllWorkspaceSummaries(
  client: MeshApiClient,
): Promise<WorkspaceSummary[]> {
  return fetchAllPages<WorkspaceSummary>((cursor) =>
    listWorkspaces(client, cursor !== null ? { cursor } : undefined),
  );
}

/** 获取单个工作区(UUID,全量含 settings 与 my_role)。 */
export async function getWorkspace(
  client: MeshApiClient,
  workspaceId: string,
): Promise<WorkspaceDetail> {
  return client.request<WorkspaceDetail>('GET', `${WORKSPACES_PATH}/${workspaceId}`);
}

/**
 * 按 slug 解析工作区(W6):当前 slug 或历史 slug 均可解析;
 * 历史 slug 返回工作区的**当前**形态(响应 slug 为现行值,即重定向语义)。
 * 非成员/不存在/已删除一律 404 not_found(不泄漏存在性,§5.3)。
 */
export async function getWorkspaceBySlug(
  client: MeshApiClient,
  slug: string,
): Promise<WorkspaceDetail> {
  return client.request<WorkspaceDetail>(
    'GET',
    `${WORKSPACES_PATH}/by-slug/${encodeURIComponent(slug)}`,
  );
}

/** 创建工作区(创建者成为 owner;409 slug_taken)。 */
export async function createWorkspace(
  client: MeshApiClient,
  input: CreateWorkspaceInput,
): Promise<WorkspaceDetail> {
  return client.request<WorkspaceDetail>('POST', WORKSPACES_PATH, { body: input });
}

/** 更新工作区(admin+;settings 按键浅合并;409 slug_taken / 422 unsupported_locale 等)。 */
export async function updateWorkspace(
  client: MeshApiClient,
  workspaceId: string,
  patch: WorkspacePatch,
): Promise<WorkspaceDetail> {
  return client.request<WorkspaceDetail>('PATCH', `${WORKSPACES_PATH}/${workspaceId}`, {
    body: patch,
  });
}

/** 软删除工作区(owner;body 携带 confirm_slug 二次确认,§4.2)。 */
export async function deleteWorkspace(
  client: MeshApiClient,
  workspaceId: string,
  confirmSlug: string,
): Promise<{ status: string }> {
  return client.request<{ status: string }>('DELETE', `${WORKSPACES_PATH}/${workspaceId}`, {
    body: { confirm_slug: confirmSlug },
  });
}

/** 恢复软删除工作区(owner,保留期内)。 */
export async function restoreWorkspace(
  client: MeshApiClient,
  workspaceId: string,
): Promise<WorkspaceDetail> {
  return client.request<WorkspaceDetail>('POST', `${WORKSPACES_PATH}/${workspaceId}/restore`);
}

/**
 * 获取当前工作区的默认 locale(i18n 协商链第三级,§6.18)。
 *
 * 列表响应不含 settings,故取首个所属工作区后读其 detail;
 * 无工作区 / settings 未设 / detail 读取失败时返回 null(协商链跳过本级)。
 */
export async function fetchWorkspaceDefaultLocale(
  client: MeshApiClient,
): Promise<string | null> {
  const workspaces = await fetchWorkspaces(client);
  if (workspaces.length === 0) return null;
  try {
    const detail = await getWorkspace(client, workspaces[0].id);
    const locale = detail.settings?.default_locale;
    return typeof locale === 'string' && locale.length > 0 ? locale : null;
  } catch {
    // 静默降级:detail 不可达时协商链跳过工作区默认级(MES-24 既定降级语义)
    return null;
  }
}
