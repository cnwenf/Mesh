/**
 * Agent API 调用(契约层,agent.md §3.1 / README §6.14 包络)。
 * 创建入口唯一为成员名册页的「+ 新建 Agent」向导(README §6.12,T35)——
 * 本模块不提供第二创建入口;列表是名册「仅 Agent」投影的数据源之一。
 */
import type { MeshApiClient } from '../../api';
import type {
  AgentConfigVersion,
  AgentDetail,
  AgentModelConfig,
  AgentSummary,
  AgentVisibility,
} from './types';

const workspaceAgentsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/agents`;

const agentPath = (workspaceId: string, agentId: string): string =>
  `${workspaceAgentsPath(workspaceId)}/${agentId}`;

/** 实时频道(README §6.7):agent 域事件走 workspace 级频道, presence 走 agent 级频道。 */
export const workspaceAgentsChannel = (workspaceId: string): string =>
  `workspace:${workspaceId}:agents`;

export const agentPresenceChannel = (agentId: string): string => `agent:${agentId}:presence`;

export interface ListAgentsParams {
  readonly status?: 'all' | 'active' | 'paused' | 'disabled' | 'archived';
  readonly visibility?: 'all' | AgentVisibility;
  readonly ownerId?: string;
  readonly q?: string;
  readonly limit?: number;
  readonly cursor?: string;
}

export async function listAgents(
  client: MeshApiClient,
  workspaceId: string,
  params: ListAgentsParams = {},
): Promise<{ data: AgentSummary[]; nextCursor: string | null }> {
  const envelope = await client.list<AgentSummary>(workspaceAgentsPath(workspaceId), {
    query: {
      status: params.status,
      visibility: params.visibility,
      owner_id: params.ownerId,
      q: params.q,
      limit: params.limit,
      cursor: params.cursor,
    },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export async function getAgent(
  client: MeshApiClient,
  workspaceId: string,
  agentId: string,
): Promise<AgentDetail> {
  return client.request<AgentDetail>('GET', agentPath(workspaceId, agentId));
}

export interface CreateAgentBody {
  readonly name: string;
  readonly avatar_url?: string | null;
  readonly role_tag?: string | null;
  readonly slug?: string | null;
  readonly bio?: string | null;
  readonly visibility?: AgentVisibility;
  readonly system_instructions?: string | null;
  readonly model_config?: AgentModelConfig;
  readonly trigger_on_assign?: boolean;
}

export async function createAgent(
  client: MeshApiClient,
  workspaceId: string,
  body: CreateAgentBody,
): Promise<AgentDetail> {
  return client.request<AgentDetail>('POST', workspaceAgentsPath(workspaceId), { body });
}

export interface PatchAgentBody {
  readonly name?: string;
  readonly avatar_url?: string | null;
  readonly role_tag?: string | null;
  readonly slug?: string | null;
  readonly bio?: string | null;
  readonly visibility?: AgentVisibility;
  readonly trigger_on_assign?: boolean;
}

export async function updateAgent(
  client: MeshApiClient,
  workspaceId: string,
  agentId: string,
  body: PatchAgentBody,
): Promise<AgentSummary> {
  return client.request<AgentSummary>('PATCH', agentPath(workspaceId, agentId), { body });
}

export interface UpdateConfigBody {
  readonly model_config?: AgentModelConfig;
  readonly system_instructions?: string | null;
  readonly change_summary?: string;
}

/** 更新配置 → 生成新的不可变配置版本(agent.md §2.7)。 */
export async function updateAgentConfig(
  client: MeshApiClient,
  workspaceId: string,
  agentId: string,
  body: UpdateConfigBody,
): Promise<AgentDetail> {
  return client.request<AgentDetail>('PATCH', `${agentPath(workspaceId, agentId)}/config`, {
    body,
  });
}

export async function listConfigVersions(
  client: MeshApiClient,
  workspaceId: string,
  agentId: string,
  params: { readonly limit?: number; readonly cursor?: string } = {},
): Promise<{ data: AgentConfigVersion[]; nextCursor: string | null }> {
  const envelope = await client.list<AgentConfigVersion>(
    `${agentPath(workspaceId, agentId)}/config-versions`,
    { query: { limit: params.limit, cursor: params.cursor } },
  );
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** 回滚 = 复制旧快照为NEW版本(不可变历史,agent.md §2.7)。 */
export async function rollbackConfig(
  client: MeshApiClient,
  workspaceId: string,
  agentId: string,
  versionId: string,
): Promise<AgentDetail> {
  return client.request<AgentDetail>(
    'POST',
    `${agentPath(workspaceId, agentId)}/config-versions/${versionId}:rollback`,
  );
}

export type AgentLifecycleVerb =
  | 'pause'
  | 'resume'
  | 'disable'
  | 'enable'
  | 'archive'
  | 'restore';

/** 生命周期动作端点(:verb 后缀,agent.md §3.1 / §4.8 状态机)。 */
export async function transitionAgentLifecycle(
  client: MeshApiClient,
  workspaceId: string,
  agentId: string,
  verb: AgentLifecycleVerb,
  body?: { readonly reason?: string; readonly in_flight_policy?: 'finish_current' | 'cancel_current' },
): Promise<AgentSummary> {
  return client.request<AgentSummary>('POST', `${agentPath(workspaceId, agentId)}:${verb}`, {
    body: body ?? {},
  });
}

export async function transferAgent(
  client: MeshApiClient,
  workspaceId: string,
  agentId: string,
  newOwnerUserId: string,
): Promise<AgentSummary> {
  return client.request<AgentSummary>('POST', `${agentPath(workspaceId, agentId)}:transfer`, {
    body: { new_owner_user_id: newOwnerUserId },
  });
}

export async function deleteAgent(
  client: MeshApiClient,
  workspaceId: string,
  agentId: string,
): Promise<void> {
  await client.request<void>('DELETE', agentPath(workspaceId, agentId));
}
