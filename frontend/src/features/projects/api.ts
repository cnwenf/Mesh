/**
 * 项目模块 API 调用(契约层,project.md §3 / README §6.14 包络)。
 * 复用 MeshApiClient:列表走 `list`(自动解 {data,next_cursor}),单对象走 `request`;
 * 乐观并发经 RequestOptions.ifMatch(§6.14 If-Match: <updated_at>,409 收敛见 optimistic.ts)。
 */
import type { MeshApiClient } from '../../api';
import type {
  AddProjectMemberBody,
  AddProjectUpdateBody,
  CreateCycleBody,
  CreateMilestoneBody,
  CreateProjectBody,
  CreateTemplateBody,
  Cycle,
  CyclePatchResult,
  InstantiateResult,
  InstantiateTemplateBody,
  ListCyclesParams,
  ListMilestonesParams,
  ListProjectsParams,
  ListUpdatesParams,
  Milestone,
  ProjectDetail,
  ProjectMemberEntry,
  ProjectSummary,
  ProjectTemplate,
  ProjectUpdateEntry,
  UpdateCycleBody,
  UpdateMilestoneBody,
  UpdateProjectBody,
  UpdateProjectMemberRoleBody,
  UpdateTemplateBody,
} from './types';

export interface Page<T> {
  readonly data: readonly T[];
  readonly nextCursor: string | null;
}

const workspaceProjectsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/projects`;

const projectPath = (projectId: string): string => `/api/v1/projects/${projectId}`;

const workspaceCyclesPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/cycles`;

/** 详情页实时频道(§3.5/§4.5):该项目全量事件(含 private)。 */
export function projectChannel(projectId: string): string {
  return `project:${projectId}`;
}

/** 工作区列表级实时频道(§3.5):仅 public 项目的列表级事件。 */
export function workspaceProjectsChannel(workspaceId: string): string {
  return `workspace:${workspaceId}:projects`;
}

/** 项目列表(§3.1 GET /workspaces/{ws}/projects)。 */
export async function listProjects(
  client: MeshApiClient,
  workspaceId: string,
  params: ListProjectsParams = {},
): Promise<Page<ProjectSummary>> {
  const envelope = await client.list<ProjectSummary>(workspaceProjectsPath(workspaceId), {
    query: {
      status: params.status,
      visibility: params.visibility,
      archived: params.archived,
      mine: params.mine,
      lead_member_id: params.lead_member_id,
      limit: params.limit,
      cursor: params.cursor,
    },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** 项目详情(含内嵌 milestones,§3.2)。 */
export async function getProject(client: MeshApiClient, projectId: string): Promise<ProjectDetail> {
  return client.request<ProjectDetail>('GET', projectPath(projectId));
}

/** 创建项目(§3.1);409 project_key_taken / project_name_taken;400 validation_error。 */
export async function createProject(
  client: MeshApiClient,
  workspaceId: string,
  body: CreateProjectBody,
): Promise<ProjectSummary> {
  return client.request<ProjectSummary>('POST', workspaceProjectsPath(workspaceId), { body });
}

/** 设置 PATCH(三态:省略保持 / null 清空;ifMatch 启用乐观并发,409 conflict 收敛)。 */
export async function updateProject(
  client: MeshApiClient,
  projectId: string,
  body: UpdateProjectBody,
  ifMatch?: string,
): Promise<ProjectSummary> {
  return client.request<ProjectSummary>('PATCH', projectPath(projectId), { body, ifMatch });
}

/** 删除项目(软删除;前缀永久保留不可复用,§6.3)。 */
export async function deleteProject(
  client: MeshApiClient,
  projectId: string,
): Promise<{ id: string; deleted: boolean }> {
  return client.request<{ id: string; deleted: boolean }>('DELETE', projectPath(projectId));
}

export async function archiveProject(
  client: MeshApiClient,
  projectId: string,
): Promise<ProjectSummary> {
  return client.request<ProjectSummary>('POST', `${projectPath(projectId)}/archive`);
}

export async function unarchiveProject(
  client: MeshApiClient,
  projectId: string,
): Promise<ProjectSummary> {
  return client.request<ProjectSummary>('POST', `${projectPath(projectId)}/unarchive`);
}

/** 健康度/状态留痕(§1.2.2):至少一个字段;同时回写 projects.health/status。 */
export async function addProjectUpdate(
  client: MeshApiClient,
  projectId: string,
  body: AddProjectUpdateBody,
): Promise<ProjectUpdateEntry> {
  return client.request<ProjectUpdateEntry>('POST', `${projectPath(projectId)}/updates`, { body });
}

export async function listProjectUpdates(
  client: MeshApiClient,
  projectId: string,
  params: ListUpdatesParams = {},
): Promise<Page<ProjectUpdateEntry>> {
  const envelope = await client.list<ProjectUpdateEntry>(`${projectPath(projectId)}/updates`, {
    query: { limit: params.limit, cursor: params.cursor },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export async function listMilestones(
  client: MeshApiClient,
  projectId: string,
  params: ListMilestonesParams = {},
): Promise<Page<Milestone>> {
  const envelope = await client.list<Milestone>(`${projectPath(projectId)}/milestones`, {
    query: { state: params.state, limit: params.limit, cursor: params.cursor },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export async function createMilestone(
  client: MeshApiClient,
  projectId: string,
  body: CreateMilestoneBody,
): Promise<Milestone> {
  return client.request<Milestone>('POST', `${projectPath(projectId)}/milestones`, { body });
}

export async function updateMilestone(
  client: MeshApiClient,
  milestoneId: string,
  body: UpdateMilestoneBody,
): Promise<Milestone> {
  return client.request<Milestone>('PATCH', `/api/v1/milestones/${milestoneId}`, { body });
}

export async function deleteMilestone(
  client: MeshApiClient,
  milestoneId: string,
): Promise<{ id: string; deleted: boolean }> {
  return client.request<{ id: string; deleted: boolean }>(
    'DELETE',
    `/api/v1/milestones/${milestoneId}`,
  );
}

export async function listCycles(
  client: MeshApiClient,
  workspaceId: string,
  params: ListCyclesParams = {},
): Promise<Page<Cycle>> {
  const envelope = await client.list<Cycle>(workspaceCyclesPath(workspaceId), {
    query: {
      state: params.state,
      project_id: params.project_id,
      limit: params.limit,
      cursor: params.cursor,
    },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** 创建周期;400 当 ends_at < starts_at(§5.1)。 */
export async function createCycle(
  client: MeshApiClient,
  workspaceId: string,
  body: CreateCycleBody,
): Promise<Cycle> {
  return client.request<Cycle>('POST', workspaceCyclesPath(workspaceId), { body });
}

/** 更新周期;完成 auto_roll 周期时响应 data 附带 next_cycle(§1.2.5)。 */
export async function updateCycle(
  client: MeshApiClient,
  cycleId: string,
  body: UpdateCycleBody,
): Promise<CyclePatchResult> {
  return client.request<CyclePatchResult>('PATCH', `/api/v1/cycles/${cycleId}`, { body });
}

export async function listProjectMembers(
  client: MeshApiClient,
  projectId: string,
  params: { limit?: number; cursor?: string } = {},
): Promise<Page<ProjectMemberEntry>> {
  const envelope = await client.list<ProjectMemberEntry>(`${projectPath(projectId)}/members`, {
    query: { limit: params.limit, cursor: params.cursor },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** 添加项目成员;409 project_member_exists。 */
export async function addProjectMember(
  client: MeshApiClient,
  projectId: string,
  body: AddProjectMemberBody,
): Promise<ProjectMemberEntry> {
  return client.request<ProjectMemberEntry>('POST', `${projectPath(projectId)}/members`, { body });
}

export async function updateProjectMemberRole(
  client: MeshApiClient,
  projectId: string,
  memberId: string,
  body: UpdateProjectMemberRoleBody,
): Promise<ProjectMemberEntry> {
  return client.request<ProjectMemberEntry>('PATCH', `${projectPath(projectId)}/members/${memberId}`, {
    body,
  });
}

export async function removeProjectMember(
  client: MeshApiClient,
  projectId: string,
  memberId: string,
): Promise<{ id: string; deleted: boolean }> {
  return client.request<{ id: string; deleted: boolean }>(
    'DELETE',
    `${projectPath(projectId)}/members/${memberId}`,
  );
}

/* ---- 项目模板(§3.2b) ---- */

export async function listProjectTemplates(
  client: MeshApiClient,
  workspaceId: string,
): Promise<Page<ProjectTemplate>> {
  const envelope = await client.list<ProjectTemplate>(
    `/api/v1/workspaces/${workspaceId}/project-templates`,
  );
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** 创建模板;409 template_name_taken。 */
export async function createProjectTemplate(
  client: MeshApiClient,
  workspaceId: string,
  body: CreateTemplateBody,
): Promise<ProjectTemplate> {
  return client.request<ProjectTemplate>(
    'POST',
    `/api/v1/workspaces/${workspaceId}/project-templates`,
    { body },
  );
}

export async function updateProjectTemplate(
  client: MeshApiClient,
  templateId: string,
  body: UpdateTemplateBody,
): Promise<ProjectTemplate> {
  return client.request<ProjectTemplate>('PATCH', `/api/v1/project-templates/${templateId}`, {
    body,
  });
}

export async function deleteProjectTemplate(
  client: MeshApiClient,
  templateId: string,
): Promise<{ id: string; deleted: boolean }> {
  return client.request<{ id: string; deleted: boolean }>(
    'DELETE',
    `/api/v1/project-templates/${templateId}`,
  );
}

/** 由模板实例化项目(§3.2b):返回新项目 + 克隆出的里程碑/周期 id。 */
export async function instantiateProjectTemplate(
  client: MeshApiClient,
  templateId: string,
  body: InstantiateTemplateBody,
): Promise<InstantiateResult> {
  return client.request<InstantiateResult>(
    'POST',
    `/api/v1/project-templates/${templateId}/instantiate`,
    { body },
  );
}
