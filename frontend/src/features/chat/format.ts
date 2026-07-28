/**
 * 聊天模块字节数本地化(chat-session.md §4.2)。KB/MB 粗粒度,够气泡与附件卡用。
 * 独立成模块以便 MessageBubble / MessageAttachments 共用而不产生循环依赖。
 */
export function formatByteSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
