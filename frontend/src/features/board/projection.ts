/**
 * 看板投影层契约(kanban.md §3.2/§4.3,README §6.14 整体游标)。
 *
 * - fetchViewIssues:GET /views/{id}/issues 执行视图配置：一维返回 groups，
 *   二维返回共享 columns + lanes[].groups，两者均只有一个顶层游标。
 * - moveCard:POST /views/{id}/moves 原子拖拽(乐观锁 + WIP + 每视图排序);
 *   group_by=project 未确认 → 422 move_confirmation_required(details.preview)。
 * - reorderCard:POST /views/{id}/reorder 仅 cell 内排序；quickCreateCard 在
 *   视图 cell 端点原子继承分组轴。
 */
import type { MeshApiClient } from '../../api';
import type { GroupedEnvelope } from '../../types/envelopes';
import type { WipLimit } from './types';

/** 看板卡片(投影响应里的 issue 呈现,snake_case 与后端逐字对齐)。 */
export interface BoardCard {
  readonly id: string;
  readonly identifier: string;
  readonly title: string;
  readonly state_category: string;
  readonly status: {
    readonly id: string;
    readonly name: string;
    readonly category: string;
  } | null;
  readonly status_id: string;
  readonly priority: string;
  readonly assignee: { readonly id: string; readonly name: string } | null;
  readonly assignee_id: string | null;
  readonly project_id: string | null;
  readonly project?: { readonly id: string; readonly name: string; readonly key: string } | null;
  readonly description?: string | null;
  readonly estimate?: number | null;
  readonly estimate_unit?: string | null;
  readonly due_date?: string | null;
  readonly position: number;
  readonly version: number;
  readonly updated_at: string;
}

/** 一个分组列(整体游标契约:count 组内总数,data 当前页切片,无每组独立 cursor)。 */
export interface BoardGroup {
  readonly key: string;
  readonly label: string;
  readonly count: number;
  readonly wip: WipLimit | null;
  readonly data: readonly BoardCard[];
}

/** 二维投影的共享列骨架。count/wip 跨全部泳道聚合。 */
export interface BoardProjectionColumn {
  readonly key: string;
  readonly label: string;
  readonly count: number;
  readonly wip: WipLimit | null;
}

/** 二维投影的一个 lane 内 cell。 */
export interface BoardLaneGroup {
  readonly key: string;
  readonly count: number;
  readonly data: readonly BoardCard[];
}

/** 二维投影的泳道。 */
export interface BoardLane {
  readonly key: string;
  readonly label: string;
  readonly count: number;
  readonly groups: readonly BoardLaneGroup[];
}

/** GET /views/{id}/issues 顶层响应(README §6.14 分组整体游标)。 */
export interface ViewProjection {
  readonly layout: string;
  readonly group_by: string;
  readonly sub_group_by: string | null;
  /** 拖拽落点(group key)→ 应设置的 status_id(state_category/status 分组)。 */
  readonly column_target_status: Readonly<Record<string, string>>;
  readonly groups: readonly BoardGroup[];
  readonly columns: readonly BoardProjectionColumn[];
  readonly lanes: readonly BoardLane[];
  readonly next_cursor: string | null;
}

export interface MoveBody {
  readonly issue_id: string;
  readonly to_group_key: string;
  readonly to_sub_group_key?: string;
  readonly position: number;
  readonly version?: number;
  readonly confirm?: boolean;
  readonly dry_run?: boolean;
}

/** 跨项目迁移字段映射/清除清单(issue.md §3.8 preview)。 */
export interface MovePlanField {
  readonly field: string;
  readonly from?: unknown;
  readonly to?: unknown;
  readonly items?: readonly unknown[];
  readonly reason?: string;
}

export interface MovePlan {
  readonly issue_id: string;
  readonly identifier: string;
  readonly from_project_id: string | null;
  readonly target_project_id: string | null;
  readonly mapped_fields: readonly MovePlanField[];
  readonly cleared_fields: readonly MovePlanField[];
  readonly kept_fields: readonly string[];
}

/** move 成功返回:更新后的卡片 + (跨项目时)迁移结果清单。 */
export type MoveResult = BoardCard & {
  readonly move_result?: {
    readonly mapped_fields: readonly MovePlanField[];
    readonly cleared_fields: readonly MovePlanField[];
  };
};

const viewPath = (viewId: string): string => `/api/v1/views/${viewId}`;
const viewIssuesPath = (viewId: string): string => `/api/v1/views/${viewId}/issues`;

/** 执行视图配置 → 分组整体游标包络(kanban §3.2)。 */
export async function fetchViewIssues(
  client: MeshApiClient,
  viewId: string,
  params: { limit?: number; cursor?: string } = {},
): Promise<ViewProjection> {
  // 分组包络顶层即 {groups, next_cursor};投影层额外携带 layout/group_by/
  // column_target_status。后端 wip 为 {limit,enforcement} 对象(非通用 Group 的
  // 数值),此处按看板契约收窄。
  const envelope = (await client.grouped<BoardCard>(viewIssuesPath(viewId), {
    query: { limit: params.limit, cursor: params.cursor },
  })) as GroupedEnvelope<BoardCard> & {
    layout?: string;
    group_by?: string;
    sub_group_by?: string | null;
    column_target_status?: Record<string, string>;
    columns?: BoardProjectionColumn[];
    lanes?: BoardLane[];
    groups?: BoardGroup[];
  };
  return {
    layout: envelope.layout ?? 'board',
    group_by: envelope.group_by ?? 'state_category',
    sub_group_by: envelope.sub_group_by ?? null,
    column_target_status: envelope.column_target_status ?? {},
    groups: Array.isArray(envelope.groups) ? (envelope.groups as unknown as BoardGroup[]) : [],
    columns: Array.isArray(envelope.columns) ? envelope.columns : [],
    lanes: Array.isArray(envelope.lanes) ? envelope.lanes : [],
    next_cursor: envelope.next_cursor,
  };
}

/** 原子拖拽(kanban §3.2):乐观锁 + advisory lock + WIP + 状态变更 + 排序 upsert。 */
export async function moveCard(
  client: MeshApiClient,
  viewId: string,
  body: MoveBody,
): Promise<MoveResult> {
  return client.request<MoveResult>('POST', `${viewPath(viewId)}/moves`, { body });
}

/** 跨项目迁移预览(kanban §3.2 dry_run → move-preview 清单)。 */
export async function previewMove(
  client: MeshApiClient,
  viewId: string,
  body: MoveBody,
): Promise<MovePlan> {
  return client.request<MovePlan>('POST', `${viewPath(viewId)}/moves`, {
    body: { ...body, dry_run: true },
  });
}

/** 列内排序(kanban §4.3):仅写每视图位置,不改状态。 */
export async function reorderCard(
  client: MeshApiClient,
  viewId: string,
  body: { issue_id: string; to_group_key: string; sub_group_key?: string; position: number },
): Promise<{ id: string; group_key: string; sub_group_key?: string; position: number }> {
  return client.request<{
    id: string;
    group_key: string;
    sub_group_key?: string;
    position: number;
  }>('POST', `${viewPath(viewId)}/reorder`, { body });
}

export interface QuickCreateCardBody {
  readonly title: string;
  readonly group_key: string;
  readonly sub_group_key?: string;
}

/** 在视图 cell 中快速创建，由后端原子继承一轴/两轴与当前过滤器。 */
export async function quickCreateCard(
  client: MeshApiClient,
  viewId: string,
  body: QuickCreateCardBody,
): Promise<BoardCard> {
  return client.request<BoardCard>('POST', viewIssuesPath(viewId), { body });
}
