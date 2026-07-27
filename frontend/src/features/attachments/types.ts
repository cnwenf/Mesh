/**
 * 附件模块实体类型(attachment.md §2 / §3,README §6.14 包络)。
 * 字段一律 snake_case(与后端信封逐字对齐);本地 UI 状态另用 camelCase。
 */

/** blob 扫描状态机(§2.2 可见性闸门):仅 clean/skipped 放行下载/预览/缩略图。 */
export type AttachmentScanStatus = 'pending' | 'clean' | 'infected' | 'error' | 'skipped';

/** 上传台账状态(§3.1 三阶段直传)。 */
export type AttachmentUploadStatus = 'pending' | 'completed' | 'failed' | 'expired';

/** 上传者成员类型快照(§4.4:API JOIN members 计算,无存储判别列)。 */
export type AttachmentMemberType = 'human' | 'agent' | null;

/** 多态逻辑外键的目标类型(§6.2-4:不建物理 FK)。 */
export type AttachmentLinkType = 'issue' | 'comment';

/** 呈现场景(§4.4:截图内联 / 报告文件卡片)。 */
export type AttachmentDisplay = 'inline' | 'card';

/** 缩略图尺寸档位(§4.3)。 */
export type ThumbnailSize = 'sm' | 'md' | 'lg';

export interface AttachmentUploader {
  readonly id: string;
  readonly member_type: AttachmentMemberType;
  readonly display_name: string | null;
}

export interface AttachmentLink {
  readonly type: AttachmentLinkType;
  readonly id: string;
  readonly display: AttachmentDisplay | null;
  readonly position: number | null;
}

/** 附件渲染对象(§3 各端点 `data` 载荷的统一形态)。 */
export interface Attachment {
  readonly id: string;
  readonly blob_id: string;
  readonly file_name: string;
  readonly file_size: number;
  readonly mime_type: string | null;
  readonly extension: string | null;
  readonly is_image: boolean;
  readonly image_width: number | null;
  readonly image_height: number | null;
  readonly scan_status: AttachmentScanStatus;
  readonly upload_status: AttachmentUploadStatus;
  readonly uploader: AttachmentUploader | null;
  readonly links: readonly AttachmentLink[];
  /** API 相对路径(如 /api/v1/attachments/{id}/thumbnail?size=md);未放行时为 null。 */
  readonly thumbnail_url: string | null;
  /** API 相对路径(§3.4 下载端点)。 */
  readonly download_url: string;
  readonly created_at: string;
  readonly updated_at: string;
}

/** upload-request 的关联意图(§3.1 可选 link_to)。 */
export interface AttachmentLinkTo {
  readonly type: AttachmentLinkType;
  readonly id: string;
  readonly display?: AttachmentDisplay;
  readonly position?: number;
}

/** 单段直传描述(§3.1:PUT 预签名 URL,字节流不经应用服务器)。 */
export interface SingleUploadDescriptor {
  readonly method: 'PUT';
  readonly url: string;
  readonly headers: Record<string, string>;
  readonly expires_at: string;
}

export interface MultipartPartUrl {
  readonly part_number: number;
  readonly url: string;
}

/** 分块直传描述(§3.1:文件 ≥64MB;逐 part PUT 并收集 ETag)。 */
export interface MultipartUploadDescriptor {
  readonly upload_id: string;
  readonly part_urls: readonly MultipartPartUrl[];
  readonly part_size: number;
  readonly part_count: number;
  readonly expires_at: string;
}

export interface UploadLimits {
  readonly max_file_bytes: number;
}

/** POST /attachments/upload-requests 的 201 载荷(§3.1)。 */
export interface UploadRequestResponse {
  readonly id: string;
  readonly upload_status: AttachmentUploadStatus;
  readonly blob_id: string | null;
  readonly scan_status: AttachmentScanStatus;
  readonly mime_type: string | null;
  readonly is_image: boolean;
  /** null → 秒传(服务端去重,已完成);否则按形态走单段/分块直传。 */
  readonly upload: SingleUploadDescriptor | MultipartUploadDescriptor | null;
  readonly limits: UploadLimits;
}

/** POST /multipart/{id}/parts 的载荷(分批领取后续 part URL)。 */
export interface MultipartPartsResponse {
  readonly part_urls: readonly MultipartPartUrl[];
  readonly part_size: number;
  readonly part_count: number;
}

export interface MultipartCompletePart {
  readonly part_number: number;
  readonly etag: string;
}

/** GET /attachments/{id}/download 的载荷(§3.4 短时效签名 URL)。 */
export interface DownloadDescriptor {
  readonly url: string;
  readonly file_name: string;
  readonly expires_at: string;
}

/** GET /attachments/{id}/thumbnail 的载荷(§4.3)。 */
export interface ThumbnailDescriptor {
  readonly url: string;
  readonly size: ThumbnailSize;
  readonly expires_at: string;
}

/**
 * 单文件上传的本地进度态(§4.2)。
 * validating → uploading → completing → scanning → ready;失败 → error。
 */
export type UploadPhase =
  | 'validating'
  | 'uploading'
  | 'completing'
  | 'scanning'
  | 'ready'
  | 'error';

export interface UploadProgress {
  readonly loaded: number;
  readonly total: number;
}

export interface UploadEntry {
  /** 本地稳定 id(前端生成,区别于服务端 attachment id)。 */
  readonly localId: string;
  readonly fileName: string;
  readonly fileSize: number;
  readonly phase: UploadPhase;
  /** 0..1;仅 uploading 阶段有意义。 */
  readonly progress: number;
  /** 服务端 attachment id(upload-request 之后可得)。 */
  readonly attachmentId: string | null;
  /** 完成后的渲染对象(ready/scanning 阶段可得)。 */
  readonly attachment: Attachment | null;
  /** 失败时的 i18n 消息键(经 t() 渲染)。 */
  readonly errorKey: string | null;
}
