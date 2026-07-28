/**
 * Squad 模块实体类型(squad.md §2 / §3)。
 * 字段一律 snake_case(与后端信封逐字对齐);本地 UI 状态另用 camelCase。
 */

export type SquadKind = 'standing' | 'adhoc' | 'task_scoped';

export type SquadStatus = 'active' | 'archived';

export type LeaderMode = 'single' | 'multi';

export type SquadRole = 'leader' | 'member' | 'observer';

export type MemberType = 'human' | 'agent';

export type MessageKind = 'chat' | 'instruction' | 'report' | 'system' | 'context';

export type SquadTaskStatus =
  | 'pending'
  | 'decomposing'
  | 'awaiting_plan_approval'
  | 'dispatching'
  | 'in_progress'
  | 'blocked'
  | 'aggregating'
  | 'done'
  | 'failed'
  | 'cancelled';

/** 终态:不再轮询 / 不再可取消(§6.4 状态机收敛)。 */
export const TERMINAL_TASK_STATUSES: ReadonlySet<SquadTaskStatus> = new Set<SquadTaskStatus>([
  'done',
  'failed',
  'cancelled',
]);

/** 稳定展示序(状态机推进方向)。 */
export const SQUAD_KIND_ORDER: readonly SquadKind[] = ['standing', 'adhoc', 'task_scoped'];

export const SQUAD_STATUS_ORDER: readonly SquadStatus[] = ['active', 'archived'];

export const SQUAD_ROLE_ORDER: readonly SquadRole[] = ['leader', 'member', 'observer'];

export const MESSAGE_KIND_ORDER: readonly MessageKind[] = [
  'chat',
  'instruction',
  'report',
  'context',
  'system',
];

/** 任务状态 → 状态点语义色(§6.12:颜色非唯一信号,文本始终并存)。 */
export const TASK_STATUS_TONE: Record<
  SquadTaskStatus,
  'success' | 'warn' | 'danger' | 'info' | 'neutral'
> = {
  pending: 'neutral',
  decomposing: 'info',
  awaiting_plan_approval: 'warn',
  dispatching: 'info',
  in_progress: 'info',
  blocked: 'warn',
  aggregating: 'info',
  done: 'success',
  failed: 'danger',
  cancelled: 'neutral',
};

/**
 * 成员快照(README §6.1:显示名服务端解析,客户端不信任/不存储)。
 * squads.primary_leader / squad_tasks.assignee / squad_messages.sender 皆此形。
 */
export interface MemberSnapshot {
  readonly member_id: string;
  readonly member_type: MemberType;
  readonly name: string;
}

/** 小队成员预览快照(§3.1 list 内嵌,最多 8 条;含角色供头像墙标注 leader)。 */
export interface MemberPreview {
  readonly member_id: string;
  readonly member_type: MemberType;
  readonly name: string;
  readonly role: SquadRole;
}

export interface Squad {
  readonly id: string;
  readonly workspace_id: string;
  readonly name: string;
  readonly description: string | null;
  /** leader 持久指令(§4.3-1/B15;可为空)。 */
  readonly instructions: string | null;
  readonly avatar_url: string | null;
  readonly kind: SquadKind;
  readonly status: SquadStatus;
  readonly leader_mode: LeaderMode;
  readonly primary_leader_id: string | null;
  readonly primary_leader: MemberSnapshot | null;
  readonly require_plan_approval: boolean;
  readonly max_decompose_depth: number;
  readonly member_count: number;
  readonly active_task_count: number;
  readonly leaders: readonly MemberSnapshot[];
  /** list 投影内嵌的成员墙快照(至多 8;详情仍以 members 端点为准)。 */
  readonly member_preview: readonly MemberPreview[];
  readonly archived_at: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface SquadMember {
  readonly id: string;
  readonly member_id: string;
  readonly member_type: MemberType;
  readonly name: string;
  readonly role: SquadRole;
  readonly joined_at: string;
}

/** 拆解树进度聚合(getTaskTree 返回;§5.3 父进度)。 */
export interface SquadTaskProgress {
  readonly total: number;
  readonly done: number;
  readonly in_progress: number;
  readonly pending: number;
  readonly failed: number;
}

export interface SquadTask {
  readonly id: string;
  readonly squad_id: string;
  readonly issue_id: string;
  readonly parent_task_id: string | null;
  readonly root_task_id: string | null;
  readonly depth: number;
  readonly title_snapshot: string | null;
  readonly status: SquadTaskStatus;
  readonly assignee: MemberSnapshot | null;
  readonly stage: number | null;
  readonly execution_id: string | null;
  readonly plan_markdown: string | null;
  readonly result_summary: string | null;
  readonly failure_reason: string | null;
  /** 依赖的任务 id(DAG 出边,§2.6)。 */
  readonly depends_on: readonly string[];
  /** 依赖中尚未 done 的任务 id(当前阻塞源)。 */
  readonly blocked_by: readonly string[];
  readonly dispatched_at: string | null;
  readonly started_at: string | null;
  readonly finished_at: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  /** 仅 getTaskTree 返回:子任务(递归)。 */
  readonly children?: readonly SquadTask[];
  /** 仅 getTaskTree 返回:整树进度聚合。 */
  readonly progress?: SquadTaskProgress;
}

/** getTaskStatus 轻量轮询视图(§3.1 状态查询)。 */
export interface TaskStatusView {
  readonly task_id: string;
  readonly status: SquadTaskStatus;
  readonly result_summary: string | null;
}

export interface SquadMessage {
  readonly id: string;
  readonly squad_id: string;
  readonly task_id: string | null;
  readonly sender: MemberSnapshot | null;
  readonly recipient: MemberSnapshot | null;
  readonly kind: MessageKind;
  readonly body_markdown: string;
  readonly body_html: string | null;
  readonly pinned: boolean;
  readonly attachment_ids: readonly string[];
  readonly created_at: string;
}

export interface SquadActivity {
  readonly id: string;
  readonly task_id: string | null;
  readonly actor_kind: string;
  readonly actor: MemberSnapshot | null;
  readonly action: string;
  readonly target_type: string | null;
  readonly target_id: string | null;
  readonly payload: Record<string, unknown> | null;
  readonly created_at: string;
}

/** 小队分派响应(§6.9 显式小队分派端点;202,根任务异步入队)。 */
export interface Assignment {
  readonly assignment_id: string;
  readonly id: string | null;
  readonly squad_id: string;
  readonly issue_id: string;
  readonly parent_task_id: string | null;
  readonly root_task_id: string | null;
  readonly depth: number;
  readonly title_snapshot: string | null;
  readonly status: SquadTaskStatus | null;
  readonly orchestrator_id: string | null;
  readonly issue_assignee_id: string;
  readonly noop: boolean;
  readonly status_url: string | null;
  readonly stream_url: string | null;
  readonly created_at: string;
}

/** 统一审批实体视图(§6.10 approvals;计划批准/驳回返回)。 */
export interface PlanApproval {
  readonly id: string;
  readonly status: string;
  readonly subject_type?: string;
  readonly subject_task_id?: string;
  readonly action_summary?: string | null;
  readonly comment?: string | null;
}

export interface CreatedSubtask {
  readonly id: string;
  readonly title: string;
  readonly assignee_id: string | null;
  readonly assignee_type: MemberType | null;
  readonly stage: number | null;
  readonly depth: number;
  readonly status: SquadTaskStatus;
}

/** 拆解方案提交响应(§6.10:需审批时 awaiting_approval=true 并携带 approval)。 */
export interface SubtasksResult {
  readonly root_task_id: string;
  readonly root_status: SquadTaskStatus;
  readonly created_subtasks: readonly CreatedSubtask[];
  readonly dependencies: readonly (readonly string[])[];
  readonly awaiting_approval: boolean;
  readonly approval: PlanApproval | null;
}

/* ---- 请求体 ---- */

export interface SquadMemberInput {
  readonly member_id: string;
  readonly role: SquadRole;
  /** 可选客户端提示;真值由服务端经 members.member_type 解析(不被信任)。 */
  readonly member_type?: MemberType;
}

export interface CreateSquadBody {
  readonly name: string;
  readonly description?: string;
  readonly instructions?: string;
  readonly avatar_url?: string;
  readonly kind?: SquadKind;
  readonly leader_mode?: LeaderMode;
  readonly require_plan_approval?: boolean;
  readonly max_decompose_depth?: number;
  readonly members: readonly SquadMemberInput[];
}

export interface UpdateSquadBody {
  readonly name?: string;
  readonly description?: string | null;
  readonly instructions?: string | null;
  readonly avatar_url?: string | null;
  readonly kind?: SquadKind;
  readonly leader_mode?: LeaderMode;
  readonly require_plan_approval?: boolean;
  readonly max_decompose_depth?: number;
  readonly primary_leader_id?: string | null;
}

/** 看板人工改状请求体(§4.2;服务端校验迁移,非法 → 409 conflict)。 */
export interface MoveTaskStatusBody {
  readonly status: SquadTaskStatus;
  readonly result_summary?: string | null;
}

/**
 * issue 的活跃小队分派视图(§2.5 / §4.3-2;by-issue 端点)。
 * 无活跃分派时端点返回 data:null。
 */
export interface IssueAssignment {
  readonly assignment_id: string;
  readonly squad_id: string;
  readonly squad_name: string;
  readonly issue_id: string;
  readonly root_task_id: string | null;
  readonly leader: MemberSnapshot | null;
  readonly assigned_at: string;
}

export interface AssignTaskBody {
  readonly issue_id: string;
  readonly brief?: string;
}

export interface SubtaskInput {
  readonly title: string;
  readonly assignee?: SquadMemberInput;
  readonly stage?: number;
  readonly depends_on?: readonly string[];
}

export interface CreateSubtasksBody {
  readonly plan_markdown?: string;
  readonly subtasks: readonly SubtaskInput[];
}

export interface SendMessageBody {
  readonly task_id?: string;
  readonly recipient?: SquadMemberInput;
  readonly kind: MessageKind;
  readonly body_markdown: string;
  readonly attachment_ids?: readonly string[];
  readonly pinned?: boolean;
}

export interface ListSquadsParams {
  readonly status?: SquadStatus;
  readonly kind?: SquadKind;
  readonly q?: string;
  readonly limit?: number;
  readonly cursor?: string;
}
