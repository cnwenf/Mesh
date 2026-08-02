/**
 * 统一审批 API 调用(契约层,README §6.10;后端 runtime/routes.py 的
 * /workspaces/{ws}/approvals 族)。字段名与后端 JSON 一致(snake_case)。
 * 决定请求体字段为 `comment`(ApprovalDecideRequest);重复决定幂等返回当前态。
 */
import type { MeshApiClient } from '../../api';
import type { ListEnvelope } from '../../types/envelopes';

export type ApprovalSubjectType = 'tool_call' | 'autopilot_action' | 'squad_plan';
export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'expired' | 'cancelled';

/** action_summary.resume_context(审批请求时冻结,§6.10 唯一续跑协议)。 */
export interface ApprovalResumeContext {
  readonly checkpoint_ref?: string | null;
  readonly completed_steps?: number | null;
  readonly pending_tool_call?: string | Record<string, unknown> | null;
}

/**
 * action_summary JSONB(§6.10):{action, capability+permission, impact_scope,
 * estimated_cost, resume_context, detail}。三类 subject 各有附加键(autopilot
 * 携带 run_id/autopilot_id;squad_plan 携带 plan_digest/subtask_count),
 * 一律只读可选,UI 呈现「能解析多少解析多少」。
 */
export interface ApprovalActionSummary {
  readonly action?: string;
  readonly capability?: string;
  readonly permission?: string;
  readonly impact_scope?: string | Record<string, unknown>;
  readonly estimated_cost?: string;
  readonly resume_context?: ApprovalResumeContext;
  readonly detail?: Record<string, unknown>;
  /** autopilot_action:所属 run/规则 id(executor 写入)。 */
  readonly run_id?: string;
  readonly autopilot_id?: string;
  /** squad_plan:方案摘要与子任务数。 */
  readonly plan_digest?: string;
  readonly subtask_count?: number;
  readonly tools_summary?: string;
  readonly resume_hint?: string;
}

/** GET /approvals 列表项 = _approval_response 序列化形态。 */
export interface Approval {
  readonly id: string;
  readonly subject_type: ApprovalSubjectType;
  readonly subject_execution_id: string | null;
  readonly subject_task_id: string | null;
  readonly status: ApprovalStatus;
  readonly action_summary: ApprovalActionSummary;
  readonly requested_at: string;
  readonly expires_at: string;
  readonly decided_at: string | null;
  readonly decision_comment: string | null;
  readonly execution_status: string | null;
}

export interface ListApprovalsParams {
  /** 'mine' = 待我审批收件箱(服务端只返回 pending) */
  readonly role?: 'mine';
  readonly status?: ApprovalStatus;
  readonly limit?: number;
}

const approvalsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/approvals`;

/** GET /workspaces/{ws}/approvals(?role=mine&status=&limit=) */
export async function listApprovals(
  client: MeshApiClient,
  workspaceId: string,
  params: ListApprovalsParams = {},
): Promise<ListEnvelope<Approval>> {
  return client.list<Approval>(approvalsPath(workspaceId), {
    query: { role: params.role, status: params.status, limit: params.limit },
  });
}

/** GET /workspaces/{ws}/approvals/{id} */
export async function getApproval(
  client: MeshApiClient,
  workspaceId: string,
  approvalId: string,
): Promise<Approval> {
  return client.request<Approval>(
    'GET',
    `${approvalsPath(workspaceId)}/${encodeURIComponent(approvalId)}`,
  );
}

export interface DecideParams {
  /** 审批留言(可选,≤2000 字符) */
  readonly comment?: string;
}

/** POST /workspaces/{ws}/approvals/{id}/approve(幂等) */
export async function approveApproval(
  client: MeshApiClient,
  workspaceId: string,
  approvalId: string,
  params: DecideParams = {},
): Promise<Approval> {
  return client.request<Approval>('POST', `${approvalsPath(workspaceId)}/${approvalId}/approve`, {
    body: { comment: params.comment ?? null },
  });
}

/** POST /workspaces/{ws}/approvals/{id}/reject(幂等) */
export async function rejectApproval(
  client: MeshApiClient,
  workspaceId: string,
  approvalId: string,
  params: DecideParams = {},
): Promise<Approval> {
  return client.request<Approval>('POST', `${approvalsPath(workspaceId)}/${approvalId}/reject`, {
    body: { comment: params.comment ?? null },
  });
}
