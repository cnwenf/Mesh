/**
 * 附件直传流水线 hook(attachment.md §3.1–§3.3 / §4.2 / §4.5)。
 * 每文件独立:预校验(大小/类型)→ content_hash → upload-request →
 * 直传(单段 PUT / 分块 / 秒传)→ complete → scanning/ready。失败/取消经 abort 收敛。
 *
 * 进度态(§4.2):complete 后 scan_status='pending' → scanning(字节已传完,可提交);
 * scan_status ∈ clean/skipped → ready(可预览)。scanning→ready 的实时切换由消费侧
 * (AttachmentPanel)经 attachment.processed 帧合并完成。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { uuidv4 } from '../../api/uuid';
import { env } from '../../env';
import {
  abortUpload,
  completeMultipart,
  completeUpload,
  putBytesWithProgress,
  requestMultipartParts,
  requestUpload,
  sha256Hex,
} from './api';
import type {
  Attachment,
  AttachmentLinkTo,
  MultipartCompletePart,
  MultipartUploadDescriptor,
  SingleUploadDescriptor,
  UploadEntry,
  UploadPhase,
  UploadRequestResponse,
} from './types';

/* ---- 客户端预校验(§4.5 第 2 步:快速失败,镜像后端 §3.6 限制) ---- */

export const DEFAULT_MAX_FILE_BYTES = 100 * 1024 * 1024;
export const DEFAULT_MAX_IMAGE_BYTES = 25 * 1024 * 1024;
/** 分块上传阈值(§3.1:文件 ≥64MB 走 multipart)。 */
export const MULTIPART_THRESHOLD_BYTES = 64 * 1024 * 1024;

const IMAGE_MIME_TYPES: ReadonlySet<string> = new Set([
  'image/png',
  'image/jpeg',
  'image/jpg',
  'image/gif',
  'image/webp',
  'image/svg+xml',
]);

/** 允许的 MIME(镜像后端 §3.6 白名单:png/jpg/jpeg/gif/webp/svg/pdf/txt/log/md/csv/xlsx/docx/zip/gz)。 */
export const ALLOWED_MIME_TYPES: readonly string[] = [
  'image/png',
  'image/jpeg',
  'image/jpg',
  'image/gif',
  'image/webp',
  'image/svg+xml',
  'application/pdf',
  'text/plain',
  'text/markdown',
  'text/x-markdown',
  'text/csv',
  'text/x-log',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/zip',
  'application/gzip',
  'application/x-gzip',
];

const ALLOWED_MIME_SET: ReadonlySet<string> = new Set(ALLOWED_MIME_TYPES);

/** 扩展名兜底(部分文件 type 为空):与 MIME 白名单同语义。 */
const ALLOWED_EXTENSIONS: ReadonlySet<string> = new Set([
  'png',
  'jpg',
  'jpeg',
  'gif',
  'webp',
  'svg',
  'pdf',
  'txt',
  'log',
  'md',
  'csv',
  'xlsx',
  'docx',
  'zip',
  'gz',
]);

function extensionOf(fileName: string): string {
  const dot = fileName.lastIndexOf('.');
  if (dot === -1 || dot === fileName.length - 1) return '';
  return fileName.slice(dot + 1).toLowerCase();
}

function isAllowedType(file: File): boolean {
  const mime = file.type.toLowerCase();
  if (mime !== '' && ALLOWED_MIME_SET.has(mime)) return true;
  return ALLOWED_EXTENSIONS.has(extensionOf(file.name));
}

function maxBytesFor(file: File): number {
  return IMAGE_MIME_TYPES.has(file.type.toLowerCase())
    ? DEFAULT_MAX_IMAGE_BYTES
    : DEFAULT_MAX_FILE_BYTES;
}

/** 预校验:返回失败 i18n 键,通过返回 null。 */
export function validateFile(file: File): string | null {
  if (file.size > maxBytesFor(file)) return 'error.file_too_large';
  if (!isAllowedType(file)) return 'error.unsupported_media_type';
  return null;
}

/** 扫描放行态:clean/skipped 可预览(§2.2 可见性闸门)。 */
function isReleased(attachment: Attachment): boolean {
  return attachment.scan_status === 'clean' || attachment.scan_status === 'skipped';
}

function isMultipart(
  upload: UploadRequestResponse['upload'],
): upload is MultipartUploadDescriptor {
  return upload !== null && 'upload_id' in upload;
}

function isSingle(upload: UploadRequestResponse['upload']): upload is SingleUploadDescriptor {
  return upload !== null && 'method' in upload;
}

/** complete 之前的上传阶段:这些阶段内服务端台账为 pending/uploading,可 abort(§3.5 状态机)。 */
const PRE_COMPLETE_PHASES: ReadonlySet<UploadPhase> = new Set(['uploading', 'completing']);

/**
 * 秒传(upload=null)时由 upload-request 响应 + 本地文件元数据合成渲染对象:
 * 服务端此刻**已建好附件行 + links**(upload_status='completed'),无需也不能再
 * complete(重放 409,§3.5 状态机)。缺失的服务端字段(uploader/尺寸等)随后由
 * 消费侧列表刷新 / attachment.processed 帧补齐。
 */
function attachmentFromInstantUpload(response: UploadRequestResponse, file: File): Attachment {
  const extension = extensionOf(file.name);
  const now = new Date().toISOString();
  return {
    id: response.id,
    blob_id: response.blob_id ?? '',
    file_name: file.name,
    file_size: file.size,
    mime_type: response.mime_type ?? (file.type === '' ? null : file.type),
    extension: extension === '' ? null : extension,
    is_image: response.is_image,
    image_width: null,
    image_height: null,
    scan_status: response.scan_status,
    upload_status: response.upload_status,
    uploader: null,
    links: [],
    thumbnail_url: null,
    download_url: `/api/v1/attachments/${response.id}/download`,
    created_at: now,
    updated_at: now,
  };
}

export interface UseAttachmentUploaderOptions {
  /** 注入客户端(测试);缺省按 env.apiBaseUrl + getToken 构建。 */
  readonly client?: MeshApiClient;
  /** 未链接到具体实体时的归属工作区(import-export.md §4.2 导入源上传)。 */
  readonly workspaceId?: string;
}

export interface AttachmentUploader {
  readonly uploads: readonly UploadEntry[];
  readonly addFiles: (files: Iterable<File>, linkTo?: AttachmentLinkTo) => void;
  readonly cancel: (localId: string) => void;
}

export function useAttachmentUploader(
  options: UseAttachmentUploaderOptions = {},
): AttachmentUploader {
  const injectedClient = options.client;
  const client = useMemo(
    () => injectedClient ?? new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }),
    [injectedClient],
  );
  const [uploads, setUploads] = useState<readonly UploadEntry[]>([]);
  const controllersRef = useRef<Map<string, AbortController>>(new Map());
  // 镜像最新 uploads:cancel 需同步读取 attachmentId(setState updater 于渲染期才执行)。
  const uploadsRef = useRef<readonly UploadEntry[]>(uploads);
  uploadsRef.current = uploads;

  const patchEntry = useCallback((localId: string, patch: Partial<UploadEntry>) => {
    setUploads((prev) =>
      prev.map((entry) => (entry.localId === localId ? { ...entry, ...patch } : entry)),
    );
  }, []);

  const runSingle = useCallback(
    async (
      descriptor: SingleUploadDescriptor,
      file: File,
      localId: string,
      signal: AbortSignal,
    ): Promise<void> => {
      await putBytesWithProgress(
        descriptor.url,
        file,
        descriptor.headers,
        (loaded, total) => {
          patchEntry(localId, { progress: total > 0 ? loaded / total : 0 });
        },
        signal,
      );
    },
    [patchEntry],
  );

  const runMultipart = useCallback(
    async (
      descriptor: MultipartUploadDescriptor,
      file: File,
      localId: string,
      signal: AbortSignal,
    ): Promise<void> => {
      const urlByPart = new Map<number, string>(
        descriptor.part_urls.map((part) => [part.part_number, part.url]),
      );
      // 初始描述仅含首批 part URL;一次性领取其余 part(§3.1)。
      if (urlByPart.size < descriptor.part_count) {
        const missing: number[] = [];
        for (let n = 1; n <= descriptor.part_count; n += 1) {
          if (!urlByPart.has(n)) missing.push(n);
        }
        const batch = await requestMultipartParts(client, descriptor.upload_id, missing);
        for (const part of batch.part_urls) urlByPart.set(part.part_number, part.url);
      }
      const parts: MultipartCompletePart[] = [];
      let uploadedBytes = 0;
      for (let partNumber = 1; partNumber <= descriptor.part_count; partNumber += 1) {
        const url = urlByPart.get(partNumber);
        if (url === undefined) throw new Error(`missing part url for part ${partNumber}`);
        const start = (partNumber - 1) * descriptor.part_size;
        const slice = file.slice(start, Math.min(start + descriptor.part_size, file.size));
        const baseBytes = uploadedBytes;
        const result = await putBytesWithProgress(
          url,
          slice,
          {},
          (loaded) => {
            patchEntry(localId, {
              progress: file.size > 0 ? (baseBytes + loaded) / file.size : 0,
            });
          },
          signal,
        );
        uploadedBytes += slice.size;
        parts.push({ part_number: partNumber, etag: result.etag ?? '' });
      }
      await completeMultipart(client, descriptor.upload_id, parts);
    },
    [client, patchEntry],
  );

  const runPipeline = useCallback(
    async (file: File, localId: string, linkTo: AttachmentLinkTo | undefined) => {
      const controller = new AbortController();
      controllersRef.current.set(localId, controller);
      try {
        // validating:预校验 + 计算 content_hash(大文件跳过,§4.5)。
        const validationError = validateFile(file);
        if (validationError !== null) {
          patchEntry(localId, { phase: 'error', errorKey: validationError });
          return;
        }
        const contentHash = (await sha256Hex(file)) ?? undefined;
        if (controller.signal.aborted) return;

        const response = await requestUpload(client, {
          file_name: file.name,
          file_size: file.size,
          mime_type: file.type === '' ? null : file.type,
          content_hash: contentHash,
          link_to: linkTo,
          ...(options.workspaceId !== undefined ? { workspace_id: options.workspaceId } : {}),
        });
        patchEntry(localId, { attachmentId: response.id });

        // 直传:秒传(upload=null)/ 单段 PUT / 分块。
        if (isMultipart(response.upload)) {
          patchEntry(localId, { phase: 'uploading', progress: 0 });
          await runMultipart(response.upload, file, localId, controller.signal);
        } else if (isSingle(response.upload)) {
          patchEntry(localId, { phase: 'uploading', progress: 0 });
          await runSingle(response.upload, file, localId, controller.signal);
        }

        if (controller.signal.aborted) return;

        // 秒传(upload=null,H1):服务端去重命中,附件行 + links 已落库且
        // upload_status='completed'——**绝不能再调 /complete**(状态机仅允许
        // pending/uploading complete,重放必 409)。直接用响应本身进入完成后的
        // 阶段:scan_status pending → scanning,clean/skipped → ready。
        if (response.upload === null) {
          const attachment = attachmentFromInstantUpload(response, file);
          patchEntry(localId, {
            phase: isReleased(attachment) ? 'ready' : 'scanning',
            progress: 1,
            attachment,
          });
          return;
        }

        patchEntry(localId, { phase: 'completing', progress: 1 });
        const attachment = await completeUpload(client, response.id);
        patchEntry(localId, {
          phase: isReleased(attachment) ? 'ready' : 'scanning',
          attachment,
        });
      } catch (err: unknown) {
        if (controller.signal.aborted) return;
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'error.network';
        patchEntry(localId, { phase: 'error', errorKey: key });
      } finally {
        controllersRef.current.delete(localId);
      }
    },
    [client, patchEntry, runMultipart, runSingle],
  );

  const addFiles = useCallback(
    (files: Iterable<File>, linkTo?: AttachmentLinkTo) => {
      const fileList = Array.from(files);
      if (fileList.length === 0) return;
      const entries: UploadEntry[] = fileList.map((file) => ({
        localId: uuidv4(), // 统一的安全上下文无关 UUID(MES-129),消除旧内联兜底重复实现
        fileName: file.name,
        fileSize: file.size,
        phase: 'validating',
        progress: 0,
        attachmentId: null,
        attachment: null,
        errorKey: null,
      }));
      setUploads((prev) => [...prev, ...entries]);
      entries.forEach((entry, index) => {
        void runPipeline(fileList[index], entry.localId, linkTo);
      });
    },
    [runPipeline],
  );

  const cancel = useCallback(
    (localId: string) => {
      const controller = controllersRef.current.get(localId);
      if (controller !== undefined) controller.abort();
      controllersRef.current.delete(localId);
      // 同步读取 attachmentId(best-effort 通知服务端中止,回收 pending 对象)。
      const attachmentId = uploadsRef.current.find((entry) => entry.localId === localId)
        ?.attachmentId;
      setUploads((prev) => prev.filter((entry) => entry.localId !== localId));
      if (attachmentId !== undefined && attachmentId !== null) {
        void abortUpload(client, attachmentId).catch(() => undefined);
      }
    },
    [client],
  );

  // M2:卸载清理——中止所有在途 XHR(经各自 AbortController);对已拿到
  // attachmentId 且仍在 complete 之前(pending/uploading)的条目,尽力通知服务端
  // abort 以回收 pending 对象(fire-and-forget:错误吞掉、卸载后绝不 setState)。
  useEffect(() => {
    // 捕获 ref 当前值供清理函数使用(两个容器均为 useRef 初始实例,不重赋)。
    const controllers = controllersRef.current;
    const uploads = uploadsRef;
    return () => {
      for (const entry of uploads.current) {
        if (entry.attachmentId !== null && PRE_COMPLETE_PHASES.has(entry.phase)) {
          void abortUpload(client, entry.attachmentId).catch(() => undefined);
        }
      }
      for (const controller of controllers.values()) controller.abort();
      controllers.clear();
    };
  }, [client]);

  return { uploads, addFiles, cancel };
}
