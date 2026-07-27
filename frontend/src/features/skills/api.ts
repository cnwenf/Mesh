/**
 * 技能 API 调用(契约层,skill.md §3.1 / README §6.14 包络)。
 * 技能库 / 详情 / 版本 / 导入审批 / 安装 / 绑定 / 市场一览。
 */
import type { MeshApiClient } from '../../api';
import type {
  AgentSkillBinding,
  AgentSkillRow,
  CapabilityDeclaration,
  ImportTask,
  MarketplaceEntry,
  SkillDetail,
  SkillInstallation,
  SkillSummary,
  SkillVersion,
} from './types';

const skillsPath = (workspaceId: string): string => `/api/v1/workspaces/${workspaceId}/skills`;
const skillPath = (workspaceId: string, skillId: string): string =>
  `${skillsPath(workspaceId)}/${skillId}`;
const installationsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/skill-installations`;
const agentSkillsPath = (workspaceId: string, agentId: string): string =>
  `/api/v1/workspaces/${workspaceId}/agents/${agentId}/skills`;

/** 实时频道(README §6.7):技能域事件走 workspace 级 skills 频道。 */
export const workspaceSkillsChannel = (workspaceId: string): string =>
  `workspace:${workspaceId}:skills`;

export interface ListSkillsParams {
  readonly status?: string;
  readonly source_type?: string;
  readonly q?: string;
  readonly limit?: number;
  readonly cursor?: string;
}

export async function listSkills(
  client: MeshApiClient,
  workspaceId: string,
  params: ListSkillsParams = {},
): Promise<{ data: SkillSummary[]; nextCursor: string | null }> {
  const envelope = await client.list<SkillSummary>(skillsPath(workspaceId), {
    query: {
      status: params.status,
      source_type: params.source_type,
      q: params.q,
      limit: params.limit,
      cursor: params.cursor,
    },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export async function getSkill(
  client: MeshApiClient,
  workspaceId: string,
  skillId: string,
): Promise<SkillDetail> {
  return client.request<SkillDetail>('GET', skillPath(workspaceId, skillId));
}

export interface CreateSkillBody {
  readonly name: string;
  readonly slug?: string;
  readonly summary: string;
  readonly tags?: string[];
  readonly icon?: string | null;
  readonly required_capabilities?: CapabilityDeclaration[];
}

export async function createSkill(
  client: MeshApiClient,
  workspaceId: string,
  body: CreateSkillBody,
): Promise<SkillSummary> {
  return client.request<SkillSummary>('POST', skillsPath(workspaceId), { body });
}

export interface PatchSkillBody {
  readonly name?: string;
  readonly summary?: string;
  readonly tags?: string[];
  readonly icon?: string | null;
  readonly status?: string;
}

export async function updateSkill(
  client: MeshApiClient,
  workspaceId: string,
  skillId: string,
  body: PatchSkillBody,
): Promise<SkillSummary> {
  return client.request<SkillSummary>('PATCH', skillPath(workspaceId, skillId), { body });
}

export async function deleteSkill(
  client: MeshApiClient,
  workspaceId: string,
  skillId: string,
): Promise<void> {
  await client.request<null>('DELETE', skillPath(workspaceId, skillId));
}

export async function listVersions(
  client: MeshApiClient,
  workspaceId: string,
  skillId: string,
  params: { limit?: number; cursor?: string } = {},
): Promise<{ data: SkillVersion[]; nextCursor: string | null }> {
  const envelope = await client.list<SkillVersion>(
    `${skillPath(workspaceId, skillId)}/versions`,
    { query: { limit: params.limit, cursor: params.cursor } },
  );
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export async function getVersion(
  client: MeshApiClient,
  workspaceId: string,
  skillId: string,
  versionId: string,
  includeContent = false,
): Promise<SkillVersion> {
  return client.request<SkillVersion>(
    'GET',
    `${skillPath(workspaceId, skillId)}/versions/${versionId}`,
    { query: { include_content: includeContent ? 'true' : 'false' } },
  );
}

export interface CreateVersionBody {
  readonly version: string;
  readonly instructions: string;
  readonly scripts?: readonly {
    readonly path: string;
    readonly runtime?: string;
    readonly entrypoint?: boolean;
    readonly required_capabilities?: CapabilityDeclaration[];
    readonly content?: string;
  }[];
  readonly references?: readonly {
    readonly path: string;
    readonly media_type?: string;
    readonly summary?: string | null;
    readonly content?: string;
  }[];
  readonly triggers?: readonly {
    readonly trigger_type?: 'keyword' | 'semantic' | 'tag';
    readonly pattern: string;
    readonly weight?: number;
  }[];
  readonly changelog?: string | null;
  readonly required_capabilities?: CapabilityDeclaration[];
  readonly publish?: boolean;
}

export async function createVersion(
  client: MeshApiClient,
  workspaceId: string,
  skillId: string,
  body: CreateVersionBody,
): Promise<SkillVersion> {
  return client.request<SkillVersion>('POST', `${skillPath(workspaceId, skillId)}/versions`, {
    body,
  });
}

// --- 导入 / 审批 (§3.1) -------------------------------------------------------

export interface ImportBody {
  readonly source_type: 'marketplace' | 'url';
  readonly uri: string;
  readonly ref?: string | null;
}

/** 202 — 返回导入任务(状态机;parsing→…→awaiting_review|ready)。 */
export async function startImport(
  client: MeshApiClient,
  workspaceId: string,
  body: ImportBody,
): Promise<ImportTask> {
  return client.request<ImportTask>('POST', `${skillsPath(workspaceId)}/import`, { body });
}

export async function getImportTask(
  client: MeshApiClient,
  workspaceId: string,
  taskId: string,
): Promise<ImportTask> {
  return client.request<ImportTask>('GET', `${skillsPath(workspaceId)}/import/${taskId}`);
}

export interface ApproveBody {
  readonly task_id: string;
  readonly granted_capabilities: CapabilityDeclaration[];
  readonly decision?: 'approve' | 'reject';
  readonly comment?: string | null;
}

export async function approveSkill(
  client: MeshApiClient,
  workspaceId: string,
  skillId: string,
  body: ApproveBody,
): Promise<ImportTask> {
  return client.request<ImportTask>('POST', `${skillPath(workspaceId, skillId)}/approve`, {
    body,
  });
}

export async function listMarketplace(
  client: MeshApiClient,
  workspaceId: string,
  params: { q?: string; limit?: number } = {},
): Promise<{ data: MarketplaceEntry[]; nextCursor: string | null }> {
  const envelope = await client.list<MarketplaceEntry>(
    `/api/v1/workspaces/${workspaceId}/marketplace/skills`,
    { query: { q: params.q, limit: params.limit } },
  );
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

// --- 安装 (§3.1) ---------------------------------------------------------------

export interface InstallBody {
  readonly skill_id: string;
  readonly skill_version_id: string;
  readonly scope?: 'workspace' | 'agent';
  readonly agent_id?: string | null;
  readonly auto_update?: boolean;
}

export async function installSkill(
  client: MeshApiClient,
  workspaceId: string,
  body: InstallBody,
): Promise<SkillInstallation> {
  return client.request<SkillInstallation>('POST', installationsPath(workspaceId), { body });
}

export async function listInstallations(
  client: MeshApiClient,
  workspaceId: string,
  params: { skill_id?: string; scope?: string; limit?: number; cursor?: string } = {},
): Promise<{ data: SkillInstallation[]; nextCursor: string | null }> {
  const envelope = await client.list<SkillInstallation>(installationsPath(workspaceId), {
    query: {
      skill_id: params.skill_id,
      scope: params.scope,
      limit: params.limit,
      cursor: params.cursor,
    },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export interface PatchInstallationBody {
  readonly skill_version_id?: string;
  readonly install_status?: 'installed' | 'disabled';
  readonly auto_update?: boolean;
}

export async function updateInstallation(
  client: MeshApiClient,
  workspaceId: string,
  installationId: string,
  body: PatchInstallationBody,
): Promise<SkillInstallation> {
  return client.request<SkillInstallation>(
    'PATCH',
    `${installationsPath(workspaceId)}/${installationId}`,
    { body },
  );
}

export async function uninstallSkill(
  client: MeshApiClient,
  workspaceId: string,
  installationId: string,
): Promise<void> {
  await client.request<null>('DELETE', `${installationsPath(workspaceId)}/${installationId}`);
}

export async function rollbackInstallation(
  client: MeshApiClient,
  workspaceId: string,
  installationId: string,
  body: { target_version_id: string; reason?: string | null },
): Promise<SkillInstallation> {
  return client.request<SkillInstallation>(
    'POST',
    `${installationsPath(workspaceId)}/${installationId}/rollback`,
    { body },
  );
}

// --- agent 绑定 (§3.1) -----------------------------------------------------------

export async function listAgentSkills(
  client: MeshApiClient,
  workspaceId: string,
  agentId: string,
  params: { limit?: number; cursor?: string } = {},
): Promise<{ data: AgentSkillRow[]; nextCursor: string | null }> {
  const envelope = await client.list<AgentSkillRow>(agentSkillsPath(workspaceId, agentId), {
    query: { limit: params.limit, cursor: params.cursor },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export interface BindBody {
  readonly skill_installation_id: string;
  readonly skill_version_id?: string | null;
  readonly auto_trigger?: boolean;
  readonly priority?: number;
}

export async function bindSkill(
  client: MeshApiClient,
  workspaceId: string,
  agentId: string,
  body: BindBody,
): Promise<AgentSkillBinding> {
  return client.request<AgentSkillBinding>('POST', agentSkillsPath(workspaceId, agentId), {
    body,
  });
}

export interface PatchBindingBody {
  readonly enabled?: boolean;
  readonly auto_trigger?: boolean;
  readonly priority?: number;
}

export async function updateBinding(
  client: MeshApiClient,
  workspaceId: string,
  agentId: string,
  bindingId: string,
  body: PatchBindingBody,
): Promise<AgentSkillBinding> {
  return client.request<AgentSkillBinding>(
    'PATCH',
    `${agentSkillsPath(workspaceId, agentId)}/${bindingId}`,
    { body },
  );
}

export async function unbindSkill(
  client: MeshApiClient,
  workspaceId: string,
  agentId: string,
  bindingId: string,
): Promise<void> {
  await client.request<null>('DELETE', `${agentSkillsPath(workspaceId, agentId)}/${bindingId}`);
}
