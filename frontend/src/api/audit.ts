/**
 * 审计日志查询 API — auth.md §3.3 / §5.3(admin+)。
 *
 * 支持按 action / actor 过滤 + before/after 时间范围(§5.3,RFC3339 半开区间)
 * + 游标分页。供「设置 → 审计」页消费。
 */
import type { MeshApiClient } from './client';
import type { ListEnvelope } from '../types/envelopes';

/** 审计日志条目(§2.6;actor_kind ∈ member|system) */
export interface AuditLogEntry {
  id: string;
  actor_member_id: string | null;
  actor_kind: string;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  ip_address: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface AuditLogQuery {
  action?: string;
  actor_member_id?: string;
  /** RFC3339;返回 created_at < before 的行 */
  before?: string;
  /** RFC3339;返回 created_at > after 的行 */
  after?: string;
  limit?: number;
  cursor?: string;
}

/** 查询工作区审计日志(admin+;游标分页,next_cursor=null 为末页)。 */
export async function listAuditLogs(
  client: MeshApiClient,
  workspaceId: string,
  query: AuditLogQuery = {},
): Promise<ListEnvelope<AuditLogEntry>> {
  return client.list<AuditLogEntry>(`/api/v1/workspaces/${workspaceId}/audit-logs`, {
    query: {
      action: query.action,
      actor_member_id: query.actor_member_id,
      before: query.before,
      after: query.after,
      limit: query.limit,
      cursor: query.cursor,
    },
  });
}
