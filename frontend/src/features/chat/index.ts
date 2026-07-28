/**
 * 聊天模块公共 API(chat-session.md)。
 * 页面 + 面板 + 对话框组件;契约/类型/流式/实时合并/hook 一并导出供测试与复用。
 */
export { ChatPage } from './ChatPage';
export { ConversationPanel } from './ConversationPanel';
export type { ConversationPanelProps } from './ConversationPanel';
export { SessionListPanel } from './SessionListPanel';
export type { SessionListPanelProps, SessionStatusFilter } from './SessionListPanel';
export { NewSessionDialog } from './NewSessionDialog';
export type { NewSessionDialogProps } from './NewSessionDialog';
export { DistillDialog } from './DistillDialog';
export type { DistillDialogProps } from './DistillDialog';
export { MessageBubble, formatByteSize } from './MessageBubble';
export type { MessageBubbleProps } from './MessageBubble';
export { MessageAttachments } from './MessageAttachments';
export type { MessageAttachmentsProps } from './MessageAttachments';
export { AgentAvatar } from './AgentAvatar';
export type { AgentAvatarProps } from './AgentAvatar';
export { ContextBar } from './ContextBar';
export type { ContextBarProps } from './ContextBar';
export { ContextPicker } from './ContextPicker';
export type { ContextPickerProps } from './ContextPicker';
export { CandidateSwitcher } from './CandidateSwitcher';
export type { CandidateSwitcherProps } from './CandidateSwitcher';
export { ChatComposer } from './ChatComposer';
export type { ChatComposerProps, ComposerSubmitOptions } from './ChatComposer';
export { MessageList } from './MessageList';
export type { MessageListProps } from './MessageList';
export { useChatStream } from './useChatStream';
export type { UseChatStream, UseChatStreamOptions, StartStreamParams } from './useChatStream';
export { parseSseBlock, parseStreamEvent, streamChatGeneration } from './sse';
export type { StreamChatGenerationOptions, StreamHandle } from './sse';
export { applySessionListFrame } from './realtime';
export { toErrorKey } from './errors';
export { buildDistillBody } from './distill';
export * from './api';
export * from './types';
