/**
 * Issue 模块实体类型(issue.md §2 / §3.3)。
 * 字段一律 snake_case(与后端信封逐字对齐);本地 UI 状态另用 camelCase。
 */

export type StateCategory =
  | 'backlog'
  | 'todo'
  | 'in_progress'
  | 'in_review'
  | 'blocked'
  | 'done'
  | 'cancelled';

export type IssuePriority = 'none' | 'low' | 'medium' | 'high' | 'urgent';

export type DependencyType = 'blocks' | 'blocked_by' | 'relates_to' | 'duplicates';

/** 看板列序与进度聚合的稳定语义序(issue.md §4.4)。 */
export const STATE_CATEGORY_ORDER: readonly StateCategory[] = [
  'backlog',
  'todo',
  'in_progress',
  'in_review',
  'blocked',
  'done',
  'cancelled',
];

export const PRIORITY_ORDER: readonly IssuePriority[] = [
  'urgent',
  'high',
  'medium',
  'low',
  'none',
];

/** 轻量成员引用(assignee/reporter):服务端解析显示名 + 类型快照(真源 members)。 */
export interface IssueMemberRef {
  readonly id: string;
  readonly name: string;
  readonly member_type: 'human' | 'agent';
}

/** 双层状态的展示层(issue.md §1.2.3):自定义状态 → 稳定 category。 */
export interface IssueStatusRef {
  readonly id: string;
  readonly project_id: string | null;
  readonly name: string;
  readonly category: StateCategory;
  readonly color: string | null;
  readonly position: number;
  readonly is_default: boolean;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface IssueProjectRef {
  readonly id: string;
  readonly name: string;
  readonly key: string;
}

export interface IssueSummary {
  readonly id: string;
  readonly workspace_id: string;
  readonly project_id: string | null;
  readonly project: IssueProjectRef | null;
  readonly identifier_namespace_key: string;
  readonly number: number;
  /** 人类可读编号(一经生成永不改变,README §6.3) */
  readonly identifier: string;
  readonly title: string;
  readonly description: string | null;
  readonly status: IssueStatusRef | null;
  readonly status_id: string;
  readonly state_category: StateCategory;
  readonly priority: IssuePriority;
  readonly assignee: IssueMemberRef | null;
  readonly assignee_id: string | null;
  readonly reporter: IssueMemberRef | null;
  readonly reporter_id: string | null;
  readonly estimate: number | null;
  readonly estimate_unit: 'points' | 'hours' | null;
  readonly due_date: string | null;
  readonly start_date: string | null;
  readonly milestone_id: string | null;
  readonly cycle_id: string | null;
  readonly parent_id: string | null;
  readonly position: number;
  readonly completed_at: string | null;
  /** 乐观并发版本号(issue.md §3.4) */
  readonly version: number;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface ChildrenProgress {
  readonly total: number;
  readonly done: number;
}

/** 详情 = 摘要 + 子项进度(getIssue 返回 children_progress)。 */
export interface IssueDetail extends IssueSummary {
  readonly children_progress: ChildrenProgress;
}

export interface DependencyEntry {
  readonly id: string;
  readonly issue_id: string;
  readonly depends_on_id: string;
  readonly type: DependencyType;
  readonly created_by: string | null;
  readonly created_at: string;
}

export interface ActivityEntry {
  readonly id: string;
  readonly issue_id: string;
  readonly actor: IssueMemberRef | null;
  readonly field: string;
  readonly old_value: unknown;
  readonly new_value: unknown;
  readonly created_at: string;
}

export interface CreateIssueBody {
  readonly title: string;
  readonly description?: string;
  readonly project_id?: string | null;
  readonly status_id?: string;
  readonly priority?: IssuePriority;
  readonly assignee_id?: string | null;
  readonly parent_id?: string | null;
  readonly due_date?: string | null;
}

export interface UpdateIssueBody {
  readonly title?: string;
  readonly description?: string | null;
  readonly status_id?: string;
  readonly priority?: IssuePriority;
  readonly assignee_id?: string | null;
  readonly due_date?: string | null;
  readonly start_date?: string | null;
  /** 乐观并发期望版本(§3.4;If-Match 另经 RequestOptions.ifMatch) */
  readonly version?: number;
}

export interface ListIssuesParams {
  readonly status?: string;
  readonly state_category?: StateCategory;
  readonly priority?: IssuePriority;
  readonly assignee_id?: string;
  readonly reporter_id?: string;
  readonly project_id?: string;
  readonly parent_id?: string;
  readonly due_before?: string;
  readonly due_after?: string;
  readonly q?: string;
  readonly sort?: 'position' | 'created_at' | 'priority' | 'due_date';
  readonly order?: 'asc' | 'desc';
  readonly group_by?: 'state_category' | 'assignee' | 'priority' | 'project' | 'cycle';
  readonly limit?: number;
  readonly cursor?: string;
}

/** 分组响应(README §6.14 整体游标契约:groups + 顶层 next_cursor,无每组独立 cursor)。 */
export interface IssueGroup {
  readonly key: string;
  readonly label: string;
  readonly count: number;
  readonly data: readonly IssueSummary[];
}

export interface BulkChanges {
  readonly status_id?: string;
  readonly priority?: IssuePriority;
  readonly assignee_id?: string | null;
  readonly project_id?: string | null;
}

export interface BulkBody {
  readonly issue_ids: readonly string[];
  readonly changes?: BulkChanges;
  readonly delete?: boolean;
  readonly confirm?: boolean;
}

export interface BulkErrorEntry {
  readonly issue_id: string;
  readonly code: string;
  readonly message: string;
}

export interface BulkResult {
  readonly succeeded: number;
  readonly failed: number;
  readonly errors: readonly BulkErrorEntry[];
}

/** 跨项目迁移预览(§3.8 两步式契约第一步)。 */
export interface MovePreviewField {
  readonly field: string;
  readonly from?: unknown;
  readonly to?: unknown;
  readonly items?: readonly unknown[];
  readonly reason: string;
}

export interface MovePreview {
  readonly issue_id: string;
  readonly identifier: string;
  readonly from_project_id: string | null;
  readonly target_project_id: string | null;
  readonly mapped_fields: readonly MovePreviewField[];
  readonly cleared_fields: readonly MovePreviewField[];
  readonly kept_fields: readonly string[];
}
