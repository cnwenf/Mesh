/**
 * 数据导入导出作业类型(import-export.md §2.2 / §2.4 / §3.6)。
 */

export type DataJobKind = 'import' | 'export';
export type DataJobEntityType = 'issues' | 'projects';
export type DataJobFormat = 'csv' | 'json';
export type DataJobStatus =
  | 'pending'
  | 'validating'
  | 'running'
  | 'completed'
  | 'completed_with_errors'
  | 'failed';

export type TransformType =
  | 'direct'
  | 'value_map'
  | 'status_by_name'
  | 'member_by_email'
  | 'date_parse'
  | 'list_split'
  | 'parent_by_external_ref';

export interface MappingTransform {
  readonly type: TransformType;
  readonly fallback?: 'default' | 'error';
  readonly on_missing?: 'null' | 'error';
  readonly map?: Readonly<Record<string, string>>;
  readonly default?: string;
  readonly delimiter?: string;
  readonly format?: string;
  readonly create_missing?: boolean;
}

export interface MappingColumn {
  readonly source: string;
  readonly target: string;
  readonly transform: MappingTransform;
}

export interface DataJobMapping {
  readonly columns: readonly MappingColumn[];
  readonly defaults?: Readonly<Record<string, string>>;
  readonly options?: Readonly<Record<string, unknown>>;
}

export interface DataJobErrorEntry {
  readonly row: number;
  readonly field: string;
  readonly code: string;
  readonly message: string;
}

export interface DataJobPreviewEntry {
  readonly row: number;
  readonly values: Readonly<Record<string, unknown>>;
}

export interface DataJobParams {
  readonly target_project_id?: string | null;
  readonly validated_at?: string | null;
  readonly predicted_failed_rows?: number;
  readonly scope?: 'project' | 'workspace' | 'view';
  readonly project_id?: string | null;
  readonly filters?: Readonly<Record<string, unknown>>;
  readonly locale?: string | null;
  readonly options?: Readonly<Record<string, unknown>>;
}

export interface DataJob {
  readonly id: string;
  readonly workspace_id: string;
  readonly kind: DataJobKind;
  readonly entity_type: DataJobEntityType;
  readonly format: DataJobFormat;
  readonly status: DataJobStatus;
  readonly total_rows: number;
  readonly succeeded_rows: number;
  readonly failed_rows: number;
  readonly source_attachment_id: string | null;
  readonly result_attachment_id: string | null;
  readonly failure_reason: string | null;
  readonly requested_by: string;
  readonly mapping: DataJobMapping;
  readonly params: DataJobParams;
  readonly started_at: string | null;
  readonly finished_at: string | null;
  readonly created_at: string | null;
  readonly updated_at: string | null;
  readonly error_report?: readonly DataJobErrorEntry[];
  readonly download_url?: string;
}

/** validate 响应附带的映射预览(§3.3)。 */
export interface DataJobValidateResult extends DataJob {
  readonly preview?: readonly DataJobPreviewEntry[];
}

export interface DownloadDescriptor {
  readonly url: string;
  readonly file_name: string;
  readonly expires_at: string;
}

/** 作业级实时频道(import-export.md §3.11)。 */
export function dataJobChannel(jobId: string): string {
  return `data_job:${jobId}`;
}

export const TERMINAL_DATA_JOB_STATUSES: ReadonlySet<DataJobStatus> = new Set([
  'completed',
  'completed_with_errors',
  'failed',
]);

export function isTerminalDataJobStatus(status: DataJobStatus): boolean {
  return TERMINAL_DATA_JOB_STATUSES.has(status);
}
