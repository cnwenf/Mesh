/**
 * issue 关联层 API 调用(label-property.md §3.1/§3.2 关联端点,§6.14 包络)。
 * 标签与字段值随 issue 返回、不单独分页(§3.4)。PUT 整体提交经
 * RequestOptions.ifMatch(issue.updated_at)启用乐观并发(§6.14)。
 */
import type { MeshApiClient } from '../../api';
import type { Label } from './types';
import type {
  FieldValueInput,
  FieldValueListingEntry,
  IssueLabelsPayload,
  MergeLabelResult,
} from './associationTypes';

const issueLabelsPath = (issueId: string): string => `/api/v1/issues/${issueId}/labels`;
const issueLabelPath = (issueId: string, labelId: string): string =>
  `/api/v1/issues/${issueId}/labels/${labelId}`;
const labelMergePath = (labelId: string): string => `/api/v1/labels/${labelId}/merge`;
const issueFieldValuesPath = (issueId: string): string =>
  `/api/v1/issues/${issueId}/custom-field-values`;

// --- issue ↔ labels ----------------------------------------------------------

export async function listIssueLabels(
  client: MeshApiClient,
  issueId: string,
): Promise<readonly Label[]> {
  const envelope = await client.list<Label>(issueLabelsPath(issueId));
  return envelope.data;
}

/** 整体替换 issue 标签(§3.1 PUT);422 label_scope_mismatch(项目级标签跨项目)。 */
export async function replaceIssueLabels(
  client: MeshApiClient,
  issueId: string,
  labelIds: readonly string[],
  ifMatch?: string,
): Promise<IssueLabelsPayload> {
  return client.request<IssueLabelsPayload>('PUT', issueLabelsPath(issueId), {
    body: { label_ids: [...labelIds] },
    ifMatch,
  });
}

export async function addIssueLabel(
  client: MeshApiClient,
  issueId: string,
  labelId: string,
): Promise<IssueLabelsPayload> {
  return client.request<IssueLabelsPayload>('POST', issueLabelPath(issueId, labelId));
}

export async function removeIssueLabel(
  client: MeshApiClient,
  issueId: string,
  labelId: string,
): Promise<IssueLabelsPayload> {
  return client.request<IssueLabelsPayload>('DELETE', issueLabelPath(issueId, labelId));
}

/** 合并标签(§3.2):源标签的 issue 迁到目标后删除源标签。 */
export async function mergeLabel(
  client: MeshApiClient,
  sourceLabelId: string,
  targetLabelId: string,
): Promise<MergeLabelResult> {
  return client.request<MergeLabelResult>('POST', labelMergePath(sourceLabelId), {
    body: { target_label_id: targetLabelId },
  });
}

// --- issue custom-field values -----------------------------------------------

/** 读取 issue 全部适用字段的定义快照 + 当前值(不单独分页,§3.4)。 */
export async function listIssueFieldValues(
  client: MeshApiClient,
  issueId: string,
): Promise<readonly FieldValueListingEntry[]> {
  const envelope = await client.list<FieldValueListingEntry>(
    issueFieldValuesPath(issueId),
  );
  return envelope.data;
}

/**
 * 整体提交字段值(§3.2 PUT)。每条 values 项恰好一个 value_* 列;
 * 422 invalid_field_value(类型/枚举/成员校验)、field_inactive(已停用字段);
 * If-Match 传 issue.updated_at 走 §6.14 乐观并发。
 */
export async function setIssueFieldValues(
  client: MeshApiClient,
  issueId: string,
  values: readonly FieldValueInput[],
  ifMatch?: string,
): Promise<readonly FieldValueListingEntry[]> {
  // request() unwraps the {"data": [...]} envelope (§6.14).
  return client.request<readonly FieldValueListingEntry[]>(
    'PUT',
    issueFieldValuesPath(issueId),
    { body: { values: [...values] }, ifMatch },
  );
}
