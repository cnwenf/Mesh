/**
 * Issue 模块 API 调用(契约层,issue.md §3.1 / README §6.14 包络)。
 * 列表走 `list`(自动解 {data,next_cursor}),分组走 `grouped`(整体游标契约),
 * 单对象走 `request`;乐观并发经 RequestOptions.ifMatch(§6.14,409 收敛见 optimistic.ts)。
 */
import type { MeshApiClient } from '../../api';
import type {
  ActivityEntry,
  BulkBody,
  BulkResult,
  CreateIssueBody,
  DependencyEntry,
  DependencyType,
  IssueDetail,
  IssueStatusRef,
  IssueSummary,
  ListIssuesParams,
  MovePreview,
  UpdateIssueBody,
} from './types';

export interface Page<T> {
  readonly data: readonly T[];
  readonly nextCursor: string | null;
}

export interface GroupedPage<T> {
  readonly groups: readonly {
    readonly key: string;
    readonly label: string;
    readonly count: number;
    readonly data: readonly T[];
  }[];
  readonly nextCursor: string | null;
}

const workspaceIssuesPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/issues`;

const issuePath = (issueId: string): string => `/api/v1/issues/${issueId}`;

/** 详情级实时频道(issue.md §3.6):该 issue 全量事件(私有项目仅走此频道,§6.7)。 */
export function issueChannel(issueId: string): string {
  return `issue:${issueId}`;
}

/** 列表级实时频道(issue.md §3.6):仅可见 issue 的列表级事件。 */
export function workspaceIssuesChannel(workspaceId: string): string {
  return `workspace:${workspaceId}:issues`;
}

/** Issue 列表(§3.2 过滤/排序;游标分页)。 */
export async function listIssues(
  client: MeshApiClient,
  workspaceId: string,
  params: ListIssuesParams = {},
  signal?: AbortSignal,
): Promise<Page<IssueSummary>> {
  const envelope = await client.list<IssueSummary>(workspaceIssuesPath(workspaceId), {
    query: {
      status: params.status,
      state_category: params.state_category,
      priority: params.priority,
      assignee_id: params.assignee_id,
      reporter_id: params.reporter_id,
      project_id: params.project_id,
      parent_id: params.parent_id,
      due_before: params.due_before,
      due_after: params.due_after,
      q: params.q,
      sort: params.sort,
      order: params.order,
      limit: params.limit,
      cursor: params.cursor,
    },
    ...(signal === undefined ? {} : { signal }),
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** 分组列表(看板用;README §6.14 整体游标契约)。 */
export async function listIssuesGrouped(
  client: MeshApiClient,
  workspaceId: string,
  params: ListIssuesParams = {},
): Promise<GroupedPage<IssueSummary>> {
  const envelope = await client.grouped<IssueSummary>(workspaceIssuesPath(workspaceId), {
    query: {
      ...{
        state_category: params.state_category,
        priority: params.priority,
        assignee_id: params.assignee_id,
        project_id: params.project_id,
        q: params.q,
        sort: params.sort,
        order: params.order,
        limit: params.limit,
        cursor: params.cursor,
      },
      group_by: params.group_by,
    },
  });
  return { groups: envelope.groups, nextCursor: envelope.next_cursor };
}

/** Issue 详情(含 children_progress,§5.3 父进度)。 */
export async function getIssue(client: MeshApiClient, issueId: string): Promise<IssueDetail> {
  return client.request<IssueDetail>('GET', issuePath(issueId));
}

/** 按人类编号寻址(§5.1:UUID 与 by-identifier 返回同一 issue)。 */
export async function getIssueByIdentifier(
  client: MeshApiClient,
  workspaceId: string,
  identifier: string,
): Promise<IssueDetail> {
  return client.request<IssueDetail>(
    'GET',
    `${workspaceIssuesPath(workspaceId)}/by-identifier/${encodeURIComponent(identifier)}`,
  );
}

/** 创建 issue(§3.3);编号由服务端自动生成(§2.4)。 */
export async function createIssue(
  client: MeshApiClient,
  workspaceId: string,
  body: CreateIssueBody,
): Promise<IssueSummary> {
  return client.request<IssueSummary>('POST', workspaceIssuesPath(workspaceId), { body });
}

/**
 * PATCH 更新(三态:省略保持 / null 清空)。
 * 乐观并发双通道:body.version(§3.4)+ If-Match: updated_at(§6.14);409 conflict 收敛见 optimistic.ts。
 */
export async function updateIssue(
  client: MeshApiClient,
  issueId: string,
  body: UpdateIssueBody,
  ifMatch?: string,
): Promise<IssueSummary> {
  return client.request<IssueSummary>('PATCH', issuePath(issueId), { body, ifMatch });
}

/** 软删除(编号永久保留不复用,README §6.3)。 */
export async function deleteIssue(
  client: MeshApiClient,
  issueId: string,
): Promise<{ id: string; deleted: boolean }> {
  return client.request<{ id: string; deleted: boolean }>('DELETE', issuePath(issueId));
}

export async function listChildren(
  client: MeshApiClient,
  issueId: string,
  params: { limit?: number; cursor?: string } = {},
): Promise<Page<IssueSummary>> {
  const envelope = await client.list<IssueSummary>(`${issuePath(issueId)}/children`, {
    query: { limit: params.limit, cursor: params.cursor },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export async function listActivity(
  client: MeshApiClient,
  issueId: string,
  params: { limit?: number; cursor?: string } = {},
): Promise<Page<ActivityEntry>> {
  const envelope = await client.list<ActivityEntry>(`${issuePath(issueId)}/activity`, {
    query: { limit: params.limit, cursor: params.cursor },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/* ---- 依赖图(§1.2.4 / §3.1) ---- */

export async function listDependencies(
  client: MeshApiClient,
  issueId: string,
): Promise<readonly DependencyEntry[]> {
  const envelope = await client.list<DependencyEntry>(`${issuePath(issueId)}/dependencies`);
  return envelope.data;
}

/** 新增依赖边;成环 → 409 circular_dependency(details.path,§5.3)。 */
export async function addDependency(
  client: MeshApiClient,
  issueId: string,
  body: { depends_on_id: string; type: DependencyType },
): Promise<DependencyEntry> {
  return client.request<DependencyEntry>('POST', `${issuePath(issueId)}/dependencies`, { body });
}

export async function removeDependency(
  client: MeshApiClient,
  issueId: string,
  dependencyId: string,
): Promise<{ id: string; deleted: boolean }> {
  return client.request<{ id: string; deleted: boolean }>(
    'DELETE',
    `${issuePath(issueId)}/dependencies/${dependencyId}`,
  );
}

/* ---- 跨项目迁移(§3.8 两步式契约) ---- */

export async function movePreview(
  client: MeshApiClient,
  issueId: string,
  targetProjectId: string | null,
): Promise<MovePreview> {
  return client.request<MovePreview>('POST', `${issuePath(issueId)}/move-preview`, {
    body: { target_project_id: targetProjectId },
  });
}

/** 确认迁移:未携带 confirm:true → 422 move_confirmation_required(携带预览)。
 * `version` 必填(§3.8 乐观锁,回传 move-preview 返回的 version)。 */
export async function moveIssue(
  client: MeshApiClient,
  issueId: string,
  body: { target_project_id: string | null; confirm: boolean; version: number },
): Promise<IssueSummary> {
  return client.request<IssueSummary>('POST', `${issuePath(issueId)}/move`, { body });
}

/* ---- 批量(§1.2.5 / §5.5) ---- */

/** 批量操作;部分失败 → 422 bulk_partial_failure(details 含 succeeded/failed/errors)。 */
export async function bulkIssues(client: MeshApiClient, body: BulkBody): Promise<BulkResult> {
  return client.request<BulkResult>('POST', '/api/v1/issues/bulk', { body });
}

/* ---- 状态定义(§1.2.3) ---- */

export async function listStatuses(
  client: MeshApiClient,
  workspaceId: string,
  projectId?: string,
): Promise<readonly IssueStatusRef[]> {
  const envelope = await client.list<IssueStatusRef>(`/api/v1/workspaces/${workspaceId}/statuses`, {
    query: { project_id: projectId },
  });
  return envelope.data;
}
