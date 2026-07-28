/**
 * 数据作业 API 调用(契约层,import-export.md §3.1–§3.6,README §6.14 包络)。
 * 单对象走 `request`(解 {data}),列表走 `list`(解 {data,next_cursor})。
 */
import type { MeshApiClient } from '../../api';
import type {
  DataJob,
  DataJobEntityType,
  DataJobFormat,
  DataJobMapping,
  DownloadDescriptor,
} from './types';

const IMPORT_PATH = '/api/v1/data-jobs/import';
const EXPORT_PATH = '/api/v1/data-jobs/export';
const JOBS_PATH = '/api/v1/data-jobs';

export interface CreateImportJobBody {
  readonly workspace_id: string;
  readonly entity_type?: DataJobEntityType;
  readonly format?: DataJobFormat;
  readonly source_attachment_id: string;
  readonly mapping?: DataJobMapping;
  readonly auto_infer?: boolean;
  readonly target_project_id?: string | null;
}

export interface CreateExportJobBody {
  readonly workspace_id: string;
  readonly entity_type?: DataJobEntityType;
  readonly format?: DataJobFormat;
  readonly scope?: 'project' | 'workspace' | 'view';
  readonly project_id?: string | null;
  readonly filters?: Record<string, unknown>;
  readonly mapping?: DataJobMapping;
  readonly locale?: string | null;
}

export interface ListJobsParams {
  readonly workspace_id: string;
  readonly kind?: 'import' | 'export';
  readonly status?: string;
  readonly requested_by?: string;
  readonly cursor?: string | null;
  readonly limit?: number;
}

/** 建导入作业(§3.2):源附件已放行;mapping 或 auto_infer 二选一。 */
export async function createImportJob(
  client: MeshApiClient,
  body: CreateImportJobBody,
  idempotencyKey?: string,
): Promise<DataJob> {
  return client.request<DataJob>('POST', IMPORT_PATH, {
    body,
    idempotencyKey,
  });
}

/** dry-run 预校验(§3.3):不落库,产出映射预览与逐行错误。 */
export async function validateImportJob(
  client: MeshApiClient,
  jobId: string,
): Promise<DataJob> {
  return client.request<DataJob>('POST', `${IMPORT_PATH}/${jobId}/validate`, {});
}

/** 确认执行(§3.4):部分成功语义,要求已 dry-run。 */
export async function runImportJob(client: MeshApiClient, jobId: string): Promise<DataJob> {
  return client.request<DataJob>('POST', `${IMPORT_PATH}/${jobId}/run`, {});
}

/** 建导出作业(§3.5):异步,worker 后台流式生成。 */
export async function createExportJob(
  client: MeshApiClient,
  body: CreateExportJobBody,
  idempotencyKey?: string,
): Promise<DataJob> {
  return client.request<DataJob>('POST', EXPORT_PATH, {
    body,
    idempotencyKey,
  });
}

/** 作业详情(§3.6):仅 requested_by / admin 可见。 */
export async function getDataJob(client: MeshApiClient, jobId: string): Promise<DataJob> {
  return client.request<DataJob>('GET', `${JOBS_PATH}/${jobId}`, {});
}

/** 作业列表(§3.6):按 kind/status/requested_by 过滤,游标分页。 */
export async function listDataJobs(
  client: MeshApiClient,
  params: ListJobsParams,
): Promise<{ data: DataJob[]; next_cursor: string | null }> {
  return client.list<DataJob>(JOBS_PATH, {
    query: {
      workspace_id: params.workspace_id,
      kind: params.kind,
      status: params.status,
      requested_by: params.requested_by,
      cursor: params.cursor ?? undefined,
      limit: params.limit,
    },
  });
}

/** 签名下载(§3.6):产物/错误报告,短时效私有 URL。 */
export async function downloadDataJobProduct(
  client: MeshApiClient,
  jobId: string,
): Promise<DownloadDescriptor> {
  return client.request<DownloadDescriptor>('GET', `${JOBS_PATH}/${jobId}/download`, {});
}
