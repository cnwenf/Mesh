/**
 * 审批实体类型(README §6.10 统一「待我审批」;runtime 模块端点同形)。
 */

export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'expired';

export interface Approval {
  readonly id: string;
  readonly subject_type: string;
  readonly subject_execution_id: string | null;
  readonly subject_task_id: string | null;
  readonly status: ApprovalStatus;
  readonly action_summary: string;
  readonly requested_at: string;
  readonly expires_at: string;
  readonly decided_at: string | null;
  readonly decision_comment: string | null;
  readonly execution_status: string | null;
}
