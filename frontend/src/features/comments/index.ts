/**
 * 评论模块公共 API(comment-inbox.md)。
 */
export { CommentsPanel } from './CommentsPanel';
export type { CommentsPanelProps } from './CommentsPanel';
export { CommentComposer } from './CommentComposer';
export type { CommentComposerProps } from './CommentComposer';
export { CommentCard } from './CommentCard';
export type { CommentCardProps } from './CommentCard';
export { RunStatus, RUN_STATUS_CONFIG } from './RunStatus';
export type { RunStatusKind, RunStatusProps } from './RunStatus';
export { HIGHLIGHT_CLASS, scrollToAndHighlight } from './scrollToAndHighlight';
export { UNDO_WINDOW_MS, useDeferredDelete } from './useDeferredDelete';
export type {
  DeferredDelete,
  DeferredDeletePhase,
  DeferredDeleteTimers,
  UseDeferredDeleteOptions,
} from './useDeferredDelete';
export { DRAFT_SAVE_DEBOUNCE_MS, useDraftSaveIndicator } from './useDraftSaveIndicator';
export type {
  DraftSaveIndicator,
  DraftSaveStatus,
  UseDraftSaveIndicatorOptions,
} from './useDraftSaveIndicator';
export * from './api';
export * from './types';
