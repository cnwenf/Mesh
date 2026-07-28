/**
 * Squad 模块 API 调用(契约层,squad.md §3 / README §6.14 包络)。
 * 列表走 `list`(自动解 {data,next_cursor}),单对象走 `request`;
 * 所有函数为接收 (client, workspaceId, …) 的自由函数(与 issues/members 同构)。
 */
import type { MeshApiClient } from '../../api';
import { env } from '../../env';
import type {
  Assignment,
  AssignTaskBody,
  CreateSquadBody,
  CreateSubtasksBody,
  IssueAssignment,
  ListSquadsParams,
  MoveTaskStatusBody,
  PlanApproval,
  Squad,
  SquadActivity,
  SquadMember,
  SquadMemberInput,
  SquadMessage,
  SquadRole,
  SquadTask,
  SubtasksResult,
  TaskStatusView,
  UpdateSquadBody,
} from './types';

export interface Page<T> {
  readonly data: readonly T[];
  readonly nextCursor: string | null;
}

const squadsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/squads`;

const squadPath = (workspaceId: string, squadId: string): string =>
  `${squadsPath(workspaceId)}/${squadId}`;

const taskPath = (workspaceId: string, squadId: string, taskId: string): string =>
  `${squadPath(workspaceId, squadId)}/tasks/${taskId}`;

/** 小队级实时频道(squad.md §3.5):该小队全量事件(成员 / 任务 / 消息 / 动态)。 */
export function squadChannel(squadId: string): string {
  return `squad:${squadId}`;
}

/**
 * 承载某 issue 的活跃小队分派(§2.5 / §4.3-2);无活跃分派返回 null。
 * 供 issue 详情页头部单一责任主体徽章渲染。
 */
export async function getIssueAssignment(
  client: MeshApiClient,
  workspaceId: string,
  issueId: string,
): Promise<IssueAssignment | null> {
  return client.request<IssueAssignment | null>(
    'GET',
    `${squadsPath(workspaceId)}/assignments/by-issue/${issueId}`,
  );
}

/**
 * 任务编排 SSE 流的绝对 URL(§3.2 / §6.8)。EventSource 无法携带 Authorization
 * 头,故消费端以 fetch 流式读取并手动带 Bearer 凭证(见 stream.ts);此处仅构 URL。
 * 同源部署 apiBaseUrl 可为空。
 */
export function taskStreamUrl(
  workspaceId: string,
  squadId: string,
  taskId: string,
): string {
  return `${env.apiBaseUrl}${taskPath(workspaceId, squadId, taskId)}/stream`;
}

/* ---- 小队 CRUD(§3.1) ---- */

/** 小队列表(status / kind / q 过滤;游标分页)。 */
export async function listSquads(
  client: MeshApiClient,
  workspaceId: string,
  params: ListSquadsParams = {},
): Promise<Page<Squad>> {
  const envelope = await client.list<Squad>(squadsPath(workspaceId), {
    query: {
      status: params.status,
      kind: params.kind,
      q: params.q,
      limit: params.limit,
      cursor: params.cursor,
    },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export async function getSquad(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
): Promise<Squad> {
  return client.request<Squad>('GET', squadPath(workspaceId, squadId));
}

/** 创建小队(201);members 可为空,leader 由服务端依 leader_mode 推导。 */
export async function createSquad(
  client: MeshApiClient,
  workspaceId: string,
  body: CreateSquadBody,
): Promise<Squad> {
  return client.request<Squad>('POST', squadsPath(workspaceId), { body });
}

export async function updateSquad(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  body: UpdateSquadBody,
): Promise<Squad> {
  return client.request<Squad>('PATCH', squadPath(workspaceId, squadId), { body });
}

/** 归档(软;active 分派级联取消,§6.9)。 */
export async function archiveSquad(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
): Promise<Squad> {
  return client.request<Squad>('POST', `${squadPath(workspaceId, squadId)}/archive`);
}

export async function restoreSquad(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
): Promise<Squad> {
  return client.request<Squad>('POST', `${squadPath(workspaceId, squadId)}/restore`);
}

/* ---- 成员管理(§3.2) ---- */

export async function listMembers(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
): Promise<readonly SquadMember[]> {
  const envelope = await client.list<SquadMember>(`${squadPath(workspaceId, squadId)}/members`);
  return envelope.data;
}

/** 批量添加成员(返回更新后的小队快照)。 */
export async function addMembers(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  members: readonly SquadMemberInput[],
): Promise<Squad> {
  return client.request<Squad>('POST', `${squadPath(workspaceId, squadId)}/members`, {
    body: { members },
  });
}

/** 变更角色(返回更新后的小队快照)。 */
export async function changeRole(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  memberId: string,
  role: SquadRole,
): Promise<Squad> {
  return client.request<Squad>(
    'PATCH',
    `${squadPath(workspaceId, squadId)}/members/${memberId}`,
    { body: { role } },
  );
}

/** 移除成员(leader 离队无替补 → 根任务 blocked,§6.9)。 */
export async function removeMember(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  memberId: string,
): Promise<unknown> {
  return client.request<unknown>(
    'DELETE',
    `${squadPath(workspaceId, squadId)}/members/${memberId}`,
  );
}

/* ---- 编排与任务(§3.3 / §3.4) ---- */

/** 显式小队分派端点(§6.9;202,根任务异步入队;改派永非 no-op)。 */
export async function assignTask(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  body: AssignTaskBody,
): Promise<Assignment> {
  return client.request<Assignment>('POST', `${squadPath(workspaceId, squadId)}/tasks`, { body });
}

export async function listTasks(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  params: { status?: string; limit?: number } = {},
): Promise<readonly SquadTask[]> {
  const envelope = await client.list<SquadTask>(`${squadPath(workspaceId, squadId)}/tasks`, {
    query: { status: params.status, limit: params.limit },
  });
  return envelope.data;
}

export async function getTask(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  taskId: string,
): Promise<SquadTask> {
  return client.request<SquadTask>('GET', taskPath(workspaceId, squadId, taskId));
}

/** 拆解树(含 children 递归 + progress 聚合,§5.3)。 */
export async function getTaskTree(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  taskId: string,
): Promise<SquadTask> {
  return client.request<SquadTask>('GET', `${taskPath(workspaceId, squadId, taskId)}/tree`);
}

/** 轻量状态查询(轮询用;§3.1)。 */
export async function getTaskStatus(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  taskId: string,
): Promise<TaskStatusView> {
  return client.request<TaskStatusView>('GET', `${taskPath(workspaceId, squadId, taskId)}/status`);
}

/** 提交拆解方案(201;require_plan_approval → awaiting_approval,经统一审批 §6.10)。 */
export async function createSubtasks(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  taskId: string,
  body: CreateSubtasksBody,
): Promise<SubtasksResult> {
  return client.request<SubtasksResult>(
    'POST',
    `${taskPath(workspaceId, squadId, taskId)}/subtasks`,
    { body },
  );
}

/** 批准拆解方案(§6.10;批准后经 execution.enqueue 入队,§6.11)。 */
export async function approvePlan(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  taskId: string,
  comment?: string,
): Promise<PlanApproval> {
  return client.request<PlanApproval>(
    'POST',
    `${taskPath(workspaceId, squadId, taskId)}/plan/approve`,
    { body: { comment } },
  );
}

export async function rejectPlan(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  taskId: string,
  comment?: string,
): Promise<PlanApproval> {
  return client.request<PlanApproval>(
    'POST',
    `${taskPath(workspaceId, squadId, taskId)}/plan/reject`,
    { body: { comment } },
  );
}

export async function dispatchTask(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  taskId: string,
): Promise<{ dispatched: number; task_id: string }> {
  return client.request<{ dispatched: number; task_id: string }>(
    'POST',
    `${taskPath(workspaceId, squadId, taskId)}/dispatch`,
  );
}

export async function cancelTask(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  taskId: string,
  reason?: string,
): Promise<unknown> {
  return client.request<unknown>('POST', `${taskPath(workspaceId, squadId, taskId)}/cancel`, {
    body: { reason },
  });
}

/**
 * 看板人工改状(§4.2):PATCH .../tasks/{taskId}/status。服务端按状态机校验迁移,
 * 非法迁移 → 409 {error:{code:"conflict"}};成功返回更新后的任务。
 */
export async function moveTaskStatus(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  taskId: string,
  body: MoveTaskStatusBody,
): Promise<SquadTask> {
  return client.request<SquadTask>('PATCH', `${taskPath(workspaceId, squadId, taskId)}/status`, {
    body,
  });
}

/* ---- 消息 / 动态(§3.5) ---- */

export async function listMessages(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  params: { taskId?: string; kind?: string; limit?: number } = {},
): Promise<readonly SquadMessage[]> {
  const envelope = await client.list<SquadMessage>(`${squadPath(workspaceId, squadId)}/messages`, {
    query: { task_id: params.taskId, kind: params.kind, limit: params.limit },
  });
  return envelope.data;
}

export async function sendMessage(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  body: {
    task_id?: string;
    kind: string;
    body_markdown: string;
    pinned?: boolean;
  },
): Promise<SquadMessage> {
  return client.request<SquadMessage>('POST', `${squadPath(workspaceId, squadId)}/messages`, {
    body,
  });
}

export async function listActivity(
  client: MeshApiClient,
  workspaceId: string,
  squadId: string,
  params: { taskId?: string; action?: string; limit?: number } = {},
): Promise<readonly SquadActivity[]> {
  const envelope = await client.list<SquadActivity>(
    `${squadPath(workspaceId, squadId)}/activity`,
    { query: { task_id: params.taskId, action: params.action, limit: params.limit } },
  );
  return envelope.data;
}
