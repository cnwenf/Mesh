/**
 * 标签与自定义字段(定义层)API 调用(label-property.md §3.1 定义层端点,§6.14 包络)。
 * 复用 MeshApiClient:列表走 `list`({data,next_cursor}),单对象走 `request`;
 * PATCH 经 RequestOptions.ifMatch 启用乐观并发(409 conflict 收敛见 optimistic.ts)。
 */
import type { MeshApiClient } from '../../api';
import type {
  CreateCustomFieldBody,
  CreateLabelBody,
  CreateOptionBody,
  CustomFieldDef,
  CustomFieldOption,
  Label,
  ListCustomFieldsParams,
  ListLabelsParams,
  UpdateCustomFieldBody,
  UpdateLabelBody,
  UpdateOptionBody,
} from './types';

export interface Page<T> {
  readonly data: readonly T[];
  readonly nextCursor: string | null;
}

const workspaceLabelsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/labels`;
const labelPath = (labelId: string): string => `/api/v1/labels/${labelId}`;
const workspaceFieldsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/custom-fields`;
const fieldPath = (fieldDefId: string): string => `/api/v1/custom-fields/${fieldDefId}`;
const optionsPath = (fieldDefId: string): string => `/api/v1/custom-fields/${fieldDefId}/options`;
const optionPath = (fieldDefId: string, optionId: string): string =>
  `/api/v1/custom-fields/${fieldDefId}/options/${optionId}`;

/** 工作区级标签/字段定义的列表频道(§3.5)。 */
export function workspaceLabelsChannel(workspaceId: string): string {
  return `workspace:${workspaceId}:labels`;
}

export function workspaceCustomFieldsChannel(workspaceId: string): string {
  return `workspace:${workspaceId}:custom_fields`;
}

/** 项目作用域定义走项目详情频道(私有项目事件只进 project:{id},§6.7)。 */
export function projectChannel(projectId: string): string {
  return `project:${projectId}`;
}

// --- labels ------------------------------------------------------------------

export async function listLabels(
  client: MeshApiClient,
  workspaceId: string,
  params: ListLabelsParams = {},
): Promise<Page<Label>> {
  const envelope = await client.list<Label>(workspaceLabelsPath(workspaceId), {
    query: { project_id: params.project_id, limit: params.limit, cursor: params.cursor },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** 409 label_name_taken;400 validation_error(颜色/名称)。 */
export async function createLabel(
  client: MeshApiClient,
  workspaceId: string,
  body: CreateLabelBody,
): Promise<Label> {
  return client.request<Label>('POST', workspaceLabelsPath(workspaceId), { body });
}

/** 409 conflict(If-Match 失配 / label_name_taken)。 */
export async function updateLabel(
  client: MeshApiClient,
  labelId: string,
  body: UpdateLabelBody,
  ifMatch?: string,
): Promise<Label> {
  return client.request<Label>('PATCH', labelPath(labelId), { body, ifMatch });
}

export async function deleteLabel(
  client: MeshApiClient,
  labelId: string,
): Promise<{ id: string; deleted: boolean }> {
  return client.request<{ id: string; deleted: boolean }>('DELETE', labelPath(labelId));
}

// --- custom field definitions -------------------------------------------------

export async function listCustomFields(
  client: MeshApiClient,
  workspaceId: string,
  params: ListCustomFieldsParams = {},
): Promise<Page<CustomFieldDef>> {
  const envelope = await client.list<CustomFieldDef>(workspaceFieldsPath(workspaceId), {
    query: {
      project_id: params.project_id,
      is_active: params.is_active,
      limit: params.limit,
      cursor: params.cursor,
    },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** 409 field_key_taken;422 invalid_field_config(类型配置/默认值非法)。 */
export async function createCustomField(
  client: MeshApiClient,
  workspaceId: string,
  body: CreateCustomFieldBody,
): Promise<CustomFieldDef> {
  return client.request<CustomFieldDef>('POST', workspaceFieldsPath(workspaceId), { body });
}

export async function updateCustomField(
  client: MeshApiClient,
  fieldDefId: string,
  body: UpdateCustomFieldBody,
  ifMatch?: string,
): Promise<CustomFieldDef> {
  return client.request<CustomFieldDef>('PATCH', fieldPath(fieldDefId), { body, ifMatch });
}

export async function deleteCustomField(
  client: MeshApiClient,
  fieldDefId: string,
): Promise<{ id: string; deleted: boolean }> {
  return client.request<{ id: string; deleted: boolean }>('DELETE', fieldPath(fieldDefId));
}

// --- enum options ---------------------------------------------------------------

export async function listOptions(
  client: MeshApiClient,
  fieldDefId: string,
  params: { limit?: number; cursor?: string } = {},
): Promise<Page<CustomFieldOption>> {
  const envelope = await client.list<CustomFieldOption>(optionsPath(fieldDefId), {
    query: { limit: params.limit, cursor: params.cursor },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** 422 field_inactive(已停用字段)/ invalid_field_config(非枚举字段);409 重名。 */
export async function createOption(
  client: MeshApiClient,
  fieldDefId: string,
  body: CreateOptionBody,
): Promise<CustomFieldOption> {
  return client.request<CustomFieldOption>('POST', optionsPath(fieldDefId), { body });
}

export async function updateOption(
  client: MeshApiClient,
  fieldDefId: string,
  optionId: string,
  body: UpdateOptionBody,
  ifMatch?: string,
): Promise<CustomFieldOption> {
  return client.request<CustomFieldOption>('PATCH', optionPath(fieldDefId, optionId), {
    body,
    ifMatch,
  });
}

export async function deleteOption(
  client: MeshApiClient,
  fieldDefId: string,
  optionId: string,
): Promise<{ id: string; deleted: boolean }> {
  return client.request<{ id: string; deleted: boolean }>(
    'DELETE',
    optionPath(fieldDefId, optionId),
  );
}
