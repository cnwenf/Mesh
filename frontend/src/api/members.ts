/**
 * 成员名册 API — member.md §3 消费契约。
 *
 * 后端现状(v0.4.0):仅 `PATCH /workspaces/{id}/members/{member_id}`(角色变更)已就绪;
 * 名册列表 `GET /workspaces/{id}/members` 随 member 全量增量(MES-14)提供——
 * 本模块按 member.md §3.1/§3.2 契约声明,端点缺失时调用方优雅降级(§4 角色呈现)。
 */
import type { MeshApiClient } from './client';
import type { ListEnvelope } from '../types/envelopes';
import type { WorkspaceRole } from './workspace';

/** 成员类型(统一名册,README §6.1) */
export type MemberType = 'human' | 'agent';

/** 名册状态(member.md §2.2) */
export type MemberStatus = 'active' | 'disabled' | 'removed';

/** 名册条目(member.md §3.2 响应字段;显示名由服务端解析,§6.1) */
export interface MemberSummary {
  id: string;
  member_type: MemberType;
  role: WorkspaceRole;
  status: MemberStatus;
  display_name?: string;
  joined_at?: string | null;
}

/** 列表查询参数(游标分页) */
export interface MemberListQuery {
  limit?: number;
  cursor?: string;
}

/** 409 错误码:最后一个 owner / agent 不可为 owner(member.md §3.3) */
export const ERROR_LAST_OWNER = 'last_owner';
export const ERROR_AGENT_OWNER_NOT_ALLOWED = 'agent_owner_not_allowed';

function membersPath(workspaceId: string): string {
  return `/api/v1/workspaces/${workspaceId}/members`;
}

/**
 * 列出工作区名册(人类与 agent 同册;需成员资格)。
 * 端点随 member 全量增量就绪;此前调用将以 404/405 失败,调用方降级呈现。
 */
export async function listMembers(
  client: MeshApiClient,
  workspaceId: string,
  query?: MemberListQuery,
): Promise<ListEnvelope<MemberSummary>> {
  return client.list<MemberSummary>(membersPath(workspaceId), {
    query:
      query !== undefined ? { limit: query.limit, cursor: query.cursor } : undefined,
  });
}

/**
 * 变更成员角色(admin+;v0.4.0 已就绪)。
 * 409 last_owner(最后一个 owner 降级)/ 409 agent_owner_not_allowed(agent 提为 owner)由服务端强校验。
 */
export async function updateMemberRole(
  client: MeshApiClient,
  workspaceId: string,
  memberId: string,
  role: WorkspaceRole,
): Promise<MemberSummary> {
  return client.request<MemberSummary>('PATCH', `${membersPath(workspaceId)}/${memberId}`, {
    body: { role },
  });
}
