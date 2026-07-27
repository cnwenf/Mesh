/**
 * 附件模块 API 调用(契约层,attachment.md §3,README §6.14 包络)。
 * 单对象走 `request`(解 {data}),列表走 `list`(解 {data,next_cursor})。
 * 字节直传走对象存储签名 URL(不经 MeshApiClient):putBytesWithProgress 用 XHR 取进度。
 */
import type { MeshApiClient } from '../../api';
import type {
  Attachment,
  AttachmentLinkTo,
  DownloadDescriptor,
  MultipartCompletePart,
  MultipartPartsResponse,
  ThumbnailDescriptor,
  ThumbnailSize,
  UploadRequestResponse,
} from './types';

const UPLOAD_REQUESTS_PATH = '/api/v1/attachments/upload-requests';

const attachmentPath = (attachmentId: string): string => `/api/v1/attachments/${attachmentId}`;

const multipartPath = (uploadId: string): string => `/api/v1/multipart/${uploadId}`;

/** 详情级实时频道(attachment.md §3.7):attachment.processed / attachment.deleted。 */
export function attachmentChannel(issueId: string): string {
  return `issue:${issueId}`;
}

/** 事件名 → 实体/动作(与 issues/realtime 同款拆法)。 */
export function attachmentEventEntity(event: string): string {
  const dot = event.lastIndexOf('.');
  return dot === -1 ? '' : event.slice(0, dot);
}

export interface RequestUploadBody {
  readonly workspace_id?: string;
  readonly file_name: string;
  readonly file_size: number;
  readonly mime_type: string | null;
  readonly content_hash?: string;
  readonly link_to?: AttachmentLinkTo;
}

/** 申请直传(§3.1):校验配额/类型/大小 → 签发 PUT 预签名 URL(或秒传/分块)。 */
export async function requestUpload(
  client: MeshApiClient,
  body: RequestUploadBody,
): Promise<UploadRequestResponse> {
  return client.request<UploadRequestResponse>('POST', UPLOAD_REQUESTS_PATH, { body });
}

/** 完成直传(§3.2):服务端 HEAD 初校验后移交隔离区(scan_status='pending')。 */
export async function completeUpload(
  client: MeshApiClient,
  attachmentId: string,
): Promise<Attachment> {
  return client.request<Attachment>('POST', `${attachmentPath(attachmentId)}/complete`, {
    body: {},
  });
}

/** 中止直传(§3.5 失败/取消):后台任务回收 pending 对象。 */
export async function abortUpload(
  client: MeshApiClient,
  attachmentId: string,
): Promise<{ id: string; upload_status: string }> {
  return client.request<{ id: string; upload_status: string }>(
    'POST',
    `${attachmentPath(attachmentId)}/abort`,
    { body: {} },
  );
}

/** 取单个附件渲染对象(§3)。 */
export async function getAttachment(
  client: MeshApiClient,
  attachmentId: string,
): Promise<Attachment> {
  return client.request<Attachment>('GET', attachmentPath(attachmentId));
}

/** 删除附件(§4.6 软删除:blob.ref_count 同事务 −1,对象延迟回收)。 */
export async function deleteAttachment(
  client: MeshApiClient,
  attachmentId: string,
): Promise<{ id: string; deleted: boolean }> {
  return client.request<{ id: string; deleted: boolean }>('DELETE', attachmentPath(attachmentId));
}

/** 下载(§3.4):短时效签名 GET URL;隔离中 → 403 scan_pending / scan_infected。 */
export async function getDownloadUrl(
  client: MeshApiClient,
  attachmentId: string,
): Promise<DownloadDescriptor> {
  return client.request<DownloadDescriptor>('GET', `${attachmentPath(attachmentId)}/download`);
}

/** 缩略图(§4.3):短时效签名 URL;非图片/未就绪 → 404。 */
export async function getThumbnailUrl(
  client: MeshApiClient,
  attachmentId: string,
  size: ThumbnailSize = 'md',
): Promise<ThumbnailDescriptor> {
  return client.request<ThumbnailDescriptor>('GET', `${attachmentPath(attachmentId)}/thumbnail`, {
    query: { size },
  });
}

/** issue 附件列表(§4.1:position 序;游标分页)。 */
export async function listIssueAttachments(
  client: MeshApiClient,
  issueId: string,
  params: { limit?: number; cursor?: string } = {},
): Promise<{ data: readonly Attachment[]; nextCursor: string | null }> {
  const envelope = await client.list<Attachment>(`/api/v1/issues/${issueId}/attachments`, {
    query: { limit: params.limit, cursor: params.cursor },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** comment 附件列表(§4.1;comment 模块合入前后均可调用)。 */
export async function listCommentAttachments(
  client: MeshApiClient,
  commentId: string,
): Promise<{ data: readonly Attachment[]; nextCursor: string | null }> {
  const envelope = await client.list<Attachment>(`/api/v1/comments/${commentId}/attachments`);
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** 分块上传:分批领取后续 part URL(§3.1 文件 ≥64MB)。 */
export async function requestMultipartParts(
  client: MeshApiClient,
  uploadId: string,
  partNumbers: readonly number[],
): Promise<MultipartPartsResponse> {
  return client.request<MultipartPartsResponse>('POST', `${multipartPath(uploadId)}/parts`, {
    body: { part_numbers: partNumbers },
  });
}

/** 分块上传:汇总各 part ETag 完成合并(§3.1)。 */
export async function completeMultipart(
  client: MeshApiClient,
  uploadId: string,
  parts: readonly MultipartCompletePart[],
): Promise<{ upload_id: string }> {
  return client.request<{ upload_id: string }>('POST', `${multipartPath(uploadId)}/complete`, {
    body: { parts },
  });
}

/* ---- 字节直传工具(经对象存储签名 URL,不经 MeshApiClient) ---- */

export interface PutBytesResult {
  /** 响应 ETag 头(分块上传完成需回传);缺省为 null。 */
  readonly etag: string | null;
}

/**
 * 直传字节到签名 URL(XMLHttpRequest 以取上传进度事件)。
 * 返回 ETag 头(分块上传需收集后 complete)。AbortSignal 触发即中止。
 *
 * M1:预中止信号必须立即 reject——真实浏览器对**未 send** 的 XHR 调 abort()
 * 不触发任何事件,若把预中止交给 xhr.abort() 收敛,promise 永不 settle。
 * XHR settle(成功/失败/中止)后移除 signal 的 abort 监听,避免悬挂引用泄漏。
 */
export function putBytesWithProgress(
  url: string,
  body: Blob,
  headers: Record<string, string>,
  onProgress?: (loaded: number, total: number) => void,
  signal?: AbortSignal,
): Promise<PutBytesResult> {
  return new Promise<PutBytesResult>((resolve, reject) => {
    if (signal !== undefined && signal.aborted) {
      reject(new DOMException('upload aborted', 'AbortError'));
      return;
    }
    const xhr = new XMLHttpRequest();
    let abortListener: (() => void) | null = null;
    const settle = (): void => {
      if (signal !== undefined && abortListener !== null) {
        signal.removeEventListener('abort', abortListener);
      }
    };
    xhr.open('PUT', url, true);
    for (const [name, value] of Object.entries(headers)) {
      xhr.setRequestHeader(name, value);
    }
    xhr.upload.onprogress = (event: ProgressEvent) => {
      if (event.lengthComputable && onProgress !== undefined) {
        onProgress(event.loaded, event.total);
      }
    };
    xhr.onload = () => {
      settle();
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve({ etag: xhr.getResponseHeader('ETag') });
        return;
      }
      reject(new Error(`upload failed with status ${xhr.status}`));
    };
    xhr.onerror = () => {
      settle();
      reject(new Error('upload network error'));
    };
    xhr.onabort = () => {
      settle();
      reject(new DOMException('upload aborted', 'AbortError'));
    };
    if (signal !== undefined) {
      abortListener = () => xhr.abort();
      signal.addEventListener('abort', abortListener, { once: true });
    }
    xhr.send(body);
  });
}

/** 大于此阈值跳过客户端哈希(交由服务端全量计算,§4.5 第 2 步)。 */
export const SHA256_MAX_BYTES = 100 * 1024 * 1024;

/** 计算文件 SHA-256(小写十六进制);超过 SHA256_MAX_BYTES 返回 null(跳过)。 */
export async function sha256Hex(file: Blob): Promise<string | null> {
  if (file.size > SHA256_MAX_BYTES) return null;
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest('SHA-256', buffer);
  const bytes = new Uint8Array(digest);
  let hex = '';
  for (const byte of bytes) {
    hex += byte.toString(16).padStart(2, '0');
  }
  return hex;
}
