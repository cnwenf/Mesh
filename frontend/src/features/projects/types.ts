/**
 * 项目模块实体类型(project.md §2 / §3.2)。
 * 字段一律 snake_case(与后端信封逐字对齐);本地 UI 状态另用 camelCase。
 */

export type ProjectStatus = 'planning' | 'active' | 'paused' | 'completed' | 'cancelled';
export type ProjectHealth = 'on_track' | 'at_risk' | 'off_track';
export type ProjectVisibility = 'public' | 'private';
export type ProjectMemberRole = 'lead' | 'member' | 'viewer';
export type MilestoneState = 'open' | 'closed';
export type CycleState = 'planned' | 'active' | 'completed';

/** 轻量成员引用(负责人/留痕作者):人类或 agent,`name` 为服务端解析后的显示名。 */
export interface EntityRef {
  readonly id: string;
  readonly name: string;
  readonly member_type: 'human' | 'agent';
}

export interface ProjectSummary {
  readonly id: string;
  readonly workspace_id: string;
  readonly name: string;
  readonly key: string;
  readonly description: string | null;
  readonly icon: string | null;
  readonly color: string | null;
  readonly status: ProjectStatus;
  readonly health: ProjectHealth | null;
  readonly visibility: ProjectVisibility;
  readonly lead: EntityRef | null;
  readonly lead_member_id: string | null;
  readonly start_date: string | null;
  readonly target_date: string | null;
  /** 0..1,由子 issue 完成率派生(§2.4) */
  readonly progress: number;
  readonly open_issues: number;
  readonly done_issues: number;
  readonly issue_seq: number;
  readonly archived: boolean;
  readonly archived_at: string | null;
  readonly my_role: ProjectMemberRole | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface Milestone {
  readonly id: string;
  readonly project_id: string;
  readonly title: string;
  readonly description: string | null;
  readonly target_date: string | null;
  readonly state: MilestoneState;
  /** `state='open' AND target_date < today`(§5.1,服务端计算) */
  readonly overdue: boolean;
  readonly created_at: string;
  readonly updated_at: string;
}

/** 项目详情 = 摘要 + 里程碑(getProject 返回内嵌 milestones)。 */
export interface ProjectDetail extends ProjectSummary {
  readonly milestones: readonly Milestone[];
}

export interface ProjectUpdateEntry {
  readonly id: string;
  readonly project_id: string;
  readonly author: EntityRef | null;
  readonly health: ProjectHealth | null;
  readonly status: ProjectStatus | null;
  readonly message: string | null;
  readonly created_at: string;
}

export interface ProjectMemberEntry {
  readonly id: string;
  readonly project_id: string;
  readonly member_id: string;
  readonly member: EntityRef | null;
  readonly role: ProjectMemberRole;
  readonly created_at: string;
}

export interface Cycle {
  readonly id: string;
  readonly project_id: string | null;
  readonly name: string;
  readonly starts_at: string;
  readonly ends_at: string;
  readonly state: CycleState;
  readonly auto_roll: boolean;
  readonly created_at: string;
  readonly updated_at: string;
}

/** 完成 auto_roll 周期时,PATCH 响应 data 附带顺延出的下一周期(§1.2.5)。 */
export interface CyclePatchResult extends Cycle {
  readonly next_cycle?: Cycle;
}

export interface ProjectTemplate {
  readonly id: string;
  readonly workspace_id: string;
  readonly name: string;
  readonly template_body: Record<string, unknown>;
  readonly created_at: string;
  readonly updated_at: string;
}

/** 模板实例化结果(§3.2b):新项目 + 克隆出的里程碑/周期 id + 跳过项。 */
export interface InstantiateResult extends ProjectSummary {
  readonly milestone_ids: readonly string[];
  readonly cycle_ids: readonly string[];
  readonly skipped: readonly string[];
}

/** 项目列表筛选(与 URL 查询参数同源,§4.1)。 */
export interface ListProjectsParams {
  readonly status?: ProjectStatus;
  readonly visibility?: ProjectVisibility;
  readonly archived?: boolean;
  readonly mine?: boolean;
  readonly lead_member_id?: string;
  readonly limit?: number;
  readonly cursor?: string;
}

export interface CreateProjectBody {
  readonly name: string;
  readonly key: string;
  readonly description?: string;
  readonly icon?: string;
  readonly color?: string;
  readonly status?: ProjectStatus;
  readonly visibility?: ProjectVisibility;
  readonly lead_member_id?: string;
  readonly start_date?: string;
  readonly target_date?: string;
}

/** 设置 PATCH 三态:省略 = 保持,null = 清空(§6.14)。 */
export interface UpdateProjectBody {
  readonly name?: string;
  readonly description?: string | null;
  readonly icon?: string | null;
  readonly color?: string | null;
  readonly status?: ProjectStatus;
  readonly health?: ProjectHealth | null;
  readonly visibility?: ProjectVisibility;
  readonly lead_member_id?: string | null;
  readonly start_date?: string | null;
  readonly target_date?: string | null;
}

export interface AddProjectUpdateBody {
  readonly health?: ProjectHealth;
  readonly status?: ProjectStatus;
  readonly message?: string;
}

export interface CreateMilestoneBody {
  readonly title: string;
  readonly description?: string;
  readonly target_date?: string;
}

export interface UpdateMilestoneBody {
  readonly title?: string;
  readonly description?: string | null;
  readonly target_date?: string | null;
  readonly state?: MilestoneState;
}

export interface ListMilestonesParams {
  readonly state?: MilestoneState;
  readonly limit?: number;
  readonly cursor?: string;
}

export interface ListUpdatesParams {
  readonly limit?: number;
  readonly cursor?: string;
}

export interface ListCyclesParams {
  readonly state?: CycleState;
  readonly project_id?: string;
  readonly limit?: number;
  readonly cursor?: string;
}

export interface CreateCycleBody {
  readonly name: string;
  readonly starts_at: string;
  readonly ends_at: string;
  readonly project_id?: string;
  readonly state?: CycleState;
  readonly auto_roll?: boolean;
}

export interface UpdateCycleBody {
  readonly name?: string;
  readonly starts_at?: string;
  readonly ends_at?: string;
  readonly state?: CycleState;
  readonly auto_roll?: boolean;
}

export interface AddProjectMemberBody {
  readonly member_id: string;
  readonly role?: ProjectMemberRole;
}

export interface UpdateProjectMemberRoleBody {
  readonly role: ProjectMemberRole;
}

export interface CreateTemplateBody {
  readonly name: string;
  readonly template_body: Record<string, unknown>;
}

export interface UpdateTemplateBody {
  readonly name?: string;
  readonly template_body?: Record<string, unknown>;
}

export interface InstantiateTemplateBody {
  readonly name: string;
  readonly key: string;
  readonly overrides?: Record<string, unknown>;
}

export const PROJECT_STATUS_ORDER: readonly ProjectStatus[] = [
  'planning',
  'active',
  'paused',
  'completed',
  'cancelled',
];

export const PROJECT_HEALTH_ORDER: readonly ProjectHealth[] = ['on_track', 'at_risk', 'off_track'];

export const PROJECT_MEMBER_ROLE_ORDER: readonly ProjectMemberRole[] = ['lead', 'member', 'viewer'];

export const CYCLE_STATE_ORDER: readonly CycleState[] = ['planned', 'active', 'completed'];
