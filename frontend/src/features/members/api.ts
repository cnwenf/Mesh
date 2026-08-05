/**
 * 成员名册 API 调用(契约层,member.md §3.1 / README §6.14 包络)。
 * 复用 MeshApiClient:列表走 `list`(自动解 {data,next_cursor}),单对象走 `request`。
 */
import type { MeshApiClient } from '../../api';
import type {
  MemberDetail,
  MemberRole,
  MemberSummary,
  MeResponse,
  Membership,
  ProjectAccess,
} from './types';

const USERS_ME_PATH = '/api/v1/users/me';

const workspaceMembersPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/members`;

const memberPath = (workspaceId: string, memberId: string): string =>
  `${workspaceMembersPath(workspaceId)}/${memberId}`;

/** 当前登录用户及其在各工作区的成员身份(member.md §3.1 GET /users/me)。 */
export async function fetchMe(client: MeshApiClient): Promise<MeResponse> {
  return client.request<MeResponse>('GET', USERS_ME_PATH);
}

export interface UpdateOwnProfileBody {
  readonly display_name?: string;
  readonly avatar_url?: string;
}

/** 当前用户资料写入(auth.md §3.1 / member.md §3.1)。 */
export async function updateOwnProfile(
  client: MeshApiClient,
  body: UpdateOwnProfileBody,
): Promise<MeResponse['user']> {
  return client.request<MeResponse['user']>('PATCH', USERS_ME_PATH, { body });
}

/** 选取名册目标工作区:取首个成员身份(MES-24 接通工作区选择器前的单一归属口径)。 */
export function activeWorkspace(memberships: readonly Membership[]): Membership | null {
  return memberships.length > 0 ? memberships[0] : null;
}

export interface ListMembersParams {
  readonly memberType?: 'all' | 'human' | 'agent';
  readonly status?: 'default' | 'all' | 'active' | 'disabled' | 'removed';
  readonly q?: string;
  readonly limit?: number;
  readonly cursor?: string;
}

/** 名册列表(人与 agent 同册;member_type 为「仅 Agent」筛选投影,同一路由)。 */
export async function listMembers(
  client: MeshApiClient,
  workspaceId: string,
  params: ListMembersParams = {},
): Promise<{ data: MemberSummary[]; nextCursor: string | null }> {
  const envelope = await client.list<MemberSummary>(workspaceMembersPath(workspaceId), {
    query: {
      member_type: params.memberType,
      status: params.status,
      q: params.q,
      limit: params.limit,
      cursor: params.cursor,
    },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export async function getMember(
  client: MeshApiClient,
  workspaceId: string,
  memberId: string,
): Promise<MemberDetail> {
  return client.request<MemberDetail>('GET', memberPath(workspaceId, memberId));
}

export interface UpdateMemberBody {
  readonly role?: MemberRole;
  readonly status?: 'active' | 'disabled';
  readonly display_override?: string | null;
}

export async function updateMember(
  client: MeshApiClient,
  workspaceId: string,
  memberId: string,
  body: UpdateMemberBody,
): Promise<MemberSummary> {
  return client.request<MemberSummary>('PATCH', memberPath(workspaceId, memberId), { body });
}

export async function removeMember(
  client: MeshApiClient,
  workspaceId: string,
  memberId: string,
  reassignTo?: string,
): Promise<{ removed: boolean; reassigned_issues: number }> {
  return client.request<{ removed: boolean; reassigned_issues: number }>(
    'DELETE',
    memberPath(workspaceId, memberId),
    { query: { reassign_to: reassignTo } },
  );
}

export async function reassignIssues(
  client: MeshApiClient,
  workspaceId: string,
  fromMemberId: string,
  toMemberId: string,
): Promise<{ reassigned_issues: number }> {
  return client.request<{ reassigned_issues: number }>(
    'POST',
    `${workspaceMembersPath(workspaceId)}/reassign`,
    { body: { from_member_id: fromMemberId, to_member_id: toMemberId } },
  );
}

/** 可加入名册的 agent(agents 表落地前恒为空列表;入口保留占位态)。 */
export async function listAvailableAgents(
  client: MeshApiClient,
  workspaceId: string,
): Promise<MemberSummary[]> {
  const envelope = await client.list<MemberSummary>(
    `/api/v1/workspaces/${workspaceId}/agents/available`,
  );
  return envelope.data;
}

/** 邀请人类(衔接 workspace.md 邀请;返回邀请链接条目)。 */
export async function createInvitation(
  client: MeshApiClient,
  workspaceId: string,
  email: string,
  role: MemberRole,
): Promise<{ invite_link: string }> {
  const envelope = await client.request<Array<{ invite_link: string }>>(
    'POST',
    `/api/v1/workspaces/${workspaceId}/invitations`,
    { body: { emails: [email], role } },
  );
  return envelope[0];
}

export async function listProjectAccess(
  client: MeshApiClient,
  workspaceId: string,
  memberId: string,
): Promise<ProjectAccess[]> {
  const envelope = await client.list<ProjectAccess>(
    `${memberPath(workspaceId, memberId)}/project-access`,
  );
  return envelope.data;
}
