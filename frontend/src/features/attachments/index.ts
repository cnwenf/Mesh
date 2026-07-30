/**
 * 附件功能入口(attachment.md §4)。
 * 导出 UI(AttachmentPanel / AttachmentComposer / Lightbox / FileIcon)、
 * 上传 hook、契约层 API 与实体类型。评论模块(MES-58)经此消费 composer。
 */
export { AttachmentComposer } from './components/AttachmentComposer';
export type { AttachmentComposerProps } from './components/AttachmentComposer';
export { AttachmentPanel } from './components/AttachmentPanel';
export type { AttachmentPanelProps } from './components/AttachmentPanel';
export { Lightbox } from './components/Lightbox';
export type { LightboxProps } from './components/Lightbox';
export { FileIcon } from './components/FileIcon';
export type { FileIconProps } from './components/FileIcon';
export { clampPercent, ProgressRing } from './components/ProgressRing';
export type { ProgressRingProps } from './components/ProgressRing';
export { formatFileSize } from './format';
export {
  clampScale,
  doubleTapScale,
  DOUBLE_TAP_BASE_SCALE,
  DOUBLE_TAP_THRESHOLD,
  DOUBLE_TAP_ZOOMED_SCALE,
  LIGHTBOX_MAX_SCALE,
  LIGHTBOX_MIN_SCALE,
  pinchScale,
  pointerDistance,
} from './pinchMath';
export type { PointerPoint } from './pinchMath';
export {
  ALLOWED_MIME_TYPES,
  DEFAULT_MAX_FILE_BYTES,
  DEFAULT_MAX_IMAGE_BYTES,
  MULTIPART_THRESHOLD_BYTES,
  useAttachmentUploader,
  validateFile,
} from './useAttachmentUploader';
export type {
  AttachmentUploader,
  UseAttachmentUploaderOptions,
} from './useAttachmentUploader';
export { applyAttachmentDeleted, applyAttachmentProcessed } from './realtime';
export * from './api';
export type {
  Attachment,
  AttachmentDisplay,
  AttachmentLink,
  AttachmentLinkTo,
  AttachmentLinkType,
  AttachmentMemberType,
  AttachmentScanStatus,
  AttachmentUploader as AttachmentUploaderRef,
  AttachmentUploadStatus,
  DownloadDescriptor,
  MultipartCompletePart,
  MultipartPartsResponse,
  MultipartPartUrl,
  SingleUploadDescriptor,
  MultipartUploadDescriptor,
  ThumbnailDescriptor,
  ThumbnailSize,
  UploadEntry,
  UploadLimits,
  UploadPhase,
  UploadProgress,
  UploadRequestResponse,
} from './types';
