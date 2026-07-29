/**
 * 邀请 API — workspace.md §3(创建/列出/撤销/预览/接受)。
 *
 * 消费后端 v0.4.0:
 * - 创建(邮箱批量 / 链接模式)201 返回 data 数组,含一次性 `invite_link`(明文 token 仅此一次返回);
 * - 接受失败 422 `invitation_invalid`,reason 枚举在 `error.details.reason`(§3.3);
 * - 预览为公开端点,恒 200,有效性在响应体(valid:false + reason)。
 */
import type { MeshApiClient } from './client';
import type { ListEnvelope } from '../types/envelopes';

/** 邀请链接生命周期状态(workspace.md §4.4;无 pending/accepted) */
export type InvitationStatus = 'active' | 'revoked' | 'expired' | 'exhausted';

/** 邀请不可用原因(preview 与 accept 失败共用枚举,§3.2/§3.3) */
export type InvitationRejectReason = 'not_found' | 'expired' | 'exhausted' | 'revoked';

/** 邀请可预设角色(不可邀请为 owner,§2.3) */
export type InvitationRole = 'admin' | 'member' | 'guest';

/** 邀请实体(列表项;`invite_link` 仅创建响应携带) */
export interface Invitation {
  id: string;
  email: string | null;
  role: string;
  status: InvitationStatus;
  max_uses: number;
  used_count: number;
  expires_at: string;
  token_prefix: string;
  invited_by: string;
  created_at: string;
  invite_link?: string;
}

/** 创建邀请请求体(邮箱批量与链接模式二选一,§3.2) */
export interface CreateInvitationInput {
  /** 定向邮箱批量(≤50);缺省 = 链接模式(一条多次使用链接) */
  emails?: string[];
  role?: InvitationRole;
  /** 缺省 10 次;显式值受 settings.invitation_max_uses_cap(默认 100)约束 */
  max_uses?: number;
  /** 缺省 168 小时(7 天);显式值受 invitation_max_lifetime_hours_cap(默认 720)约束 */
  expires_in_hours?: number;
}

/** 邀请预览(公开端点;有效时仅暴露有限字段,不含内部 id) */
export type InvitationPreview =
  | {
      valid: true;
      workspace_name: string;
      workspace_logo_url: string | null;
      role: string;
      expires_at: string;
      /**
       * 展示偏好公开字段(theme.md §2.2/§3.1,workspace.md §3.1 MES-76 H2):
       * 未登录邀请接受页主题协商链第 2 级读取 `default_theme`;与工作区名同暴露面,
       * 不开放完整 workspace detail。
       */
      appearance?: { default_theme?: string };
    }
  | { valid: false; reason: InvitationRejectReason };

/** 接受邀请成功响应(新入册或既有名册条目 + 所属工作区) */
export interface AcceptInvitationResult {
  member: { id: string; role: string; status: string };
  workspace: { id: string; name: string; slug: string };
}

/** 列表查询参数(游标分页) */
export interface InvitationListQuery {
  limit?: number;
  cursor?: string;
}

/** 422 错误码:邀请不可用 / 超过工作区可配置上限(§3.3) */
export const ERROR_INVITATION_INVALID = 'invitation_invalid';
export const ERROR_INVITATION_LIMITS_EXCEEDED = 'invitation_limits_exceeded';

function invitationsPath(workspaceId: string): string {
  return `/api/v1/workspaces/${workspaceId}/invitations`;
}

/**
 * 创建邀请(admin+;201)。邮箱模式为每个邮箱生成一条邀请;链接模式生成一条多次使用链接。
 * 响应 data 数组中 `invite_link` 仅此一次返回(数据库仅存 token_hash,§2.3)。
 */
export async function createInvitations(
  client: MeshApiClient,
  workspaceId: string,
  input: CreateInvitationInput,
): Promise<Invitation[]> {
  return client.request<Invitation[]>('POST', invitationsPath(workspaceId), { body: input });
}

/** 列出邀请(admin+;游标分页;status 含惰性 expired 判定)。 */
export async function listInvitations(
  client: MeshApiClient,
  workspaceId: string,
  query?: InvitationListQuery,
): Promise<ListEnvelope<Invitation>> {
  return client.list<Invitation>(invitationsPath(workspaceId), {
    query:
      query !== undefined ? { limit: query.limit, cursor: query.cursor } : undefined,
  });
}

/** 撤销邀请(admin+;非 active 链接 → 409 conflict,details.status 为当前状态)。 */
export async function revokeInvitation(
  client: MeshApiClient,
  workspaceId: string,
  invitationId: string,
): Promise<Invitation> {
  return client.request<Invitation>(
    'DELETE',
    `${invitationsPath(workspaceId)}/${invitationId}`,
  );
}

/**
 * 预览邀请(公开端点,无需登录;恒 200)。
 * 有效 → workspace_name/role/expires_at;无效 → valid:false + reason 枚举。
 */
export async function previewInvitation(
  client: MeshApiClient,
  token: string,
): Promise<InvitationPreview> {
  return client.request<InvitationPreview>('GET', '/api/v1/invitations/preview', {
    query: { token },
  });
}

/**
 * 接受邀请(需登录)。成功返回名册条目与工作区;重加入/重复接受为 no-op 同形响应。
 * 失败抛 MeshApiError(422 invitation_invalid,details.reason ∈ 四枚举,§3.3)。
 */
export async function acceptInvitation(
  client: MeshApiClient,
  token: string,
): Promise<AcceptInvitationResult> {
  return client.request<AcceptInvitationResult>('POST', '/api/v1/invitations/accept', {
    body: { token },
  });
}
