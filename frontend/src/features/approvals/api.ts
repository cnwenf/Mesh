/**
 * 审批 API 调用(README §6.10 / runtime.md 端点同形,§6.14 包络)。
 * 决策权限由后端在 decide 端点强制(admin/owner 或 agent owner);
 * 列表端点本身对全部工作区成员开放(「待我审批」统一入口语义)。
 */
import type { MeshApiClient } from '../../api';
import type { Approval } from './types';

const approvalsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/approvals`;

export interface ListApprovalsParams {
  readonly status?: string;
  /** 'mine' → 仅待我审批(pending)。 */
  readonly role?: 'mine';
}

export async function listApprovals(
  client: MeshApiClient,
  workspaceId: string,
  params: ListApprovalsParams = {},
): Promise<readonly Approval[]> {
  const envelope = await client.list<Approval>(approvalsPath(workspaceId), {
    query: { status: params.status, role: params.role },
  });
  return envelope.data;
}

export interface DecideApprovalBody {
  readonly comment?: string | null;
}

export async function approveApproval(
  client: MeshApiClient,
  workspaceId: string,
  approvalId: string,
  body: DecideApprovalBody = {},
): Promise<Approval> {
  return client.request<Approval>('POST', `${approvalsPath(workspaceId)}/${approvalId}/approve`, {
    body,
  });
}

export async function rejectApproval(
  client: MeshApiClient,
  workspaceId: string,
  approvalId: string,
  body: DecideApprovalBody = {},
): Promise<Approval> {
  return client.request<Approval>('POST', `${approvalsPath(workspaceId)}/${approvalId}/reject`, {
    body,
  });
}
